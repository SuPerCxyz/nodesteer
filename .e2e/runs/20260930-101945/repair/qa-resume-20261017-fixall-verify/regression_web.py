#!/usr/bin/env python3
"""qa-resume-20261017-fixall-verify: Web-side regression on the deployed fixall build.

Build: dddee4f0-e2e-repair-20261017-fixall. Authority: repair hub :18080 only.
Real browser (agent-browser) against the real UI; owned fixtures only.

Covers: logout revocation, four editors' pending-disabled, skip-to-main focus,
narrow-viewport overflow, exit_code UI, node-selection drift + destructive
controls (delete node/script/application/artifact, revoke credential).
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402
import hostlib  # noqa: E402
import regression_api as A  # noqa: E402

EV = HERE / 'evidence'
HUB = 'http://192.168.100.209:18080'
AB = 'agent-browser'
os.umask(0o077)
hub = hubclient.Hub()


class Browser:
    def __init__(self, session):
        self.session = session

    def run(self, *args, timeout=120, stdin=None, check=True):
        import subprocess
        cmd = [AB, '--session', self.session] + [str(a) for a in args]
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
        out, err = (cp.stdout or '').strip(), (cp.stderr or '').strip()
        if check and cp.returncode != 0:
            raise RuntimeError('browser_cmd_failed(%s rc=%s): %s' % (args[0], cp.returncode, (err or out)[-400:]))
        return cp.returncode, out, err

    def open(self, url, timeout=150):
        return self.run('open', url, timeout=timeout)[1]

    def evaljs(self, js, timeout=120):
        rc, out, err = self.run('eval', '--stdin', timeout=timeout, stdin=js, check=False)
        if rc != 0:
            raise RuntimeError('eval_failed: %s' % (err or out)[-400:])
        out = out.strip()
        for cand in (out, out.splitlines()[-1] if out else ''):
            try:
                return json.loads(cand)
            except Exception:
                continue
        return out

    def click_marked(self, tag, timeout=60):
        rc, out, err = self.run('click', '[data-fixall-target="%s"]' % tag, timeout=timeout, check=False)
        return {'rc': rc, 'out': out[-200:], 'err': err[-300:]}

    def press(self, key, timeout=60):
        return self.run('press', key, timeout=timeout, check=False)

    def set_viewport(self, w, h):
        return self.run('set', 'viewport', w, h)[1]

    def url(self):
        return self.evaljs('location.href')

    def screenshot(self, name):
        path = str(EV / name)
        rc, out, err = self.run('screenshot', '--full', path, timeout=150, check=False)
        return {'rc': rc, 'path': 'evidence/' + name}


BR = Browser('fixall-' + A.NONCE)


def mark_js(js_expr, tag):
    """Mark one element (found by JS expression) with data-fixall-target=<tag>."""
    js = ("(function(){document.querySelectorAll('[data-fixall-target=\"%s\"]').forEach(e=>e.removeAttribute('data-fixall-target'));"
          "const el=(%s);if(!el)return 'missing';el.scrollIntoView({block:'center'});"
          "el.setAttribute('data-fixall-target','%s');return 'marked';})()" % (tag, js_expr, tag))
    return BR.evaljs(js)


def click_el(js_expr, tag):
    r = mark_js(js_expr, tag)
    if r != 'marked':
        return {'marked': r}
    out = BR.click_marked(tag)
    return {'marked': r, 'click': out}


def login(username, password):
    BR.open(HUB + '/sign-in')
    time.sleep(2)
    if BR.evaljs('!!localStorage.getItem("nodesteer_token") && location.pathname!=="/sign-in"'):
        return {'already': True, 'url': BR.url()}
    js = ("(function(){const set=(el,v)=>{const proto=Object.getPrototypeOf(el);"
          "const d=Object.getOwnPropertyDescriptor(proto,'value')||Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
          "d.set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};"
          "set(document.querySelector('input[name=\"username\"]'),%s);"
          "set(document.querySelector('input[name=\"password\"]'),%s);"
          "const btn=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='登录');"
          "if(!btn)return 'no-btn';btn.click();return 'submitted';})()"
          % (json.dumps(username), json.dumps(password)))
    r = BR.evaljs(js)
    deadline = time.time() + 30
    while time.time() < deadline:
        if BR.evaljs('!!localStorage.getItem("nodesteer_token") && location.pathname!=="/sign-in"'):
            return {'submit': r, 'url': BR.url()}
        time.sleep(1)
    raise RuntimeError('login_not_completed: %s' % BR.url())


def api_me_status(token):
    # 独立客户端且不登录：否则 hubclient 会用自身 admin token 覆盖传入的 Authorization 头
    fresh = hubclient.Hub()
    code, _ = fresh.raw('GET', '/api/me', None, headers={'Authorization': 'Bearer ' + token})
    return code


def wait_node_deleted(node_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, _ = hub.get('/api/nodes/' + node_id)
        if code == 404:
            return True
        time.sleep(1)
    return False


def find_owned_rows():
    """Return [{idx, name, checked}] for the owned node rows in the current table."""
    return BR.evaljs(
        "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
        "return rows.map((r,i)=>{const cb=r.querySelector('button[role=\"checkbox\"]');"
        "const name=(r.innerText.match(/qa-fixall-[0-9a-f]+-[a-d]/)||[])[0]||null;"
        "return {idx:i,name:name,checked:cb?cb.getAttribute('data-state')==='checked':null};});})()")


def node_id_by_name(name):
    for k, v in A.ST['children'].items():
        if v['name'] == name:
            return v['node_id']
    return None


# ---------------------------------------------------------------- cases
def case_logout():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261010-UI-LOGOUT-NOT-REVOKED']}
    login(hub.cred['admin_username'], hub.cred['admin_password'])
    token = BR.evaljs('localStorage.getItem("nodesteer_token")')
    out['token_captured'] = bool(token)
    out['me_before'] = api_me_status(token)
    out['me_before_via_browser'] = BR.evaljs(
        "(async()=>{const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+localStorage.getItem('nodesteer_token')}});return r.status;})()")
    # open the user dropdown with a real click
    r1 = click_el("Array.from(document.querySelectorAll('button')).find(b=>b.innerText.includes('qa-repair'))", 'usermenu')
    out['open_menu'] = r1
    time.sleep(1)
    r2 = click_el("Array.from(document.querySelectorAll('[role=\"menuitem\"]')).find(i=>i.innerText.trim()==='退出登录')", 'logoutitem')
    out['click_logout'] = r2
    time.sleep(1)
    dlg = BR.evaljs("(function(){const d=document.querySelector('[role=\"alertdialog\"]');return d?d.innerText.replace(/\\n+/g,' | '):null;})()")
    out['dialog'] = dlg
    r3 = click_el("(function(){const d=document.querySelector('[role=\"alertdialog\"]');if(!d)return null;"
                  "const bs=Array.from(d.querySelectorAll('button'));return bs[bs.length-1];})()", 'confirm')
    out['click_confirm'] = r3
    deadline = time.time() + 20
    while time.time() < deadline:
        if BR.evaljs('location.pathname') == '/sign-in':
            break
        time.sleep(0.5)
    out['url_after'] = BR.url()
    out['token_after'] = BR.evaljs('localStorage.getItem("nodesteer_token")')
    out['me_after_old_token'] = api_me_status(token)
    out['screenshot'] = BR.screenshot('web-logout-after.png')
    ok = (out['me_before'] == 200 and out['me_after_old_token'] == 401
          and out['token_after'] is None and '/sign-in' in out['url_after'])
    A.record('OBS-20261010-UI-LOGOUT-NOT-REVOKED', 'web', 'FIXED' if ok else 'STILL_BROKEN',
             'UI 退出后旧 token /api/me -> 401（服务端会话已撤销）' if ok
             else '仍复现：%s' % {k: out[k] for k in ('me_before', 'me_after_old_token', 'url_after')},
             ['evidence/web-logout.json'], out)
    A.ev_write('web-logout.json', out)
    # relogin for the following cases
    out['relogin'] = login(hub.cred['admin_username'], hub.cred['admin_password'])
    A.ev_write('web-logout.json', out)
    A.dump_results()
    return out


PENDING_TARGETS = [
    ('schedule', '/api/schedules/', 'PUT', '保存'),
    ('script', '/api/scripts/', 'PUT', '保存'),
    ('group', '/api/groups/', 'PUT', '保存'),
    ('application', '/api/applications/', 'PUT', '保存'),
]


def case_pending_editors():
    out = {'at_utc': A.now_utc(), 'case': ['E2E-20261016-EDITOR-PENDING-NOT-DISABLED-DUPLICATE-POST'], 'editors': {}}
    paths = {'schedule': '/schedules/' + A.ST['ids']['schedule'],
             'script': '/scripts/' + A.ST['ids']['script'],
             'group': '/groups/' + A.ST['ids']['group'],
             'application': '/applications/' + A.ST['ids']['app']}
    for key, match, method, label in PENDING_TARGETS:
        rec = {}
        BR.open(HUB + paths[key])
        time.sleep(3)
        rec['url'] = BR.url()
        rec['patch'] = BR.evaljs(
            "(function(){window.__fx={log:[],hold:null};const orig=window.fetch.bind(window);window.__fx.orig=orig;"
            "window.fetch=function(input,init){const url=(typeof input==='string')?input:(input&&input.url)||'';"
            "const m=((init&&init.method)||(input&&input.method)||'GET').toUpperCase();"
            "const rec={url:url,method:m};"
            "if(window.__fx.hold&&url.includes(window.__fx.hold.match)&&m===window.__fx.hold.method){"
            "window.__fx.log.push(rec);return new Promise(function(resolve,reject){window.__fx.hold.release=reject;});}"
            "window.__fx.log.push(rec);return orig(input,init);};"
            "window.__fx.hold={match:%s,method:%s};"
            "const btn=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()===%s);"
            "if(btn)btn.setAttribute('data-fixall-target','save');"
            "return JSON.stringify({hasSave:!!btn,url:location.pathname});})()"
            % (json.dumps(match), json.dumps(method), json.dumps(label)))
        click = BR.click_marked('save')
        time.sleep(1.5)
        rec['after_first_click'] = BR.evaljs(
            "(function(){const btn=document.querySelector('[data-fixall-target=\"save\"]');"
            "if(!btn)return {missing:true};"
            "const before={disabled:btn.disabled,ariaBusy:btn.getAttribute('aria-busy'),"
            "requests:window.__fx.log.filter(r=>r.method===%s&&r.url.includes(%s)).length};"
            "btn.click();btn.click();"
            "return {before:before,requestsAfterDoubleClick:window.__fx.log.filter(r=>r.method===%s&&r.url.includes(%s)).length};})()"
            % (json.dumps(method), json.dumps(match), json.dumps(method), json.dumps(match)))
        rec['click'] = click
        rec['release'] = BR.evaljs(
            "(function(){if(window.__fx.hold&&window.__fx.hold.release){window.__fx.hold.release(new Error('fixall-release'));return 'released';}return 'no-hold';})()")
        time.sleep(1.5)
        rec['after_release'] = BR.evaljs(
            "(function(){const btn=document.querySelector('[data-fixall-target=\"save\"]');"
            "return btn?{disabled:btn.disabled,ariaBusy:btn.getAttribute('aria-busy')}:{missing:true};})()")
        rec['restore'] = BR.evaljs("(function(){if(window.__fx&&window.__fx.orig){window.fetch=window.__fx.orig;return 'restored';}return 'no';})()")
        b = rec['after_first_click'] or {}
        before = b.get('before') or {}
        ok = (before.get('disabled') is True and before.get('ariaBusy') == 'true'
              and before.get('requests') == 1 and b.get('requestsAfterDoubleClick') == 1)
        rec['verdict'] = {'disabled': before.get('disabled') is True,
                          'aria_busy': before.get('ariaBusy') == 'true',
                          'single_request': before.get('requests') == 1 and b.get('requestsAfterDoubleClick') == 1}
        out['editors'][key] = rec
        A.record('E2E-20261016-EDITOR-PENDING-NOT-DISABLED-DUPLICATE-POST-' + key.upper(), 'web',
                 'FIXED' if ok else 'STILL_BROKEN',
                 '%s 编辑器 pending 期间保存按钮 disabled + aria-busy，重复点击仍只发 1 次请求' % key if ok
                 else '%s 编辑器偏差：%s' % (key, rec['verdict']),
                 ['evidence/web-pending.json'], rec)
    A.ev_write('web-pending.json', out)
    A.dump_results()
    return out


def case_skip_main():
    out = {'at_utc': A.now_utc(), 'case': ['WEB-SKIP-TO-MAIN-DEAD-ANCHOR-20261017']}
    BR.open(HUB + '/nodes')
    time.sleep(3)
    out['content_exists'] = BR.evaljs("(function(){const c=document.getElementById('content');return c?{tabindex:c.getAttribute('tabindex')}:null;})()")
    out['blur'] = BR.evaljs("(function(){if(document.activeElement&&document.activeElement.blur)document.activeElement.blur();return document.activeElement===document.body? 'body':'other';})()")
    out['press_tab'] = BR.press('Tab')
    time.sleep(0.5)
    out['focused_after_tab'] = BR.evaljs(
        "(function(){const a=document.activeElement;return a?{tag:a.tagName,text:(a.innerText||'').trim().slice(0,40),href:a.getAttribute&&a.getAttribute('href')}:null;})()")
    out['press_enter'] = BR.press('Enter')
    time.sleep(0.8)
    out['focused_after_enter'] = BR.evaljs(
        "(function(){const a=document.activeElement;return a?{tag:a.tagName,id:a.id,tabindex:a.getAttribute&&a.getAttribute('tabindex')}:null;})()")
    out['screenshot'] = BR.screenshot('web-skip-to-main.png')
    ok = (out['focused_after_enter'] or {}).get('id') == 'content'
    A.record('WEB-SKIP-TO-MAIN-DEAD-ANCHOR-20261017', 'web', 'FIXED' if ok else 'STILL_BROKEN',
             'Tab 聚焦跳转链接 + Enter 后焦点移动到 #content' if ok
             else '仍复现：%s' % out['focused_after_enter'],
             ['evidence/web-skipmain.json'], out)
    A.ev_write('web-skipmain.json', out)
    A.dump_results()
    return out


def measure_overflow():
    return BR.evaljs(
        "(function(){return {docScrollW:document.documentElement.scrollWidth,"
        "docClientW:document.documentElement.clientWidth,"
        "bodyScrollW:document.body.scrollWidth,"
        "innerW:window.innerWidth,"
        "pageOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth};})()")


def case_narrow():
    out = {'at_utc': A.now_utc(), 'case': ['E2E-20261016-NARROW-VIEWPORT-LONG-VALUE-OVERFLOW'], 'checks': {}}
    pages = {'schedule': '/schedules/' + A.ST['ids']['schedule'],
             'script': '/scripts/' + A.ST['ids']['script']}
    for key, path in pages.items():
        for w, h in ((760, 900), (480, 800)):
            BR.set_viewport(w, h)
            BR.open(HUB + path)
            time.sleep(2.5)
            m = measure_overflow()
            extra = BR.evaljs(
                "(function(){const main=document.querySelector('main')||document.querySelector('[data-slot=sidebar-inset]')||document.body;"
                "return {mainW:main?main.getBoundingClientRect().width:null};})()")
            rec = {'viewport': '%dx%d' % (w, h), 'measure': m, 'extra': extra}
            rec['no_page_overflow'] = not m.get('pageOverflow')
            out['checks']['%s_%d' % (key, w)] = rec
            A.ev_write('web-narrow.json', out)
    BR.set_viewport(1600, 1000)
    ok = all(c['no_page_overflow'] for c in out['checks'].values())
    A.record('E2E-20261016-NARROW-VIEWPORT-LONG-VALUE-OVERFLOW', 'web', 'FIXED' if ok else 'STILL_BROKEN',
             '760/480 视口下 /schedules/{id} 与 /scripts/{id} 均无页面级横向滚动' if ok
             else '仍有溢出：%s' % {k: (v['measure'].get('docScrollW'), v['measure'].get('innerW')) for k, v in out['checks'].items() if not v['no_page_overflow']},
             ['evidence/web-narrow.json'], out)
    A.ev_write('web-narrow.json', out)
    A.dump_results()
    return out


def case_exitcode_ui():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261008-EXECUTION-ZERO-EXIT-CODE-UI']}
    import urllib.parse
    BR.open(HUB + '/executions?q=' + urllib.parse.quote('exit0-超长'))
    time.sleep(3)
    out['url'] = BR.url()
    out['rows'] = BR.evaljs(
        "(function(){const heads=Array.from(document.querySelectorAll('table thead th')).map(t=>t.innerText.trim());"
        "const rows=Array.from(document.querySelectorAll('table tbody tr')).map(r=>{"
        "const cells=Array.from(r.querySelectorAll('td')).map(td=>td.innerText.trim());return cells;});"
        "const ei=heads.indexOf('退出码');"
        "return {heads:heads,exitIndex:ei,exitValues:rows.map(c=>c[ei]),taskCells:rows.map(c=>c[1].slice(0,60))};})()")
    out['screenshot'] = BR.screenshot('web-exitcode.png')
    vals = (out['rows'] or {}).get('exitValues') or []
    ok = len(vals) >= 1 and vals[0] == '0'
    A.record('OBS-20261008-EXECUTION-ZERO-EXIT-CODE-UI', 'web', 'FIXED' if ok else 'STILL_BROKEN',
             '执行列表退出码列显示 0（非 -）' if ok else '仍显示异常：%s' % vals[:3],
             ['evidence/web-exitcode.json'], out)
    A.ev_write('web-exitcode.json', out)
    A.dump_results()
    return out


def ui_delete_row_menu(row_expr, dialog_label, tag_prefix):
    """Open a row's MoreMenu, click 删除, confirm. Returns steps."""
    steps = {}
    steps['open_menu'] = click_el(
        "(function(){const r=(%s);return r?r.querySelector('button[aria-label=\"操作\"]'):null;})()" % row_expr,
        tag_prefix + '-menu')
    time.sleep(1)
    steps['click_delete_item'] = click_el(
        "Array.from(document.querySelectorAll('[role=\"menuitem\"]')).find(i=>i.innerText.trim()==='删除')",
        tag_prefix + '-delitem')
    time.sleep(1)
    steps['dialog'] = BR.evaljs(
        "(function(){const d=document.querySelector('[role=\"alertdialog\"]');return d?d.innerText.replace(/\\n+/g,' | ').slice(0,160):null;})()")
    steps['click_confirm'] = click_el(
        "(function(){const d=document.querySelector('[role=\"alertdialog\"]');if(!d)return null;"
        "const bs=Array.from(d.querySelectorAll('button'));return bs[bs.length-1];})()", tag_prefix + '-confirm')
    time.sleep(2)
    return steps


