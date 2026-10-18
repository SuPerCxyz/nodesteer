#!/usr/bin/env python3
"""Diagnostic #2: replay harness phases 0-5 and dump the raw journal/registry/log."""
import json
import time
import traceback

import hostlib
import hubapi
import run_papp

run = run_papp.Run()
dumps = {}


def dump(tag):
    try:
        ch = run.child['a']
        raw = ch.remote_py(
            "import json,os,sqlite3\n"
            "data=%r\n"
            "db=os.path.join(data,'state.db')\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5)\n"
            "out={'db':db,'exists':os.path.isfile(db)}\n"
            "out['counts']={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in "
            "('identity','sync_state','applications','artifacts','executions','deployments','unit_registry')}\n"
            "out['deployments']=[list(r) for r in c.execute('SELECT id,application_id,from_version,to_version,phase,backup_path,started_at FROM deployments ORDER BY started_at')]\n"
            "out['unit_registry']=[list(r) for r in c.execute('SELECT * FROM unit_registry')]\n"
            "out['executions']=[list(r) for r in c.execute('SELECT id,application_id,application_operation,status,start_time,end_time FROM executions ORDER BY start_time')]\n"
            "out['applications']=[list(r) for r in c.execute('SELECT id,revision FROM applications')]\n"
            "out['identity']=[list(r) for r in c.execute('SELECT key,value FROM identity')]\n"
            "out['data_ls']=sorted(os.listdir(data))\n"
            "print(json.dumps(out,default=str))\n" % ch.data)
        raw['hub_executions'] = run.hub.get('/api/applications/%s/executions'
                                           % run.hub.quote(run.state['apps']['main']))[1]
        raw['log'] = run.child['a'].log_tail(8000)['tail']
        raw['host'] = {'unit': run.child['a'].inventory(run_papp.UNIT)['owned_unit_state'],
                       'unit_file': run.child['a'].inventory(run_papp.UNIT)['owned_unit_file_exists']}
        dumps[tag] = raw
    except Exception as exc:
        dumps[tag] = {'dump_error': '%s: %s' % (type(exc).__name__, exc)}


for name, fn in (('guard', run.phase0_guard), ('children', run.phase1_children),
                 ('artifacts', run.phase2_artifacts), ('app_create', run.phase3_app_create)):
    try:
        fn()
        print('phase', name, 'ok')
        dump(name)
    except Exception:
        traceback.print_exc()
        dump(name + '_FAILED')
        break
try:
    dumps['install_v1'] = run.phase4_install_v1()
    print('phase install_v1 ok')
except Exception:
    traceback.print_exc()
    dumps['install_v1_error'] = 'failed'
dump('after_install')

hubapi.write_json('diag2-result.json', dumps)
print(json.dumps({'after_install_counts': (dumps.get('after_install') or {}).get('counts'),
                  'deployments': (dumps.get('after_install') or {}).get('deployments'),
                  'unit_registry': (dumps.get('after_install') or {}).get('unit_registry'),
                  'identity': (dumps.get('after_install') or {}).get('identity'),
                  'hub_executions': [(e.get('id'), e.get('status'), e.get('start_time'))
                                     for e in ((dumps.get('after_install') or {}).get('hub_executions') or [])],
                  'phase4_evidence': {k: v for k, v in (dumps.get('install_v1') or {}).items()
                                      if k in ('install_ok', 'stage_order_observed', 'journal_phase_sequence')}},
                 ensure_ascii=False, indent=1))
print('--- log ---')
print((dumps.get('after_install') or {}).get('log', '')[-4000:])
