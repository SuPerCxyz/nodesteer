#!/usr/bin/env python3
"""G04-03 closure batch: group duplicate-name error text on the fixall build.

Dimensions under test (from ledger remaining):
  1) group duplicate-name error must be normalized ("group name already exists")
     and must NOT leak SQLite raw text;
  2) cross-page generic error text read-only scan.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib as L  # noqa: E402

REPO = Path('/home/superc/code/nodesteer')

hub = L.Hub()
hub.login()

G1 = L.PREFIX + 'grp-a'
G2 = L.PREFIX + 'grp-b'
G1_VARIANT = ' ' + G1.upper() + ' '

SQL_MARKERS = ['UNIQUE constraint', 'SQLITE', 'Sqlite', 'sqlite', 'constraint failed',
               'no such table', 'database is locked', 'SQL logic error', 'sql: ']

results = {
    'case_id': 'G04-03',
    'at_utc': L.now_utc(),
    'authority': L.UI + ' (repair)',
    'owned_group_names': [G1, G2],
    'checks': {},
}


def check(name, ok, detail):
    results['checks'][name] = {'ok': bool(ok), 'detail': detail}
    print(('[OK]  ' if ok else '[FAIL]') + ' ' + name + ' :: ' + json.dumps(detail, ensure_ascii=False)[:400], flush=True)
    return ok


def sql_free(body):
    text = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    return [m for m in SQL_MARKERS if m in text]


try:
    # ---------- API: duplicate on create ----------
    c1, b1, _ = hub.post('/api/groups', {'name': G1, 'description': 'owned by qa-prst', 'type': 'static', 'members': []})
    check('create_g1_ok', c1 == 200 and isinstance(b1, dict) and b1.get('name') == G1, {'http': c1, 'name': (b1 or {}).get('name')})
    L.ledger_add({'kind': 'group', 'name': G1, 'id': (b1 or {}).get('id'), 'purpose': 'G04-03 duplicate-name regression', 'delete_method': 'DELETE /api/groups/{id}', 'status': 'created'})

    c2, b2, _ = hub.post('/api/groups', {'name': G1, 'description': 'dup exact', 'type': 'static', 'members': []})
    err2 = (b2 or {}).get('error') if isinstance(b2, dict) else b2
    check('dup_exact_http400', c2 == 400, {'http': c2, 'body': b2})
    check('dup_exact_normalized_text', err2 == 'group name already exists: ' + G1, {'error': err2, 'expected': 'group name already exists: ' + G1})
    check('dup_exact_no_sql_leak', not sql_free(b2), {'sql_markers': sql_free(b2)})

    c3, b3, _ = hub.post('/api/groups', {'name': G1_VARIANT, 'description': 'dup case+space variant', 'type': 'static', 'members': []})
    err3 = (b3 or {}).get('error') if isinstance(b3, dict) else b3
    check('dup_variant_http400', c3 == 400, {'http': c3, 'sent_name': G1_VARIANT, 'body': b3})
    check('dup_variant_normalized_text', err3 == 'group name already exists: ' + G1.upper(), {'error': err3})
    check('dup_variant_no_sql_leak', not sql_free(b3), {'sql_markers': sql_free(b3)})

    # ---------- API: duplicate on rename (PUT) ----------
    c4, b4, _ = hub.post('/api/groups', {'name': G2, 'description': 'owned by qa-prst', 'type': 'static', 'members': []})
    g2id = (b4 or {}).get('id')
    check('create_g2_ok', c4 == 200 and bool(g2id), {'http': c4, 'id': g2id})
    L.ledger_add({'kind': 'group', 'name': G2, 'id': g2id, 'purpose': 'G04-03 rename-duplicate regression', 'delete_method': 'DELETE /api/groups/{id}', 'status': 'created'})

    c5, b5, _ = hub.put('/api/groups/' + str(g2id), {'name': G1, 'description': 'rename to dup', 'type': 'static', 'members': []})
    err5 = (b5 or {}).get('error') if isinstance(b5, dict) else b5
    check('rename_dup_http400', c5 == 400, {'http': c5, 'body': b5})
    check('rename_dup_normalized_text', err5 == 'group name already exists: ' + G1, {'error': err5})
    check('rename_dup_no_sql_leak', not sql_free(b5), {'sql_markers': sql_free(b5)})

    c6, b6, _ = hub.get('/api/groups/' + str(g2id))
    check('rename_rejected_g2_unchanged', c6 == 200 and (b6 or {}).get('name') == G2, {'http': c6, 'name': (b6 or {}).get('name')})

    # ---------- API: empty name stays specific ----------
    c7, b7, _ = hub.post('/api/groups', {'name': '   ', 'type': 'static', 'members': []})
    err7 = (b7 or {}).get('error') if isinstance(b7, dict) else b7
    check('empty_name_http400_specific', c7 == 400 and err7 == 'name required', {'http': c7, 'error': err7})

    results['api_verdict'] = 'PASS' if all(v['ok'] for v in results['checks'].values()) else 'FAIL'
    L.save('g0403-api.json', results)
except Exception as exc:
    results['exception'] = str(exc)
    L.save('g0403-api.json', results)
    raise

# ---------- UI: duplicate name inline error ----------
ui = {'case_id': 'G04-03', 'phase': 'ui', 'at_utc': L.now_utc()}
try:
    login = L.ab_login()
    ui['login'] = login
    L.ab_open('/groups/new', wait=2.5)
    fill = L.ab_js(r"""
