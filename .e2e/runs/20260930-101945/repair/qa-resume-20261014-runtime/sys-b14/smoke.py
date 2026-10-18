#!/usr/bin/env python3
"""Smoke test: guard -> children -> one end-to-end transfer -> cleanup/oracle.
No Hub restart. Used only to de-risk the full sys-b14 run."""
import json
import sys

import run_sysb14 as R
import xferlib as X

run = R.Run()
run.state['units_before'] = X.unit_probe()
try:
    g = R.phase0_guard(run)
    print('GUARD ok protected nodes:', list(g['protected_nodes_before']))
    c = R.phase1_children(run)
    print('CHILDREN ok:', {k: v['node_id'] for k, v in c['children'].items()})
    m = R.phase2_main_transfer(run)
    run.state['main_tid'] = m['transfer_id']
    print('TRANSFER ok:', m['transfer_id'], m['final']['status'], m['node_side_dst'].get('sha256') == m['source']['sha256'])
    print('NEG auth transfer unchanged test -> quick download-as-source:')
    code, _, body = run.hub.agent_download(m['transfer_id'], run.child('src').agent_id, run.state['src_cred'])
    print('  source download http=', code, X.decode(body))
    code2, _, body2 = run.hub.agent_upload(m['transfer_id'], run.child('tgt').agent_id, run.state['tgt_cred'],
                                          b'x' * 16, declared_size=16, content_range='bytes 0-15/16')
    print('  target upload http=', code2, X.decode(body2))
finally:
    for n in ('src_proxy', 'tgt_proxy'):
        p = run.samples.get(n)
        if p:
            try:
                p.set_mode('pass')
                p.stop()
            except Exception:
                pass
    cleanup = R.phase8_cleanup(run)
    run.state.setdefault('main_tid', None)
    oracle = R.phase9_oracle(run, cleanup)
    print('CLEANUP:', json.dumps({k: cleanup[k] for k in cleanup if k != 'children_removed'}, ensure_ascii=False))
    print('ORACLE clean:', oracle['clean'])
sys.exit(0)
