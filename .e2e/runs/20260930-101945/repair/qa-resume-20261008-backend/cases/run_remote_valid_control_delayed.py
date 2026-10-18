#!/usr/bin/env python3
"""真实Remote正向控制；响应内容不变，仅将同一Hub响应传输延时500ms以跨过已测时钟偏差。"""
import hashlib
import json
import uuid
import backend_cases as h

CASE='BE-REMOTE-TTL-004'
ROOT='/tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-remote-ttl'
MANIFEST='7a5bf21f70ec2610527f054809f40fbc17bb5dcbfd24f8af804237bc811f7cfc'


def controls_source(rule):
    return '''import hashlib,json,os,stat\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";m=json.loads(mp.read_bytes());c=p/"controls.json"\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif (p.stat().st_dev,p.stat().st_ino,p.stat().st_uid)!=(33,135,65534) or sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or m["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5":raise RuntimeError("owner_guard_failed")\nif json.loads(c.read_text())!={}:raise RuntimeError("controls_not_empty")\nvalue={"remote_override":%r}\nt=c.with_suffix(".json.tmp");t.write_text(json.dumps(value,separators=(",",":")));os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","controls_sha256":sha(c),"controls_mode":"0o600","controls_uid":c.stat().st_uid,"matching_request_id_only":True,"delivery_delay_ms":500},separators=(",",":")))\n'''%(MANIFEST,rule)


def clear_source(rule):
    return '''import hashlib,json,os\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";m=json.loads(mp.read_bytes());c=p/"controls.json"\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea":raise RuntimeError("owner_guard_failed")\nif json.loads(c.read_text())!={"remote_override":%r}:raise RuntimeError("observe_delay_rule_guard_failed")\nt=c.with_suffix(".json.tmp");t.write_text("{}");os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","controls_empty":True,"controls_sha256":sha(c)},separators=(",",":")))\n'''%(MANIFEST,rule)


def main():
    node=h.api_get('/api/nodes/'+h.url(h.CHILD))
    if node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('child_not_online_synced_before_control')
    suffix=uuid.uuid4().hex[:8];folder=ROOT+'/'+suffix
    history_path=folder+'/history.txt';marker=folder+'/valid-remote-control.txt'
    history=h.create_task('qa-resume-20261008-remote-history-valid-'+suffix,'mkdir -p '+folder+'; printf history-success > '+history_path,timeout=30)
    if history.get('target',{}).get('node_ids')!=[h.CHILD] or history.get('retry')!=0 or history.get('schedule'):raise RuntimeError('history_task_definition_guard_failed')
    history_run=h.run_task(history,CASE+'-fresh-history-run')
    history_file=h.child_file_probe(CASE+'-fresh-history-oracle',{'history':history_path})
    if history_run.get('status')!='SUCCESS' or (history_file.get('output') or {}).get('history')!='history-success':raise RuntimeError('fresh_history_not_real_success')
    condition={'type':'remote','remote':{'node_id':h.CHILD,'property':'last_execution','task_id':history['id'],'operator':'==','value':'SUCCESS'}}
    task=h.create_task('qa-resume-20261008-remote-valid-delayed-'+suffix,'mkdir -p '+folder+'; printf remote-control-success > '+marker,condition=condition,timeout=45)
    if task.get('target',{}).get('node_ids')!=[h.CHILD] or task.get('retry')!=0 or task.get('schedule'):raise RuntimeError('remote_control_task_definition_guard_failed')
    rule={'target_node_id':h.CHILD,'property':'last_execution','task_id':history['id'],'observe_only':True,'delay_ms':500}
    arm=h.create_native4_command(CASE+'-arm-observe-only-delay',controls_source(rule),timeout=20)
    if arm.get('status')!='SUCCESS' or arm.get('output',{}).get('status')!='PASS':raise RuntimeError('observe_only_delivery_delay_arm_failed')
    run_error=None
    try:
        result=h.run_task(task,CASE+'-fresh-valid-delayed-run',expected_status='SUCCESS')
    except RuntimeError as e:
        run_error=str(e);result=json.loads((h.HERE/'BE-REMOTE-TTL-004-fresh-valid-delayed-run-execution.json').read_text())
    # 保留当前响应与request关联摘要后，再撤销唯一observe-only规则。
    events_source='''import json\nfrom pathlib import Path\nrows=[]\nfor l in Path("/tmp/qa-resume-20261008-child-ciivjupc/proxy-events.jsonl").read_text().splitlines()[-800:]:\n try:\n  x=json.loads(l)\n  if x.get("event") in {"remote_request","remote_override","remote_delivery_delay"}:rows.append(x)\n except Exception:pass\nprint(json.dumps(rows[-50:],separators=(",",":")))\n'''
    events=h.create_native4_command(CASE+'-fresh-valid-delayed-events',events_source,timeout=30)
    clear=h.create_native4_command(CASE+'-clear-observe-only-delay',clear_source(rule),timeout=20)
    if clear.get('status')!='SUCCESS' or clear.get('output',{}).get('status')!='PASS':raise RuntimeError('observe_only_delivery_delay_clear_failed')
    if run_error:raise RuntimeError('real_remote_positive_control_failed:'+run_error)
    files=h.child_file_probe(CASE+'-fresh-valid-delayed-file-oracle',{'history':history_path,'condition':marker})
    values=files.get('output') or {}
    if result.get('status')!='SUCCESS' or values.get('history')!='history-success' or values.get('condition')!='remote-control-success':raise RuntimeError('fresh_remote_control_effect_missing')
    journal=h.child_db_probe(CASE+'-fresh-valid-delayed-journal',"SELECT id,task_id,node_id,status,exit_code,synced,offline,block_reason FROM executions WHERE id=?",(result['id'],))
    rows=journal.get('output') if isinstance(journal.get('output'),list) else []
    if len(rows)!=1 or rows[0].get('status')!='SUCCESS' or not rows[0].get('synced'):raise RuntimeError('fresh_remote_control_journal_mismatch')
    cp={'case_id':CASE,'step':'valid-control-PASS','history_task_id':history['id'],'history_execution_id':history_run['id'],'condition_task_id':task['id'],'condition_execution_id':result['id'],'child_node_id':h.CHILD,'condition':condition,'history_file':history_path,'condition_marker':marker,'condition_status':result['status'],'local_journal':rows[0],'real_response_observe_only':True,'proxy_delivery_delay_ms':500,'proxy_events_evidence':'BE-REMOTE-TTL-004-fresh-valid-delayed-events-task-result.json','control_marker_sha256':hashlib.sha256(b'remote-control-success').hexdigest(),'clock_skew_observation':'Hub ObservedAt was approximately 150ms ahead of local proxy clock; this response is delayed only in transport and payload bytes are unchanged.','next_step':'Run each TTL/value/time failure one factor at a time, clearing exact proxy rule after every Task.'}
    h.atomic(h.HERE/'BE-REMOTE-TTL-004-checkpoint.json',cp)
    h.atomic(h.HERE/'remote-valid-control-delayed-diagnostic.json',{'history_task':{'id':history['id'],'execution_id':history_run['id'],'status':history_run['status']},'control_task':{'id':task['id'],'execution_id':result['id'],'status':result['status']},'request_events':events.get('output'),'payload_changed':False,'delay_ms':500,'rule_cleared':clear.get('output')})
    print(json.dumps({'status':'PASS','history_task_id':history['id'],'control_execution_id':result['id'],'marker':'remote-control-success','delay_ms':500},ensure_ascii=False))


if __name__=='__main__':main()
