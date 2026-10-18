#!/usr/bin/env python3
"""UI stage for batch07: transfers table readback, form validation negatives, app assign."""
import json
import sys
import time

import hub
import nodes
import p16
import ui
from run_b07 import (NONCE, PREFIX, N3DIR, NATIVE3, rec, save_state, load_state, now)

EV = 'evidence/'
N3_HOST = 'qa-repair-20260930-101945-native-3'
HOST001_HOST = 'qa-resume-20261010-host-f174890c3846-host001'


def shot(name):
    return ui.screenshot('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/'
                         'qa-resume-20261014-web/batch07/evidence/%s.png' % name)


def select_combo(label_needle, option_text):
    """Open a radix Select identified by a needle on its *combobox* line and pick an option."""
    sn = ui.snap()
    combo_lines = [l for l in sn.splitlines() if 'combobox' in l and label_needle in l]
    if not combo_lines:
        combo_lines = [l for l in sn.splitlines() if 'combobox' in l]
    if not combo_lines:
        return {'ok': False, 'stage': 'combo_not_found', 'combobox_lines': [l.strip()[:110] for l in sn.splitlines() if 'combobox' in l]}
    ref = ui.REF_RE.search(combo_lines[0]).group(1) if ui.REF_RE.search(combo_lines[0]) else None
    if not ref:
        return {'ok': False, 'stage': 'combo_ref_missing', 'line': combo_lines[0].strip()[:110]}
    ui.click_ref(ref)
    time.sleep(1.2)
    sn2 = ui.snap()
    opt, oc = ui.ref_of(sn2, '"%s"' % option_text)
    if not opt:
        opt, oc = ui.ref_of(sn2, option_text)
    if not opt:
        return {'ok': False, 'stage': 'option_not_found', 'combo_line': combo_lines[0].strip()[:110],
                'option_lines': [l.strip()[:90] for l in sn2.splitlines() if 'option' in l][:8]}
    ui.click_ref(opt)
    time.sleep(0.8)
    return {'ok': True, 'combo_ref': ref, 'option_ref': opt}


def stage_ui_transfers(h, st):
    ev = {'at': now()}
    ui.login()
    ui.open_url(ui.BASE + '/transfers')
    time.sleep(3)
    ev['scan'] = ui.scan()
    ev['screenshot'] = shot('UI-01-transfers-table')
    ev['owned_rows'] = [r for r in (ev['scan'].get('rows') or []) if NONCE in json.dumps(r, ensure_ascii=False)]
    ev['expected_owned_targets'] = {
        t['id']: {'case': t['case'], 'status': 'see API snapshot',
                  'targets': list((t.get('targets') or {}).keys()) if isinstance(t.get('targets'), dict) else None}
        for t in st['created']['transfers']}
    return rec(st, 'UI-01-transfers.json', ev, 'UI transfers table')


def stage_ui_form_negative(h, st):
    """Drive the real new-transfer form with invalid paths: server 400 must surface as UI feedback."""
    ev = {'at': now(), 'attempts': []}
    code, before = h.get('/api/transfers')
    ev['transfers_before'] = len(before or [])
    ui.login()
    for attempt in (('relative_source', 'qa/w1410b07-relative.bin', N3DIR + '/ui-neg-dst.bin'),
                    ('traversal_dest', N3DIR + '/src-1m.bin', '/tmp/../../etc/b07-evil.bin')):
        name, src_path, dst_path = attempt
        ui.open_url(ui.BASE + '/transfers')
        time.sleep(2.5)
        a = {'name': name, 'source_path': src_path, 'dest_path': dst_path}
        a['select_source'] = select_combo('选择源 Agent', N3_HOST)
        sn = ui.snap()
        ref, cands = ui.ref_of(sn, '"数据"') or (None, [])
        # source path input: second textbox on the page (first is the source path)
        inputs = ui.eval_js("""(() => [...document.querySelectorAll('input')].map((e, i) => ({i, ph: e.placeholder, v: e.value})))()""")
        a['inputs_before'] = inputs
        idx = [i for i in inputs if i.get('ph') == '/var/lib/app/config.yaml'] if isinstance(inputs, list) else []
        a['path_inputs'] = idx
        # fill source path via the DOM (React-safe) by index 0 of the path inputs
        if idx:
            a['set_source_path'] = ui.eval_js("""(() => {
                const el = document.querySelectorAll('input')[%d];
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(el, %s);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return { value: el.value };
            })()""" % (idx[0]['i'], json.dumps(src_path)))
        a['select_target'] = select_combo('目标 Agent', HOST001_HOST)
        ins2 = ui.eval_js("""(() => [...document.querySelectorAll('input')].map((e, i) => ({i, ph: e.placeholder, v: e.value})))()""")
        pid = [i for i in ins2 if i.get('ph') == '/var/lib/app/config.yaml'] if isinstance(ins2, list) else []
        a['path_inputs_after_target'] = pid
        if len(pid) >= 2:
            a['set_dest_path'] = ui.eval_js("""(() => {
                const el = document.querySelectorAll('input')[%d];
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(el, %s);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return { value: el.value };
            })()""" % (pid[1]['i'], json.dumps(dst_path)))
        # 添加目标
        sn = ui.snap()
        add, cands = ui.ref_of(sn, 'button "添加目标"')
        a['add_target_ref'] = add
        a['add_target_line'] = cands[:2]
        if add:
            ui.click_ref(add)
            time.sleep(1.0)
        a['targets_listed'] = ui.eval_js("""(() => document.body.innerText.includes('添加目标') && [...document.querySelectorAll('div')].filter(d => d.innerText && d.innerText.includes('未知节点')).length)()""")
        # submit
        sn = ui.snap()
        sub, cands = ui.ref_of(sn, 'button "开始传输"')
        a['submit_ref'] = sub
        a['submit_line'] = cands[:2]
        if sub:
            ui.click_ref(sub)
        time.sleep(3)
        a['scan_after_submit'] = ui.scan()
        a['screenshot'] = shot('UI-02-form-negative-%s' % name)
        code2, after = h.get('/api/transfers')
        a['transfers_after'] = len(after or [])
        a['no_new_transfer'] = (a['transfers_after'] == ev['transfers_before'])
        ev['attempts'].append(a)
    return rec(st, 'UI-02-form-negative.json', ev, 'UI form validation negatives')


