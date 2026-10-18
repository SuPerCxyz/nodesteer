#!/usr/bin/env python3
"""Read-only capture of the live binary identity behind the repair authority (:18080)
and of the repair agent on the scheduling target (native-1)."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY = str(Path.home() / '.ssh' / 'id_ed25519')
HUB = '192.168.100.209'
N1 = '192.168.100.204'

HUB_CMD = ('ss -ltnp 2>/dev/null | grep -E ":18080|:8080"; '
           'echo "--- exe of :18080 listener"; '
           'p=$(ss -ltnp 2>/dev/null | grep ":18080" | grep -oP "pid=\\K[0-9]+" | head -1); '
           'readlink /proc/$p/exe; sha256sum /proc/$p/exe; '
           'echo "--- all repair hub binaries"; '
           'sha256sum /opt/nodesteer-repair-20260930-101945/bin/*')
N1_CMD = ('echo "--- agent exe"; readlink /proc/1494/exe; sha256sum /proc/1494/exe; '
          'echo "--- configured binary"; '
          'sha256sum /opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent; '
          'echo "--- units"; systemctl is-active '
          'nodesteer-agent-repair-20260930-101945-native-1.service nodesteer-agent.service')


def sh(host, cmd):
    cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-o', 'ConnectTimeout=8', '-i', KEY, 'root@' + host, cmd],
                        capture_output=True, text=True, timeout=40)
    return {'rc': cp.returncode, 'stdout': cp.stdout, 'stderr': cp.stderr}


out = {'captured_at_utc': datetime.now(timezone.utc).isoformat(),
       'method': 'readlink /proc/<pid>/exe of the :18080 listener + sha256sum on the same file '
                 '(read-only)',
       'hub_host_18080': sh(HUB, HUB_CMD),
       'agent_native1': sh(N1, N1_CMD)}
(HERE / 'evidence' / 'phase9-live-binaries.json').write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(out, ensure_ascii=False, indent=1))
