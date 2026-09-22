#!/usr/bin/env python3
"""Independent checks of provenance, grains and Ami's quantitative annotations."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from ami_annotations import SESSION_ID


def validate(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert c.execute('PRAGMA user_version').fetchone()[0]==2
    assert not c.execute('PRAGMA foreign_key_check').fetchall()
    for sha,raw in c.execute('SELECT sha256,bytes FROM source'):
        assert hashlib.sha256(raw).hexdigest()==sha
    for sid,sha in c.execute('SELECT id,source_sha256 FROM session'):
        source=sqlite3.connect(':memory:')
        source.deserialize(c.execute('SELECT bytes FROM source WHERE sha256=?',(sha,)).fetchone()[0])
        for table,destination in [('message','message'),('part','event')]:
            original=dict(source.execute('SELECT id,data FROM '+table))
            imported=dict(c.execute('SELECT id,raw_json FROM '+destination+' WHERE session_id=?',(sid,)))
            assert original==imported,table+' is not lossless'
        executive=c.execute('SELECT id,source_sha256,executive_version,status FROM executive_session WHERE session_id=?',(sid,)).fetchone()
        if executive:
            executive_id,executive_sha,version,status=executive
            assert version==1
            assert c.execute('SELECT count(*) FROM source WHERE sha256=?',(executive_sha,)).fetchone()[0]==1
            event_counts=dict(c.execute('SELECT kind,count(*) FROM executive_event WHERE executive_session_id=? GROUP BY kind',(executive_id,)))
            assert event_counts.get('begin')==1
            assert event_counts.get('finish',0)<=1
            if status=='finished':
                assert c.execute('SELECT count(*) FROM executive_metric WHERE executive_session_id=?',(executive_id,)).fetchone()[0]>0
        if sid!=SESSION_ID:
            print('PASS:',sid,'— lossless source, source hashes, foreign keys; Executive validated' if executive else '— lossless source, source hashes, foreign keys')
            source.close()
            continue
        for table in ['experiment','episode','snapshot','stimulus','strategy_evidence','finding_evidence']:
            assert c.execute('SELECT count(*) FROM '+table).fetchone()[0]>0
        metric=dict(c.execute('SELECT name,value FROM metric WHERE session_id=?',(sid,)))
        calls=list(c.execute('SELECT e.id,e.created_ms,t.name,t.status,t.input_json,t.output_json FROM tool_call t JOIN event e ON e.id=t.event_id WHERE e.session_id=? ORDER BY e.ordinal',(sid,)))
        assert metric['tool_calls']==len(calls)==605
        assert metric['manual_move_calls']==sum(r[2]=='game_v1_move' for r in calls)==431
        assert metric['manual_move_share']==431/605
        assert metric['tool_errors']==sum(r[3]=='error' for r in calls)==10
        for kind in ['training','run']:
            starts=[r for r in calls if r[2]=='gamelab_v1_'+kind+'_start']
            assert metric[kind+'_start_attempts']==len(starts)==8
            assert metric[kind+'_starts']==sum(bool(json.loads(r[5] or '{}').get('experiment_id')) for r in starts)==7
        eps=c.execute('SELECT count(*) FROM episode e JOIN experiment x ON x.id=e.experiment_id WHERE x.session_id=?',(sid,)).fetchone()[0]
        completed,requested=c.execute("SELECT sum(completed_episodes),sum(requested_episodes) FROM experiment WHERE session_id=? AND kind='training'",(sid,)).fetchone()
        assert eps==completed==metric['completed_training_episodes']==65
        assert requested==metric['requested_training_episodes']==290
        assert c.execute("SELECT count(*) FROM episode WHERE result!='timeout'").fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM experiment WHERE session_id=? AND kind='training' AND status='cancelled'",(sid,)).fetchone()[0]==7
        best=c.execute('SELECT min(abs(x-987)) FROM snapshot s JOIN event e ON e.id=s.event_id WHERE e.session_id=?',(sid,)).fetchone()[0]
        assert best==metric['best_observed_state_error']==0.5
        last=c.execute('SELECT x FROM snapshot s JOIN event e ON e.id=s.event_id WHERE e.session_id=? ORDER BY e.ordinal DESC LIMIT 1',(sid,)).fetchone()[0]
        assert last==metric['last_observed_x']==766
        promise=c.execute('SELECT created_ms FROM event WHERE id=?',('prt_0ca56ae3e001OuXkuxoFhdDw4l',)).fetchone()[0]
        post=[r for r in calls if r[2]=='game_v1_move' and r[1]>promise]
        assert len(post)==metric['manual_calls_after_no_manual_promise']==67
        assert (post[0][1]-promise)/1000==metric['seconds_to_manual_relapse']
        assert c.execute('SELECT count(*) FROM stimulus s JOIN event e ON e.id=s.event_id WHERE e.session_id=?',(sid,)).fetchone()[0]==20
        assert c.execute("SELECT count(*) FROM strategy_assessment WHERE session_id=? AND state='attempted'",(sid,)).fetchone()[0]==8
        assert c.execute('SELECT count(*) FROM finding WHERE session_id=? AND id NOT IN (SELECT finding_id FROM finding_evidence)',(sid,)).fetchone()[0]==0
        evaluations={r[0].split(':',1)[1]:(r[1],r[2]) for r in c.execute(
            'SELECT dimension_id,numeric_value,denominator FROM evaluation_result WHERE session_id=?',(sid,))}
        assert len(evaluations)==13
        assert evaluations['manual_share']==(431,605)
        assert evaluations['training_budget']==(completed,requested)
        assert evaluations['best_error']==(best,None)
        assert evaluations['advice_persistence']==(metric['seconds_to_manual_relapse'],None)
        assert evaluations['cost_per_success']==(None,0)
        assert c.execute('SELECT count(*) FROM evaluation_result r WHERE session_id=? AND NOT EXISTS '
                         '(SELECT 1 FROM evaluation_evidence e WHERE e.session_id=r.session_id AND e.dimension_id=r.dimension_id)',(sid,)).fetchone()[0]==0
        print('PASS:',sid,'— lossless source, source hashes, foreign keys, counts, episode deduplication, annotation metrics')
        source.close()
    c.close()


if __name__=='__main__':
    validate(sys.argv[1] if len(sys.argv)>1 else Path(__file__).with_name('brain_experience.sqlite3'))