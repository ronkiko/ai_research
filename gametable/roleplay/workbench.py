"""Approved learning operations; no model-selected artifact, budget or authority."""
from __future__ import annotations

import copy
import threading

MOTOR_SPEC = 'motor.continuous_1d.v1'
SPINE_SPEC = 'spine.flat_run.v1'
MOTOR_SUITE = 'motor.certification.v2'
SPINE_SUITE = 'spine.flat_run.verify.v1'
LEARNING_ACTIONS = {'prepare_training', 'train_motor', 'verify_motor', 'train_spine',
                    'verify_spine', 'select_skill', 'learning_status'}


class Workbench:
    def __init__(self, store, factory=None):
        self.store = store
        self.factory = factory
        self._learning = None
        self.lock = threading.RLock()

    def service(self):
        with self.lock:
            if self._learning is None:
                if self.factory is None:
                    from organism.learning import LearningService
                    self.factory = LearningService
                self._learning = self.factory()
            return self._learning

    def execute(self, proposal, request_id):
        service = self.service()
        kind = proposal['action_type']
        refs = self.store.runtime_value('learning_refs', {})
        if kind == 'learning_status':
            return {'status': 'completed', 'result': service.describe()}
        if kind == 'prepare_training':
            if proposal['source'] != 'director_request':
                raise ValueError('Подготовка курса требует отдельного разрешения Директора')
            authorization = 'director.' + request_id
            service.issue_training_prepare_authorization(
                authorization, character_revision=self.store.state()['revision'])
            value = service.training_prepare(MOTOR_SPEC, request_id, authorization)
            return {'status': 'completed' if value['status'] in {'already_ready', 'assisted_setup'}
                    else value['status'], 'result': value}
        if kind == 'train_motor':
            # Explicit new practice request, never silently reuse an operator's certificate.
            value = service.motor_train_start(MOTOR_SPEC, 100, request_id)
            refs['motor_id'] = value['spec']['artifact_id']
            refs.pop('spine_id', None)
        elif kind == 'verify_motor':
            motor = refs.get('motor_id')
            if not motor:
                raise ValueError('Сначала нужно потренировать базовые движения')
            value = service.verify_start(motor, MOTOR_SUITE, request_id)
        elif kind == 'train_spine':
            motor = refs.get('motor_id')
            if not motor:
                raise ValueError('Сначала нужны собственные проверенные базовые движения')
            value = service.spine_train_start(motor, SPINE_SPEC, 100, request_id)
            refs['spine_id'] = value['skill_id']
        elif kind == 'verify_spine':
            if not refs.get('spine_id'):
                raise ValueError('Сначала нужно потренировать координацию')
            value = service.verify_start(refs['spine_id'], SPINE_SUITE, request_id)
        elif kind == 'select_skill':
            if not refs.get('spine_id'):
                raise ValueError('Пока нет собственного навыка для применения')
            value = service.skill_select(refs['spine_id'], request_id)
            return {'status': 'completed', 'result': value}
        else:
            raise ValueError('Неизвестное учебное действие')
        self.store.set_runtime_value('learning_refs', refs)
        mode = 'verify' if kind.startswith('verify_') else 'train'
        return {'status': value['status'], 'action_id': f"learning:{mode}:{value['job_id']}",
                'result': copy.deepcopy(value)}

    def poll(self, action_id):
        _, mode, job_id = action_id.split(':', 2)
        fn = self.service().verify_status if mode == 'verify' else self.service().training_status
        value = fn(job_id)
        return {'action_id': action_id, 'status': value['status'], 'result': value}

    def cancel(self, action_id, request_id):
        _, mode, job_id = action_id.split(':', 2)
        fn = self.service().verify_cancel if mode == 'verify' else self.service().training_cancel
        value = fn(job_id, request_id)
        return {'action_id': action_id, 'status': value['status'], 'result': value}
