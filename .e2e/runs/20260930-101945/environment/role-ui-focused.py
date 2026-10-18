#!/usr/bin/env python3
"""定向重验 Radix checkbox 及取消动画，并补空列表的新建路由。"""
from pathlib import Path

source_file = Path(__file__).with_name('role-ui-runner.py')
exec(compile(source_file.read_text().split('try:\n    for role, session in')[0], str(source_file), 'exec'))
result = json.loads((OUT / 'role-ui-results.json').read_text())
result['harness_observations'] = [
    {'classification': 'TEST_HARNESS', 'issue': 'Radix checkbox capture now records role=checkbox before HTML element type; original disabled properties were captured correctly.', 'initial_false_failures_invalidated': True},
    {'classification': 'TEST_HARNESS', 'issue': 'Cancel result must wait for closing animation then check open dialog state.', 'initial_false_failures_invalidated': True},
]

def cancel_dialog(case_id, screenshot):
    before = evaluate('({open:!!document.querySelector("[role=alertdialog][data-state=open],[role=dialog][data-state=open]"),guard:window.__roleReadOnly})')
    browser(['screenshot', str(EVIDENCE / screenshot)])
    browser(['find', 'role', 'button', 'click', '--name', '取消'])
    time.sleep(.5)
    after = evaluate('({open:!!document.querySelector("[role=alertdialog][data-state=open],[role=dialog][data-state=open]"),guard:window.__roleReadOnly})')
    row = {'id': case_id, 'status': 'PASS' if before['open'] and not after['open'] and not after['guard']['blockedWrites'] else 'FAIL', 'opened': before['open'], 'closed': not after['open'], 'business_requests': after['guard']['requests'], 'blocked_writes': after['guard']['blockedWrites'], 'evidence': 'environment/role-ui-evidence/' + screenshot}
    result['roles']['operator']['dialogs'] = [r for r in result['roles']['operator']['dialogs'] if r['id'] != case_id] + [row]
    save()

try:
    for role, session in [('operator', 'rolop2'), ('viewer', 'rolvi2')]:
        profile = PRIVATE / ('browser-role-' + role + '-focused')
        assert not profile.exists(); profile.mkdir(mode=0o700)
        config = PRIVATE / ('browser-role-' + role + '-focused-config.json'); config.write_text('{}')
        ENV = dict(os.environ, AGENT_BROWSER_SOCKET_DIR=str(PRIVATE / 'browser-runtime'), AGENT_BROWSER_PROFILE=str(profile), AGENT_BROWSER_CONFIG=str(config), AGENT_BROWSER_ARGS='--no-sandbox,--disable-setuid-sandbox,--disable-crash-reporter,--disable-breakpad,--crash-dumps-dir=' + str(PRIVATE / 'browser-crash'), XDG_CACHE_HOME=str(PRIVATE / 'browser-cache'), XDG_CONFIG_HOME=str(PRIVATE / 'browser-xdg-config'), TMPDIR=str(PRIVATE))
        BASE = ['agent-browser', '--namespace', 'nse2e', '--session', session]
        browser(['open', URL + '/sign-in']); assert evaluate('!!localStorage.getItem("nodesteer_token")') is False
        evaluate('(()=>{const values=' + json.dumps({'username': accounts[role]['username'], 'password': accounts[role]['password']}) + ';for(const [name,value]of Object.entries(values)){const input=document.querySelector("input[name="+name+"]");Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,value);input.dispatchEvent(new Event("input",{bubbles:true}));input.dispatchEvent(new Event("change",{bubbles:true}))}return true})()')
        browser(['click', 'button[type=submit]'])
        for attempt in range(30):
            state = evaluate('({path:location.pathname,has_token:!!localStorage.getItem("nodesteer_token")})')
            if state['path'] == '/' and state['has_token']: break
            time.sleep(.2)
        assert state['path'] == '/' and state['has_token']
        result['roles'][role]['focused_recheck_fresh_login'] = evaluate('(async()=>{const r=await fetch("/api/me",{headers:{Authorization:"Bearer "+localStorage.getItem("nodesteer_token")}});const d=await r.json();return {status:r.status,role:d.Role}})()')
        page = capture(role, 'application-detail', '/applications/e35f4c8d-e88e-42b0-87c3-d395911222e6')
        boxes = [x for x in page['inputs'] if x['label'] == 'checkbox']
        visible = any(x['text'] in ['部署', 'Deploy'] for x in page['buttons'])
        page['application_operation_control_matches_role'] = visible == (role == 'operator')
        page['assign_checkboxes_disabled'] = bool(boxes) and all(x['disabled'] for x in boxes)
        page['status'] = 'PASS' if page['application_operation_control_matches_role'] and page['assign_checkboxes_disabled'] and not page['unexpected_cud_controls'] else 'FAIL'; save()
        if role == 'operator':
            capture(role, 'task-run', '/tasks/917a3edc-6de0-4e6a-a988-c81e51ee9e55/run')
            browser(['find', 'role', 'button', 'click', '--name', '立即运行'])
            cancel_dialog('task-run-open-cancel', 'operator-task-run-dialog.png')
            capture(role, 'agent-bulk', '/agents')
            browser(['find', 'first', 'tbody button[role=checkbox]', 'check'])
            browser(['find', 'role', 'button', 'click', '--name', '升级 Agent'])
            cancel_dialog('agent-upgrade-open-cancel', 'operator-upgrade-dialog.png')
        for key, path in [('group-create','/groups/new'),('schedule-create','/schedules/new'),('script-create','/scripts/new'),('task-create','/tasks/new'),('application-create','/applications/new'),('artifact-create','/artifacts/new')]:
            capture(role, key, path)
        browser(['close'], allow_error=True)
    result['unverified_row_controls'] = ['Groups and Schedules existing-row CUD menus: no current records; actual new/editor routes and hidden submit checked. No existing-row mutation executed.']
    result['status'] = 'passed' if all(p['status'] == 'PASS' for r in result['roles'].values() for p in r['pages'].values()) and all(c['status'] == 'PASS' for r in result['roles'].values() for c in r['contracts']) and all(d['status'] == 'PASS' for r in result['roles'].values() for d in r['dialogs']) else 'completed_with_failures'
except Exception as e:
    result['status'] = 'interrupted'; result['error'] = {'type': type(e).__name__, 'message': 'Focused role UI recheck failed; private CLI logs retained'}
finally:
    browser(['close'], allow_error=True)
    result['recorded_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat(); save()
    print(json.dumps({'focused_result': result['status'], 'page_counts': {k: len(r['pages']) for k,r in result['roles'].items()}, 'dialogs': result['roles']['operator']['dialogs'], 'page_failures': {k: [p for p,v in r['pages'].items() if v['status'] != 'PASS'] for k,r in result['roles'].items()}, 'error': result.get('error')}), flush=True)
