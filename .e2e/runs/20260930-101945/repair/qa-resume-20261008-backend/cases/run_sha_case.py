#!/usr/bin/env python3
"""BE-SCRIPT-SHA-002：先记录修复版本真实基线，再执行两Script坏SHA拒帧与恢复。"""
import hashlib
import json
import time
import uuid
from pathlib import Path
import backend_cases as h

CASE = 'BE-SCRIPT-SHA-002'
REPAIR_IDS = sorted(h.OLD_IDS)


def main():
    h.checkpoint(CASE, 'preflight')
    node = h.api_get('/api/nodes/' + h.url(h.CHILD))
    if node.get('id') != h.CHILD or node.get('agent_id') != 'agent-ab15a133-73cd-4025-8c97-f2ff37e000f5' or node.get('status') != 'online' or node.get('sync_status') != 'synced':
        raise RuntimeError('child_identity_or_health_preflight_failed')
    peers = h.api_get('/api/nodes')
    by_id = {row.get('id'): row for row in peers if isinstance(row, dict)}
    if not set(REPAIR_IDS).issubset(by_id) or any(by_id[rid].get('status') != 'online' or by_id[rid].get('sync_status') != 'synced' for rid in REPAIR_IDS):
        raise RuntimeError('original_repair_peers_not_healthy')

    suffix = uuid.uuid4().hex[:10]
    root = h.OWNED_DIR + '/data/qa-be-script-sha-' + suffix
    good_path, bad_path = root + '/good.txt', root + '/bad.txt'
    good_v1 = 'mkdir -p ' + root + '; printf good-v1 > ' + good_path
    bad_v1 = 'mkdir -p ' + root + '; printf bad-v1 > ' + bad_path
    good = h.create_resource('script', {'name': 'qa-resume-20261008-scriptsha-good-' + suffix, 'interpreter': 'shell', 'content': good_v1, 'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 15, 'enabled': True}, CASE + '-good-v1')
    bad = h.create_resource('script', {'name': 'qa-resume-20261008-scriptsha-bad-' + suffix, 'interpreter': 'shell', 'content': bad_v1, 'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 15, 'enabled': True}, CASE + '-bad-v1')
    task = h.create_task('qa-resume-20261008-scriptsha-good-task-' + suffix, '', task_type='script', script_id=good['id'])
    h.checkpoint(CASE, 'definitions-created', good_script_id=good['id'], bad_script_id=bad['id'], task_id=task['id'], owned_directory=root,
                 scripts={'good': {'revision': good.get('revision'), 'sha256': good.get('sha256'), 'content_sha256': hashlib.sha256(good_v1.encode()).hexdigest()}, 'bad': {'revision': bad.get('revision'), 'sha256': bad.get('sha256'), 'content_sha256': hashlib.sha256(bad_v1.encode()).hexdigest()}},
                 initial_global_revision=node.get('global_revision'))

    # 真实Agent本地SQLite以mode=ro读取；等待两脚本都落入本地，而后才执行。
    deadline = time.monotonic() + 30
    baseline = None
    query = 'SELECT id,name,content,revision,sha256 FROM scripts WHERE id IN (?,?) ORDER BY id'
    while time.monotonic() < deadline:
        probe = h.child_db_probe(CASE + '-baseline-db-' + uuid.uuid4().hex[:6], query, (good['id'], bad['id']))
        if probe.get('status') == 'SUCCESS':
            try:
                rows = probe.get('output') if isinstance(probe.get('output'), list) else []
            except (ValueError, TypeError):
                rows = []
            if len(rows) == 2 and {r['id'] for r in rows} == {good['id'], bad['id']}:
                baseline = rows
                break
        time.sleep(1)
    if baseline is None:
        raise RuntimeError('child_sqlite_did_not_sync_both_v1_scripts')
    revision_probe = h.child_db_probe(CASE + '-baseline-revision', 'SELECT global_revision FROM sync_state WHERE id=1')
    if revision_probe.get('status') != 'SUCCESS' or not isinstance(revision_probe.get('output'), list) or len(revision_probe['output']) != 1:
        raise RuntimeError('child_sync_revision_probe_failed')
    base_rev = revision_probe['output'][0]['global_revision']
    actual = {r['id']: r for r in baseline}
    for script, content in ((good, good_v1), (bad, bad_v1)):
        row = actual[script['id']]
        if row.get('content') != content or row.get('revision') != script.get('revision') or row.get('sha256') != script.get('sha256'):
            raise RuntimeError('child_v1_script_baseline_mismatch')
    run = h.run_task(task, CASE + '-v1-good-run')
    if run.get('stdout', '').strip() != '':
        # Script redirects output to its own file; stdout being empty is part of the command design.
        raise RuntimeError('v1_good_task_stdout_unexpected')
    file_probe = h.child_file_probe(CASE + '-v1-file-oracle', {'good': good_path, 'bad': bad_path})
    try:
        file_values = file_probe.get('output', {})
    except (ValueError, TypeError):
        file_values = {}
    if file_values.get('good') != 'good-v1' or file_values.get('bad') is not None:
        raise RuntimeError('v1_real_file_oracle_mismatch')
    h.checkpoint(CASE, 'v1-baseline-pass', good_script_id=good['id'], bad_script_id=bad['id'], task_id=task['id'], baseline_global_revision=base_rev,
                 local_scripts=[{'id': r['id'], 'revision': r['revision'], 'sha256': r['sha256'], 'content_sha256': hashlib.sha256(r['content'].encode()).hexdigest()} for r in baseline],
                 good_execution_id=run['id'], good_execution_status=run['status'], good_file_sha256=hashlib.sha256(b'good-v1').hexdigest(), bad_file_absent=True,
                 next_step='先保存坏帧阶段的定义/源SHA，再停止仅owned child，Hub同时更新两脚本v2并只针对bad脚本内容篡改声明SHA保持不变。')
    print(json.dumps({'status': 'PASS', 'phase': 'v1-baseline', 'good_script_id': good['id'], 'bad_script_id': bad['id'], 'task_id': task['id'], 'global_revision': base_rev}, ensure_ascii=False))


if __name__ == '__main__':
    main()
