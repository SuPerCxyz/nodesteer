#!/usr/bin/env python3
"""qa-resume-20261018-final-verify: Web-side regression on the deployed final build.

Build: dddee4f0-e2e-repair-20261018-final. Authority: repair hub :18080 only.
Real browser (agent-browser) against the real UI; owned fixtures only.

Covers task items 11-22:
11 Tasks loading/empty states         12 Dashboard app-state fault oracle
13 App state card version/time        14 Task save payload field hygiene
15 AND condition summary + clear     16 Cross-page links (audit/transfer/user/artifact/node)
17 Badge localization (transfer/node) 18 Invalid task deeplink
19 pageSize out of range             20 Schedule inline validation
21 Sidebar role filter                22 Logout session revocation (+session refresh)
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402
import hostlib  # noqa: E402
import regression_go as A  # noqa: E402

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
        rc, out, err = self.run('click', '[data-fx-target="%s"]' % tag, timeout=timeout, check=False)
        return {'rc': rc, 'out': out[-200:], 'err': err[-300:]}

    def press(self, key, timeout=60):
        return self.run('press', key, timeout=timeout, check=False)

    def url(self):
        return self.evaljs('location.href')

    def screenshot(self, name):
        path = str(EV / name)
        rc, out, err = self.run('screenshot', '--full', path, timeout=150, check=False)
        return {'rc': rc, 'path': 'evidence/' + name}


BR = Browser('final-' + A.NONCE)


def mark_js(js_expr, tag):
    js = ("(function(){document.querySelectorAll('[data-fx-target=\"%s\"]').forEach(e=>e.removeAttribute('data-fx-target'));"
          "const el=(%s);if(!el)return 'missing';el.scrollIntoView({block:'center'});"
          "el.setAttribute('data-fx-target','%s');return 'marked';})()" % (tag, js_expr, tag))
    return BR.evaljs(js)


def click_el(js_expr, tag):
    r = mark_js(js_expr, tag)
    if r != 'marked':
        return {'marked': r}
    out = BR.click_marked(tag)
    return {'marked': r, 'click': out}


def body_text():
    return BR.evaljs("document.body.innerText") or ''


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


def switch_user(username, password):
    BR.evaljs("localStorage.removeItem('nodesteer_token');")
    BR.open(HUB + '/sign-in')
    time.sleep(1.5)
    return login(username, password)


FETCH_PATCH = r'''
(function(){
  if (window.__fx && window.__fx.orig) { try { window.fetch = window.__fx.orig; } catch(e){} }
  const orig = window.fetch.bind(window);
  window.__fx = {orig: orig, mode: 'normal', releases: [], bodies: [], calls: []};
  window.fetch = function(input, init){
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    const m = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    window.__fx.calls.push({url: url, method: m});
    if (init && init.body) {
      const rec = {url: url, method: m, body: String(init.body)};
      window.__fx.bodies.push(rec);
      try {
        const arr = JSON.parse(sessionStorage.getItem('__fx_bodies') || '[]');
        arr.push(rec);
        sessionStorage.setItem('__fx_bodies', JSON.stringify(arr));
      } catch (e) {}
    }
    const isTasksList = /\/api\/tasks(\?|$)/.test(url) && m === 'GET';
    const isAppState = /\/api\/applications\/[^\/]+\/state/.test(url) && m === 'GET';
    if (window.__fx.mode === 'tasks-hold' && isTasksList) {
      return new Promise(function(resolve){ window.__fx.releases.push(function(){ resolve(new Response('[]', {status:200, headers:{'Content-Type':'application/json'}})); }); });
    }
    if (window.__fx.mode === 'tasks-empty' && isTasksList) {
      return Promise.resolve(new Response('[]', {status:200, headers:{'Content-Type':'application/json'}}));
    }
    if (window.__fx.mode === 'appstate-fail' && isAppState) {
      return Promise.resolve(new Response('{"error":"qa-final injected state fault"}', {status:500, headers:{'Content-Type':'application/json'}}));
    }
    return orig(input, init);
  };
  return 'patched';
})()
'''


def release_holds():
    return BR.evaljs("(function(){const n=(window.__fx&&window.__fx.releases||[]).length;"
                     "(window.__fx&&window.__fx.releases||[]).forEach(f=>f());window.__fx.releases=[];return n;})()")


def set_mode(mode):
    return BR.evaljs("window.__fx && (window.__fx.mode=%s)" % json.dumps(mode))


def restore_fetch():
    return BR.evaljs("(function(){if(window.__fx&&window.__fx.orig){window.fetch=window.__fx.orig;return 'restored';}return 'no';})()")


def click_nav(href, tag=None):
    return click_el("Array.from(document.querySelectorAll('a')).find(a=>a.getAttribute('href')===%s)" % json.dumps(href),
                    tag or ('nav-' + href.strip('/').replace('/', '-')))


# ---------------------------------------------------------------- item 5 + 22
def case_session_and_logout():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261015-API-CONTRACT-ME-KEYS-UI',
                                           'OBS-20261010-UI-LOGOUT-NOT-REVOKED']}
    login(hub.cred['admin_username'], hub.cred['admin_password'])
    token = BR.evaljs('localStorage.getItem("nodesteer_token")')
    out['token_captured'] = bool(token)
    out['me_before'] = BR.evaljs(
        "(async()=>{const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+localStorage.getItem('nodesteer_token')}});"
        "const b=await r.json();return {status:r.status,keys:Object.keys(b).sort(),user_id:b.user_id,username:b.username,role:b.role};})()")
    # session refresh: reload the page, session must persist (Web consumes snake_case session)
    BR.open(HUB + '/')
    time.sleep(3)
    out['after_reload'] = {'url': BR.url(), 'token_kept': bool(BR.evaljs('localStorage.getItem("nodesteer_token")'))}
    out['me_after_reload'] = BR.evaljs(
        "(async()=>{const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+localStorage.getItem('nodesteer_token')}});return r.status;})()")
    me = out['me_before'] or {}
    keys = me.get('keys') or []
    ok5 = (out['token_captured'] and me.get('status') == 200
           and {'token', 'user_id', 'username', 'role', 'expires'} <= set(keys)
           and out['after_reload']['token_kept'] and out['me_after_reload'] == 200
           and '/sign-in' not in (out['after_reload']['url'] or ''))
    A.record('OBS-20261015-API-CONTRACT-ME-KEYS-UI', 'web', 'FIXED' if ok5 else 'STILL_BROKEN',
             'UI 登录/刷新后会话正常（snake_case 会话键）；/api/me 200' if ok5
             else '仍复现：%s' % out, ['evidence/web-session-logout.json'])
    # logout via the real user menu
    r1 = click_el("Array.from(document.querySelectorAll('button')).find(b=>b.innerText.includes(%s))"
                  % json.dumps('qa-repair'), 'usermenu')
    out['open_menu'] = r1
    time.sleep(1)
    r2 = click_el("Array.from(document.querySelectorAll('[role=\"menuitem\"]')).find(i=>i.innerText.trim()==='退出登录')", 'logoutitem')
    out['click_logout'] = r2
    time.sleep(1)
    out['dialog'] = BR.evaljs("(function(){const d=document.querySelector('[role=\"alertdialog\"]');return d?d.innerText.replace(/\\n+/g,' | '):null;})()")
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
    fresh = hubclient.Hub()
    out['me_after_old_token'] = fresh.raw('GET', '/api/me', None, headers={'Authorization': 'Bearer ' + (token or '')})[0]
    out['screenshot'] = BR.screenshot('web-logout-after.png')
    ok22 = (me.get('status') == 200 and out['me_after_old_token'] == 401
            and out['token_after'] is None and '/sign-in' in (out['url_after'] or ''))
    A.record('OBS-20261010-UI-LOGOUT-NOT-REVOKED', 'web', 'FIXED' if ok22 else 'STILL_BROKEN',
             'UI 退出后旧 token /api/me -> 401（服务端会话已撤销）' if ok22
             else '仍复现：%s' % {k: out[k] for k in ('me_before', 'me_after_old_token', 'url_after')},
             ['evidence/web-session-logout.json'], out)
    out['relogin'] = login(hub.cred['admin_username'], hub.cred['admin_password'])
    A.ev_write('web-session-logout.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 11
def case_tasks_states():
    out = {'at_utc': A.now_utc(), 'case': ['G03-10', 'TABLE-TASKS-ERROR']}
    login(hub.cred['admin_username'], hub.cred['admin_password'])
    BR.open(HUB + '/agents')  # ensure the tasks query is not cached yet
    time.sleep(2.5)
    out['patch'] = BR.evaljs(FETCH_PATCH)
    out['mode'] = set_mode('tasks-hold')
    out['nav'] = click_nav('/tasks')
    time.sleep(2)
    out['loading_state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;return {hasLoading:t.includes('加载中'),hasNoResults:t.includes('没有结果'),url:location.pathname};})()")
    out['screenshot_loading'] = BR.screenshot('web-tasks-loading.png')
    out['released'] = release_holds()
    time.sleep(2)
    out['empty_state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;return {hasNoData:t.includes('暂无数据'),hasNoResults:t.includes('没有结果')};})()")
    out['screenshot_empty'] = BR.screenshot('web-tasks-empty.png')
    out['restore'] = restore_fetch()
    # real data after restore: full reload of the tasks page
    BR.open(HUB + '/tasks')
    time.sleep(3.5)
    out['real_state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;return {hasOwnTask:t.includes(%s),hasLoading:t.includes('加载中')};})()"
        % json.dumps(A.P + 'task-main'))
    ok11 = (out['loading_state']['hasLoading'] and not out['loading_state']['hasNoResults']
            and out['empty_state']['hasNoData'] and not out['empty_state']['hasNoResults']
            and out['real_state']['hasOwnTask'])
    A.record('G03-10', 'web', 'FIXED' if ok11 else 'STILL_BROKEN',
             'Tasks 初始加载显示「加载中」而非「没有结果」；空数据「暂无数据」；恢复后真实列表可见' if ok11
             else '仍复现：%s' % {k: out[k] for k in ('loading_state', 'empty_state', 'real_state')},
             ['evidence/web-tasks-states.json'])
    A.record('TABLE-TASKS-ERROR', 'web', 'FIXED' if ok11 else 'STILL_BROKEN',
             '同 G03-10（TABLE-TASKS-ERROR 初始加载反馈）' if ok11 else '仍复现',
             ['evidence/web-tasks-states.json'])
    A.ev_write('web-tasks-states.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 12
def case_dashboard_fault():
    out = {'at_utc': A.now_utc(), 'case': ['P03-10', 'DASHBOARD-FAULT-ORACLE']}
    BR.open(HUB + '/agents')
    time.sleep(2)
    out['patch'] = BR.evaljs(FETCH_PATCH)
    out['mode'] = set_mode('appstate-fail')
    out['nav'] = click_nav('/')
    time.sleep(3.5)
    out['fault_state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;"
        "const alert=document.querySelector('[role=\"alert\"]');"
        "const retry=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='重试');"
        "return {hasUnavailable:t.includes('健康状态不可用'),hasZeroRatio:t.includes('0/2'),"
        "hasAlert:!!alert,alertText:alert?alert.innerText.slice(0,120):null,hasRetry:!!retry};})()")
    out['screenshot_fault'] = BR.screenshot('web-dashboard-fault.png')
    out['mode2'] = set_mode('normal')
    out['retry_click'] = click_el("Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='重试')", 'dash-retry')
    time.sleep(3)
    out['recovered'] = BR.evaljs(
        "(function(){const t=document.body.innerText;return {hasHealthyRatio:t.includes('2/2'),hasUnavailable:t.includes('健康状态不可用')};})()")
    out['screenshot_recovered'] = BR.screenshot('web-dashboard-recovered.png')
    out['restore'] = restore_fetch()
    ok12 = (out['fault_state']['hasUnavailable'] and not out['fault_state']['hasZeroRatio']
            and out['fault_state']['hasAlert'] and out['fault_state']['hasRetry']
            and out['recovered']['hasHealthyRatio'] and not out['recovered']['hasUnavailable'])
    A.record('P03-10', 'web', 'FIXED' if ok12 else 'STILL_BROKEN',
             '注入应用状态失败 -> 错误条 + 重试；健康比显示「健康状态不可用」而非 0/2；重试后恢复 2/2' if ok12
             else '仍复现：%s' % {k: out[k] for k in ('fault_state', 'recovered')},
             ['evidence/web-dashboard-fault.json'])
    A.record('DASHBOARD-FAULT-ORACLE', 'web', 'FIXED' if ok12 else 'STILL_BROKEN',
             '同 P03-10（Dashboard 故障 oracle）' if ok12 else '仍复现',
             ['evidence/web-dashboard-fault.json'])
    A.ev_write('web-dashboard-fault.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 13
def case_app_state_card():
    out = {'at_utc': A.now_utc(), 'case': ['P14P15-13']}
    app_id = A.ST['ids']['app']
    BR.open(HUB + '/applications/' + app_id)
    time.sleep(3.5)
    out['card'] = BR.evaljs(
        "(function(){const t=document.body.innerText;const time=document.querySelector('time');"
        "return {hasVersion:t.includes('版本 2.0'),hasCard:t.includes('健康检查'),"
        "timeTitle:time?time.getAttribute('title'):null,hasTime:!!time};})()")
    out['screenshot'] = BR.screenshot('web-app-state-card.png')
    ok13 = out['card']['hasVersion'] and out['card']['hasCard'] and out['card']['hasTime']
    A.record('P14P15-13', 'web', 'FIXED' if ok13 else 'STILL_BROKEN',
             '应用 state 卡片渲染「版本 2.0 · <time>」' if ok13
             else '仍复现：%s' % out['card'], ['evidence/web-app-state-card.json'])
    A.ev_write('web-app-state-card.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 14
def case_task_payload():
    out = {'at_utc': A.now_utc(), 'case': ['P09P11-12']}
    task_id = A.ST['ids']['task']
    BR.open(HUB + '/tasks/' + task_id + '/edit')
    time.sleep(3)
    out['patch'] = BR.evaljs(FETCH_PATCH)
    out['clear_storage'] = BR.evaljs("(function(){sessionStorage.removeItem('__fx_bodies');return 'cleared';})()")
    out['click_save'] = click_el("Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='保存')", 'task-save')
    time.sleep(3.5)
    out['bodies'] = BR.evaljs(
        "(function(){try{return JSON.parse(sessionStorage.getItem('__fx_bodies')||'[]').map(b=>({url:b.url,method:b.method,body:b.body.slice(0,1500)}));}catch(e){return [];}})()")
    out['restore'] = restore_fetch()
    save_bodies = [b for b in (out['bodies'] or [])
                   if b['method'] == 'PUT' and '/api/tasks/' in b['url']]
    payload = None
    if save_bodies:
        try:
            payload = json.loads(save_bodies[0]['body'])
        except Exception:
            payload = None
    forbidden = ['application_id', 'script_id', 'group_ids', 'label_key', 'label_value']
    present_forbidden = [k for k in forbidden if payload is not None and k in payload]
    out['payload_keys'] = sorted(payload.keys()) if payload else None
    out['forbidden_present'] = present_forbidden
    ok14 = (payload is not None and not present_forbidden
            and payload.get('type') == 'command'
            and payload.get('target', {}).get('type') == 'node'
            and payload.get('name') == A.P + 'task-main')
    A.record('P09P11-12', 'web', 'FIXED' if ok14 else 'STILL_BROKEN',
             '任务保存 PUT payload 不含 application_id/script_id/group_ids/label_*（键：%s）' % (out['payload_keys'],)
             if ok14 else '仍复现：forbidden=%s keys=%s' % (present_forbidden, out['payload_keys']),
             ['evidence/web-task-payload.json'])
    A.ev_write('web-task-payload.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 15
def case_condition_summary():
    out = {'at_utc': A.now_utc(), 'case': ['G5-AND-CONDITION-NO-SUMMARY-NO-CLEAR']}
    node_a = A.ST['children']['a']['node_id']
    node_b = A.ST['children']['b']['node_id']
    name_a = A.ST['children']['a']['name']
    name_b = A.ST['children']['b']['name']
    cond = {'type': 'and', 'and': [
        {'type': 'remote', 'remote': {'node_id': node_a, 'property': 'online', 'operator': '==', 'value': 'online'}},
        {'type': 'remote', 'remote': {'node_id': node_b, 'property': 'online', 'operator': '==', 'value': 'online'}},
    ]}
    task = {'name': A.P + 'task-and', 'type': 'command', 'command': 'echo and',
            'target': {'type': 'node', 'node_ids': [node_a]}, 'condition': cond,
            'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    code, t = hub.post('/api/tasks', task)
    if code not in (200, 201):
        raise RuntimeError('and_task_create_failed_%s_%s' % (code, t))
    A.ST['ids']['task_and'] = t['id']
    A.add_fixture('task', t['id'], task['name'])
    out['task'] = {'id': t['id'], 'http': code}
    BR.open(HUB + '/tasks/' + t['id'] + '/edit')
    time.sleep(3)
    out['summary'] = BR.evaljs(
        "(function(){const el=document.querySelector('[data-testid=\"condition-summary\"]');"
        "const t=document.body.innerText;"
        "return {exists:!!el,text:el?el.innerText:null,hasCard:t.includes('条件摘要'),"
        "hasClear:!!Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='清除全部条件')};})()")
    out['screenshot_before'] = BR.screenshot('web-condition-summary.png')
    out['clear_click'] = click_el("Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='清除全部条件')", 'cond-clear')
    time.sleep(1.5)
    out['after_clear'] = BR.evaljs(
        "(function(){const el=document.querySelector('[data-testid=\"condition-summary\"]');"
        "return {stillExists:!!el};})()")
    out['screenshot_after'] = BR.screenshot('web-condition-cleared.png')
    s = out['summary']
    ok15 = (s['exists'] and ' AND ' in (s['text'] or '')
            and name_a in (s['text'] or '') and name_b in (s['text'] or '')
            and s['hasCard'] and s['hasClear'] and not out['after_clear']['stillExists'])
    A.record('G5-AND-CONDITION-NO-SUMMARY-NO-CLEAR', 'web', 'FIXED' if ok15 else 'STILL_BROKEN',
             'AND 任务编辑页条件摘要含节点名 %s/%s 与 AND；一键清除后摘要消失' % (name_a, name_b) if ok15
             else '仍复现：%s' % {k: out[k] for k in ('summary', 'after_clear')},
             ['evidence/web-condition-summary.json'])
    A.ev_write('web-condition-summary.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 16
def case_crosspage():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261017-CROSSPAGE-LINK-GAPS']}
    task_id = A.ST['ids']['task']
    transfer_id = A.ST['ids']['transfer_happy']
    user_id = A.ST['ids']['user_dup']
    artifact_id = A.ST['ids']['artifact_A2']
    node_b = A.ST['children']['b']['node_id']
    schedule_id = A.ST['ids']['schedule']
    app_id = A.ST['ids']['app']
    # ① audit resource link -> /tasks/{id}
    BR.open(HUB + '/audit')
    time.sleep(3)
    out['audit_filter'] = BR.evaljs(
        "(function(){const inp=Array.from(document.querySelectorAll('input')).find(i=>(i.placeholder||'').includes('搜索'));"
        "if(!inp)return 'no-input';const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
        "d.set.call(inp,%s);inp.dispatchEvent(new Event('input',{bubbles:true}));return inp.value;})()"
        % json.dumps(task_id))
    time.sleep(2.5)
    out['audit_link'] = BR.evaljs(
        "(function(){const a=document.querySelector('a[href=%s]');"
        "return a?{href:a.getAttribute('href'),text:a.innerText.slice(0,40)}:null;})()"
        % json.dumps('/tasks/' + task_id))
    out['audit_click'] = click_el("document.querySelector('a[href=%s]')" % json.dumps('/tasks/' + task_id), 'audit-link')
    time.sleep(2.5)
    out['after_audit_click'] = {'path': BR.evaljs('location.pathname'),
                                'hasTask': (A.P + 'task-main') in body_text()}
    # ② transfer detail reachable
    BR.open(HUB + '/transfers/' + transfer_id)
    time.sleep(3)
    out['transfer_detail'] = BR.evaljs(
        "(function(){const t=document.body.innerText;return {path:location.pathname,"
        "hasStatus:t.includes('成功'),hasSourcePath:t.includes('xfer-src.txt'),hasTitle:t.includes('文件传输')};})()")
    # ③ user detail reachable
    BR.open(HUB + '/users/' + user_id)
    time.sleep(3)
    out['user_detail'] = BR.evaljs(
        "(function(){const t=document.body.innerText;return {path:location.pathname,hasUser:t.includes(%s)};})()"
        % json.dumps(A.P + 'dup'))
    # ④ artifact detail reachable
    BR.open(HUB + '/artifacts/' + artifact_id)
    time.sleep(3)
    out['artifact_detail'] = BR.evaljs(
        "(function(){const t=document.body.innerText;return {path:location.pathname,hasName:t.includes(%s)};})()"
        % json.dumps(A.P + 'payload-a2'))
    # ⑤ node detail backlinks (task / schedule / application)
    BR.open(HUB + '/agents/' + node_b)
    time.sleep(4)
    out['node_detail'] = BR.evaljs(
        "(function(){const links=Array.from(document.querySelectorAll('a')).map(a=>a.getAttribute('href'));"
        "const t=document.body.innerText;"
        "return {taskLink:links.includes(%s),schedLink:links.includes(%s),appLink:links.includes(%s),"
        "hasTaskSection:t.includes('任务'),hasSchedSection:t.includes('调度'),hasAppSection:t.includes('应用')};})()"
        % (json.dumps('/tasks/' + task_id), json.dumps('/schedules/' + schedule_id), json.dumps('/applications/' + app_id)))
    out['screenshot_node'] = BR.screenshot('web-node-backlinks.png')
    ok16 = (out['audit_link'] is not None and out['audit_link']['href'] == '/tasks/' + task_id
            and out['after_audit_click']['path'] == '/tasks/' + task_id and out['after_audit_click']['hasTask']
            and out['transfer_detail']['path'] == '/transfers/' + transfer_id and out['transfer_detail']['hasSourcePath']
            and out['user_detail']['hasUser'] and out['artifact_detail']['hasName']
            and out['node_detail']['taskLink'] and out['node_detail']['schedLink'] and out['node_detail']['appLink'])
    A.record('OBS-20261017-CROSSPAGE-LINK-GAPS', 'web', 'FIXED' if ok16 else 'STILL_BROKEN',
             '审计资源 ID 可点击跳转；transfers/users/artifacts 详情可达；节点详情含任务/调度/应用回链' if ok16
             else '仍复现：%s' % {k: out[k] for k in ('audit_link', 'after_audit_click', 'transfer_detail', 'user_detail', 'artifact_detail', 'node_detail')},
             ['evidence/web-crosspage.json'])
    A.ev_write('web-crosspage.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 17 helpers
def child_signal(ch, sig):
    return ch.remote_py(
        "import json,os,signal,subprocess,time\n"
        "cfg=%(cfg)r\n"
        "p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
        "pids=[int(x) for x in p.stdout.split() if x.isdigit()]\n"
        "for pid in pids:\n"
        "    try: os.kill(pid, signal.SIG%(sig)s)\n"
        "    except Exception: pass\n"
        "time.sleep(0.4)\n"
        "states={}\n"
        "for pid in pids:\n"
        "    try:\n"
        "        st=open('/proc/%%d/stat'%%pid).read().split(') ',1)[1].split()[0]\n"
        "        states[pid]=st\n"
        "    except Exception:\n"
        "        states[pid]='ERR'\n"
        "print(json.dumps({'pids':pids,'states':states}))\n" % {'cfg': ch.config, 'sig': sig})


def transfer_status(tid):
    c, t = hub.get('/api/transfers/' + tid)
    return t if c == 200 else None


def find_transfer_row_js(needle):
    return ("(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "const r=rows.find(r=>r.innerText.includes(%s));"
            "return r?{text:r.innerText.replace(/\\n+/g,' | '),html:r.innerHTML.slice(0,300)}:null;})()"
            % json.dumps(needle))


# ---------------------------------------------------------------- item 17
def case_badges():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261014-TRANSFER-STATUS-BADGE-NOT-LOCALIZED',
                                           'OBS-20261014-STATUSBADGE-MAINTENANCE-DISABLED-NO-ZH']}
    ch_a, ch_b = A.child_of('a'), A.child_of('b')
    node_b = A.ST['children']['b']['node_id']
    # ---- part 1: node status badges (maintenance + disabled) — filter by name to avoid pagination
    hub.post('/api/nodes/%s' % node_b, {'status': 'maintenance'})
    time.sleep(2)
    BR.open(HUB + '/agents')
    time.sleep(3.5)

    def filter_nodes(query):
        BR.evaljs(
            "(function(){const inp=Array.from(document.querySelectorAll('input')).find(i=>(i.placeholder||'').includes('搜索'));"
            "if(!inp)return 'no';const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
            "d.set.call(inp,%s);inp.dispatchEvent(new Event('input',{bubbles:true}));return inp.value;})()"
            % json.dumps(query))
        time.sleep(1.8)
        return BR.evaljs(
            "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "const r=rows.find(r=>r.innerText.includes(%s));"
            "return r?r.innerText.replace(/\\n+/g,' | ').slice(0,200):null;})()"
            % json.dumps(query))

    row_b = filter_nodes(A.P + 'b')
    row_d = filter_nodes(A.P + 'd')
    out['node_rows'] = {'b': row_b, 'd': row_d}
    out['node_badges'] = {
        'maintenance_zh': '维护' in (out['node_rows']['b'] or ''),
        'maintenance_en_absent': 'maintenance' not in (out['node_rows']['b'] or ''),
        'disabled_zh': '已禁用' in (out['node_rows']['d'] or ''),
        'disabled_en_absent': 'disabled' not in (out['node_rows']['d'] or ''),
    }
    out['screenshot_nodes'] = BR.screenshot('web-node-badges.png')
    # restore b to online
    hub.post('/api/nodes/%s' % node_b, {'status': 'online'})
    # ---- part 2: transfer UPLOADING / DELIVERING badges via real transfer + short freezes
    src = ch_a.root + '/share/xfer-big.bin'
    dst = ch_b.root + '/incoming/xfer-big.bin'
    out['write_big'] = ch_a.remote_py(
        "import json,os\np=%(p)r\nos.makedirs(os.path.dirname(p),mode=0o700,exist_ok=True)\n"
        "with open(p,'wb') as f:\n"
        "    b=b'Q'*1048576\n"
        "    for _ in range(600): f.write(b)\n"
        "print(json.dumps({'size':os.path.getsize(p)}))\n" % {'p': src})
    code, tr = hub.post('/api/transfers', {'source_node_id': A.ST['children']['a']['node_id'],
                                           'source_path': src,
                                           'targets': [{'node_id': node_b, 'destination_path': dst}]})
    out['create_http'] = code
    if code != 200:
        raise RuntimeError('big transfer create failed %s %s' % (code, tr))
    tid = tr['id']
    A.ST['ids']['transfer_big'] = tid
    A.add_fixture('transfer', tid, 'badge big a->b')
    out['transfer_id'] = tid
    # wait for UPLOADING then freeze the source
    deadline = time.time() + 30
    st = None
    while time.time() < deadline:
        st = transfer_status(tid)
        if st and st.get('status') == 'UPLOADING':
            break
        time.sleep(0.15)
    out['uploading_seen'] = {'status': (st or {}).get('status'), 'freeze': child_signal(ch_a, 'STOP')}
    BR.open(HUB + '/transfers')
    time.sleep(3)
    out['uploading_row'] = BR.evaljs(find_transfer_row_js('xfer-big.bin'))
    out['uploading_badge'] = {'zh': '上传中' in (out['uploading_row'] or {}).get('text', ''),
                              'en_absent': 'UPLOADING' not in (out['uploading_row'] or {}).get('text', '')}
    out['screenshot_uploading'] = BR.screenshot('web-transfer-uploading.png')
    out['resume_a'] = child_signal(ch_a, 'CONT')
    # wait for DELIVERING then freeze the target
    deadline = time.time() + 60
    st = None
    while time.time() < deadline:
        st = transfer_status(tid)
        tgt = ((st or {}).get('targets') or [{}])[0]
        if st and st.get('status') == 'DELIVERING' and tgt.get('status') == 'DELIVERING':
            break
        time.sleep(0.2)
    out['delivering_seen'] = {'status': (st or {}).get('status'),
                              'target': ((st or {}).get('targets') or [{}])[0].get('status'),
                              'freeze': child_signal(ch_b, 'STOP')}
    BR.open(HUB + '/transfers')
    time.sleep(3)
    out['delivering_row'] = BR.evaljs(find_transfer_row_js('xfer-big.bin'))
    out['delivering_badge'] = {'zh': '投递中' in (out['delivering_row'] or {}).get('text', ''),
                               'en_absent': 'DELIVERING' not in (out['delivering_row'] or {}).get('text', '')}
    out['screenshot_delivering'] = BR.screenshot('web-transfer-delivering.png')
    out['resume_b'] = child_signal(ch_b, 'CONT')
    # wait terminal and check success badge
    deadline = time.time() + 90
    final = None
    while time.time() < deadline:
        final = transfer_status(tid)
        if final and final.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'):
            break
        time.sleep(1)
    out['final'] = {'status': (final or {}).get('status'), 'error': (final or {}).get('error')}
    BR.open(HUB + '/transfers')
    time.sleep(3)
    out['final_row'] = BR.evaljs(find_transfer_row_js('xfer-big.bin'))
    out['success_badge'] = '成功' in (out['final_row'] or {}).get('text', '')
    nb = out['node_badges']
    tb = out['uploading_badge']
    db = out['delivering_badge']
    ok17 = (nb['maintenance_zh'] and nb['maintenance_en_absent'] and nb['disabled_zh'] and nb['disabled_en_absent']
            and out['uploading_seen']['status'] == 'UPLOADING' and tb['zh'] and tb['en_absent']
            and out['delivering_seen']['status'] == 'DELIVERING' and db['zh'] and db['en_absent']
            and out['final']['status'] == 'SUCCESS' and out['success_badge'])
    A.record('OBS-20261014-TRANSFER-STATUS-BADGE-NOT-LOCALIZED', 'web', 'FIXED' if ok17 else 'STILL_BROKEN',
             '真实传输冻结观测：UPLOADING->上传中、DELIVERING->投递中、终态->成功（均无英文枚举）' if ok17
             else '仍复现：%s' % {'up': out['uploading_badge'], 'del': out['delivering_badge'],
                                 'seen': (out['uploading_seen']['status'], out['delivering_seen']),
                                 'final': out['final'], 'success': out['success_badge']},
             ['evidence/web-badges.json'])
    A.record('OBS-20261014-STATUSBADGE-MAINTENANCE-DISABLED-NO-ZH', 'web', 'FIXED' if (nb['maintenance_zh'] and nb['disabled_zh']) else 'STILL_BROKEN',
             '节点列表 maintenance->维护、disabled->已禁用' if (nb['maintenance_zh'] and nb['disabled_zh'])
             else '仍复现：%s' % out['node_rows'], ['evidence/web-badges.json'])
    A.ev_write('web-badges.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 18
def case_invalid_task():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261017-INVALID-TASK-DEEPLINK-NO-ERROR']}
    BR.open(HUB + '/tasks/00000000-0000-4000-8000-0000000000ee/edit')
    time.sleep(3)
    out['state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;const a=document.querySelector('[role=\"alert\"]');"
        "return {hasNotFound:t.includes('任务不存在'),hasAlert:!!a,alertText:a?a.innerText.slice(0,140):null,"
        "hasBackLink:!!Array.from(document.querySelectorAll('a')).find(x=>x.innerText.includes('返回'))};})()")
    out['screenshot'] = BR.screenshot('web-invalid-task.png')
    ok18 = out['state']['hasNotFound'] and out['state']['hasAlert'] and out['state']['hasBackLink']
    A.record('OBS-20261017-INVALID-TASK-DEEPLINK-NO-ERROR', 'web', 'FIXED' if ok18 else 'STILL_BROKEN',
             '无效 task 深链 -> 明确「任务不存在或已被删除」+ 返回入口' if ok18
             else '仍复现：%s' % out['state'], ['evidence/web-invalid-task.json'])
    A.ev_write('web-invalid-task.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 19
def case_pagesize():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261014-PAGESIZE-PERSISTED-OUT-OF-RANGE-BLANK']}
    BR.open(HUB + '/agents')
    time.sleep(2.5)
    out['set'] = BR.evaljs("(function(){localStorage.setItem('nodesteer.table.nodes', JSON.stringify({pageSize: 999}));return localStorage.getItem('nodesteer.table.nodes');})()")
    BR.open(HUB + '/agents')
    time.sleep(3.5)
    out['pagination'] = BR.evaljs(
        "(function(){const t=document.body.innerText;const spans=Array.from(document.querySelectorAll('[data-slot=\"select-value\"]'));"
        "const texts=spans.map(s=>s.innerText.trim());"
        "const rows=document.querySelectorAll('table tbody tr').length;"
        "return {selectValues:texts,hasBlank:texts.some(x=>x===''),rowCount:rows,"
        "hasPageSizeLabel:t.includes('每页行数')};})()")
    out['screenshot'] = BR.screenshot('web-pagesize-999.png')
    out['cleanup'] = BR.evaljs("(function(){localStorage.removeItem('nodesteer.table.nodes');return 'cleared';})()")
    ok19 = (not out['pagination']['hasBlank'] and out['pagination']['rowCount'] > 0
            and any(re.fullmatch(r'\d+', v or '') for v in out['pagination']['selectValues']))
    A.record('OBS-20261014-PAGESIZE-PERSISTED-OUT-OF-RANGE-BLANK', 'web', 'FIXED' if ok19 else 'STILL_BROKEN',
             '持久化 pageSize=999 -> 下拉回退默认（select 值非空 %s）' % out['pagination']['selectValues'] if ok19
             else '仍复现：%s' % out['pagination'], ['evidence/web-pagesize.json'])
    A.ev_write('web-pagesize.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 20
def case_schedule_validation():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED',
                                           'OBS-20261014-PAST-ONETIME-SAVE-ENABLED-UI']}
    task_id = A.ST['ids']['task']
    sched = {'name': A.P + 'sched-interval', 'task_id': task_id, 'type': 'interval',
             'interval_sec': 300, 'timezone': 'UTC', 'enabled': True, 'execution_owner': 'agent',
             'offline_policy': 'hub_online_required', 'misfire_policy': 'skip'}
    code, s = hub.post('/api/schedules', sched)
    if code not in (200, 201):
        raise RuntimeError('interval_schedule_create_failed_%s_%s' % (code, s))
    A.ST['ids']['schedule_interval'] = s['id']
    A.add_fixture('schedule', s['id'], sched['name'])
    out['interval_schedule'] = {'id': s['id'], 'http': code}
    BR.open(HUB + '/schedules/' + s['id'])
    time.sleep(3)
    out['set_zero'] = BR.evaljs(
        "(function(){const inp=document.querySelector('input[type=\"number\"]');if(!inp)return 'missing';"
        "const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');d.set.call(inp,'0');"
        "inp.dispatchEvent(new Event('input',{bubbles:true}));return inp.value;})()")
    time.sleep(1.2)
    out['zero_state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;"
        "const alert=document.querySelector('[role=\"alert\"]');"
        "const save=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='保存');"
        "return {hasError:t.includes('间隔必须大于 0 秒'),alertText:alert?alert.innerText:null,"
        "saveDisabled:save?save.disabled:null};})()")
    out['screenshot_zero'] = BR.screenshot('web-schedule-interval-zero.png')
    out['set_negative'] = BR.evaljs(
        "(function(){const inp=document.querySelector('input[type=\"number\"]');if(!inp)return 'missing';"
        "const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');d.set.call(inp,'-1');"
        "inp.dispatchEvent(new Event('input',{bubbles:true}));return inp.value;})()")
    time.sleep(1.2)
    out['negative_state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;"
        "const save=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='保存');"
        "return {hasError:t.includes('间隔必须大于 0 秒'),saveDisabled:save?save.disabled:null};})()")
    # past one_time on the existing future schedule
    BR.open(HUB + '/schedules/' + A.ST['ids']['schedule'])
    time.sleep(3)
    out['set_past'] = BR.evaljs(
        "(function(){const inp=document.querySelector('input[type=\"datetime-local\"]');if(!inp)return 'missing';"
        "const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
        "const past=new Date(Date.now()-86400000);"
        "const pad=(n)=>String(n).padStart(2,'0');"
        "const v=past.getFullYear()+'-'+pad(past.getMonth()+1)+'-'+pad(past.getDate())+'T'+pad(past.getHours())+':'+pad(past.getMinutes());"
        "d.set.call(inp,v);inp.dispatchEvent(new Event('input',{bubbles:true}));return inp.value;})()")
    time.sleep(1.5)
    out['past_state'] = BR.evaljs(
        "(function(){const t=document.body.innerText;"
        "const save=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='保存');"
        "return {hasError:t.includes('运行时间必须晚于当前时间'),saveDisabled:save?save.disabled:null};})()")
    out['screenshot_past'] = BR.screenshot('web-schedule-past.png')
    z, n, p = out['zero_state'], out['negative_state'], out['past_state']
    ok20 = (z['hasError'] and z['saveDisabled'] is True and n['hasError'] and n['saveDisabled'] is True
            and p['hasError'] and p['saveDisabled'] is True)
    A.record('OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED', 'web', 'FIXED' if ok20 else 'STILL_BROKEN',
             'interval 0/-1 -> 内联错误「间隔必须大于 0 秒」+ 保存禁用；过去 run_at -> 内联错误 + 保存禁用' if ok20
             else '仍复现：%s' % {'zero': z, 'neg': n, 'past': p},
             ['evidence/web-schedule-validation.json'])
    A.record('OBS-20261014-PAST-ONETIME-SAVE-ENABLED-UI', 'web', 'FIXED' if (p['hasError'] and p['saveDisabled'] is True) else 'STILL_BROKEN',
             '过去时间 one_time 内联错误 + 保存禁用' if (p['hasError'] and p['saveDisabled'] is True)
             else '仍复现：%s' % p, ['evidence/web-schedule-validation.json'])
    A.ev_write('web-schedule-validation.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 21
def case_role_sidebar():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261014-NAV-ROLE-UNFILTERED'], 'roles': {}}
    probes = [('admin', hub.cred['admin_username'], hub.cred['admin_password']),
              ('operator', A.P + 'role-operator', 'qa-final-pass-123'),
              ('viewer', A.P + 'role-viewer', 'qa-final-pass-123')]
    for role, uname, pw in probes:
        switch_user(uname, pw)
        time.sleep(2)
        BR.open(HUB + '/')
        time.sleep(3)
        rec = BR.evaljs(
            "(function(){const a=document.querySelector('a[href=\"/users\"]');"
            "return {hasUsersEntry:!!a,usersText:a?a.innerText.trim():null,"
            "hasSettings:!!document.querySelector('a[href=\"/settings\"]'),path:location.pathname};})()")
        out['roles'][role] = rec
    out['screenshot_operator'] = None
    ok21 = (out['roles']['admin']['hasUsersEntry'] and out['roles']['admin']['hasSettings']
            and not out['roles']['operator']['hasUsersEntry'] and out['roles']['operator']['hasSettings']
            and not out['roles']['viewer']['hasUsersEntry'] and out['roles']['viewer']['hasSettings'])
    A.record('OBS-20261014-NAV-ROLE-UNFILTERED', 'web', 'FIXED' if ok21 else 'STILL_BROKEN',
             'operator/viewer 侧边栏无「用户」入口；admin 保留；设置入口三者都在' if ok21
             else '仍复现：%s' % out['roles'], ['evidence/web-role-sidebar.json'])
    # switch to operator for a screenshot then back to admin
    switch_user(A.P + 'role-operator', 'qa-final-pass-123')
    BR.open(HUB + '/')
    time.sleep(3)
    out['screenshot_operator'] = BR.screenshot('web-role-operator.png')
    switch_user(hub.cred['admin_username'], hub.cred['admin_password'])
    A.ev_write('web-role-sidebar.json', out)
    A.dump_results()
    return out


def main():
    hub.login()
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    cases = {'session': case_session_and_logout, 'tasks': case_tasks_states,
             'dashboard': case_dashboard_fault, 'appcard': case_app_state_card,
             'payload': case_task_payload, 'condition': case_condition_summary,
             'crosspage': case_crosspage, 'badges': case_badges,
             'invalid': case_invalid_task, 'pagesize': case_pagesize,
             'schedule': case_schedule_validation, 'roles': case_role_sidebar}
    order = ['session', 'tasks', 'dashboard', 'appcard', 'payload', 'condition',
             'crosspage', 'badges', 'invalid', 'pagesize', 'schedule', 'roles']
    if which == 'all':
        for name in order:
            print('==== web case', name, flush=True)
            cases[name]()
    else:
        cases[which]()


if __name__ == '__main__':
    main()
