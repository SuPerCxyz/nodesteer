#!/usr/bin/env python3
"""准备BE-SCRIPT-SHA-002坏帧：先保存真实v1，再停child、更新两个Script并设置唯一代理规则。"""
import hashlib
import json
import uuid
from pathlib import Path
import backend_cases as h

CASE = 'BE-SCRIPT-SHA-002'
GOOD_ID = '6d3f43cb-cdf1-4729-b3f1-53a3d69ab0b1'
BAD_ID = '92c0e242-0f67-4553-92e0-f7b8ffe675e4'
GOOD_TASK_ID = '959630b9-0184-43aa-8da4-1824c989b860'
BAD_TASK_ID = 'f965ddc2-CHANGE-AFTER-LOOKUP'
GOOD_V2 = 'mkdir -p /tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-script-sha-8a4084bf69; printf good-v2 > /tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-script-sha-8a4084bf69/good.txt'
BAD_V2 = 'mkdir -p /tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-script-sha-8a4084bf69; printf bad-v2 > /tmp/qa-resume-20261008-child-ciivjupc/data/qa-be-script-sha-8a4084bf69/bad.txt'


def stop_source(expected_manifest, child):
    return '''import hashlib,json,os,signal,time\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";raw=mp.read_bytes();m=json.loads(raw)\ndef sha(x): return hashlib.sha256(Path(x).read_bytes()).hexdigest()\ndef start(pid):\n x=Path("/proc",str(pid),"stat").read_text();return int(x[x.rfind(")")+2:].split()[19])\ndef uid(pid):\n for x in Path("/proc",str(pid),"status").read_text().splitlines():\n  if x.startswith("Uid:"):return int(x.split()[1])\n raise RuntimeError("uid_missing")\ndef argv(pid):return [x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(bytes([0])) if x]\ndef match(row):\n pid=int(row["pid"]);return start(pid)==int(row["starttime"]) and uid(pid)==int(row["uid"])==65534 and sha("/proc/"+str(pid)+"/exe")==row["exe_sha256"] and any(a==str(p) or a.startswith(str(p)+os.sep) for a in argv(pid))\nif (p.stat().st_dev,p.stat().st_ino,p.stat().st_uid)!=(33,135,65534) or sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or m["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5" or (p/"owner.marker").read_text()!=m["owner_marker"]:raise RuntimeError("owned_directory_manifest_guard_failed")\nif m["child"]["pid"]!=%d or m["child"]["starttime"]!=%d or m["child"]["exe_sha256"]!="%s" or not match(m["child"]) or not match(m["supervisor"]):raise RuntimeError("owned_process_identity_guard_failed_no_signal")\npid=m["child"]["pid"];os.kill(pid,signal.SIGTERM);end=time.monotonic()+6\nwhile time.monotonic()<end:\n try:\n  if start(pid)!=m["child"]["starttime"]:break\n except OSError:break\n time.sleep(.1)\nelse:\n if match(m["child"]):os.kill(pid,signal.SIGKILL)\nend=time.monotonic()+3\nwhile time.monotonic()<end:\n try:\n  if start(pid)!=m["child"]["starttime"]:break\n except OSError:break\n time.sleep(.1)\nelse:raise RuntimeError("owned_child_stop_failed")\nprint(json.dumps({"status":"PASS","child_stopped":True,"supervisor_still_owned":match(m["supervisor"]),"node_id":m["node_id"],"child_pid":pid,"manifest_sha256":sha(mp)},separators=(",",":")))\n''' % (expected_manifest, child['pid'], child['starttime'], child['exe_sha256'])


def controls_source(expected_manifest, bad_id):
    return '''import hashlib,json,os,stat\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";raw=mp.read_bytes();m=json.loads(raw)\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif (p.stat().st_dev,p.stat().st_ino,p.stat().st_uid)!=(33,135,65534) or sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or m["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5":raise RuntimeError("owned_manifest_guard_failed")\nchild=m["child"]\ntry:Path("/proc/"+str(child["pid"])+"/stat").read_text();raise RuntimeError("child_still_alive_before_fault_arm")\nexcept FileNotFoundError:pass\nc=p/"controls.json";st=c.lstat()\nif c.is_symlink() or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600 or st.st_uid!=65534:raise RuntimeError("controls_private_file_guard_failed")\ncurrent=json.loads(c.read_text())\nif current:raise RuntimeError("unexpected_existing_proxy_controls")\ncurrent["corrupt_script_sha"]=%r\nt=c.with_suffix(".json.tmp");t.write_text(json.dumps(current,separators=(",",":")));os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","node_id":m["node_id"],"corrupt_script_id":current["corrupt_script_sha"],"controls_sha256":sha(c),"controls_mode":"0o600","controls_uid":c.stat().st_uid,"child_stopped":True},separators=(",",":")))\n''' % (expected_manifest, bad_id)


