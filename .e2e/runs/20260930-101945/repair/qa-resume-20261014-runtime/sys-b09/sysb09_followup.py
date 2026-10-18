#!/usr/bin/env python3
"""sys-b09 follow-up: corrected Agent-DB slot readback + 4-node remote-condition defect spread.

Owned fixture: one command Task with a remote online condition, targeted at the four
protected repair native nodes, run once on each.  Deleted with exact GET404 afterwards.

Writes:
  evidence/phase10-remote-condition-multinode.json
  evidence/phase11-agent-db-readback-retry.json
  fixture-ledger-followup.json
"""
import json
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
EVID = HERE / 'evidence'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
NONCE = 'qa-sysb09-7f31ac2e'
NODES = {
    'native-1': '91f44866-8a28-435e-bbda-2f2367126c00',
    'native-2': '611c02a5-1859-4465-878b-d543b7f6fdec',
    'native-3': '37989297-3bc8-4c0c-b302-7c6efbdba630',
    'native-4': '03cb50cc-eab6-4afd-bbe0-f73c4e326773',
}
NODE_IPS = {'native-2': '192.168.100.206', 'native-4': '192.168.100.247'}
REMOTE_TARGET = '91f44866-8a28-435e-bbda-2f2367126c00'   # native-1 (online)
OFFLINE_NODE = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
KEY = str(Path.home() / '.ssh' / 'id_ed25519')

TOKEN = [None]
BASE = [None]
BROWSER = build_opener(ProxyHandler({}))


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {'_non_json_body': raw[:300].decode('utf-8', 'replace')}


def api(method, path, body=None, timeout=25):
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if TOKEN[0]:
        headers['Authorization'] = 'Bearer ' + TOKEN[0]
    try:
        with BROWSER.open(Request(BASE[0] + path, data=data, headers=headers, method=method),
                          timeout=timeout) as resp:
            return resp.status, _decode(resp.read())
    except HTTPError as exc:
        return exc.code, _decode(exc.read())


def get(path):
    return api('GET', path)


def parse_ts(value):
    from datetime import datetime, timezone
    if not value:
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def ssh_python(host, script, timeout=45):
    cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-i', KEY, 'root@' + host, 'python3 -'],
                        input=script, capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def last_json_line(out):
    for line in reversed(out.strip().splitlines()):
        if line.strip().startswith('{'):
            return json.loads(line)
    return None


def wait_execution(exec_id, timeout=60):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, last = api('GET', '/api/executions/%s' % exec_id)
        if code == 200 and last.get('status') in TERMINAL:
            return last
        time.sleep(1)
    return last


