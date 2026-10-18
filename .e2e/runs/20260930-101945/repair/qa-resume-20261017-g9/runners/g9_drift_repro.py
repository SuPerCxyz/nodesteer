"""G9 关键复现：列表增删后被选集合是否漂移（干净状态 + 对照）。

Test A（漂移主复现）：干净 / 无筛选 / pageSize=10；
  在第 2 页选中第 1 行 R（按名称核验勾选）→ 回第 1 页删除第 1 行 V → 观察
  选择栏计数、R 行当前勾选态、以及真实批量动作 payload 里的 ID 是否仍等于 R 的 ID。
Test B（对照）：选中第 2 页第 1 行 R 后，删除第 3 页第 1 行 V'（在被选项之后）→ 期望无漂移。
Test C：干净状态下末页末项真实删除 → 记录删除后的页码/行数（复现 v2 观察到的回第 1 页）。

所有动作只在隔离实例，节点均为自有 qa-<nonce>-* 探针节点。
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

    ev = {"case_id": "X14", "probe": "selection-drift-on-list-mutation",
          "nonce": nonce, "at_utc": C.utc(), "authority": cr["base_url"]}

    # ---- 归一化：把自有探针节点统一为 pending（避免状态排序干扰），不动 owned child ----
    norm = []
    for n in nodes():
        h = n["hostname"]
        if h.startswith(f"qa-{nonce}-") and not h.endswith("-child") and n.get("status") != "pending":
            r = hub.put(f"/api/nodes/{n['id']}", admin, body={"status": "pending"})
            norm.append({"name": h, "from": n.get("status"), "http": r["status"]})
    ev["normalize_to_pending"] = norm
    ev["status_after_normalize"] = {s: sum(1 for v in snap().values() if v == s)
                                    for s in sorted(set(snap().values()))}

    U.init()
    who = "g9c"
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])
    id_by_name = idmap()
    ev["id_by_name"] = id_by_name

    def bulk_payload():
        U.clear_rec(who)
        op = click_text(who, "^设为维护", wait=1.2)
        dlg = U.js(who, r"""
