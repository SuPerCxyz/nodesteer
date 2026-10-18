#!/usr/bin/env python3
"""Remote TTL合法控制组：历史Task实效成功，Remote条件Task成功并写入owned marker。"""
import hashlib
import json
import uuid
import backend_cases as h

CASE='BE-REMOTE-TTL-004'
ROOT='/tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-remote-ttl'


def main():
    node=h.api_get('/api/nodes/'+h.url(h.CHILD))
    if node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('child_not_online_synced_before_remote_control')
    suffix=uuid.uuid4().hex[:8]
    history_path=ROOT+'/'+suffix+'/history.txt'
    history=h.create_task('qa-resume-20261008-remote-history-'+suffix,'mkdir -p '+ROOT+'/'+suffix+'; printf history-success > '+history_path,timeout=30)
    if history.get('target',{}).get('node_ids')!=[h.CHILD] or history.get('retry')!=0 or history.get('schedule'):raise RuntimeError('history_task_definition_guard_failed')
    history_run=h.run_task(history,CASE+'-history-control')
    history_file=h.child_file_probe(CASE+'-history-file-oracle',{'history':history_path})
    if (history_file.get('output') or {}).get('history')!='history-success':raise RuntimeError('remote_history_task_effect_missing')
    condition={'type':'remote','remote':{'node_id':h.CHILD,'property':'last_execution','task_id':history['id'],'operator':'==','value':'SUCCESS'}}
    control_path=ROOT+'/'+suffix+'/valid-remote-control.txt'
    control=h.create_task('qa-resume-20261008-remote-valid-control-'+suffix,'mkdir -p '+ROOT+'/'+suffix+'; printf remote-control-success > '+control_path,condition=condition,timeout=40)
    if control.get('target',{}).get('node_ids')!=[h.CHILD] or control.get('retry')!=0 or control.get('schedule'):raise RuntimeError('control_task_definition_guard_failed')
    result=h.run_task(control,CASE+'-valid-control-run')
    files=h.child_file_probe(CASE+'-valid-control-file-oracle',{'history':history_path,'condition':control_path})
    values=files.get('output') or {}
    if result.get('status')!='SUCCESS' or values.get('history')!='history-success' or values.get('condition')!='remote-control-success':raise RuntimeError('valid_remote_condition_control_failed')
    # 记录真实Agent本地Journal终态与同步标记。
    journal=h.child_db_probe(CASE+'-valid-control-journal',"SELECT id,task_id,node_id,status,exit_code,synced,offline,block_reason FROM executions WHERE id=?",(result['id'],))
    rows=journal.get('output') if isinstance(journal.get('output'),list) else []
    if len(rows)!=1 or rows[0].get('status')!='SUCCESS' or not rows[0].get('synced'):raise RuntimeError('valid_remote_condition_journal_mismatch')
    cp={'case_id':CASE,'step':'valid-control-PASS','history_task_id':history['id'],'history_execution_id':history_run['id'],'condition_task_id':control['id'],'condition_execution_id':result['id'],'child_node_id':h.CHILD,'condition':condition,'history_file':history_path,'history_content_sha256':hashlib.sha256(b'history-success').hexdigest(),'condition_file':control_path,'condition_content_sha256':hashlib.sha256(b'remote-control-success').hexdigest(),'condition_status':result['status'],'local_journal':rows[0],'fault_controls_empty':True,'next_step':'仅对匹配此history Task的Remote响应逐项设置单一观察时间/TTL/值变异，每个Execution及marker独立。'}
    h.atomic(h.HERE/'BE-REMOTE-TTL-004-checkpoint.json',cp)
    print(json.dumps({'status':'PASS','history_task_id':history['id'],'history_execution_id':history_run['id'],'control_task_id':control['id'],'control_execution_id':result['id'],'effect':'remote-control-success'},ensure_ascii=False))


if __name__=='__main__':main()
