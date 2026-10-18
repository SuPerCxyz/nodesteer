#!/usr/bin/env python3
"""用隔离的agent-browser会话核验真实Execution详情反馈，结束后删除凭据profile和会话。"""
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')
HUB = 'http://192.168.100.209:18080'
EXECUTION_ID = 'b60efe6f-5685-4dbd-a58d-02d0b7c58ad3'


def cli(args, *, data=None, timeout=60):
    result = subprocess.run(['agent-browser', *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return result.returncode, result.stdout.decode('utf-8', 'replace'), result.stderr.decode('utf-8', 'replace')


def main():
    st = PRIVATE.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
        raise RuntimeError('private_connection_file_gate_failed')
    info = json.loads(PRIVATE.read_text())
    if info.get('hub_url') != HUB or not info.get('admin_username') or not info.get('admin_password'):
        raise RuntimeError('private_connection_authority_gate_failed')
    profile = 'nodesteer-qa-' + secrets.token_hex(5)
    session = 'nodesteer-backend-qa-' + secrets.token_hex(5)
    screenshot = HERE / 'sha-execution-ui.png'
    evidence = HERE / 'sha-execution-ui.json'
    try:
        rc, _, _ = cli(['auth', 'save', profile, '--url', HUB + '/sign-in', '--username', info['admin_username'], '--password-stdin'], data=info['admin_password'].encode() + b'\n')
        if rc != 0:
            raise RuntimeError('browser_auth_profile_save_failed')
        base = ['--session', session, '--allowed-domains', '192.168.100.209']
        rc, _, _ = cli(base + ['auth', 'login', profile], timeout=90)
        if rc != 0:
            raise RuntimeError('browser_auth_login_failed')
        rc, _, _ = cli(base + ['open', HUB + '/executions/' + EXECUTION_ID], timeout=60)
        if rc != 0:
            raise RuntimeError('browser_execution_navigation_failed')
        rc, _, _ = cli(base + ['wait', '--load', 'networkidle'], timeout=60)
        if rc != 0:
            raise RuntimeError('browser_execution_load_failed')
        rc, snapshot, _ = cli(base + ['snapshot', '-i'])
        if rc != 0:
            raise RuntimeError('browser_execution_snapshot_failed')
        rc, page_text, _ = cli(base + ['read'])
        if rc != 0:
            raise RuntimeError('browser_execution_read_failed')
        rc, title, _ = cli(base + ['get', 'title'])
        if rc != 0:
            raise RuntimeError('browser_execution_title_failed')
        rc, url, _ = cli(base + ['get', 'url'])
        if rc != 0:
            raise RuntimeError('browser_execution_url_failed')
        rc, _, _ = cli(base + ['screenshot', str(screenshot)])
        if rc != 0 or not screenshot.is_file():
            raise RuntimeError('browser_execution_screenshot_failed')
        normalized = page_text.lower()
        assertions = {
            'correct_execution_route': EXECUTION_ID in url,
            'execution_success_visible': 'success' in normalized or '成功' in page_text,
            'stdout_or_output_section_visible': any(x in normalized for x in ['stdout', 'output', 'standard output']),
        }
        if not all(assertions.values()):
            raise RuntimeError('browser_execution_visible_state_assertion_failed')
        result = {'status': 'PASS', 'url': url.strip(), 'title': title.strip(), 'execution_id': EXECUTION_ID, 'assertions': assertions, 'snapshot': snapshot, 'page_text': page_text, 'screenshot': str(screenshot), 'credential_values_saved_to_evidence': False}
        tmp = evidence.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, evidence)
        print(json.dumps({'status': 'PASS', 'execution_id': EXECUTION_ID, 'screenshot': str(screenshot), 'evidence': str(evidence)}, ensure_ascii=False))
    finally:
        cli(['--session', session, 'close'], timeout=30)
        cli(['auth', 'delete', profile], timeout=30)


if __name__ == '__main__':
    main()
