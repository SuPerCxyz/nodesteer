#!/usr/bin/env python3
"""逐项真实RemoteState负例；每次只变一个协议字段并核Journal/API/实际marker。"""
import json
import time
import uuid
import backend_cases as h

CASE='BE-REMOTE-TTL-004'
ROOT='/tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-remote-ttl'
CASES=[
 {'id':'expired_observed_at_ttl1','changes':{'observed_at':'2000-01-01T00:00:00Z','ttl_sec':1}},
 {'id':'ttl_zero','changes':{'ttl_sec':0}},
 {'id':'ttl_negative','changes':{'ttl_sec':-1}},
 {'id':'observed_at_malformed','changes':{'observed_at':'not-a-time'}},
 {'id':'observed_at_future','changes':{'observed_at':'2999-12-31T23:59:59Z'}},
 {'id':'value_unknown','changes':{'value':'UNKNOWN'}},
 {'id':'value_empty','changes':{'value':''}},
]


def arm_source(manifest, rule):
    return '''import hashlib,json,os,stat\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";m=json.loads(mp.read_bytes());c=p/"controls.json"\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif (p.stat().st_dev,p.stat().st_ino,p.stat().st_uid)!=(33,135,65534) or sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or m["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5":raise RuntimeError("owner_guard_failed")\nif json.loads(c.read_text())!={}:raise RuntimeError("controls_not_empty")\nr=%r\nt=c.with_suffix(".json.tmp");t.write_text(json.dumps({"remote_override":r},separators=(",",":")));os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","controls_sha256":sha(c),"controls_mode":"0o600","controls_uid":c.stat().st_uid,"fault_fields":sorted(k for k in r if k not in {"target_node_id","property","task_id","delay_ms"}),"delay_ms":r["delay_ms"]},separators=(",",":")))\n'''%(manifest,rule)


def clear_source(manifest, rule):
    return '''import hashlib,json,os\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";m=json.loads(mp.read_bytes());c=p/"controls.json"\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea":raise RuntimeError("owner_guard_failed")\nif json.loads(c.read_text())!={"remote_override":%r}:raise RuntimeError("exact_rule_guard_failed")\nt=c.with_suffix(".json.tmp");t.write_text("{}");os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","controls_empty":True,"controls_sha256":sha(c)},separators=(",",":")))\n'''%(manifest,rule)


def read_remote_events_source(since):
    return '''import json\nfrom pathlib import Path\nsince=%r;rows=[]\nfor line in Path("/tmp/qa-resume-20261008-child-ciivjupc/proxy-events.jsonl").read_text().splitlines():\n try:\n  x=json.loads(line)\n  if x.get("time",0)>=since and x.get("event") in {"remote_request","remote_override","remote_delivery_delay"}:rows.append(x)\n except Exception:pass\nprint(json.dumps(rows,separators=(",",":")))\n'''%since


