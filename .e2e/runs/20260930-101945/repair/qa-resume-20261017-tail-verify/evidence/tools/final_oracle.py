#!/usr/bin/env python3
"""tail-verify 批次收尾核验：repair :18080 残留、.204 残留、隔离实例、端口、进程。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402
import tail_common as C  # noqa: E402

EV = HERE.parent
OUT = EV / "final-cleanup-oracle.json"


def main():
    h = hubclient.Hub()
    h.login()
    res = {"at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "authority": h.base}
    # 1) 本批自有 fixture 是否已全部 404
    deleted = json.loads((EV / "cleanup-part1.json").read_text())
    res["deleted_fixtures"] = deleted
    res["deleted_all_404"] = all(d.get("after_get") == 404 for d in deleted)

    # 2) repair :18080 现存对象清单（如实列出，含历史遗留）
    for kind, path in (("nodes", "/api/nodes"), ("applications", "/api/applications"),
                       ("scripts", "/api/scripts"), ("tasks", "/api/tasks"),
                       ("schedules", "/api/schedules"), ("artifacts", "/api/artifacts"),
                       ("groups", "/api/groups"), ("transfers", "/api/transfers"),
                       ("users", "/api/users")):
        code, body = h.raw("GET", path)
        rows = body if isinstance(body, list) else (body.get("items") if isinstance(body, dict) else [])
        rows = rows or []
        res.setdefault("existing", {})[kind] = {
            "http": code, "count": len(rows),
            "names": sorted({str(r.get("name") or r.get("hostname") or r.get("id"))[:48] for r in rows})[:12],
        }
    # 本批自有前缀是否清零
    qa = res["existing"]["nodes"]["names"]
    res["owned_prefix_left"] = {
        "nodes_qa_tail": [n for n in qa if n.startswith("qa-tail")],
        "apps_qa_tail": [n for n in res["existing"]["applications"]["names"] if n.startswith("qa-tail")],
        "scripts_qa_tail": [n for n in res["existing"]["scripts"]["names"] if n.startswith("qa-tail")],
        "tasks_qa_tail": [n for n in res["existing"]["tasks"]["names"] if n.startswith("qa-tail")],
    }
    res["owned_residue_zero"] = all(len(v) == 0 for v in res["owned_prefix_left"].values())

    # 3) 隔离实例 / 端口 / 进程 / systemd（.209）
    cp = C.ssh("""
echo "PORT_18088:$(ss -ltn | grep -c ':18088 ' || true)"
echo "PORT_18086:$(ss -ltn | grep -c ':18086 ' || true)"
echo "PORT_18080:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PORT_8080:$(ss -ltn | grep -c ':8080 ' || true)"
echo "QA_PROCS:$(pgrep -f 'nodesteer-qa-tail' | wc -l)"
echo "QA_DATADIR:$(ls -d /var/lib/nodesteer-qa-tail* 2>/dev/null | wc -l)"
echo "QA_CFG:$(ls /etc/nodesteer/qa-tail*.yaml 2>/dev/null | wc -l)"
echo "QA_LOG:$(ls /var/log/nodesteer-qa-tail*.log 2>/dev/null | wc -l)"
echo "REPAIR_PID:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "PRIMARY_PID:$(pgrep -f 'nodesteer-hub --config /etc/nodesteer/hub.yaml' | head -1)"
echo "REPAIR_EXE_SHA:$(sha256sum /proc/$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)/exe 2>/dev/null | awk '{print $1}')"
echo "AGENT_UNITS:$(systemctl list-units --type=service --all 2>/dev/null | grep -c 'nodesteer-agent')"
echo "QA_UNITS:$(find /etc/systemd/system -name 'nodesteer-qa-*.service' 2>/dev/null | wc -l)"
echo "NEW_UNITS_1H:$(find /etc/systemd/system -newermt '-90 minutes' -name '*.service' 2>/dev/null | wc -l)"
echo "TMP_CREDS:$(ls /tmp/opencode/tail-creds-*.json 2>/dev/null | wc -l)"
""", check=False)
    d = {}
    for ln in cp.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k] = v.strip()
    res["hub_host_209"] = d

    # 4) .204 宿主残留
    cp2 = subprocess.run(
        ["ssh", "-i", str(Path.home() / ".ssh" / "id_ed25519"), "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=no", "root@192.168.100.204",
         "echo CHILD_ROOTS:$(ls -d /var/lib/nodesteer-qa-fixall-* /var/lib/nodesteer-qa-tail-* 2>/dev/null | wc -l); "
         "echo OWNED_UNITS:$(ls /etc/systemd/system/qa-tail-*.service 2>/dev/null | wc -l); "
         "echo OWNED_BINS:$(ls -d /opt/qa-tail-* 2>/dev/null | wc -l); "
         "echo OWNED_CONFS:$(ls /etc/qa-tail-*.conf 2>/dev/null | wc -l); "
         "echo OWNED_PROCS:$(ps -eo cmd | grep -F 'qa-tail-' | grep -v grep | wc -l); "
         "echo PROTECTED_UNIT:$(systemctl is-active nodesteer-agent-repair-20260930-101945-native-1.service); "
         "echo PRIMARY_AGENT:$(systemctl is-active nodesteer-agent.service); "
         "echo PROTECTED_PID:$(pgrep -f 'repair-20260930-101945/native-1.yaml' | head -1)"],
        capture_output=True, text=True, timeout=60)
    d2 = {}
    for ln in cp2.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d2[k] = v.strip()
    res["agent_host_204"] = d2

    res["verdict"] = {
        "isolated_instance_destroyed": d.get("PORT_18088") == "0" and d.get("QA_PROCS") == "0"
                                       and d.get("QA_DATADIR") == "0" and d.get("QA_CFG") == "0",
        "no_owned_residue_on_authority": res["owned_residue_zero"] and res["deleted_all_404"],
        "no_owned_residue_on_204": (d2.get("CHILD_ROOTS") == "0" and d2.get("OWNED_UNITS") == "0"
                                    and d2.get("OWNED_BINS") == "0" and d2.get("OWNED_CONFS") == "0"
                                    and d2.get("OWNED_PROCS") == "0"),
        "primary_8080_untouched": d.get("PORT_8080") == "1" and d.get("PRIMARY_PID") not in (None, ""),
        "protected_agent_untouched": d2.get("PROTECTED_UNIT") == "active",
        "no_new_systemd_units": d.get("NEW_UNITS_1H") == "0",
    }
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "existing"}, ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
