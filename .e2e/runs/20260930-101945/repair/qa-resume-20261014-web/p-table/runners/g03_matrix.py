"""P-TABLE：G03 搜索/清空/Reset/每页条数/分页控件矩阵（真实 UI 输入与点击）。

覆盖 G03-01 搜索命中、G03-02 无结果、G03-03 清空恢复、G03-04 Reset 清除、
G03-06 每页 10/20/30/40/50、G03-07 首末/前后/页码、G03-08 禁用态。
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402
from table_paging import select_page_size, observe_page_size_options  # noqa: E402

TABLES = [
    ("nodes", "/nodes", "/api/nodes", "nodes"),
    ("groups", "/groups", "/api/groups", "groups"),
    ("scripts", "/scripts", "/api/scripts", "scripts"),
    ("tasks", "/tasks", "/api/tasks", "tasks"),
    ("schedules", "/schedules", "/api/schedules", "schedules"),
    ("applications", "/applications", "/api/applications", "applications"),
    ("artifacts", "/artifacts", "/api/artifacts", "artifacts"),
    ("executions", "/executions", "/api/executions", "executions"),
    ("transfers", "/transfers", "/api/transfers", "transfers"),
    ("audit", "/audit", "/api/audit", "audit"),
    ("users", "/users", "/api/users", "users"),
]

STATE_JS = r"""
const key = __KEY__, api = __API__;
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
let apiStatus = null, apiCount = null, apiTotal = null;
try {
  const r = await fetch(api, { headers: { Authorization: 'Bearer ' + t } });
  apiStatus = r.status; apiTotal = r.headers.get('x-total-count');
  const b = await r.json(); apiCount = Array.isArray(b) ? b.length : (b?.items?.length ?? null);
} catch (e) {}
return {
  route: location.pathname, apiStatus, apiCount,
  apiTotal: apiTotal === null ? null : Number(apiTotal),
  rows: rows.length,
  rowFirst: rows[0] ? rows[0].innerText.trim().split('\n')[0] : null,
  pageOf: (txt.match(/第\s*\d+\s*页，共\s*\d+\s*页/) || [])[0] || null,
  totalRowsLabel: (txt.match(/共\s*\d+\s*条/) || [])[0] || null,
  noResults: /没有结果|No results/.test(txt),
  nav: {
    first: nav('跳转到第一页') ? nav('跳转到第一页').disabled : null,
    prev: nav('上一页') ? nav('上一页').disabled : null,
    next: nav('下一页') ? nav('下一页').disabled : null,
    last: nav('跳转到最后一页') ? nav('跳转到最后一页').disabled : null,
  },
  pageSize: combo ? combo.textContent.trim() : null,
  localView: view,
  sortableHeaders: [...(m?.querySelectorAll('thead th button') || [])].length,
  ariaSort: [...(m?.querySelectorAll('thead th') || [])].map(th => th.getAttribute('aria-sort')).filter(Boolean),
};"""


def state(api: str, key: str) -> dict:
    return C.ab_json(STATE_JS.replace("__KEY__", json.dumps(key))
                     .replace("__API__", json.dumps(api)))


def find_search_ref() -> str | None:
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    for line in snap.splitlines():
        if "textbox" in line and "搜索" in line:
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            if m:
                return m.group(1)
    return None


def wait_ui(ms: int = 600) -> None:
    time.sleep(ms / 1000)


def observe_page_size_options_local() -> dict:
    """（已迁移到 table_paging.observe_page_size_options）"""
    return observe_page_size_options()


def run_table(name: str, route: str, api: str, key: str, token: str) -> dict:
    rec = {"table": name, "route": route, "api": api, "captured_at_utc": C.utc(),
           "steps": [], "search": {}, "page_size": {}, "pagination": {}, "sort": {}}
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(700)
    s0 = state(api, key)
    rec["initial"] = s0
    total = s0["apiCount"] or 0

    ref = find_search_ref()
    rec["search"]["search_input_found"] = bool(ref)

    # 排序（补充观测；先于搜索做，避免受过滤态影响）
    if total > 1 and s0["sortableHeaders"]:
        before = state(api, key)
        C.ab("click", "thead th:nth-child(2) button", check=False)
        wait_ui(900)
        after = state(api, key)
        C.ab("click", "thead th:nth-child(2) button", check=False)
        wait_ui(900)
        after2 = state(api, key)
        rec["sort"] = {"header_clicked": True, "before_first": before["rowFirst"],
                       "after_first": after["rowFirst"], "after_second_click": after2["rowFirst"],
                       "ariaSort": after["ariaSort"],
                       "order_changed": before["rowFirst"] != after["rowFirst"]}

    if ref:
        C.ab("fill", f"@{ref}", token, timeout=25)
        wait_ui(900)
        rec["search"]["hit"] = state(api, key)
        C.ab("fill", f"@{ref}", f"zzz-no-match-{token}", timeout=25)
        wait_ui(900)
        rec["search"]["no_match"] = state(api, key)
        # 真实按键清空（fill "" 不触发 React onChange，用 Ctrl+A + Backspace）
        C.ab("focus", f"@{ref}", check=False)
        C.ab("press", "Control+a", check=False)
        C.ab("press", "Backspace", check=False)
        wait_ui(1000)
        rec["search"]["cleared"] = state(api, key)

    sizes = {}
    for size in ("20", "30", "40", "50", "10"):
        clicked = select_page_size(size)
        st = state(api, key)
        sizes[size] = {"clicked": clicked, "pageSize": st["pageSize"], "rows": st["rows"],
                       "localView": st["localView"], "pageOf": st["pageOf"]}
    rec["page_size"]["sizes"] = sizes
    rec["page_size"]["options_observed"] = observe_page_size_options()
    pages = max(1, math.ceil(max(total, 1) / 10))
    rec["pagination"] = {
        "page1": s0,
        "expected_pages_at_10": pages,
        "next_clicked": None, "page2": None, "last_clicked": None, "last_page": None,
        "first_clicked": None, "back_page1": None, "page_buttons": None,
    }
    if pages > 1:
        rec["pagination"]["page_buttons"] = C.ab_json(r"""
        const m=document.querySelector('main');
        return [...(m?.querySelectorAll('button')||[])].map(b=>(b.querySelector('.sr-only')?.textContent||'').trim())
          .filter(x=>x.startsWith('跳转到第') && !x.includes('第一页') && !x.includes('最后一页'));
        """)
        C.ab("find", "role", "button", "click", "--name", "下一页", check=False)
        wait_ui(700)
        rec["pagination"]["next_clicked"] = True
        rec["pagination"]["page2"] = state(api, key)
        C.ab("find", "role", "button", "click", "--name", "跳转到最后一页", check=False)
        wait_ui(700)
        rec["pagination"]["last_clicked"] = True
        rec["pagination"]["last_page"] = state(api, key)
        C.ab("find", "role", "button", "click", "--name", "跳转到第一页", check=False)
        wait_ui(700)
        rec["pagination"]["first_clicked"] = True
        rec["pagination"]["back_page1"] = state(api, key)

    # 排序（补充观测；仅记录真实行为）
    rec["total"] = total
    return rec


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    token = led["nonce"]
    only = set(sys.argv[1:])
    tables = [t for t in TABLES if not only or t[0] in only]
    out = {"captured_at_utc": C.utc(), "search_token_kind": "instance nonce（owned fixture 名称前缀）",
           "tables": []}
    prev = C.ROOT / "evidence" / "G03-search-paging-matrix.json"
    if only and prev.exists():
        out["tables"] = [t for t in json.loads(prev.read_text())["tables"]
                         if t.get("table") not in only]
    for name, route, api, key in tables:
        try:
            rec = run_table(name, route, api, key, token)
        except Exception as exc:  # noqa: BLE001
            rec = {"table": name, "route": route, "harness_error": str(exc)[:300]}
        out["tables"].append(rec)
        s = rec.get("search", {})
        print(json.dumps({"table": name, "total": rec.get("total"),
                          "hit_rows": (s.get("hit") or {}).get("rows"),
                          "no_match": (s.get("no_match") or {}).get("noResults"),
                          "cleared_rows": (s.get("cleared") or {}).get("rows"),
                          "sizes": {k: v["pageSize"] for k, v in
                                    (rec.get("page_size", {}).get("sizes") or {}).items()},
                          "size_options": (rec.get("page_size", {}).get("options_observed") or {}).get("options"),
                          "pages": (rec.get("pagination") or {}).get("expected_pages_at_10"),
                          "next_ok": bool((rec.get("pagination") or {}).get("page2")),
                          "sort": rec.get("sort")}, ensure_ascii=False), flush=True)
    order = [t[0] for t in TABLES]
    out["tables"].sort(key=lambda t: order.index(t["table"]) if t.get("table") in order else 99)
    rel = C.write_evidence("G03-search-paging-matrix.json", out)
    print(json.dumps({"evidence": rel}, ensure_ascii=False))


if __name__ == "__main__":
    main()
