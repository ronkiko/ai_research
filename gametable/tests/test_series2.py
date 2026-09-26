"""Series 2 gameplay integration: authority, experience, initiative and learning workflow."""
import copy
import json
from pathlib import Path
import re
import tempfile
import time
import unittest
from types import SimpleNamespace

from gametable.roleplay.engine import load_rules, build_director_action_proposal
from gametable.roleplay.external import ActionExecutor, ActionScopeError
from gametable.roleplay.runtime import Runtime
from gametable.roleplay.store import Store
from gametable.tests.test_table import FakeBackend, FakeNavigation, event


class LearningFixture:
    """Contract fixture, not evidence of learned convergence."""
    def __init__(self):
        self.calls = []
        self.jobs = {}
    def describe(self): return {'active_job': None, 'body': {'zone_id': 'training/flat_run'}}
    def issue_training_prepare_authorization(self, auth, **kw):
        self.calls.append(('grant', auth))
    def training_prepare(self, spec, request, authorization):
        self.calls.append(('prepare', spec, authorization))
        return {'status': 'assisted_setup', 'learned_success': False}
    def motor_train_start(self, spec, budget, request):
        self.calls.append(('motor', spec, budget))
        value = {'job_id': 'm1', 'status': 'running', 'spec': {'artifact_id': 'motor-own'}}
        self.jobs['m1'] = value
        return value
    def spine_train_start(self, motor, spec, budget, request):
        self.calls.append(('spine', motor, budget))
        value = {'job_id': 's1', 'status': 'running', 'skill_id': 'spine-own'}
        self.jobs['s1'] = value
        return value
    def verify_start(self, artifact, suite, request):
        self.calls.append(('verify', artifact, suite))
        value = {'job_id': 'v1', 'status': 'running'}
        self.jobs['v1'] = value
        return value
    def skill_select(self, skill, request):
        self.calls.append(('mount', skill))
        return {'mounted': {'skill_id': skill}}
    def training_status(self, job): return copy.deepcopy(self.jobs[job])
    verify_status = training_status
    def training_cancel(self, job, request):
        self.jobs[job]['status'] = 'cancel_requested'
        return {'status': 'cancel_requested', 'accepted': True}
    verify_cancel = training_cancel


