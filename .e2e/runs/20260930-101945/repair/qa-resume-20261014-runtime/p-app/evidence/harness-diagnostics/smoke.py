#!/usr/bin/env python3
"""Pre-flight smoke test for the p-app harness: guard, owned children, helper scripts.

Read-only against the repair Hub except for the owned child enrollment; everything
created here is removed again before the script exits.
"""
import json
import sys
import time

import hostlib
import hubapi
import run_papp

out = {'at_utc': hostlib.now_utc()}
run = run_papp.Run()
print('endpoint', run.hub.base)
out['guard'] = run.phase0_guard()
print('guard ok:', out['guard']['guard_ok'])
out['children'] = run.phase1_children()
print('children started:', list(out['children']['children']))

# helper scripts must be syntactically valid on the remote host and the journal probe must work
probe = {}
assert run.results['children']['distinct_node_identities'] is True
for key in ('a', 'b', 'c'):
    ch = run.child[key]
    probe[key] = ch.remote_py(
        "import ast,json,os\n"
        "root=%r\n"
        "res={}\n"
        "for n in ('phasewatch.py','netsample.py','dirwatch.py'):\n"
        "    p=os.path.join(root,n)\n"
        "    try:\n"
        "        ast.parse(open(p).read()); res[n]='ok'\n"
        "    except Exception as e:\n"
        "        res[n]='ERR:'+type(e).__name__+':'+str(e)[:120]\n"
        "print(json.dumps(res))\n" % ch.root)
    probe[key]['journal'] = ch.deployments()
    probe[key]['app_state_ok'] = 'db_error' not in ch.app_state('', '', '/', '/')
    probe[key]['log_bytes'] = ch.log_tail(2000)['size']
out['probe'] = probe
print(json.dumps(probe, ensure_ascii=False)[:1200])

# one short watcher with an explicit row id that does not exist -> must not kill anything
w = run.child['a'].start_watcher('smoke', '', '/nonexistent', row_id='does-not-exist',
                                 mode='observe', timeout=3)
time.sleep(5)
rw = run.child['a'].read_watcher('smoke', wait_exit=True, timeout=20)
out['watcher_smoke'] = {'start': w, 'exited': rw['exited'], 'phases': rw['phase_sequence'],
                        'rows': rw['rows']}
print('watcher:', out['watcher_smoke'])
alive = run.child['a'].process_alive()
out['child_alive_after_watcher'] = alive

cleanup = {}
for key in ('a', 'b', 'c'):
    ch = run.child[key]
    cleanup[key] = {'remove': ch.remove()}
for key in ('a', 'b', 'c'):
    nid = run.state['nodes'][key]
    code, body = run.hub.delete('/api/nodes/' + run.hub.quote(nid))
    cleanup[key]['node_delete'] = {'http': code, 'get': run.hub.get('/api/nodes/' + run.hub.quote(nid))[0]}
    for label, objs, attr in (('artifacts', run.hub.json('/api/artifacts'), 'name'),
                              ('applications', run.hub.json('/api/applications'), 'name'),
                              ('nodes', run.hub.json('/api/nodes'), 'hostname')):
        cleanup.setdefault('leftovers_' + label, [o.get(attr) for o in objs
                                                  if (o.get(attr) or '').startswith(run_papp.PREFIX)])
out['cleanup'] = cleanup
out['host_after'] = hostlib.OwnedChild(run_papp.NONCE).host_baseline()
out['ok'] = (not out['host_after']['root_exists'] and not out['host_after']['opt_root_exists']
             and not out['host_after']['global_owned_prefix_scan'])
hubapi.write_json('smoke-result.json', out)
print('host clean:', out['ok'])
sys.exit(0 if out['ok'] else 1)
