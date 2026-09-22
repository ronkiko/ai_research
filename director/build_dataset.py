#!/usr/bin/env python3
"""Lossless, evidence-linked OpenCode session importer; stdlib only.

Usage: python director/build_dataset.py SOURCE.sqlite3 [--database PATH]
       [--attachments SCREENSHOT ...]
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


def import_session(source, database, attachments):
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
            if not eid or d['tool'] not in ['gamelab_v1_training_start','gamelab_v1_run_start']:
                continue
            db.execute('INSERT INTO experiment VALUES(?,?,?,?,?,?,?,?,?)',
                       (eid,sid,'training' if 'training' in d['tool'] else 'run',r['id'],r['id'],
                        o['status'],o.get('episodes_requested'),o.get('episodes_completed'),js(o)))
        for r in tools:
            d=r['parsed']; o=r['output']; eid=o.get('experiment_id')
            if not eid or not d['tool'].endswith('_status'):
                continue
            if not db.execute('SELECT 1 FROM experiment WHERE id=?',(eid,)).fetchone():
                raise ValueError('Status has no observed experiment start: '+eid)
            db.execute('UPDATE experiment SET last_event=?,status=?,completed_episodes=coalesce(?,completed_episodes) WHERE id=?',
                       (r['id'],o['status'],o.get('episodes_completed'),eid))
            for e in o.get('recent_episodes',[]):
                old=db.execute('SELECT raw_json FROM episode WHERE experiment_id=? AND number=?',(eid,e['episode'])).fetchone()
                if old and old[0]!=js(e):
                    raise ValueError('Conflicting duplicate episode')
                db.execute('INSERT OR IGNORE INTO episode VALUES(?,?,?,?,?,?,?,?)',
                           (eid,e['episode'],r['id'],e.get('result'),e.get('final_x'),e.get('reward'),e.get('policy_id'),js(e)))
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
    args=ap.parse_args()
    import_session(args.source,args.database,args.attachments)
