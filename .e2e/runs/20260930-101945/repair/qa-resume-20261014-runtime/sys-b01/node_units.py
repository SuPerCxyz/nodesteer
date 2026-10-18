#!/usr/bin/env python3
"""Read-only unit inventory for the 4 protected repair native nodes + the
primary Hub VM.  Used to prove that no protected unit was restarted by this
batch: the same probe is run before and after the batch and the MainPIDs and
ExecMainStartTimestamp values must be identical.

No writes, no configuration reads.
"""
import json
import subprocess
import sys
from pathlib import Path

SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')
NODES = {
    'native-1': ('192.168.100.204', 'nodesteer-agent-repair-20260930-101945-native-1.service'),
    'native-2': ('192.168.100.206', 'nodesteer-agent-repair-20260930-101945-native-2.service'),
    'native-3': ('192.168.100.222', 'nodesteer-agent-repair-20260930-101945-native-3.service'),
    'native-4': ('192.168.100.247', 'nodesteer-agent-repair-20260930-101945-native-4.service'),
}
HUB = ('192.168.100.209', 'nodesteer-hub.service', 'nodesteer-hub-repair-20260930-101945.service')

PROBE = (
    "import json,subprocess\n"
    "units=%r\n"
    "out={}\n"
    "for u in units:\n"
    "    r=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,SubState,"
    "MainPID,ExecMainStartTimestamp',u],capture_output=True,text=True)\n"
    "    out[u]=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
    "print(json.dumps(out))\n"
)


def probe(host, units, timeout=40):
    ssh = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=8', '-i', SSH_KEY, 'root@' + host]
    cp = subprocess.run(ssh + ['python3', '-'], input=PROBE % units, capture_output=True,
                        text=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError('unit_probe_failed:%s:%s' % (host, cp.stderr[-200:]))
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    return json.loads(lines[-1])


def main():
    out = {}
    for label, (ip, unit) in NODES.items():
        out[label] = {'ip': ip, 'units': probe(ip, [unit, 'nodesteer-agent.service'])}
    out['hub-vm'] = {'ip': HUB[0], 'units': probe(HUB[0], [HUB[1], HUB[2]])}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
