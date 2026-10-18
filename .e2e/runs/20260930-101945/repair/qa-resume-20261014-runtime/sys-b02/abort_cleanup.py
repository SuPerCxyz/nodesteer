#!/usr/bin/env python3
"""Abort-recovery cleanup for sys-b02 attempt01 leftovers (own fixtures only)."""
import json
import subprocess
import sys
import time
from pathlib import Path

import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb02-%s-' % NONCE
ROOT = '/var/lib/nodesteer-qa-sysb02-%s' % NONCE
CFG = ROOT + '/child.yaml'
AGENT = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@192.168.100.204']


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'prefix': PREFIX, 'at_epoch': time.time(), 'actions': []}
    nodes = hub.json('/api/nodes')
    targets = [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]
    scripts = [s['id'] for s in hub.json('/api/scripts') if (s.get('name') or '').startswith(PREFIX)]
    tasks = [t['id'] for t in hub.json('/api/tasks') if (t.get('name') or '').startswith(PREFIX)]
    schedules = [s['id'] for s in hub.json('/api/schedules') if (s.get('name') or '').startswith(PREFIX)]
    out['found'] = {'nodes': targets, 'scripts': scripts, 'tasks': tasks, 'schedules': schedules}
    for kind, ids in (('schedule', schedules), ('task', tasks), ('script', scripts)):
        for oid in ids:
            path = '/api/%ss/%s' % (kind, hub.quote(oid))
            code, _ = hub.delete(path)
            gcode, _ = hub.get(path)
            out['actions'].append({'kind': kind, 'id': oid, 'delete_http': code, 'get_http': gcode})
    remote = (
        "import json,os,signal,shutil,subprocess,time\n"
        "root=%r; cfg=%r; agent=%r\n"
        "def matched():\n"
        "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
        "    out=[]\n"
        "    for tok in p.stdout.split():\n"
        "        if not tok.isdigit():\n"
        "            continue\n"
        "        pid=int(tok)\n"
        "        q=subprocess.run(['readlink','-f','/proc/'+tok+'/exe'],capture_output=True,text=True).stdout.strip()\n"
        "        if q==agent: out.append(pid)\n"
        "    return out\n"
        "pids=matched()\n"
        "for pid in pids:\n"
        "    os.kill(pid, signal.SIGTERM)\n"
        "end=time.time()+12\n"
        "while time.time()<end and matched():\n"
        "    time.sleep(0.5)\n"
        "left=matched()\n"
        "for pid in left:\n"
        "    os.kill(pid, signal.SIGKILL)\n"
        "time.sleep(1)\n"
        "left=matched()\n"
        "existed=os.path.isdir(root)\n"
        "if existed: shutil.rmtree(root)\n"
        "q=subprocess.run(['pgrep','-af','nodesteer-qa-sysb02'],capture_output=True,text=True)\n"
        "print(json.dumps({'signalled':pids,'remaining':left,'root_removed':existed,"
        "'root_exists_after':os.path.exists(root),'any_left':[l for l in q.stdout.splitlines() if l.strip()]}))\n"
    ) % (ROOT, CFG, AGENT)
    cp = subprocess.run(SSH + ['python3', '-'], input=remote, capture_output=True, text=True, timeout=120)
    out['native_cleanup'] = json.loads([l for l in cp.stdout.splitlines() if l.strip()][-1]) if cp.stdout.strip() else {
        'error': cp.stderr[-300:]}
    for nid in targets:
        code, body = hub.delete('/api/nodes/' + hub.quote(nid))
        gcode, _ = hub.get('/api/nodes/' + hub.quote(nid))
        out['actions'].append({'kind': 'node', 'id': nid, 'delete_http': code, 'get_http': gcode,
                               'body': body if isinstance(body, str) else None})
    time.sleep(2)
    scripts_after = [s['id'] for s in hub.json('/api/scripts') if (s.get('name') or '').startswith(PREFIX)]
    tasks_after = [t['id'] for t in hub.json('/api/tasks') if (t.get('name') or '').startswith(PREFIX)]
    nodes_after = [n['id'] for n in hub.json('/api/nodes') if (n.get('hostname') or '').startswith(PREFIX)]
    out['after'] = {'scripts': scripts_after, 'tasks': tasks_after, 'nodes': nodes_after}
    out['clean'] = not (scripts_after or tasks_after or nodes_after) and \
        not out['native_cleanup'].get('root_exists_after') and not out['native_cleanup'].get('any_left')
    hubapi.write_json('evidence/attempt01-abort-cleanup.json', out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out['clean'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
