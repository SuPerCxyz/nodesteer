#!/usr/bin/env python3
"""撤除所有值/时间/TTL变异，真实Remote条件恢复SUCCESS并生成独占marker。"""
import hashlib
import json
import uuid
import backend_cases as h
import run_remote_valid_control_delayed as controls

CASE='BE-REMOTE-TTL-004'
ROOT='/tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-remote-ttl'


def main():
    cp=json.loads((h.HERE/'BE-REMOTE-TTL-004-checkpoint.json').read_text())
    if cp.get('step')!='negative-cases-PASS' or len(cp.get('completed_negative_cases',[]))!=7:raise RuntimeError('all_remote_negative_cases_not_complete')
    history_task_id=cp['history_task_id']
    history=h.api_get('/api/tasks/'+h.url(history_task_id))
    suffix=uuid.uuid4().hex[:8];folder=ROOT+'/'+suffix;marker=folder+'/recovery-success.txt'
    condition={'type':'remote','remote':{'node_id':h.CHILD,'property':'last_execution','task_id':history_task_id,'operator':'==','value':'SUCCESS'}}
    task=h.create_task('qa-resume-20261008-remote-recovery-'+suffix,'mkdir -p '+folder+'; printf remote-recovery-success > '+marker,condition=condition,timeout=45)
    if task.get('target',{}).get('node_ids')!=[h.CHILD] or task.get('retry')!=0 or task.get('schedule') or task.get('condition')!=condition:raise RuntimeError('recovery_task_definition_guard_failed')
    rule={'target_node_id':h.CHILD,'property':'last_execution','task_id':history_task_id,'observe_only':True,'delay_ms':500}
    armed=h.create_native4_command(CASE+'-arm-recovery-observe-delay',controls.controls_source(rule),timeout=20)
    if armed.get('status')!='SUCCESS' or armed.get('output',{}).get('status')!='PASS':raise RuntimeError('recovery_observe_delay_arm_failed')
    result=h.run_task(task,CASE+'-recovery-run',expected_status='SUCCESS',timeout=90)
    events_source='''import json\nfrom pathlib import Path\nrows=[]\nfor l in Path("/tmp/qa-resume-20261008-child-ciivjupc/proxy-events.jsonl").read_text().splitlines()[-1000:]:\n try:\n  x=json.loads(l)\n  if x.get("event") in {"remote_request","remote_override","remote_delivery_delay"}:rows.append(x)\n except Exception:pass\nprint(json.dumps(rows[-40:],separators=(",",":")))\n'''
    events=h.create_native4_command(CASE+'-recovery-proxy-events',events_source,timeout=30)
    clear=h.create_native4_command(CASE+'-clear-recovery-observe-delay',controls.clear_source(rule),timeout=20)
    if clear.get('status')!='SUCCESS' or clear.get('output',{}).get('status')!='PASS':raise RuntimeError('recovery_observe_delay_clear_failed')
    event_rows=events.get('output') if isinstance(events.get('output'),list) else []
    override=next((e for e in reversed(event_rows) if e.get('event')=='remote_override' and e.get('request',{}).get('task_id')==history_task_id),None)
    delay=next((e for e in reversed(event_rows) if e.get('event')=='remote_delivery_delay' and e.get('request',{}).get('task_id')==history_task_id),None)
    if not override or not delay or override.get('before')!=override.get('after') or override.get('before',{}).get('value')!='SUCCESS':raise RuntimeError('recovery_remote_payload_not_canonical')
    file_probe=h.child_file_probe(CASE+'-recovery-file-oracle',{'marker':marker})
    if (file_probe.get('output') or {}).get('marker')!='remote-recovery-success':raise RuntimeError('remote_recovery_marker_missing')
    journal=h.child_db_probe(CASE+'-recovery-journal',"SELECT id,task_id,node_id,status,exit_code,synced,offline,block_reason FROM executions WHERE id=?",(result['id'],))
    rows=journal.get('output') if isinstance(journal.get('output'),list) else []
    if len(rows)!=1 or rows[0].get('status')!='SUCCESS' or not rows[0].get('synced'):raise RuntimeError('remote_recovery_journal_mismatch')
    cp.update({'step':'negative-cases-and-recovery-PASS','recovery':{'task_id':task['id'],'execution_id':result['id'],'status':result['status'],'marker_path':marker,'marker_sha256':hashlib.sha256(b'remote-recovery-success').hexdigest(),'local_journal':rows[0],'remote_payload_before_after_equal':True,'payload':override.get('before'),'delivery_delay_ms':delay.get('delay_ms'),'fault_rules_cleared':True,'evidence':['BE-REMOTE-TTL-004-recovery-run-execution.json','BE-REMOTE-TTL-004-recovery-file-oracle-task-result.json']},'next_step':'完成每条BLOCKED Execution UI实际reason核对；随后清理本case精确Tasks。'})
    h.atomic(h.HERE/'BE-REMOTE-TTL-004-checkpoint.json',cp)
    h.atomic(h.HERE/'remote-recovery-diagnostic.json',{'recovery_execution_id':result['id'],'remote_request_id':override.get('id'),'original_remote_payload':override.get('before'),'no_payload_fields_changed':override.get('before')==override.get('after'),'delivery_delay_ms':delay.get('delay_ms'),'fault_rule_cleared':clear.get('output')})
    print(json.dumps({'status':'PASS','execution_id':result['id'],'marker':'remote-recovery-success','remote_value':override['before']['value'],'delay_ms':delay['delay_ms']},ensure_ascii=False))


if __name__=='__main__':main()
