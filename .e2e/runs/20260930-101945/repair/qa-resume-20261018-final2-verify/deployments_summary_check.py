#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: item 11 — deployments-summary.json on real agents.

Checks the read-only recovery snapshot on all four repair agent data dirs:
existence, JSON structure, RFC3339 generated_at, atomic-write residue, mode.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import finallib as L  # noqa: E402

os.umask(0o077)
RFC3339 = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$')

DATA = {
    'native-1': ('192.168.100.204', '/var/lib/nodesteer-repair-20260930-101945/native-1'),
    'native-2': ('192.168.100.206', '/var/lib/nodesteer-repair-20260930-101945/native-2'),
    'native-3': ('192.168.100.222', '/var/lib/nodesteer-repair-20260930-101945/native-3'),
    'native-4': ('192.168.100.247', '/var/lib/nodesteer-repair-20260930-101945/native-4'),
}


def main():
    out = {'at_utc': L.now_utc(), 'nodes': {}, 'verdict': {}}
    for name, (ip, data_dir) in DATA.items():
        p = data_dir + '/deployments-summary.json'
        rc, stdout, err = L.ssh(ip, 'cat %s 2>/dev/null; echo "---"; stat -c "%%a %%U:%%G %%s %%y" %s 2>/dev/null; echo "---"; ls %s/*.tmp 2>/dev/null | head -3; echo "---"; stat -c "%%Y" %s 2>/dev/null' % (p, p, data_dir, p))
        parts = [x.strip() for x in stdout.split('---')]
        rec = {'path': p, 'ssh_rc': rc}
        try:
            rec['content'] = json.loads(parts[0]) if parts[0] else None
        except ValueError as exc:
            rec['content'] = None
            rec['parse_error'] = str(exc)
        rec['stat'] = parts[1] if len(parts) > 1 else None
        rec['tmp_leftovers'] = [x for x in (parts[2].splitlines() if len(parts) > 2 else []) if x.strip()]
        rec['mtime_epoch'] = parts[3] if len(parts) > 3 else None
        c = rec['content']
        checks = {
            'exists': isinstance(c, dict),
            'generated_at_rfc3339': isinstance((c or {}).get('generated_at'), str)
                                    and bool(RFC3339.match((c or {}).get('generated_at', ''))),
            'done_by_kind_int_map': isinstance((c or {}).get('done_by_kind'), dict)
                                    and all(isinstance(v, int) for v in (c or {}).get('done_by_kind', {}).values()),
            'active_is_list': isinstance((c or {}).get('active'), list),
            'active_rows_shape': all(isinstance(r, dict) and 'id' in r and 'application_id' in r
                                     and 'phase' in r for r in (c or {}).get('active', [])),
            'no_tmp_leftover': not rec['tmp_leftovers'],
        }
        rec['checks'] = checks
        rec['ok'] = all(checks.values())
        out['nodes'][name] = rec
    out['verdict'] = {k: v['ok'] for k, v in out['nodes'].items()}
    out['all_ok'] = all(out['verdict'].values())
    p = HERE / 'evidence' / 'deployments-summary-check.json'
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, p)
    print(json.dumps({'all_ok': out['all_ok'], 'verdict': out['verdict'],
                      'contents': {k: v['content'] for k, v in out['nodes'].items()}},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
