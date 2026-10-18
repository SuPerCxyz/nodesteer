"""P-TABLE：TABLE-NODES-PAGE / TABLE-TRANSFERS-PAGE 的真实分页与末页数据变化。

全部通过真实 UI 点击（无障碍名）与同页数据变化观测；节点删除用真实确认框。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

CASES = {
    "TABLE-NODES-PAGE": ("/nodes", "/api/nodes", "nodes", False),
    "TABLE-TRANSFERS-PAGE": ("/transfers", "/api/transfers", "transfers", True),
}

STATE_JS = r"""
const key = __KEY__;
const m = document.querySelector('main');
const btns = [...(m?.querySelectorAll('button') || [])];
const sr = b => (b.querySelector('.sr-only')?.textContent || '').trim();
const nav = lbl => btns.find(b => sr(b) === lbl) || null;
const txt = m ? m.innerText : '';
const rows = [...(m?.querySelectorAll('tbody tr') || [])];
const combo = (() => {
  const cs = [...(m?.querySelectorAll('[role="combobox"]') || [])];
  for (let i = cs.length - 1; i >= 0; i--) {
    let p = cs[i].parentElement;
    for (let k = 0; k < 4 && p; k++, p = p.parentElement) {
      if ((p.innerText || '').includes('每页行数')) return cs[i];
    }
  }
  return null;
})();
let view = null; try { view = localStorage.getItem('nodesteer.table.' + key); } catch (e) {}
const t = localStorage.getItem('nodesteer_token');
const hdr = { Authorization: 'Bearer ' + t };
const r = await fetch(__API__, { headers: hdr });
let arr = []; try { const b = await r.json(); arr = Array.isArray(b) ? b : (b?.items || []); } catch (e) {}
return {
  url: location.pathname,
  apiStatus: r.status,
  apiCountThisPage: arr.length,
  apiTotalHeader: r.headers.get('x-total-count'),
  rows: rows.length,
  rowFirst: rows[0] ? rows[0].innerText.trim().split('\n')[0] : null,
  rowLast: rows.length ? rows[rows.length - 1].innerText.trim().split('\n')[0] : null,
  pageOf: (txt.match(/第\s*\d+\s*页，共\s*\d+\s*页/) || [])[0] || null,
  totalRowsLabel: (txt.match(/共\s*\d+\s*条/) || [])[0] || null,
  noResults: /没有结果|No results/.test(txt),
  nav: {
    first: nav('跳转到第一页') ? nav('跳转到第一页').disabled : null,
    prev: nav('上一页') ? nav('上一页').disabled : null,
    next: nav('下一页') ? nav('下一页').disabled : null,
    last: nav('跳转到最后一页') ? nav('跳转到最后一页').disabled : null,
  },
  pageButtons: btns.map(sr).filter(x => x.startsWith('跳转到第') && !/^跳转到第[一末]/.test(x)),
  pageSize: combo ? combo.textContent.trim() : null,
  localView: view,
};"""


def state(api: str, key: str) -> dict:
    return C.ab_json(STATE_JS.replace("__KEY__", json.dumps(key))
                     .replace("__API__", json.dumps(api)))


def pagesize_combo_index() -> int:
    idx = C.ab_json(r"""
    const cs = [...document.querySelectorAll('[role="combobox"]')];
    let idx = -1;
    for (let i = cs.length - 1; i >= 0; i--) {
      let p = cs[i].parentElement;
      for (let k = 0; k < 4 && p; k++, p = p.parentElement) {
        if ((p.innerText || '').includes('每页行数')) { idx = i; break; }
      }
      if (idx >= 0) break;
    }
    return idx;
    """)
    return int(idx)


def refs_by_role(role: str) -> list[str]:
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    refs = []
    for line in snap.splitlines():
        if role in line:
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            if m:
                refs.append(m.group(1))
    return refs


def select_page_size(size: str) -> bool:
    """真实点击每页条数选择器（快照 ref）并选择指定值。"""
    idx = pagesize_combo_index()
    if idx < 0:
        return False
    refs = refs_by_role("combobox")
    if idx >= len(refs):
        return False
    if C.ab("click", f"@{refs[idx]}", check=False).returncode != 0:
        return False
    wait_ui(450)
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    opt_ref = None
    for line in snap.splitlines():
        m = re.match(rf'\s*-?\s*option "{re.escape(size)}"', line)
        if m:
            r = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            if r:
                opt_ref = r.group(1)
                break
    if opt_ref is None:
        ok = C.ab("find", "role", "option", "click", "--name", size, check=False).returncode == 0
    else:
        ok = C.ab("click", f"@{opt_ref}", check=False).returncode == 0
    wait_ui(700)
    return ok


def observe_page_size_options() -> dict:
    idx = pagesize_combo_index()
    refs = refs_by_role("combobox")
    if idx < 0 or idx >= len(refs):
        return {"options": [], "opened": False}
    opened = C.ab("click", f"@{refs[idx]}", check=False).returncode == 0
    wait_ui(500)
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    opts = [m.group(1) for line in snap.splitlines()
            if (m := re.match(r'\s*-?\s*option "([^"]+)"', line))]
    C.ab("press", "Escape", check=False)
    wait_ui(300)
    return {"options": opts, "opened": opened}


def click_name(name: str) -> bool:
    for args in (["find", "role", "button", "click", "--name", name],
                 ["find", "last", "button", "click", "--name", name]):
        cp = C.ab(*args, check=False)
        if cp.returncode == 0:
            return True
    return False


def wait_ui(ms: int = 500) -> None:
    time.sleep(ms / 1000)


def topup(api: str, target: int = 13) -> dict:
    """把数据集补齐到至少 target 行（仅限本实例的 owned fixtures）。"""
    js = r"""
    const api = __API__, target = __TARGET__;
    const nonce = __NONCE__;
    const t = localStorage.getItem('nodesteer_token');
    const H = { Authorization: 'Bearer ' + t, 'Content-Type': 'application/json' };
    const g = async (p) => { const r = await fetch(p, { headers: H }); return await r.json().catch(() => null); };
    const cur = await g(api);
    let n = Array.isArray(cur) ? cur.length : 0;
    const errors = [];
    let nodes = null;
    if (n < target) {
      if (api === '/api/nodes') {
        for (let i = n; i < target; i++) {
          const r = await fetch('/api/nodes/enrollment', { method: 'POST', headers: H, body: JSON.stringify({
            node_name: `ptable-${nonce}-topup-${i + 1}`, node_ip: `10.42.1.${11 + i}`, hub_address: null }) });
          if (r.status >= 300) errors.push(r.status);
        }
      } else if (api === '/api/transfers') {
        nodes = await g('/api/nodes');
        const ids = (nodes || []).map(x => x.id);
        for (let i = n; i < target; i++) {
          const r = await fetch('/api/transfers', { method: 'POST', headers: H, body: JSON.stringify({
            source_node_id: ids[0], source_path: `/var/tmp/ptable-topup-${i + 1}.txt`,
            targets: [{ node_id: ids[1], destination_path: `/var/tmp/ptable-topup-dst-${i + 1}.txt` }] }) });
          if (r.status >= 300) errors.push(r.status);
        }
      }
    }
    const after = await g(api);
    return { before: n, after: Array.isArray(after) ? after.length : 0, errors };
    """.replace("__API__", json.dumps(api)).replace("__TARGET__", str(target)).replace(
        "__NONCE__", json.dumps(json.loads((C.ROOT / "instance-ledger.json").read_text())["nonce"]))
    return C.ab_json(js, timeout=180)


def run(case_id: str) -> dict:
    route, api, key, live_refetch = CASES[case_id]
    steps = []
    ev = {"case_id": case_id, "route": route, "list_get_path": api,
          "captured_at_utc": C.utc(), "storage_key": key}
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    ev["topup"] = topup(api)
    C.ab("reload", timeout=30)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui()

    def rec(label: str, st: dict) -> dict:
        steps.append({"step": label, "state": st})
        return st

    s0 = rec("initial_page1", state(api, key))
    click_name("下一页")
    wait_ui()
    s1 = rec("click_next_expected_page2", state(api, key))
    click_name("跳转到最后一页")
    wait_ui()
    s2 = rec("click_last", state(api, key))
    click_name("跳转到第一页")
    wait_ui()
    s3 = rec("click_first", state(api, key))
    click_name("跳转到第 2 页")
    wait_ui()
    s4 = rec("click_page_button_2", state(api, key))

    # pageSize 10 → 20 → 30 → 10（Radix SelectItem 的角色是 option）
    page_size_steps = []
    for size in ("20", "30", "10"):
        clicked = select_page_size(size)
        page_size_steps.append({"select": size, "option_clicked": clicked,
                                "state": state(api, key)})
    ev["page_size_steps"] = page_size_steps
    # 持久化验证：设为 20 后 reload
    select_page_size("20")
    before_reload = state(api, key)
    C.ab("reload", timeout=30)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(700)
    after_reload = state(api, key)
    ev["page_size_persistence"] = {"before_reload": before_reload, "after_reload": after_reload}
    # 回到每页 10
    select_page_size("10")

    # 末页数据变化
    if live_refetch:
        click_name("跳转到最后一页")
        wait_ui(600)
        pre = state(api, key)
        add_js = r"""
        const t = localStorage.getItem('nodesteer_token');
        const H = { Authorization: 'Bearer ' + t, 'Content-Type': 'application/json' };
        const g = async (p) => { const r = await fetch(p, { headers: H }); return await r.json(); };
        const nodes = await g('/api/nodes');
        const ids = nodes.map(x => x.id);
        const n = (await g('/api/transfers')).length;
        const r = await fetch('/api/transfers', { method: 'POST', headers: H, body: JSON.stringify({
          source_node_id: ids[0], source_path: '/var/tmp/ptable-livechange-' + n + '.txt',
          targets: [{ node_id: ids[1], destination_path: '/var/tmp/ptable-livechange-dst-' + n + '.txt' }] }) });
        return { status: r.status, before: n };
        """
        created = C.ab_json(add_js, timeout=120)
        wait_ui(4200)  # transfers 列表 refetchInterval = 3s
        post = state(api, key)
        click_name("跳转到最后一页")
        wait_ui(700)
        post_last = state(api, key)
        ev["last_page_data_change"] = {
            "kind": "live_refetch_after_api_create",
            "create_result": created,
            "before": pre, "after_refetch": post, "last_page_after_refetch": post_last,
            "auto_reset_page_observed": post["pageOf"] != pre["pageOf"],
        }
    else:
        click_name("跳转到最后一页")
        wait_ui(600)
        pre = state(api, key)
        rows_before = pre["rows"]
        row_sel = f'tbody tr:nth-child({rows_before}) button[aria-label="操作"]'
        # (a) Escape 取消：不得发送 DELETE，且焦点回到触发按钮
        C.ab("network", "requests", "--clear", check=False)
        C.ab("click", row_sel, check=False)
        wait_ui(450)
        C.ab("find", "role", "menuitem", "click", "--name", "删除", check=False)
        wait_ui(700)
        dlg = C.ab_json(r"""
        const d = document.querySelector('[role="alertdialog"]');
        return { present: !!d, text: d ? d.innerText.trim().slice(0, 120) : null };
        """)
        C.ab("press", "Escape", check=False)
        wait_ui(800)
        after_escape = C.ab_json(r"""
        const d = document.querySelector('[role="alertdialog"]');
        const a = document.activeElement;
        return { dialog_present: !!d, focused_aria: a ? a.getAttribute('aria-label') : null,
                 focused_text: a ? (a.textContent || '').trim().slice(0, 20) : null };
        """)
        reqs = C.ab("network", "requests", check=False).stdout
        delete_count = sum(1 for l in reqs.splitlines()
                           if " DELETE " in l and "/api/nodes/" in l)
        mid = state(api, key)
        # (b) 确认删除：恰好 1 次 DELETE，末页行数减少
        C.ab("network", "requests", "--clear", check=False)
        C.ab("click", row_sel, check=False)
        wait_ui(450)
        C.ab("find", "role", "menuitem", "click", "--name", "删除", check=False)
        wait_ui(700)
        C.ab("find", "role", "button", "click", "--name", "删除", check=False)
        wait_ui(1600)
        reqs2 = C.ab("network", "requests", check=False).stdout
        delete_count2 = sum(1 for l in reqs2.splitlines()
                            if " DELETE " in l and "/api/nodes/" in l)
        post = state(api, key)
        # tanstack autoResetPageIndex：删行后回到第 1 页，故再回到末页核对行数变化
        click_name("跳转到最后一页")
        wait_ui(700)
        post_last = state(api, key)
        ev["last_page_data_change"] = {
            "kind": "ui_delete_on_last_page",
            "confirm_dialog": dlg,
            "escape_cancel": {"after_escape": after_escape, "delete_requests": delete_count,
                              "rows_after_escape": mid["rows"]},
            "confirmed": {"delete_requests": delete_count2, "before": pre,
                          "after_delete": post, "last_page_after_delete": post_last},
        }

    ev["steps"] = steps
    total = s0["apiCountThisPage"] or int(s0["apiTotalHeader"] or 0)
    pages = max(1, -(-total // 10))
    last_rows = total - 10 * (pages - 1)
    ev["dataset"] = {"total": total, "pages_at_10": pages, "last_page_rows": last_rows}
    ok = (
        total >= 11
        and s0["rows"] == 10 and s0["pageOf"] and f"共 {pages} 页" in s0["pageOf"]
        and s0["nav"] == {"first": True, "prev": True, "next": False, "last": False}
        and s1["rows"] == last_rows and "第 2 页" in (s1["pageOf"] or "")
        and s1["nav"]["next"] is True and s1["nav"]["last"] is True
        and s2["rows"] == last_rows and s2["nav"]["next"] is True
        and s3["rows"] == 10 and s3["nav"]["prev"] is True and s3["nav"]["first"] is True
        and s4["rows"] == last_rows and "第 2 页" in (s4["pageOf"] or "")
    )
    lp = ev["last_page_data_change"]
    if lp["kind"] == "ui_delete_on_last_page":
        conf = lp["confirmed"]
        ok = ok and lp["escape_cancel"]["delete_requests"] == 0
        ok = ok and not lp["escape_cancel"]["after_escape"]["dialog_present"]
        ok = ok and lp["escape_cancel"]["rows_after_escape"] == lp["confirmed"]["before"]["rows"]
        ok = ok and conf["delete_requests"] == 1
        ok = ok and conf["last_page_after_delete"]["rows"] == lp["confirmed"]["before"]["rows"] - 1
        ok = ok and (conf["last_page_after_delete"]["apiCountThisPage"]
                     == lp["confirmed"]["before"]["apiCountThisPage"] - 1)
        ev["dataset"]["last_page_rows_after_delete"] = conf["last_page_after_delete"]["rows"]
    else:
        ok = ok and lp["last_page_after_refetch"]["apiCountThisPage"] > lp["before"]["apiCountThisPage"]
        ok = ok and lp["last_page_after_refetch"]["rows"] > lp["before"]["rows"]
        ok = ok and lp["create_result"]["status"] == 200
    psp = ev["page_size_persistence"]
    ok = ok and "20" in (psp["after_reload"]["pageSize"] or "")
    ok = ok and (psp["after_reload"]["localView"] or "").find('"pageSize":20') >= 0
    ok = ok and psp["after_reload"]["rows"] > 10
    remaining = []
    if not ok:
        remaining.append("部分分页/每页条数/末页数据变化断言未全部满足，见 evidence 的逐步状态。")
    return {**ev, "status": "PASS" if ok else "PARTIAL", "remaining": remaining,
            "server_writes": 1 if lp["kind"] == "ui_delete_on_last_page" else 1}


def main() -> None:
    only = sys.argv[1:] if len(sys.argv) > 1 else list(CASES)
    state_path = C.ROOT / "state.json"
    st = json.loads(state_path.read_text()) if state_path.exists() else {"cases": {}}
    for cid in only:
        res = run(cid)
        rel = C.write_evidence(f"{cid}-paging.json", res)
        st.setdefault("cases", {})[cid] = {
            "case_id": cid, "status": res["status"], "evidence": [rel],
            "actual": {k: v for k, v in res.items()
                       if k in ("steps", "page_size_persistence", "last_page_data_change")},
            "remaining": res["remaining"]}
        state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({cid: {"status": res["status"],
                                "evidence": rel,
                                "steps": [(x["step"], x["state"]["rows"], x["state"]["pageOf"]) for x in res["steps"]],
                                "pageSize_persist": res["page_size_persistence"]["after_reload"].get("pageSize"),
                                "lastChangeKind": res["last_page_data_change"]["kind"]}},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
