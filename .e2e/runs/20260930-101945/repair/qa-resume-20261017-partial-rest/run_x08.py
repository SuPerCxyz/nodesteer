#!/usr/bin/env python3
"""X08 closure: cancel paths (Stop / bulk upgrade / revoke credential),
slow-response repeated clicks, failure-then-retry. Owned fixtures + one
cancel-only interaction with a protected native node (zero write asserted).

Slow response = client-side fetch delay injection (window.fetch wrapper),
documented in evidence. Observation counts are taken from the same wrapper.
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

ev = {
    'case_id': 'X08',
    'at_utc': L.now_utc(),
    'authority': L.UI + ' (repair)',
    'browser_session': L.BROWSER_SESSION,
    'tests': {},
}
cleanup_plan = {'tasks': [], 'nodes': [], 'executions_history': []}


def rec(tid, status, payload):
    payload = dict(payload)
    payload['status'] = status
    payload['at_utc'] = L.now_utc()
    ev['tests'][tid] = payload
    L.save('x08-results.json', ev)
    print('[%s] %s :: %s' % (status, tid, json.dumps(payload, ensure_ascii=False)[:400]), flush=True)


def api_exec(exec_id):
    c, b, _ = hub.get('/api/executions/' + exec_id)
    return b if c == 200 else None


def wait_exec_status(exec_id, statuses, timeout=40):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = api_exec(exec_id)
        if last and last.get('status') in statuses:
            return last
        time.sleep(1.0)
    return last


def mark(js_find, tag):
    return L.ab_js("""
