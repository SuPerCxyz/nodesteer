"""P-TABLE：补齐 transfers fixtures（POST 体 mode 为 uint32，先前传字符串导致 400）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    nonce = led["nonce"]
    js = r"""
const nonce = __NONCE__;
const t = localStorage.getItem('nodesteer_token');
const H = { Authorization: 'Bearer ' + t, 'Content-Type': 'application/json' };
const j = async (method, path, body) => {
  const r = await fetch(path, { method, headers: H, body: JSON.stringify(body) });
  let b = null; try { b = await r.json(); } catch (e) {}
  return { status: r.status, body: b };
};
const g = async (p) => { const r = await fetch(p, { headers: H }); return await r.json().catch(() => null); };
const nodes = await g('/api/nodes');
const ids = (nodes || []).map(x => x.id);
const errors = [];
let trs = await g('/api/transfers') || [];
if (trs.length < 12) {
  for (let i = trs.length; i < 12; i++) {
    const r = await j('POST', '/api/transfers', {
      source_node_id: ids[0],
      source_path: `/var/tmp/ptable-${nonce}-${i + 1}.txt`,
      targets: [{ node_id: ids[1], destination_path: `/var/tmp/ptable-dst-${i + 1}.txt` }],
    });
    if (r.status >= 300) errors.push({ step: i, status: r.status, body: r.body });
  }
}
trs = await g('/api/transfers') || [];
return {
  transfers: trs.length,
  errors,
  ids: trs.slice(0, 3).map(x => x.id),
  statuses: [...new Set(trs.map(x => x.status))],
  audit_total: (await g('/api/audit?limit=1&offset=0') || {}).total,
  executions_total: (await g('/api/executions?limit=1&offset=0') || {}).total,
};
""".replace("__NONCE__", json.dumps(nonce))
    C.ab("open", C.origin() + "/transfers", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    res = C.ab_json(js, timeout=180)
    fl = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    fl["final_counts"]["transfers"] = res["transfers"]
    fl["final_counts"]["audit_total"] = res.get("audit_total")
    fl["final_counts"]["executions_total"] = res.get("executions_total")
    fl["transfer_fixture"] = {
        "count": res["transfers"],
        "statuses_observed": res.get("statuses"),
        "errors": res.get("errors"),
        "note": "POST /api/transfers mode 字段为 uint32，省略即默认值；先前字符串导致 400，已修正并真实创建。",
    }
    (C.ROOT / "fixture-ledger.json").write_text(
        json.dumps(fl, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"transfers": res["transfers"], "errors": res.get("errors"),
                      "statuses": res.get("statuses"),
                      "audit_total": res.get("audit_total")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
