#!/usr/bin/env python3
"""撤除本例SHA故障，重启同一owned child并验证合法v2同步恢复。"""
import ast
import json
import hashlib
import time
from pathlib import Path
import backend_cases as h
import start_fault_child as starter

CASE = 'BE-SCRIPT-SHA-002'
GOOD_ID = '6d3f43cb-cdf1-4729-b3f1-53a3d69ab0b1'
BAD_ID = '92c0e242-0f67-4553-92e0-f7b8ffe675e4'
GOOD_TASK_ID = '959630b9-0184-43aa-8da4-1824c989b860'
EXPECTED_MANIFEST = 'c169c141500c9a9e433a47025fe7b6547e342c7c10c2ca2ac002fd7bb041b3df'
EXPECTED_PROXY_SHA = '7573c838dc562af47c5903c6f72c1c3ab4b8a4c7f3a0f8ee3042f753ad51103d'


def main():
    clear_source = '''import hashlib,json,os,stat\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";m=json.loads(mp.read_bytes());c=p/"controls.json"\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif (p.stat().st_dev,p.stat().st_ino,p.stat().st_uid)!=(33,135,65534) or sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or m["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5" or (p/"owner.marker").read_text()!=m["owner_marker"]:raise RuntimeError("owned_manifest_guard_failed")\ntry:Path("/proc/"+str(m["child"]["pid"])+"/stat").read_text();raise RuntimeError("child_still_running_before_fault_clear")\nexcept FileNotFoundError:pass\nst=c.lstat()\nif c.is_symlink() or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600 or st.st_uid!=65534:raise RuntimeError("private_controls_guard_failed")\ncurrent=json.loads(c.read_text())\nif current!={"corrupt_script_sha":%r}:raise RuntimeError("fault_control_target_mismatch")\nt=c.with_suffix(".json.tmp");t.write_text("{}");os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","fault_cleared":True,"controls_sha256":sha(c),"controls_mode":"0o600","controls_uid":c.stat().st_uid,"child_stopped":True,"owner_manifest_sha256":sha(mp)},separators=(",",":")))\n''' % (EXPECTED_MANIFEST, BAD_ID)
    clear = h.create_native4_command(CASE + '-clear-corrupt-script-sha', clear_source, timeout=20)
    if clear.get('status') != 'SUCCESS' or clear.get('output', {}).get('status') != 'PASS':
        raise RuntimeError('fault_clear_failed')
    h.atomic(h.HERE / 'sha-fault-clear.json', {'task_id': clear['task_id'], 'execution_id': clear['execution_id'], 'command_sha256': clear['command_sha256'], 'status': clear['status'], 'output': clear['output']})

    restart_source = starter.SOURCE.replace('14f3adb750b9021b87856e7794f3126cd71a0dc338c20b86719c89bb497b907c', EXPECTED_MANIFEST)
    restart_source = restart_source.replace('7573c838dc562af47c5903c6f72c1c3ab4b8a4c7f3a0f8ee3042f753ad51103d', EXPECTED_PROXY_SHA)
    old_control = 'if fault!={"corrupt_script_sha":"92c0e242-0f67-4553-92e0-f7b8ffe675e4"}:raise RuntimeError("fault_target_guard_failed")'
    new_control = 'if fault!={}:raise RuntimeError("fault_controls_not_empty")'
    if restart_source.count(old_control) != 1:
        raise RuntimeError('restart_guard_template_drift')
    restart_source = restart_source.replace(old_control, new_control)
    ast.parse(restart_source)
    start = h.create_native4_command(CASE + '-restart-child-after-fault-clear', restart_source, timeout=25)
    if start.get('status') != 'SUCCESS' or start.get('output', {}).get('status') != 'PASS':
        raise RuntimeError('child_restart_after_fault_clear_failed')
    output = start['output']
    h.atomic(h.HERE / 'owned-process-ledger.json', output)

    token = h.TOKEN
    deadline = time.monotonic() + 40
    node = None
    while time.monotonic() < deadline:
        node = h.api_get('/api/nodes/' + h.url(h.CHILD))
        if node.get('status') == 'online' and node.get('sync_status') == 'synced':
            break
        time.sleep(.5)
    if not node or node.get('status') != 'online' or node.get('sync_status') != 'synced':
        h.checkpoint(CASE, 'restore-sync-blocked', node_status=node.get('status') if node else None, sync_status=node.get('sync_status') if node else None, child=output['child'])
        raise RuntimeError('child_did_not_resync_after_fault_clear')

    task = h.api_get('/api/tasks/' + h.url(GOOD_TASK_ID))
    run = h.run_task(task, CASE + '-v2-good-recovery-run')
    query = 'SELECT id,content,revision,sha256 FROM scripts WHERE id IN (?,?) ORDER BY id'
    db = h.child_db_wait(CASE + '-v2-recovery-db', query, (GOOD_ID, BAD_ID), expected_count=2, seconds=20)
    rows = db.get('output', {}).get('rows', [])
    good = h.api_get('/api/scripts/' + h.url(GOOD_ID))
    bad = h.api_get('/api/scripts/' + h.url(BAD_ID))
    if len(rows) != 2:
        raise RuntimeError('post_recovery_scripts_missing')
    local = {row['id']: row for row in rows}
    for script in (good, bad):
        row = local[script['id']]
        if row.get('content') != script.get('content') or row.get('revision') != 2 or row.get('sha256') != script.get('sha256'):
            raise RuntimeError('post_recovery_script_not_canonical_v2')
    root = '/tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-script-sha-8a4084bf69'
    files = h.child_file_probe(CASE + '-v2-recovery-file-oracle', {'good': root + '/good.txt', 'bad': root + '/bad.txt', 'injected': root + '/injected.txt'})
    values = files.get('output') or {}
    if run.get('status') != 'SUCCESS' or values.get('good') != 'good-v2' or values.get('bad') is not None or values.get('injected') is not None:
        raise RuntimeError('post_recovery_business_effect_oracle_failed')
    h.checkpoint(CASE, 'PASS', good_script_id=GOOD_ID, bad_script_id=BAD_ID, good_task_id=GOOD_TASK_ID, bad_task_id='5d0e1e0c-c4a6-4675-a3a2-a6fbf9e51d39',
                 fault_event_evidence='sha-fault-frame-events.json', local_negative_oracle='sha-fault-local-oracle.json', baseline_local_revision=692,
                 fault_ack={'ok': False, 'applied_rev': 0, 'error_sha256': '9218fcd8637f5067affa03b3ef7d6a87ebd46fa7cfb866e5363cf00c02cc685c'},
                 recovery={'node_status': node.get('status'), 'sync_status': node.get('sync_status'), 'local_scripts': [{'id': r['id'], 'revision': r['revision'], 'sha256': r['sha256'], 'content_sha256': hashlib.sha256(r['content'].encode()).hexdigest()} for r in rows], 'execution_id': run['id'], 'execution_status': run['status'], 'good_file_sha256': hashlib.sha256(b'good-v2').hexdigest(), 'bad_file_absent': True, 'injected_marker_absent': True},
                 child=output['child'], supervisor=output['supervisor'], owner_manifest_sha256=output['owner_manifest_sha256'], proxy_source_sha256=output['proxy_source_sha256'], next_step='核对真实Execution页面反馈后，按本批精确ledger清理Task与Script，最后处理重新纳管例。')
    print(json.dumps({'status':'PASS','phase':'recovery','execution_id':run['id'],'good_file':'good-v2','sync_status':node.get('sync_status')},ensure_ascii=False))


if __name__ == '__main__':
    main()