def main():
    node = h.api_get('/api/nodes/' + h.url(h.CHILD))
    if node.get('status') != 'online' or node.get('sync_status') != 'synced':
        raise RuntimeError('child_not_online_before_fault_setup')
    good = h.api_get('/api/scripts/' + h.url(GOOD_ID))
    bad = h.api_get('/api/scripts/' + h.url(BAD_ID))
    good_task = h.api_get('/api/tasks/' + h.url(GOOD_TASK_ID))
    tasks = h.api_get('/api/tasks')
    bad_tasks = [row for row in tasks if row.get('script_id') == BAD_ID and row.get('target', {}).get('node_ids') == [h.CHILD] and row.get('retry') == 0 and not row.get('schedule')]
    if not bad_tasks or good_task.get('target', {}).get('node_ids') != [h.CHILD] or good_task.get('retry') != 0 or good_task.get('schedule'):
        raise RuntimeError('two_owned_script_tasks_missing_or_misassigned')
    bad_task = bad_tasks[0]

    query = 'SELECT id,content,revision,sha256 FROM scripts WHERE id IN (?,?) ORDER BY id'
    before = h.child_db_probe(CASE + '-pre-fault-db', query, (GOOD_ID, BAD_ID))
    rows = before.get('output') if isinstance(before.get('output'), list) else []
    if len(rows) != 2:
        raise RuntimeError('child_v1_or_revision_baseline_missing')
    good_path = good['content'].split(' > ')[-1]
    bad_path = bad['content'].split(' > ')[-1]
    files = h.child_file_probe(CASE + '-pre-fault-files', {'good': good_path, 'bad': bad_path})
    file_values = files.get('output') or {}
    if file_values.get('good') != 'good-v1' or file_values.get('bad') is not None:
        raise RuntimeError('child_v1_file_oracle_missing')
    ledger = json.loads((h.HERE / 'owned-process-ledger.json').read_text())
    expected_manifest = ledger['owner_manifest_sha256']
    stopped = h.create_native4_command(CASE + '-stop-child-before-fault', stop_source(expected_manifest, ledger['child']), timeout=20)
    if stopped.get('status') != 'SUCCESS' or stopped.get('output', {}).get('status') != 'PASS':
        raise RuntimeError('owned_child_stop_failed')
    # Stop Task itself may advance a global revision. Read the stable local state only after the child is stopped.
    baseline_db = h.child_db_probe(CASE + '-post-stop-db', query, (GOOD_ID, BAD_ID))
    baseline_rev = h.child_db_probe(CASE + '-post-stop-revision', 'SELECT global_revision FROM sync_state WHERE id=1')
    rows = baseline_db.get('output') if isinstance(baseline_db.get('output'), list) else []
    if len(rows) != 2 or baseline_rev.get('status') != 'SUCCESS' or not baseline_rev.get('output'):
        raise RuntimeError('post_stop_child_baseline_probe_failed')
    local_rev = baseline_rev['output'][0]['global_revision']
    rows_by_id = {row['id']: row for row in rows}
    for script in (good, bad):
        row = rows_by_id[script['id']]
        if row.get('content') != script['content'] or row.get('revision') != script['revision'] or row.get('sha256') != script['sha256']:
            raise RuntimeError('post_stop_v1_script_baseline_mismatch')
    files = h.child_file_probe(CASE + '-post-stop-files', {'good': good_path, 'bad': bad_path})
    file_values = files.get('output') or {}
    if file_values.get('good') != 'good-v1' or file_values.get('bad') is not None:
        raise RuntimeError('post_stop_v1_file_oracle_mismatch')

    good2 = h.update_resource('script', GOOD_ID, {'content': GOOD_V2}, CASE + '-good-v2')
    bad2 = h.update_resource('script', BAD_ID, {'content': BAD_V2}, CASE + '-bad-v2')
    if good2.get('revision') != 2 or bad2.get('revision') != 2 or good2.get('sha256') != hashlib.sha256(GOOD_V2.encode()).hexdigest() or bad2.get('sha256') != hashlib.sha256(BAD_V2.encode()).hexdigest():
        raise RuntimeError('hub_v2_script_definition_mismatch')
    armed = h.create_native4_command(CASE + '-arm-corrupt-script-sha', controls_source(expected_manifest, BAD_ID), timeout=20)
    if armed.get('status') != 'SUCCESS' or armed.get('output', {}).get('status') != 'PASS':
        raise RuntimeError('proxy_fault_arm_failed')
    h.checkpoint(CASE, 'fault-armed-child-stopped', good_script_id=GOOD_ID, bad_script_id=BAD_ID, good_task_id=GOOD_TASK_ID, bad_task_id=bad_task['id'],
                 baseline_local_revision=local_rev, baseline_local_scripts=[{'id': row['id'], 'revision': row['revision'], 'sha256': row['sha256'], 'content_sha256': hashlib.sha256(row['content'].encode()).hexdigest()} for row in rows],
                 baseline_good_file_sha256=hashlib.sha256(b'good-v1').hexdigest(), baseline_bad_file_absent=True,
                 hub_v2=[{'id': x['id'], 'revision': x['revision'], 'sha256': x['sha256'], 'content_sha256': hashlib.sha256(x['content'].encode()).hexdigest()} for x in [good2,bad2]],
                 bad_declared_sha_held_constant=True, fault_target_script_id=BAD_ID, fault_task_definition_ids=[good_task['id'],bad_task['id']], child_stopped=True, proxy_control_task_output=armed.get('output'),
                 next_step='仅owned child重启；代理单帧记录应列出两Script v2并只将bad Script内容改为content SHA不匹配；随后检查ACK、Revision、SQLite及文件oracle。')
    print(json.dumps({'status':'PASS','phase':'fault-armed-child-stopped','bad_script_id':BAD_ID,'baseline_local_revision':local_rev,'good_v2_revision':good2['revision'],'bad_v2_revision':bad2['revision'],'bad_task_id':bad_task['id']},ensure_ascii=False))


if __name__ == '__main__':
    main()
