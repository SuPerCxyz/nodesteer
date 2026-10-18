#!/usr/bin/env python3
"""Supplemental UI probe: the P14P15-14 `UNKNOWN` dimension.

The dashboard card `托管应用状态` derives `health = healthy | unhealthy | unknown`
from `/applications/{id}/state` (web/src/features/overview/index.tsx:77-85), so an
application that has never been deployed (no state rows at all) renders the
`未知` badge there.  This probe creates one owned application through the Web UI
with no node assigned, reads the dashboard badge, then removes it through the
product delete path and verifies GET 404 for every fixture it created.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import browser as browsemod  # noqa: E402
import hostlib  # noqa: E402
import hubapi  # noqa: E402
import run_appui as R  # noqa: E402


def main():
    br = browsemod.Browser(R.BROWSER_SESSION)
    run = R.Run(br)
    run.ui_log = []
    out = {'at_utc': R.NOW(), 'case': 'P14P15-14', 'dimension': 'UNKNOWN',
           'verified_build': R.VERIFIED_BUILD, 'endpoint': R.HUB_HTTP,
           'script': 'supplemental.py'}
    art_id = None
    app_id = None
    try:
        out['preexisting_owned'] = {
            'apps': [a['name'] for a in run.hub.json('/api/applications')
                     if (a.get('name') or '').startswith(R.PREFIX)],
            'artifacts': [a['name'] for a in run.hub.json('/api/artifacts')
                          if (a.get('name') or '').startswith(R.PREFIX)]}
        if any(out['preexisting_owned'].values()):
            raise RuntimeError('owned_fixture_already_present')

        content = R.app_script('9.9')
        name = R.PREFIX + 'payload-supp'
        code, body = run.hub.upload_artifact(name, 'v1', name + '.bin', content)
        if code != 200 or body.get('sha256') != R.SHA(content):
            raise RuntimeError('artifact_upload_failed_http_%s' % code)
        art_id = body['id']
        run.state['artifacts'] = {'SUPP': {'id': art_id, 'sha256': body['sha256'],
                                           'name': name, 'version': 'v1', 'size': body['size']}}
        out['artifact'] = {'id': art_id, 'name': name, 'sha256': body['sha256']}

        # --- create through the Web UI (no node checked -> the app has no state)
        run.ui_open('/applications/new')
        br.wait_text('新建托管应用', timeout=30)
        time.sleep(1.0)
        run.ui_fill_field('名称', R.APP_NAME)
        run.ui_select_artifact(name)
        run.ui_fill_field('版本', '9.9')
        run.ui_fill_field('二进制路径', R.BINARY)
        run.ui_fill_field('配置路径', R.CONFIG)
        run.ui_fill_field('Unit', R.UNIT)
        run.ui_fill_field('配置内容', 'APP_MODE=supp\nAPP_VERSION=9.9\n')
        run.ui_set_health_type('systemd')
        run.ui_fill_field('健康检查目标', R.UNIT)
        out['checkboxes_left_untouched'] = run.ui_checkbox_states()
        out['save_url'] = run.ui_save()
        time.sleep(2.5)
        app_id = out['save_url'].rstrip('/').split('/')[-1]
        run.state['apps']['main'] = app_id
        out['created_app_id'] = app_id
        out['assign_after_create'] = run.app_nodes(app_id)
        out['state_after_create'] = run.app_states(app_id)
        out['revision_after_create'] = (run.app_hub(app_id) or {}).get('revision')

        # --- dashboard badge for a never-deployed application
        run.ui_open('/')
        br.wait_text('托管应用状态', timeout=30)
        time.sleep(2.5)
        out['dashboard_application_state'] = br.evaljs(
            "(function(){const t=Array.from(document.querySelectorAll('div'))"
            ".find(e=>e.innerText.trim()==='托管应用状态');if(!t)return null;"
            "const card=t.closest('div.bg-card')||t.closest('div');"
            "return {card_text:card.innerText, items:Array.from(card.querySelectorAll('li,div'))"
            ".map(e=>e.innerText.trim()).filter(x=>x&&x.length<200)};})()")
        out['dashboard_badge_for_owned_app'] = br.evaljs(
            "(function(){const el=Array.from(document.querySelectorAll('*'))"
            ".filter(e=>e.children.length===0&&e.innerText&&e.innerText.trim()===%s);"
            "if(!el.length)return null;let n=el[0],chain=[];"
            "for(let i=0;i<4&&n;i++){chain.push({tag:n.tagName,text:(n.innerText||'').trim().slice(0,40),"
            "cls:String(n.className||'').slice(0,80)});n=n.parentElement;}return chain;})()"
            % json.dumps(R.APP_NAME))
        out['dashboard_unknown_badge_present'] = bool(br.evaljs(
            "(function(){const t=Array.from(document.querySelectorAll('div'))"
            ".find(e=>e.innerText.trim()==='托管应用状态');if(!t)return false;"
            "const card=t.closest('div.bg-card')||t.closest('div');"
            "return card.innerText.includes(%s)&&card.innerText.includes('未知');})()"
            % json.dumps(R.APP_NAME)))
        br.screenshot(str(R.EVID / 'ui-16-dashboard-unknown.png'))
        out['dashboard_card_text'] = (out['dashboard_application_state'] or {}).get('card_text')
        run.rec('supplemental-dashboard-unknown.json', out)

        # --- product-path delete (confirm) + 404 verification
        out['menu_items'] = run.ui_click_row_menu('删除')
        out['confirm_dialog'] = run.ui_confirm_dialog_text()
        out['confirm_click'] = run.ui_click_action_dialog('删除')
        time.sleep(2)
        out['delete_requests'] = run.ui_net('/api/applications/' + app_id, 'DELETE')
        deadline = time.time() + 60
        code = 0
        while time.time() < deadline:
            code, _ = run.hub.get('/api/applications/' + run.hub.quote(app_id))
            if code == 404:
                break
            time.sleep(2)
        out['get_http_after_delete'] = code
        out['ui_row_after_delete'] = run.ui_app_row()
        out['host_app_cleanup'] = _host_cleanup_probe(run, app_id)
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        out['error'] = '%s: %s' % (type(exc).__name__, exc)
    finally:
        # always delete everything this probe created, through the product path
        try:
            if app_id:
                run.hub.delete('/api/applications/' + run.hub.quote(app_id))
                time.sleep(2)
                out['final_app_get_http'] = run.hub.get('/api/applications/' + run.hub.quote(app_id))[0]
            if art_id:
                run.hub.delete('/api/artifacts/' + run.hub.quote(art_id))
                out['final_artifact_get_http'] = run.hub.get('/api/artifacts/' + run.hub.quote(art_id))[0]
        except BaseException as exc:
            out['final_delete_error'] = str(exc)
        out['leftover_owned'] = {
            'apps': [a['id'] for a in run.hub.json('/api/applications')
                     if (a.get('name') or '').startswith(R.PREFIX)],
            'artifacts': [a['id'] for a in run.hub.json('/api/artifacts')
                          if (a.get('name') or '').startswith(R.PREFIX)],
            'nodes': [n['id'] for n in run.hub.json('/api/nodes')
                      if (n.get('hostname') or '').startswith(R.PREFIX)]}
        out['clean'] = not any(out['leftover_owned'].values())
        run.rec('supplemental-dashboard-unknown.json', out)
        print(json.dumps({k: out.get(k) for k in
                          ('error', 'created_app_id', 'dashboard_unknown_badge_present',
                           'get_http_after_delete', 'clean')}, ensure_ascii=False, indent=1))
    return 0 if out.get('clean') else 1


def _host_cleanup_probe(run, app_id):
    """Confirm nothing was installed on native-1 by the never-deployed app."""
    ch = hostlib.OwnedChild(R.NONCE, 'n1', '')
    probe = ch.app_state(app_id, R.UNIT, R.BINARY, R.CONFIG)
    return {'unit_file_lexists': probe['unit_file'].get('lexists'),
            'binary_lexists': probe['binary'].get('lexists'),
            'config_lexists': probe['config'].get('lexists'),
            'unit_state': probe['unit_state'].get('LoadState'),
            'unit_registry': probe.get('unit_registry')}


if __name__ == '__main__':
    sys.exit(main())