(() => {
  const setVal = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const inputs = [...document.querySelectorAll('main input')];
  if (!inputs.length) return {ok:false};
  setVal(inputs[0], %s);
  return {ok:true, value: inputs[0].value};
})()
""" % json.dumps(G1))
    ui['fill_name'] = fill
    L.ab_net(clear=True)
    click = L.ab('find', 'role', 'button', 'click', '--name', '保存')
    ui['click_save'] = {'rc': click[2], 'out': (click[0] or '').strip()[:120], 'err': (click[1] or '').strip()[:120]}
    time.sleep(2.0)
    probe = L.ab_js(r"""
(() => {
  const alert = document.querySelector('[role=alert]');
  const bodyText = document.body.innerText || '';
  const sqlMarkers = ['UNIQUE constraint', 'SQLITE', 'sqlite', 'constraint failed', 'no such table'];
  return {
    alert_text: alert ? alert.innerText : null,
    alert_present: !!alert,
    url: location.pathname,
    sql_hits: sqlMarkers.filter(m => bodyText.includes(m)),
    name_in_alert: alert ? (alert.innerText||'').includes(%s) : false,
  };
})()
""" % json.dumps(G1))
    ui['probe'] = probe
    ui['screenshot'] = L.ab_shot('g0403-ui-duplicate-name.png')
    net = L.brief_reqs(L.ab_net())
    posts = [r for r in net if (r.get('method') or '').upper() == 'POST' and '/api/groups' in (r.get('url') or '')]
    ui['post_groups_requests'] = posts
    alert_text = (probe or {}).get('alert_text') or ''
    ui['verdict'] = {
        'inline_error_present': bool((probe or {}).get('alert_present')),
        'error_specific_localized': ('已存在' in alert_text) and (G1 in alert_text),
        'no_sql_text_on_page': not (probe or {}).get('sql_hits'),
        'stayed_on_editor': (probe or {}).get('url') == '/groups/new',
        'exactly_one_post': len(posts) == 1,
    }
    ui['status'] = 'PASS' if all(ui['verdict'].values()) else 'FAIL'
    L.save('g0403-ui.json', ui)
    print('UI verdict:', json.dumps(ui['verdict'], ensure_ascii=False), flush=True)
except Exception as exc:
    ui['exception'] = str(exc)
    L.save('g0403-ui.json', ui)
    raise
finally:
    L.ab_close()

# ---------- read-only cross-page error text scan ----------
scan = {'case_id': 'G04-03', 'phase': 'source-scan', 'at_utc': L.now_utc(), 'repo': str(REPO)}
try:
    generic_patterns = ['Something went wrong', 'Unknown error', 'Request failed',
                        '出错了', '未知错误', '发生错误', '操作失败，请重试', 'internal server error']
    gen_hits = {}
    for pat in generic_patterns:
        cp = subprocess.run(['grep', '-rn', '--include=*.ts', '--include=*.tsx', '--include=*.json', pat,
                             str(REPO / 'web' / 'src')], capture_output=True, text=True)
        lines = [l for l in cp.stdout.splitlines() if l.strip()]
        if lines:
            gen_hits[pat] = lines[:20]
    scan['generic_hardcoded_error_strings'] = {k: len(v) for k, v in gen_hits.items()}
    scan['generic_hardcoded_error_samples'] = gen_hits

    # error surfaces should render the caught error, not a constant
    cp = subprocess.run(['grep', '-rn', '--include=*.tsx', '-E', r'(toast\.error\(|setError\(|ErrorState)', str(REPO / 'web' / 'src')],
                        capture_output=True, text=True)
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    scan['error_surface_line_count'] = len(lines)
    with_message = [l for l in lines if re.search(r'error\.message|errorMessage\(|cause instanceof Error|\{error\}|error=\{', l)]
    scan['error_surface_with_caught_message'] = len(with_message)
    constant_only = [l for l in lines if re.search(r"toast\.error\('|toast\.error\(\"|setError\('|setError\(\"", l) and 'error' not in l.split("'")[0].split('"')[0][-20:]]
    scan['error_surface_suspected_constant'] = constant_only[:20]

    # formatApiError coverage for the duplicate-name family on this build
    fmt = (REPO / 'web' / 'src' / 'lib' / 'api-errors.ts').read_text()
    scan['formatApiError'] = {
        'duplicate_name_pattern_present': bool(re.search(r'name already exists', fmt)),
        'unique_constraint_pattern_present': bool(re.search(r'unique constraint failed', fmt, re.I)),
        'sqlite_leak_translation': 'UNIQUE constraint failed' in fmt,
    }
    scan['method'] = ('read-only grep scan of web/src for hardcoded generic error strings, '
                      'error-surface call sites, and api-errors.ts mapping; no writes, no runtime calls')
    scan['note'] = ('full cross-page runtime enumeration is out of scope for this batch; the scan is the '
                    'read-only counterpart agreed in partial-closeout-plan B1 ("跨页错误文案只读扫描")')
    L.save('g0403-source-scan.json', scan)
except Exception as exc:
    scan['exception'] = str(exc)
    L.save('g0403-source-scan.json', scan)
    raise

# ---------- cleanup ----------
cleanup = {'case_id': 'G04-03', 'at_utc': L.now_utc(), 'deleted': []}
for gid, gname in [(str((b1 or {}).get('id')), G1), (str(g2id), G2)]:
    c, b, _ = hub.delete('/api/groups/' + gid)
    gc, gb, _ = hub.get('/api/groups/' + gid)
    cleanup['deleted'].append({'name': gname, 'id': gid, 'delete_http': c, 'get_after_http': gc, 'get404': gc == 404})
    L.ledger_add({'kind': 'group', 'name': gname, 'id': gid, 'status': 'deleted', 'delete_http': c, 'get_after_http': gc})
cleanup['all_deleted_get404'] = all(d['get404'] for d in cleanup['deleted'])
L.save('g0403-cleanup.json', cleanup)
print('G04-03 done. api=', results.get('api_verdict'), 'ui=', ui.get('status'), 'cleanup404=', cleanup['all_deleted_get404'], flush=True)
