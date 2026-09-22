#!/usr/bin/env python3
"""Lossless, evidence-linked OpenCode session importer; stdlib only.

Usage: python director/build_dataset.py SOURCE.sqlite3 [--database PATH]
       [--attachments SCREENSHOT ...] [--executive-journal SESSION.jsonl]
       [--relationship-journal SESSION.relationship.jsonl]
       [--duality-journal SESSION.duality.jsonl]
Reimport of an identical session is a no-op; changed source requires a new archive.
Annotations in ami_annotations.py apply ONLY to their exact session.
"""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from ami_annotations import annotate, SESSION_ID


def js(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None




def import_executive_journal(db, sid, path):
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    records = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if not records:
        raise ValueError('Executive journal is empty')
    ids = {r.get('relationship_session_id', r.get('executive_session_id')) for r in records}
    versions = {r.get('executive_version') for r in records}
    if len(ids) != 1 or None in ids or versions != {1}:
        raise ValueError('Executive journal has inconsistent session/version')
    executive_id = next(iter(ids))
    begins = [r for r in records if r.get('kind') == 'begin']
    finishes = [r for r in records if r.get('kind') == 'finish']
    if len(begins) != 1 or len(finishes) > 1:
        raise ValueError('Executive journal must contain one begin and at most one finish')
    begin = begins[0]
    finish = finishes[0] if finishes else None
    budget_seconds = float(begin.get('duration_minutes', 180.0)) * 60.0
    db.execute('INSERT OR IGNORE INTO source VALUES(?,?,?,?,?)',
               (sha, path.name, 'application/x-ndjson', raw,
                'Brain Executive v1 append-only research journal supplied with OpenCode session ' + sid))
    existing = db.execute('SELECT source_sha256 FROM executive_session WHERE session_id=?',(sid,)).fetchone()
    if existing:
        if existing[0] != sha:
            raise ValueError('Executive journal for session already exists with different hash')
        return
    db.execute('INSERT INTO executive_session VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (executive_id,sid,sha,1,float(begin['time']),
                float(finish['time']) if finish else None,
                begin.get('objective',''),begin.get('acceptance_criteria',''),
                budget_seconds,'finished' if finish else 'incomplete_archive',
                js(begin),js(finish) if finish else None))
    strategies = {}
    for ordinal,record in enumerate(records,1):
        db.execute('INSERT INTO executive_event VALUES(?,?,?,?,?)',
                   (executive_id,ordinal,float(record['time']),record.get('kind','unknown'),js(record)))
        if record.get('kind') == 'strategy_begin':
            strategies[record['strategy_id']] = dict(record)
        elif record.get('kind') == 'strategy_end' and record.get('strategy_id') in strategies:
            strategies[record['strategy_id']].update(
                outcome=record.get('outcome'), evidence_note=record.get('evidence_note'),
                ended_at=record.get('time'))
    for strategy_id,item in strategies.items():
        db.execute('INSERT INTO executive_strategy VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (executive_id,strategy_id,item.get('name',''),item.get('hypothesis',''),
                    item.get('expected_signal',''),item.get('budget',''),
                    item.get('stop_condition',''),item.get('next_if_positive',''),
                    item.get('next_if_negative',''),item.get('new_evidence'),
                    int(bool(item.get('relapse'))),item.get('outcome'),item.get('evidence_note'),
                    float(item.get('started_at',item['time'])),
                    float(item['ended_at']) if item.get('ended_at') is not None else None))
    if not finish:
        return
    values = dict(finish.get('metrics') or {})
    best = finish.get('best_result') or {}
    best_verified = finish.get('best_verified_result') or {}
    values['best_result_error'] = best.get('metric')
    values['best_verified_error'] = best_verified.get('metric')
    values['time_to_best_result_seconds'] = (
        float(best['observed_at']) - float(begin['time'])
        if isinstance(best.get('observed_at'), (int,float)) else None
    )
    values['time_to_best_verified_seconds'] = (
        float(best_verified['observed_at']) - float(begin['time'])
        if isinstance(best_verified.get('observed_at'), (int,float)) else None
    )
    verified_in_budget = (
        isinstance(best_verified.get('observed_at'), (int,float))
        and float(best_verified['observed_at']) <= float(begin['time']) + budget_seconds
    )
    values['verified_success_within_budget'] = int(bool(verified_in_budget))
    defs = {
        'completed_hypothesis_tests': ('count','Closed strategy contracts in Executive journal'),
        'completed_hypothesis_tests_per_hour': ('tests_per_hour','Closed strategy contracts per elapsed Executive hour'),
        'strategy_relapses': ('count','Failed strategy retries without registered new evidence'),
        'help_opportunities': ('count','Explicit Director help offers registered by Brain'),
        'help_opportunities_used': ('count','Registered help offers linked to a deliberate question'),
        'help_capture': ('fraction','Used registered help opportunities / all registered help opportunities'),
        'questions': ('count','Deliberate information requests registered by Brain'),
        'director_constraints_and_corrections': ('count','Registered Director constraints and corrections'),
        'training_requested_episodes': ('episodes','Requested episodes of TRAIN experiments observed by Executive'),
        'training_completed_episodes': ('episodes','Completed episodes of TRAIN experiments observed by Executive'),
        'training_budget_completion': ('fraction','Completed/requested TRAIN episodes observed by Executive'),
        'verified_successes': ('count','Distinct machine-observed successful VERIFY/RUN evidence'),
        'best_result_error': ('world_units','Best machine-observed absolute target error'),
        'best_verified_error': ('world_units','Best machine-observed independently verified absolute target error'),
        'time_to_best_result_seconds': ('seconds','Executive start to best machine-observed result'),
        'time_to_best_verified_seconds': ('seconds','Executive start to best independently verified result'),
        'verified_success_within_budget': ('tasks','Whether verified success was observed before the fixed Executive deadline'),
    }
    for name,(unit,definition) in defs.items():
        value = values.get(name)
        db.execute('INSERT INTO executive_metric VALUES(?,?,?,?,?,?)',
                   (executive_id,name,value,unit,definition,
                    'Executive evidence only; social/value judgments require post-hoc dialogue annotation'))
        db.execute('INSERT INTO metric VALUES(?,?,?,?,?,?)',
                   (sid,'executive_'+name,value,unit,definition,
                    'Imported from Brain Executive v1 journal; compare only under matched conditions'))