def main():
    checkpoint=json.loads((h.HERE/'BE-REMOTE-TTL-004-checkpoint.json').read_text())
    if checkpoint.get('step')!='valid-control-PASS':raise RuntimeError('remote_valid_control_not_passed')
    history_task_id=checkpoint['history_task_id']
    history=h.api_get('/api/tasks/'+h.url(history_task_id))
    if history.get('target',{}).get('node_ids')!=[h.CHILD] or history.get('retry')!=0 or history.get('schedule'):
        raise RuntimeError('history_task_definition_guard_failed')
    manifest=json.loads((h.HERE/'owned-process-ledger.json').read_text())['owner_manifest_sha256']
    condition={'type':'remote','remote':{'node_id':h.CHILD,'property':'last_execution','task_id':history_task_id,'operator':'==','value':'SUCCESS'}}
    results=[]
    for case in CASES:
        suffix=uuid.uuid4().hex[:8]
        marker=ROOT+'/'+suffix+'/'+case['id']+'.txt'
        command='mkdir -p '+ROOT+'/'+suffix+'; printf should-not-run > '+marker
        task=h.create_task('qa-resume-20261008-remote-'+case['id']+'-'+suffix,command,condition=condition,timeout=40)
        if task.get('target',{}).get('node_ids')!=[h.CHILD] or task.get('retry')!=0 or task.get('schedule') or task.get('condition')!=condition:
            raise RuntimeError(case['id']+'_task_definition_guard_failed')
        rule={'target_node_id':h.CHILD,'property':'last_execution','task_id':history_task_id,'delay_ms':500,**case['changes']}
        armed=h.create_native4_command(CASE+'-'+case['id']+'-arm',arm_source(manifest,rule),timeout=20)
        if armed.get('status')!='SUCCESS' or armed.get('output',{}).get('status')!='PASS':raise RuntimeError(case['id']+'_rule_arm_failed')
        since=time.time()
        run_error=None
        try:
            result=h.run_task(task,CASE+'-'+case['id']+'-blocked-run',expected_status='BLOCKED',timeout=90)
        except RuntimeError as error:
            run_error=str(error)
            execution_evidence=h.HERE/(CASE+'-'+case['id']+'-blocked-run-execution.json')
            result=json.loads(execution_evidence.read_text()) if execution_evidence.exists() else {}
        clear=h.create_native4_command(CASE+'-'+case['id']+'-clear',clear_source(manifest,rule),timeout=20)
        if clear.get('status')!='SUCCESS' or clear.get('output',{}).get('status')!='PASS':raise RuntimeError(case['id']+'_fault_clear_failed')
        if run_error:raise RuntimeError(case['id']+'_run_failed_'+run_error)
        api_execution=h.api_get('/api/executions/'+h.url(result['id']))
        if api_execution.get('status')!='BLOCKED' or not api_execution.get('block_reason'):
            raise RuntimeError(case['id']+'_hub_terminal_reason_missing')
        journal=h.child_db_probe(CASE+'-'+case['id']+'-journal',"SELECT id,task_id,node_id,status,exit_code,synced,offline,block_reason FROM executions WHERE id=?",(result['id'],))
        rows=journal.get('output') if isinstance(journal.get('output'),list) else []
        if len(rows)!=1 or rows[0].get('status')!='BLOCKED' or not rows[0].get('synced') or rows[0].get('block_reason')!=api_execution.get('block_reason'):
            raise RuntimeError(case['id']+'_hub_agent_journal_mismatch')
        file_probe=h.child_file_probe(CASE+'-'+case['id']+'-marker-oracle',{'marker':marker})
        if (file_probe.get('output') or {}).get('marker') is not None:
            raise RuntimeError(case['id']+'_blocked_task_created_business_marker')
        events_task=h.create_native4_command(CASE+'-'+case['id']+'-proxy-events',read_remote_events_source(since-1),timeout=25)
        events=events_task.get('output') if isinstance(events_task.get('output'),list) else []
        event_override=next((e for e in reversed(events) if e.get('event')=='remote_override'),None)
        request_id=(event_override or {}).get('id')
        request=next((e for e in reversed(events) if e.get('event')=='remote_request' and e.get('id')==request_id),None)
        delay=next((e for e in reversed(events) if e.get('event')=='remote_delivery_delay' and e.get('id')==request_id),None)
        if not event_override or not request or not delay:
            raise RuntimeError(case['id']+'_remote_request_response_association_missing')
        before=event_override.get('before',{});after=event_override.get('after',{})
        for key in ['value','observed_at','ttl_sec']:
            expected=case['changes'].get(key,before.get(key))
            if after.get(key)!=expected:raise RuntimeError(case['id']+'_single_field_override_mismatch_'+key)
        result_row={'id':case['id'],'task_id':task['id'],'execution_id':result['id'],'execution_status':api_execution['status'],'block_reason':api_execution['block_reason'],'local_journal':rows[0],'marker_absent':True,'remote_request':request,'remote_override':event_override,'remote_delivery_delay':delay,'controls_cleared':True,'changes':case['changes'],'evidence':[case['id']+'-blocked-run-execution.json',case['id']+'-journal-execution.json',case['id']+'-marker-oracle-task-result.json']}
        results.append(result_row)
        h.atomic(h.HERE/'BE-REMOTE-TTL-004-checkpoint.json',{**checkpoint,'step':'negative-cases-running','completed_negative_cases':results,'next_case':CASES[len(results)]['id'] if len(results)<len(CASES) else None})
        print(json.dumps({'case':case['id'],'status':'PASS','execution_id':result['id'],'block_reason':api_execution['block_reason'],'request_id':request_id},ensure_ascii=False))
    checkpoint.update({'step':'negative-cases-PASS','completed_negative_cases':results,'all_proxy_controls_cleared':True,'next_step':'核对Execution详情UI真实block_reason，再运行不注入的SUCCESS恢复控制组。'})
    h.atomic(h.HERE/'BE-REMOTE-TTL-004-checkpoint.json',checkpoint)


if __name__=='__main__':main()