def main():
    cred = json.loads(CRED.read_text())
    conn = json.loads(CONN.read_text())
    BASE[0] = conn['hub_url'].rstrip('/')
    if urlsplit(BASE[0]).port != 18080:
        raise SystemExit('endpoint guard failed')
    code, body = api('POST', '/api/login', {'username': cred['admin_username'],
                                            'password': cred['admin_password']})
    if code != 200:
        raise SystemExit('login failed')
    TOKEN[0] = body['token']

    result = {'batch_id': 'sys-b09', 'followup_at_utc': now_utc(), 'nonce': NONCE}

    # ---- 1. remote-condition defect spread across all four protected nodes ---- #
    name = '%s-followup-remote-online-multinode' % NONCE
    code, task = api('POST', '/api/tasks', {
        'name': name, 'type': 'command', 'command': 'printf QA:%s:REMOTE_MULTINODE' % NONCE,
        'target': {'type': 'node', 'node_ids': list(NODES.values())},
        'parameters': [], 'param_values': {}, 'timeout': 15, 'retry': 0,
        'offline_policy': 'hub_online_required', 'enabled': True,
        'condition': {'type': 'remote', 'remote': {'node_id': REMOTE_TARGET, 'property': 'online',
                                                   'operator': '==', 'value': 'online'}}}, timeout=25)
    if code != 200:
        raise SystemExit('followup task create failed: %s %s' % (code, task))
    task_id = task['id']
    runs = {}
    try:
        code, resp = api('POST', '/api/tasks/%s/run' % task_id, {})
        if code != 200:
            raise SystemExit('followup run failed: %s %s' % (code, resp))
        for ex in resp:
            final = wait_execution(ex['id'])
            node_label = [k for k, v in NODES.items() if v == final.get('node_id')]
            runs[node_label[0] if node_label else final.get('node_id')] = {
                'execution_id': final.get('id'), 'status': final.get('status'),
                'block_reason': final.get('block_reason'),
                'stdout': (final.get('stdout') or '').strip(),
                'trigger_type': final.get('trigger_type'),
                'elapsed_s': round((parse_ts(final.get('end_time')) or 0)
                                   - (parse_ts(final.get('start_time')) or 0), 4),
                'condition_expected': 'SUCCESS (remote node online)',
            }
    finally:
        del_code, _ = api('DELETE', '/api/tasks/' + task_id)
        gcode, _gbody = api('GET', '/api/tasks/' + task_id)
        result['followup_task'] = {'id': task_id, 'name': name, 'delete_http': del_code,
                                   'get_http': gcode, 'deleted_get404': gcode == 404}
    result['remote_condition_multinode'] = {
        'remote_target_node': {'id': REMOTE_TARGET, 'label': 'native-1', 'expected_status': 'online'},
        'condition': 'remote online == online',
        'per_node': runs,
        'all_blocked_fail_closed': all(v['status'] == 'BLOCKED' for v in runs.values()),
    }
    (EVID / 'phase10-remote-condition-multinode.json').write_text(
        json.dumps(result['remote_condition_multinode'], ensure_ascii=False, indent=2) + '\n')

    # ---- 2. corrected Agent-local DB slot readback for the duplicate-trigger task ---- #
    case_result = json.loads(((HERE / 'case-result.json') if (HERE / 'case-result.json').exists()
                              else (EVID / 'case-result.json')).read_text())
    dup_agent_task = None
    for case in case_result['cases']:
        if case['case_id'] == 'SYS-22':
            dup_agent_task = case['actual_resources']['dup_agent_task_id']
            agent_slot = case['actual_budget']['identical_run_at_agent_utc']
            hub_slot = case['actual_budget']['identical_run_at_hub_utc']
            dup_hub_task = case['actual_resources']['dup_hub_task_id']
    readback_script = """
import sqlite3,json
p="/var/lib/nodesteer-repair-20260930-101945/native-2/state.db"
c=sqlite3.connect("file:"+p+"?mode=ro",uri=True,timeout=5)
c.execute("PRAGMA query_only=ON")
qk=c.execute("PRAGMA quick_check").fetchone()[0]
sq=c.execute("SELECT count(*) FROM sqlite_master WHERE type=? AND name=?",("index","idx_local_exec_slot")).fetchone()[0]
rev=c.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0]
ident=dict(c.execute("SELECT key,value FROM identity").fetchall())
rows=c.execute("SELECT id,task_id,node_id,trigger_type,scheduled_time,status,script_id FROM executions WHERE task_id=?",("__TASK__",)).fetchall()
slots=c.execute("SELECT scheduled_time,count(*) FROM executions WHERE task_id=? AND scheduled_time<>'' GROUP BY scheduled_time",("__TASK__",)).fetchall()
print(json.dumps({"quick_check":qk,"slot_unique_index_present":sq==1,"local_global_revision":rev,
 "identity":ident,"rows":rows,"slot_grouping":slots}))
""".replace('__TASK__', str(dup_agent_task))
    rc, out, err = ssh_python(NODE_IPS['native-2'], readback_script)
    readback = last_json_line(out) if rc == 0 else {'error': err.strip()[-300:], 'rc': rc}
    if isinstance(readback, dict) and readback.get('rows') is not None:
        rows = readback.get('rows') or []
        readback['row_count'] = len(rows)
        readback['expected_slot'] = agent_slot
        readback['distinct_slots'] = sorted({r[4][:19] for r in rows})
        readback['slot_row_counts'] = {s: n for s, n in (readback.get('slot_grouping') or [])}
        readback['rows_match_expected_slot'] = all(parse_ts(r[4]) == parse_ts(agent_slot) for r in rows)
        readback['slot_unique_single_row'] = (len(rows) == 1
                                             and (readback.get('slot_row_counts') or {}).get(
                                                 readback['distinct_slots'][0], 0) == 1)
    result['agent_db_slot_readback_retry'] = readback
    (EVID / 'phase11-agent-db-readback-retry.json').write_text(
        json.dumps(readback, ensure_ascii=False, indent=2) + '\n')

    # ---- 3. hub-side duplicate slot sanity from preserved executions ---- #
    hub_slots = {}
    code, exs = api('GET', '/api/executions?task_id=%s&limit=50' % dup_hub_task)
    if code == 200 and isinstance(exs, list):
        grouped = {}
        for e in exs:
            if e.get('trigger_type') == 'schedule':
                grouped.setdefault((e.get('scheduled_time') or '')[:19], []).append(e['id'])
        hub_slots = grouped
    result['hub_duplicate_slot_grouping'] = {
        'expected_slot': hub_slot, 'slot_to_execution_ids': hub_slots,
        'single_execution_for_slot': all(len(v) == 1 for v in hub_slots.values()) and bool(hub_slots)}
    (EVID / 'phase12-hub-duplicate-slot-grouping.json').write_text(
        json.dumps(result['hub_duplicate_slot_grouping'], ensure_ascii=False, indent=2) + '\n')

    ledger = {
        'batch_id': 'sys-b09', 'followup': True, 'nonce': NONCE,
        'fixtures': [{'kind': 'task', 'id': task_id, 'name': name,
                      'deleted_get404': result['followup_task']['deleted_get404']}],
        'preserved_executions': [{'id': v['execution_id'], 'node': k,
                                  'purpose': 'SYS-27 follow-up remote condition on %s' % k}
                                 for k, v in runs.items()],
        'status': 'CLEANED' if result['followup_task']['deleted_get404'] else 'CLEANUP_INCOMPLETE',
    }
    (HERE / 'fixture-ledger-followup.json').write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