(() => {
  document.querySelectorAll('[data-prst-target]').forEach(e => e.removeAttribute('data-prst-target'));
  const el = %s;
  if (!el) return {ok:false};
  el.setAttribute('data-prst-target', %s);
  return {ok:true, text: (el.innerText||'').trim().slice(0,40)};
})()
""" % (js_find, json.dumps(tag)))


def native_click(tag, timeout=15):
    out, err, rc = L.ab('click', '[data-prst-target=%s]' % tag, timeout=timeout)
    return {'rc': rc, 'out': (out or '').strip()[:160], 'err': (err or '').strip()[:160]}


def obs_counts(path):
    obs = L.read_obs() or {}
    return (obs.get('counts') or {}).get(path, 0)


# ---------------- fixtures ----------------
c, nodes, _ = hub.get('/api/nodes')
n1 = next(n for n in nodes if n['hostname'].endswith('native-1'))
N1_ID = n1['id']
N1_BEFORE = {k: n1.get(k) for k in ('status', 'global_revision', 'sync_status')}
ev['native1_before'] = N1_BEFORE

TASK_NAME = L.PREFIX + 'stop'
c, task, _ = hub.post('/api/tasks', {
    'name': TASK_NAME, 'type': 'command',
    'command': 'printf "QA_PRST_STOP_STREAM\\n"; sleep 90',
    'target': {'type': 'node', 'node_ids': [N1_ID]},
    'timeout': 120, 'enabled': True,
})
TASK_ID = task.get('id')
L.ledger_add({'kind': 'task', 'name': TASK_NAME, 'id': TASK_ID, 'purpose': 'X08 stop cancel/confirm + slow response',
              'delete_method': 'DELETE /api/tasks/{id}', 'status': 'created'})
cleanup_plan['tasks'].append(TASK_ID)
rec('fixture_task', 'INFO' if TASK_ID else 'ERROR', {'http': c, 'task_id': TASK_ID})

OWNED_NODE_NAME = L.PREFIX + 'node'
c, enroll, _ = hub.post('/api/nodes/enrollment', {'node_name': OWNED_NODE_NAME, 'node_ip': '192.0.2.210', 'hub_address': L.UI})
OWNED_NODE_ID = enroll.get('node_id') if isinstance(enroll, dict) else None
L.ledger_add({'kind': 'node', 'name': OWNED_NODE_NAME, 'id': OWNED_NODE_ID, 'purpose': 'X08 revoke-credential cancel (owned)',
              'delete_method': 'DELETE /api/nodes/{id}', 'status': 'created'})
cleanup_plan['nodes'].append(OWNED_NODE_ID)
rec('fixture_node', 'INFO' if OWNED_NODE_ID else 'ERROR', {'http': c, 'node_id': OWNED_NODE_ID,
    'note': 'owned enrollment node, pending; command text not persisted (registration token inside)'})

RETRY_NODE_NAME = L.PREFIX + 'retry'
node_before = hub.get('/api/nodes/' + OWNED_NODE_ID)[1]

# ---------------- UI session ----------------
login = L.ab_login()
ev['login'] = login

try:
    # ===== T1: Run cancel 0-write + slow-response repeated click =====
    t1 = {}
    L.ab_open('/tasks/%s/run' % TASK_ID, wait=2.8)
    t1['page_url'] = L.ab_url()
    # cancel path (no injection)
    t1['mark_run'] = mark("[...document.querySelectorAll('main button')].find(b => (b.innerText||'').trim() === '立即运行')", 'runbtn')
    t1['click_run'] = native_click('runbtn')
    time.sleep(1.0)
    t1['mark_cancel'] = mark("[...document.querySelectorAll('[role=alertdialog] button')].find(b => (b.innerText||'').trim() === '取消')", 'cancelbtn')
    t1['click_cancel'] = native_click('cancelbtn')
    time.sleep(0.8)
    t1['dialog_closed_after_cancel'] = L.ab_js("!document.querySelector('[role=alertdialog]')")
    t1['cancel_zero_write'] = L.read_obs().get('counts', {})  # no patch yet -> empty

    # slow response + native double click on confirm
    t1['inject'] = L.inject_obs(delay_path='/api/tasks/%s/run' % TASK_ID, delay_ms=2500)
    t1['mark_run2'] = mark("[...document.querySelectorAll('main button')].find(b => (b.innerText||'').trim() === '立即运行')", 'runbtn2')
    t1['click_run2'] = native_click('runbtn2')
    time.sleep(1.0)
    t1['dialog_open'] = L.ab_js("!!document.querySelector('[role=alertdialog]')")
    t1['mark_confirm'] = mark("[...document.querySelectorAll('[role=alertdialog] button')].find(b => (b.innerText||'').trim() === '立即运行')", 'confirmrun')
    dbl = L.ab('dblclick', '[data-prst-target=confirmrun]', timeout=15)
    t1['dblclick_confirm'] = {'rc': dbl[2], 'out': (dbl[0] or '').strip()[:160], 'err': (dbl[1] or '').strip()[:160]}
    time.sleep(1.2)
    obs = L.read_obs()
    t1['obs_after_dblclick'] = obs
    run_path = '/api/tasks/%s/run' % TASK_ID
    t1['run_post_count'] = (obs.get('counts') or {}).get(run_path, 0)
    t1['single_request_on_slow_dblclick'] = t1['run_post_count'] == 1
    # wait for execution to appear
    exec_id = ''
    deadline = time.time() + 25
    while time.time() < deadline and not exec_id:
        cc, body, _ = hub.get('/api/executions?task_id=%s&limit=10' % TASK_ID)
        if cc == 200 and isinstance(body, list) and body:
            exec_id = body[0].get('id')
            break
        time.sleep(1.0)
    t1['execution_id'] = exec_id
    cleanup_plan['executions_history'].append(exec_id)
    exec_state = wait_exec_status(exec_id, ('RUNNING', 'PENDING'), timeout=25) if exec_id else None
    t1['execution_after_run'] = {'status': (exec_state or {}).get('status'), 'id': exec_id}
    rec('T1_run_cancel_and_slow_dblclick', 'PASS' if (t1.get('dialog_closed_after_cancel') and t1['single_request_on_slow_dblclick'] and exec_id) else 'FAIL', t1)

    # ===== T2: Stop cancel 0-write + slow-response repeated click + audit =====
    t2 = {}
    if exec_id:
        wait_exec_status(exec_id, ('RUNNING',), timeout=30)
        L.ab_open('/executions/%s' % exec_id, wait=3.0)
        t2['page_url'] = L.ab_url()
        t2['stop_button_present'] = L.ab_js("[...document.querySelectorAll('button')].some(b => (b.innerText||'').trim() === '停止执行')")
        # cancel path
        t2['mark_stop'] = mark("[...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '停止执行')", 'stopbtn')
        t2['click_stop'] = native_click('stopbtn')
        time.sleep(1.0)
        t2['dialog_open'] = L.ab_js("!!document.querySelector('[role=alertdialog]')")
        t2['mark_cancel'] = mark("[...document.querySelectorAll('[role=alertdialog] button')].find(b => (b.innerText||'').trim() === '取消')", 'stopcancel')
        t2['click_cancel'] = native_click('stopcancel')
        time.sleep(0.8)
        t2['dialog_closed_after_cancel'] = L.ab_js("!document.querySelector('[role=alertdialog]')")
        t2['stop_cancel_zero_write'] = (L.read_obs().get('counts') or {}).get('/api/executions/%s/cancel' % exec_id, 0) == 0

        # slow response + native double click on confirm
        t2['inject'] = L.inject_obs(delay_path='/api/executions/%s/cancel' % exec_id, delay_ms=2500)
        t2['mark_stop2'] = mark("[...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '停止执行')", 'stopbtn2')
        t2['click_stop2'] = native_click('stopbtn2')
        time.sleep(1.0)
        t2['mark_confirm'] = mark("[...document.querySelectorAll('[role=alertdialog] button')].find(b => (b.innerText||'').trim() === '停止执行')", 'stopconfirm')
        dbl2 = L.ab('dblclick', '[data-prst-target=stopconfirm]', timeout=15)
        t2['dblclick_confirm'] = {'rc': dbl2[2], 'out': (dbl2[0] or '').strip()[:160], 'err': (dbl2[1] or '').strip()[:160]}
        time.sleep(1.2)
        obs2 = L.read_obs()
        t2['obs_after_dblclick'] = obs2
        cancel_path = '/api/executions/%s/cancel' % exec_id
        t2['cancel_post_count'] = (obs2.get('counts') or {}).get(cancel_path, 0)
        t2['single_request_on_slow_dblclick'] = t2['cancel_post_count'] == 1
        final = wait_exec_status(exec_id, ('CANCELED',), timeout=40)
        t2['execution_final'] = {'status': (final or {}).get('status')}
        # audit rows for this execution
        ca, audit, _ = hub.get('/api/audit?q=%s&limit=100' % exec_id)
        rows = [r for r in (audit or []) if r.get('resource_id') == exec_id]
        cancels = [r for r in rows if r.get('action') == 'cancel']
        t2['audit_rows'] = rows
        t2['audit_cancel_count'] = len(cancels)
        t2['audit_single_cancel'] = len(cancels) == 1
        t2['screenshot'] = L.ab_shot('x08-stop-after.png')
    rec('T2_stop_cancel_and_slow_dblclick_audit',
        'PASS' if (t2.get('stop_cancel_zero_write') and t2.get('single_request_on_slow_dblclick') and t2.get('audit_single_cancel') and (t2.get('execution_final') or {}).get('status') == 'CANCELED') else 'FAIL', t2)

    # ===== T3: revoke credential cancel (owned node, zero write) =====
    t3 = {}
    L.ab_open('/agents', wait=3.0)
    t3['mark_menu'] = mark("[...document.querySelectorAll('tbody tr')].find(t => (t.innerText||'').includes(%s)) && [...[...document.querySelectorAll('tbody tr')].find(t => (t.innerText||'').includes(%s)).querySelectorAll('button')].find(b => (b.innerText||'').trim() === '操作' || (b.getAttribute('aria-label')||'') === '操作')" % (json.dumps(OWNED_NODE_NAME), json.dumps(OWNED_NODE_NAME)), 'nodemenu')
    t3['click_menu'] = native_click('nodemenu')
    time.sleep(1.0)
    t3['mark_revoke'] = mark("[...document.querySelectorAll('[role=menuitem]')].find(i => (i.innerText||'').includes('撤销凭证'))", 'revoke')
    out, err, rc = L.ab('click', '[data-prst-target=revoke]', timeout=12)
    t3['click_revoke'] = {'rc': rc, 'out': (out or '').strip()[:180], 'err': (err or '').strip()[:180]}
    time.sleep(0.5)
    t3['dialog_status'] = (L.ab('dialog', 'status')[0] or '').strip()[:200]
    dis = L.ab('dialog', 'dismiss')
    t3['dialog_dismiss'] = {'rc': dis[2], 'out': (dis[0] or '').strip()[:120]}
    time.sleep(1.0)
    obs3 = L.read_obs()
    t3['obs_after_cancel'] = obs3
    revoke_paths = {k: v for k, v in (obs3.get('counts') or {}).items() if 'revoke-credential' in k}
    t3['revoke_post_count'] = sum(revoke_paths.values())
    t3['revoke_cancel_zero_write'] = t3['revoke_post_count'] == 0
    c3, node_after, _ = hub.get('/api/nodes/' + OWNED_NODE_ID)
    t3['node_after'] = {'http': c3, 'status': (node_after or {}).get('status'), 'same_status': (node_after or {}).get('status') == (node_before or {}).get('status')}
    rec('T3_revoke_cancel_zero_write', 'PASS' if (t3['revoke_cancel_zero_write'] and c3 == 200 and t3['node_after']['same_status']) else 'FAIL', t3)

    # ===== T4: bulk upgrade cancel (protected native-1, cancel-only, zero write) =====
    t4 = {}
    L.ab_open('/agents', wait=3.0)
    t4['mark_select'] = mark("[...[...document.querySelectorAll('tbody tr')].find(t => (t.innerText||'').includes('native-1')).querySelectorAll('button')].find(b => (b.getAttribute('aria-label')||'').includes('选择该行'))", 'selectn1')
    t4['click_select'] = native_click('selectn1')
    time.sleep(1.0)
    t4['toolbar'] = L.ab_js("[...document.querySelectorAll('[role=toolbar] button')].map(b => (b.innerText||'').trim())")
    t4['mark_upgrade'] = mark("[...document.querySelectorAll('[role=toolbar] button')].find(b => (b.innerText||'').includes('升级 Agent'))", 'upgradebtn')
    t4['click_upgrade'] = native_click('upgradebtn')
    time.sleep(1.0)
    t4['dialog_text'] = L.ab_js("(() => { const d = document.querySelector('[role=alertdialog]'); return d ? d.innerText : null; })()")
    t4['mark_cancel'] = mark("[...document.querySelectorAll('[role=alertdialog] button')].find(b => (b.innerText||'').trim() === '取消')", 'bulkcancel')
    t4['click_cancel'] = native_click('bulkcancel')
    time.sleep(0.8)
    t4['dialog_closed_after_cancel'] = L.ab_js("!document.querySelector('[role=alertdialog]')")
    obs4 = L.read_obs()
    t4['obs_after_cancel'] = obs4
    post_nodes = {k: v for k, v in (obs4.get('counts') or {}).items() if k.startswith('/api/nodes')}
    t4['node_post_counts'] = post_nodes
    t4['bulk_upgrade_zero_write'] = len(post_nodes) == 0
    c4, n1_after, _ = hub.get('/api/nodes/' + N1_ID)
    t4['native1_after'] = {k: (n1_after or {}).get(k) for k in ('status', 'global_revision', 'sync_status')}
    t4['native1_unchanged'] = t4['native1_after'] == N1_BEFORE
    t4['shared_node_effect'] = 'none (cancel-only; zero write asserted)'
    # clear selection to leave the page clean
    L.ab_js("[...document.querySelectorAll('[role=toolbar] button')].find(b => (b.innerText||'').includes('清除选择')) && [...document.querySelectorAll('[role=toolbar] button')].find(b => (b.innerText||'').includes('清除选择')).click()")
    rec('T4_bulk_upgrade_cancel_zero_write', 'PASS' if (t4['bulk_upgrade_zero_write'] and t4['native1_unchanged'] and t4['dialog_closed_after_cancel']) else 'FAIL', t4)

    # ===== T5: enrollment failure -> retry =====
    t5 = {}
    L.ab_open('/agents', wait=3.0)
    t5['mark_add'] = mark("[...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '添加节点')", 'addnode')
    t5['click_add'] = native_click('addnode')
    time.sleep(1.0)
    t5['dialog_open'] = L.ab_js("!!document.querySelector('[role=dialog]')")
    set_bad = L.ab_js(r"""
