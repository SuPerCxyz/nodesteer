"""G9 观察探针：更大的无间隔 enrollment 连发，捕获可能的 500 响应体（仅隔离实例）。

节点名使用 qa-<nonce>-pNN 前缀，随后在 G9 的破坏性批量步骤中作为删除目标。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"], timeout=30)
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    base = cr["base_url"]
    results = []
    n = 12
    for i in range(n):
        name = f"qa-{nonce}-p{i:02d}"
        r = hub.post("/api/nodes/enrollment", admin,
                     body={"node_name": name, "node_ip": f"10.45.0.{30 + i}", "hub_address": base})
        results.append({"i": i + 1, "name": name, "http": r["status"],
                        "body_head": (r["body_raw"] or b"")[:300].decode(errors="replace")})
    nodes = C.as_list(hub.get("/api/nodes", admin)["json"])
    ev = {"nonce": nonce, "at_utc": C.utc(),
          "pattern": f"{n}x sequential POST /api/nodes/enrollment without delay",
          "results": results,
          "http_counts": {str(h): sum(1 for x in results if x["http"] == h)
                          for h in sorted({x["http"] for x in results})},
          "nodes_after": len(nodes),
          "pages_at_pagesize_10": (len(nodes) + 9) // 10,
          "note": "独立观察（enrollment 间歇 500），非 X14/X16 判定项；探针节点随后用于 G9 破坏性批量删除断言。"}
    C.write_evidence("g9", "G9-enrollment-burst-observation2.json", ev)
    print(json.dumps({"http_counts": ev["http_counts"], "nodes_after": ev["nodes_after"],
                      "pages": ev["pages_at_pagesize_10"], "errors": [x for x in results if x["http"] != 200]},
                     ensure_ascii=False, indent=1)[:1500])


if __name__ == "__main__":
    main()
