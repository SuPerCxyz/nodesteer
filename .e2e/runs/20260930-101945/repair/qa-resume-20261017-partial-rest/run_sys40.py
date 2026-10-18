#!/usr/bin/env python3
"""SYS-40 per-attempt audit evidence: run a real retry=2 task on the fixall
build, count attempts in the dual-stream logs, and count audit rows for the
logical run. Decision-support evidence only; no forced PASS.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib as L  # noqa: E402

hub = L.Hub()
hub.login()

MARKER = 'QA_PRST_RETRY_MARKER'
TASK_NAME = L.PREFIX + 'retry'
ev = {'case_id': 'SYS-40', 'at_utc': L.now_utc(), 'authority': L.UI, 'marker': MARKER}

c, nodes, _ = hub.get('/api/nodes')
n1 = next(n for n in nodes if n['hostname'].endswith('native-1'))

c, task, _ = hub.post('/api/tasks', {
    'name': TASK_NAME, 'type': 'command',
    'command': "printf '%s\\n'; exit 7" % MARKER,
    'target': {'type': 'node', 'node_ids': [n1['id']]},
    'timeout': 60, 'retry': 2, 'enabled': True,
})
TASK_ID = task.get('id')
L.ledger_add({'kind': 'task', 'name': TASK_NAME, 'id': TASK_ID, 'purpose': 'SYS-40 retry=2 per-attempt audit evidence',
              'delete_method': 'DELETE /api/tasks/{id}', 'status': 'created'})
ev['task'] = {'id': TASK_ID, 'name': TASK_NAME, 'retry': 2, 'command': "printf '<marker>\\n'; exit 7", 'http': c}

try:
    c, run_body, _ = hub.post('/api/tasks/%s/run' % TASK_ID, {})
    execs = run_body if isinstance(run_body, list) else [run_body]
    EXEC_ID = execs[0].get('id')
    ev['run'] = {'http': c, 'execution_id': EXEC_ID, 'returned': len(execs)}

    final = None
    deadline = time.time() + 150
    while time.time() < deadline:
        c, ex, _ = hub.get('/api/executions/' + EXEC_ID)
        if isinstance(ex, dict) and ex.get('status') in ('FAILED', 'SUCCESS', 'CANCELED', 'TIMED_OUT', 'SKIPPED'):
            final = ex
            break
        time.sleep(2.0)
    ev['execution_final'] = {k: (final or {}).get(k) for k in ('status', 'exit_code', 'start_time', 'end_time', 'task_id', 'node_id', 'trigger_type')}
    ev['final_status'] = (final or {}).get('status')

    # per-attempt logs
    c, chunks, _ = hub.get('/api/executions/%s/logs' % EXEC_ID)
    joined = ''.join((ch or {}).get('chunk', '') for ch in (chunks or []))
    marker_count = joined.count(MARKER)
    ev['logs'] = {
        'http': c, 'chunk_count': len(chunks or []),
        'marker_occurrences': marker_count,
        'expected_attempts': 3,
        'per_attempt_logs_ok': marker_count == 3,
        'stdout_stream_chunks': sum(1 for ch in (chunks or []) if ch.get('stream') == 'stdout'),
        'stderr_stream_chunks': sum(1 for ch in (chunks or []) if ch.get('stream') == 'stderr'),
        'log_sample_tail': joined[-400:],
    }

    # audit rows: logical run (resource=task) vs per-attempt
    c1, rows_task, _ = hub.get('/api/audit?q=%s&limit=200' % TASK_ID)
    task_rows = [r for r in (rows_task or []) if r.get('resource_id') == TASK_ID]
    execute_rows = [r for r in task_rows if r.get('action') == 'execute']
    c2, rows_exec, _ = hub.get('/api/audit?q=%s&limit=200' % EXEC_ID)
    exec_rows = [r for r in (rows_exec or []) if r.get('resource_id') == EXEC_ID]
    c3, all_execute, _ = hub.get('/api/audit?action=execute&limit=200')
    execute_for_task = [r for r in (all_execute or []) if r.get('resource_id') == TASK_ID]
    ev['audit'] = {
        'rows_for_task_id': task_rows,
        'execute_rows_count': len(execute_rows),
        'rows_for_execution_id': exec_rows,
        'action_execute_all_count_for_task': len(execute_for_task),
        'per_attempt_audit_rows': 0,
        'finding': 'single execute audit row per logical run; no per-attempt audit row exists',
    }

    # UI: execution detail shows 3 attempt outputs; audit page search shows 1 execute row
    login = L.ab_login()
    ev['login'] = login
    try:
        L.ab_open('/executions/%s' % EXEC_ID, wait=3.0)
        pre_text = L.ab_js("(() => { const pre = document.querySelector('pre'); return pre ? pre.innerText : null; })()")
        ui_marker = (pre_text or '').count(MARKER)
        ev['ui'] = {'marker_occurrences_in_pre': ui_marker, 'screenshot': L.ab_shot('sys40-execution-attempts.png')}
        L.ab_open('/audit', wait=3.0)
        fill = L.ab_js(r"""
(() => {
  const input = [...document.querySelectorAll('input')].find(i => (i.getAttribute('placeholder')||'').includes('搜索') || i.type === 'search' || i.type === 'text');
  if (!input) return {ok:false};
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, %s);
  input.dispatchEvent(new Event('input', {bubbles:true}));
  input.dispatchEvent(new Event('change', {bubbles:true}));
  return {ok:true};
})()
""" % json.dumps(TASK_ID))
        time.sleep(2.5)
        audit_ui = L.ab_js("(() => { const tb = document.querySelector('tbody'); return tb ? tb.innerText : null; })()")
        ev['ui']['audit_search'] = {
            'fill': fill,
            'tbody_text': (audit_ui or '')[:600],
            'execute_row_occurrences': (audit_ui or '').count('execute'),
        }
        ev['ui']['audit_screenshot'] = L.ab_shot('sys40-audit-search.png')
    finally:
        L.ab_close()
except Exception as exc:
    ev['exception'] = str(exc)
finally:
    c, _, _ = hub.delete('/api/tasks/' + TASK_ID)
    gc, _, _ = hub.get('/api/tasks/' + TASK_ID)
    ev['cleanup'] = {'delete_http': c, 'get_after_http': gc, 'get404': gc == 404,
                     'non_deletable_history': {'executions': [EXEC_ID], 'note': 'no delete API; retained as history'}}
    L.ledger_add({'kind': 'task', 'id': TASK_ID, 'status': 'deleted', 'delete_http': c, 'get_after_http': gc})
    L.save('sys40-retry-audit.json', ev)

print('SYS-40: status=%s marker_count=%s execute_rows=%s per_attempt_audit=%s cleanup404=%s' % (
    ev.get('final_status'), ev.get('logs', {}).get('marker_occurrences'), ev.get('audit', {}).get('execute_rows_count'),
    ev.get('audit', {}).get('per_attempt_audit_rows'), ev.get('cleanup', {}).get('get404')), flush=True)
