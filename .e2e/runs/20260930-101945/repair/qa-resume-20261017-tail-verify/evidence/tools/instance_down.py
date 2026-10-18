#!/usr/bin/env python3
"""销毁隔离实例并逐项核验零残留。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tail_common as C  # noqa: E402

EV = HERE.parent
OUT = EV / "final-cleanup-oracle.json"


def main() -> None:
    cp = C.ssh("""
PID=$(pgrep -f 'qa-tail[0-9a-z]*-admin\\|qa-tail.*\\.yaml' | head -1)
ALL=$(pgrep -af 'nodesteer-qa-tail' || true)
echo "PROCS:$ALL"
for p in $(pgrep -f 'nodesteer-qa-tail' || true); do kill "$p" 2>/dev/null || true; done
sleep 3
for p in $(pgrep -f 'nodesteer-qa-tail' || true); do kill -9 "$p" 2>/dev/null || true; done
sleep 1
echo "PROCS_AFTER:$(pgrep -f 'nodesteer-qa-tail' | wc -l)"
rm -rf /var/lib/nodesteer-qa-tail* /var/log/nodesteer-qa-tail*.log /etc/nodesteer/qa-tail*.yaml
echo "DATADIR_LEFT:$(ls -d /var/lib/nodesteer-qa-tail* 2>/dev/null | wc -l)"
echo "CFG_LEFT:$(ls /etc/nodesteer/qa-tail*.yaml 2>/dev/null | wc -l)"
echo "LOG_LEFT:$(ls /var/log/nodesteer-qa-tail*.log 2>/dev/null | wc -l)"
echo "PORT_18088:$(ss -ltn | grep -c ':18088 ' || true)"
echo "PORT_18086:$(ss -ltn | grep -c ':18086 ' || true)"
RP=$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1); echo "REPAIR_PID:$RP"
echo "REPAIR_EXE_SHA:$(sha256sum /proc/$RP/exe 2>/dev/null | awk '{print $1}')"
PP=$(pgrep -f 'nodesteer-hub --config /etc/nodesteer/hub.yaml' | head -1); echo "PRIMARY_PID:$PP"
echo "REPAIR_18080_LISTEN:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_8080_LISTEN:$(ss -ltn | grep -c ':8080 ' || true)"
echo "AGENT_UNITS:$(systemctl list-units --type=service --all 2>/dev/null | grep -c 'nodesteer-agent')"
echo "QA_UNITS:$(find /etc/systemd/system -name 'nodesteer-qa-*.service' | wc -l)"
echo "TMP_CREDS:$(ls /tmp/opencode/tail-creds-*.json 2>/dev/null | wc -l)"
""", check=False)
    d = {}
    for ln in cp.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k] = v.strip()
    d["at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    d["verdict"] = "ISOLATED_INSTANCE_DESTROYED" if (
        d.get("PROCS_AFTER") == "0" and d.get("PORT_18088") == "0"
        and d.get("DATADIR_LEFT") == "0" and d.get("CFG_LEFT") == "0"
    ) else "RESIDUE_DETECTED"
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(d, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
