"""G9 对照：列表增删后选择漂移的边界（同页/异页、被选行前/后）。

C1 选中第 2 页第 1 行 → 删除第 3 页第 1 行（被选行之后，异页）→ 期望无漂移
C2 选中第 1 页第 3 行 → 删除第 1 页第 1 行（被选行之前，同页）→ 期望漂移
C3 选中第 1 页第 1 行 → 删除第 1 页第 3 行（被选行之后，同页）→ 期望无漂移
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402
from g9_dataset2 import ROWS_JS, check_row, click_text, delete_row, set_page_size  # noqa: E402


def goto_page(who, n):
    return click_text(who, f"^跳转到第 {n} 页", wait=1.4)


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])

    def nodes():
        return C.as_list(hub.get("/api/nodes?limit=500", admin)["json"])

    def snap():
        return {n["hostname"]: n.get("status") for n in nodes()}

    def idmap():
        return {n["hostname"]: n["id"] for n in nodes()}

    id_by_name = idmap()
    ev = {"case_id": "X14", "probe": "drift-controls", "nonce": nonce, "at_utc": C.utc()}
    U.init()
    who = "g9d"
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])

    def bulk_names():
        U.clear_rec(who)
        click_text(who, "^设为维护", wait=1.2)
        click_text(who, "^确认执行$|^确认$|^确定$", wait=0.6)
        time.sleep(2.4)
        reqs = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT", "DELETE")
                and "/api/nodes" in (r.get("url") or "")]
        ids = []
        for r in reqs:
            seg = (r.get("url") or "").split("/api/nodes/")[-1].split("?")[0].split("/")[0]
            if seg and seg not in ("upgrade", "enrollment"):
                ids.append(seg)
        inv = {v: k for k, v in id_by_name.items()}
        return sorted(inv.get(i, i) for i in ids)

    def cleanup(names):
        out = []
        for k in names:
            if k in id_by_name:
                out.append({"name": k, "http": hub.put(f"/api/nodes/{id_by_name[k]}", admin,
                                                       body={"status": "pending"})["status"]})
        return out

    def walk_checked():
        res = []
        for page in range(1, 6):
            if page > 1:
                goto_page(who, page)
            st = U.js(who, ROWS_JS)
            res.append({"page": page, "page_label": st["page_label"],
                        "count_text": st["selected_count_text"],
                        "checked_names": [r["name"] for r in st["rows"] if r["checked"]],
                        "rows": [r["name"] for r in st["rows"]][:3]})
        return res

    # ---------- C1：异页删除（被选行之后）----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    goto_page(who, 2)
    p2 = U.js(who, ROWS_JS)
    R1 = p2["rows"][0]["name"]
    ev["C1"] = {"R": R1, "R_id": id_by_name[R1], "check": check_row(who, R1),
                "bar": U.js(who, ROWS_JS)["selected_count_text"]}
    goto_page(who, 3)
    p3 = U.js(who, ROWS_JS)
    V1 = p3["rows"][0]["name"]
    ev["C1"]["victim"] = V1
    goto_page(who, 1)
    ev["C1"]["delete"] = delete_row(who, V1)
    ev["C1"]["victim_absent"] = V1 not in snap()
    ev["C1"]["walk"] = walk_checked()
    ev["C1"]["bulk_names"] = bulk_names()
    ev["C1"]["verdict"] = {"no_drift": ev["C1"]["bulk_names"] == [R1]}
    ev["C1"]["cleanup"] = cleanup(ev["C1"]["bulk_names"])

    # ---------- C2：同页删除（被选行之前）----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    p1 = U.js(who, ROWS_JS)
    ev["C2"] = {"page1_rows": [r["name"] for r in p1["rows"][:5]]}
    R2 = p1["rows"][2]["name"]
    V2 = p1["rows"][0]["name"]
    ev["C2"].update({"R": R2, "R_id": id_by_name[R2], "check": check_row(who, R2),
                     "bar": U.js(who, ROWS_JS)["selected_count_text"], "victim": V2})
    ev["C2"]["delete"] = delete_row(who, V2)
    ev["C2"]["victim_absent"] = V2 not in snap()
    ev["C2"]["walk"] = walk_checked()
    ev["C2"]["bulk_names"] = bulk_names()
    ev["C2"]["verdict"] = {"no_drift": ev["C2"]["bulk_names"] == [R2]}
    ev["C2"]["cleanup"] = cleanup(ev["C2"]["bulk_names"])

    # ---------- C3：同页删除（被选行之后）----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    p1b = U.js(who, ROWS_JS)
    R3 = p1b["rows"][0]["name"]
    V3 = p1b["rows"][2]["name"]
    ev["C3"] = {"R": R3, "R_id": id_by_name[R3], "check": check_row(who, R3),
                "bar": U.js(who, ROWS_JS)["selected_count_text"], "victim": V3}
    ev["C3"]["delete"] = delete_row(who, V3)
    ev["C3"]["victim_absent"] = V3 not in snap()
    ev["C3"]["walk"] = walk_checked()
    ev["C3"]["bulk_names"] = bulk_names()
    ev["C3"]["verdict"] = {"no_drift": ev["C3"]["bulk_names"] == [R3]}
    ev["C3"]["cleanup"] = cleanup(ev["C3"]["bulk_names"])

    ev["summary"] = {
        "C1_later_page_delete_no_drift": ev["C1"]["verdict"]["no_drift"],
        "C2_same_page_before_delete_no_drift": ev["C2"]["verdict"]["no_drift"],
        "C3_same_page_after_delete_no_drift": ev["C3"]["verdict"]["no_drift"],
        "pattern": "仅当删除的行位于被选行之前（列表顺序）时出现漂移",
    }
    C.write_evidence("g9", "X14-drift-controls.json", ev)
    print(json.dumps({k: {"R": ev[k].get("R"), "victim": ev[k].get("victim"),
                          "victim_absent": ev[k].get("victim_absent"),
                          "walk_counts": [w["count_text"] for w in ev[k]["walk"]],
                          "checked_per_page": [w["checked_names"] for w in ev[k]["walk"]],
                          "bulk_names": ev[k]["bulk_names"], "verdict": ev[k]["verdict"],
                          "cleanup": ev[k]["cleanup"]}
                      for k in ("C1", "C2", "C3")}, ensure_ascii=False, indent=1)[:4000])
    print("SUMMARY:", json.dumps(ev["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
