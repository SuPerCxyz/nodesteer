#!/usr/bin/env python3
"""仅操作本轮 core Docker Hub/Agent；原卷保留、协议扰动独占代理。"""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time
import uuid
import urllib.error

from runner import Runner, Blocked, HERE, RUN_ID


class IsolatedRunner(Runner):
    def __init__(self, config, hub_faults=False):
        super().__init__(config, mutations=True)
        self.home = HERE / 'isolated'
        self.home.mkdir(exist_ok=True)
        self.ledger_path = self.home / 'ledger.json'
        self.ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else []
        self.evidence = self.home / 'evidence'
        self.evidence.mkdir(exist_ok=True)
        self.hub_faults = hub_faults
        self.agent = config['agent_container']
        self.hub_container = config['hub_container']
        self.volume = config['agent_volume']
        core = next(node for node in config['nodes'] if node.get('docker_container') == self.agent)
        self.core_node_id = core['id']
        assert self.core_node_id == 'd940ab68-9f29-434b-bfe8-5d075a519edc', 'core专属Node授权边界变化'
        self.nodes = [core]
        assert self.agent.startswith('qa-' + RUN_ID + '-env-')
        assert self.hub_container.startswith('qa-' + RUN_ID + '-env-')
        assert self.volume.startswith('qa-' + RUN_ID + '-env-')
        self.events = []
        self.spec = json.loads(self.control('docker inspect ' + shlex.quote(self.agent), record=False))[0]
        for entry in self.spec['Config'].get('Env', []):
            key, _, value = entry.partition('=')
            if any(word in key.upper() for word in ['TOKEN', 'PASSWORD', 'CREDENTIAL']): self.secrets.append(value)
        self.token = ''
        self.login()
        self.resolve_nodes()

    def resolve_nodes(self):
        value, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id, trace=False)
        self.nodes[0].update(name=value['hostname'], arch=value['arch'], mode=value['deployment_mode'])

    def task(self, label, command='', node_ids=None, **extra):
        assert node_ids is None or set(node_ids) == {self.core_node_id}, 'core runner禁止分派额外Env Agent'
        return super().task(label, command, [self.core_node_id], **extra)

    def checkpoint(self, kind, **entry):
        if not hasattr(self, 'home'): return
        with (self.home / 'progress.jsonl').open('a') as stream:
            stream.write(json.dumps(self.safe({'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'kind': kind, **entry}), ensure_ascii=False) + '\n')

    def api(self, method, path, *args, **kwargs):
        self.checkpoint('api-start', method=method, path=path)
        result = super().api(method, path, *args, **kwargs)
        self.checkpoint('api-end', method=method, path=path)
        return result

    def control(self, command, record=True):
        argv = ['ssh', '-F', self.cfg.get('ssh_config', '/home/superc/.ssh/config'), '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR', '-p', str(self.cfg.get('ssh_port', 22)), '-i', self.cfg['ssh_identity_file'], self.cfg['ssh_target'], 'sh -s']
        if record: self.checkpoint('control-start', command=command)
        result = subprocess.run(argv, input=command + '\n', text=True, capture_output=True, timeout=80)
        if record: self.checkpoint('control-end', command=command, exit_code=result.returncode)
        if record: self.events.append({'control': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        assert result.returncode == 0, f'本轮隔离控制失败: {self.safe(result.stderr)}'
        return result.stdout.strip()

    def execute(self, case):
        self.checkpoint('case-start', case_id=case['id'], container=self.agent, volume=self.volume, node_id=self.core_node_id)
        result = super().execute(case)
        self.checkpoint('case-end', case_id=case['id'], status=result['status'], evidence=result['evidence'])
        return result

    def path(self, suffix=''):
        return '/var/lib/nodesteer/' + self.prefix + ('/' + suffix if suffix else '')

    def state(self, query, params=()):
        source = self.control('docker volume inspect -f ' + shlex.quote('{{.Mountpoint}}') + ' ' + shlex.quote(self.volume), record=False)
        code = "import sqlite3,json,sys; c=sqlite3.connect('file:'+sys.argv[1]+'/state.db?mode=ro',uri=True); c.row_factory=sqlite3.Row; print(json.dumps([dict(r) for r in c.execute(sys.argv[2],json.loads(sys.argv[3])).fetchall()]))"
        output = self.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(source) + ' ' + shlex.quote(query) + ' ' + shlex.quote(json.dumps(params)))
        return json.loads(output)

    def journal(self, node, execution_id):
        assert node['id'] == self.core_node_id, '不能用core卷冒充其他Agent Journal'
        rows = self.state('SELECT id,task_id,node_id,status,exit_code,scheduled_time,synced,offline,block_reason,task_revision,script_revision FROM executions WHERE id=?', [execution_id])
        assert rows, 'core Agent 持久卷Journal缺少Execution'
        return rows[0]

    def identity(self):
        code = "import sqlite3,json,sys,hashlib; c=sqlite3.connect('file:'+sys.argv[1]+'/state.db?mode=ro',uri=True); r=c.execute('select key,value from identity order by key').fetchall(); print(json.dumps({'keys':[v[0] for v in r],'fingerprint':hashlib.sha256(json.dumps(r).encode()).hexdigest(),'revision':c.execute('select global_revision from sync_state where id=1').fetchone()[0]}))"
        source = self.control('docker volume inspect -f ' + shlex.quote('{{.Mountpoint}}') + ' ' + shlex.quote(self.volume), record=False)
        return json.loads(self.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(source)))

    def wait_online(self, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id, trace=False)
            if value['status'] == 'online' and value.get('sync_status') == 'synced':
                self.events.append({'core_node': value})
                return value
            time.sleep(1)
        raise AssertionError('core原Node未恢复online/synced')

    def recreate(self, volume=None, hub_url=None):
        volume = volume or self.volume
        assert volume.startswith('qa-' + RUN_ID + '-')
        env = []
        for entry in self.spec['Config']['Env']:
            if hub_url and entry.startswith('NODESTEER_HUB_URL='): entry = 'NODESTEER_HUB_URL=' + hub_url
            env += ['-e', entry]
        command = ['docker', 'run', '-d', '--name', self.agent, '--network', self.cfg['network'], '--restart', 'unless-stopped', '--hostname', self.spec['Config']['Hostname'], '-v', volume + ':/var/lib/nodesteer'] + env + [self.spec['Config']['Image']] + (self.spec['Config'].get('Cmd') or [])
        self.control('docker rm -f ' + shlex.quote(self.agent))
        self.control(shlex.join(command))

    def make_seed(self, label):
        path = self.path(label + '-' + uuid.uuid4().hex[:6])
        task = self.task('iso-' + label, 'mkdir -p ' + shlex.quote(self.path()) + '; printf seed-real > ' + shlex.quote(path))
        result = self.run_task(task)[0]
        return path, task, result

    def wait_schedule(self, task_id, timeout=50):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            values, _, _ = self.api('GET', '/api/executions?task_id=' + task_id, trace=False)
            if values:
                result = self.wait_execution(values[0]['id'])
                self.remember('execution', result)
                return result
            time.sleep(1)
        raise AssertionError('隔离调度未触发')

    def proxy_info(self):
        path = self.home / 'proxy-info.json'
        return json.loads(path.read_text()) if path.exists() else None

    def proxy_controls(self, controls):
        info = self.proxy_info()
        assert info, '代理尚未启动'
        code = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(sys.argv[2])'
        self.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(info['controls']) + ' ' + shlex.quote(json.dumps(controls)))

    def proxy_events(self, since=0):
        info = self.proxy_info()
        output = self.control('cat ' + shlex.quote(info['events']), record=False)
        rows = [json.loads(line) for line in output.splitlines() if line]
        rows = [row for row in rows if row.get('time', 0) >= since]
        self.events.append({'proxy_events': rows})
        return rows

    def sql_lock(self, volume, relative):
        assert volume in {self.volume, self.cfg['hub_volume']}
        assert relative in {'state.db', 'hub.db', 'nodesteer.db'}
        mount = self.control('docker volume inspect -f ' + shlex.quote('{{.Mountpoint}}') + ' ' + shlex.quote(volume), record=False)
        root = self.cfg['remote_root'] + '/' + self.prefix + '-lock-' + uuid.uuid4().hex[:6]
        source = root + '.py'; log = root + '.jsonl'; pidfile = root + '.pid'
        code = "import json,pathlib,sqlite3,sys,time\np=pathlib.Path(sys.argv[2])\ndef event(k):\n with p.open('a') as f:f.write(json.dumps({'event':k,'time':time.time()})+'\\n')\nc=sqlite3.connect('file:'+sys.argv[1]+'?mode=rw',uri=True,timeout=5)\ntry:\n c.execute('BEGIN IMMEDIATE');event('lock_acquired');time.sleep(15)\nfinally:\n c.rollback();c.close();event('lock_released')\n"
        install = 'import pathlib,base64,sys;pathlib.Path(sys.argv[1]).write_bytes(base64.b64decode(sys.argv[2]))'
        self.control('python3 -c ' + shlex.quote(install) + ' ' + shlex.quote(source) + ' ' + shlex.quote(base64.b64encode(code.encode()).decode()))
        command = ['python3', source, mount + '/' + relative, log]
        pid = self.control('nohup ' + shlex.join(command) + ' > ' + shlex.quote(root + '.stdout') + ' 2>&1 & qa_lock_pid=$!; printf %s "$qa_lock_pid" > ' + shlex.quote(pidfile) + '; cat ' + shlex.quote(pidfile))
        info = {'pid': int(pid), 'source': source, 'log': log, 'pidfile': pidfile, 'volume': volume, 'database': relative, 'maximum_hold_sec': 15, 'transaction': 'BEGIN IMMEDIATE → ROLLBACK; no data/DDL writes'}
        self.events.append({'sqlite_lock': info})
        self.checkpoint('sqlite-lock-start', **info)
        time.sleep(.4)
        assert 'lock_acquired' in self.control('cat ' + shlex.quote(log))
        return info

    def lock_evidence(self, info):
        rows = [json.loads(row) for row in self.control('cat ' + shlex.quote(info['log'])).splitlines()]
        self.events.append({'sqlite_lock_events': rows})
        return rows

    def wait_lock_release(self, info):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            rows = self.lock_evidence(info)
            if rows[-1]['event'] == 'lock_released': return rows
            time.sleep(.5)
        raise AssertionError('本轮限时纯锁进程未正常ROLLBACK，必须精确PID恢复')

    def start_proxy(self):
        info = self.proxy_info()
        source_sha = hashlib.sha256((HERE / 'ws_proxy.py').read_bytes()).hexdigest()
        if not info or info.get('upstream_host') != self.cfg['ssh_host'] or info.get('source_sha') != source_sha:
            original_url = info['original_hub_url'] if info else next(e.split('=', 1)[1] for e in self.spec['Config']['Env'] if e.startswith('NODESTEER_HUB_URL='))
            if info:
                code = "import os,pathlib,signal,sys;p=int(pathlib.Path(sys.argv[1]).read_text());f=pathlib.Path('/proc/'+str(p)+'/cmdline');assert not f.exists() or sys.argv[2].encode() in f.read_bytes().split(bytes([0]));os.kill(p,signal.SIGTERM) if f.exists() else None"
                self.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(info['pid_file']) + ' ' + shlex.quote(info['source']))
            root = self.cfg['remote_root'] + '/' + self.prefix + '-proxy'
            source, controls, events, pid_file = root + '.py', root + '.controls.json', root + '.events.jsonl', root + '.pid'
            script = (HERE / 'ws_proxy.py').read_bytes()
            code = 'import pathlib,base64,sys;pathlib.Path(sys.argv[1]).write_bytes(base64.b64decode(sys.argv[2]))'
            self.checkpoint('proxy-install', path=source, sha256=hashlib.sha256(script).hexdigest(), port=18084)
            self.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(source) + ' ' + shlex.quote(base64.b64encode(script).decode()), record=False)
            info = {'source': source, 'controls': controls, 'events': events, 'pid_file': pid_file, 'port': 18084, 'upstream_host': self.cfg['ssh_host'], 'original_hub_url': original_url, 'source_sha': source_sha}
            self.save(self.home / 'proxy-info.json', info)
            self.proxy_controls({})
            command = ['python3', source, '--listen', '18084', '--upstream-host', self.cfg['ssh_host'], '--upstream-port', '18080', '--controls', controls, '--events', events]
            started = self.control('nohup ' + shlex.join(command) + ' > ' + shlex.quote(root + '.stdout') + ' 2>&1 & proxy_pid=$!; printf %s "$proxy_pid" > ' + shlex.quote(pid_file) + '; cat ' + shlex.quote(pid_file))
            info['pid'] = int(started); self.save(self.home / 'proxy-info.json', info)
            time.sleep(1)
        self.proxy_controls({})
        self.recreate(hub_url='ws://' + self.cfg['ssh_host'] + ':18084/ws')
        self.wait_online()

    def wait_script(self, script, timeout=75):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.state('SELECT content,revision,sha256 FROM scripts WHERE id=?', [script['id']])
            if rows and rows[0]['content'] == script['content'] and rows[0]['revision'] == script['revision']:
                assert rows[0]['sha256'] == script['sha256']
                return rows[0]
            time.sleep(1)
        raise AssertionError('真实Agent脚本状态未按目标Revision收敛')

    def script_fixture(self, label):
        script = self.create('script', {'name': self.prefix + '-proxy-' + label + '-' + uuid.uuid4().hex[:6], 'interpreter': 'shell', 'content': 'printf proxy-one', 'enabled': True, 'timeout': 5})
        task = self.task('proxy-' + label, type='script', script_id=script['id'])
        self.run_task(task); self.wait_script(script)
        return script, task

    def update_script(self, script, marker):
        script['content'] = 'printf ' + shlex.quote(marker)
        value, _, _ = self.api('PUT', '/api/scripts/' + script['id'], script)
        return value

    def restore_hub(self):
        self.control('docker start ' + shlex.quote(self.hub_container))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                self.api('GET', '/readyz', token=''); break
            except (OSError, AssertionError, urllib.error.URLError): time.sleep(1)
        self.token = ''; self.login(); self.wait_online()

    def stop_hub(self):
        if not self.hub_faults: raise Blocked('尚无coreHub故障窗口')
        self.checkpoint('hub-stop-warning', container=self.hub_container, affected='本轮coreHub全部依赖Agent；primary/SSO不动')
        self.control('docker stop -t 2 ' + shlex.quote(self.hub_container))

    def wait_local_terminal(self, execution_id, status='SUCCESS', timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.journal(self.node(), execution_id)
            if row['status'] in {'SUCCESS', 'FAILED', 'CANCELED', 'TIMED_OUT', 'SKIPPED', 'BLOCKED'}:
                if status is not None: assert row['status'] == status
                return row
            time.sleep(1)
        raise AssertionError('Hub离线期间Agent本地执行未终结')

    def wait_local_schedule(self, task_id, timeout=25, status='SUCCESS'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.state('SELECT id,status,offline,synced,scheduled_time FROM executions WHERE task_id=?', [task_id])
            if rows and rows[0]['status'] == status: return rows[0]
            time.sleep(1)
        raise AssertionError('Hub离线期间本地调度未执行')

    def credential(self, required=True):
        source = self.control('docker volume inspect -f ' + shlex.quote('{{.Mountpoint}}') + ' ' + shlex.quote(self.volume), record=False)
        code = "import sqlite3,json,sys;c=sqlite3.connect('file:'+sys.argv[1]+'/state.db?mode=ro',uri=True);print(json.dumps(c.execute(\"SELECT value FROM identity WHERE key='credential'\").fetchone()[0]))"
        value = json.loads(self.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(source), record=False))
        if required: assert value
        if value:
            self.secrets.append(value)
            self.events.append({'credential_fingerprint': hashlib.sha256(value.encode()).hexdigest()})
        return value

    def normal_case(self, name):
        if name == 'arch.docker_agent_restart':
            path, task, result = self.make_seed('restart')
            before = self.identity()
            running = self.task('iso-running-restart', 'sleep 120', timeout=130)
            values, _, _ = self.api('POST', '/api/tasks/' + running['id'] + '/run', {})
            self.remember('execution', values[0]); time.sleep(2)
            self.control('docker restart -t 2 ' + shlex.quote(self.agent))
            self.wait_online()
            interrupted = self.wait_execution(values[0]['id'], 'FAILED')
            assert interrupted.get('block_reason') == 'agent_restarted'
            after = self.identity()
            assert after['fingerprint'] == before['fingerprint'] and after['revision'] >= before['revision']
            self.check_file(self.node(), path, 'seed-real')
            assert self.journal(self.node(), result['id'])['status'] == 'SUCCESS'
        elif name == 'arch.docker_agent_recreate':
            path, task, result = self.make_seed('recreate')
            before = self.identity()
            once_path = self.path('one-slot-' + uuid.uuid4().hex[:6])
            once_task = self.task('iso-one-slot', 'mkdir -p ' + shlex.quote(self.path()) + '; printf "slot\\n" >> ' + shlex.quote(once_path))
            schedule = self.create('schedule', {'task_id': once_task['id'], 'type': 'one_time', 'run_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat(), 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'run_once', 'enabled': True})
            self.wait_schedule(once_task['id'])
            self.recreate(); self.wait_online(); time.sleep(4)
            self.check_file(self.node(), path, 'seed-real')
            self.check_file(self.node(), once_path, 'slot')
            assert self.identity()['fingerprint'] == before['fingerprint']
            values, _, _ = self.api('GET', '/api/executions?task_id=' + once_task['id'])
            assert len(values) == 1, '已完成One-Time在保卷重建后重复执行'
            assert self.journal(self.node(), result['id'])['synced']
            self.api('DELETE', '/api/schedules/' + schedule['id'])
        elif name == 'arch.persistent_volume_missing':
            path, task, result = self.make_seed('missing-volume')
            before = self.identity()
            original_nodes, _, _ = self.api('GET', '/api/nodes')
            spare = self.prefix + '-missing-volume-' + uuid.uuid4().hex[:6]
            self.control('docker volume create ' + shlex.quote(spare))
            try:
                self.recreate(volume=spare); time.sleep(6)
                self.check_file(self.node(), path, absent=True)
                after_nodes, _, _ = self.api('GET', '/api/nodes')
                additions = [n for n in after_nodes if n['id'] not in {old['id'] for old in original_nodes} and n.get('hostname') == self.node()['name']]
                self.events.append({'missing_volume_observation': {'original_volume_preserved': self.volume, 'replacement_volume': spare, 'node_count_before': len(original_nodes), 'node_count_after': len(after_nodes), 'attributed_new_nodes': [n['id'] for n in additions], 'contract': '记录真实数据/身份变化，不预设拒绝重新注册'}})
            finally:
                self.recreate(); self.wait_online()
                assert self.identity()['fingerprint'] == before['fingerprint']
                self.check_file(self.node(), path, 'seed-real')
                assert self.journal(self.node(), result['id'])['status'] == 'SUCCESS'
                self.control('docker volume rm ' + shlex.quote(spare))
        elif name in {'arch.agent_offline_during_hub_script_update', 'arch.agent_offline_during_task_update'}:
            script = self.create('script', {'name': self.prefix + '-iso-script-' + uuid.uuid4().hex[:6], 'interpreter': 'shell', 'content': 'printf revision-one', 'enabled': True, 'timeout': 5})
            task = self.task('iso-offline-change', type='script', script_id=script['id']) if 'script' in name else self.task('iso-offline-task', 'printf revision-one')
            self.run_task(task)
            self.control('docker stop -t 2 ' + shlex.quote(self.agent))
            try:
                if 'script' in name:
                    script['content'] = 'printf revision-two'
                    script, _, _ = self.api('PUT', '/api/scripts/' + script['id'], script)
                else:
                    task['command'] = 'printf revision-two'
                    task, _, _ = self.api('PUT', '/api/tasks/' + task['id'], task)
            finally:
                self.control('docker start ' + shlex.quote(self.agent)); self.wait_online()
            result = self.run_task(task)[0]
            assert result['stdout'] == 'revision-two'
            assert result['task_revision'] == task['revision']
            if 'script' in name:
                assert result['script_revision'] == script['revision']
                row = self.state('SELECT content,revision,sha256 FROM scripts WHERE id=?', [script['id']])[0]
                assert row['content'] == script['content'] and row['sha256'] == hashlib.sha256(script['content'].encode()).hexdigest()
        elif name == 'feature.schedule.on_start':
            path = self.path('onstart-' + uuid.uuid4().hex[:6])
            task = self.task('iso-onstart', 'mkdir -p ' + shlex.quote(self.path()) + '; printf "started\\n" >> ' + shlex.quote(path))
            schedule = self.create('schedule', {'task_id': task['id'], 'type': 'on_start', 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'run_once', 'enabled': True})
            time.sleep(2)
            before, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'])
            assert not before, 'on_start在当前进程已启动之后创建时不应误触发'
            self.control('docker restart -t 2 ' + shlex.quote(self.agent)); self.wait_online(); self.wait_schedule(task['id'])
            self.check_file(self.node(), path, 'started')
            self.recreate(); self.wait_online(); time.sleep(3)
            values, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'])
            assert len(values) == 2
            for value in values: self.wait_execution(value['id'])
            self.check_file(self.node(), path, 'started\nstarted')
            self.api('DELETE', '/api/schedules/' + schedule['id'])
        elif name in {'arch.lost_notification', 'arch.duplicate_notification', 'arch.out_of_order_notification'}:
            self.start_proxy()
            script, task = self.script_fixture(name.split('.')[1])
            since = time.time()
            flag = {'arch.lost_notification': 'drop_types', 'arch.duplicate_notification': 'duplicate_types', 'arch.out_of_order_notification': 'reorder_types'}[name]
            self.proxy_controls({flag: ['CHANGE_NOTIFICATION']})
            try:
                script = self.update_script(script, 'proxy-two')
                if name == 'arch.out_of_order_notification': script = self.update_script(script, 'proxy-three')
                self.wait_script(script)
                result = self.run_task(task)[0]
                assert result['stdout'] == ('proxy-three' if name == 'arch.out_of_order_notification' else 'proxy-two')
                rows = self.proxy_events(since)
                expected = {'drop_types': 'drop', 'duplicate_types': 'duplicate', 'reorder_types': 'reorder_release'}[flag]
                assert any(row.get('event') == expected and row.get('type') == 'CHANGE_NOTIFICATION' for row in rows), '代理没有真实扰动目标控制帧'
            finally: self.proxy_controls({})
        elif name in {'arch.change_log_window_expiry', 'arch.full_resync', 'arch.revision_lag'}:
            self.start_proxy()
            script, task = self.script_fixture('full')
            settings, _, _ = self.api('GET', '/api/settings')
            since = time.time()
            self.control('docker stop -t 2 ' + shlex.quote(self.agent))
            try:
                if name != 'arch.revision_lag': self.api('PUT', '/api/settings', {'changelog_window': '1'})
                for marker in ['full-two', 'full-three', 'full-four']: script = self.update_script(script, marker)
            finally:
                self.control('docker start ' + shlex.quote(self.agent)); self.wait_online()
            try:
                self.wait_script(script)
                assert self.run_task(task)[0]['stdout'] == 'full-four'
                rows = self.proxy_events(since)
                if name != 'arch.revision_lag': assert any(row.get('type') == 'SYNC_RESPONSE' and row.get('full_resync') for row in rows), '未观察到真实Full Resync响应'
            finally:
                if name != 'arch.revision_lag': self.api('PUT', '/api/settings', {'changelog_window': settings['changelog_window']})
        elif name == 'arch.sync_dependency_failure':
            self.start_proxy()
            good, task = self.script_fixture('good-dependency')
            bad, bad_task = self.script_fixture('bad-dependency')
            before = self.identity()['revision']
            since = time.time()
            self.proxy_controls({'corrupt_script_id': bad['id']})
            try:
                good = self.update_script(good, 'good-updated')
                bad = self.update_script(bad, 'bad-updated')
                time.sleep(3)
                assert self.identity()['revision'] == before, '失败同步推进了Agent Revision'
                row = self.state('SELECT content,revision FROM scripts WHERE id=?', [good['id']])[0]
                assert row['content'] == 'printf proxy-one', '失败同步泄漏了部分脚本修改'
                assert any(row.get('event') == 'corrupt_script' for row in self.proxy_events(since))
            finally: self.proxy_controls({})
            self.recreate(hub_url='ws://' + self.cfg['ssh_host'] + ':18084/ws'); self.wait_online()
            self.wait_script(good); self.wait_script(bad)
            assert self.run_task(task)[0]['stdout'] == 'good-updated'
        elif name == 'feature.sync.script-sha':
            self.start_proxy()
            script, task = self.script_fixture('sha')
            before = self.identity()['revision']
            self.proxy_controls({'corrupt_script_sha': script['id']})
            try:
                script = self.update_script(script, 'canonical-script-two')
                time.sleep(3)
                row = self.state('SELECT content,revision,sha256 FROM scripts WHERE id=?', [script['id']])[0]
                self.events.append({'script_sha_observation': {'content_sha256': hashlib.sha256(row['content'].encode()).hexdigest(), 'declared_sha256': row['sha256'], 'revision': row['revision'], 'agent_revision': self.identity()['revision'], 'before_revision': before}})
                assert row['content'] != 'printf injected-script-corruption' and self.identity()['revision'] == before, 'Agent接受SHA256不一致脚本并推进Revision'
            finally:
                self.proxy_controls({})
                self.recreate(hub_url='ws://' + self.cfg['ssh_host'] + ':18084/ws'); self.wait_online(); self.wait_script(script)
        elif name in {'arch.execution_re_upload', 'arch.duplicate_execution_upload'}:
            self.start_proxy()
            self.proxy_controls({'drop_types': ['EXECUTION_ACK']})
            task = self.task('ack-loss', 'printf ack-loss-effect')
            since = time.time()
            try:
                values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
                execution = self.remember('execution', values[0])
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    ex, _, _ = self.api('GET', '/api/executions/' + execution['id'], trace=False)
                    if ex['status'] == 'SUCCESS': break
                    time.sleep(1)
                assert ex['status'] == 'SUCCESS'
                assert not self.journal(self.node(), execution['id'])['synced'], '丢ACK后Journal错误标记已同步'
                assert any(row.get('event') == 'drop' and row.get('type') == 'EXECUTION_ACK' for row in self.proxy_events(since))
            finally: self.proxy_controls({})
            self.recreate(hub_url='ws://' + self.cfg['ssh_host'] + ':18084/ws'); self.wait_online()
            final = self.wait_execution(execution['id'])
            assert final['stdout'] == 'ack-loss-effect'
            rows, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'])
            assert len(rows) == 1 and rows[0]['id'] == execution['id']
            if name == 'arch.duplicate_execution_upload':
                self.recreate(hub_url='ws://' + self.cfg['ssh_host'] + ':18084/ws'); self.wait_online()
                rows, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'])
                assert len(rows) == 1
        elif name == 'arch.hub_disconnect_during_execution':
            path = self.path('hub-down-' + uuid.uuid4().hex[:6])
            task = self.task('hub-down', 'mkdir -p ' + shlex.quote(self.path()) + '; printf running > ' + shlex.quote(path) + '; sleep 10; printf hub-down-completed > ' + shlex.quote(path) + '; printf hub-down-log', timeout=25)
            values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
            execution = self.remember('execution', values[0])
            time.sleep(1)
            assert self.journal(self.node(), execution['id'])['status'] == 'RUNNING'
            self.check_file(self.node(), path, 'running')
            self.stop_hub()
            try:
                row = self.wait_local_terminal(execution['id'])
                assert not row['synced']
                self.events.append({'offline_semantics': '该Manual Execution在线启动，offline初始false保持；断线期间结果以synced=false持久，字段不擅自改为全程断线语义', 'journal': row})
                self.check_file(self.node(), path, 'hub-down-completed')
                logs = self.state('SELECT stdout,stderr FROM executions WHERE id=?', [execution['id']])[0]
                assert logs['stdout'] == 'hub-down-log'
            finally: self.restore_hub()
            final = self.wait_execution(execution['id'])
            assert final['stdout'] == 'hub-down-log'
        elif name in {'arch.offline_scheduled_execution', 'arch.duplicate_schedule_trigger', 'arch.hub_restart'}:
            path = self.path('scheduled-' + uuid.uuid4().hex[:6])
            task = self.task('offline-slot', 'mkdir -p ' + shlex.quote(self.path()) + '; printf "offline-slot\\n" >> ' + shlex.quote(path))
            run_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=8)).isoformat()
            owner = 'hub' if name == 'arch.hub_restart' else 'agent'
            schedule = self.create('schedule', {'task_id': task['id'], 'type': 'one_time', 'run_at': run_at, 'timezone': 'UTC', 'execution_owner': owner, 'offline_policy': 'hub_online_required' if owner == 'hub' else 'allow_offline', 'misfire_policy': 'run_once', 'enabled': True})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.state('SELECT id FROM schedules WHERE id=?', [schedule['id']]): break
                time.sleep(.5)
            if name == 'arch.offline_scheduled_execution':
                self.stop_hub()
                try:
                    local = self.wait_local_schedule(task['id'])
                    assert local['offline'] and not local['synced']
                    self.check_file(self.node(), path, 'offline-slot')
                finally: self.restore_hub()
                self.wait_execution(local['id'])
            else:
                self.wait_schedule(task['id'])
                if name == 'arch.hub_restart': self.stop_hub(); self.restore_hub()
                else: self.recreate(hub_url='ws://' + self.cfg['ssh_host'] + ':18084/ws' if self.proxy_info() else None); self.wait_online()
                time.sleep(3)
            values, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'])
            assert len(values) == 1, '同一Schedule slot重复生成Execution'
            self.check_file(self.node(), path, 'offline-slot')
            self.api('DELETE', '/api/schedules/' + schedule['id'])
        elif name == 'feature.schedule.misfire':
            tasks, schedules = {}, {}
            when = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat()
            for policy in ['skip', 'run_once']:
                tasks[policy] = self.task('misfire-' + policy, 'printf misfire-' + policy)
                schedules[policy] = self.create('schedule', {'task_id': tasks[policy]['id'], 'type': 'one_time', 'run_at': when, 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': policy, 'enabled': True})
            time.sleep(1)
            self.control('docker stop -t 2 ' + shlex.quote(self.agent)); time.sleep(8)
            self.control('docker start ' + shlex.quote(self.agent)); self.wait_online()
            self.wait_schedule(tasks['run_once']['id'])
            skipped, _, _ = self.api('GET', '/api/executions?task_id=' + tasks['skip']['id'])
            assert not skipped, 'SKIP对过期One-Time错误补跑'
            rows, _, _ = self.api('GET', '/api/executions?task_id=' + tasks['run_once']['id'])
            assert len(rows) == 1
            for schedule in schedules.values(): self.api('DELETE', '/api/schedules/' + schedule['id'])
        elif name == 'feature.sync.tombstone':
            script, task = self.script_fixture('tombstone')
            schedule = self.create('schedule', {'task_id': task['id'], 'type': 'one_time', 'run_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat(), 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'skip', 'enabled': False})
            time.sleep(1)
            self.control('docker stop -t 2 ' + shlex.quote(self.agent))
            try:
                for kind, obj in [('schedules', schedule), ('tasks', task), ('scripts', script)]: self.api('DELETE', '/api/' + kind + '/' + obj['id'])
            finally: self.control('docker start ' + shlex.quote(self.agent)); self.wait_online()
            for table, obj in [('schedules', schedule), ('tasks', task), ('scripts', script)]:
                assert not self.state('SELECT id FROM ' + table + ' WHERE id=?', [obj['id']]), 'Tombstone/快照未移除旧本地对象'
        elif name == 'feature.node.delete-guard':
            spare_id = '9800f15c-caa3-456a-a81d-9a0d6771d241'
            value, _, _ = self.api('GET', '/api/nodes/' + spare_id)
            assert value['hostname'] == 'qa-20260930-101945-env-docker-agent' and value['agent_id'] == 'agent-5e813abd-b4d4-4a14-afeb-aba483c34719'
            group = self.create('group', {'name': self.prefix + '-spare-reference-' + uuid.uuid4().hex[:6], 'type': 'static', 'members': [spare_id]})
            self.api('DELETE', '/api/nodes/' + spare_id, expected=(409,))
            self.api('DELETE', '/api/groups/' + group['id'])
            self.api('DELETE', '/api/nodes/' + spare_id)
            self.api('GET', '/api/nodes/' + spare_id, expected=(404,))
        elif name == 'feature.node.revoke':
            before, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id)
            old = self.credential()
            artifact = self.upload_artifact(b'credential-revoke-proof')
            task = self.task('re-enroll-proof', 'printf re-enroll-real')
            self.api('POST', '/api/nodes/' + self.core_node_id + '/revoke-credential', {})
            value, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id)
            assert value['status'] == 'disabled'
            auth = {'X-NodeSteer-Agent-ID': before['agent_id'], 'X-NodeSteer-Agent-Token': old}
            self.api('GET', '/api/artifacts/' + artifact['id'] + '/download', token='', headers=auth, expected=(401,))
            self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, expected=(400,))
            enrollment, _, _ = self.api('POST', '/api/nodes/enrollment', {'node_name': before['hostname'], 'node_ip': before['ip'], 'hub_address': self.base})
            assert enrollment['node_id'] == self.core_node_id and enrollment['agent_id'] == before['agent_id'], '重新纳管丢失原Node/Agent身份'
            self.control('docker restart -t 2 ' + shlex.quote(self.agent))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                value, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id)
                new = self.credential(required=False)
                if new and new != old and value.get('sync_status') == 'synced': break
                time.sleep(1)
            assert new and new != old
            reenrolled_status = value['status']
            self.events.append({'re_enrollment_observation': {'node_id': value['id'], 'agent_id': value['agent_id'], 'status_after_new_credential_accepted': reenrolled_status, 'sync_status': value['sync_status'], 'same_volume': self.volume}})
            if reenrolled_status != 'online':
                self.api('POST', '/api/nodes/' + self.core_node_id, {'status': 'online'})
            self.wait_online()
            self.api('GET', '/api/artifacts/' + artifact['id'] + '/download', token='', headers=auth, expected=(401,))
            auth['X-NodeSteer-Agent-Token'] = new
            _, _, downloaded = self.api('GET', '/api/artifacts/' + artifact['id'] + '/download', token='', headers=auth)
            assert downloaded == b'credential-revoke-proof'
            assert self.run_task(task)[0]['stdout'] == 're-enroll-real'
            assert reenrolled_status == 'online', '新凭证认证/同步成功，但重新纳管后仍disabled；已显式Enable恢复QA环境'
        elif name == 'feature.node.docker-upgrade':
            before = self.control('docker exec ' + shlex.quote(self.agent) + ' sha256sum /usr/local/bin/nodesteer-agent').split()[0]
            result, _, _ = self.api('POST', '/api/nodes/upgrade', {'node_ids': [self.core_node_id, self.core_node_id]})
            assert len(result) == 1 and result[0]['status'] == 'SKIPPED'
            assert result[0].get('block_reason'), 'Docker拒绝升级缺少原因'
            after = self.control('docker exec ' + shlex.quote(self.agent) + ' sha256sum /usr/local/bin/nodesteer-agent').split()[0]
            assert after == before
            if result[0].get('execution_id'):
                ex, _, _ = self.api('GET', '/api/executions/' + result[0]['execution_id'])
                assert ex['status'] == 'SKIPPED'
                assert not self.state('SELECT id FROM executions WHERE id=?', [ex['id']]), '被Hub过滤的升级错误下发Agent'
        elif name == 'feature.condition.operators-and-remote':
            for op, expected in [('==', '5'), ('!=', '6'), ('>', '4'), ('<', '6'), ('>=', '5'), ('<=', '5')]:
                condition = {'type': 'local', 'local': {'metric': 'command_result', 'command': 'printf 5', 'operator': op, 'value': expected}}
                result = self.run_task(self.task('operator-' + uuid.uuid4().hex[:4], 'printf comparison-real', condition=condition))[0]
                assert result['stdout'] == 'comparison-real'
            true = {'type': 'local', 'local': {'metric': 'command_result', 'command': 'printf true', 'operator': '==', 'value': 'true'}}
            false = {'type': 'local', 'local': {'metric': 'command_result', 'command': 'printf false', 'operator': '==', 'value': 'true'}}
            successful = self.task('remote-history', 'printf remote-history-real')
            self.run_task(successful)
            remote = {'type': 'remote', 'remote': {'node_id': self.core_node_id, 'property': 'last_execution', 'task_id': successful['id'], 'operator': '==', 'value': 'SUCCESS'}}
            unused = self.task('remote-no-history', 'printf unused')
            unknown = {'type': 'remote', 'remote': {'node_id': self.core_node_id, 'property': 'last_execution', 'task_id': unused['id'], 'operator': '!=', 'value': 'SUCCESS'}}
            variants = [([true, remote], 'SUCCESS'), ([true, false], 'SKIPPED'), ([unknown, true], 'BLOCKED'), ([unknown, false], 'BLOCKED'), ([false, unknown], 'SKIPPED')]
            for parts, status in variants:
                path = self.path('and-' + uuid.uuid4().hex[:6])
                task = self.task('and-' + uuid.uuid4().hex[:4], 'mkdir -p ' + shlex.quote(self.path()) + '; printf and-real > ' + shlex.quote(path), condition={'type': 'and', 'and': parts})
                self.run_task(task, status)
                self.check_file(self.node(), path, 'and-real' if status == 'SUCCESS' else None, absent=status != 'SUCCESS')
            nodes, _, _ = self.api('GET', '/api/nodes')
            peer = next((node for node in nodes if node['id'] != self.core_node_id and node['status'] == 'online'), None)
            if not peer: raise Blocked('不存在真实在线第二Agent，remote online维度未验证')
            condition = {'type': 'remote', 'remote': {'node_id': peer['id'], 'property': 'online', 'operator': '==', 'value': 'online'}}
            assert self.run_task(self.task('remote-online', 'printf peer-online-real', condition=condition))[0]['stdout'] == 'peer-online-real'
        elif name == 'feature.condition.remote-ttl':
            self.start_proxy()
            successful = self.task('ttl-history', 'printf ttl-history-real')
            self.run_task(successful)
            path = self.path('expired-ttl-' + uuid.uuid4().hex[:6])
            condition = {'type': 'remote', 'remote': {'node_id': self.core_node_id, 'property': 'last_execution', 'task_id': successful['id'], 'operator': '==', 'value': 'SUCCESS'}}
            task = self.task('expired-ttl', 'mkdir -p ' + shlex.quote(self.path()) + '; printf expired-state-executed > ' + shlex.quote(path), condition=condition)
            since = time.time(); self.proxy_controls({'expire_remote': True})
            try:
                values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
                execution = self.remember('execution', values[0])
                local = self.wait_local_terminal(execution['id'], status=None, timeout=15)
                final, _, _ = self.api('GET', '/api/executions/' + execution['id'])
                actual = self.remote(self.node(), 'if test -e ' + shlex.quote(path) + '; then cat ' + shlex.quote(path) + '; else printf absent; fi\n')
                self.events.append({'expired_remote_effect': {'execution_id': execution['id'], 'status': final['status'], 'marker': actual, 'property': 'last_execution', 'observed_at': '2000-01-01T00:00:00Z', 'ttl_sec': 1}})
                assert any(row.get('event') == 'expire_remote' for row in self.proxy_events(since))
                assert final['status'] == 'BLOCKED' and actual == 'absent', 'Agent接受过期Remote State并真实执行任务，未Fail Closed'
            finally: self.proxy_controls({})
        elif name == 'arch.process_group_cleanup':
            for action in ['cancel', 'timeout']:
                directory = self.path('pg-' + action + '-' + uuid.uuid4().hex[:6])
                inner = 'trap "" TERM; sleep 120 & printf %s "$!" > ' + shlex.quote(directory + '/child') + '; wait'
                command = 'mkdir -p ' + shlex.quote(directory) + '; sh -c ' + shlex.quote(inner) + ' & child=$!; printf %s "$child" > ' + shlex.quote(directory + '/parent') + '; wait "$child"'
                task = self.task('pg-' + action, command, timeout=3 if action == 'timeout' else 130, retry=0)
                values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
                execution = self.remember('execution', values[0]); time.sleep(1)
                if action == 'cancel': self.api('POST', '/api/executions/' + execution['id'] + '/cancel', {})
                self.wait_execution(execution['id'], 'CANCELED' if action == 'cancel' else 'TIMED_OUT')
                pids = [int(self.remote(self.node(), 'cat ' + shlex.quote(directory + '/' + part) + '\n')) for part in ['parent', 'child']]
                code = '; '.join('if test -r /proc/' + str(pid) + '/stat; then cat /proc/' + str(pid) + '/stat; fi' for pid in pids)
                remaining = self.remote(self.node(), code + '\n')
                self.events.append({'process_group_effect': {'action': action, 'pids': pids, 'remaining_proc_stat': remaining}})
                assert not remaining or all(' Z ' in row for row in remaining.splitlines()), '忽略SIGTERM的进程组仍有运行中进程'
        elif name in {'arch.artifact_download_interruption', 'arch.artifact_sha_failure'}:
            self.start_proxy()
            suffix = uuid.uuid4().hex[:8]
            payload = ('qa-artifact-' + suffix).encode() * 8192
            artifact = self.upload_artifact(payload)
            binary = self.path('prefetch-' + suffix + '/unused-bin')
            unit = self.prefix + '-prefetch-' + suffix + '.service'
            application = self.create('application', {'name': self.prefix + '-prefetch-' + suffix, 'version': 'qa-prefetch', 'artifact_id': artifact['id'], 'binary_path': binary, 'config_path': self.path('prefetch-' + suffix + '/unused.conf'), 'config': 'prefetch-only', 'unit_name': unit, 'health_check': {'type': 'systemd'}})
            mode = 'truncate' if name == 'arch.artifact_download_interruption' else 'corrupt'
            url_path = '/api/artifacts/' + artifact['id'] + '/download'
            controls = {'redirect_app_id': application['id'], 'artifact_proxy_url': 'http://' + self.cfg['ssh_host'] + ':18084' + url_path, 'http_fault': {'path': url_path, 'mode': mode}}
            since = time.time(); self.proxy_controls(controls)
            try:
                self.api('POST', '/api/applications/' + application['id'] + '/assign', {'node_ids': [self.core_node_id]})
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    rows = self.proxy_events(since)
                    if any(row.get('event') == 'http_fault' for row in rows): break
                    time.sleep(1)
                assert any(row.get('event') == 'http_fault' and row.get('mode') == mode for row in rows), '未发生真实Artifact HTTP故障'
                time.sleep(2)
                assert not self.state('SELECT sha256 FROM artifacts WHERE sha256=?', [artifact['sha256']]), '失败下载注册了可信缓存'
                self.check_file(self.node(), '/var/lib/nodesteer/artifacts/' + artifact['sha256'], absent=True)
                self.check_file(self.node(), binary, absent=True)
                self.check_file(self.node(), '/etc/systemd/system/' + unit, absent=True)
            finally:
                controls.pop('http_fault', None); self.proxy_controls(controls)
            application['description'] = 'retry after actual HTTP fault'
            self.api('PUT', '/api/applications/' + application['id'], application)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                cached = self.state('SELECT sha256,path FROM artifacts WHERE sha256=?', [artifact['sha256']])
                if cached: break
                time.sleep(1)
            assert cached
            actual_sha = self.remote(self.node(), 'sha256sum ' + shlex.quote(cached[0]['path']) + '\n').split()[0]
            assert actual_sha == artifact['sha256']
            self.proxy_controls({})
            self.api('DELETE', '/api/applications/' + application['id'])
            self.api('DELETE', '/api/artifacts/' + artifact['id'])
            for row in self.ledger:
                if row['id'] in {application['id'], artifact['id']}: row['status'] = 'deleted'
            self.save(self.ledger_path, self.ledger)
        elif name == 'feature.sync.ready':
            self.start_proxy()
            marker = self.path('not-ready-' + uuid.uuid4().hex[:6])
            task = self.task('ready-gate', 'mkdir -p ' + shlex.quote(self.path()) + '; printf forbidden > ' + shlex.quote(marker))
            time.sleep(1); since = time.time()
            self.proxy_controls({'hold_types': ['SYNC_RESPONSE']})
            try:
                self.control('docker restart -t 2 ' + shlex.quote(self.agent))
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if any(row.get('event') == 'hold' for row in self.proxy_events(since)): break
                    time.sleep(1)
                assert any(row.get('event') == 'hold' for row in self.proxy_events(since))
                self.run_task(task, 'BLOCKED')
                self.check_file(self.node(), marker, absent=True)
            finally: self.proxy_controls({})
            self.wait_online(); time.sleep(1)
            self.run_task(task); self.check_file(self.node(), marker, 'forbidden')
        elif name == 'feature.schedule.interval-offline':
            marker = self.path('offline-interval-' + uuid.uuid4().hex[:6])
            task = self.task('offline-interval', 'mkdir -p ' + shlex.quote(self.path()) + '; printf "slot\\n" >> ' + shlex.quote(marker))
            interval = self.create('schedule', {'task_id': task['id'], 'type': 'interval', 'interval_sec': 4, 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'run_once', 'enabled': True})
            required_marker = self.path('hub-required-' + uuid.uuid4().hex[:6])
            required_task = self.task('hub-required', 'printf forbidden > ' + shlex.quote(required_marker))
            required = self.create('schedule', {'task_id': required_task['id'], 'type': 'one_time', 'run_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=6)).isoformat(), 'timezone': 'UTC', 'execution_owner': 'hub', 'offline_policy': 'hub_online_required', 'misfire_policy': 'skip', 'enabled': True})
            time.sleep(1); self.stop_hub()
            try:
                time.sleep(10)
                local = self.state('SELECT id,status,offline,synced,scheduled_time FROM executions WHERE task_id=?', [task['id']])
                assert len(local) >= 2 and all(row['status'] == 'SUCCESS' for row in local)
                assert any(row['offline'] and not row['synced'] for row in local)
                self.check_file(self.node(), required_marker, absent=True)
                assert not self.state('SELECT id FROM executions WHERE task_id=?', [required_task['id']])
            finally: self.restore_hub()
            self.api('DELETE', '/api/schedules/' + interval['id']); self.api('DELETE', '/api/schedules/' + required['id'])
            time.sleep(2)
            remote, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'])
            assert len({row['scheduled_time'] for row in remote}) == len(remote)
            for row in remote: self.wait_execution(row['id'])
            lines = self.remote(self.node(), 'wc -l < ' + shlex.quote(marker) + '\n')
            assert int(lines) == len(remote)
            self.check_file(self.node(), required_marker, absent=True)
        elif name == 'feature.docker.cache-preservation':
            before = self.state('SELECT sha256,path FROM artifacts')
            assert before, '尚无真实校验成功的Artifact缓存'
            before_hashes = {row['sha256']: self.remote(self.node(), 'sha256sum ' + shlex.quote(row['path']) + '\n').split()[0] for row in before}
            identity = self.identity(); self.recreate(hub_url='ws://' + self.cfg['ssh_host'] + ':18084/ws'); self.wait_online()
            after = self.state('SELECT sha256,path FROM artifacts')
            assert {row['sha256'] for row in after} == set(before_hashes)
            for row in after: assert self.remote(self.node(), 'sha256sum ' + shlex.quote(row['path']) + '\n').split()[0] == before_hashes[row['sha256']]
            assert self.identity()['fingerprint'] == identity['fingerprint']
        elif name == 'feature.execution.duplicate-id':
            self.start_proxy()
            marker = self.path('duplicate-id-' + uuid.uuid4().hex[:6])
            task = self.task('duplicate-id', 'mkdir -p ' + shlex.quote(self.path()) + '; sleep 1; printf "once\\n" >> ' + shlex.quote(marker))
            since = time.time(); self.proxy_controls({'duplicate_task_id': task['id'], 'replay_delay': 4})
            try:
                execution = self.run_task(task)[0]
                time.sleep(5)
                rows = self.proxy_events(since)
                assert any(row.get('event') == 'duplicate' and row.get('id') == execution['id'] for row in rows)
                assert any(row.get('event') == 'replay_execution' and row.get('id') == execution['id'] for row in rows)
                assert self.remote(self.node(), 'wc -l < ' + shlex.quote(marker) + '\n') == '1'
                assert len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])) == 1
                records, _, _ = self.api('GET', '/api/executions?task_id=' + task['id'])
                assert len(records) == 1 and records[0]['id'] == execution['id']
            finally: self.proxy_controls({})
        elif name == 'feature.schedule.onstart-network-disabled':
            self.start_proxy()
            marker = self.path('onstart-network-' + uuid.uuid4().hex[:6])
            task = self.task('onstart-network', 'mkdir -p ' + shlex.quote(self.path()) + '; printf "start\\n" >> ' + shlex.quote(marker))
            schedule = self.create('schedule', {'task_id': task['id'], 'type': 'on_start', 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'run_once', 'enabled': True})
            time.sleep(1)
            self.control('docker restart -t 2 ' + shlex.quote(self.agent)); self.wait_online(); self.wait_schedule(task['id'])
            before_pid = self.control('docker inspect -f ' + shlex.quote('{{.State.Pid}}') + ' ' + shlex.quote(self.agent))
            connection = next(row['connection'] for row in reversed(self.proxy_events()) if row.get('event') == 'connection' and row.get('websocket'))
            since = time.time(); self.proxy_controls({'disconnect_connection': connection})
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    rows = self.proxy_events(since)
                    if any(row.get('event') == 'connection' and row.get('websocket') and row['connection'] != connection for row in rows): break
                    time.sleep(1)
                assert any(row.get('event') == 'injected_disconnect' for row in rows)
                assert any(row.get('event') == 'connection' and row.get('websocket') and row['connection'] != connection for row in rows)
            finally: self.proxy_controls({})
            self.wait_online(); self.run_task(self.task('reconnect-proof', 'printf reconnect-real'))
            assert self.control('docker inspect -f ' + shlex.quote('{{.State.Pid}}') + ' ' + shlex.quote(self.agent)) == before_pid
            assert len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])) == 1
            self.check_file(self.node(), marker, 'start')
            schedule['enabled'] = False; self.api('PUT', '/api/schedules/' + schedule['id'], schedule)
            time.sleep(1)
            self.control('docker restart -t 2 ' + shlex.quote(self.agent)); self.wait_online(); time.sleep(2)
            assert len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])) == 1
            self.check_file(self.node(), marker, 'start')
            self.api('DELETE', '/api/schedules/' + schedule['id'])
        elif name == 'feature.condition.unknown-nested-offline':
            true = {'type': 'local', 'local': {'metric': 'command_result', 'command': 'printf yes', 'operator': '==', 'value': 'yes'}}
            unknown = {'type': 'local', 'local': {'metric': 'command_result', 'command': 'printf unavailable >&2; exit 3', 'operator': '==', 'value': 'yes'}}
            nested = {'type': 'and', 'and': [true, {'type': 'and', 'and': [true, {'type': 'and', 'and': [true, true]}]}]}
            for condition, status in [(unknown, 'BLOCKED'), (nested, 'SUCCESS'), ({'type': 'and', 'and': [true, unknown]}, 'BLOCKED')]:
                marker = self.path('nested-' + uuid.uuid4().hex[:6])
                self.run_task(self.task('nested-condition', 'mkdir -p ' + shlex.quote(self.path()) + '; printf effect > ' + shlex.quote(marker), condition=condition), status)
                self.check_file(self.node(), marker, 'effect' if status == 'SUCCESS' else None, absent=status != 'SUCCESS')
            failed = self.task('failed-history', 'printf history-failed >&2; exit 9'); self.run_task(failed, 'FAILED')
            remote = {'type': 'remote', 'remote': {'node_id': self.core_node_id, 'property': 'last_execution', 'task_id': failed['id'], 'operator': '==', 'value': 'FAILED'}}
            assert self.run_task(self.task('remote-failed', 'printf failed-history-real', condition=remote))[0]['stdout'] == 'failed-history-real'
            remote['remote']['operator'] = '!='
            self.run_task(self.task('remote-failed-false', 'printf forbidden', condition=remote), 'SKIPPED')
            for condition in [{'type': 'and', 'and': []}, {'type': 'and', 'and': [{'type': 'bad'}]}]:
                payload = {'name': self.prefix + '-invalid-and-' + uuid.uuid4().hex[:6], 'type': 'command', 'target': {'type': 'node', 'node_ids': [self.core_node_id]}, 'command': 'printf forbidden', 'timeout': 5, 'retry': 0, 'enabled': True, 'condition': condition}
                self.api('POST', '/api/tasks', payload, expected=(400,))
            remote['remote']['operator'] = '=='
            marker = self.path('remote-offline-' + uuid.uuid4().hex[:6])
            task = self.task('remote-offline', 'printf forbidden > ' + shlex.quote(marker), condition=remote)
            schedule = self.create('schedule', {'task_id': task['id'], 'type': 'one_time', 'run_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=4)).isoformat(), 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'run_once', 'enabled': True})
            time.sleep(1); self.stop_hub()
            try:
                local = self.wait_local_schedule(task['id'], timeout=12, status='BLOCKED')
                assert local['status'] == 'BLOCKED' and local['offline'] and not local['synced']
                self.check_file(self.node(), marker, absent=True)
            finally: self.restore_hub()
            self.wait_execution(local['id'], 'BLOCKED')
            self.api('DELETE', '/api/schedules/' + schedule['id'])
        elif name == 'feature.execution.journal-write-failure':
            self.start_proxy()
            marker = self.path('journal-failure-' + uuid.uuid4().hex[:6])
            task = self.task('journal-failure', 'mkdir -p ' + shlex.quote(self.path()) + '; printf once > ' + shlex.quote(marker))
            time.sleep(1)
            info = self.sql_lock(self.volume, 'state.db')
            since = time.time(); self.proxy_controls({'duplicate_task_id': task['id'], 'replay_delay': 22})
            try:
                values, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
                execution = self.remember('execution', values[0])
                time.sleep(7)
                assert not self.state('SELECT id FROM executions WHERE id=?', [execution['id']])
                self.check_file(self.node(), marker, absent=True)
                log_code = 'import subprocess,sys; r=subprocess.run(["docker","logs","--since",sys.argv[1],sys.argv[2]],capture_output=True,text=True);print("\\n".join(x for x in (r.stdout+r.stderr).splitlines() if sys.argv[3] in x and "create execution journal failed" in x))'
                logs = self.control('python3 -c ' + shlex.quote(log_code) + ' ' + shlex.quote(dt.datetime.fromtimestamp(since-1, dt.timezone.utc).isoformat()) + ' ' + shlex.quote(self.agent) + ' ' + shlex.quote(execution['id']))
                assert logs and 'database is locked' in logs, '未实际观察到busy timeout造成Journal失败'
                self.events.append({'journal_failure_effect': {'execution_id': execution['id'], 'marker_absent': True, 'journal_absent': True, 'error': logs, 'observed_while_lock_held': self.lock_evidence(info)}})
                self.wait_execution(execution['id'], timeout=50)
                self.check_file(self.node(), marker, 'once')
                assert len(self.state('SELECT id FROM executions WHERE id=?', [execution['id']])) == 1
                assert any(row.get('event') == 'replay_execution' and row.get('id') == execution['id'] for row in self.proxy_events(since))
                assert self.lock_evidence(info)[-1]['event'] == 'lock_released'
            finally:
                self.proxy_controls({})
                time.sleep(max(0, 16 - (time.time()-since)))
                assert self.lock_evidence(info)[-1]['event'] == 'lock_released'
        elif name == 'feature.node.pause':
            before, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id)
            assert before['status'] == 'online'
            marker = self.path('pause-' + uuid.uuid4().hex[:6])
            task = self.task('pause-interval', 'mkdir -p ' + shlex.quote(self.path()) + '; printf "slot\\n" >> ' + shlex.quote(marker))
            interval = self.create('schedule', {'task_id': task['id'], 'type': 'interval', 'interval_sec': 3, 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'skip', 'enabled': True})
            self.wait_schedule(task['id'])
            try:
                for status in ['maintenance', 'disabled']:
                    self.api('POST', '/api/nodes/' + self.core_node_id, {'status': status}); time.sleep(2)
                    count = len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']]))
                    old_marker = self.remote(self.node(), 'cat ' + shlex.quote(marker) + '\n')
                    self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, expected=(400,))
                    skip_marker = self.path('paused-hub-' + status + '-' + uuid.uuid4().hex[:6])
                    skip_task = self.task('paused-hub-' + status, 'printf forbidden > ' + shlex.quote(skip_marker))
                    skip = self.create('schedule', {'task_id': skip_task['id'], 'type': 'one_time', 'run_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=3)).isoformat(), 'timezone': 'UTC', 'execution_owner': 'hub', 'offline_policy': 'hub_online_required', 'misfire_policy': 'run_once', 'enabled': True})
                    time.sleep(5)
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        executions, _, _ = self.api('GET', '/api/executions?task_id=' + skip_task['id'], trace=False)
                        if executions: break
                        time.sleep(1)
                    assert len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])) == count
                    self.check_file(self.node(), marker, old_marker)
                    value, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id)
                    assert value['status'] == status, 'Heartbeat覆盖了管理员暂停状态'
                    executions, _, _ = self.api('GET', '/api/executions?task_id=' + skip_task['id'])
                    assert len(executions) == 1 and executions[0]['status'] == 'SKIPPED' and executions[0].get('block_reason')
                    assert not self.state('SELECT id FROM executions WHERE task_id=?', [skip_task['id']])
                    self.check_file(self.node(), skip_marker, absent=True)
                    self.api('DELETE', '/api/schedules/' + skip['id'])
                    self.events.append({'paused_effect': {'status': status, 'agent_interval_count_unchanged': count, 'hub_schedule': executions, 'manual_http': 400, 'heartbeat_preserved_status': True}})
                self.api('POST', '/api/nodes/' + self.core_node_id, {'status': 'online'}); self.wait_online()
                assert self.run_task(self.task('unpaused-proof', 'printf unpaused-real'))[0]['stdout'] == 'unpaused-real'
                time.sleep(4)
                assert len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])) > count
            finally:
                self.api('POST', '/api/nodes/' + self.core_node_id, {'status': 'online'}); self.wait_online()
                self.api('DELETE', '/api/schedules/' + interval['id'])
        elif name == 'feature.schedule.disable-delete':
            marker = self.path('disable-interval-' + uuid.uuid4().hex[:6])
            task = self.task('disable-interval', 'mkdir -p ' + shlex.quote(self.path()) + '; printf "slot\\n" >> ' + shlex.quote(marker))
            interval = self.create('schedule', {'task_id': task['id'], 'type': 'interval', 'interval_sec': 3, 'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': 'allow_offline', 'misfire_policy': 'skip', 'enabled': True})
            self.wait_schedule(task['id'])
            interval['enabled'] = False; self.api('PUT', '/api/schedules/' + interval['id'], interval)
            time.sleep(2); count = len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']]))
            saved = self.remote(self.node(), 'cat ' + shlex.quote(marker) + '\n')
            guards = []
            for owner in ['agent', 'hub']:
                for kind in ['cron', 'one_time', 'interval']:
                    path = self.path('disabled-' + owner + '-' + kind + '-' + uuid.uuid4().hex[:6])
                    child = self.task('disabled-' + owner + '-' + kind, 'printf forbidden > ' + shlex.quote(path))
                    body = {'task_id': child['id'], 'type': kind, 'timezone': 'UTC', 'execution_owner': owner, 'offline_policy': 'allow_offline' if owner == 'agent' else 'hub_online_required', 'misfire_policy': 'run_once', 'enabled': False}
                    if kind == 'cron': body['expression'] = '* * * * *'
                    elif kind == 'interval': body['interval_sec'] = 3
                    else: body['run_at'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=4)).isoformat()
                    schedule = self.create('schedule', body); guards.append((child, schedule, path))
            since = time.time()
            time.sleep(70)  # 跨完整分钟和45秒Hub tick；由会话工具可中断轮询。
            assert len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])) == count
            self.check_file(self.node(), marker, saved)
            for child, schedule, path in guards:
                rows, _, _ = self.api('GET', '/api/executions?task_id=' + child['id'])
                assert not rows and not self.state('SELECT id FROM executions WHERE task_id=?', [child['id']])
                self.check_file(self.node(), path, absent=True)
                self.api('DELETE', '/api/schedules/' + schedule['id'])
            interval['enabled'] = True; self.api('PUT', '/api/schedules/' + interval['id'], interval)
            time.sleep(5)
            resumed = len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']]))
            assert resumed > count
            self.api('DELETE', '/api/schedules/' + interval['id']); time.sleep(2)
            deleted_count = len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']]))
            time.sleep(7)
            assert len(self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])) == deleted_count
            self.events.append({'disable_delete_effect': {'disabled_observation_start': since, 'disabled_observation_seconds': 70, 'types': ['interval', 'cron', 'one_time'], 'owners': ['agent', 'hub'], 'disabled_execution_absent': True, 'resumed_count': resumed, 'after_delete_count': deleted_count, 'delete_future_seconds': 7}})
        elif name == 'feature.capability.host-operation':
            self.start_proxy()
            node, _, _ = self.api('GET', '/api/nodes/' + self.core_node_id)
            assert node['deployment_mode'] == 'docker'
            artifact = self.upload_artifact(b'capability-block-never-install')
            binary = self.path('capability-' + uuid.uuid4().hex[:6] + '/bin')
            unit = self.prefix + '-capability-' + uuid.uuid4().hex[:6] + '.service'
            app = self.create('application', {'name': unit, 'version': 'qa-cap', 'artifact_id': artifact['id'], 'binary_path': binary, 'config_path': binary + '.conf', 'config': 'never-install', 'unit_name': unit, 'health_check': {'type': 'systemd'}})
            since = time.time()
            for operation in ['deploy', 'upgrade', 'start', 'stop', 'restart']:
                value, _, _ = self.api('POST', '/api/applications/' + app['id'] + '/deploy', {'node_ids': [self.core_node_id], 'operation': operation}, expected=(400,))
                assert 'capability' in str(value), '未实际因缺cap拒绝Host Operation'
            for task_type in ['app_deploy', 'app_operation']:
                task = self.task('cap-' + task_type, type=task_type, application_id=app['id'], app_operation='deploy' if task_type == 'app_deploy' else 'restart')
                value, _, _ = self.api('POST', '/api/tasks/' + task['id'] + '/run', {}, expected=(400,))
                assert 'capability' in str(value)
                assert not self.state('SELECT id FROM executions WHERE task_id=?', [task['id']])
                self.api('DELETE', '/api/tasks/' + task['id'])
            rows = self.proxy_events(since)
            assert not any(row.get('type') == 'RUN_EXECUTION' for row in rows)
            self.check_file(self.node(), binary, absent=True)
            self.check_file(self.node(), '/etc/systemd/system/' + unit, absent=True)
            self.events.append({'capability_rejection': {'node': node, 'app_id': app['id'], 'operations': ['deploy', 'upgrade', 'start', 'stop', 'restart'], 'task_types': ['app_deploy', 'app_operation'], 'run_execution_absent': True, 'binary_unit_absent': True}})
            self.api('DELETE', '/api/applications/' + app['id']); self.api('DELETE', '/api/artifacts/' + artifact['id'])
        elif name == 'feature.transfer.offline-restart':
            target_id = '560a9232-0b15-46c7-9783-4edba87dc401'
            target_container = 'qa-' + RUN_ID + '-env-enroll-docker'
            target = next(node for node in self.cfg['nodes'] if node['id'] == target_id)
            assert target['docker_container'] == target_container
            actual, _, _ = self.api('GET', '/api/nodes/' + target_id)
            assert actual['agent_id'] == 'agent-2dee25e8-c24f-4e01-8440-cb0523115c99' and actual['status'] == 'online'
            mounts = json.loads(self.control('docker inspect -f ' + shlex.quote('{{json .Mounts}}') + ' ' + shlex.quote(target_container)))
            mount = next(item['Source'] for item in mounts if item['Destination'] == '/var/lib/nodesteer')
            source_path = self.path('relay-restart-source-' + uuid.uuid4().hex[:6])
            target_directory = self.path('relay-restart-target-' + uuid.uuid4().hex[:6])
            destination = target_directory + '/received'
            self.run_task(self.task('relay-restart-source', 'mkdir -p ' + shlex.quote(self.path()) + '; dd if=/dev/zero of=' + shlex.quote(source_path) + ' bs=1024 count=512 2>/dev/null; printf relay-resume-proof >> ' + shlex.quote(source_path)))
            expected = self.remote(self.node(), 'sha256sum ' + shlex.quote(source_path) + '\n').split()[0]
            size = int(self.remote(self.node(), 'wc -c < ' + shlex.quote(source_path) + '\n'))
            prepare = 'mkdir -p ' + shlex.quote(target_directory) + '; printf old-destination > ' + shlex.quote(destination)
            self.control('docker exec ' + shlex.quote(target_container) + ' sh -c ' + shlex.quote(prepare))
            physical = mount + destination.removeprefix('/var/lib/nodesteer')
            read_file = 'import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text())'
            identity_code = "import sqlite3,json,hashlib,sys;c=sqlite3.connect('file:'+sys.argv[1]+'/state.db?mode=ro',uri=True);r=c.execute('select key,value from identity order by key').fetchall();print(hashlib.sha256(json.dumps(r).encode()).hexdigest())"
            identity_before = self.control('python3 -c ' + shlex.quote(identity_code) + ' ' + shlex.quote(mount))
            self.control('docker stop -t 2 ' + shlex.quote(target_container))
            try:
                transfer = self.create('transfer', {'source_node_id': self.core_node_id, 'source_path': source_path, 'targets': [{'node_id': target_id, 'destination_path': destination}]})
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    staged, _, _ = self.api('GET', '/api/transfers/' + transfer['id'])
                    if staged.get('sha256') == expected: break
                    time.sleep(1)
                assert staged['sha256'] == expected and staged['size'] == size
                assert staged['targets'][0]['status'] == 'PENDING'
                assert self.control('python3 -c ' + shlex.quote(read_file) + ' ' + shlex.quote(physical)) == 'old-destination'
                self.events.append({'offline_target_staged': {'transfer': staged, 'old_file_unchanged': True, 'source_sha256': expected, 'source_size': size, 'target_container': target_container}})
                self.stop_hub(); time.sleep(3); self.restore_hub()
                recovered, _, _ = self.api('GET', '/api/transfers/' + transfer['id'])
                assert recovered['sha256'] == expected and recovered['size'] == size
                assert self.control('python3 -c ' + shlex.quote(read_file) + ' ' + shlex.quote(physical)) == 'old-destination'
            finally:
                self.control('docker start ' + shlex.quote(target_container))
                self.restore_hub()
            result = self.wait_transfer(transfer['id'], 'SUCCESS', timeout=60)
            actual_sha = self.control('docker exec ' + shlex.quote(target_container) + ' sha256sum ' + shlex.quote(destination)).split()[0]
            assert actual_sha == expected and result['sha256'] == expected
            no_tmp = 'import pathlib,sys;print(json.dumps([p.name for p in pathlib.Path(sys.argv[1]).iterdir()]))'
            names = json.loads(self.control('python3 -c ' + shlex.quote('import json;' + no_tmp) + ' ' + shlex.quote(mount + target_directory.removeprefix('/var/lib/nodesteer'))))
            assert names == ['received'], '目标留下.tmp/part'
            assert self.control('python3 -c ' + shlex.quote(identity_code) + ' ' + shlex.quote(mount)) == identity_before
            final, _, _ = self.api('GET', '/api/nodes/' + target_id)
            assert final['status'] == 'online' and final['sync_status'] == 'synced' and final['agent_id'] == actual['agent_id']
            self.events.append({'relay_restart_delivery': {'transfer': result, 'target_sha256': actual_sha, 'target_directory_names': names, 'target_identity_unchanged': True, 'target_final_node': final}})
            self.api('POST', '/api/transfers/' + transfer['id'] + '/cancel', {})
            self.control('docker exec ' + shlex.quote(target_container) + ' rm -rf -- ' + shlex.quote(target_directory))
            self.remote(self.node(), 'rm -f -- ' + shlex.quote(source_path) + '\n')
        elif name == 'feature.sync.commit-failure-notify':
            self.start_proxy()
            script, task = self.script_fixture('hub-lock')
            before = self.identity(); since = time.time()
            hub_mount = self.control('docker volume inspect -f ' + shlex.quote('{{.Mountpoint}}') + ' ' + shlex.quote(self.cfg['hub_volume']), record=False)
            list_code = 'import pathlib,sys;print("\\n".join(p.name for p in pathlib.Path(sys.argv[1]).glob("*.db")))'
            names = self.control('python3 -c ' + shlex.quote(list_code) + ' ' + shlex.quote(hub_mount)).splitlines()
            assert len(names) == 1 and names[0] in {'hub.db', 'nodesteer.db'}
            info = self.sql_lock(self.cfg['hub_volume'], names[0])
            changed = dict(script); changed['content'] = 'printf commit-after-unlock'
            try:
                value, status, _ = self.api('PUT', '/api/scripts/' + script['id'], changed, expected=(400, 500))
                assert 'locked' in str(value).lower(), 'API未真正因SQLite锁写失败'
                after = self.identity()
                assert before['revision'] == after['revision']
                assert not any(row.get('type') == 'CHANGE_NOTIFICATION' and row.get('event') in {'forward_control', 'duplicate'} for row in self.proxy_events(since))
                assert self.state('SELECT content FROM scripts WHERE id=?', [script['id']])[0]['content'] == script['content']
                self.events.append({'failed_commit_observation': {'status': status, 'old_revision': before['revision'], 'revision_after_failed_mutation': after['revision'], 'notify_absent': True, 'lock_events': self.lock_evidence(info)}})
            finally:
                self.wait_lock_release(info)
            recovered, _, _ = self.api('GET', '/api/scripts/' + script['id'])
            assert recovered['content'] == script['content'] and recovered['revision'] == script['revision']
            recovered = self.update_script(recovered, 'commit-after-unlock'); self.wait_script(recovered)
            assert self.run_task(task)[0]['stdout'] == 'commit-after-unlock'
        else:
            raise Blocked('该精确故障尚需协议代理/Hub故障窗口；先执行已授权真实生命周期')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--case', action='append', required=True)
    parser.add_argument('--allow-hub-faults', action='store_true')
    args = parser.parse_args()
    assert args.config.stat().st_mode & 0o077 == 0
    runner = IsolatedRunner(json.loads(args.config.read_text()), args.allow_hub_faults)
    inventory = json.loads((HERE / 'case-inventory.json').read_text())
    cases = [next(case for case in inventory['cases'] if case['id'] == case_id) for case_id in args.case]
    assert len(cases) == len(set(args.case)), '用例清单未登记全部请求'
    results = [runner.execute(case) for case in cases]
    runner.save(runner.home / 'latest-summary.json', {'results': results})
    raise SystemExit(1 if any(r['status'] == 'failed' for r in results) else 0)


if __name__ == '__main__':
    main()
