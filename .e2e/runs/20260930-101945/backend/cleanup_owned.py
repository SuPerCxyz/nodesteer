#!/usr/bin/env python3
"""仅本轮BE精确ledger/fixture/process；历史不删，容器与卷留给Env统一清理。"""
import collections
import json
from pathlib import Path
import shlex
import sys
import time

from runner import HERE, Runner
from isolated_runner import IsolatedRunner

PRIVATE = Path(sys.argv[1])
normal = Runner(json.loads((PRIVATE / 'connection.json').read_text()), mutations=True, services=True)
normal.token = ''; normal.login(); normal.events = []
transfer_cleanup = []
for row in normal.ledger:
    if row['type'] != 'transfer': continue
    before, _, _ = normal.api('GET', '/api/transfers/' + row['id'])
    after, _, _ = normal.api('POST', '/api/transfers/' + row['id'] + '/cancel', {})
    assert after['status'] == 'CANCELED'
    transfer_cleanup.append({'id': row['id'], 'before_status': before['status'], 'after_status': after['status'], 'action': '产品API Cancel释放Stage；原SUCCESS验证保留于原case证据/历史记录仍存在'})
normal.save(HERE / 'primary-stage-cleanup-result.json', {'transfers': transfer_cleanup, 'events': normal.events})

r = IsolatedRunner(json.loads((PRIVATE / 'isolated.json').read_text()), hub_faults=True)
r.services = True
r.events = []
plan = [{'type': row['type'], 'id': row['id'], 'name': row.get('name'), 'status': row['status']} for row in r.ledger if row['status'] == 'created']
r.save(r.home / 'cleanup-plan.json', {'exact_ledger': plan, 'core_container': r.agent, 'core_volume': r.volume, 'node_id': r.core_node_id, 'history_policy': '不删Execution/Transfer/Audit历史；通过产品API Cancel本轮Transfer释放Stage。污染执行已精确Cancel仍RUNNING，保留来源/证据、不改DB。'})
target_dirs = set()
for row in r.ledger:
    if row['type'] != 'transfer': continue
    before, _, _ = r.api('GET', '/api/transfers/' + row['id'])
    assert before['source_node_id'] == r.core_node_id
    for target in before['targets']:
        assert target['node_id'] == '560a9232-0b15-46c7-9783-4edba87dc401'
        directory = str(Path(target['destination_path']).parent)
        assert directory.startswith(r.path('relay-restart-target-'))
        target_dirs.add(directory)
    r.api('POST', '/api/transfers/' + row['id'] + '/cancel', {})

r.cleanup()
for directory in sorted(target_dirs):
    r.control('docker exec qa-20260930-101945-env-enroll-docker rm -rf -- ' + shlex.quote(directory))
r.remote(r.node(), 'rm -rf -- ' + shlex.quote(r.path()) + '; test ! -e ' + shlex.quote(r.path()) + '\n')

before_identity = r.identity()
proxy = r.proxy_info()
proxy_rows = r.proxy_events() if proxy else []
if proxy:
    r.proxy_controls({})
    r.recreate(hub_url=proxy['original_hub_url']); r.wait_online()
    assert r.identity()['fingerprint'] == before_identity['fingerprint']
    code = "import os,pathlib,signal,sys;p=int(pathlib.Path(sys.argv[1]).read_text());f=pathlib.Path('/proc/'+str(p)+'/cmdline');assert not f.exists() or sys.argv[2].encode() in f.read_bytes().split(bytes([0]));os.kill(p,signal.SIGTERM) if f.exists() else None;print('owned proxy stopped')"
    r.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(proxy['pid_file']) + ' ' + shlex.quote(proxy['source']))

locks = {}
for path in (r.home / 'evidence').glob('*.json'):
    evidence = json.loads(path.read_text())
    for event in evidence.get('events', []):
        if 'sqlite_lock' in event: locks[event['sqlite_lock']['source']] = event['sqlite_lock']
lock_final = []
for info in locks.values():
    events = r.lock_evidence(info)
    assert events[-1]['event'] == 'lock_released'
    code = "import pathlib,sys;p=pathlib.Path('/proc/'+sys.argv[1]+'/cmdline');v=p.read_bytes() if p.exists() else b'';assert sys.argv[2].encode() not in v;print('lock process exited; no action against reused PID')"
    r.control('python3 -c ' + shlex.quote(code) + ' ' + shlex.quote(str(info['pid'])) + ' ' + shlex.quote(info['source']))
    lock_final.append({'info': info, 'events': events, 'active': False})
    for path in [info['source'], info['log'], info['pidfile'], info['source'].removesuffix('.py') + '.stdout']:
        assert path.startswith(r.cfg['remote_root'] + '/' + r.prefix + '-lock-')
        r.control('rm -f -- ' + shlex.quote(path))
if proxy:
    for path in [proxy['source'], proxy['controls'], proxy['events'], proxy['pid_file'], proxy['source'].removesuffix('.py') + '.stdout']:
        assert path.startswith(r.cfg['remote_root'] + '/' + r.prefix + '-proxy')
        r.control('rm -f -- ' + shlex.quote(path))
    proxy.update(cleanup_status='stopped-and-remote-files-removed', final_agent_hub_url=proxy['original_hub_url'])
    r.save(r.home / 'proxy-info.json', proxy)

remaining = []
for kind in ['scripts', 'tasks', 'schedules', 'groups', 'applications', 'artifacts']:
    values, _, _ = r.api('GET', '/api/' + kind)
    remaining.extend({'kind': kind, 'id': value['id'], 'name': value.get('name')} for value in values if str(value.get('name', '')).startswith(r.prefix))
node, _, _ = r.api('GET', '/api/nodes/' + r.core_node_id)
target, _, _ = r.api('GET', '/api/nodes/560a9232-0b15-46c7-9783-4edba87dc401')
settings, _, _ = r.api('GET', '/api/settings')
spec = json.loads(r.control('docker inspect ' + shlex.quote(r.agent), record=False))[0]
actual_url = next(value.split('=', 1)[1] for value in spec['Config']['Env'] if value.startswith('NODESTEER_HUB_URL='))
assert not proxy or actual_url == proxy['original_hub_url']
assert node['status'] == 'online' and node['sync_status'] == 'synced'
assert target['status'] == 'online' and target['sync_status'] == 'synced'
r.save(r.home / 'cleanup-result.json', {'exact_cleanup_plan': 'backend/isolated/cleanup-plan.json', 'own_definition_remaining': remaining, 'ledger_counts': dict((str(k), v) for k, v in collections.Counter((row['type'], row['status']) for row in r.ledger).items()), 'cleanup_errors': [row for row in r.ledger if 'cleanup_error' in row], 'core_node': node, 'authorized_relay_target': target, 'original_core_container_volume_preserved': {'container': r.agent, 'volume': r.volume, 'identity': r.identity()}, 'actual_agent_hub_url': actual_url, 'settings': settings, 'proxy_final': proxy, 'proxy_events_archive': proxy_rows, 'lock_processes': lock_final, 'removed_target_fixture_directories': sorted(target_dirs), 'removed_core_fixture_directory': r.path(), 'polluted_execution_cancel_evidence': 'backend/isolated/pollution-cancel-result.json', 'events': r.events})
print('core cleanup', len(remaining), 'remaining own definitions;', len([row for row in r.ledger if 'cleanup_error' in row]), 'errors; canonical identity/url online/synced; proxy/locks stopped; containers/volumes preserved')
