"""G9/X14：选择漂移最小复现（干净会话、单场景、含真实批量 payload）。

场景：pageSize=10 第 1 页 → 勾选第 4 行 R（index 3）→ 删除第 1 行 V（index 0，位于 R 之前）
      → 立即读取勾选行 → 执行「设为维护」并抓取真实请求 payload。

判定：若选择按行 ID 保留，payload 必须命中 R；若按行索引保留，勾选会漂移为原 index 4 的行，
      且 payload 命中该漂移行（数据完整性缺陷）。

所有动作只在隔离实例内；结束后把被改状态恢复为 pending。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402
from g9_dataset2 import ROWS_JS, check_row, click_text, delete_row, set_page_size  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    C.register_redaction(cr["admin_password"], "<ADMIN_PW>")
    C.register_redaction(cr["registration_token"], "<REG_TOKEN>")
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    U.init()
    who = "g9r"

    def nodes():
        return C.as_list(hub.get("/api/nodes?limit=500", admin)["json"])

    def snap():
        return {n["hostname"]: n.get("status") for n in nodes()}

    ev = {"case_id": "X14", "probe": "selection-drift-minimal-repro",
          "nonce": nonce, "at_utc": C.utc(), "authority": cr["base_url"]}
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    ev["pagesize"] = set_page_size(who, "10")
    U.install_request_recorder(who)

    before = U.js(who, ROWS_JS)
    ev["page1_before"] = before
    rows = before["rows"]
    R, V = rows[3], rows[0]
    ev["selection"] = {"R": R["name"], "R_index": 3, "V": V["name"], "V_index": 0}
    ev["select_R"] = check_row(who, R["name"])
    ev["bar_after_select"] = U.js(who, ROWS_JS)["selected_count_text"]

    st_before = snap()
    ev["delete_V"] = delete_row(who, V["name"])
    time.sleep(1.2)
    st_after_delete = snap()
    ev["victim_absent_in_api"] = V["name"] not in st_after_delete

    after = U.js(who, ROWS_JS)
    ev["page1_after_delete"] = after
    checked_after = [r["name"] for r in after["rows"] if r["checked"]]
    ev["checked_after_delete"] = checked_after
    ev["drifted_row"] = next((r["name"] for r in after["rows"] if r["checked"]), None)
    ev["bar_after_delete"] = after["selected_count_text"]
    ev["shot_after_delete"] = U.shot(who, "g9", "G9-minrepro-drifted-selection.png")

    # 真实批量动作：抓 payload + 实际状态变化
    U.clear_rec(who)
    ev["bulk_open"] = click_text(who, "^设为维护", wait=1.2)
    ev["bulk_confirm"] = click_text(who, "^确认执行$|^确认$|^确定$", wait=0.6)
    time.sleep(2.6)
    reqs = [r for r in U.rec(who)
            if r.get("method") in ("POST", "PUT", "DELETE") and "/api/nodes" in (r.get("url") or "")]
    id_by_name = {n["hostname"]: n["id"] for n in nodes()}
    inv = {v: k for k, v in id_by_name.items()}
    payload_ids = []
    for r in reqs:
        seg = (r.get("url") or "").split("/api/nodes/")[-1].split("?")[0].split("/")[0]
        if seg and seg not in ("upgrade", "enrollment"):
            payload_ids.append(seg)
    st_after_bulk = snap()
    changed = [{"name": k, "before": st_before.get(k), "after": v}
               for k, v in st_after_bulk.items() if st_before.get(k) != v]
    ev["bulk"] = {
        "request_count": len(reqs),
        "request_methods": sorted({r.get("method") for r in reqs}),
        "payload_ids": payload_ids,
        "payload_names": sorted(inv.get(i, i) for i in payload_ids),
        "expected_id": id_by_name.get(R["name"]),
        "expected_name": R["name"],
        "changed_nodes": changed,
    }
    ev["drift"] = {
        "selection_position_shifted": checked_after == [rows[4]["name"]] if len(rows) > 4 else None,
        "payload_equals_selected_R": ev["bulk"]["payload_names"] == [R["name"]],
        "changed_equals_selected_R": [c["name"] for c in changed] == [R["name"]],
    }

    # 清理：把批量动作命中的行恢复为 pending
    cleanup = []
    for c in changed:
        nid = id_by_name.get(c["name"])
        if nid:
            r = hub.put(f"/api/nodes/{nid}", admin, body={"status": "pending"})
            cleanup.append({"name": c["name"], "from": c["after"], "http": r["status"]})
    ev["cleanup"] = cleanup
    ev["status_after_cleanup"] = snap()

    C.write_evidence("g9", "X14-selection-drift-minrepro.json", ev)
    print(json.dumps({k: ev[k] for k in ("selection", "bar_after_select", "delete_V",
                                         "victim_absent_in_api", "page1_after_delete",
                                         "checked_after_delete", "drifted_row", "bar_after_delete",
                                         "bulk", "drift", "cleanup")},
                     ensure_ascii=False, indent=1)[:4500])


if __name__ == "__main__":
    main()