(() => {
  const setVal = (el, v) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const name = document.querySelector('#enrollment-node-name');
  const ip = document.querySelector('#enrollment-node-ip');
  if (!name || !ip) return {ok:false};
  setVal(name, 'qa prst bad!');
  setVal(ip, '192.0.2.211');
  return {ok:true, name: name.value, ip: ip.value};
})()
""")
    t5['fill_bad_name'] = set_bad
    t5['inject'] = L.inject_obs()
    t5['mark_submit'] = mark("[...document.querySelectorAll('[role=dialog] button')].find(b => (b.innerText||'').includes('生成安装命令'))", 'enrollsubmit')
    t5['click_submit_bad'] = native_click('enrollsubmit')
    time.sleep(2.0)
    t5['error_state_1'] = L.ab_js("(() => { const a = document.querySelector('[role=dialog] [role=alert]'); return a ? a.innerText : null; })()")
    t5['mark_retry'] = mark("[...document.querySelectorAll('[role=dialog] [role=alert] button')].find(b => (b.innerText||'').includes('重试'))", 'enrollretry')
    t5['click_retry'] = native_click('enrollretry')
    time.sleep(2.0)
    t5['error_state_2'] = L.ab_js("(() => { const a = document.querySelector('[role=dialog] [role=alert]'); return a ? a.innerText : null; })()")
    t5['screenshot_error'] = L.ab_shot('x08-enrollment-error-retry.png')
    # fix name -> submit again
    fix = L.ab_js(r"""