def case_destructive_web():
    out = {'at_utc': A.now_utc(), 'cases': {}}
    st = A.ST
    app_id = st['ids']['app']
    script_id = st['ids']['script']
    group_id = st['ids']['group']
    art_id = st['ids']['artifact_A2']
    # ---------------- 3 delete application (UI) + agent host cleanup
    rec = {'before': {}}
    code, app = hub.get('/api/applications/' + app_id)
    rec['before']['http'] = code
    rec['before']['assigned'] = hub.get('/api/applications/%s/nodes' % app_id)[1]
    BR.open(HUB + '/applications')
    time.sleep(3)
    row = ("Array.from(document.querySelectorAll('table tbody tr')).find(r=>r.innerText.includes(%s))"
           % json.dumps(A.APP_NAME))
    rec['ui_delete'] = ui_delete_row_menu(row, '删除', 'appdel')
    ok404 = False
    deadline = time.time() + 60
    while time.time() < deadline:
        c, _ = hub.get('/api/applications/' + app_id)
        if c == 404:
            ok404 = True
            break
        time.sleep(1)
    rec['after_get_http'] = c
    rec['get404'] = ok404
    # agent-side cleanup on child a host (unit/binary/config removed by product path)
    child_a = A.child_of('a')
    step = None
    deadline = time.time() + 90
    while time.time() < deadline:
        step = child_a.remote_py(
            "import json,os,subprocess\n"
            "unit=%(unit)r; bp=%(bp)r; cp=%(cp)r; opt=%(opt)r\n"
            "out={'unit_lexists':os.path.lexists('/etc/systemd/system/'+unit),"
            "'binary_lexists':os.path.lexists(bp),'config_lexists':os.path.lexists(cp),"
            "'opt_exists':os.path.isdir(opt)}\n"
            "r=subprocess.run(['systemctl','show','-p','LoadState','--value',unit],capture_output=True,text=True)\n"
            "out['unit_load_state']=r.stdout.strip()\n"
            "print(json.dumps(out))\n" % {'unit': A.UNIT_NAME, 'bp': A.BINARY, 'cp': A.CONFIG,
                                          'opt': A.OPT_ROOT})
        if not step.get('unit_lexists') and not step.get('binary_lexists') and not step.get('config_lexists'):
            break
        time.sleep(3)
    rec['agent_cleanup'] = step
    rec['verdict'] = {'ui_delete': bool(rec['ui_delete'].get('click_confirm')),
                      'get404': ok404,
                      'agent_cleaned': not step.get('unit_lexists') and not step.get('binary_lexists')}
    out['cases']['delete_application'] = rec
    A.record('DESTRUCTIVE-DELETE-APPLICATION', 'destructive',
             'PASS' if all(rec['verdict'].values()) else 'PARTIAL',
             'UI 删除应用 → GET 404；Agent 侧 unit/binary/config 已由产品路径清理' if all(rec['verdict'].values())
             else '偏差：%s' % rec['verdict'], ['evidence/web-destructive.json'], rec)
    # ---------------- 2 delete script (UI)
    rec = {}
    BR.open(HUB + '/scripts')
    time.sleep(3)
    row = ("Array.from(document.querySelectorAll('table tbody tr')).find(r=>r.innerText.includes(%s))"
           % json.dumps('qa-fixall-%s-script' % A.NONCE))
    rec['ui_delete'] = ui_delete_row_menu(row, '删除', 'scriptdel')
    deadline = time.time() + 30
    c = 0
    while time.time() < deadline:
        c, _ = hub.get('/api/scripts/' + script_id)
        if c == 404:
            break
        time.sleep(1)
    rec['after_get_http'] = c
    rec['get404'] = c == 404
    out['cases']['delete_script'] = rec
    A.record('DESTRUCTIVE-DELETE-SCRIPT', 'destructive', 'PASS' if rec['get404'] else 'FAIL',
             'UI 删除脚本 → GET 404', ['evidence/web-destructive.json'], rec)
    # ---------------- delete group (UI) - needed before node deletion
    rec = {}
    BR.open(HUB + '/groups')
    time.sleep(3)
    row = ("Array.from(document.querySelectorAll('table tbody tr')).find(r=>r.innerText.includes(%s))"
           % json.dumps('qa-fixall-%s-group' % A.NONCE))
    rec['ui_delete'] = ui_delete_row_menu(row, '删除', 'groupdel')
    deadline = time.time() + 30
    c = 0
    while time.time() < deadline:
        c, _ = hub.get('/api/groups/' + group_id)
        if c == 404:
            break
        time.sleep(1)
    rec['after_get_http'] = c
    rec['get404'] = c == 404
    out['cases']['delete_group'] = rec
    A.record('FIXTURE-DELETE-GROUP', 'destructive', 'PASS' if rec['get404'] else 'FAIL',
             'UI 删除分组 → GET 404', ['evidence/web-destructive.json'], rec)
    # ---------------- 4 delete artifact (UI)
    rec = {}
    code, art = hub.get('/api/artifacts/' + art_id)
    rec['before_http'] = code
    BR.open(HUB + '/artifacts')
    time.sleep(3)
    row = ("Array.from(document.querySelectorAll('table tbody tr')).find(r=>r.innerText.includes(%s))"
           % json.dumps('qa-fixall-%s-payload-a2' % A.NONCE))
    rec['ui_delete'] = ui_delete_row_menu(row, '删除', 'artdel')
    deadline = time.time() + 30
    c = 0
    while time.time() < deadline:
        c, _ = hub.get('/api/artifacts/' + art_id)
        if c == 404:
            break
        time.sleep(1)
    rec['after_get_http'] = c
    rec['get404'] = c == 404
    out['cases']['delete_artifact'] = rec
    A.record('DESTRUCTIVE-DELETE-ARTIFACT', 'destructive', 'PASS' if rec['get404'] else 'FAIL',
             'UI 删除发布包 → GET 404', ['evidence/web-destructive.json'], rec)
    A.ev_write('web-destructive.json', out)
    A.dump_results()
    return out


