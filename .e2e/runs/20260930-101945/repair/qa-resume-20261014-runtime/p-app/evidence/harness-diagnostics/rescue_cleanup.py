#!/usr/bin/env python3
"""Rescue cleanup: remove any owned child/unit/process/dir left on native-4 and any owned
Hub fixtures for this nonce.  Safe to run repeatedly."""
import json
import sys
import time

import hostlib
import hubapi
import run_papp

out = {'at_utc': hostlib.now_utc(), 'steps': {}}
hub = hubapi.Hub()
hub.login()

# prefix-wide sweep first: any *qa-papp* unit file / want symlink is ours (exclusive nonce prefix)
out['steps']['prefix_sweep'] = hostlib.OwnedChild(run_papp.NONCE, '').remote_py(
    "import json,os,shutil,subprocess\n"
    "tag='qa-papp'\n"
    "out={'units':[],'wants':[],'dirs':[],'mounts':[]}\n"
    "def run(c):\n"
    "    r=subprocess.run(c,capture_output=True,text=True)\n"
    "    return {'rc':r.returncode,'err':r.stderr.strip()[:150]}\n"
    "w='/etc/systemd/system/multi-user.target.wants'\n"
    "for n in sorted(os.listdir(w)) if os.path.isdir(w) else []:\n"
    "    if tag in n:\n"
    "        p=os.path.join(w,n); out['wants'].append({'name':n,'rc':run(['rm','-f',p])})\n"
    "for n in sorted(os.listdir('/etc/systemd/system')):\n"
    "    p='/etc/systemd/system/'+n\n"
    "    if tag in n:\n"
    "        u=n\n"
    "        run(['systemctl','stop',u]); run(['systemctl','disable',u])\n"
    "        out['units'].append({'name':n,'remove':run(['rm','-f',p])})\n"
    "run(['systemctl','daemon-reload'])\n"
    "for base in ('/opt','/var/lib'):\n"
    "    for n in sorted(os.listdir(base)):\n"
    "        if n.startswith(tag):\n"
    "            p=os.path.join(base,n)\n"
    "            try:\n"
    "                if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)\n"
    "                else: os.remove(p)\n"
    "                out['dirs'].append(p)\n"
    "            except Exception as e:\n"
    "                out['dirs'].append(p+':ERR:'+type(e).__name__)\n"
    "print(json.dumps(out))\n")

host = {}
for tag in ('', '-b', '-c'):
    ch = hostlib.OwnedChild(run_papp.NONCE, tag)
    ch.seam(ch.data + '/applications', 'chattr_minus_i')
    ch.seam(ch.data + '/artifacts', 'chattr_minus_i')
    ch.seam(ch.opt_root + '/app/bin', 'chattr_minus_i')
    for n in ('multi', 'partial', 'blocked', 'cancel'):
        ch.seam(ch.root + '/dst-' + n, 'umount')
    host[tag or 'a'] = {'cleanup': ch.unit_cleanup('', '', '', ch.opt_root,
                                                   extra_files=[ch.root + '/dst-' + n + '/out.bin'
                                                                for n in ('multi', 'partial', 'blocked', 'cancel')]),
                        'remove': ch.remove()}
out['steps']['host'] = host

foreign = 'nodesteer-' + run_papp.PREFIX + 'foreign.service'
out['steps']['foreign_unit'] = hostlib.OwnedChild(run_papp.NONCE, '').remote_py(
    "import json,os,subprocess\n"
    "u=%r; p='/etc/systemd/system/'+u\n"
    "def run(c):\n"
    "    r=subprocess.run(c,capture_output=True,text=True)\n"
    "    return {'rc':r.returncode,'err':r.stderr.strip()[:150]}\n"
    "out={'stop':run(['systemctl','stop',u]),'disable':run(['systemctl','disable',u])}\n"
    "if os.path.lexists(p): os.remove(p)\n"
    "run(['systemctl','daemon-reload']); run(['systemctl','reset-failed',u])\n"
    "out['remaining']=os.path.lexists(p)\n"
    "print(json.dumps(out))\n" % foreign)

deleted = {}
for label, objs, attr, path in (('applications', hub.json('/api/applications'), 'name',
                                 '/api/applications/'),
                                ('artifacts', hub.json('/api/artifacts'), 'name', '/api/artifacts/')):
    for o in objs:
        if (o.get(attr) or '').startswith(run_papp.PREFIX):
            code, body = hub.delete(path + hub.quote(o['id']))
            deleted[o['id']] = {'kind': label, 'name': o[attr], 'delete_http': code,
                                'get_http': hub.get(path + hub.quote(o['id']))[0]}
for attempt in range(4):
    pending = [n for n in hub.json('/api/nodes') if (n.get('hostname') or '').startswith(run_papp.PREFIX)]
    if not pending:
        break
    for n in pending:
        code, body = hub.delete('/api/nodes/' + hub.quote(n['id']))
        deleted[n['id']] = {'kind': 'node', 'hostname': n['hostname'], 'attempt': attempt,
                            'delete_http': code, 'body': body,
                            'get_http': hub.get('/api/nodes/' + hub.quote(n['id']))[0]}
    time.sleep(3)
out['steps']['hub_deletes'] = deleted

out['host_after'] = hostlib.OwnedChild(run_papp.NONCE).host_baseline()
leftovers = {}
for label, objs, attr in (('artifacts', hub.json('/api/artifacts'), 'name'),
                          ('applications', hub.json('/api/applications'), 'name'),
                          ('nodes', hub.json('/api/nodes'), 'hostname')):
    leftovers[label] = [o.get(attr) for o in objs if (o.get(attr) or '').startswith(run_papp.PREFIX)]
out['leftovers'] = leftovers
b = out['host_after']
out['clean'] = (not b['root_exists'] and not b['opt_root_exists'] and not b['global_owned_prefix_scan']
                and not b['owned_child_processes'] and not b['owned_units']
                and not any(leftovers.values()))
hubapi.write_json('rescue-cleanup.json', out)
print(json.dumps({'clean': out['clean'], 'leftovers': leftovers,
                  'root_exists': b['root_exists'], 'opt_root_exists': b['opt_root_exists'],
                  'prefix_scan': b['global_owned_prefix_scan'],
                  'child_processes': b['owned_child_processes'],
                  'protected_units': {k: v.get('ActiveState') for k, v in
                                      (('primary', b['primary_agent_unit']), ('repair', b['repair_agent_unit']))}},
                 ensure_ascii=False, indent=1))
sys.exit(0 if out['clean'] else 1)
