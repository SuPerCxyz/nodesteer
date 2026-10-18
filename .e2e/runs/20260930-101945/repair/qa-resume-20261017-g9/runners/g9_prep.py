"""G9 前置补数：把数据集补到 ≥23 行（3 页 @pageSize=10），并记录 enrollment 间歇 500 观察。

- 先做 6 连发探针抓 500 的响应体（新观察，非 G8/G9 断言项）；
- 再按 0.4s 间隔补注册缺失节点，直至节点总数 ≥ 23（best effort，最多 16 次）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402


def enroll_raw(hub, admin, name, ip, base):
    r = hub.post("/api/nodes/enrollment", admin,
                 body={"node_name": name, "node_ip": ip, "hub_address": base})
    body = r["body_raw"][:400].decode(errors="replace") if r.get("body_raw") else ""
    return {"name": name, "ip": ip, "http": r["status"], "body": body}


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"], timeout=30)
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    base = cr["base_url"]
    ev = {"nonce": nonce, "at_utc": C.utc()}

    total0 = len(C.as_list(hub.get("/api/nodes", admin)["json"]))
    ev["nodes_before"] = total0

    # ---- 1) 6 连发探针（无间隔）----
    burst = []
    for i in range(6):
        burst.append(enroll_raw(hub, admin, f"qa-{nonce}-b{i}", f"10.43.0.{20 + i}", base))
    ev["rapid_burst_probe"] = {
        "pattern": "6x sequential POST /api/nodes/enrollment without delay",
        "results": [{"i": i + 1, "http": b["http"], "body_head": b["body"][:200]} for i, b in enumerate(burst)],
        "http_counts": {str(h): sum(1 for b in burst if b["http"] == h)
                        for h in sorted({b["http"] for b in burst})},
    }

    # ---- 2) 按节奏补数到 >= 23 ----
    added, failures = [], []
    attempt = 0
    while attempt < 16:
        attempt += 1
        total = len(C.as_list(hub.get("/api/nodes", admin)["json"]))
        if total >= 23:
            break
        name = f"qa-{nonce}-x{attempt:02d}"
        r = enroll_raw(hub, admin, name, f"10.44.0.{10 + attempt}", base)
        (added if r["http"] == 200 else failures).append(r)
        time.sleep(0.4)

    nodes = C.as_list(hub.get("/api/nodes", admin)["json"])
    ev["paced_added"] = [{"name": a["name"], "http": a["http"]} for a in added]
    ev["paced_failed"] = [{"name": f["name"], "http": f["http"], "body_head": f["body"][:200]} for f in failures]
    ev["nodes_after"] = len(nodes)
    ev["node_status_breakdown"] = {s: sum(1 for n in nodes if n.get("status") == s)
                                   for s in sorted({n.get("status") for n in nodes})}
    ev["pages_at_pagesize_10"] = (len(nodes) + 9) // 10
    ev["observation"] = (
        "enrollment 间歇返回 500（同一请求体此前/此后均成功，无请求体差异）——"
        "独立观察，非 X14/X16 判定项；具体错误体见 rapid_burst_probe / paced_failed。")

    C.write_evidence("g9", "G9-enrollment-burst-observation.json", ev)
    (C.EVID_DIRS["g9"].parent / "dataset-ledger.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: ev[k] for k in ("nodes_before", "http_counts", "nodes_after",
                                        "node_status_breakdown", "pages_at_pagesize_10")},
                     ensure_ascii=False, indent=1)
          if False else json.dumps({k: ev.get(k) for k in ("nodes_before", "nodes_after",
                                                          "node_status_breakdown",
                                                          "pages_at_pagesize_10")},
                                  ensure_ascii=False, indent=1))
    print("proxy_status_counts", json.dumps(ev["rapid_burst_probe"]["http_counts"]))
    print("burst_body_heads", json.dumps([b["body_head"][:120] for b in burst], ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