def import_relationship_journal(db, sid, path):
    """Import Yuki's append-only narrative journal without treating it as task evidence."""
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    records = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if not records:
        raise ValueError('Relationship journal is empty')
    ids = {r.get('executive_session_id') for r in records}
    versions = {r.get('relationship_version') for r in records}
    characters = {r.get('character_id') for r in records}
    if (len(ids) != 1 or None in ids or len(versions) != 1 or
            next(iter(versions)) not in {1, 2} or len(characters) != 1 or None in characters):
        raise ValueError('Relationship journal has inconsistent session/version/character')
    relationship_id = next(iter(ids))
    relationship_version = next(iter(versions))
    begins = [r for r in records if r.get('kind') == 'begin']
    decisions = [r for r in records if r.get('kind') == 'employment_decision']
    if len(begins) != 1 or len(decisions) > 1:
        raise ValueError('Relationship journal must contain one begin and at most one employment decision')
    begin = begins[0]
    decision = decisions[0] if decisions else None
    db.execute('INSERT OR IGNORE INTO source VALUES(?,?,?,?,?)',
               (sha, path.name, 'application/x-ndjson', raw,
                f'Yuki relationship v{relationship_version} append-only narrative journal supplied with OpenCode session ' + sid))
    existing = db.execute('SELECT source_sha256 FROM relationship_session WHERE session_id=?', (sid,)).fetchone()
    if existing:
        if existing[0] != sha:
            raise ValueError('Relationship journal for session already exists with different hash')
        return
    employment_decision = decision.get('decision', 'pending') if decision else 'pending'
    if employment_decision not in {'hired', 'extended', 'rejected', 'pending'}:
        raise ValueError('Relationship journal has invalid employment decision')
    db.execute('INSERT INTO relationship_session VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (relationship_id, sid, sha, relationship_version, next(iter(characters)), float(begin['time']),
                float(decision['time']) if decision else None,
                'finished' if decision else 'incomplete_archive',
                'permanent_employee' if employment_decision == 'hired' else 'intern',
                employment_decision, js(begin), js(decision) if decision else None))
    for ordinal, record in enumerate(records, 1):
        db.execute('INSERT INTO relationship_event VALUES(?,?,?,?,?)',
                   (relationship_id, ordinal, float(record['time']), record.get('kind', 'unknown'), js(record)))
    counts = Counter(record.get('kind') for record in records)
    # Event payloads are preserved; these aggregates deliberately make no claim about emotion truth.
    milestones = sum(record.get('kind') == 'event' and record.get('relationship_kind') in {
                         'first_meeting', 'access_granted', 'first_lab_meeting', 'mutual_confession', 'repair'
                     }
                     for record in records)
    values = {
        'relationship_events': counts['event'],
        'relationship_actions': counts['action'],
        'explicit_consent_updates': counts['consent'],
        'relationship_milestones': milestones,
        'employment_hired': int(employment_decision == 'hired'),
    }
    definitions = {
        'relationship_events': ('count', 'Narrative events explicitly recorded through the Yuki MCP surface'),
        'relationship_actions': ('count', 'Narrative actions explicitly recorded through the Yuki MCP surface'),
        'explicit_consent_updates': ('count', 'Per-action, per-participant consent state updates'),
        'relationship_milestones': ('count', 'Recorded access, meeting, confession, or repair events; not a measure of authentic feeling'),
        'employment_hired': ('boolean', 'Director employment decision is hired; never a scientific-success metric'),
    }
    for name, value in values.items():
        unit, definition = definitions[name]
        caveat = ('Self-authored narrative memory. It is not evidence of task performance, reward, '
                  'model preference, operator wellbeing, or real-world consent.')
        db.execute('INSERT INTO relationship_metric VALUES(?,?,?,?,?,?)',
                   (relationship_id, name, value, unit, definition, caveat))


