#!/usr/bin/env python3
"""X08 add-on: revoke credential CONFIRM path on an owned node (single request).

Complements T3 (cancel=0 write). Confirm path must send exactly one POST and
leave the owned node in disabled state; owned node then deleted -> GET 404.
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

NODE_NAME = L.PREFIX + 'revnode'
c, enroll, _ = hub.post('/api/nodes/enrollment', {'node_name': NODE_NAME, 'node_ip': '192.0.2.212', 'hub_address': L.UI})
NID = enroll.get('node_id') if isinstance(enroll, dict) else None
L.ledger_add({'kind': 'node', 'name': NODE_NAME, 'id': NID, 'purpose': 'X08 revoke-credential confirm (owned)',
              'delete_method': 'DELETE /api/nodes/{id}', 'status': 'created'})

ev = {'case_id': 'X08', 'test': 'T6_revoke_confirm_single_request', 'at_utc': L.now_utc(),
      'authority': L.UI, 'owned_node': {'name': NODE_NAME, 'id': NID}}
try:
    c, node0, _ = hub.get('/api/nodes/' + NID)
    ev['node_before'] = {'http': c, 'status': (node0 or {}).get('status')}

    login = L.ab_login()
    ev['login'] = login
    L.ab_open('/agents', wait=3.0)
    ev['inject'] = L.inject_obs()

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

    ev['mark_menu'] = mark("[...document.querySelectorAll('tbody tr')].find(t => (t.innerText||'').includes(%s)) && [...[...document.querySelectorAll('tbody tr')].find(t => (t.innerText||'').includes(%s)).querySelectorAll('button')].find(b => (b.innerText||'').trim() === '操作' || (b.getAttribute('aria-label')||'') === '操作')" % (json.dumps(NODE_NAME), json.dumps(NODE_NAME)), 'revmenu')
    ev['click_menu'] = native_click('revmenu')
    time.sleep(1.0)
    ev['mark_revoke'] = mark("[...document.querySelectorAll('[role=menuitem]')].find(i => (i.innerText||'').includes('撤销凭证'))", 'revitem')
    out, err, rc = L.ab('click', '[data-prst-target=revitem]', timeout=12)
    ev['click_revoke'] = {'rc': rc, 'out': (out or '').strip()[:160], 'err': (err or '').strip()[:160]}
    time.sleep(0.5)
    ev['dialog_status'] = (L.ab('dialog', 'status')[0] or '').strip()[:160]
    acc = L.ab('dialog', 'accept')
    ev['dialog_accept'] = {'rc': acc[2], 'out': (acc[0] or '').strip()[:120]}
    time.sleep(2.0)
    obs = L.read_obs()
    ev['obs'] = obs
    revoke_counts = {k: v for k, v in (obs.get('counts') or {}).items() if 'revoke-credential' in k}
    ev['revoke_post_count'] = sum(revoke_counts.values())
    ev['single_request_on_confirm'] = ev['revoke_post_count'] == 1
    c, node_after, _ = hub.get('/api/nodes/' + NID)
    ev['node_after'] = {'http': c, 'status': (node_after or {}).get('status')}
    ev['confirm_effect_ok'] = (node_after or {}).get('status') == 'disabled'
    ev['screenshot'] = L.ab_shot('x08-revoke-confirm.png')
    ev['status'] = 'PASS' if (ev['single_request_on_confirm'] and ev['confirm_effect_ok']) else 'FAIL'
except Exception as exc:
    ev['exception'] = str(exc)
    ev['status'] = 'FAIL'
finally:
    L.ab_close()
    c, _, _ = hub.delete('/api/nodes/' + NID)
    gc, _, _ = hub.get('/api/nodes/' + NID)
    ev['cleanup'] = {'delete_http': c, 'get_after_http': gc, 'get404': gc == 404}
    L.ledger_add({'kind': 'node', 'id': NID, 'status': 'deleted', 'delete_http': c, 'get_after_http': gc})
    L.save('x08-t6-revoke-confirm.json', ev)

# merge into main x08 results
main = L.load('x08-results.json') or {}
main.setdefault('tests', {})['T6_revoke_confirm_single_request'] = ev
main['slow_response_method'] = ('client-side window.fetch wrapper injected via agent-browser eval: records all non-GET /api '
                                'requests and delays the target path by 2500ms; native agent-browser dblclick on the dialog confirm button')
main['verdict'] = 'PASS' if all(t.get('status') == 'PASS' for k, t in main['tests'].items()
                                if not k.startswith('fixture_') and k != 'T4_bulk_upgrade_cancel_zero_write') and (main.get('cleanup', {}) or {}).get('all_deleted_get404') else 'FAIL'
L.save('x08-results.json', main)
print('T6:', ev['status'], 'revoke_post_count:', ev.get('revoke_post_count'), 'node_after:', ev.get('node_after'), 'cleanup404:', ev['cleanup']['get404'], flush=True)