class Series2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'save.sqlite3'
        self.rules = load_rules()
        self.store = Store(self.path, self.rules)
        self.learning = LearningFixture()
        self.nav = FakeNavigation()
        self.actions = ActionExecutor(lambda: self.nav, store=self.store,
                                      learning_factory=lambda: self.learning)
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def proposal(self, intent, eid='test-action'):
        e = event(eid, 'Предлагаю занятие.', intent)
        proposal = build_director_action_proposal(self.store.state(), e, {'disposition': 'accept'})
        return e, proposal
    def dispatch(self, intent, eid):
        e, p = self.proposal(intent, eid)
        reserved = self.store.reserve_action(eid, p)
        result = self.actions.start(p, reserved['request_id'])
        record = self.store.record_action_result(p['proposal_id'], result)
        return record

    def test_learning_sequence_keeps_own_artifact_identity_and_explicit_mount(self):
        prepared = self.dispatch('request_training_prepare', 'prepare')
        self.assertFalse(prepared['result']['result']['learned_success'])
        motor = self.dispatch('request_motor_train', 'motor')
        self.assertEqual(motor['status'], 'running')
        self.dispatch('request_motor_verify', 'vm')
        self.dispatch('request_spine_train', 'spine')
        self.dispatch('request_spine_verify', 'vs')
        self.assertFalse(any(c[0] == 'mount' for c in self.learning.calls))
        selected = self.dispatch('request_skill_select', 'select')
        self.assertEqual(selected['status'], 'completed')
        self.assertIn(('mount', 'spine-own'), self.learning.calls)
        self.assertIn(('spine', 'motor-own', 100), self.learning.calls)
        self.assertEqual(self.store.runtime_value('learning_refs')['motor_id'], 'motor-own')

    def test_mcp_ticket_is_scoped_single_use_and_model_text_is_not_receipt(self):
        e, p = self.proposal('request_motor_train')
        self.store.reserve_action(e['id'], p)
        outer = self
        class Worker:
            def complete(self, parent, agent, prompt, **kw):
                outer.assertEqual(kw['allowed_tools'], ('learning_v1_execute_approved',))
                token = json.loads(re.search(r'approval_id=("[^"]+")', prompt)[1])
                with outer.assertRaises(ActionScopeError):
                    outer.actions.execute_approved(token, 'navigation')
                first = outer.actions.execute_approved(token, 'learning')
                outer.assertEqual(first, outer.actions.execute_approved(token, 'learning'))
                raise RuntimeError('LLM failed after tool receipt')
        self.actions.backend = Worker()
        result = self.actions.start(p, 'action.' + p['proposal_id'])
        self.assertEqual(result['status'], 'running')
        self.assertEqual(len(self.learning.calls), 1)
        self.assertEqual(self.store.action(p['proposal_id'])['status'], 'running')
        with self.assertRaises(ActionScopeError):
            self.actions.execute_approved('invented', 'learning')

    def test_worker_cannot_claim_success_without_tool(self):
        self.actions.backend = SimpleNamespace(complete=lambda *a, **k: {'text': 'completed'})
        result = self.dispatch('request_motor_train', 'fake-success')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.learning.calls, [])

    def test_poll_failure_reports_unknown_outcome(self):
        self.nav.action_status = lambda action_id: (_ for _ in ()).throw(ConnectionError())
        result = self.actions.poll('navigation-job')
        self.assertEqual(result['status'], 'uncertain')
        self.assertTrue(result['uncertain'])

    def test_ticket_retry_after_store_failure_does_not_repeat_side_effect(self):
        e, proposal = self.proposal('request_motor_train')
        self.store.reserve_action(e['id'], proposal)
        self.actions._approvals['ticket'] = {'service': 'learning', 'proposal': proposal,
                                            'request_id': 'ticket-request', 'result': None}
        record_result = self.store.record_action_result
        def fail_commit(*args):
            raise OSError('temporary persistence failure')
        self.store.record_action_result = fail_commit
        with self.assertRaises(OSError):
            self.actions.execute_approved('ticket', 'learning')
        self.store.record_action_result = record_result
        result = self.actions.execute_approved('ticket', 'learning')
        self.assertEqual(result['status'], 'running')
        self.assertEqual(len(self.learning.calls), 1)

    def test_declined_learning_does_not_call_mcp(self):
        e = event('decline', 'Давай потренируемся.', 'request_motor_train')
        backend = FakeBackend(e, self.store.state(), disposition='decline')
        runtime = Runtime(self.store, backend, self.rules, actions=self.actions)
        self.store.begin(e); runtime.run(e)
        self.assertEqual(self.store.get(e['id'])['status'], 'done')
        self.assertEqual(self.learning.calls, [])

    def test_initiative_must_pass_both_voices_and_decline_does_not_move(self):
        e, p = self.proposal('request_lab_work')
        p['source'] = 'self_initiated'
        e = {'id': 'self-test', 'text': 'Я рассматриваю действие: ' + p['rationale'],
             'intent_id': 'consider_action', 'source': 'self_initiated', 'proposal': p}
        backend = FakeBackend(e, self.store.state(), disposition='decline')
        runtime = Runtime(self.store, backend, self.rules, actions=self.actions)
        runtime.start_self_action(p, event_id=e['id']); runtime.thread.join(5)
        self.assertFalse(runtime.thread.is_alive())
        self.assertEqual(self.store.get(e['id'])['status'], 'done')
        self.assertEqual(self.nav.calls, [])
        self.assertEqual(len([c for c in backend.calls if 'MODE: APPRAISAL' in c[1]]), 2)
        self.assertFalse(any(m['speaker_id'] == 'director' for m in self.store.dialogue()))
        self.assertIsNone(self.store.state()['memories'][-1]['director'])

    def test_terminal_fact_survives_restart_and_reaches_both_voices(self):
        record = self.dispatch('request_lab_work', 'go')
        runtime = Runtime(self.store, None, self.rules, actions=self.actions)
        runtime.poll_actions(); runtime.poll_actions()
        self.assertEqual(len(self.store.experiences()), 1)
        self.store.close(); self.store = Store(self.path, self.rules)
        e = event('after-arrival')
        backend = FakeBackend(e, self.store.state())
        runtime = Runtime(self.store, backend, self.rules, actions=self.actions)
        self.store.begin(e); runtime.run(e)
        packets = [json.loads(c[1].split('Ниже данные сцены:\n')[1])
                   for c in backend.calls if 'MODE: APPRAISAL' in c[1]]
        self.assertEqual(packets[0], packets[1])
        self.assertEqual(packets[0]['experiences'][0]['status'], 'arrived')
        self.assertEqual(len(self.store.experiences()), 1)

    def test_cancel_ack_is_not_finished_job(self):
        record = self.dispatch('request_motor_train', 'motor')
        result = self.actions.cancel(record['action_id'], 'cancel1')
        self.assertEqual(result['status'], 'cancel_requested')
        self.assertEqual(self.actions.poll(record['action_id'])['status'], 'cancel_requested')

    def test_internal_event_does_not_invent_director_speech_after_restart(self):
        self.store.begin({'id': 'world1', 'text': 'Подтверждённое событие', 'intent_id': 'talk', 'source': 'world'})
        self.store.close(); self.store = Store(self.path, self.rules)
        self.assertEqual(self.store.dialogue(), [])

    def test_rules_upgrade_preserves_personal_history_and_stats(self):
        state = self.store.state()
        state['rules_hash'] = self.rules['compatible_previous_hashes'][0]
        state['rules_version'] = 'vn-6'
        state['stats']['trust'] = 72
        with self.store.db:
            self.store.db.execute('UPDATE save SET value=? WHERE id=1', (json.dumps(state),))
        self.store.close(); self.store = Store(self.path, self.rules)
        self.assertEqual(self.store.state()['stats']['trust'], 72)
        self.assertEqual(self.store.state()['rules_version'], 'vn-7')

    def test_preparation_cannot_be_self_authorized(self):
        e, p = self.proposal('request_training_prepare')
        p['source'] = 'self_initiated'
        runtime = Runtime(self.store, None, self.rules, actions=self.actions)
        with self.assertRaises(ValueError): runtime.start_self_action(p)
        self.assertEqual(self.learning.calls, [])

if __name__ == '__main__': unittest.main()
