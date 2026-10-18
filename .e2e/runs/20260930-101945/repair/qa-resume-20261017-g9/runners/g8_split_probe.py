"""G8 补充：跨块/跨流/分半输出探针（X16 Execution 遮蔽的关键边界）。

针对 X16 台账里已有的缺陷 BE-SECRET-STREAM-001（“secret 参数跨实时日志分块后可重组为明文”，
台账标 regression PASS）做一次独立复核：
 - halves：把秘密正好对半，两次 printf 相隔 1.5s（旧缺陷的原始复现形态）；
 - boundary：两半之间插入 200KB 填充，强制落在不同日志块；
 - cross-stream：一半走 stdout、一半走 stderr。
同时导出 script revision 原文，确认 Revision 载荷是否包含参数/秘密值。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
from g8_matrix import chunks_of, run_task, wait_terminal  # noqa: E402

TERMINAL = {"SUCCESS", "FAILED", "CANCELLED", "TIMEOUT", "BLOCKED", "SKIPPED"}


def make_task(hub, admin, name, command, node_id):
    r = hub.post("/api/tasks", admin, body={
        "name": name, "type": "command", "command": command,
        "target": {"type": "node", "node_ids": [node_id]},
        "parameters": [{"name": "marker_secret", "type": "secret", "required": False}],
        "param_values": {}, "timeout": 60, "retry": 0, "offline_policy": "allow_offline",
        "enabled": True})
    return {"http": r["status"], "id": (r["json"] or {}).get("id") if isinstance(r["json"], dict) else None,
            "body_head": (r["body_raw"] or b"")[:200].decode(errors="replace")}


HALVES = ("bash -c 'S=\"$NODESTEER_PARAM_marker_secret\"; printf \"%s\" \"${S:0:21}\"; "
          "sleep 1.5; printf \"%s\\n\" \"${S:21}\"'")
BOUNDARY = ("bash -c 'S=\"$NODESTEER_PARAM_marker_secret\"; printf \"%s\" \"${S:0:21}\"; "
            "head -c 200000 /dev/zero | tr \"\\0\" \"x\"; printf \"\\n%s\\n\" \"${S:21}\"'")
CROSS = ("bash -c 'S=\"$NODESTEER_PARAM_marker_secret\"; printf \"%s\" \"${S:0:21}\"; "
         "sleep 0.8; printf \"%s\\n\" \"${S:21}\" >&2'")


def run_probe(hub, admin, tag, task_id, marker, realtime=False):
    run = run_task(hub, admin, task_id, params={"marker_secret": marker})
    out = {"tag": tag, "task_id": task_id, "run": run, "marker_len": len(marker)}
    if not run["exec_ids"]:
        return out
    ex_id = run["exec_ids"][0]
    out["execution_id"] = ex_id
    polls = []
    if realtime:
        t0 = time.time()
        while time.time() - t0 < 15:
            _, ch = chunks_of(hub, admin, ex_id)
            polls.append({"t": round(time.time() - t0, 2),
                          "stdout": "".join(c["chunk"] for c in ch if c["stream"] == "stdout"),
                          "stderr": "".join(c["chunk"] for c in ch if c["stream"] == "stderr"),
                          "chunks": len(ch)})
            d = hub.get(f"/api/executions/{ex_id}", admin)["json"]
            if isinstance(d, dict) and d.get("status") in TERMINAL:
                break
            time.sleep(0.3)
    wait_terminal(hub, admin, ex_id)
    _, ch = chunks_of(hub, admin, ex_id)
    stdout = "".join(c["chunk"] for c in ch if c["stream"] == "stdout")
    stderr = "".join(c["chunk"] for c in ch if c["stream"] == "stderr")
    out["chunk_count"] = len(ch)
    out["chunk_streams"] = [[c["stream"], c["seq"], len(c["chunk"])] for c in ch]
    out["stdout"] = stdout[:600]
    out["stderr"] = stderr[:600]
    first, second = marker[:len(marker) // 2 + 1], marker[len(marker) // 2 + 1:]
    out["analysis"] = {
        "full_in_any_single_chunk": any(marker in c["chunk"] for c in ch),
        "full_in_stdout_concat": marker in stdout,
        "full_in_stderr_concat": marker in stderr,
        "full_in_stdout_plus_stderr": marker in (stdout + stderr),
        "half1_in_stdout": first in stdout,
        "half2_in_stdout": second in stdout,
        "half1_in_stderr": first in stderr,
        "half2_in_stderr": second in stderr,
        "recoverable_from_streams": (first in (stdout + stderr)) and (second in (stdout + stderr)),
        "redacted_marker_count": (stdout + stderr).count("[REDACTED]"),
        "filler_present": "xxxx" in stdout,
        "stdout_len": len(stdout), "stderr_len": len(stderr),
    }
    if realtime:
        out["realtime"] = {"polls": len(polls),
                           "snapshots_with_full_marker": [p["t"] for p in polls
                                                          if marker in (p["stdout"] + p["stderr"])],
                           "max_chunks_seen": max([p["chunks"] for p in polls] or [0])}
    return out


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])

    # 每次探针用不同的自建秘密值（全部非敏感标记）
    m_halves = f"QAMARK-HALVES-{nonce[-8:]}-0123456789abcdef"
    m_boundary = f"QAMARK-BOUNDARY-{nonce[-8:]}-fedcba9876543210"
    m_cross = f"QAMARK-CROSS-{nonce[-8:]}-abcdef0123456789"
    out = {"nonce": nonce, "at_utc": C.utc(), "markers_used": {
        "halves": m_halves, "boundary": m_boundary, "cross": m_cross}}

    t1 = make_task(hub, admin_call(hub, cr), f"qa-{nonce}-task-halves", HALVES, cr["node_id"])
    t2 = make_task(hub, admin_call(hub, cr), f"qa-{nonce}-task-boundary", BOUNDARY, cr["node_id"])
    t3 = make_task(hub, admin_call(hub, cr), f"qa-{nonce}-task-cross", CROSS, cr["node_id"])
    out["tasks_created"] = {"halves": t1, "boundary": t2, "cross": t3}

    if t1["id"]:
        out["probe_halves"] = run_probe(hub, admin_call(hub, cr), "halves", t1["id"], m_halves)
    if t2["id"]:
        out["probe_boundary"] = run_probe(hub, admin_call(hub, cr), "boundary", t2["id"], m_boundary,
                                          realtime=True)
    if t3["id"]:
        out["probe_cross"] = run_probe(hub, admin_call(hub, cr), "cross", t3["id"], m_cross)

    # Revision 原文（确认 Revision 载荷结构与是否含参数）
    sid = cr["script_id"]
    revs = C.as_list(hub.get(f"/api/scripts/{sid}/revisions", admin_call(hub, cr))["json"])
    out["revisions_raw"] = []
    for r in revs:
        if isinstance(r, dict):
            rn = r.get("revision")
            g = hub.get(f"/api/scripts/{sid}/revisions/{rn}", admin_call(hub, cr))
            out["revisions_raw"].append({"revision": rn, "http": g["status"], "payload": g["json"]})

    C.write_evidence("g8", "G8-split-stream-probes.json", out)
    print(json.dumps({k: (out[k] if k != "revisions_raw" else
                          [{kk: (vv if kk != "payload" else list(vv.keys()) if isinstance(vv, dict) else type(vv).__name__)
                            for kk, vv in r.items()} for r in out["revisions_raw"]])
                      for k in ("tasks_created", "probe_halves", "probe_boundary", "probe_cross",
                                "revisions_raw")}, ensure_ascii=False, indent=1)[:6000])


def admin_call(hub, cr):
    return hub.token(cr["admin_username"], cr["admin_password"])


if __name__ == "__main__":
    main()
