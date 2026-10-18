#!/usr/bin/env python3
"""Read-only recon of the repair authority :18080 for web batch08.

Never prints credentials. Endpoint hard-guarded to :18080.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
REQUIRED_PORT = 18080
BASE = 'http://192.168.100.209:18080'
HERE = Path(__file__).resolve().parent
BATCH = HERE.parent


def call(method, path, token=None, body=None, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw or b'null')
            except Exception:
                return resp.status, raw[:300].decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b'null')
        except Exception:
            return exc.code, raw[:300].decode('utf-8', 'replace')


def login():
    st = CRED.lstat()
    if CRED.is_symlink() or not CRED.is_file() or (st.st_mode & 0o777) != 0o600:
        raise SystemExit('credential file gate failed')
    cred = json.loads(CRED.read_text())
    code, body = call('POST', '/api/login', body={
        'username': cred['admin_username'], 'password': cred['admin_password']})
    if code != 200 or not isinstance(body, dict) or not body.get('token'):
        raise SystemExit('login failed http=%s' % code)
    return body['token']


def main():
    url = urllib.parse.urlsplit(BASE)
    if url.port != REQUIRED_PORT:
        raise SystemExit('endpoint guard failed')
    token = login()
    out = {'base': BASE, 'generated_at': None}
    nodes = call('GET', '/api/nodes', token)[1]
    out['nodes'] = [{
        'id': n.get('id'), 'name': n.get('name'), 'hostname': n.get('hostname'),
        'status': n.get('status'), 'sync_status': n.get('sync_status'),
        'deployment_mode': n.get('deployment_mode'), 'agent_version': n.get('agent_version'),
        'capabilities': n.get('capabilities'), 'labels': n.get('labels'),
    } for n in (nodes or [])]
    for key, path in (('applications', '/api/applications'), ('artifacts', '/api/artifacts'),
                      ('tasks', '/api/tasks'), ('scripts', '/api/scripts'),
                      ('schedules', '/api/schedules'), ('groups', '/api/groups'),
                      ('executions', '/api/executions')):
        code, body = call('GET', path, token)
        rows = body if isinstance(body, list) else (body or {}).get('items', []) if isinstance(body, dict) else []
        out[key] = {'http': code, 'count': len(rows)}
        if key in ('applications', 'artifacts'):
            out[key]['rows'] = [{
                'id': r.get('id'), 'name': r.get('name'), 'version': r.get('version'),
                'artifact_id': r.get('artifact_id'), 'artifact_url': r.get('artifact_url'),
                'revision': r.get('revision'),
            } for r in rows]
        if key == 'tasks':
            out[key]['rows'] = [{
                'id': r.get('id'), 'name': r.get('name'), 'type': r.get('type'),
                'script_id': r.get('script_id'), 'targets': r.get('targets'),
            } for r in rows]
        if key == 'scripts':
            out[key]['rows'] = [{'id': r.get('id'), 'name': r.get('name'),
                                 'revision': r.get('revision')} for r in rows]
        if key == 'executions':
            out[key]['head'] = [{'id': r.get('id'), 'task_id': r.get('task_id'),
                                 'node_id': r.get('node_id'), 'status': r.get('status'),
                                 'trigger_type': r.get('trigger_type')} for r in rows[:5]]
    out['rev'] = None
    code, body = call('GET', '/api/nodes', token)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
