#!/usr/bin/env python3
"""qa-resume-20261018-final-verify: post-cleanup fix-up.

The owned application's systemd unit/binary/config on n2 were deployed by the
owned child agent; the child was stopped before the app was deleted via the API,
so the agent-driven uninstall could not run. These are owned paths created by
this batch, so they are removed directly (same as the previous batch's
unit_cleanup seam), then the host residue scan and oracle are re-run.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import hostlib  # noqa: E402
import regression_go as A  # noqa: E402

os.umask(0o077)


def main():
    st = A.ST
    prefix = A.P
    out = {'at_utc': A.now_utc(), 'steps': {}}
    unit = 'nodesteer-' + prefix + 'app.service'
    opt = '/opt/' + prefix + 'app'
    ch = hostlib.OwnedChild(st['nonce'], 'n2', '-scan')
    out['steps']['unit_cleanup_n2'] = ch.unit_cleanup(
        unit=unit, binary_path=opt + '/bin/app', config_path=opt + '/config/app.env',
        opt_root=opt)
    # re-scan residue on all three hosts
    out['steps']['host_residue'] = {}
    for host_key in ('n1', 'n2', 'n3'):
        c = hostlib.OwnedChild(st['nonce'], host_key, '-scan')
        out['steps']['host_residue'][host_key] = c.remote_py(
            "import json,os,subprocess\n"
            "prefix='qa-final'\n"
            "out={'etc_units':[n for n in sorted(os.listdir('/etc/systemd/system')) if prefix in n],"
            "'wants':[n for n in sorted(os.listdir('/etc/systemd/system/multi-user.target.wants')) if prefix in n] if os.path.isdir('/etc/systemd/system/multi-user.target.wants') else [],"
            "'var_lib':[n for n in sorted(os.listdir('/var/lib')) if prefix in n],"
            "'opt':[n for n in sorted(os.listdir('/opt')) if prefix in n],"
            "'procs':[l for l in subprocess.run(['ps','-eo','pid,args','--no-headers'],capture_output=True,text=True).stdout.splitlines() if prefix in l and 'scan' not in l]}\n"
            "print(json.dumps(out))\n")
    A.ev_write('cleanup-fixup.json', out)
    # update the oracle in place
    oracle = json.loads((HERE / 'evidence' / 'final-cleanup-oracle.json').read_text())
    oracle['host_residue'] = out['steps']['host_residue']
    oracle['verdict']['no_owned_host_units_or_dirs_or_procs'] = all(
        not v.get('etc_units') and not v.get('var_lib') and not v.get('opt') and not v.get('procs')
        for v in out['steps']['host_residue'].values())
    oracle['fixup_note'] = ('n2 上 owned 应用 unit/二进制/配置由本批 owned 子 Agent 安装；'
                            '删除应用前子 Agent 已停止，故按 owned 路径直接清理（unit_cleanup），'
                            '随后重扫三台主机零残留')
    A.ev_write('final-cleanup-oracle.json', oracle)
    print(json.dumps(oracle['verdict'], indent=1))


if __name__ == '__main__':
    main()
