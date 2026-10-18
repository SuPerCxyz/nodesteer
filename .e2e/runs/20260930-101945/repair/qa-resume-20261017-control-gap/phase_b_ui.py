#!/usr/bin/env python3
"""Phase B: control-level gap re-tests (qa-resume-20261017-control-gap).

All tests are read-only or cancel-path; the only writes are the owned-schedule
one_time save (edit path) and the X06 inline upload of an owned artifact2.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import cgap_lib as L  # noqa: E402

SETUP = L.load('phase-a-setup.json')
TASK = SETUP['task']['id']
SCRIPT = SETUP['script']['id']
GROUP = SETUP['group']['id']
APP = SETUP['application']['id']
ART1 = SETUP['artifact']['id']
NATIVE1 = SETUP['native1_id']
NATIVE1_HOST = next(n['hostname'] for n in SETUP['nodes'] if n['id'] == NATIVE1)

hub = L.Hub()
hub.login()

RESULTS = L.load('phase-b-results.json') or {'phase': 'B', 'nonce': L.NONCE, 'owner': L.OWNER, 'tests': {}}
RESULTS.setdefault('tests', {})
RESULTS['nonce'] = L.NONCE
RESULTS['owner'] = L.OWNER
ORDER = list(RESULTS['tests'].keys())


def record(tid, status, payload):
    payload['status'] = status
    payload['at_utc'] = datetime.now(timezone.utc).isoformat()
    RESULTS['tests'][tid] = payload
    ORDER.append(tid)
    L.save('progress.json', {
        'batch': 'qa-resume-20261017-control-gap', 'phase': 'B',
        'order': ORDER,
        'summary': {k: v['status'] for k, v in RESULTS['tests'].items()},
    })
    print('[%s] %s' % (status, tid), flush=True)


def js(code, timeout=120):
    return L.ab_js(code, timeout=timeout)


def click_role(role, name, exact=False):
    args = ['find', 'role', role, 'click', '--name', name]
    if exact:
        args.append('--exact')
    out, err, rc = L.ab(*args)
    return {'out': (out or '').strip()[:100], 'rc': rc, 'err': (err or '').strip()[:100]}


def set_input_by_js(find_js, value):
    code = ("(() => { const el = %s; if (!el) return {err:'not-found'}; "
            "const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; "
            "const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; "
            "setter.call(el, %s); el.dispatchEvent(new Event('input', {bubbles:true})); "
            "el.dispatchEvent(new Event('change', {bubbles:true})); return {ok:true, value: el.value}; })()"
            % (find_js, json.dumps(value)))
    return js(code)


def net_entries(requests):
    return [{k: r.get(k) for k in ('method', 'url', 'status', 'requestId') if r.get(k) is not None} for r in requests]


def non_get(requests):
    return [r for r in requests if (r.get('method') or '').upper() != 'GET']


def wait_url_contains(part, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        u = L.ab_url()
        if part in u:
            return u
        time.sleep(0.4)
    return L.ab_url()


def wait_url_ends(part, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        u = L.ab_url()
        if u.rstrip('/').endswith(part.rstrip('/')):
            return u
        time.sleep(0.4)
    return L.ab_url()


def wait_for_js(expr, timeout=8):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = js("(() => %s)()" % expr)
        except Exception as exc:
            last = {'_error': str(exc)}
        if last is True or (isinstance(last, dict) and last.get('ok')):
            return last
        time.sleep(0.4)
    return last


def read_exec_rows():
    return js("""(() => {
      const headers = [...document.querySelectorAll('table thead th')].map(th => th.textContent.trim());
      const idx = headers.indexOf('触发方式');
      const rows = [...document.querySelectorAll('table tbody tr')].map(tr => {
        const tds = [...tr.querySelectorAll('td')].map(td => td.textContent.trim());
        return {trigger: idx >= 0 ? tds[idx] : null, task: tds[1] || null};
      });
      return {headers, triggerIndex: idx, rows};
    })()""")


# ------------------------------------------------------------------ B1 trigger filter
def test_b1():
    L.ab_open('/executions', wait=3.2)
    L.ab_net(clear=True)
    initial = js("""(() => {
      const sel = [...document.querySelectorAll('select')].find(s => s.getAttribute('aria-label') === '触发方式');
      return sel ? {options: [...sel.options].map(o => ({v: o.value, t: o.textContent.trim()})), value: sel.value} : null;
    })()""")
    rows0 = read_exec_rows()
    L.ab_shot('b1-executions-initial.png')
    results = {}
    for value, label in (('manual', '手动'), ('schedule', '调度'), ('system', '系统'), ('', '全部')):
        L.ab_net(clear=True)
        setres = js("""(() => {
          const sel = [...document.querySelectorAll('select')].find(s => s.getAttribute('aria-label') === '触发方式');
          if (!sel) return {err: 'select-not-found'};
          const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
          setter.call(sel, %s);
          sel.dispatchEvent(new Event('change', {bubbles: true}));
          return {ok: true, value: sel.value};
        })()""" % json.dumps(value))
        time.sleep(2.0)
        url = L.ab_url()
        reqs = L.ab_net('api/executions')
        rows = read_exec_rows()
        body = js("(() => document.body.innerText.slice(0, 800))()")
        api_probe = None
        if value:
            c, b, h = hub.get('/api/executions?limit=100&trigger_type=' + value)
            items = b if isinstance(b, list) else b.get('items', [])
            api_probe = {'code': c, 'x_total_count': h.get('X-Total-Count'), 'sample_trigger_types': sorted({x.get('trigger_type') for x in items})}
        results[label] = {
            'select_set': setres, 'url': url, 'requests': net_entries(reqs),
            'row_count': len(rows.get('rows') or []), 'row_triggers': sorted({r['trigger'] for r in (rows.get('rows') or [])}),
            'api_probe': api_probe,
            'empty_state': ('暂无数据' in body or 'No data' in body) if value == 'system' else None,
            'body_head': body[:200] if value == 'system' else None,
        }
        L.ab_shot('b1-executions-trigger-%s.png' % (value or 'all'))
    # verdict
    ok = True
    detail = []
    for label, expect in (('手动', '手动'), ('调度', '调度')):
        r = results[label]
        req = [x for x in r['requests'] if (x.get('method') or '').upper() == 'GET' and 'trigger_type=' in (x.get('url') or '')]
        good = bool(req) and r['row_triggers'] == [expect] and ('trigger=' in r['url'])
        ok = ok and good
        detail.append({'label': label, 'request_with_trigger_type': bool(req), 'rows_all_match': r['row_triggers'] == [expect], 'url_has_param': 'trigger=' in r['url']})
    sysr = results['系统']
    sys_req = [x for x in sysr['requests'] if 'trigger_type=system' in (x.get('url') or '')]
    allr = results['全部']
    record('B1-executions-trigger-filter', 'COVERED' if ok else 'PARTIAL', {
        'case_ids': ['P17P18-04'], 'page': '/executions', 'control': '触发方式 过滤器',
        'initial': initial, 'initial_rows': rows0,
        'per_filter': results,
        'verdict_detail': detail,
        'system_filter': {'request_with_trigger_type_system': bool(sys_req), 'rows': sysr['row_count'], 'empty_state': sysr['empty_state'], 'api_count': (sysr.get('api_probe') or {}).get('x_total_count')},
        'all_cleared': {'url': allr['url'], 'trigger_param_absent': 'trigger=' not in allr['url']},
    })


# ------------------------------------------------------------------ B2 timezone
def test_b2():
    ev = {'case_ids': ['P12P13-05', 'X10'], 'page': '/schedules/new + /schedules/{id}', 'control': '时区'}
    browser_tz = js("Intl.DateTimeFormat().resolvedOptions().timeZone")
    ev['browser_timezone'] = browser_tz

    # --- create page: preset + default tz + empty run_at validation ---
    L.ab_open('/schedules/new?task_id=' + TASK, wait=3.2)
    ev['create_page'] = js("""(() => {
      const combos = [...document.querySelectorAll('[role=combobox]')].map(c => c.textContent.trim().slice(0, 40));
      const tz = [...document.querySelectorAll('input')].filter(i => i.type === 'text').map(i => i.value);
      const preset = document.body.innerText.includes('由任务详情创建，任务已锁定。');
      const taskCombo = document.querySelectorAll('[role=combobox]')[0];
      return {combos, timezone_values: tz, preset_banner: preset,
              task_combo_disabled: taskCombo ? taskCombo.getAttribute('data-disabled') !== null : null};
    })()""")
    # switch type -> one_time
    ev['type_click'] = click_role('combobox', '类型')
    time.sleep(0.7)
    ev['one_time_click'] = click_role('option', '一次性')
    time.sleep(1.0)
    ev['after_one_time'] = js("""(() => ({
      combos: [...document.querySelectorAll('[role=combobox]')].map(c => c.textContent.trim().slice(0, 16)),
      has_run_at: !!document.querySelector('input[type=datetime-local]'),
      run_at_value: (document.querySelector('input[type=datetime-local]') || {}).value || null,
      tz_value: [...document.querySelectorAll('input')].filter(i => i.type === 'text').map(i => i.value)[0] || null,
    }))()""")
    # empty run_at -> save -> expect inline error + zero POST
    L.ab_net(clear=True)
    ev['empty_save_click'] = click_role('button', '保存')
    time.sleep(1.4)
    ev['empty_run_at'] = {
        'error_shown': js("document.body.innerText.includes('请选择运行时间')"),
        'url': L.ab_url(),
        'non_get_requests': net_entries(non_get(L.ab_net('api/schedules'))),
    }
    # fill run_at + verify preview conversion text (no save on create page)
    ev['set_run_at'] = set_input_by_js("[...document.querySelectorAll('input')].find(i => i.type === 'datetime-local')", '2030-06-15T10:00')
    time.sleep(1.0)
    ev['preview'] = js("""(() => { const el = [...document.querySelectorAll('div')].find(d => d.textContent.includes('接下来 5 次触发')); return el ? el.innerText : null; })()""")
    ev['create_page_no_write'] = {'non_get': net_entries(non_get(L.ab_net('api/schedules')))}
    L.ab_shot('b2-schedules-new-timezone.png')

    # --- locate owned schedule ---
    c, b, _ = hub.get('/api/schedules')
    sched = next((s for s in (b or []) if s.get('task_id') == TASK), None)
    assert sched, 'owned schedule not found'
    ev['schedule_id'] = sched['id']
    ev['api_after_manual_create'] = {'run_at': sched.get('run_at'), 'timezone': sched.get('timezone'), 'type': sched.get('type')}

    def wall_of(iso, tzname):
        from zoneinfo import ZoneInfo
        d = datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(ZoneInfo(tzname))
        return d.strftime('%Y-%m-%dT%H:%M')

    expected_wall_before = wall_of(sched['run_at'], sched.get('timezone') or 'UTC')
    ev['expected_wall_before'] = expected_wall_before

    # --- edit page: round-trip display + real conversion on PUT ---
    L.ab_open('/schedules/' + sched['id'], wait=3.2)
    ev['edit_page_roundtrip'] = js("""(() => ({
      run_at_input: (document.querySelector('input[type=datetime-local]') || {}).value || null,
      tz_input: [...document.querySelectorAll('input')].filter(i => i.type === 'text').map(i => i.value).slice(-1)[0] || null,
      combos: [...document.querySelectorAll('[role=combobox]')].map(c => c.textContent.trim().slice(0, 16)),
    }))()""")
    L.ab_net(clear=True)
    ev['set_run_at_edit'] = set_input_by_js("[...document.querySelectorAll('input')].find(i => i.type === 'datetime-local')", '2030-06-16T10:00')
    time.sleep(0.8)
    ev['preview_edit'] = js("""(() => { const el = [...document.querySelectorAll('div')].find(d => d.textContent.includes('接下来 5 次触发')); return el ? el.innerText : null; })()""")
    ev['edit_save_click'] = click_role('button', '保存')
    wait_url_ends("/schedules", 12)
    time.sleep(1.0)
    ev['edit_save_net'] = net_entries(L.ab_net('api/schedules'))
    c, b2, _ = hub.get('/api/schedules/' + sched['id'])
    ev['api_after_edit_save'] = {'code': c, 'run_at': b2.get('run_at'), 'timezone': b2.get('timezone'), 'revision': b2.get('revision')}
    L.ab_open('/schedules/' + sched['id'], wait=3.0)
    ev['edit_reload_display'] = js("""(() => ({
      run_at_input: (document.querySelector('input[type=datetime-local]') || {}).value || null,
      tz_input: [...document.querySelectorAll('input')].filter(i => i.type === 'text').map(i => i.value)[0] || null,
    }))()""")

    # --- X10: invalid timezone -> on_start hides/resets; back to cron keeps valid value ---
    L.ab_net(clear=True)
    ev['set_invalid_tz'] = set_input_by_js("[...document.querySelectorAll('input')].find(i => i.type === 'text')", 'Not/AZone')
    time.sleep(0.8)
    ev['invalid_tz_preview'] = js("""(() => document.body.innerText.split(String.fromCharCode(10)).filter(t => /时区/.test(t)).join(' | '))()""")
    ev['on_start_click'] = click_role('combobox', '类型')
    time.sleep(0.7)
    ev['on_start_option'] = click_role('option', '节点启动时')
    time.sleep(1.2)
    ev['after_on_start'] = js("""(() => ({
      tz_inputs: [...document.querySelectorAll('input')].filter(i => i.type === 'text').map(i => i.value),
      combos: [...document.querySelectorAll('[role=combobox]')].map(c => c.textContent.trim().slice(0, 16)),
      on_start_note: document.body.innerText.includes('节点每次启动后触发一次，无固定触发时刻。'),
    }))()""")
    ev['cron_click'] = click_role('combobox', '类型')
    time.sleep(0.7)
    ev['cron_option'] = click_role('option', 'Cron')
    time.sleep(1.2)
    ev['after_back_to_cron'] = js("""(() => ({
      tz_inputs: [...document.querySelectorAll('input')].filter(i => i.type === 'text').map(i => i.value),
      combos: [...document.querySelectorAll('[role=combobox]')].map(c => c.textContent.trim().slice(0, 16)),
    }))()""")
    ev['invalid_tz_cycle_no_write'] = {'non_get': net_entries(non_get(L.ab_net('api/schedules')))}
    L.ab_shot('b2-schedules-edit-onstart-reset.png')

    # verdict
    conv_ok = (ev['api_after_edit_save'].get('run_at') == '2030-06-16T02:00:00Z'
               and ev['api_after_edit_save'].get('timezone') == 'Asia/Shanghai')
    roundtrip_ok = ev['edit_page_roundtrip'].get('run_at_input') == expected_wall_before
    reload_ok = ev['edit_reload_display'].get('run_at_input') == '2030-06-16T10:00'
    empty_ok = bool(ev['empty_run_at']['error_shown']) and not ev['empty_run_at']['non_get_requests']
    onstart_ok = ev['after_on_start']['tz_inputs'] == [] and (ev['after_back_to_cron']['tz_inputs'] or [None])[-1] == 'Asia/Shanghai'
    status = 'COVERED' if (conv_ok and roundtrip_ok and reload_ok and empty_ok and onstart_ok) else 'PARTIAL'
    ev['verdict'] = {'conversion_edit_put_utc_instant_ok': conv_ok, 'roundtrip_display_ok': roundtrip_ok,
                     'post_save_reload_display_ok': reload_ok,
                     'empty_run_at_blocked_no_write': empty_ok, 'invalid_tz_on_start_reset_ok': onstart_ok}
    record('B2-schedules-timezone', status, ev)


# ------------------------------------------------------------------ B3 skip to main
def test_b3():
    ev = {'case_ids': ['G04-04', 'X19'], 'page': '(global shell)', 'control': 'Skip to Main'}
    L.ab_open('/agents', wait=2.6)
    ev['dom'] = js("""(() => {
      const a = [...document.querySelectorAll('a')].find(x => (x.textContent || '').trim() === 'Skip to Main');
      return a ? {href: a.getAttribute('href'), class: a.className, content_target_exists: !!document.getElementById('content')} : null;
    })()""")
    tab_seq = []
    focused_link = False
    for _ in range(4):
        L.ab('press', 'Tab')
        time.sleep(0.35)
        cur = js("""(() => { const a = document.activeElement; return {tag: a.tagName, text: (a.textContent || '').trim().slice(0, 40), href: a.getAttribute && a.getAttribute('href')}; })()""")
        tab_seq.append(cur)
        if cur.get('text') == 'Skip to Main':
            focused_link = True
            break
    ev['tab_sequence'] = tab_seq
    ev['focus_visible_style'] = js("""(() => {
      const a = document.activeElement;
      const cs = getComputedStyle(a);
      return {transform: cs.transform, opacity: cs.opacity, rect: a.getBoundingClientRect().toJSON()};
    })()""")
    L.ab_shot('b3-skip-to-main-focused.png')
    pre = js("(() => ({hash: location.hash, y: window.scrollY, active: document.activeElement.tagName, active_text: (document.activeElement.textContent||'').trim().slice(0,30)}))()")
    if focused_link:
        L.ab('press', 'Enter')
        time.sleep(1.2)
    post = js("""(() => ({
      hash: location.hash, y: window.scrollY,
      active_tag: document.activeElement.tagName, active_text: (document.activeElement.textContent || '').trim().slice(0, 40),
      content_el_exists: !!document.getElementById('content'),
      main_rect: document.querySelector('main') ? document.querySelector('main').getBoundingClientRect().toJSON() : null,
    }))()""")
    L.ab('press', 'Tab')
    time.sleep(0.35)
    next_tab = js("""(() => { const a = document.activeElement; return {tag: a.tagName, text: (a.textContent || '').trim().slice(0, 40), href: a.getAttribute && a.getAttribute('href')}; })()""")
    ev['enter_behavior'] = {'pre': pre, 'post': post, 'next_tab_after_enter': next_tab, 'url': L.ab_url()}
    L.ab_shot('b3-skip-to-main-after-enter.png')
    reachable = focused_link
    activated = post.get('hash') == '#content'
    skip_effective = bool(post.get('content_el_exists')) and (post.get('y', 0) > 0 or post.get('active_tag') not in ('A', 'BODY'))
    ev['verdict'] = {'keyboard_reachable_first_tab': reachable,
                     'link_activated_hash_set': activated,
                     'anchor_target_exists': post.get('content_el_exists'),
                     'skip_effective_focus_or_scroll_moved_to_main': skip_effective,
                     'dead_anchor': activated and not skip_effective}
    status = 'COVERED' if (reachable and skip_effective) else ('DEFECT' if reachable and activated and not skip_effective else 'PARTIAL')
    record('B3-skip-to-main', status, ev)


# ------------------------------------------------------------------ B4 open logs
def test_b4():
    EID = 'c527873b-ba5e-452a-a600-3bfc2dab0366'
    ev = {'case_ids': ['P17P18-08'], 'page': '/executions/{id}', 'control': '打开日志', 'execution_id': EID}
    L.ab_open('/executions/' + EID, wait=3.4)
    ev['pre'] = js("""(() => {
      const el = document.getElementById('execution-logs');
      return {y: window.scrollY, doc_h: document.documentElement.scrollHeight, win_h: window.innerHeight,
              logs_exists: !!el, logs_top: el ? el.getBoundingClientRect().top : null,
              dialogs: document.querySelectorAll('[role=dialog],[role=alertdialog]').length, url: location.href};
    })()""")
    L.ab_shot('b4-execution-detail-before-openlogs.png')
    ev['click'] = click_role('button', '打开日志')
    time.sleep(1.8)
    ev['post'] = js("""(() => {
      const el = document.getElementById('execution-logs');
      return {y: window.scrollY, logs_top: el ? el.getBoundingClientRect().top : null,
              dialogs: document.querySelectorAll('[role=dialog],[role=alertdialog]').length,
              url: location.href, active: document.activeElement.tagName};
    })()""")
    L.ab_shot('b4-execution-detail-after-openlogs.png')
    pre, post = ev['pre'], ev['post']
    in_view = post.get('logs_top') is not None and 0 <= post['logs_top'] < pre.get('win_h', 0)
    scrolled = post.get('y', 0) > pre.get('y', 0)
    no_dialog = post.get('dialogs') == 0 and pre.get('dialogs') == 0
    same_url = post.get('url') == pre.get('url')
    ev['verdict'] = {'scrolled_down': scrolled, 'logs_section_in_viewport': in_view, 'no_dialog_opened': no_dialog, 'url_unchanged': same_url}
    status = 'COVERED' if (scrolled and in_view and no_dialog and same_url) else 'PARTIAL'
    record('B4-execution-open-logs', status, ev)


# ------------------------------------------------------------------ B5a script editor cancel
def test_b5_script():
    ev = {'case_ids': ['X08'], 'page': '/scripts/{id}', 'control': '取消（编辑脚本）'}
    c, before, _ = hub.get('/api/scripts/' + SCRIPT)
    c2, audit_before, _ = hub.get('/api/audit?q=' + L.PREFIX + 'script&limit=100')
    ev['api_before'] = {'code': c, 'revision': before.get('revision'), 'description': before.get('description'),
                        'audit_count': len(audit_before or [])}
    L.ab_open('/scripts/' + SCRIPT, wait=3.2)
    ev['mutate'] = set_input_by_js("[...document.querySelectorAll('input')].find(i => i.value === 'owned control-gap fixture (cancel edit test)')",
                                   'mutated-by-qa-cgap (must not persist)')
    time.sleep(0.6)
    L.ab_net(clear=True)
    ev['cancel_click'] = js("""(() => { const a = [...document.querySelectorAll('a')].find(x => x.textContent.trim() === '取消' && x.getAttribute('href') === '/scripts'); if (!a) return {err:'cancel-not-found'}; a.click(); return {ok:true}; })()""")
    url = wait_url_ends("/scripts", 12)
    time.sleep(1.0)
    ev['url_after'] = url
    ev['non_get_requests'] = net_entries(non_get(L.ab_net()))
    c, after, _ = hub.get('/api/scripts/' + SCRIPT)
    c2, audit_after, _ = hub.get('/api/audit?q=' + L.PREFIX + 'script&limit=100')
    ev['api_after'] = {'code': c, 'revision': after.get('revision'), 'description': after.get('description'),
                       'audit_count': len(audit_after or []), 'audit_ids_after': sorted(x.get('id') for x in (audit_after or []))}
    ok = (not ev['non_get_requests']) and after.get('revision') == before.get('revision') and after.get('description') == before.get('description') and len(audit_after or []) == len(audit_before or [])
    ev['verdict'] = {'zero_non_get_requests': not ev['non_get_requests'], 'revision_unchanged': after.get('revision') == before.get('revision'),
                     'description_unchanged': after.get('description') == before.get('description'), 'audit_unchanged': len(audit_after or []) == len(audit_before or [])}
    record('B5a-script-editor-cancel', 'COVERED' if ok else 'PARTIAL', ev)


# ------------------------------------------------------------------ B5b app editor cancel + inline upload
def test_b5_app():
    ev = {'case_ids': ['X06', 'X08'], 'page': '/applications/{id}', 'control': '取消 + 上传新发布包（内嵌）'}
    c, before, _ = hub.get('/api/applications/' + APP)
    c2, audit_before, _ = hub.get('/api/audit?q=' + L.PREFIX + 'app&limit=100')
    ev['api_before'] = {'code': c, 'artifact_id': before.get('artifact_id'), 'version': before.get('version'),
                        'revision': before.get('revision'), 'name': before.get('name'), 'audit_count': len(audit_before or [])}
    L.ab_open('/applications/' + APP, wait=3.4)
    ev['preset_before'] = js("""(() => {
      const combo = document.querySelectorAll('[role=combobox]')[0];
      const ver = [...document.querySelectorAll('input')].find(i => i.value === '1.0.0');
      return {artifact_combo: combo ? combo.textContent.trim() : null, version_input: ver ? ver.value : null};
    })()""")
    # (a) open inline upload form
    L.ab_net(clear=True)
    ev['open_upload'] = click_role('button', '上传新发布包')
    ev['form_wait'] = wait_for_js("!!document.querySelector('#app-artifact-file')", 10)
    time.sleep(0.3)
    ev['upload_form'] = js("""(() => ({
      file_input: !!document.querySelector('#app-artifact-file'),
      inputs: [...document.querySelectorAll('form input')].map(i => ({name: i.name, type: i.type, value: i.value})),
      buttons: [...document.querySelectorAll('form button')].map(b => b.textContent.trim()),
    }))()""")
    # (b) inner cancel -> collapse, zero POST, preset unchanged
    ev['inner_cancel_click'] = js("""(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === '取消'); if (!b) return {err:'inner-cancel-not-found'}; b.click(); return {ok:true}; })()""")
    time.sleep(0.9)
    ev['after_inner_cancel'] = js("""(() => {
      const c = document.querySelectorAll('[role=combobox]')[0];
      return {file_input_present: !!document.querySelector('#app-artifact-file'),
              artifact_combo: c ? c.textContent.trim() : null};
    })()""")
    ev['inner_cancel_net'] = {'non_get': net_entries(non_get(L.ab_net('api/artifacts')))}
    # (c) reopen, fill, submit without file -> native required failure, zero POST
    click_role('button', '上传新发布包')
    ev['reopen_wait'] = wait_for_js("!!document.querySelector('form input[name=name]')", 10)
    time.sleep(0.3)
    ev['fill_name'] = set_input_by_js("document.querySelector('form input[name=name]')", L.PREFIX + 'art2')
    ev['fill_version'] = set_input_by_js("document.querySelector('form input[name=version]')", '2.0.0')
    L.ab_net(clear=True)
    ev['submit_no_file_click'] = click_role('button', '上传')
    time.sleep(1.2)
    ev['native_required'] = {
        'file_input_validity_missing': js("(() => { const f = document.querySelector('#app-artifact-file'); return f ? f.validity.valueMissing : null; })()"),
        'non_get_requests': net_entries(non_get(L.ab_net('api/artifacts'))),
    }
    # (d) real inline upload of owned artifact2 -> auto-select, no app save
    art2_path = '/tmp/opencode/' + L.PREFIX + 'art2.bin'
    Path(art2_path).write_bytes(b'QA-CGAP-INLINE-UPLOAD-ARTIFACT2-1CDAD4FE\n' * 6)
    up_out, up_err, up_rc = L.ab('upload', '#app-artifact-file', art2_path)
    time.sleep(0.6)
    ev['file_uploaded'] = {'out': (up_out or '').strip()[:80], 'rc': up_rc,
                           'file_name_shown': js("document.body.innerText.includes('art2.bin')")}
    L.ab_net(clear=True)
    ev['submit_with_file_click'] = click_role('button', '上传')
    time.sleep(2.5)
    ev['upload_net'] = net_entries(L.ab_net('api/artifacts'))
    ev['after_upload'] = js("""(() => {
      const combo = document.querySelectorAll('[role=combobox]')[0];
      return {artifact_combo: combo ? combo.textContent.trim() : null,
              version_inputs: [...document.querySelectorAll('input')].map(i => i.value).filter(v => v === '2.0.0'),
              upload_form_present: !!document.querySelector('#app-artifact-file')};
    })()""")
    c, arts, _ = hub.get('/api/artifacts')
    art2 = next((a for a in (arts or []) if a.get('name') == L.PREFIX + 'art2' and a.get('version') == '2.0.0'), None)
    ev['api_art2'] = art2
    # app must NOT be saved yet
    c, mid, _ = hub.get('/api/applications/' + APP)
    ev['api_app_after_upload'] = {'code': c, 'artifact_id': mid.get('artifact_id'), 'version': mid.get('version'), 'revision': mid.get('revision')}
    # (e) mutate name then page cancel -> zero PUT, app unchanged
    ev['name_input_wait'] = wait_for_js("[...document.querySelectorAll('input')].some(i => i.value === 'qa-cgap-1cdad4fe-app')", 8)
    ev['mutate_name'] = set_input_by_js("[...document.querySelectorAll('input')].find(i => i.value === 'qa-cgap-1cdad4fe-app')", 'mutated-by-qa-cgap')
    time.sleep(0.5)
    L.ab_net(clear=True)
    ev['page_cancel_click'] = js("""(() => { const a = [...document.querySelectorAll('a')].find(x => x.textContent.trim() === '取消' && x.getAttribute('href') === '/applications'); if (!a) return {err:'page-cancel-not-found'}; a.click(); return {ok:true}; })()""")
    url = wait_url_ends("/applications", 12)
    time.sleep(1.0)
    ev['url_after_cancel'] = url
    ev['page_cancel_net'] = net_entries(non_get(L.ab_net()))
    c, after, _ = hub.get('/api/applications/' + APP)
    c2, audit_after, _ = hub.get('/api/audit?q=' + L.PREFIX + 'app&limit=100')
    ev['api_after'] = {'code': c, 'artifact_id': after.get('artifact_id'), 'version': after.get('version'),
                       'revision': after.get('revision'), 'name': after.get('name'), 'audit_count': len(audit_after or [])}
    auto_selected = bool(art2) and L.PREFIX + 'art2' in (ev['after_upload'].get('artifact_combo') or '') and bool(ev['after_upload'].get('version_inputs'))
    app_unsaved = after.get('artifact_id') == before.get('artifact_id') and after.get('version') == before.get('version') and after.get('revision') == before.get('revision')
    cancel_clean = (not ev['inner_cancel_net']['non_get']) and (not ev['page_cancel_net']) and (not ev['native_required']['non_get_requests'])
    upload_ok = any((r.get('method') == 'POST' and r.get('status') == 200 and (r.get('url') or '').endswith('/api/artifacts')) for r in ev['upload_net'])
    ev['verdict'] = {'inner_cancel_zero_post_and_preset_kept': not ev['inner_cancel_net']['non_get'] and not ev['after_inner_cancel']['file_input_present'],
                     'native_required_blocked': bool(ev['native_required']['file_input_validity_missing']) and not ev['native_required']['non_get_requests'],
                     'inline_upload_post_200': upload_ok, 'auto_select_new_artifact': auto_selected,
                     'app_not_saved_by_upload_or_cancel': app_unsaved, 'page_cancel_zero_write': not ev['page_cancel_net'],
                     'art2_created': bool(art2)}
    status = 'COVERED' if (upload_ok and auto_selected and app_unsaved and cancel_clean and art2) else 'PARTIAL'
    record('B5b-app-editor-inline-upload-cancel', status, ev)


# ------------------------------------------------------------------ B5c artifact editor cancel
def test_b5_artifact():
    ev = {'case_ids': ['X06'], 'page': '/artifacts/new', 'control': '取消（上传发布包）'}
    L.ab_open('/artifacts/new', wait=3.0)
    ev['fill_name'] = set_input_by_js("document.querySelector('input[name=name]')", L.PREFIX + 'art3')
    ev['fill_version'] = set_input_by_js("document.querySelector('input[name=version]')", '9.9.9')
    art3_path = '/tmp/opencode/' + L.PREFIX + 'art3.bin'
    Path(art3_path).write_bytes(b'QA-CGAP-ARTIFACT3-CANCEL-1CDAD4FE\n' * 4)
    up_out, up_err, up_rc = L.ab('upload', '#artifact-file', art3_path)
    time.sleep(0.5)
    ev['file_selected'] = {'out': (up_out or '').strip()[:80], 'rc': up_rc}
    L.ab_net(clear=True)
    ev['cancel_click'] = js("""(() => { const a = [...document.querySelectorAll('a')].find(x => x.textContent.trim() === '取消' && x.getAttribute('href') === '/artifacts'); if (!a) return {err:'cancel-not-found'}; a.click(); return {ok:true}; })()""")
    url = wait_url_ends("/artifacts", 12)
    time.sleep(1.0)
    ev['url_after'] = url
    ev['non_get_requests'] = net_entries(non_get(L.ab_net()))
    c, arts, _ = hub.get('/api/artifacts')
    art3 = [a for a in (arts or []) if a.get('name') == L.PREFIX + 'art3']
    ev['art3_exists'] = bool(art3)
    ok = (not ev['non_get_requests']) and not art3
    ev['verdict'] = {'zero_write_requests': not ev['non_get_requests'], 'no_artifact_created': not art3}
    record('B5c-artifact-editor-cancel', 'COVERED' if ok else 'PARTIAL', ev)


# ------------------------------------------------------------------ B6 tasks edit/run pages
def test_b6():
    ev = {'case_ids': ['ROUTE-16', 'ROUTE-17', 'P11-01', 'X09'], 'page': '/tasks/{id}/edit + /tasks/{id}/run', 'control': '编辑任务/立即运行页面（owned Task 非空态）'}
    L.ab_open('/tasks/' + TASK + '/edit', wait=3.4)
    ev['edit_page'] = js("""(() => {
      const name = [...document.querySelectorAll('input')].find(i => i.value === 'qa-cgap-1cdad4fe-task');
      const error = document.body.innerText.includes('任务不存在') || document.body.innerText.includes('已被删除');
      return {url: location.href, name_input_found: !!name,
              error_state: error,
              combos: [...document.querySelectorAll('[role=combobox]')].map(c => c.textContent.trim().slice(0, 20)),
              buttons: [...document.querySelectorAll('button')].map(b => b.textContent.trim()).filter(Boolean).slice(0, 12),
              cancel_links: [...document.querySelectorAll('a')].filter(a => a.textContent.trim() === '取消').map(a => a.getAttribute('href')),
              body_head: document.body.innerText.slice(0, 150)};
    })()""")
    L.ab_shot('b6-task-edit-page.png')
    L.ab_open('/tasks/' + TASK + '/run', wait=3.2)
    c, execs_before, h = hub.get('/api/executions?limit=5&task_id=' + TASK)
    ev['run_page'] = js("""(() => {
      const inputs = [...document.querySelectorAll('input')].map(i => ({type: i.type, value: i.value}));
      const labels = [...document.querySelectorAll('label')].map(l => l.textContent.trim().slice(0, 30));
      const error = document.body.innerText.includes('任务不存在') || document.body.innerText.includes('已被删除');
      return {url: location.href, error_state: error, inputs, labels,
              buttons: [...document.querySelectorAll('button')].map(b => b.textContent.trim()).filter(Boolean),
              cancel_links: [...document.querySelectorAll('a')].filter(a => a.textContent.trim() === '取消').map(a => a.getAttribute('href'))};
    })()""")
    ev['executions_before'] = {'code': c, 'x_total_count': h.get('X-Total-Count'), 'count': len(execs_before or [])}
    L.ab_shot('b6-task-run-page.png')
    L.ab_net(clear=True)
    ev['open_dialog_click'] = click_role('button', '立即运行')
    time.sleep(1.0)
    ev['dialog'] = js("""(() => {
      const d = document.querySelector('[role=alertdialog],[role=dialog]');
      return d ? {present: true, text: d.innerText.slice(0, 200),
                  buttons: [...d.querySelectorAll('button')].map(b => b.textContent.trim())} : {present: false};
    })()""")
    L.ab_shot('b6-task-run-confirm-dialog.png')
    ev['dialog_cancel_click'] = js("""(() => {
      const d = document.querySelector('[role=alertdialog],[role=dialog]');
      if (!d) return {err: 'dialog-gone'};
      const b = [...d.querySelectorAll('button')].find(x => x.textContent.trim() === '取消');
      if (!b) return {err: 'cancel-not-found'};
      b.click(); return {ok: true};
    })()""")
    time.sleep(1.0)
    ev['dialog_after_cancel'] = js("(() => ({present: !!document.querySelector('[role=alertdialog],[role=dialog]')}))()")
    ev['run_net'] = net_entries(L.ab_net('api/tasks'))
    c, execs_after, h2 = hub.get('/api/executions?limit=5&task_id=' + TASK)
    ev['executions_after'] = {'code': c, 'x_total_count': h2.get('X-Total-Count'), 'count': len(execs_after or [])}
    edit_ok = ev['edit_page']['name_input_found'] and not ev['edit_page']['error_state']
    run_ok = (not ev['run_page']['error_state']) and bool(ev['run_page']['inputs']) and ev['dialog'].get('present') and ev['dialog_after_cancel']['present'] is False
    zero_run_post = not [r for r in ev['run_net'] if (r.get('method') or '').upper() != 'GET' and '/run' in (r.get('url') or '')]
    no_new_exec = ev['executions_after']['x_total_count'] == ev['executions_before']['x_total_count']
    ev['verdict'] = {'edit_page_renders_owned_task': edit_ok, 'run_page_renders_owned_task': run_ok,
                     'dialog_cancel_zero_run_post': zero_run_post, 'no_new_execution': no_new_exec}
    status = 'COVERED' if (edit_ok and run_ok and zero_run_post and no_new_exec) else 'PARTIAL'
    record('B6-task-edit-run-pages', status, ev)


# ------------------------------------------------------------------ B7 group detail page
def test_b7():
    ev = {'case_ids': ['ROUTE-09', 'P06-04'], 'page': '/groups/{id}', 'control': '编辑分组页面（owned Group 非空态）'}
    L.ab_open('/groups/' + GROUP, wait=3.2)
    ev['page'] = js("""(() => {
      const name = [...document.querySelectorAll('input')].find(i => i.value === 'qa-cgap-1cdad4fe-group');
      const error = document.body.innerText.includes('分组不存在') || document.body.innerText.includes('已被删除');
      return {url: location.href, name_input_found: !!name, error_state: error,
              member_visible: document.body.innerText.includes('%s'),
              combos: [...document.querySelectorAll('[role=combobox]')].map(c => c.textContent.trim().slice(0, 20)),
              buttons: [...document.querySelectorAll('button')].map(b => b.textContent.trim()).filter(Boolean).slice(0, 12),
              cancel_links: [...document.querySelectorAll('a')].filter(a => a.textContent.trim() === '取消').map(a => a.getAttribute('href'))};
    })()""" % NATIVE1_HOST)
    L.ab_shot('b7-group-edit-page.png')
    ok = ev['page']['name_input_found'] and not ev['page']['error_state']
    ev['verdict'] = {'renders_owned_group': ok, 'member_visible': ev['page']['member_visible']}
    record('B7-group-detail-page', 'COVERED' if ok else 'PARTIAL', ev)


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    tests = {
        'b1': test_b1, 'b2': test_b2, 'b3': test_b3, 'b4': test_b4,
        'b5a': test_b5_script, 'b5b': test_b5_app, 'b5c': test_b5_artifact,
        'b6': test_b6, 'b7': test_b7,
    }
    if which == 'all':
        for name, fn in tests.items():
            try:
                fn()
            except Exception as exc:
                record(name, 'ERROR', {'error': repr(exc)})
                import traceback
                traceback.print_exc()
    else:
        tests[which]()
    L.save('phase-b-results.json', RESULTS)
    print('DONE', json.dumps({k: v['status'] for k, v in RESULTS['tests'].items()}, ensure_ascii=False))