def stage_ui_assign(h, st):
    """Assign the owned application to native-3 through the real UI editor (drives the artifact prefetch)."""
    app_id = st['created']['applications'][0]['id']
    sha = st['created']['artifacts'][0]['sha256']
    ev = {'at': now(), 'application_id': app_id, 'artifact_sha256': sha}
    p16.stop_watcher()
    ev['watcher'] = p16.start_watcher(sha, seconds=240)
    ev['cache_before'] = p16.cache_listing()
    ui.login()
    ui.open_url(ui.BASE + '/applications/%s' % app_id)
    time.sleep(3)
    ev['before_scan'] = ui.scan()
    sn = ui.snap()
    ref, cands = ui.ref_of(sn, N3_HOST)
    ev['node_label_lines'] = cands[:6]
    ev['node_ref'] = ref
    if ref:
        ui.click_ref(ref)
        time.sleep(1.0)
    ev['after_check_scan'] = ui.scan()
    sn2 = ui.snap()
    save, sc = ui.ref_of(sn2, '保存')
    ev['save_ref'] = save
    if save:
        ui.click_ref(save)
    time.sleep(4)
    ev['after_save_scan'] = ui.scan()
    ev['after_save_toasts'] = ui.eval_js(
        """(() => [...document.querySelectorAll('[data-sonner-toast]')].map(e => e.innerText.trim()))()""")
    ev['screenshot'] = shot('UI-03-app-assign-native3')
    ev['api_app_nodes'] = h.get('/api/applications/%s/nodes' % app_id)[1]
    # give the agent time to download the 256 MiB artifact
    ev['cache_wait'] = p16.wait_cache_state(sha, 'file', timeout=180)
    time.sleep(5)
    ev['watch'] = p16.read_watch()
    ev['cache_after'] = p16.cache_listing()
    ev['cache_file_sha'] = p16.sh('sha256sum %s/%s 2>/dev/null | cut -d" " -f1' % (p16.CACHE, sha))
    ev['cache_file_stat'] = p16.sh('stat -c "%%s %%a %%Y" %s/%s' % (p16.CACHE, sha))
    ev['db'] = nodes.agent_db_artifacts()
    ev['agent_log'] = p16.agent_log('artifact').get('stdout', '')
    ev['baseline_entry'] = p16.sh('ls -la %s/%s' % (p16.CACHE, '37a674d655a6ac4111e488608d81d368364db8d8a13d4293d03e9f9eec8a7b8a'))
    p16.stop_watcher()
    return rec(st, 'UI-03-app-assign-prefetch.json', ev, 'UI app assign -> agent artifact prefetch')


def stage_ui_app_delete(h, st):
    """Delete the owned application through the UI (row menu -> 删除 -> confirm)."""
    app_id = st['created']['applications'][0]['id']
    name = st['created']['applications'][0]['name']
    ev = {'at': now(), 'application_id': app_id, 'name': name}
    ui.login()
    ui.open_url(ui.BASE + '/applications')
    time.sleep(3)
    ev['scan'] = ui.scan()
    sn = ui.snap()
    row_ref, cands = ui.ref_of(sn, name)
    ev['row_line'] = cands[:2]
    menu, mc = ui.ref_of(sn, 'button "打开菜单"', 0)
    if not menu:
        menu, mc = ui.ref_of(sn, 'menu', 0)
    ev['menu_ref'] = menu
    ev['menu_cands'] = mc[:5]
    if menu:
        ui.click_ref(menu)
        time.sleep(1.0)
        sn2 = ui.snap()
        delref, dc = ui.ref_of(sn2, '删除')
        ev['delete_item_ref'] = delref
        ev['delete_cands'] = dc[:6]
        if delref:
            ui.click_ref(delref)
            time.sleep(1.2)
            ev['confirm_scan'] = ui.scan()
            ev['screenshot_confirm'] = shot('UI-04-app-delete-confirm')
            sn3 = ui.snap()
            conf, cc = ui.ref_of(sn3, 'button "删除"')
            ev['confirm_ref'] = conf
            if conf:
                ui.click_ref(conf)
                time.sleep(3)
    ev['scan_after'] = ui.scan()
    code, body = h.get('/api/applications/%s' % app_id)
    ev['get_after_delete'] = {'http': code, 'body': body}
    return rec(st, 'UI-04-app-delete.json', ev, 'UI application delete -> GET 404')


STAGES = {'ui-transfers': stage_ui_transfers, 'ui-form-negative': stage_ui_form_negative,
          'ui-assign': stage_ui_assign, 'ui-app-delete': stage_ui_app_delete}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in STAGES:
        print('usage: run_ui.py <%s>' % '|'.join(STAGES))
        return 2
    st = load_state()
    h = hub.Hub()
    h.login()
    STAGES[sys.argv[1]](h, st)
    save_state(st)
    print(json.dumps({'stage': sys.argv[1], 'ok': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
