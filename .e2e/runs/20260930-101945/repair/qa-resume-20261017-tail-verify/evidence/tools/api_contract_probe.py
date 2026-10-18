#!/usr/bin/env python3
"""OBS-20261015-API-CONTRACT-DEVIATIONS-LOW 判定探针（repair :18080，只读探测）。

只做无副作用的方法/形状探测；DELETE 幂等只针对**不存在的 id**（不删任何真实对象）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402

EV = HERE.parent
OUT = EV / "api-contract-deviations.json"
h = hubclient.Hub()
h.login()
res = {"at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "authority": h.base, "probes": []}
MISSING = "00000000-0000-0000-0000-0000000000ff"


def probe(name, method, path, headers=None, body=None):
    code, resp = h.raw(method, path, body, headers=headers)
    entry = {"probe": name, "method": method, "path": path, "http": code,
             "body": (resp if not isinstance(resp, (dict, list)) else json.dumps(resp, ensure_ascii=False)[:200])}
    res["probes"].append(entry)
    return entry


def raw_probe(name, method, path, extra_headers=None):
    """需要读响应头/原始体（/metrics 非 JSON）。"""
    import urllib.request
    hdrs = {"Accept": "*/*", "Authorization": "Bearer " + h.token}
    if extra_headers:
        hdrs.update(extra_headers)
    req = urllib.request.Request(h.base + path, method=method, headers=hdrs)
    try:
        import urllib.error
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            entry = {"probe": name, "method": method, "path": path, "http": r.status,
                     "allow": r.headers.get("Allow"), "expose": r.headers.get("Access-Control-Expose-Headers"),
                     "total_count": r.headers.get("X-Total-Count"), "body_head": body[:120]}
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", 0)
        hdr = getattr(e, "headers", {}) or {}
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        entry = {"probe": name, "method": method, "path": path, "http": code,
                 "allow": hdr.get("Allow"), "expose": hdr.get("Access-Control-Expose-Headers"),
                 "total_count": hdr.get("X-Total-Count"), "body_head": body[:120]}
    res["probes"].append(entry)
    return entry


def main():
    # 1) 方法开关：audit / me / metrics
    probe("audit POST (expect 405 now)", "POST", "/api/audit")
    probe("me POST (expect 405 now)", "POST", "/api/me")
    raw_probe("metrics POST (expect 405 now)", "POST", "/metrics")
    raw_probe("metrics GET (baseline)", "GET", "/metrics")
    # 2) logout GET
    probe("logout GET (expect 405 now)", "GET", "/api/logout")
    # 3) transfers 非 POST
    probe("transfers/{id} PUT (expect 405, observed 403)", "PUT", "/api/transfers/" + MISSING)
    probe("transfers/{id}/cancel GET (expect 405, observed 403)", "GET", "/api/transfers/" + MISSING + "/cancel")
    # 4) DELETE 不存在 id 幂等
    probe("DELETE /api/nodes/{missing} as admin", "DELETE", "/api/nodes/" + MISSING)
    probe("DELETE /api/scripts/{missing}", "DELETE", "/api/scripts/" + MISSING)
    # 5) 键名形状
    probe("GET /api/me (key shape)", "GET", "/api/me")
    # 6) X-Total-Count 覆盖面
    for p in ("/api/executions?limit=1", "/api/audit?limit=1", "/api/nodes", "/api/tasks",
              "/api/scripts", "/api/applications", "/api/schedules", "/api/users", "/api/transfers"):
        raw_probe("X-Total-Count " + p, "GET", p)
    # 7) CORS 预检
    raw_probe("OPTIONS /api/nodes", "OPTIONS", "/api/nodes",
              {"Origin": "http://example.invalid", "Access-Control-Request-Method": "GET"})

    # 汇总
    res["summary"] = {
        "audit_method_switch": next((p["http"] for p in res["probes"] if p["probe"].startswith("audit POST")), None),
        "me_method_switch": next((p["http"] for p in res["probes"] if p["probe"].startswith("me POST")), None),
        "metrics_method_switch": next((p["http"] for p in res["probes"] if p["probe"].startswith("metrics POST")), None),
        "logout_get": next((p["http"] for p in res["probes"] if p["probe"].startswith("logout GET")), None),
        "transfers_put": next((p["http"] for p in res["probes"] if "transfers/{id} PUT" in p["probe"]), None),
        "me_keys": None,
        "total_count_endpoints": [p["path"] for p in res["probes"]
                                  if p["probe"].startswith("X-Total-Count") and p.get("total_count")],
    }
    me = next((p for p in res["probes"] if p["probe"].startswith("GET /api/me")), None)
    if me and isinstance(me.get("body"), str) and me["body"].startswith("{"):
        try:
            res["summary"]["me_keys"] = sorted(json.loads(me["body"]).keys())
        except Exception:
            pass

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    for p in res["probes"]:
        print("%-46s %-6s -> %s  allow=%s" % (p["probe"], p["method"], p["http"], p.get("allow")))


if __name__ == "__main__":
    main()
