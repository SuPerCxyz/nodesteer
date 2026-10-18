"""G8（X16）遮蔽矩阵 —— API/执行/审计/错误/实时维度（全程只在本隔离实例内）。

逐项：
 A 默认值（script/task 的 secret default）在 API 与执行中的处理；
 B Revision（script 历史版本）内是否出现秘密值；
 C Execution（实时轮询 + 终态 stdout/stderr/明细）；
 D Audit（/api/audit 全量行）；
 E 错误路径（required 缺失 / 目标解析失败 / 运行期失败 / 校验失败）；
 F 证据目录与响应泄漏（Registration Token / Authorization / Cookie）→ 见 g8_leakscan.py。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402

TERMINAL = {"SUCCESS", "FAILED", "CANCELLED", "TIMEOUT", "SKIPPED", "BLOCKED", "SKIP"}


def run_task(hub, admin, task_id, params=None, node_ids=None):
    body = {}
    if params is not None:
        body["params"] = params
    if node_ids is not None:
        body["node_ids"] = node_ids
    r = hub.post(f"/api/tasks/{task_id}/run", admin, body=body)
    ex_ids = []
    j = r["json"]
    for e in (j if isinstance(j, list) else C.as_list(j)):
        if isinstance(e, dict) and e.get("id"):
            ex_ids.append(e["id"])
    return {"http": r["status"], "exec_ids": ex_ids,
            "body_head": (r["body_raw"] or b"")[:300].decode(errors="replace")}


def wait_terminal(hub, admin, ex_id, timeout=40):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        d = hub.get(f"/api/executions/{ex_id}", admin)["json"]
        if isinstance(d, dict):
            last = d
            if d.get("status") in TERMINAL:
                return d
        time.sleep(0.5)
    return last or {}


def chunks_of(hub, admin, ex_id):
    r = hub.get(f"/api/executions/{ex_id}/logs", admin)
    raw = C.as_list(r["json"])
    out = []
    for c in raw:
        if isinstance(c, dict):
            out.append({"stream": c.get("stream"), "seq": c.get("seq"),
                        "chunk": c.get("chunk") if isinstance(c.get("chunk"), str) else ""})
    return r["status"], sorted(out, key=lambda x: (x["stream"] or "", x["seq"] or 0))


def analyze(stdout: str, stderr: str, markers: dict, execution_detail: dict | None = None):
    res = {"stdout_len": len(stdout), "stderr_len": len(stderr)}
    for name, m in markers.items():
        pre, suf = m[:8], m[-8:]
        res[f"full_in_stdout[{name}]"] = m in stdout
        res[f"full_in_stderr[{name}]"] = m in stderr
        res[f"prefix8_in_stdout[{name}]"] = pre in stdout
        res[f"suffix8_in_stderr[{name}]"] = suf in stderr
        if execution_detail is not None:
            detail_txt = json.dumps({k: v for k, v in execution_detail.items()
                                     if k in ("stdout", "stderr", "block_reason", "error")},
                                    ensure_ascii=False)
            res[f"full_in_execution_detail[{name}]"] = m in detail_txt
    res["redacted_token_count_stdout"] = stdout.count("[REDACTED]")
    res["redacted_token_count_stderr"] = stderr.count("[REDACTED]")
    return res


def exec_probe(hub, admin, tag, task_id, params=None, realtime=False, node_ids=None):
    run = run_task(hub, admin, task_id, params=params, node_ids=node_ids)
    out = {"tag": tag, "run": run}
    if not run["exec_ids"]:
        return out
    ex_id = run["exec_ids"][0]
    out["execution_id"] = ex_id
    polls = []
    if realtime:
        t0 = time.time()
        while time.time() - t0 < 12:
            _, ch = chunks_of(hub, admin, ex_id)
            polls.append({"t": round(time.time() - t0, 2),
                          "stdout": "".join(c["chunk"] for c in ch if c["stream"] == "stdout"),
                          "stderr": "".join(c["chunk"] for c in ch if c["stream"] == "stderr")})
            d = hub.get(f"/api/executions/{ex_id}", admin)["json"]
            if isinstance(d, dict) and d.get("status") in TERMINAL:
                break
            time.sleep(0.25)
    detail = wait_terminal(hub, admin, ex_id)
    code, ch = chunks_of(hub, admin, ex_id)
    stdout = "".join(c["chunk"] for c in ch if c["stream"] == "stdout")
    stderr = "".join(c["chunk"] for c in ch if c["stream"] == "stderr")
    out["terminal_status"] = detail.get("status")
    out["exit_code"] = detail.get("exit_code")
    out["chunk_count"] = len(ch)
    out["chunks"] = [{"stream": c["stream"], "seq": c["seq"], "len": len(c["chunk"]),
                      "head": c["chunk"][:60]} for c in ch[:20]]
    out["stdout_head"] = stdout[:200]
    out["stderr_head"] = stderr[:200]
    out["analysis"] = analyze(stdout, stderr, OUT["markers"], detail)
    if realtime:
        snap_full = []
        for p in polls:
            for nm, m in OUT["markers"].items():
                if m in p["stdout"] or m in p["stderr"]:
                    snap_full.append({"t": p["t"], "marker": nm, "stream": "stdout/stderr"})
        out["realtime"] = {"polls": len(polls), "snapshots_with_full_marker": snap_full,
                           "last_stdout_head": (polls[-1]["stdout"][:160] if polls else None)}
    out["execution_detail_scan"] = {
        k: (OUT["markers"]["task_value"] in v if isinstance(v, str) else None)
        for k, v in (detail or {}).items() if k in ("stdout", "stderr", "block_reason")}
    return out


OUT: dict = {}


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    admin = C.Hub(cr["local_base"]).token(cr["admin_username"], cr["admin_password"])
    hub = C.Hub(cr["local_base"])
    M = cr["markers"]
    OUT.update({"nonce": nonce, "at_utc": C.utc(), "authority": cr["base_url"], "markers": M,
                "task_ids": {k: cr.get("task_" + k) for k in
                             ("full", "split", "stderr", "fail", "script_default", "missing_required")},
                "script_id": cr["script_id"]})
    sid = cr["script_id"]

    # ---------------- A. 默认值 ----------------
    sc = hub.get(f"/api/scripts/{sid}", admin)
    tf = hub.get(f"/api/tasks/{cr['task_full']}", admin)
    A = {"script_get_http": sc["status"], "task_get_http": tf["status"]}
    A["script_response_contains_script_default"] = M["script_default"] in (sc["body_raw"] or b"").decode(errors="replace")
    A["task_response_contains_task_default"] = M["task_default"] in (tf["body_raw"] or b"").decode(errors="replace")
    A["task_response_contains_task_value"] = M["task_value"] in (tf["body_raw"] or b"").decode(errors="replace")
    A["script_param_fields"] = [{"name": p.get("name"), "type": p.get("type"),
                                 "default_present": bool(p.get("default")),
                                 "default_len": len(p.get("default") or "")}
                                for p in ((sc["json"] or {}).get("parameters") or [])]
    A["task_param_fields"] = [{"name": p.get("name"), "type": p.get("type"),
                               "default_present": bool(p.get("default"))}
                              for p in ((tf["json"] or {}).get("parameters") or [])]
    A["note"] = ("管理员鉴权 API 返回明文 default/param_values 是编辑所必需（同一取值用于下发执行）；"
                 "本用例的“默认值遮蔽”断言面为 UI 摘要 + 执行侧，见 g8_ui.py 与下面 C 段。")
    OUT["A_default_masking"] = A

    # ---------------- B. Revision ----------------
    B = {}
    cur = sc["json"] if isinstance(sc["json"], dict) else {}
    new_params = []
    for p in cur.get("parameters") or []:
        q = dict(p)
        if q.get("type") == "secret":
            q["default"] = M["revision"]
        new_params.append(q)
    put = hub.put(f"/api/scripts/{sid}", admin, body={**cur, "parameters": new_params,
                                                      "description": f"g8 revision probe {nonce[-8:]}"})
    B["put_http"] = put["status"]
    revs = hub.get(f"/api/scripts/{sid}/revisions", admin)
    rev_list = C.as_list(revs["json"])
    B["revisions_http"] = revs["status"]
    B["revision_count"] = len(rev_list)
    B["revision_summaries"] = [{k: (v if not isinstance(v, str) or len(v) < 60 else v[:57] + "...")
                               for k, v in r.items() if k in ("revision", "created_at", "description", "sha256")}
                               for r in rev_list if isinstance(r, dict)][:6]
    B["per_revision_marker_scan"] = []
    for r in rev_list:
        if not isinstance(r, dict):
            continue
        rn = r.get("revision")
        g = hub.get(f"/api/scripts/{sid}/revisions/{rn}", admin)
        txt = (g["body_raw"] or b"").decode(errors="replace")
        B["per_revision_marker_scan"].append({
            "revision": rn, "http": g["status"],
            "contains_script_default": M["script_default"] in txt,
            "contains_revision_marker": M["revision"] in txt,
            "contains_redacted_token": "[REDACTED]" in txt,
            "len": len(txt)})
    B["note"] = "Revision 历史为管理员鉴权 API；记录明文/遮蔽实况（UI 侧见 g8_ui.py）。"
    OUT["B_revision_masking"] = B

    # ---------------- C. Execution ----------------
    C_ = {}
    C_["full"] = exec_probe(hub, admin, "full", cr["task_full"])
    C_["split_realtime"] = exec_probe(hub, admin, "split", cr["task_split"], realtime=True)
    C_["stderr"] = exec_probe(hub, admin, "stderr", cr["task_stderr"])
    C_["fail"] = exec_probe(hub, admin, "fail", cr["task_fail"])
    C_["script_default_chain"] = exec_probe(hub, admin, "script_default", cr["task_script_default"])
    # 运行期由运行参数覆盖默认值的显式样本
    C_["run_with_supplied_secret"] = exec_probe(
        hub, admin, "full+supplied", cr["task_full"],
        params={"marker_secret": M["ui_input"]})
    OUT["C_execution_masking"] = C_

    # ---------------- D. Audit ----------------
    code, rows = C.audit_rows(hub, admin, limit=500)
    audit_blob = json.dumps(rows, ensure_ascii=False)
    D = {"http": code, "rows": len(rows),
         "row_keys": sorted(rows[0].keys()) if rows and isinstance(rows[0], dict) else [],
         "marker_hits": {name: audit_blob.count(m) for name, m in M.items() if m in audit_blob},
         "sample_actions": sorted({str(r.get("action") or r.get("Action")) for r in rows
                                   if isinstance(r, dict)})[:25]}
    OUT["D_audit_masking"] = D

    # ---------------- E. 错误路径 ----------------
    E = {}
    # E1 required 缺失（先移除该 task 的 default）
    t_miss = cr["task_missing_required"]
    tjson = hub.get(f"/api/tasks/{t_miss}", admin)["json"]
    if isinstance(tjson, dict):
        tjson["parameters"] = [{"name": "marker_secret", "type": "secret", "required": True}]
        tjson["param_values"] = {}
        put2 = hub.put(f"/api/tasks/{t_miss}", admin, body=tjson)
        E["required_fixture_put_http"] = put2["status"]
    r1 = run_task(hub, admin, t_miss, params={})
    E["required_missing_run"] = {"http": r1["http"], "exec_ids": r1["exec_ids"],
                                 "error_head": r1["body_head"][:200],
                                 "echoes_error_marker": M["error"] in r1["body_head"]}
    # E2 目标解析失败（伪造 node id）+ 同时带 secret 值
    r2 = run_task(hub, admin, cr["task_full"],
                  params={"marker_secret": M["error"]},
                  node_ids={"00000000-0000-0000-0000-000000000000": True})
    E["bogus_target_run"] = {"http": r2["http"], "exec_ids": r2["exec_ids"],
                             "error_head": r2["body_head"][:300],
                             "echoes_supplied_secret": M["error"] in r2["body_head"]}
    # E3 运行期失败（exit 7，stdout/stderr 各含秘密片段）
    fail_probe = OUT["C_execution_masking"]["fail"]
    E["runtime_failure"] = {
        "execution_id": fail_probe.get("execution_id"),
        "status": fail_probe.get("terminal_status"),
        "exit_code": fail_probe.get("exit_code"),
        "full_marker_in_stdout": fail_probe.get("analysis", {}).get("full_in_stdout[task_value]"),
        "error_field_present": bool((fail_probe.get("execution_detail_scan") or {})),
    }
    # E4 校验失败（篡改 script 参数类型）
    bad = dict(cur)
    bad["parameters"] = [{"name": "probe", "type": "not-a-type", "required": False,
                          "default": "full"},
                         {"name": "marker_secret", "type": "secret", "required": False,
                          "default": M["error"]}]
    r4 = hub.put(f"/api/scripts/{sid}", admin, body=bad)
    E["invalid_type_put"] = {"http": r4["status"], "error_head": (r4["body_raw"] or b"")[:200].decode(errors="replace"),
                             "echoes_error_marker": M["error"] in (r4["body_raw"] or b"").decode(errors="replace")}
    # 恢复合法定义
    hub.put(f"/api/scripts/{sid}", admin, body={**cur, "parameters": new_params,
                                                "description": f"g8 revision probe {nonce[-8:]} r2"})
    OUT["E_error_masking"] = E

    C.write_evidence("g8", "G8-masking-matrix-api.json", OUT)
    (C.EVID_DIRS["g8"].parent / "matrix-raw.json").write_text(
        json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
    summary = {
        "A": {k: A[k] for k in ("script_response_contains_script_default",
                                "task_response_contains_task_default",
                                "task_response_contains_task_value")},
        "B": {"revision_count": B["revision_count"], "per_revision_marker_scan": B["per_revision_marker_scan"]},
        "C": {k: {"status": v.get("terminal_status"),
                  "full_in_stdout": v.get("analysis", {}).get("full_in_stdout[task_value]"),
                  "full_in_stderr": v.get("analysis", {}).get("full_in_stderr[task_value]"),
                  "redacted_stdout": v.get("analysis", {}).get("redacted_token_count_stdout"),
                  "redacted_stderr": v.get("analysis", {}).get("redacted_token_count_stderr"),
                  "realtime": v.get("realtime")}
              for k, v in C_.items()},
        "D": D, "E": E,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