(() => {
  const setVal = (el, v) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const name = document.querySelector('#enrollment-node-name');
  if (!name) return {ok:false};
  setVal(name, %s);
  return {ok:true, name: name.value};
})()
""" % json.dumps(RETRY_NODE_NAME))
    t5['fill_fixed_name'] = fix
    t5['mark_submit2'] = mark("[...document.querySelectorAll('[role=dialog] button')].find(b => (b.innerText||'').includes('生成安装命令'))", 'enrollsubmit2')
    t5['click_submit_fixed'] = native_click('enrollsubmit2')
    time.sleep(2.5)
    t5['command_shown_no_text_capture'] = L.ab_js("(() => { const pre = document.querySelector('[role=dialog] pre'); return pre ? {present: true, length: (pre.innerText||'').length} : {present: false}; })()")
    obs5 = L.read_obs()
    t5['obs'] = obs5
    enroll_path = '/api/nodes/enrollment'
    t5['enrollment_post_count'] = (obs5.get('counts') or {}).get(enroll_path, 0)
    t5['sequence_expected'] = 'bad(400) -> retry-same-bad(400) -> fixed(200)'
    # find created node id
    c5, all_nodes, _ = hub.get('/api/nodes')
    retry_node = next((n for n in (all_nodes or []) if n.get('hostname') == RETRY_NODE_NAME), None)
    t5['retry_node'] = {'found': bool(retry_node), 'id': (retry_node or {}).get('id'), 'status': (retry_node or {}).get('status')}
    if retry_node:
        cleanup_plan['nodes'].append(retry_node['id'])
        L.ledger_add({'kind': 'node', 'name': RETRY_NODE_NAME, 'id': retry_node['id'],
                      'purpose': 'X08 enrollment failure->retry', 'delete_method': 'DELETE /api/nodes/{id}', 'status': 'created'})
    rec('T5_enrollment_failure_retry', 'PASS' if (t5['error_state_1'] and t5['error_state_2'] and t5['enrollment_post_count'] == 3 and retry_node) else 'FAIL', t5)

except Exception as exc:
    ev['exception'] = str(exc)
    L.save('x08-results.json', ev)
    raise
finally:
    L.ab_close()

# ---------------- cleanup ----------------
clean = {'at_utc': L.now_utc(), 'deleted': []}
for nid in cleanup_plan['nodes']:
    c, _, _ = hub.delete('/api/nodes/' + nid)
    gc, _, _ = hub.get('/api/nodes/' + nid)
    clean['deleted'].append({'kind': 'node', 'id': nid, 'delete_http': c, 'get_after_http': gc, 'get404': gc == 404})
    L.ledger_add({'kind': 'node', 'id': nid, 'status': 'deleted', 'delete_http': c, 'get_after_http': gc})
for tid in cleanup_plan['tasks']:
    c, _, _ = hub.delete('/api/tasks/' + tid)
    gc, _, _ = hub.get('/api/tasks/' + tid)
    clean['deleted'].append({'kind': 'task', 'id': tid, 'delete_http': c, 'get_after_http': gc, 'get404': gc == 404})
    L.ledger_add({'kind': 'task', 'id': tid, 'status': 'deleted', 'delete_http': c, 'get_after_http': gc})
clean['all_deleted_get404'] = all(d['get404'] for d in clean['deleted'])
clean['non_deletable_history'] = {
    'executions': cleanup_plan['executions_history'],
    'note': 'execution records have no delete API; retained as history (this batch created the listed execution(s) only)',
}
ev['cleanup'] = clean
ev['verdict'] = 'PASS' if all(t.get('status') == 'PASS' for k, t in ev['tests'].items() if not k.startswith('fixture_')) and clean['all_deleted_get404'] else 'FAIL'
L.save('x08-results.json', ev)
print('X08 verdict:', ev['verdict'], 'cleanup404:', clean['all_deleted_get404'], flush=True)
