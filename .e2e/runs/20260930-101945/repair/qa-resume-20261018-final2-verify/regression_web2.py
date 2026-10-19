#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: Web-side checks (items 13 + 16 sampling) on the deployed final2 build.

Real browser (agent-browser) against the real UI; owned fixtures only.
  - group 13: /transfers FAILED row must not show a Cancel button
  - item 16 sampling (previous 22 items): select drift (pageSize=999),
    four-editor pending (script editor real double-click), Tasks three states,
    AND condition summary, /api/me session keys + logout revocation.
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
import regression_go2 as A  # noqa: E402

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

    def url(self):
        return self.evaljs('location.href')

    def screenshot(self, name):
        path = str(EV / name)
        rc, out, err = self.run('screenshot', '--full', path, timeout=150, check=False)
        return {'rc': rc, 'path': 'evidence/' + name}


BR = Browser('final2-' + A.NONCE)


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


def click_nav(href, tag=None):
    return click_el("Array.from(document.querySelectorAll('a')).find(a=>a.getAttribute('href')===%s)" % json.dumps(href),
                    tag or ('nav-' + href.strip('/').replace('/', '-')))


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
    if (window.__fx.mode === 'tasks-hold' && isTasksList) {
      return new Promise(function(resolve){ window.__fx.releases.push(function(){ resolve(new Response('[]', {status:200, headers:{'Content-Type':'application/json'}})); }); });
    }
    if (window.__fx.mode === 'tasks-empty' && isTasksList) {
      return Promise.resolve(new Response('[]', {status:200, headers:{'Content-Type':'application/json'}}));
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


# ---------------------------------------------------------------- item 16: session + logout
def case_session_and_logout():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261015-API-CONTRACT-ME-KEYS-UI',
                                           'OBS-20261010-UI-LOGOUT-NOT-REVOKED']}
    login(hub.cred['admin_username'], hub.cred['admin_password'])
    token = BR.evaljs('localStorage.getItem("nodesteer_token")')
    out['token_captured'] = bool(token)
    out['me_before'] = BR.evaljs(
        "(async()=>{const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+localStorage.getItem('nodesteer_token')}});"
        "const b=await r.json();return {status:r.status,keys:Object.keys(b).sort(),user_id:b.user_id,username:b.username,role:b.role};})()")
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


