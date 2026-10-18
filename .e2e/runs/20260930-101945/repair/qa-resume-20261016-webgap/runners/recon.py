#!/usr/bin/env python3
"""Recon: login, env/build label, nodes, baseline counts, transfers/artifacts inventory."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import BASE, CRED, call, j, as_list, login, counts  # noqa: E402

BATCH = Path(__file__).resolve().parent.parent
EV = BATCH / "evidence"
OUT = {}


def save():
    EV.mkdir(parents=True, exist_ok=True)
    (EV / "recon.json").write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")


tok = login()
OUT["login"] = {"http_ok": True, "base": BASE}

# environment / build label
for ep in ["/api/environment", "/api/system/environment", "/api/system", "/api/config",
           "/api/build", "/api/system/build", "/api/version", "/api/info"]:
    st, payload = j("GET", ep, tok)
    OUT.setdefault("env_probe", {})[ep] = {"http": st,
                                          "body": payload if st == 200 else str(payload)[:120]}

st, nodes = j("GET", "/api/nodes", tok)
nodelist = as_list(nodes)
OUT["nodes"] = {"http": st, "count": len(nodelist)}
OUT["node_rows"] = [{k: n.get(k) for k in ("id", "name", "address", "status", "deployment_mode",
                                          "agent_id", "last_seen", "created_at")}
                    for n in nodelist]

st, tr = j("GET", "/api/transfers", tok)
tl = as_list(tr)
OUT["transfers"] = {"http": st, "count": len(tl)}
OUT["transfer_rows"] = [{k: t.get(k) for k in ("id", "source_node_id", "source_path", "status",
                                               "created_at", "targets")} for t in tl][:60]

st, ar = j("GET", "/api/artifacts", tok)
al = as_list(ar)
OUT["artifacts"] = {"http": st, "count": len(al), "rows": al[:20]}

st, tk = j("GET", "/api/tasks", tok)
OUT["tasks"] = {"http": st, "count": len(as_list(tk)), "rows": as_list(tk)[:20]}
st, sc = j("GET", "/api/schedules", tok)
OUT["schedules"] = {"http": st, "count": len(as_list(sc)), "rows": as_list(sc)[:20]}
st, sk = j("GET", "/api/scripts", tok)
OUT["scripts"] = {"http": st, "count": len(as_list(sk)), "rows": as_list(sk)[:10]}
st, ex = j("GET", "/api/executions", tok)
exl = as_list(ex)
OUT["executions"] = {"http": st, "count": len(exl),
                     "statuses": sorted({e.get("status") for e in exl}),
                     "rows": exl[:5]}
OUT["counts_baseline"] = counts(tok)
save()
print(json.dumps({k: OUT[k] for k in ("env_probe", "nodes", "node_rows", "transfers", "tasks",
                                      "schedules", "scripts", "executions", "counts_baseline")},
                 ensure_ascii=False, indent=1)[:6000])