(async () => { const d = document.querySelector('[role=dialog]');
  const txt = d ? (d.innerText||'').replace(/\n/g,' | ').slice(0,400) : null;
  const btns = d ? [...d.querySelectorAll('button')].map(b=>(b.innerText||'').trim()) : [];
  return {present: !!d, text: txt, buttons: btns}; })()""")
        conf = click_text(who, "^确认执行$|^确认$|^确定$", wait=0.6)
        time.sleep(2.6)
        reqs = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT", "DELETE", "PATCH")
                and "/api/nodes" in (r.get("url") or "")]
        ids = []
        for r in reqs:
            seg = (r.get("url") or "").split("/api/nodes/")[-1].split("?")[0].split("/")[0]
            if seg and seg not in ("upgrade", "enrollment"):
                ids.append(seg)
            for m in re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", r.get("body") or ""):
                if m not in ids:
                    ids.append(m)
        before = ev["_before_bulk"]
        after = snap()
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        id2name = {v: k for k, v in id_by_name.items()}
        return {"open_bar": op, "dialog": dlg, "confirm": conf, "request_count": len(reqs),
                "payload_ids": ids, "payload_names": sorted(id2name.get(i, i) for i in ids),
                "changed_names": changed,
                "changed_detail": [{"name": k, "before": before.get(k), "after": after.get(k)} for k in changed]}

    # ================= Test A：删除被选行之前的行 =================
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    a0 = U.js(who, ROWS_JS)
    ev["A_page1_before"] = {"rows": [r["name"] for r in a0["rows"]], "page_label": a0["page_label"],
                            "count_text": a0["selected_count_text"]}
    goto_page(who, 2)
    a2 = U.js(who, ROWS_JS)
    ev["A_page2_before"] = {"rows": [r["name"] for r in a2["rows"]], "page_label": a2["page_label"]}
    R = a2["rows"][0]["name"]
    V = a0["rows"][0]["name"]
    ev["A_selected_R"] = {"name": R, "id": id_by_name.get(R)}
    ev["A_victim_V"] = {"name": V, "id": id_by_name.get(V)}
    ev["A_check_R"] = check_row(who, R)
    ev["A_bar_after_select"] = U.js(who, ROWS_JS)["selected_count_text"]
    U.shot(who, "g9", "G9-A-before-delete.png")
    goto_page(who, 1)
    ev["A_delete_V"] = delete_row(who, V)
    ev["A_V_absent_in_api"] = V not in snap()
    ev["A_bar_after_delete"] = U.js(who, ROWS_JS)["selected_count_text"]
    walks = []
    for page in range(1, 6):
        if page > 1:
            goto_page(who, page)
        st = U.js(who, ROWS_JS)
        checked = [r["name"] for r in st["rows"] if r["checked"]]
        r_state = next((r for r in st["rows"] if r["name"] == R), None)
        walks.append({"page": page, "page_label": st["page_label"], "count_text": st["selected_count_text"],
                      "checked_names": checked, "R_present": r_state is not None,
                      "R_checked": (r_state or {}).get("checked")})
        if r_state is not None and r_state.get("checked"):
            break
    ev["A_post_delete_walk"] = walks
    ev["_before_bulk"] = snap()
    ev["A_bulk"] = bulk_payload()
    ev["A_verdict"] = {
        "selected_R": R, "selected_R_id": id_by_name.get(R),
        "payload_ids": ev["A_bulk"]["payload_ids"], "payload_names": ev["A_bulk"]["payload_names"],
        "no_drift": ev["A_bulk"]["payload_ids"] == [id_by_name.get(R)],
        "count_text_after_delete": ev["A_bar_after_delete"],
        "R_checked_after_delete": next((w["R_checked"] for w in walks if w["R_present"]), None),
    }
    # 复位被改动的节点
    ev["A_cleanup"] = [{"name": k, "http": hub.put(f"/api/nodes/{id_by_name[k]}", admin,
                                                  body={"status": "pending"})["status"]}
                       for k in ev["A_bulk"]["changed_names"] if k in id_by_name]

    # ================= Test B：删除被选行之后的行（对照）=================
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    b1 = U.js(who, ROWS_JS)
    goto_page(who, 3)
    b3 = U.js(who, ROWS_JS)
    ev["B_page1"] = [r["name"] for r in b1["rows"]]
    ev["B_page3"] = [r["name"] for r in b3["rows"]]
    R2 = b3["rows"][0]["name"]
    goto_page(who, 2)
    b2 = U.js(who, ROWS_JS)
    ev["B_check_R2"] = {"name": R2, "id": id_by_name.get(R2), "check": check_row(who, R2)}
    V2 = b2["rows"][0]["name"]
    ev["B_victim_V2"] = {"name": V2, "id": id_by_name.get(V2)}
    goto_page(who, 1)
    ev["B_delete_V2"] = delete_row(who, V2)
    ev["B_V2_absent_in_api"] = V2 not in snap()
    walks_b = []
    for page in range(1, 6):
        goto_page(who, page)
        st = U.js(who, ROWS_JS)
        r_state = next((r for r in st["rows"] if r["name"] == R2), None)
        walks_b.append({"page": page, "page_label": st["page_label"], "count_text": st["selected_count_text"],
                        "checked_names": [r["name"] for r in st["rows"] if r["checked"]],
                        "R2_present": r_state is not None, "R2_checked": (r_state or {}).get("checked")})
        if r_state is not None and r_state.get("checked"):
            break
    ev["B_post_delete_walk"] = walks_b
    ev["_before_bulk"] = snap()
    ev["B_bulk"] = bulk_payload()
    ev["B_verdict"] = {"selected_R2": R2, "selected_R2_id": id_by_name.get(R2),
                       "payload_ids": ev["B_bulk"]["payload_ids"], "payload_names": ev["B_bulk"]["payload_names"],
                       "no_drift": ev["B_bulk"]["payload_ids"] == [id_by_name.get(R2)]}
    ev["B_cleanup"] = [{"name": k, "http": hub.put(f"/api/nodes/{id_by_name[k]}", admin,
                                                  body={"status": "pending"})["status"]}
                       for k in ev["B_bulk"]["changed_names"] if k in id_by_name]

    # ================= Test C：末页末项删除（干净）=================
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    click_text(who, "^跳转到最后一页", wait=1.8)
    c1 = U.js(who, ROWS_JS)
    ev["C_last_before"] = {"page_label": c1["page_label"], "rows": len(c1["rows"]),
                           "names": [r["name"] for r in c1["rows"]], "count_text": c1["selected_count_text"]}
    last_name = c1["rows"][-1]["name"]
    total_before = len(nodes())
    ev["C_select_last"] = check_row(who, last_name)
    ev["C_bar_after_select"] = U.js(who, ROWS_JS)["selected_count_text"]
    U.clear_rec(who)
    ev["C_delete"] = delete_row(who, last_name)
    ev["C_requests"] = [{"method": r.get("method"), "url": (r.get("url") or "").split("?")[0][-60:]}
                        for r in U.rec(who) if r.get("method") != "GET"]
    c2 = U.js(who, ROWS_JS)
    ev["C_last_after"] = {"page_label": c2["page_label"], "rows": len(c2["rows"]),
                          "count_text": c2["selected_count_text"]}
    ev["C_api"] = {"total_before": total_before, "total_after": len(nodes()),
                   "deleted_absent": last_name not in snap()}
    U.shot(who, "g9", "G9-C-after-last-delete.png")

    C.write_evidence("g9", "X14-selection-drift-repro.json", ev)
    (C.EVID_DIRS["g9"].parent / "drift-repro.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({
        "normalize": ev["normalize_to_pending"], "status": ev["status_after_normalize"],
        "A": {"selected_R": ev["A_selected_R"], "victim_V": ev["A_victim_V"],
              "bar_after_select": ev["A_bar_after_select"], "bar_after_delete": ev["A_bar_after_delete"],
              "walk": ev["A_post_delete_walk"], "bulk": {"payload_names": ev["A_bulk"]["payload_names"],
                                                         "changed": ev["A_bulk"]["changed_detail"],
                                                         "dialog": ev["A_bulk"]["dialog"].get("text")},
              "verdict": ev["A_verdict"], "cleanup": ev["A_cleanup"]},
        "B": {"selected_R2": ev["B_check_R2"], "victim_V2": ev["B_victim_V2"],
              "walk": ev["B_post_delete_walk"], "bulk_payload_names": ev["B_bulk"]["payload_names"],
              "verdict": ev["B_verdict"], "cleanup": ev["B_cleanup"]},
        "C": {"before": ev["C_last_before"], "after": ev["C_last_after"], "api": ev["C_api"],
              "requests": ev["C_requests"]},
    }, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
