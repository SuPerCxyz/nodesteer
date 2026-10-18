#!/usr/bin/env python3
"""X08 T4 re-run with corrected oracle.

First attempt compared native-1.global_revision captured before this batch's own
fixture writes (task create/enroll/delete advance the global revision), and read
obs without injecting the wrapper on the /agents page. This re-run captures the
node snapshot immediately before the cancel interaction, injects the fetch
observer, and asserts zero non-GET /api requests + no upgrade audit row.
Cancel-only on protected native-1: no write, no state change.
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

c, nodes, _ = hub.get('/api/nodes')
n1 = next(n for n in nodes if n['hostname'].endswith('native-1'))
N1_ID = n1['id']
KEYS = ('status', 'global_revision', 'sync_status', 'last_seen')

t4 = {'case_id': 'X08', 'test': 'T4_bulk_upgrade_cancel_zero_write_rerun', 'at_utc': L.now_utc(),
      'authority': L.UI, 'protected_node': {'id': N1_ID, 'hostname': n1['hostname']},
      'note': 'cancel-only; owned fixture not possible because pending enrollment nodes have deployment_mode="" '
              'and the bulk upgrade entry filters deployment_mode===native (node-bulk.ts + PrepareEnrollment reset)'}


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


def upgrade_audit_rows():
    c, rows, _ = hub.get('/api/audit?action=upgrade&limit=100')
    return [r for r in (rows or []) if r.get('resource_id') == N1_ID]


login = L.ab_login()
t4['login'] = login
try:
    L.ab_open('/agents', wait=3.0)
    # snapshot immediately before interaction
    c, n_before, _ = hub.get('/api/nodes/' + N1_ID)
    t4['node_before'] = {k: (n_before or {}).get(k) for k in KEYS}
    t4['upgrade_audit_before'] = len(upgrade_audit_rows())

    t4['inject'] = L.inject_obs()
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
    time.sleep(1.0)
    t4['dialog_closed_after_cancel'] = L.ab_js("!document.querySelector('[role=alertdialog]')")
    obs = L.read_obs()
    t4['obs_after_cancel'] = obs
    post_nodes = {k: v for k, v in (obs.get('counts') or {}).items() if k.startswith('/api/nodes')}
    t4['node_post_counts'] = post_nodes
    t4['bulk_upgrade_zero_write'] = len(post_nodes) == 0
    c, n_after, _ = hub.get('/api/nodes/' + N1_ID)
    t4['node_after'] = {k: (n_after or {}).get(k) for k in KEYS}
    t4['node_state_unchanged_during_cancel'] = all(t4['node_after'].get(k) == t4['node_before'].get(k) for k in ('status', 'global_revision', 'sync_status'))
    t4['last_seen_advanced'] = t4['node_after'].get('last_seen') != t4['node_before'].get('last_seen')
    t4['upgrade_audit_after'] = len(upgrade_audit_rows())
    t4['no_new_upgrade_audit'] = t4['upgrade_audit_after'] == t4['upgrade_audit_before']
    t4['shared_node_effect'] = 'none (cancel-only)'
    # clear selection
    L.ab_js("[...document.querySelectorAll('[role=toolbar] button')].find(b => (b.innerText||'').includes('清除选择')) && [...document.querySelectorAll('[role=toolbar] button')].find(b => (b.innerText||'').includes('清除选择')).click()")
    time.sleep(0.5)
    t4['toolbar_cleared'] = L.ab_js("!document.querySelector('[role=toolbar]')")
    t4['screenshot'] = L.ab_shot('x08-bulk-upgrade-cancel-rerun.png')
except Exception as exc:
    t4['exception'] = str(exc)
finally:
    L.ab_close()

t4['status'] = 'PASS' if (t4.get('bulk_upgrade_zero_write') and t4.get('node_state_unchanged_during_cancel')
                          and t4.get('no_new_upgrade_audit') and t4.get('dialog_closed_after_cancel')) else 'FAIL'
L.save('x08-t4-rerun.json', t4)

# annotate the main results file
main = L.load('x08-results.json') or {}
if 'tests' in main and 'T4_bulk_upgrade_cancel_zero_write' in main['tests']:
    main['tests']['T4_bulk_upgrade_cancel_zero_write']['harness_note'] = (
        'first-run oracle flaw: global_revision was captured before this batch\'s own fixture writes '
        '(task create/enroll advance the global revision) and obs was read without injecting the wrapper '
        'on the /agents page; corrected rerun is x08-t4-rerun.json')
    main['tests']['T4_bulk_upgrade_cancel_zero_write_rerun'] = t4
    main['verdict'] = 'PASS' if all(
        t.get('status') == 'PASS' for k, t in main['tests'].items()
        if not k.startswith('fixture_') and k != 'T4_bulk_upgrade_cancel_zero_write') and (main.get('cleanup', {}) or {}).get('all_deleted_get404') else 'FAIL'
    L.save('x08-results.json', main)

print('T4 rerun status:', t4['status'], json.dumps({k: t4[k] for k in ('bulk_upgrade_zero_write', 'node_state_unchanged_during_cancel', 'no_new_upgrade_audit', 'dialog_closed_after_cancel')}, ensure_ascii=False), flush=True)
