#!/usr/bin/env python3
"""本轮真实 Hub/Agent API 验收；凭据只从私密 JSON 加载。"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
RUN_ID = '20260930-101945'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}


class Blocked(Exception):
    pass


class Runner:
    def __init__(self, config, mutations=False, services=False):
        self.cfg = config
        self.mutations, self.services = mutations, services
        self.base = config.get('hub_url', config.get('base_url', '')).rstrip('/')
        self.token = config.get('token', config.get('admin_token', ''))
        self.cfg.setdefault('username', config.get('admin_username', ''))
        self.cfg.setdefault('password', config.get('admin_password', ''))
        self.prefix = config.get('prefix', 'qa-' + RUN_ID + '-be')
        assert re.fullmatch(r'qa-20260930-101945-be(?:-[A-Za-z0-9]+)*', self.prefix)
        self.nodes = config.get('nodes', config.get('agents', []))
        self.secrets = [str(v) for k, v in config.items() if k in {'token', 'password', 'admin_token', 'admin_password', 'registration_token'} and v]
        for n in self.nodes:
            self.secrets.extend(str(n[k]) for k in ['ssh_password', 'password'] if n.get(k))
        for login in config.get('roles', {}).values():
            self.secrets.extend(str(login[k]) for k in ['password', 'token'] if login.get(k))
        self.ledger_path = HERE / 'ledger.json'
        self.ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else []
        self.events = []
        self.evidence = HERE / 'evidence'
        self.evidence.mkdir(exist_ok=True)

    def safe(self, value):
        if isinstance(value, dict):
            return {k: ('[redacted]' if k.lower() in {'password', 'token', 'credential', 'agent_credential', 'registration_token', 'registration_key', 'authorization', 'cookie', 'password_hash'} else self.safe(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.safe(v) for v in value]
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, '[redacted]')
        return value

    def save(self, path, value):
        path.write_text(json.dumps(self.safe(value), ensure_ascii=False, indent=2) + '\n')

    def api(self, method, path, body=None, expected=(200,), token=None, headers=None, raw=None, trace=True):
        if method not in {'GET', 'HEAD', 'OPTIONS'} and not self.mutations:
            raise Blocked('未开启本轮状态变更执行门禁')
        request_headers = dict(headers or {})
        active_token = self.token if token is None else token
        if active_token:
            request_headers['Authorization'] = 'Bearer ' + active_token
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            request_headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path, data=data, headers=request_headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status, response_headers, payload = response.status, dict(response.headers), response.read()
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            value = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        if trace:
            self.events.append({'api': method + ' ' + path, 'status': status, 'request': body, 'response': value, 'headers': response_headers})
        assert status in expected, f'{method} {path}: HTTP {status}, expected {expected}: {self.safe(value)}'
        return value, response_headers, payload

    def login(self):
        if not self.token:
            value, _, _ = self.api('POST', '/api/login', {'username': self.cfg['username'], 'password': self.cfg['password']}, token='')
            self.token = value['token']
            self.secrets.append(self.token)

    def remember(self, kind, value):
        row = {'type': kind, 'id': value['id'], 'name': value.get('name', value.get('username', '')), 'status': 'created'}
        assert kind in {'script', 'task', 'schedule', 'group', 'artifact', 'application', 'transfer', 'execution', 'user'}
        self.ledger.append(row)
        self.save(self.ledger_path, self.ledger)
        return value

    def create(self, kind, body):
        value, _, _ = self.api('POST', '/api/' + {'application': 'applications'}.get(kind, kind + 's'), body)
        return self.remember(kind, value)

    def node(self, number=0):
        if len(self.nodes) <= number:
            raise Blocked(f'需要至少 {number + 1} 个真实 Agent 客户机')
        return self.nodes[number]

    def resolve_nodes(self):
        values, _, _ = self.api('GET', '/api/nodes', trace=False)
        available = []
        for node in self.nodes:
            matches = [value for value in values if value.get('id') == node.get('id') or value.get('ip') == node.get('ip') or value.get('hostname') == node.get('domain')]
            if not matches or matches[0]['status'] != 'online': continue
            node.update(id=matches[0]['id'], name=matches[0].get('hostname', ''), arch=matches[0].get('arch', 'amd64'), mode=matches[0].get('deployment_mode', 'native'))
            available.append(node)
        self.nodes = available

    def remote(self, node, command):
        argv = node.get('executor_argv', self.cfg.get('executor_argv'))
        if argv and node.get('ssh_target') and any(part.endswith('/guest-exec.py') for part in argv):
            argv = None  # 管理沙箱下SSH显式读取用户配置，避免系统config owner检查误伤。
        env = os.environ.copy()
        if argv:
            argv = [part.replace('{node_id}', node['id']).replace('{node_name}', node.get('name', node['id'])) for part in argv]
        elif node.get('ssh_target'):
            argv = ['ssh', '-F', self.cfg.get('ssh_config', '/home/superc/.ssh/config'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
            if self.cfg.get('jump_host'):
                argv += ['-J', self.cfg['jump_host']]
            if node.get('ssh_port'):
                argv += ['-p', str(node['ssh_port'])]
            if node.get('ssh_identity_file'):
                argv += ['-i', node['ssh_identity_file']]
            remote_command = 'docker exec -i ' + shlex.quote(node['docker_container']) + ' sh -s' if node.get('docker_container') else 'sh -s'
            argv += [node['ssh_target'], remote_command]
        else:
            raise Blocked('该 Agent 没有客户机读取 helper/SSH 配置')
        result = subprocess.run(argv, input=command, text=True, capture_output=True, timeout=45, env=env)
        self.events.append({'agent': node['id'], 'read_command': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        assert result.returncode == 0, f'Agent 客户机读取失败: {self.safe(result.stderr)}'
        return result.stdout.strip()

    def journal(self, node, execution_id):
        if not node.get('data_dir'):
            raise Blocked('缺少 Agent data_dir，无法核实本地 Journal')
        script = "import sqlite3,json,sys; c=sqlite3.connect('file:'+sys.argv[1]+'/state.db?mode=ro',uri=True); c.row_factory=sqlite3.Row; r=c.execute('SELECT id,task_id,node_id,status,exit_code,scheduled_time,synced,block_reason,task_revision,script_revision FROM executions WHERE id=?',(sys.argv[2],)).fetchone(); print(json.dumps(dict(r) if r else None))"
        output = self.remote(node, 'python3 -c ' + shlex.quote(script) + ' ' + shlex.quote(node['data_dir']) + ' ' + shlex.quote(execution_id) + '\n')
        value = json.loads(output)
        assert value is not None, '真实 Agent Journal 缺少执行记录'
        return value

    def task(self, label, command='', node_ids=None, **extra):
        value = {'name': self.prefix + '-' + label + '-' + uuid.uuid4().hex[:6], 'type': 'command', 'target': {'type': 'node', 'node_ids': node_ids or [self.node()['id']]}, 'command': command, 'timeout': 20, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
        value.update(extra)
        return self.create('task', value)

    def wait_execution(self, execution_id, status='SUCCESS', timeout=100):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value, _, _ = self.api('GET', '/api/executions/' + execution_id, trace=False)
            if value['status'] in TERMINAL:
                self.events.append({'api': 'GET /api/executions/' + execution_id, 'terminal': value})
                assert value['status'] == status, f'执行终态 {value["status"]}，预期 {status}: {self.safe(value)}'
                node = next((n for n in self.nodes if n['id'] == value['node_id']), None)
                if not node:
                    raise Blocked('缺少该执行目标客户机配置')
                for attempt in range(5):
                    local = self.journal(node, execution_id)
                    if local['synced']:
                        break
                    time.sleep(1)
                assert local['status'] == value['status'], 'Hub/Agent 终态不一致'
                assert local['synced'], 'Agent 未收到成功 ACK，Journal 未同步'
                return value
            time.sleep(1)
        raise AssertionError('执行未在限定时间内进入终态: ' + execution_id)

    def run_task(self, task, status='SUCCESS', params=None, token=None):
        values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {'params': params or {}}, token=token)
        assert values, '未生成真实 Execution'
        for value in values:
            self.remember('execution', value)
        return [self.wait_execution(value['id'], status) for value in values]

    def path(self, suffix=''):
        return '/tmp/' + self.prefix + ('/' + suffix if suffix else '')

    def check_file(self, node, path, expected=None, absent=False):
        command = ('test ! -e ' if absent else 'cat -- ') + shlex.quote(path) + '\n'
        output = self.remote(node, command)
        if expected is not None:
            assert output == expected, f'客户机文件内容错误: {output!r}'

    def normal_case(self, name):
        if name == 'api.public-auth':
            self.api('GET', '/healthz', token='')
            self.api('GET', '/readyz', token='')
            self.api('GET', '/api/oidc/state', token='')
            self.api('HEAD', '/api/agent/binary?architecture=amd64', token='', expected=(200, 503))
            self.api('GET', '/api/agent/binary?architecture=invalid', token='', expected=(400,))
            for path in ['/api/nodes', '/api/groups', '/api/scripts', '/api/tasks', '/api/schedules', '/api/artifacts', '/api/applications', '/api/executions', '/api/settings', '/api/audit', '/api/users', '/api/me', '/metrics']:
                self.api('GET', path, token='', expected=(401,))
        elif name == 'api.read-lists':
            for path in ['/api/nodes', '/api/groups', '/api/scripts', '/api/tasks', '/api/schedules', '/api/artifacts', '/api/applications', '/api/executions?limit=3&offset=0', '/api/settings', '/api/audit?limit=3&offset=0', '/api/users', '/api/me', '/metrics']:
                self.api('GET', path)
            values, _, _ = self.api('GET', '/api/nodes')
            found = {n['id']: n for n in values}
            for node in self.nodes:
                assert node['id'] in found and found[node['id']]['status'] == 'online'
                assert found[node['id']].get('inventory'), '真实 Inventory 为空'
                self.remote(node, 'uname -s; uname -m; hostname\n')
        elif name == 'rbac.roles':
            if not self.cfg.get('roles'):
                raise Blocked('缺少本轮专用 Operator/Viewer 账号')
            for role in ['operator', 'viewer']:
                login = self.cfg['roles'].get(role)
                if not login:
                    raise Blocked('缺少角色账号: ' + role)
                result, _, _ = self.api('POST', '/api/login', {'username': login['username'], 'password': login['password']}, token='')
                token = result['token']; self.secrets.append(token)
                value, _, _ = self.api('GET', '/api/me', token=token)
                assert value.get('role', value.get('Role')) == role
                self.api('GET', '/api/nodes', token=token)
                self.api('GET', '/api/users', token=token, expected=(403,))
                for path in ['/api/scripts', '/api/tasks', '/api/groups', '/api/artifacts', '/api/applications', '/api/transfers']:
                    self.api('POST', path, {}, token=token, expected=(403,))
                if role == 'viewer':
                    self.api('POST', '/api/tasks/' + str(uuid.uuid4()) + '/run', {}, token=token, expected=(403,))
                self.api('POST', '/api/logout', {}, token=token)
                self.api('GET', '/api/me', token=token, expected=(401,))
        elif name == 'feature.users.management':
            name = self.prefix + '-viewer-' + uuid.uuid4().hex[:6]
            old, new = 'QaOld-' + uuid.uuid4().hex, 'QaNew-' + uuid.uuid4().hex
            self.secrets.extend([old, new])
            user = self.create('user', {'username': name, 'password': old, 'role': 'viewer'})
            login, _, _ = self.api('POST', '/api/login', {'username': name, 'password': old}, token='')
            self.secrets.append(login['token'])
            self.api('GET', '/api/nodes', token=login['token'])
            self.api('PUT', '/api/users/' + user['id'], {'password': new})
            self.api('POST', '/api/login', {'username': name, 'password': old}, token='', expected=(401,))
            login, _, _ = self.api('POST', '/api/login', {'username': name, 'password': new}, token='')
            self.secrets.append(login['token'])
            self.api('GET', '/api/nodes', token=login['token'])
            self.api('PUT', '/api/users/' + user['id'], {'role': 'viewer'})
            self.api('POST', '/api/logout', {}, token=login['token'])
            next(row for row in self.ledger if row['id'] == user['id'])['status'] = 'retained-viewer-no-delete-api'
            self.save(self.ledger_path, self.ledger)
        elif name.startswith('script.'):
            interpreter = name.split('.')[1]
            marker = 'script-' + interpreter
            content = 'print("' + marker + '")\n' if interpreter == 'python' else 'printf "' + marker + '\\n"\n'
            script = self.create('script', {'name': self.prefix + '-' + interpreter + '-' + uuid.uuid4().hex[:6], 'interpreter': interpreter, 'content': content, 'enabled': True, 'timeout': 10, 'parameters': []})
            assert script['sha256'] == hashlib.sha256(content.encode()).hexdigest()
            task = self.task(interpreter, type='script', script_id=script['id'])
            result = self.run_task(task)[0]
            assert marker in result['stdout'] and result.get('script_revision') == script['revision']
            self.api('DELETE', '/api/scripts/' + script['id'], expected=(409,))
            script['content'] = content + ('print("revision-two")\n' if interpreter == 'python' else 'printf "revision-two\\n"\n')
            updated, _, _ = self.api('PUT', '/api/scripts/' + script['id'], script)
            assert updated['revision'] > script['revision']
            revisions, _, _ = self.api('GET', '/api/scripts/' + script['id'] + '/revisions')
            assert len(revisions) >= 2
            result = self.run_task(task)[0]
            assert 'revision-two' in result['stdout'] and result['script_revision'] == updated['revision']
            updated['enabled'] = False
            self.api('PUT', '/api/scripts/' + script['id'], updated)
            self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, expected=(400,))
        elif name == 'task.success':
            path = self.path('success')
            task = self.task('success', 'mkdir -p ' + shlex.quote(self.path()) + '; printf real-success > ' + shlex.quote(path) + '; printf stdout-marker; printf stderr-marker >&2')
            result = self.run_task(task)[0]
            assert result.get('exit_code', 0) == 0 and 'stdout-marker' in result['stdout'] and 'stderr-marker' in result['stderr']
            self.check_file(self.node(), path, 'real-success')
            self.api('GET', '/api/executions/' + result['id'] + '/logs')
        elif name == 'feature.task.operator-run':
            if not self.cfg.get('roles'): raise Blocked('缺少专用角色账号')
            tokens = {}
            for role in ['operator', 'viewer']:
                login = self.cfg['roles'][role]
                value, _, _ = self.api('POST', '/api/login', {'username': login['username'], 'password': login['password']}, token='')
                tokens[role] = value['token']; self.secrets.append(value['token'])
            path = self.path('operator-effect')
            task = self.task('operator', 'mkdir -p ' + shlex.quote(self.path()) + '; printf operator-real > ' + shlex.quote(path))
            self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, token=tokens['viewer'], expected=(403,))
            self.run_task(task, token=tokens['operator'])
            self.check_file(self.node(), path, 'operator-real')
        elif name == 'task.parameters-secrets':
            secret = 'qa-secret-' + uuid.uuid4().hex
            self.secrets.append(secret)
            task = self.task('params', 'printf "msg=%s secret=%s" "$NODESTEER_PARAM_msg" "$NODESTEER_PARAM_password"', parameters=[{'name': 'msg', 'type': 'string', 'default': 'default', 'required': True}, {'name': 'password', 'type': 'secret', 'required': True}])
            self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, expected=(400,))
            result = self.run_task(task, params={'msg': 'runtime-override', 'password': secret})[0]
            assert 'runtime-override' in result['stdout'] and secret not in result['stdout'] + result['stderr']
            chunks, _, _ = self.api('GET', '/api/executions/' + result['id'] + '/logs')
            assert secret not in json.dumps(chunks)
        elif name == 'task.retry':
            path = self.path('retry-count-' + uuid.uuid4().hex[:6])
            command = 'mkdir -p ' + shlex.quote(self.path()) + '; n=0; test ! -f ' + shlex.quote(path) + ' || n=$(cat ' + shlex.quote(path) + '); n=$((n+1)); printf %s "$n" > ' + shlex.quote(path) + '; test "$n" -ge 3'
            self.run_task(self.task('retry', command, retry=2))
            self.check_file(self.node(), path, '3')
        elif name == 'task.manual-intents':
            path = self.path('manual-count-' + uuid.uuid4().hex[:6])
            command = 'mkdir -p ' + shlex.quote(self.path()) + '; n=0; test ! -f ' + shlex.quote(path) + ' || n=$(cat ' + shlex.quote(path) + '); n=$((n+1)); printf %s "$n" > ' + shlex.quote(path)
            task = self.task('manual-intents', command)
            first, second = self.run_task(task)[0], self.run_task(task)[0]
            assert first['id'] != second['id'], '独立Manual Run应生成新的Execution意图'
            self.check_file(self.node(), path, '2')
        elif name == 'task.failed-exit':
            result = self.run_task(self.task('failed-exit', 'printf real-failure >&2; exit 7'), 'FAILED')[0]
            assert result['exit_code'] == 7 and 'real-failure' in result['stderr']
        elif name == 'task.cwd-env-user':
            self.run_task(self.task('cwd-prepare', 'mkdir -p ' + shlex.quote(self.path())))
            script = self.create('script', {'name': self.prefix + '-cwd-' + uuid.uuid4().hex[:6], 'interpreter': 'shell', 'content': 'id -un; pwd; printf "%s\\n" "$QA_TEST_ENV"', 'working_dir': self.path(), 'run_user': 'nobody', 'environment': {'QA_TEST_ENV': 'real-env'}, 'enabled': True, 'timeout': 5})
            result = self.run_task(self.task('cwd', type='script', script_id=script['id']))[0]
            assert result['stdout'].splitlines() == ['nobody', self.path(), 'real-env']
        elif name == 'task.secret-split-stream':
            secret = 'qa-split-secret-' + uuid.uuid4().hex
            self.secrets.append(secret)
            code = 'import os,sys,time; v=os.environ["NODESTEER_PARAM_password"]; sys.stdout.write(v[:len(v)//2]); sys.stdout.flush(); time.sleep(1); sys.stdout.write(v[len(v)//2:]); sys.stdout.flush()'
            task = self.task('secret-split', 'python3 -c ' + shlex.quote(code), parameters=[{'name': 'password', 'type': 'secret', 'required': True}])
            result = self.run_task(task, params={'password': secret})[0]
            chunks, _, _ = self.api('GET', '/api/executions/' + result['id'] + '/logs')
            stdout = ''.join(c.get('chunk', c.get('data', '')) for c in chunks if c.get('stream') == 'stdout')
            assert secret not in result['stdout'] and secret not in stdout, 'secret跨实时分块重组后明文泄漏'
        elif name == 'task.log-truncation':
            settings, _, _ = self.api('GET', '/api/settings')
            limit = int(settings.get('max_log_bytes', 1048576))
            if limit > 4 * 1048576: raise Blocked('primary日志限制大于4MiB；不改共享设置，等待隔离环境')
            code = 'import sys; sys.stdout.write("A"*' + str(limit + 65536) + '); sys.stderr.write("B"*' + str(limit + 65536) + ')'
            result = self.run_task(self.task('log-truncation', 'python3 -c ' + shlex.quote(code)))[0]
            assert result['stdout_truncated'] and result['stderr_truncated']
            assert len(result['stdout']) <= limit and len(result['stderr']) <= limit
        elif name == 'task.timeout':
            self.run_task(self.task('timeout', 'sleep 30', timeout=1), 'TIMED_OUT')
        elif name == 'task.cancel':
            task = self.task('cancel', 'sleep 120', timeout=130, retry=2)
            values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
            ex = self.remember('execution', values[0]); time.sleep(2)
            self.api('POST', '/api/executions/' + ex['id'] + '/cancel', {})
            self.wait_execution(ex['id'], 'CANCELED')
        elif name == 'feature.process.native':
            node = self.node()
            assert node['id'] == '56765828-4896-492b-b562-64afdc0fb8d6'
            for action in ['cancel', 'timeout']:
                directory = self.path('native-pg-' + action + '-' + uuid.uuid4().hex[:6])
                inner = 'trap "" TERM; sleep 120 & printf %s "$!" > ' + shlex.quote(directory + '/child') + '; wait'
                command = 'mkdir -p ' + shlex.quote(directory) + '; sh -c ' + shlex.quote(inner) + ' & child=$!; printf %s "$child" > ' + shlex.quote(directory + '/parent') + '; wait "$child"'
                task = self.task('native-pg-' + action, command, timeout=4 if action == 'timeout' else 130)
                values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
                execution = self.remember('execution', values[0]); time.sleep(1)
                if action == 'cancel': self.api('POST', '/api/executions/' + execution['id'] + '/cancel', {})
                self.wait_execution(execution['id'], 'CANCELED' if action == 'cancel' else 'TIMED_OUT')
                pids = [int(self.remote(node, 'cat ' + shlex.quote(directory + '/' + part) + '\n')) for part in ['parent', 'child']]
                command = '; '.join('if test -r /proc/' + str(pid) + '/stat; then cat /proc/' + str(pid) + '/stat; fi' for pid in pids)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    remaining = self.remote(node, command + '\n')
                    if not remaining: break
                    time.sleep(1)
                self.events.append({'native_process_group_effect': {'action': action, 'pids': pids, 'remaining_proc_stat': remaining}})
                assert not remaining, 'Native后代PID未被init收割'
        elif name == 'task.disabled-invalid':
            task = self.task('disabled', 'printf must-not-run', enabled=False)
            self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, expected=(400,))
            task['enabled'] = True; task['timeout'] = -1
            self.api('PUT', '/api/tasks/' + task['id'], task, expected=(400,))
            task['timeout'] = 5; task['parameters'] = [{'name': 'duplicate'}, {'name': 'duplicate'}]
            self.api('PUT', '/api/tasks/' + task['id'], task, expected=(400,))
        elif name == 'target.static-group':
            ids = [n['id'] for n in self.nodes]
            if not ids: raise Blocked('缺少真实 Agent')
            group = self.create('group', {'name': self.prefix + '-group-' + uuid.uuid4().hex[:6], 'type': 'static', 'members': ids})
            task = self.task('group', 'printf group-target', target={'type': 'group', 'group_ids': [group['id']]})
            results = self.run_task(task)
            assert {e['node_id'] for e in results} == set(ids)
            self.api('DELETE', '/api/groups/' + group['id'], expected=(409,))
        elif name == 'target.multiple-nodes':
            ids = [n['id'] for n in self.nodes]
            if len(ids) < 2: raise Blocked('缺少多节点')
            results = self.run_task(self.task('multiple-nodes', 'printf explicit-multiple', node_ids=ids))
            assert {e['node_id'] for e in results} == set(ids)
        elif name == 'feature.node.labels':
            node = next((n for n in self.nodes if n['id'] == 'd77e2440-919f-4431-a488-733817dcd809'), None)
            if not node: raise Blocked('标签串行窗口仅授权Agent2')
            value, _, _ = self.api('GET', '/api/nodes/' + node['id'])
            original = dict(value.get('labels') or {})
            key = self.prefix
            assert key not in original, '本轮标签key已有，需先确认归属'
            try:
                self.api('POST', '/api/nodes/' + node['id'], {'labels': dict(original, **{key: 'test'})})
                group = self.create('group', {'name': self.prefix + '-label-' + uuid.uuid4().hex[:6], 'type': 'label', 'label_key': key, 'label_value': 'test'})
                tasks = [self.task('label-group', 'printf label-group', target={'type': 'group', 'group_ids': [group['id']]}), self.task('label-direct', 'printf label-direct', target={'type': 'label', 'label_key': key, 'label_value': 'test'})]
                for task in tasks:
                    results = self.run_task(task)
                    assert len(results) == 1 and results[0]['node_id'] == node['id']
                self.api('POST', '/api/nodes/' + node['id'], {'labels': original})
                for task in tasks: self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, expected=(400,))
                script = "import sqlite3,json,sys; c=sqlite3.connect('file:'+sys.argv[1]+'/state.db?mode=ro',uri=True); print(json.dumps(c.execute('select id from tasks where id in (?,?)',sys.argv[2:]).fetchall()))"
                command = 'python3 -c ' + shlex.quote(script) + ' ' + shlex.quote(node['data_dir']) + ' ' + ' '.join(shlex.quote(t['id']) for t in tasks) + '\n'
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    rows = json.loads(self.remote(node, command))
                    if not rows: break
                    time.sleep(1)
                assert not rows, '标签目标移除后Agent本地旧任务未收敛删除'
            finally:
                self.api('POST', '/api/nodes/' + node['id'], {'labels': original})
                value, _, _ = self.api('GET', '/api/nodes/' + node['id'])
                assert (value.get('labels') or {}) == original, 'Agent2原labels未恢复'
        elif name.startswith('condition.'):
            metric = name.split('.')[1]
            local = {'metric': metric, 'operator': '>=', 'value': '0'}
            if metric == 'file_exists': local = {'metric': metric, 'operator': '==', 'value': 'false', 'path': self.path('missing-' + uuid.uuid4().hex)}
            if metric == 'dir_exists': local = {'metric': metric, 'operator': '==', 'value': 'true', 'path': '/tmp'}
            if metric == 'disk_usage': local['path'] = '/tmp'
            if metric == 'process_exists': local = {'metric': metric, 'operator': '==', 'value': 'true', 'path': 'systemd'}
            if metric == 'port_listening': local = {'metric': metric, 'operator': '==', 'value': 'true', 'path': str(self.node().get('guest_ssh_port', 22))}
            if metric == 'command_result': local = {'metric': metric, 'operator': '==', 'value': 'condition-ok', 'command': 'printf condition-ok'}
            if metric == 'false': local = {'metric': 'command_result', 'operator': '==', 'value': 'true', 'command': 'printf false'}
            if metric == 'remote-unknown':
                self.node(1)
                unused = self.task('no-history', 'printf unused')
                condition = {'type': 'remote', 'remote': {'node_id': self.node(1)['id'], 'property': 'last_execution', 'task_id': unused['id'], 'operator': '==', 'value': 'SUCCESS'}}
            else: condition = {'type': 'local', 'local': local}
            path = self.path('condition-' + metric)
            task = self.task(metric, 'mkdir -p ' + shlex.quote(self.path()) + '; printf condition-ok > ' + shlex.quote(path), condition=condition)
            status = 'BLOCKED' if metric == 'remote-unknown' else 'SKIPPED' if metric == 'false' else 'SUCCESS'
            result = self.run_task(task, status)[0]
            self.check_file(self.node(), path, 'condition-ok' if status == 'SUCCESS' else None, absent=status != 'SUCCESS')
            if status != 'SUCCESS': assert result.get('block_reason'), '跳过/阻塞原因未回传'
        elif name.startswith('schedule.'):
            _, kind, owner = name.split('.')
            task = self.task('schedule-' + kind + '-' + owner, 'printf schedule-real')
            schedule = {'task_id': task['id'], 'type': kind, 'timezone': 'Asia/Shanghai', 'execution_owner': owner, 'offline_policy': 'allow_offline' if owner == 'agent' else 'hub_online_required', 'misfire_policy': 'run_once', 'enabled': True}
            if kind == 'one_time': schedule['run_at'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=12)).isoformat()
            if kind == 'interval': schedule['interval_sec'] = 10
            if kind == 'cron': schedule['expression'] = '* * * * *'
            created = self.create('schedule', schedule)
            self.api('DELETE', '/api/tasks/' + task['id'], expected=(409,))
            deadline = time.monotonic() + 95
            while time.monotonic() < deadline:
                values, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'], trace=False)
                if values: break
                time.sleep(2)
            assert values, '调度没有真实触发'
            result = self.wait_execution(values[0]['id'])
            assert result['trigger_type'] == 'schedule' and result.get('scheduled_time')
            assert 'schedule-real' in result['stdout']
            self.remember('execution', result)
            self.api('DELETE', '/api/schedules/' + created['id'])
            next(r for r in self.ledger if r['id'] == created['id'])['status'] = 'deleted'
            self.save(self.ledger_path, self.ledger)
        elif name == 'transfer.success-source-failure':
            source, target = self.node(), self.node(1)
            source_path, target_path = self.path('transfer-source'), self.path('transfer-target')
            self.run_task(self.task('file-source', 'mkdir -p ' + shlex.quote(self.path()) + '; printf transfer-real > ' + shlex.quote(source_path)))
            body = {'source_node_id': source['id'], 'source_path': source_path, 'targets': [{'node_id': target['id'], 'destination_path': target_path}]}
            transfer = self.create('transfer', body)
            self.wait_transfer(transfer['id'], 'SUCCESS')
            self.check_file(target, target_path, 'transfer-real')
            body['source_path'] = self.path('never-created-' + uuid.uuid4().hex)
            failed = self.create('transfer', body)
            result = self.wait_transfer(failed['id'], 'FAILED')
            assert result.get('error') and all(t['status'] == 'FAILED' for t in result['targets'])
            self.api('POST', '/api/transfers/' + failed['id'] + '/cancel', {})
            self.wait_transfer(failed['id'], 'CANCELED')
        elif name == 'feature.transfer.multitarget-retry':
            source, target, other = self.node(), self.node(1), self.node(2)
            suffix = uuid.uuid4().hex[:6]
            source_path, blocker, good = self.path('src-' + suffix), self.path('block-' + suffix), self.path('good-' + suffix)
            self.run_task(self.task('relay-source', 'mkdir -p ' + shlex.quote(self.path()) + '; printf retry-relay-real > ' + shlex.quote(source_path)))
            self.run_task(self.task('relay-blocker', 'mkdir -p ' + shlex.quote(self.path()) + '; printf blocker > ' + shlex.quote(blocker), node_ids=[target['id']]))
            transfer = self.create('transfer', {'source_node_id': source['id'], 'source_path': source_path, 'targets': [{'node_id': target['id'], 'destination_path': blocker + '/received'}, {'node_id': other['id'], 'destination_path': good}]})
            failed = self.wait_transfer(transfer['id'], 'FAILED')
            assert failed['sha256'] == hashlib.sha256(b'retry-relay-real').hexdigest()
            states = {t['node_id']: t for t in failed['targets']}
            assert states[target['id']]['status'] == 'FAILED' and states[target['id']].get('error')
            assert states[other['id']]['status'] == 'SUCCESS'
            self.check_file(other, good, 'retry-relay-real')
            self.run_task(self.task('relay-fix', 'rm -- ' + shlex.quote(blocker) + '; mkdir ' + shlex.quote(blocker), node_ids=[target['id']]))
            self.api('POST', '/api/transfers/' + transfer['id'] + '/retry?target_id=' + target['id'], {})
            self.wait_transfer(transfer['id'], 'SUCCESS')
            self.check_file(target, blocker + '/received', 'retry-relay-real')
            self.check_file(other, good, 'retry-relay-real')
        elif name == 'artifact.upload-download':
            payload = b'qa-artifact-real-' + uuid.uuid4().hex.encode()
            artifact = self.upload_artifact(payload)
            value, headers, downloaded = self.api('GET', '/api/artifacts/' + artifact['id'] + '/download')
            assert downloaded == payload and artifact['sha256'] == hashlib.sha256(payload).hexdigest()
            assert int(artifact['size']) == len(payload)
            self.api('GET', '/api/artifacts/' + artifact['id'] + '/download', token='', expected=(401,))
        elif name in {'feature.application.lifecycle', 'feature.application.operator-deploy'}:
            self.application_lifecycle(first_as_operator=name.endswith('operator-deploy'))
        elif name == 'api.missing-contract':
            for kind in ['scripts', 'tasks', 'schedules', 'groups', 'applications', 'artifacts']:
                missing = str(uuid.uuid4())
                self.api('GET', '/api/' + kind + '/' + missing, expected=(404,))
                self.api('DELETE', '/api/' + kind + '/' + missing, expected=(200,))
        elif name == 'audit.execution-actions':
            values, headers, _ = self.api('GET', '/api/audit?limit=1000')
            ids = {r['id'] for r in self.ledger}
            owned = [v for v in values if v.get('resource_id') in ids]
            assert owned, '缺少本轮资源审计'
            assert any(v.get('action') == 'execute' for v in owned), '缺少本轮任务运行审计'
            assert any(v.get('action') == 'cancel' for v in owned), '缺少本轮执行取消审计'
            assert 'X-Total-Count' in headers, '审计分页缺少总数'
        else:
            raise Blocked('该用例需要根代理串行协调故障/共享节点/服务操作，不能在 normal 阶段自行扰动')

    def upload_artifact(self, payload):
        boundary = 'qa' + uuid.uuid4().hex
        body = b''
        for key, value in {'name': self.prefix + '-artifact-' + uuid.uuid4().hex[:6], 'version': 'qa-v1', 'architecture': self.node().get('arch', 'amd64')}.items():
            body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + value + '\r\n').encode()
        body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="qa-payload.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + payload + ('\r\n--' + boundary + '--\r\n').encode()
        value, _, _ = self.api('POST', '/api/artifacts', raw=body, headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        return self.remember('artifact', value)

    def wait_transfer(self, transfer_id, status, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value, _, _ = self.api('GET', '/api/transfers/' + transfer_id, trace=False)
            if value['status'] in {'SUCCESS', 'FAILED', 'CANCELED'}:
                self.events.append({'api': 'GET /api/transfers/' + transfer_id, 'terminal': value})
                assert value['status'] == status, str(self.safe(value))
                return value
            time.sleep(1)
        raise AssertionError('传输未终结: ' + transfer_id)

    def application_lifecycle(self, first_as_operator=False):
        if not self.services: raise Blocked('本轮新登记Unit需根代理--allow-services串行窗口')
        node = self.node()
        if node['id'] != '56765828-4896-492b-b562-64afdc0fb8d6':
            raise Blocked('服务窗口仅授权Agent1')
        suffix = uuid.uuid4().hex[:8]
        unit = self.prefix + '-app-' + suffix + '.service'
        directory = self.path('app-' + suffix)
        binary, config = directory + '/app-bin', directory + '/app.conf'
        fixture = base64.b64decode(self.remote(node, "python3 -c 'import base64; print(base64.b64encode(open(\"/bin/sh\",\"rb\").read()).decode())'\n"))
        port = int(self.remote(node, "python3 -c 'import socket; s=socket.socket(); s.bind((\"127.0.0.1\",0)); print(s.getsockname()[1]); s.close()'\n"))
        artifact = self.upload_artifact(fixture)
        application = self.create('application', {'name': self.prefix + '-app-' + suffix, 'version': 'qa-v1', 'artifact_id': artifact['id'], 'binary_path': binary, 'config_path': config, 'config': 'config-v1\n', 'unit_name': unit, 'arguments': ['-c', f'exec python3 -m http.server {port} --bind 127.0.0.1 --directory /tmp'], 'environment': {'QA_RUN': RUN_ID}, 'health_check': {'type': 'systemd', 'timeout': 2, 'attempts': 5, 'interval': 1}})
        self.api('POST', '/api/applications/' + application['id'] + '/assign', {'node_ids': [node['id']]})

        def deploy(operation='deploy', expected='SUCCESS', token=None):
            values, _, _ = self.api('POST', '/api/applications/' + application['id'] + '/deploy', {'node_ids': [node['id']], 'operation': operation}, token=token)
            assert len(values) == 1
            self.remember('execution', values[0])
            return self.wait_execution(values[0]['id'], expected)

        def service(expected):
            actual = self.remote(node, 'systemctl is-active ' + shlex.quote(unit) + ' || true\n')
            assert actual == expected, f'Unit真实状态 {actual}，预期 {expected}'

        initial_token = None
        if first_as_operator:
            account = self.cfg['roles']['operator']
            login, _, _ = self.api('POST', '/api/login', {'username': account['username'], 'password': account['password']}, token='')
            initial_token = login['token']; self.secrets.append(initial_token)
        deploy(token=initial_token); service('active'); self.check_file(node, config, 'config-v1')
        deployed_sha = self.remote(node, 'sha256sum ' + shlex.quote(binary) + '\n').split()[0]
        assert deployed_sha == artifact['sha256']
        unit_text = self.remote(node, 'systemctl cat ' + shlex.quote(unit) + '\n')
        assert binary in unit_text and str(port) in unit_text and RUN_ID in unit_text
        self.api('GET', '/api/applications/' + application['id'] + '/state')
        self.api('GET', '/api/applications/' + application['id'] + '/executions')
        for kind, target in [('tcp', '127.0.0.1:' + str(port)), ('http', 'http://127.0.0.1:' + str(port) + '/'), ('command', 'test "$(cat ' + shlex.quote(config) + ')" = config-v1')]:
            application['health_check'] = {'type': kind, 'target': target, 'timeout': 2, 'attempts': 5, 'interval': 1}
            application, _, _ = self.api('PUT', '/api/applications/' + application['id'], application)
            deploy('upgrade'); service('active')
        deploy('stop'); service('inactive')
        deploy('start'); service('active')
        deploy('restart'); service('active')
        application.update(version='qa-v2', config='config-v2\n', health_check={'type': 'systemd', 'timeout': 2, 'attempts': 5, 'interval': 1})
        application, _, _ = self.api('PUT', '/api/applications/' + application['id'], application)
        self.check_file(node, config, 'config-v1')
        deploy('upgrade'); self.check_file(node, config, 'config-v2'); service('active')
        revisions, _, _ = self.api('GET', '/api/applications/' + application['id'] + '/revisions')
        assert len(revisions) >= 5
        task = self.task('app-operation', type='app_operation', application_id=application['id'], app_operation='restart')
        self.run_task(task); service('active')
        deployment_task = self.task('app-deploy-task', type='app_deploy', application_id=application['id'], app_operation='deploy')
        self.run_task(deployment_task); service('active')
        scheduled_task = self.task('app-scheduled-operation', type='app_operation', application_id=application['id'], app_operation='restart')
        schedule = self.create('schedule', {'task_id': scheduled_task['id'], 'type': 'one_time', 'run_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)).isoformat(), 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'run_once', 'enabled': True})
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            executions, _, _ = self.api('GET', '/api/executions?task_id=' + scheduled_task['id'], trace=False)
            if executions: break
            time.sleep(1)
        assert executions, '计划应用操作未触发'
        self.wait_execution(executions[0]['id']); service('active')
        self.remember('execution', executions[0])
        self.api('DELETE', '/api/schedules/' + schedule['id'])
        next(row for row in self.ledger if row['id'] == schedule['id'])['status'] = 'deleted'
        if self.cfg.get('roles'):
            role_tokens = {}
            for role in ['operator', 'viewer']:
                account = self.cfg['roles'][role]
                value, _, _ = self.api('POST', '/api/login', {'username': account['username'], 'password': account['password']}, token='')
                role_tokens[role] = value['token']; self.secrets.append(value['token'])
            self.api('POST', '/api/applications/' + application['id'] + '/deploy', {'node_ids': [node['id']], 'operation': 'restart'}, token=role_tokens['viewer'], expected=(403,))
            deploy('restart', token=role_tokens['operator']); service('active')
        application.update(version='qa-v3-fails', config='config-v3\n', arguments=['-c', 'exit 17'])
        application, _, _ = self.api('PUT', '/api/applications/' + application['id'], application)
        failed = deploy('upgrade', 'FAILED')
        assert failed.get('block_reason'), '回滚失败的Execution缺少原因'
        self.check_file(node, config, 'config-v2'); service('active')
        self.api('GET', '/api/applications/' + application['id'] + '/state')
        for t in [task, deployment_task, scheduled_task]: self.api('DELETE', '/api/tasks/' + t['id'])
        self.api('DELETE', '/api/applications/' + application['id'])
        for row in self.ledger:
            if row['id'] in {task['id'], deployment_task['id'], scheduled_task['id'], application['id']}: row['status'] = 'deleted'
        self.save(self.ledger_path, self.ledger)
        for path in [binary, config, '/etc/systemd/system/' + unit]: self.check_file(node, path, absent=True)
        service('inactive')

    def execute(self, case):
        self.events = []
        start = dt.datetime.now(dt.timezone.utc).isoformat()
        result = {'id': case['id'], 'name': case['name'], 'started_at': start, 'hub_url': self.base, 'ui_coverage': '由 frontend 单元另行核验；本 runner 仅真实 API+Agent 客户机链路', 'layer': 'real-api-agent', 'status': 'pending'}
        try:
            self.normal_case(case['id'])
            result['status'] = 'passed'
        except Blocked as exc:
            result.update(status='blocked', reason=str(exc))
        except Exception as exc:
            result.update(status='failed', reason=self.safe(str(exc)), traceback=self.safe(traceback.format_exc()))
        result['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        evidence_file = self.evidence / (case['id'] + '-' + uuid.uuid4().hex[:8] + '.json')
        result['evidence'] = str(evidence_file.relative_to(HERE.parent))
        self.save(evidence_file, {'result': result, 'events': self.events})
        with (HERE / 'results.jsonl').open('a') as stream:
            stream.write(json.dumps(self.safe(result), ensure_ascii=False) + '\n')
        inventory_file = HERE / 'case-inventory.json'
        inventory = json.loads(inventory_file.read_text())
        stored = next(c for c in inventory['cases'] if c['id'] == case['id'])
        stored.update(status=result['status'], last_result=result)
        stored.setdefault('attempts', []).append(result)
        self.save(inventory_file, inventory)
        print(result['id'], result['status'], self.safe(result.get('reason', '')), flush=True)
        return result

    def cleanup(self):
        self.token = ''; self.login()
        for kind in ['schedule', 'task', 'application', 'artifact', 'group', 'script']:
            for row in self.ledger:
                if row['type'] != kind or row['status'] != 'created': continue
                if kind == 'application' and not self.services:
                    raise Blocked('清理托管应用会停止并移除本轮服务，须根代理 --allow-services 门禁')
                try:
                    self.api('DELETE', '/api/' + kind + 's/' + row['id'], expected=(200, 404))
                    row['status'] = 'deleted'
                except Exception as exc:
                    row['cleanup_error'] = self.safe(str(exc))
                self.save(self.ledger_path, self.ledger)
        for row in self.ledger:
            if row['type'] in {'execution', 'transfer'} and row['status'] == 'created': row['status'] = 'retained-history'
        self.save(self.ledger_path, self.ledger)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True, help='私密 JSON 路径，不接受命令行凭据')
    parser.add_argument('--suite', choices=['read', 'normal', 'coordinated'], default='read')
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--allow-mutations', action='store_true')
    parser.add_argument('--allow-services', action='store_true')
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    assert args.config.stat().st_mode & 0o077 == 0, '凭据配置必须 chmod 600'
    runner = Runner(json.loads(args.config.read_text()), args.allow_mutations, args.allow_services)
    accounts = args.config.parent / 'main-accounts.json'
    if accounts.exists():
        runner.cfg['roles'] = json.loads(accounts.read_text())
        for login in runner.cfg['roles'].values():
            runner.secrets.extend(str(login[k]) for k in ['password', 'token'] if login.get(k))
    inventory = json.loads((HERE / 'case-inventory.json').read_text())['cases']
    if args.cleanup:
        runner.cleanup(); return
    if args.allow_mutations: runner.token = ''
    if args.allow_mutations or runner.token:
        runner.login()
        runner.resolve_nodes()
    chosen = [c for c in inventory if c['phase'] == args.suite and (not args.case or c['id'] in args.case)]
    assert chosen, '没有匹配的用例'
    results = [runner.execute(c) for c in chosen]
    runner.save(HERE / 'latest-summary.json', {'run_id': RUN_ID, 'suite': args.suite, 'counts': {s: sum(r['status'] == s for r in results) for s in ['passed', 'failed', 'blocked']}, 'results': results})
    raise SystemExit(1 if any(r['status'] == 'failed' for r in results) else 0)


if __name__ == '__main__':
    main()
