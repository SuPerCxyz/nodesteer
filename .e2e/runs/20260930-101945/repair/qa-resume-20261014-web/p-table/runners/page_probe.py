"""P-TABLE：页面状态探针（真空态矩阵）。

对隔离实例的 11 个列表页做只读探测：API 状态/条数、表格行数、骨架/加载提示、
ErrorState/Retry、空态文案、搜索框与分页控件。写入 evidence。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

# route, list GET path (服务端分页时带 limit/offset), 是否服务端分页
PAGES = [
    ("/nodes", "/api/nodes", False),
    ("/groups", "/api/groups", False),
    ("/scripts", "/api/scripts", False),
    ("/tasks", "/api/tasks", False),
    ("/schedules", "/api/schedules", False),
    ("/applications", "/api/applications", False),
    ("/artifacts", "/api/artifacts", False),
    ("/executions", "/api/executions", True),
    ("/transfers", "/api/transfers", False),
    ("/audit", "/api/audit", True),
    ("/users", "/api/users", False),
]

PROBE_JS = r"""
const path = __PATH__;
const t = localStorage.getItem('nodesteer_token');
const hdr = { Authorization: 'Bearer ' + t };
const r = await fetch(path, { headers: hdr });
let body = null;
try { body = await r.json(); } catch (e) { body = null; }
const arr = Array.isArray(body) ? body
  : Array.isArray(body?.items) ? body.items
  : Array.isArray(body?.data) ? body.data
  : Array.isArray(body?.data?.items) ? body.data.items : [];
const m = document.querySelector('main');
const txt = m ? m.innerText : '';
const btns = [...(m?.querySelectorAll('button') || [])];
const srOnly = (b) => (b.querySelector('.sr-only')?.textContent || '').trim();
return {
  route: location.pathname,
  apiPath: path,
  apiStatus: r.status,
  apiCount: arr.length,
  apiTotal: Number.isFinite(body?.total) ? body.total : null,
  rows: m?.querySelectorAll('tbody tr').length ?? 0,
  tbodyCount: m?.querySelectorAll('tbody').length ?? 0,
  skeletons: m?.querySelectorAll('[data-slot="skeleton"]').length ?? 0,
  spinners: m?.querySelectorAll('svg.animate-spin').length ?? 0,
  alerts: m?.querySelectorAll('[role="alert"]').length ?? 0,
  alertText: [...(m?.querySelectorAll('[role="alert"]') || [])].map(e => e.innerText.trim()).slice(0, 2),
  retryLabels: btns.filter(b => /重试|retry/i.test(b.innerText || '')).map(b => b.innerText.trim()).slice(0, 3),
  inboxIcon: m?.querySelectorAll('svg.lucide-inbox').length ?? 0,
  emptyCardText: txt.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 6),
  hasSearchInput: !!m?.querySelector('input[type="text"], input[type="search"], input:not([type])'),
  searchPlaceholder: m?.querySelector('input[type="text"], input[type="search"], input:not([type])')?.getAttribute('placeholder') || null,
  totalRowsLabel: (txt.match(/共\s*\d+\s*行/) || [])[0] || null,
  pageOf: (txt.match(/第\s*\d+\s*\/\s*\d+\s*页/) || [])[0] || null,
  pagination: {
    first: !!btns.find(b => srOnly(b) === '跳转到第一页' || srOnly(b) === 'First page'),
    prev: !!btns.find(b => srOnly(b) === '上一页' || srOnly(b) === 'Previous page'),
    next: !!btns.find(b => srOnly(b) === '下一页' || srOnly(b) === 'Next page'),
    last: !!btns.find(b => srOnly(b) === '跳转到最后一页' || srOnly(b) === 'Last page'),
    disabled: btns.filter(b => ['跳转到第一页','上一页','下一页','跳转到最后一页'].includes(srOnly(b))).map(b => ({label: srOnly(b), disabled: b.disabled})),
  },
  pageSizeSelect: !!m?.querySelector('[role="combobox"]'),
  tableHeaderCount: m?.querySelectorAll('thead th').length ?? 0,
  noResultsText: /没有结果|No results/.test(txt),
};
"""


def probe(route: str, path: str, server_paged: bool) -> dict:
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    C.ab("wait", "350")
    api = path + ("?limit=10&offset=0" if server_paged else "")
    return C.ab_json(PROBE_JS.replace("__PATH__", json.dumps(api)))


def main() -> None:
    out = {"captured_at_utc": C.utc(), "phase": "pristine_empty_state_matrix", "pages": []}
    for route, path, sp in PAGES:
        try:
            res = probe(route, path, sp)
        except Exception as exc:  # noqa: BLE001
            res = {"route": route, "apiPath": path, "probe_error": str(exc)[:200]}
        res["serverPaged"] = sp
        out["pages"].append(res)
        print(json.dumps({k: res.get(k) for k in
                          ("route", "apiStatus", "apiCount", "rows", "skeletons",
                           "inboxIcon", "hasSearchInput", "noResultsText")},
                         ensure_ascii=False))
    rel = C.write_evidence("P-TABLE-01-pristine-empty-matrix.json", out)
    print(json.dumps({"evidence": rel}, ensure_ascii=False))


if __name__ == "__main__":
    main()
