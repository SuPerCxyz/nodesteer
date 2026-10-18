"""P-TABLE：隔离实例上 11 个列表页的 Loading / Empty / Error / Retry 真实矩阵。

对每个列表页：
1) 硬刷新时暂停精确列表 GET → 采样真实 Loading 反馈，再放行得到 200；
2) 对同一精确 GET 制造 5 次真实 net::ERR_FAILED → 采样真实 ErrorState + Retry；
3) 解除拦截后真实点击 Retry → 断言精确 GET 200、UI 恢复、alert 清除；
4) 只读 oracle 判定真实 Empty（API 0 条且有空态反馈）。
"""
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

CASES = {
    "TABLE-NODES-ERROR": ("/nodes", "/api/nodes", False),
    "TABLE-GROUPS-ERROR": ("/groups", "/api/groups", False),
    "TABLE-SCRIPTS-ERROR": ("/scripts", "/api/scripts", False),
    "TABLE-TASKS-ERROR": ("/tasks", "/api/tasks", False),
    "TABLE-SCHEDULES-ERROR": ("/schedules", "/api/schedules", False),
    "TABLE-APPLICATIONS-ERROR": ("/applications", "/api/applications", False),
    "TABLE-ARTIFACTS-ERROR": ("/artifacts", "/api/artifacts", False),
    "TABLE-EXECUTIONS-ERROR": ("/executions", "/api/executions", True),
    "TABLE-TRANSFERS-ERROR": ("/transfers", "/api/transfers", False),
    "TABLE-AUDIT-ERROR": ("/audit", "/api/audit", True),
    "TABLE-USERS-ERROR": ("/users", "/api/users", False),
}

ORACLE = r"""
const api = __API__;
const t = localStorage.getItem('nodesteer_token');
const r = await fetch(api, { headers: { Authorization: 'Bearer ' + t } });
let body = null; try { body = await r.json(); } catch (e) {}
const arr = Array.isArray(body) ? body
  : Array.isArray(body?.items) ? body.items
  : Array.isArray(body?.data) ? body.data
  : Array.isArray(body?.data?.items) ? body.data.items : [];
const m = document.querySelector('main'); const txt = m ? m.innerText : '';
const btns = [...(m?.querySelectorAll('button') || [])];
return {
  route: location.pathname, apiPath: api, apiStatus: r.status, apiCount: arr.length,
  apiTotal: Number.isFinite(body?.total) ? body.total : null,
  rows: m?.querySelectorAll('tbody tr').length ?? 0,
  skeletons: m?.querySelectorAll('[data-slot="skeleton"]').length ?? 0,
  spinners: m?.querySelectorAll('svg.animate-spin').length ?? 0,
  alerts: m?.querySelectorAll('[role="alert"]').length ?? 0,
  alertText: [...(m?.querySelectorAll('[role="alert"]') || [])].map(e => e.innerText.trim()).slice(0, 2),
  retryButtons: btns.filter(b => /重试|retry/i.test(b.innerText || '')).length,
  inboxIcon: m?.querySelectorAll('svg.lucide-inbox').length ?? 0,
  noResultsText: /没有结果|No results/.test(txt),
  emptyFeedback: (m?.querySelectorAll('svg.lucide-inbox').length ?? 0) > 0
    || /没有结果|暂无数据|暂无|未找到|没有匹配|No results/i.test(txt),
};"""


def env() -> dict:
    e = dict(os.environ)
    e["PTABLE_SESSION"] = C.SESSION
    e["PTABLE_ORIGIN"] = C.origin()
    return e


def start_runner(name: str, path: str, route: str):
    proc = subprocess.Popen(
        ["node", str(Path(__file__).resolve().parent / name), path, route],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env())
    lines, ready = [], None
    deadline = time.time() + 12
    while time.time() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 0.2)
        if r:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.strip())
            try:
                obj = json.loads(line)
                if obj.get("phase") == "READY":
                    ready = obj
                    break
            except Exception:  # noqa: BLE001
                pass
        if proc.poll() is not None:
            break
    if not ready:
        proc.kill()
        out, err = proc.communicate(timeout=5)
        return None, lines, {"harness_error": "runner_ready_timeout", "stderr": err.strip()[:300]}
    return proc, lines, ready