def case_drift_and_nodes():
    out = {'at_utc': A.now_utc(), 'case': ['WEB-NODE-SELECTION-DRIFT-20261017', 'DESTRUCTIVE-DELETE-NODE', 'DESTRUCTIVE-REVOKE-CREDENTIAL']}
    BR.open(HUB + '/nodes')
    time.sleep(3)
    rows = find_owned_rows()
    out['rows_initial'] = rows
    owned = [r for r in rows if r['name']]
    if len(owned) < 3:
        raise RuntimeError('owned_rows_missing: %s' % rows)
    a_row, b_row, c_row = owned[0], owned[1], owned[2]
    out['roles'] = {'A': a_row['name'], 'B': b_row['name'], 'C': c_row['name']}
    # select B (real click on its checkbox)
    sel = click_el("(function(){const rows=document.querySelectorAll('table tbody tr');"
                   "return rows[%d].querySelector('button[role=\"checkbox\"]');})()" % b_row['idx'], 'cb-b')
    out['select_b'] = sel
    time.sleep(1)
    out['after_select'] = find_owned_rows()
    # delete A (row before B) via UI
    row_expr = "Array.from(document.querySelectorAll('table tbody tr')).find(r=>r.innerText.includes(%s))" % json.dumps(a_row['name'])
    out['delete_A'] = ui_delete_row_menu(row_expr, '删除', 'nodedelA')
    node_a = node_id_by_name(a_row['name'])
    out['A_deleted_get404'] = wait_node_deleted(node_a)
    time.sleep(2)
    out['rows_after_A_delete'] = find_owned_rows()
    # capture the bulk-action payload
    out['patch_fetch'] = BR.evaljs(
        "(function(){window.__fx={log:[]};const orig=window.fetch.bind(window);window.__fx.orig=orig;"
        "window.fetch=function(input,init){const url=(typeof input==='string')?input:(input&&input.url)||'';"
        "const m=((init&&init.method)||(input&&input.method)||'GET').toUpperCase();"
        "window.__fx.log.push({url:url,method:m,body:init&&init.body?String(init.body).slice(0,200):null});"
        "return orig(input,init);};return 'patched';})()")
    out['bulk_button_before'] = BR.evaljs(
        "(function(){const bs=Array.from(document.querySelectorAll('button')).filter(b=>b.innerText.includes('设为维护'));"
        "return bs.map(b=>b.innerText.trim());})()")
    out['click_bulk'] = click_el(
        "Array.from(document.querySelectorAll('button')).find(b=>b.innerText.includes('设为维护'))", 'bulk')
    time.sleep(1)
    out['bulk_dialog'] = BR.evaljs(
        "(function(){const d=document.querySelector('[role=\"alertdialog\"]');return d?d.innerText.replace(/\\n+/g,' | ').slice(0,200):null;})()")
    out['click_bulk_confirm'] = click_el(
        "(function(){const d=document.querySelector('[role=\"alertdialog\"]');if(!d)return null;"
        "const bs=Array.from(d.querySelectorAll('button'));return bs[bs.length-1];})()", 'bulkconfirm')
    time.sleep(2.5)
    out['bulk_posts'] = BR.evaljs("window.__fx.log.filter(r=>r.method==='POST'&&r.url.includes('/api/nodes/'))")
    out['restore_fetch'] = BR.evaljs("(function(){if(window.__fx&&window.__fx.orig){window.fetch=window.__fx.orig;return 'restored';}return 'no';})()")
    node_b = node_id_by_name(b_row['name'])
    node_c = node_id_by_name(c_row['name'])
    code_b, nb = hub.get('/api/nodes/' + node_b)
    code_c, nc = hub.get('/api/nodes/' + node_c)
    out['status_after_bulk'] = {'b': (nb or {}).get('status'), 'c': (nc or {}).get('status')}
    posts = out['bulk_posts'] or []
    hit_b = any(node_b in (p.get('url') or '') for p in posts)
    hit_c = any(node_c in (p.get('url') or '') for p in posts)
    out['verdict_drift'] = {
        'b_checkbox_kept_after_row_removal': any(r['name'] == b_row['name'] and r['checked'] for r in (out['rows_after_A_delete'] or [])),
        'bulk_post_hits_selected_b': hit_b,
        'bulk_post_does_not_hit_c': not hit_c,
        'b_status_maintenance': out['status_after_bulk']['b'] == 'maintenance',
        'c_status_unchanged_online': out['status_after_bulk']['c'] == 'online',
    }
    A.record('WEB-NODE-SELECTION-DRIFT-20261017', 'web',
             'FIXED' if all(out['verdict_drift'].values()) else 'STILL_BROKEN',
             '删除选中行之前的行后，选择仍跟随原节点：批量操作 payload 命中真正选中的节点（非漂移行）'
             if all(out['verdict_drift'].values()) else '仍复现：%s' % out['verdict_drift'],
             ['evidence/web-drift.json'], out)
    # resume B via bulk resume
    BR.open(HUB + '/nodes')
    time.sleep(2)
    rows2 = find_owned_rows()
    b_row2 = next((r for r in rows2 if r['name'] == b_row['name']), None)
    out['select_b_resume'] = click_el("(function(){const rows=document.querySelectorAll('table tbody tr');"
                                      "return rows[%d].querySelector('button[role=\"checkbox\"]');})()" % b_row2['idx'], 'cb-b2') if b_row2 else None
    time.sleep(1)
    out['click_resume'] = click_el(
        "Array.from(document.querySelectorAll('button')).find(b=>b.innerText.includes('恢复'))", 'resume')
    time.sleep(1)
    out['resume_confirm'] = click_el(
        "(function(){const d=document.querySelector('[role=\"alertdialog\"]');if(!d)return null;"
        "const bs=Array.from(d.querySelectorAll('button'));return bs[bs.length-1];})()", 'resumeconfirm')
    time.sleep(2.5)
    code_b, nb = hub.get('/api/nodes/' + node_b)
    out['b_status_after_resume'] = (nb or {}).get('status')
    A.ev_write('web-drift.json', out)
    # ---------------- 5 revoke credential on D
    d_row = owned[3] if len(owned) > 3 else None
    rec = {}
    if d_row:
        node_d = node_id_by_name(d_row['name'])
        code, body = hub.post('/api/nodes/%s/revoke-credential' % node_d, {})
        rec['revoke_http'] = code
        code, nd = hub.get('/api/nodes/' + node_d)
        rec['status_after'] = (nd or {}).get('status')
        rec['verdict'] = {'http_200': code == 200, 'status_disabled': rec['status_after'] == 'disabled'}
        A.record('DESTRUCTIVE-REVOKE-CREDENTIAL', 'destructive',
                 'PASS' if all(rec['verdict'].values()) else 'FAIL',
                 'owned 节点撤销凭证：200 且状态变 disabled（UI 同一端点 /nodes/{id}/revoke-credential）'
                 if all(rec['verdict'].values()) else '偏差：%s' % rec,
                 ['evidence/web-drift.json'], rec)
        out['revoke'] = rec
    # ---------------- 1 delete nodes B, C, D via UI
    deleted = {}
    for r in owned[1:]:
        name = r['name']
        node_id = node_id_by_name(name)
        if name == b_row['name'] and out.get('b_status_after_resume') == 'maintenance':
            hub.post('/api/nodes/%s' % node_id, {'status': 'online'})
        BR.open(HUB + '/nodes')
        time.sleep(2.5)
        row_expr = "Array.from(document.querySelectorAll('table tbody tr')).find(r=>r.innerText.includes(%s))" % json.dumps(name)
        steps = ui_delete_row_menu(row_expr, '删除', 'nodedel-' + name[-1])
        gone = wait_node_deleted(node_id)
        deleted[name] = {'steps': steps, 'get404': gone}
    out['deleted_nodes'] = deleted
    out['delete_verdict'] = {'all_get404': all(v['get404'] for v in deleted.values()),
                             'count': len(deleted)}
    A.record('DESTRUCTIVE-DELETE-NODE', 'destructive',
             'PASS' if out['delete_verdict']['all_get404'] else 'PARTIAL',
             'UI 删除 owned 节点（含漂移场景 A 节点）：逐个 GET 404（共 %d 个）' % (len(deleted) + 1),
             ['evidence/web-drift.json'], {'deleted': deleted, 'a_404': out.get('A_deleted_get404')})
    out['screenshot'] = BR.screenshot('web-nodes-after-deletes.png')
    A.ev_write('web-drift.json', out)
    A.dump_results()
    return out


def main():
    hub.login()
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    cases = {'logout': case_logout, 'pending': case_pending_editors, 'skipmain': case_skip_main,
             'narrow': case_narrow, 'exitcode': case_exitcode_ui,
             'destructive': case_destructive_web, 'drift': case_drift_and_nodes}
    order = ['logout', 'pending', 'skipmain', 'narrow', 'exitcode', 'destructive', 'drift']
    if which == 'all':
        for name in order:
            print('==== web case', name, flush=True)
            cases[name]()
    else:
        cases[which]()


if __name__ == '__main__':
    main()