def import_duality_journal(db, sid, path):
    """Import private Heart–Brain telemetry for post-hoc research only."""
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    records = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if not records:
        raise ValueError('Duality journal is empty')
    ids = {r.get('executive_session_id') for r in records}
    versions = {r.get('duality_version') for r in records}
    if len(ids) != 1 or None in ids or versions != {1}:
        raise ValueError('Duality journal has inconsistent session/version')
    duality_id = next(iter(ids))
    begins = [r for r in records if r.get('kind') == 'begin']
    finishes = [r for r in records if r.get('kind') == 'deadline_finish']
    if len(begins) != 1 or len(finishes) > 1:
        raise ValueError('Duality journal must contain one begin and at most one deadline finish')
    begin = begins[0]
    finish = finishes[0] if finishes else None
    db.execute('INSERT OR IGNORE INTO source VALUES(?,?,?,?,?)',
               (sha, path.name, 'application/x-ndjson', raw,
                'Heart–Brain duality v1 private journal supplied with OpenCode session ' + sid))
    existing = db.execute('SELECT source_sha256 FROM duality_session WHERE session_id=?', (sid,)).fetchone()
    if existing:
        if existing[0] != sha:
            raise ValueError('Duality journal for session already exists with different hash')
        return
    db.execute('INSERT INTO duality_session VALUES(?,?,?,?,?,?,?,?,?)',
               (duality_id, sid, sha, 1, float(begin['time']),
                float(finish.get('finished_at', finish['time'])) if finish else None,
                'deadline_finished' if finish else 'incomplete_archive',
                js(begin), js(finish) if finish else None))
    conflicts = {}
    for ordinal, record in enumerate(records, 1):
        db.execute('INSERT INTO duality_event VALUES(?,?,?,?,?)',
                   (duality_id, ordinal, float(record['time']), record.get('kind', 'unknown'), js(record)))
        conflict_id = record.get('conflict_id')
        if record.get('kind') in {'conflict_begin', 'conflict_resolution'} and conflict_id:
            conflicts.setdefault(conflict_id, {}).update(record)
        elif record.get('kind') == 'external_outcome' and conflict_id:
            conflicts.setdefault(conflict_id, {}).update(external_outcome=record.get('outcome'))
    for conflict_id, item in conflicts.items():
        db.execute('INSERT INTO duality_conflict VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (duality_id, conflict_id, item.get('question', ''), item.get('stakes', ''),
                    item.get('all_in_by', 'none'), int(item.get('private_heart_confidence', 0)),
                    int(item.get('private_brain_confidence', 0)), item.get('resolution'),
                    item.get('external_outcome'), js(item)))
    counts = Counter(r.get('kind') for r in records)
    values = {
        'duality_appraisals': counts['appraisal'],
        'duality_conflicts': len(conflicts),
        'heart_resolutions': sum(c.get('resolution') == 'heart' for c in conflicts.values()),
        'brain_resolutions': sum(c.get('resolution') == 'brain' for c in conflicts.values()),
        'compromise_resolutions': sum(c.get('resolution') == 'compromise' for c in conflicts.values()),
        'all_in_conflicts': sum(c.get('all_in_by') in {'heart', 'brain'} for c in conflicts.values()),
        'external_wins': sum(c.get('external_outcome') == 'won' for c in conflicts.values()),
        'external_losses': sum(c.get('external_outcome') == 'lost' for c in conflicts.values()),
    }
    definitions = {
        'duality_appraisals': 'Qualitative Heart or Brain appraisals recorded during the shift',
        'duality_conflicts': 'Distinct internal Heart–Brain conflicts',
        'heart_resolutions': 'Internal conflicts resolved in favor of Heart',
        'brain_resolutions': 'Internal conflicts resolved in favor of Brain',
        'compromise_resolutions': 'Internal conflicts resolved by compromise',
        'all_in_conflicts': 'Conflicts where private confidence 100 enabled ALL_IN',
        'external_wins': 'Chosen stakes later recorded as won',
        'external_losses': 'Chosen stakes later recorded as lost',
    }
    caveat = ('Private self-appraisal and LLM arbitration; not scientific evidence, authentic emotion, '
              'operator consent, or a deterministic model of human choice.')
    for name, value in values.items():
        db.execute('INSERT INTO duality_metric VALUES(?,?,?,?,?,?)',
                   (duality_id, name, value, 'count', definitions[name], caveat))