# ---------------------------------------------------------------- item 16: tasks three states
def case_tasks_states():
    out = {'at_utc': A.now_utc(), 'case': ['G03-10', 'TABLE-TASKS-ERROR']}
    login(hub.cred['admin_username'], hub.cred['admin_password'])
    BR.open(HUB + '/agents')
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
    A.ev_write('web-tasks-states.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 16: AND condition summary
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


# ---------------------------------------------------------------- item 16: select drift (pageSize=999)
def case_pagesize():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261014-PAGESIZE-PERSISTED-OUT-OF-RANGE-BLANK']}
    login(hub.cred['admin_username'], hub.cred['admin_password'])
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


# ---------------------------------------------------------------- group 13: transfers cancel button
def case_transfer_cancel_buttons():
    out = {'at_utc': A.now_utc(), 'case': ['P16-UI-FAILED-CANCEL-BUTTON']}
    ev = json.loads((EV / 'phase08-g8-terminal.json').read_text())
    ok_id = ev['success_transfer']['id']
    bad_id = ev['failed_transfer']['id']
    src_ok = ev['success_transfer']['id']
    # file names carry the nonce; find them from the API rows for robust search
    c, transfers = hub.get('/api/transfers')
    rows = transfers if isinstance(transfers, list) else (transfers or {}).get('transfers') or []
    row_ok = next((t for t in rows if t.get('id') == ok_id), None)
    row_bad = next((t for t in rows if t.get('id') == bad_id), None)
    needle_ok = (row_ok or {}).get('source_path', '').rsplit('/', 1)[-1]
    needle_bad = (row_bad or {}).get('source_path', '').rsplit('/', 1)[-1]
    login(hub.cred['admin_username'], hub.cred['admin_password'])
    BR.open(HUB + '/transfers')
    time.sleep(3)

    def search_and_row(needle):
        BR.evaljs(
            "(function(){const inp=Array.from(document.querySelectorAll('input')).find(i=>(i.placeholder||'').includes('搜索'));"
            "if(!inp)return 'no';const d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
            "d.set.call(inp,%s);inp.dispatchEvent(new Event('input',{bubbles:true}));return inp.value;})()"
            % json.dumps(needle))
        time.sleep(2.5)
        return BR.evaljs(
            "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "const r=rows.find(r=>r.innerText.includes(%s));"
            "if(!r)return null;const btns=Array.from(r.querySelectorAll('button')).map(b=>b.innerText.trim());"
            "return {text:r.innerText.replace(/\\n+/g,' | ').slice(0,240),buttons:btns};})()"
            % json.dumps(needle))

    out['needles'] = {'failed': needle_bad, 'success': needle_ok}
    out['failed_row'] = search_and_row(needle_bad)
    out['screenshot_failed'] = BR.screenshot('web-transfer-failed-row.png')
    out['success_row'] = search_and_row(needle_ok)
    fr = out['failed_row'] or {}
    sr = out['success_row'] or {}
    ok = (fr and '取消' not in (fr.get('buttons') or [])
          and sr and '取消' not in (sr.get('buttons') or []))
    A.record('P16-UI-FAILED-CANCEL-BUTTON', 'web', 'FIXED' if ok else 'STILL_BROKEN',
             'FAILED 行无「取消」按钮（按钮：%s）；SUCCESS 行亦无（按钮：%s）'
             % (fr.get('buttons'), sr.get('buttons')) if ok
             else '仍复现：failed=%s success=%s' % (fr, sr),
             ['evidence/web-transfer-buttons.json'], out)
    A.ev_write('web-transfer-buttons.json', out)
    A.dump_results()
    return out


# ---------------------------------------------------------------- item 16: editor pending (script editor)
def case_editor_pending():
    out = {'at_utc': A.now_utc(), 'case': ['OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED']}
    script_id = A.ST['ids']['script']
    login(hub.cred['admin_username'], hub.cred['admin_password'])
    BR.open(HUB + '/scripts/' + script_id)
    time.sleep(3)
    out['patch'] = BR.evaljs(
        "(function(){if(window.__ed&&window.__ed.orig){try{window.fetch=window.__ed.orig;}catch(e){}}"
        "const orig=window.fetch.bind(window);window.__ed={orig:orig,count:0,held:[],urls:[]};"
        "window.fetch=function(input,init){const url=(typeof input==='string')?input:(input&&input.url)||'';"
        "const m=((init&&init.method)||(input&&input.method)||'GET').toUpperCase();"
        "if(m==='PUT'&&url.indexOf(%s)>=0){window.__ed.count++;window.__ed.urls.push(url);"
        "return new Promise(function(res){window.__ed.held.push(function(){res(orig(input,init));});});}"
        "return orig(input,init);};return 'patched';})()" % json.dumps('/api/scripts/' + script_id))
    time.sleep(0.5)
    out['double_click'] = BR.evaljs(
        "(function(){const btn=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='保存');"
        "if(!btn)return {found:false};btn.click();btn.click();"
        "return {found:true,disabled_immediate:btn.disabled,count:window.__ed.count};})()")
    time.sleep(1.2)
    out['after'] = BR.evaljs(
        "(function(){const btn=Array.from(document.querySelectorAll('button')).find(b=>b.innerText.trim()==='保存');"
        "return {disabled:btn?btn.disabled:null,ariaBusy:btn?btn.getAttribute('aria-busy'):null,"
        "count:window.__ed.count,urls:window.__ed.urls};})()")
    out['screenshot'] = BR.screenshot('web-editor-pending.png')
    out['release'] = BR.evaljs("(function(){const n=(window.__ed&&window.__ed.held||[]).length;"
                               "(window.__ed&&window.__ed.held||[]).forEach(f=>f());window.__ed.held=[];return n;})()")
    time.sleep(2)
    out['restore'] = BR.evaljs("(function(){if(window.__ed&&window.__ed.orig){window.fetch=window.__ed.orig;return 'restored';}return 'no';})()")
    ok = (out['double_click'].get('found') and out['after'].get('count') == 1
          and out['after'].get('disabled') is True)
    A.record('OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED', 'web', 'FIXED' if ok else 'STILL_BROKEN',
             '脚本编辑器保存请求在途：双击仅 1 次 PUT（count=%s）、按钮禁用（aria-busy=%s）'
             % (out['after'].get('count'), out['after'].get('ariaBusy')) if ok
             else '仍复现：%s' % {k: out[k] for k in ('double_click', 'after')},
             ['evidence/web-editor-pending.json'], out)
    A.ev_write('web-editor-pending.json', out)
    A.dump_results()
    return out


CASES = {'session': case_session_and_logout, 'tasks': case_tasks_states,
         'condition': case_condition_summary, 'pagesize': case_pagesize,
         'transfer-buttons': case_transfer_cancel_buttons, 'editor-pending': case_editor_pending}


def main():
    hub.login()
    name = sys.argv[1] if len(sys.argv) > 1 else 'all'
    order = ['session', 'pagesize', 'tasks', 'condition', 'transfer-buttons', 'editor-pending']
    if name == 'all':
        for n in order:
            print('==== web case', n, flush=True)
            CASES[n]()
    else:
        CASES[name]()
    A.dump_results()


if __name__ == '__main__':
    main()
