#!/usr/bin/env python3
"""继续已创建的SHA fixture，不重建业务对象。"""
import hashlib
import json
from pathlib import Path
import backend_cases as h

CASE = 'BE-SCRIPT-SHA-002'


def main():
    state = json.loads((h.HERE / (CASE + '-checkpoint.json')).read_text())
    if state.get('step') not in {'definitions-created', 'definitions-created-with-both-dependencies', 'baseline-sync-blocked'}:
        raise RuntimeError('unexpected_sha_checkpoint')
    ids = state if 'good_script_id' in state else state.get('fixture_ids', {})
    ids = {**ids, 'good_script_id': ids.get('good_script_id', ids.get('good')), 'bad_script_id': ids.get('bad_script_id', ids.get('bad')), 'task_id': ids.get('task_id', ids.get('task'))}
    good = h.api_get('/api/scripts/' + h.url(ids['good_script_id']))
    bad = h.api_get('/api/scripts/' + h.url(ids['bad_script_id']))
    task = h.api_get('/api/tasks/' + h.url(ids['task_id']))
    bad_task = None
    if state.get('bad_task_id'):
        bad_task = h.api_get('/api/tasks/' + h.url(state['bad_task_id']))
    else:
        existing = [row for row in h.api_get('/api/tasks') if row.get('script_id') == bad['id'] and row.get('target', {}).get('node_ids') == [h.CHILD] and str(row.get('name', '')).startswith('qa-resume-20261008-scriptsha-bad-dependency-')]
        bad_task = existing[0] if existing else h.create_task('qa-resume-20261008-scriptsha-bad-dependency-' + bad['id'][:8], '', task_type='script', script_id=bad['id'])
    state['bad_task_id'] = bad_task['id']
    state['bad_task_name'] = bad_task.get('name')
    state['good_script_id'] = good['id']
    state['bad_script_id'] = bad['id']
    state['task_id'] = task['id']
    state['owned_directory'] = good['content'].split('mkdir -p ', 1)[1].split(';', 1)[0]
    h.atomic(h.HERE / (CASE + '-checkpoint.json'), {**state, 'step': 'definitions-created-with-both-dependencies'})
    query = 'SELECT id,name,content,revision,sha256 FROM scripts WHERE id IN (?,?) ORDER BY id'
    probe = h.child_db_wait(CASE + '-baseline-db-wait', query, (good['id'], bad['id']), expected_count=2, seconds=25)
    result = probe.get('output') or {}
    baseline = result.get('rows', []) if isinstance(result, dict) else []
    if result.get('ready') is not True or len(baseline) != 2:
        h.checkpoint(CASE, 'baseline-sync-blocked', good_script_id=good['id'], bad_script_id=bad['id'], task_id=task['id'], bad_task_id=bad_task['id'], child_probe=result, fixture_ids={'good': good['id'], 'bad': bad['id'], 'task': task['id'], 'bad_task': bad_task['id']})
        raise RuntimeError('child_sqlite_did_not_sync_both_v1_scripts')
    revision_probe = h.child_db_probe(CASE + '-baseline-revision', 'SELECT global_revision FROM sync_state WHERE id=1')
    if revision_probe.get('status') != 'SUCCESS' or not isinstance(revision_probe.get('output'), list) or len(revision_probe['output']) != 1:
        raise RuntimeError('child_sync_revision_probe_failed')
    base_rev = revision_probe['output'][0]['global_revision']
    actual = {row['id']: row for row in baseline}
    for script in (good, bad):
        row = actual[script['id']]
        if row.get('content') != script.get('content') or row.get('revision') != script.get('revision') or row.get('sha256') != script.get('sha256'):
            raise RuntimeError('child_v1_script_baseline_mismatch')
    run = h.run_task(task, CASE + '-v1-good-run')
    root = state['owned_directory']
    file_probe = h.child_file_probe(CASE + '-v1-file-oracle', {'good': root + '/good.txt', 'bad': root + '/bad.txt'})
    file_values = file_probe.get('output') or {}
    if file_values.get('good') != 'good-v1' or file_values.get('bad') is not None:
        raise RuntimeError('v1_real_file_oracle_mismatch')
    if run.get('status') != 'SUCCESS':
        raise RuntimeError('v1_good_script_did_not_succeed')
    h.checkpoint(CASE, 'v1-baseline-pass', good_script_id=good['id'], bad_script_id=bad['id'], task_id=task['id'], bad_task_id=bad_task['id'], baseline_global_revision=base_rev,
                 local_scripts=[{'id': row['id'], 'revision': row['revision'], 'sha256': row['sha256'], 'content_sha256': hashlib.sha256(row['content'].encode()).hexdigest()} for row in baseline],
                 good_execution_id=run['id'], good_execution_status=run['status'], good_file_sha256=hashlib.sha256(b'good-v1').hexdigest(), bad_file_absent=True,
                 next_step='停止child，更新两Script为v2，并在单个SYNC_RESPONSE中仅改变bad脚本内容而保持其declared SHA不变。')
    print(json.dumps({'status':'PASS','phase':'v1-baseline','good_script_id':good['id'],'bad_script_id':bad['id'],'task_id':task['id'],'global_revision':base_rev},ensure_ascii=False))


if __name__ == '__main__':
    main()
