import asyncio
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

OUT = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web')
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
SESSION = 'qawebsecure20261009'
HOST = '192.168.100.209'
ORIGIN = 'http://192.168.100.209:18080'
EXECUTION_ID = '5c5d8c3e-945f-4ca2-ab91-fd30cb8b5b9b'
TASK_ID = '4006d075-8c15-4d14-9cbc-8b63f5823131'
CLI = ['agent-browser', '--session', SESSION, '--allowed-domains', HOST, '--args', '--unsafely-treat-insecure-origin-as-secure=' + ORIGIN]
conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
HUB = conn['hub_url'].rstrip('/')
AUTH = {'Authorization': 'Bearer ' + conn['token']}


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def save_json(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def progress(stage, **fields):
    p = json.loads((OUT / 'progress.json').read_text())
    p['phase'] = stage
    p['updated_at'] = now()
    p['current_gate'] = stage
    p.setdefault('runner', {}).update(fields)
    (OUT / 'progress.json').write_text(json.dumps(p, ensure_ascii=False, indent=2) + '\n')
    print(stage, flush=True)


def cli(*args, timeout=20):
    r = subprocess.run(CLI + list(args), text=True, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError('agent-browser action failed: ' + str(args[0]) + ', exit=' + str(r.returncode))
    return r.stdout


def ev(js):
    s = cli('eval', js)
    return json.loads(s)


def api(path):
    r = urlopen(Request(HUB + path, headers=AUTH), timeout=8)
    return r.status, json.loads(r.read())


def api_summary():
    hs, e = api('/api/executions/' + EXECUTION_ID)
    hl, logs = api('/api/executions/' + EXECUTION_ID + '/logs')
    return {
        'detail_http': hs,
        'status': e.get('status'),
        'exit_code': e.get('exit_code'),
        'task_revision': e.get('task_revision'),
        'offline': e.get('offline'),
        'synced': e.get('synced'),
        'stdout_length': len(e.get('stdout') or ''),
        'stderr_length': len(e.get('stderr') or ''),
        'logs_http': hl,
        'log_count': len(logs),
        'sequences': [x.get('seq') for x in logs],
        'streams': [x.get('stream') for x in logs],
        'attempt_metadata_present': any(('attempt' in x or 'attempt_id' in x) for x in logs) or ('attempts' in e),
        'markers': {
            'first': any('QA_WEB_LIVE_FIRST' in (x.get('chunk') or '') for x in logs),
            'second': any('QA_WEB_LIVE_SECOND' in (x.get('chunk') or '') for x in logs),
        },
    }


def page_summary():
    return ev('({path:location.pathname,online:navigator.onLine,secure:window.isSecureContext,first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND"),running:document.body.innerText.includes("运行中"),failed:document.body.innerText.includes("失败"),navCount:performance.getEntriesByType("navigation").length})')


def follow_state():
    return ev('(()=>{const b=[...document.querySelectorAll("button")].find(x=>x.innerText.trim()==="跟随");return {found:!!b,pressed:b?b.getAttribute("aria-pressed"):null}})()')


def log_text():
    return ev('(()=>{const c=[...document.querySelectorAll("pre,code")].find(x=>x.innerText.includes("QA_WEB_LIVE_FIRST"));return c?c.innerText:""})()')


def copy_feedback():
    return ev('({failed:document.body.innerText.includes("复制失败")||document.body.innerText.toLowerCase().includes("copy failed"),success:document.body.innerText.includes("复制成功")||document.body.innerText.includes("已复制")||document.body.innerText.toLowerCase().includes("copied")})')


def clip_text():
    raw = cli('--json', 'clipboard', 'read')
    d = json.loads(raw)
    def find(x):
        if isinstance(x, str): return x
        if isinstance(x, dict):
            for k in ('text', 'clipboard', 'content', 'data'):
                if isinstance(x.get(k), str): return x[k]
            for v in x.values():
                z = find(v)
                if z is not None: return z
        if isinstance(x, list):
            for v in x:
                z = find(v)
                if z is not None: return z
        return None
    return find(d) or ''


async def set_permission(setting):
    import websockets
    cdp = cli('get', 'cdp-url').strip()
    async with websockets.connect(cdp, open_timeout=5, close_timeout=2) as ws:
        await ws.send(json.dumps({
            'id': 1,
            'method': 'Browser.setPermission',
            'params': {
                'permission': {'name': 'clipboard-write'},
                'setting': setting,
                'origin': ORIGIN,
            },
        }))
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        return not bool(msg.get('error'))


def copy_ui(label):
    expected = log_text() or ''
    cli('find', 'role', 'button', 'click', '--name', '复制日志', '--exact')
    time.sleep(0.15)
    actual = clip_text()
    feedback = copy_feedback()
    return {
        'expected_length': len(expected.encode()),
        'clipboard_length': len(actual.encode()),
        'expected_sha256': hashlib.sha256(expected.encode()).hexdigest(),
        'clipboard_sha256': hashlib.sha256(actual.encode()).hexdigest(),
        'exact_match': actual == expected,
        'expected_nonempty': bool(expected),
        'ui_feedback': feedback,
        'feedback_says_copy_failed': feedback['failed'],
        'feedback_says_copy_success': feedback['success'],
        'label': label,
    }


def main():
    # This runner continues the already-running browser session/execution.
    start_ui = page_summary()
    start_api = api_summary()
    if not start_ui['path'].endswith(EXECUTION_ID): raise RuntimeError('wrong execution page')
    if not start_ui['first']: raise RuntimeError('first chunk missing in UI')
    if start_ui['online']: raise RuntimeError('browser must be offline before runner starts')
    if start_api['status'] != 'RUNNING': raise RuntimeError('execution is not running at offline start')
    save_json('offline-start.json', {'captured_at': now(), 'ui': {k:v for k,v in start_ui.items() if k!='logText'}, 'api': start_api})
    cli('scroll', 'down', '600')
    cli('screenshot', 'main', str((OUT / 'offline-running-first.png').resolve()))
    progress('OFFLINE_RUNNING_FIRST_CHUNK', browser_offline=True, execution_status=start_api['status'], first_visible=True, second_visible=start_ui['second'])

    # Exercise stream filter, search and Follow with the browser session genuinely offline.
    cli('select', 'select', 'stdout')
    stdout_ui = page_summary()
    cli('select', 'select', 'stderr')
    stderr_ui = page_summary()
    cli('select', 'select', 'all')
    cli('fill', 'input[placeholder="搜索日志..."]', 'QA_WEB_LIVE_FIRST')
    search_ui = page_summary()
    cli('fill', 'input[placeholder="搜索日志..."]', '')
    follow_before = follow_state()
    cli('find', 'role', 'button', 'click', '--name', '跟随', '--exact')
    follow_off = follow_state()
    cli('find', 'role', 'button', 'click', '--name', '跟随', '--exact')
    follow_on = follow_state()
    offline_controls = {
        'stdout_first_visible': stdout_ui['first'],
        'stderr_first_visible': stderr_ui['first'],
        'all_after_reset_first_visible': page_summary()['first'],
        'search_keeps_first': search_ui['first'],
        'follow_before': follow_before['pressed'],
        'follow_after_first_click': follow_off['pressed'],
        'follow_after_second_click': follow_on['pressed'],
        'nav_count': page_summary()['navCount'],
        'browser_online_after_controls': page_summary()['online'],
    }
    # Copy the currently visible UI log through the actual page button and read back system clipboard.
    offline_copy = copy_ui('offline_first_chunk')
    denied_copy = None
    permission_denied_supported = False
    permission_state = 'unavailable'
    permission_restored = False
    try:
        permission_denied_supported = asyncio.run(set_permission('denied'))
        if permission_denied_supported:
            q = ev('navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state)')
            permission_state = q if isinstance(q, str) else str(q)
            denied_copy = copy_ui('clipboard_permission_denied')
            permission_restored = asyncio.run(set_permission('granted'))
    except Exception as ex:
        permission_state = 'tool_error:' + type(ex).__name__
    save_json('offline-ui-controls.json', {'captured_at': now(), 'controls': offline_controls, 'copy': offline_copy, 'clipboard_permission_denied_supported': permission_denied_supported, 'permission_state': permission_state, 'denied_copy': denied_copy, 'permission_restored': permission_restored, 'first_only': not page_summary()['second']})
    progress('OFFLINE_UI_FILTER_SEARCH_FOLLOW_COPY_CAPTURED', browser_offline=True, copy_exact=offline_copy['exact_match'], permission_denial_supported=permission_denied_supported, controls=offline_controls)

    # The Agent keeps executing while the browser session is offline. Confirm server state out of band.
    time.sleep(5)
    offline_api = api_summary()
    cli('set', 'offline', 'off')
    progress('BROWSER_RECONNECTED_WAITING_FOR_LOGS_AND_TERMINAL', browser_offline=False, server_status_during_offline=offline_api['status'], offline_log_count=offline_api['log_count'])

    # Wait for the live page to recover, then for the configured retry path to reach terminal FAILED.
    deadline = time.time() + 105
    recovered = None
    final_api = None
    while time.time() < deadline:
        recovered = page_summary()
        final_api = api_summary()
        if recovered['online'] and recovered['second']:
            break
        time.sleep(1)
    recovered_api = final_api
    progress('RECONNECTED_SECOND_CHUNK_VISIBLE' if recovered and recovered['second'] else 'RECONNECTED_SECOND_CHUNK_NOT_YET_VISIBLE', browser_online=recovered['online'] if recovered else None, second_visible=recovered['second'] if recovered else False, api_status=recovered_api['status'] if recovered_api else None)

    # Final status may follow the second attempt's delay. Keep observing the same URL; never reload.
    while time.time() < deadline:
        recovered = page_summary()
        final_api = api_summary()
        if final_api['status'] in ('FAILED','SUCCESS','CANCELED','TIMED_OUT','SKIPPED','BLOCKED'):
            break
        time.sleep(1)
    terminal_ui = page_summary()
    terminal_api = api_summary()
    cli('scroll', 'down', '600')
    cli('screenshot', 'main', str((OUT / 'reconnected-terminal.png').resolve()))
    final_copy = copy_ui('reconnected_terminal')
    final_record = {
        'captured_at': now(),
        'execution_id': EXECUTION_ID,
        'ui': {k:v for k,v in terminal_ui.items() if k != 'logText'},
        'api': terminal_api,
        'offline_server_state': offline_api,
        'offline_controls': offline_controls,
        'offline_copy': offline_copy,
        'terminal_copy': final_copy,
        'same_page_no_reload': terminal_ui['navCount'] == 1,
        'attempt_metadata_visible_or_persisted': terminal_api['attempt_metadata_present'],
        'evidence': ['offline-running-first.png','reconnected-terminal.png','offline-start.json','offline-ui-controls.json'],
        'raw_logs_saved': False,
        'clipboard_plaintext_saved': False,
        'credentials_saved': False,
    }
    save_json('live-offline-recovery-results.json', final_record)
    progress('LIVE_OFFLINE_RECOVERY_FINALIZED', browser_online=terminal_ui['online'], ui_status='FAILED' if terminal_ui['failed'] else ('RUNNING' if terminal_ui['running'] else 'OTHER'), api_status=terminal_api['status'], api_exit_code=terminal_api['exit_code'], same_page_no_reload=terminal_ui['navCount']==1, copy_exact=final_copy['exact_match'])
    print(json.dumps({'offline_server_status':offline_api['status'],'offline_chunk_count':offline_api['log_count'],'ui_offline':offline_controls,'reconnected_ui':{k:v for k,v in terminal_ui.items() if k!='logText'},'api_terminal':terminal_api,'offline_copy_exact':offline_copy['exact_match'],'terminal_copy_exact':final_copy['exact_match'],'permission_denial_supported':permission_denied_supported,'permission_denial_feedback':denied_copy['feedback_says_copy_failed'] if denied_copy else None},ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