def finish_runner(proc, lines, timeout=30):
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
        lines.append("runner_timeout")
    lines.extend(l.strip() for l in out.splitlines())
    return lines


def get_oracle(api: str) -> dict:
    return C.ab_json(ORACLE.replace("__API__", json.dumps(api)))


def find_retry_ref() -> str | None:
    snap = C.ab("snapshot", timeout=20).stdout
    refs = []
    for line in snap.splitlines():
        if "button" in line and "重试" in line:
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            if m:
                refs.append(m.group(1))
    return refs[0] if len(refs) == 1 else None


def run_case(case_id: str) -> dict:
    route, path, sp = CASES[case_id]
    api = path + ("?limit=10&offset=0" if sp else "")
    ev = {"case_id": case_id, "route": route, "list_get_path": path,
          "captured_at_utc": C.utc(), "instance": "isolated-18082"}
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    if C.ab("get", "url").stdout.strip() != C.origin() + route:
        raise RuntimeError("route_guard_failed")

    # --- Loading ---
    proc, logs, ready = start_runner("loading-sampler.mjs", path, route)
    if not proc:
        ev["loading_harness_error"] = ready
        return {**ev, "status": "PARTIAL",
                "remaining": ["Loading sampler 无法就绪：harness 异常，未取得 Loading 证据。"]}
    C.ab("reload", timeout=30)
    logs = finish_runner(proc, logs)
    parsed = [json.loads(x) for x in logs if x.startswith("{")]
    loading = next((x for x in parsed if x.get("phase") == "LOADING_SAMPLED"), {})
    cont = next((x for x in parsed if x.get("phase") == "APP_GET_200"), {})
    fin = next((x for x in parsed if x.get("phase") == "FINISHED"), {})
    after_load = get_oracle(api)
    C.write_evidence(f"{case_id}-loading.json", {
        **ev, "phase": "hard reload: exact GET paused then continued",
        "loading_sampler": {"ready": ready, "sample": loading.get("sample"),
                            "loadingSamples": loading.get("loadingSamples"),
                            "loading_sampler_finished": fin},
        "actual_app_get_200": cont, "after_success_oracle": after_load})

    # --- Error ---
    proc, logs, ready = start_runner("failure-observer.mjs", path, route)
    if not proc:
        ev["failure_harness_error"] = ready
        return {**ev, "status": "PARTIAL",
                "remaining": ["Failure observer 无法就绪：harness 异常，未取得 Error/Retry 证据。"]}
    C.ab("reload", timeout=30)
    logs = finish_runner(proc, logs)
    parsed = [json.loads(x) for x in logs if x.startswith("{")]
    err = next((x for x in parsed if x.get("phase") == "ERROR_STATE_SAMPLE"), {})
    ffin = next((x for x in parsed if x.get("phase") == "FINISHED"), {})
    retry_ref = find_retry_ref() if err.get("errorState") else None
    snap_err = get_oracle(api)
    C.write_evidence(f"{case_id}-failure.json", {
        **ev, "phase": "exact GET failed 5x", "error_sample": err,
        "failure_finished": ffin, "retry_ref_found": bool(retry_ref),
        "oracle_after_failure": snap_err})

    # --- Retry ---
    C.ab("network", "requests", "--clear", check=False)
    clicked = False
    if retry_ref:
        try:
            C.ab("click", f"@{retry_ref}", timeout=25)
            clicked = True
        except Exception as exc:  # noqa: BLE001
            ev["retry_click_error"] = str(exc)[:200]
        time.sleep(1.8)
    reqs = C.ab("network", "requests", "--filter", path, check=False).stdout
    statuses = []
    for line in reqs.splitlines():
        if " GET " in line and path in line:
            m = re.search(r"\s(\d{3})\s*$", line)
            if m:
                statuses.append(int(m.group(1)))
    post = get_oracle(api)
    true_empty = bool(post.get("apiStatus") == 200 and post.get("apiCount") == 0
                      and post.get("emptyFeedback"))
    recovered = bool(post.get("apiStatus") == 200 and post.get("alerts") == 0
                     and ((post.get("apiCount", 0) == 0 and post.get("emptyFeedback"))
                          or (post.get("apiCount", 0) > 0 and post.get("rows", 0) > 0)))
    C.write_evidence(f"{case_id}-retry.json", {
        **ev, "phase": "user clicked Retry after interception removed",
        "retry_button_clicked": clicked, "exact_get_statuses_after_click": statuses,
        "oracle_after_retry": post,
        "true_empty_confirmed": true_empty, "recovered": recovered})

    ls = loading.get("sample") or {}
    loading_seen = bool(ls.get("skeletons") or ls.get("progressbars") or ls.get("spinners")
                        or ls.get("ariaBusy") or ls.get("loadingText"))
    load_ok = loading_seen and cont.get("status") == 200
    error_ok = bool(err.get("errorState") and err.get("retryVisible")
                    and (err.get("loadingFailedEvents") or 0) > 0)
    retry_ok = clicked and 200 in statuses and recovered
    ok = load_ok and error_ok and retry_ok
    remaining = []
    if not loading_seen:
        remaining.append("暂停精确 GET 时未采到 Loading 反馈（骨架/旋转/加载文案）。")
    if not error_ok:
        remaining.append("精确 GET 失败后未完整观测到真实 ErrorState + Retry。")
    if not retry_ok:
        remaining.append("点击 Retry 后未验证精确 GET 200 与 UI 恢复。")
    if post.get("apiStatus") == 200 and post.get("apiCount", 0) > 0:
        if case_id == "TABLE-USERS-ERROR":
            remaining.append("用户表真空态在产品上不可达：管理员账号必然存在（本实例 1 个用户），未删除账号造空态。")
        elif case_id == "TABLE-AUDIT-ERROR":
            remaining.append("审计表真空态在产品上不可达：任何登录/写操作都会写入审计行（本实例至少 1 行）。")
        else:
            remaining.append(f"该表真空态未达成：API 返回 {post.get('apiCount')} 条。")
    elif not true_empty:
        remaining.append("API 返回 0 条但未观测到真实空态反馈。")
    actual = {
        "loading_feedback_observed": loading_seen,
        "initial_get_200": cont.get("status") == 200,
        "real_fetch_failures": err.get("loadingFailedEvents", 0),
        "error_state_and_retry": error_ok,
        "retry_clicked": clicked,
        "retry_200": 200 in statuses,
        "retry_recovered_ui": recovered,
        "api_count_after_retry": post.get("apiCount"),
        "ui_rows_after_retry": post.get("rows"),
        "true_empty_confirmed": true_empty,
        "evidence": [f"evidence/{case_id}-loading.json",
                     f"evidence/{case_id}-failure.json",
                     f"evidence/{case_id}-retry.json"],
    }
    return {**ev, "status": "PASS" if ok else "PARTIAL", "actual": actual,
            "remaining": remaining, "server_writes": 0}


def main() -> None:
    only = sys.argv[1:] if len(sys.argv) > 1 else list(CASES)
    state_path = C.ROOT / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"cases": {}}
    for cid in only:
        res = run_case(cid)
        state["cases"][cid] = res
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({cid: {"status": res.get("status"),
                                "loading": (res.get("actual") or {}).get("loading_feedback_observed"),
                                "error": (res.get("actual") or {}).get("error_state_and_retry"),
                                "retry200": (res.get("actual") or {}).get("retry_200"),
                                "trueEmpty": (res.get("actual") or {}).get("true_empty_confirmed"),
                                "apiCount": (res.get("actual") or {}).get("api_count_after_retry")}},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