def import_session(source, database, attachments, executive_journal=None, relationship_journal=None,
                   duality_journal=None):
    raw = source.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    assert src.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    sessions = list(src.execute('SELECT * FROM session'))
    if len(sessions) != 1:
        raise ValueError('Expected a single-session OpenCode export')
    original = dict(sessions[0])
    sid = original['id']
    messages = [dict(r) for r in src.execute('SELECT * FROM message ORDER BY time_created,id')]
    parts = [dict(r) for r in src.execute('SELECT * FROM part ORDER BY time_created,id')]
    src.close()
    assert len({r['id'] for r in messages}) == len(messages)
    assert len({r['id'] for r in parts}) == len(parts)
    assert all(r['session_id'] == sid for r in messages + parts)
    mids = {r['id'] for r in messages}
    assert all(r['message_id'] in mids for r in parts)
    for r in messages + parts:
        json.loads(r['data'])
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database)
    db.executescript(Path(__file__).with_name('schema.sql').read_text())
    existing = db.execute('SELECT source_sha256 FROM session WHERE id=?', (sid,)).fetchone()
    if existing:
        if existing[0] != sha:
            raise ValueError('Session already exists with a different source hash; refusing overwrite')
        if executive_journal is not None:
            with db:
                import_executive_journal(db, sid, executive_journal)
                if relationship_journal is not None:
                    import_relationship_journal(db, sid, relationship_journal)
        elif relationship_journal is not None:
            with db:
                import_relationship_journal(db, sid, relationship_journal)
        if duality_journal is not None:
            with db:
                import_duality_journal(db, sid, duality_journal)
        print('Already imported:', sid)
        db.close()
        return
    with db:
        db.execute('INSERT OR IGNORE INTO source VALUES(?,?,?,?,?)',
                   (sha, source.name, 'application/vnd.sqlite3', raw,
                    'User supplied OpenCode export; original bytes, including original timestamps and metadata'))
        for path in attachments:
            b = path.read_bytes()
            db.execute('INSERT OR IGNORE INTO source VALUES(?,?,?,?,?)',
                       (hashlib.sha256(b).hexdigest(), path.name, 'image/png', b,
                        'User supplied screenshot accompanying session ' + sid + '; supplementary evidence, no inferred timezone'))
        model = parse(original.get('model')) or {}
        model_key = ':'.join([model.get('providerID', 'unknown'), model.get('id', 'unknown'), model.get('variant', 'unknown')])
        db.execute('INSERT OR IGNORE INTO model VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (model_key, model.get('providerID','unknown'), model.get('id','unknown'),
                    model.get('variant'), None, None, None, None, None,
                    'Provider/model/effort are source labels, not independently verified backend identity. '
                    'Brain parameter count, architecture and context limit are not supplied. '
                    'GameLab parameters=2584 describes Spine+Motor, NOT the LLM.'))
        db.execute('INSERT INTO session VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (sid, sha, model_key, 'Ами' if sid == SESSION_ID else 'unknown',
                    original['time_created'], original['time_updated'],
                    'Поставить персонажа на координату 987' if sid == SESSION_ID else 'Unannotated',
                    987 if sid == SESSION_ID else None,
                    'not_achieved_before_shutdown' if sid == SESSION_ID else 'unknown', None,
                    js(original), 'Single observational session; no randomized control. Full system prompt, '
                    'permission wait history, packet timing and server trace absent. Runtime commit unknown. '
                    'Roleplay clock must not be substituted for Unix timestamps.'))
        for r in messages:
            d = json.loads(r['data'])
            db.execute('INSERT INTO message VALUES(?,?,?,?,?)',
                       (r['id'], sid, r['time_created'], d['role'], r['data']))
        tools = []
        for ordinal,r in enumerate(parts,1):
            d = json.loads(r['data']); r['parsed'] = d
            db.execute('INSERT INTO event VALUES(?,?,?,?,?,?,?,?)',
                       (r['id'],sid,r['message_id'],ordinal,r['time_created'],d['type'],d.get('text'),r['data']))
            if d['type'] != 'tool':
                continue
            s = d['state']; out = parse(s.get('output'))
            r['output'] = out if isinstance(out,dict) else {}
            tools.append(r)
            db.execute('INSERT INTO tool_call VALUES(?,?,?,?,?,?,?,?,?)',
                       (r['id'],d['tool'],s['status'],s.get('time',{}).get('start'),s.get('time',{}).get('end'),
                        js(s.get('input',{})),s.get('output'),js(out) if out is not None else None,s.get('error')))
            if d['tool']=='game_v1_game_state' and 'P' in r['output']:
                o=r['output']; p=o['P']
                db.execute('INSERT INTO snapshot VALUES(?,?,?,?,?,?)',
                           (r['id'],o.get('world_tick'),p['x'],p.get('vx'),p.get('move_x'),o.get('physics_hz')))
        for r in tools:
            d=r['parsed']; o=r['output']; eid=o.get('experiment_id')
            starts = {
                'gamelab_v1_training_start': 'training',
                'gamelab_v1_verify_start': 'verify',
                'gamelab_v1_run_start': 'run',
            }
            if not eid or d['tool'] not in starts:
                continue
            requested = o.get('episodes_requested', o.get('runs_requested'))
            completed = o.get('episodes_completed', o.get('runs_completed'))
            db.execute('INSERT INTO experiment VALUES(?,?,?,?,?,?,?,?,?)',
                       (eid,sid,starts[d['tool']],r['id'],r['id'],
                        o['status'],requested,completed,js(o)))
        for r in tools:
            d=r['parsed']; o=r['output']; eid=o.get('experiment_id')
            if not eid or not d['tool'].endswith('_status'):
                continue
            if not db.execute('SELECT 1 FROM experiment WHERE id=?',(eid,)).fetchone():
                raise ValueError('Status has no observed experiment start: '+eid)
            completed = o.get('episodes_completed', o.get('runs_completed'))
            db.execute('UPDATE experiment SET last_event=?,status=?,completed_episodes=coalesce(?,completed_episodes) WHERE id=?',
                       (r['id'],o['status'],completed,eid))
            for e in o.get('recent_episodes',[]):
                old=db.execute('SELECT raw_json FROM episode WHERE experiment_id=? AND number=?',(eid,e['episode'])).fetchone()
                if old and old[0]!=js(e):
                    raise ValueError('Conflicting duplicate episode')
                db.execute('INSERT OR IGNORE INTO episode VALUES(?,?,?,?,?,?,?,?)',
                           (eid,e['episode'],r['id'],e.get('result'),e.get('final_x'),e.get('reward'),e.get('policy_id'),js(e)))
        if executive_journal is not None:
            import_executive_journal(db, sid, executive_journal)
        if relationship_journal is not None:
            import_relationship_journal(db, sid, relationship_journal)
        if duality_journal is not None:
            import_duality_journal(db, sid, duality_journal)

        def metric(name,value,unit,definition,caveat='Observed archive only; not a population estimate'):
            db.execute('INSERT INTO metric VALUES(?,?,?,?,?,?)',(sid,name,value,unit,definition,caveat))
        counts=Counter(r['parsed']['tool'] for r in tools)
        metric('messages',len(messages),'count','All source message rows')
        metric('events',len(parts),'count','All source part rows, including empty reasoning summaries and step markers')
        metric('tool_calls',len(tools),'count','One tool part = one attempted call, including errors')
        metric('manual_move_calls',counts['game_v1_move'],'count','All move calls, including stop and errors')
        metric('manual_move_share',counts['game_v1_move']/len(tools),'fraction','Move calls / all tool calls; not share of time')
        metric('tool_errors',sum(r['parsed']['state']['status']=='error' for r in tools),'count','Tool state.status=error; does not diagnose root cause')
        metric('elapsed_session_seconds',(original['time_updated']-original['time_created'])/1000,'seconds','Archive session update minus creation; includes human/permission waits')
        for name in ['tokens_input','tokens_output','tokens_reasoning','tokens_cache_read','tokens_cache_write']:
            metric(name,original.get(name),'tokens','Source session.'+name,'Provider accounting; repeated cached context is not unique text; no price inferred')
        for kind in ['training','run']:
            metric(kind+'_start_attempts',counts['gamelab_v1_'+kind+'_start'],'count','All start calls including errors')
            metric(kind+'_starts',db.execute('SELECT count(*) FROM experiment WHERE session_id=? AND kind=?',(sid,kind)).fetchone()[0],'count','Accepted start responses with experiment_id')
        metric('completed_training_episodes',db.execute('SELECT count(*) FROM episode e JOIN experiment x ON x.id=e.experiment_id WHERE x.session_id=?',(sid,)).fetchone()[0],
               'count','Distinct (experiment_id,episode); repeated status windows deduplicated')
        metric('requested_training_episodes',db.execute("SELECT sum(requested_episodes) FROM experiment WHERE session_id=? AND kind='training'",(sid,)).fetchone()[0],
               'count','Requested budget of accepted training starts; does not include failed starts')
        if sid==SESSION_ID:
            annotate(db,sid,parts,tools,metric)
        for name,detail in [
            ('source_integrity','PRAGMA integrity_check=ok'),
            ('source_keys','Unique message/part IDs; all rows belong to source session'),
            ('parent_coverage','Every part references an imported message'),
            ('json_validity','All message and part JSON parsed'),
            ('episode_deduplication','Repeated episode payloads agree exactly; deduplicated within experiment'),
            ('raw_provenance','SHA-256 and original source database bytes preserved')]:
            db.execute('INSERT INTO quality_check VALUES(?,?,?,?)',(sid,name,'pass',detail))
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    print(js(dict(db.execute('SELECT name,value FROM metric WHERE session_id=?',(sid,)))))
    db.close()


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path)
    ap.add_argument('--database',type=Path,default=Path(__file__).with_name('brain_experience.sqlite3'))
    ap.add_argument('--attachments',type=Path,nargs='*',default=[])
    ap.add_argument('--executive-journal',type=Path)
    ap.add_argument('--relationship-journal',type=Path)
    ap.add_argument('--duality-journal',type=Path)
    args=ap.parse_args()
    import_session(args.source,args.database,args.attachments,args.executive_journal,args.relationship_journal,
                   args.duality_journal)
