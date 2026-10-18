#!/usr/bin/env python3
"""Read-only physical owner/control/watchdog/proxy-event inspector for PENDING attempt06."""
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/tmp/qa-resume-20261008-child-ciivjupc")
UNIT = "nodesteer-agent-repair-20260930-101945-native-4.service"
NODE = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
AGENT = "agent-ab15a133-73cd-4025-8c97-f2ff37e000f5"
TASK = "61709c81-66b6-4e0f-8c5b-a969b721b222"
EXECUTION = "e7a4ce07-4a10-420c-909f-f2e96a91738d"
NONCE = "c2ce573d4aea"
WATCHDOG_PID = 50803
MANIFEST_SHA = "c8142503fc467066265b67a1db641ec3274119961f65cdadcd92f2ba26a2e2ce"
CONFIG_SHA = "2d36aeb7f541573683c5458f35e8322ea7b84024f33d01541f720b144dbbab1c"
CHILD = (49873, 137980573, 65534, "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add")
SUPERVISOR = (44932, 123677922, 65534, "078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531")
PROXY_SOURCE_SHA = "d9ac209a20ddda8a6101e80a9d42ddaa9be49af7892f403a98ef6e3b57c0cd57"
WATCHDOG_SOURCE_SHA = "573a1ad8a42c668678313a737ea038db39600c578b95d332049db8bdc518bc9b"
WINDOW_START = "2026-10-10T14:35:26Z"
WINDOW_END = "2026-10-10T14:35:48Z"
MAX_SMALL = 262144
MAX_EVENTS = 64 * 1024 * 1024


class GuardError(Exception):
    pass


def need(ok, code):
    if not ok:
        raise GuardError(code)


def safe(v):
    if isinstance(v, (str, int)) and len(str(v)) <= 160:
        s = str(v)
        return s if all(c.isalnum() or c in "_.:-" for c in s) else None
    return None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_guarded(path, limit, uid=None, gid=None, mode=None):
    p = Path(path)
    st = p.lstat()
    need(stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode), "file_not_regular")
    need(st.st_size <= limit, "file_size_guard")
    if uid is not None:
        need(st.st_uid == uid, "file_uid_guard")
    if gid is not None:
        need(st.st_gid == gid, "file_gid_guard")
    if mode is not None:
        need(stat.S_IMODE(st.st_mode) == mode, "file_mode_guard")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fs = os.fstat(fd)
        expected = (st.st_dev, st.st_ino, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode))
        actual = (fs.st_dev, fs.st_ino, fs.st_uid, fs.st_gid, stat.S_IMODE(fs.st_mode))
        need(actual == expected, "file_changed_during_open")
        raw = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    need(len(raw) <= limit, "file_read_limit")
    return st, raw


def proc(pid):
    p = Path("/proc") / str(pid)
    if not p.is_dir():
        return {"pid": pid, "present": False, "state": None, "ticks": None,
                "uid": None, "exe_sha256": None, "argv": [], "cgroup": ""}
    raw = (p / "stat").read_text()
    tail = raw[raw.rfind(")") + 2:].split()
    status = (p / "status").read_text().splitlines()
    uid = int(next(x for x in status if x.startswith("Uid:")).split()[1])
    argv = [x.decode("utf-8", "replace") for x in (p / "cmdline").read_bytes().split(b"\0") if x]
    cgroup = "".join(x.split(":", 2)[2] for x in (p / "cgroup").read_text().splitlines()
                     if x.startswith("0::"))
    try:
        exe_sha = sha((p / "exe").read_bytes())
    except OSError:
        exe_sha = None
    return {"pid": pid, "present": True, "state": tail[0], "ticks": int(tail[19]),
            "uid": uid, "exe_sha256": exe_sha, "argv": argv, "cgroup": cgroup}


def unit_props():
    p = subprocess.run(["systemctl", "show", UNIT, "-p", "LoadState", "-p", "ActiveState",
                        "-p", "MainPID", "-p", "ControlGroup"],
                       capture_output=True, text=True, timeout=6, check=False)
    need(p.returncode == 0, "unit_read_failed")
    return dict(x.split("=", 1) for x in p.stdout.splitlines() if "=" in x)


def sidecar(name, expected_statuses):
    path = ROOT / name
    out = {"name": name, "exists": False}
    try:
        st, raw = read_guarded(path, MAX_SMALL, uid=65534, gid=65534)
    except FileNotFoundError:
        return out
    out.update({"exists": True, "dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid,
                "gid": st.st_gid, "mode": format(stat.S_IMODE(st.st_mode), "04o"),
                "size": st.st_size, "sha256": sha(raw)})
    try:
        d = json.loads(raw)
        if not isinstance(d, dict):
            out["safe_error"] = "json_not_object"
            return out
        status = safe(d.get("status"))
        error = safe(d.get("safe_error") or d.get("error"))
        out.update({"status": status, "safe_error": error, "json_keys": sorted(d),
                    "task_id_matches": d.get("task_id") == TASK,
                    "nonce_matches": d.get("hold_nonce") == NONCE,
                    "expected_status": status in expected_statuses})
        for key in ("watchdog_pid", "exit_code"):
            if isinstance(d.get(key), int):
                out[key] = d[key]
        for key in ("hold_write_committed",):
            if isinstance(d.get(key), bool):
                out[key] = d[key]
    except (ValueError, TypeError):
        out["safe_error"] = "json_invalid"
    return out


def recursively_find_proxy_source(obj, keypath=""):
    hits = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = (keypath + "." + key).strip(".")
            low = path.lower()
            if isinstance(value, str) and "proxy" in low and "source" in low and "sha" in low:
                hits.append({"key_path": path, "matches_expected": value == PROXY_SOURCE_SHA})
            hits.extend(recursively_find_proxy_source(value, path))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            hits.extend(recursively_find_proxy_source(value, f"{keypath}[{i}]"))
    return hits


def inspect_declared_proxy_source(obj, keypath=""):
    results = []
    if isinstance(obj, dict):
        source_sha = None
        source_path = None
        for key, value in obj.items():
            low = key.lower()
            if isinstance(value, str) and "source" in low and "sha" in low:
                source_sha = value
            if isinstance(value, str) and "source" in low and ("path" in low or "file" in low):
                source_path = value
        relevant = "proxy" in keypath.lower() or any("proxy" in k.lower() for k in obj)
        if relevant and source_sha:
            row = {"declared_sha_matches_expected": source_sha == PROXY_SOURCE_SHA,
                   "source_file_checked": False}
            if source_path:
                candidate = Path(source_path)
                if not candidate.is_absolute():
                    candidate = ROOT / candidate
                try:
                    resolved = candidate.resolve(strict=True)
                    confined = resolved.is_relative_to(ROOT.resolve())
                    st = candidate.lstat()
                    regular = stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode)
                    row["source_path_confined"] = confined
                    row["source_file_regular"] = regular
                    if confined and regular and st.st_uid == 65534:
                        _, raw = read_guarded(candidate, 4 * 1024 * 1024, uid=65534)
                        got = sha(raw)
                        row["source_sha_matches_expected"] = got == PROXY_SOURCE_SHA
                        row["source_sha_matches_manifest"] = got == source_sha
                        row["source_file_checked"] = True
                except (OSError, GuardError):
                    row["source_file_status"] = "unavailable_or_guard_failed"
            results.append(row)
        for key, value in obj.items():
            results.extend(inspect_declared_proxy_source(value, (keypath + "." + key).strip(".")))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            results.extend(inspect_declared_proxy_source(value, f"{keypath}[{i}]"))
    return results


def parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def nested_values(obj):
    found = {"event": [], "type": [], "task_id": [], "execution_id": [],
             "id": [], "connection_id": [], "timestamp": []}
    if isinstance(obj, dict):
        for key, value in obj.items():
            k = str(key).lower()
            if k in found and isinstance(value, (str, int)):
                found[k].append(str(value))
            elif k in {"at_utc", "timestamp", "time", "created_at"} and isinstance(value, str):
                found["timestamp"].append(value)
            if isinstance(value, (dict, list)):
                child = nested_values(value)
                for name, values in child.items():
                    found[name].extend(values)
    elif isinstance(obj, list):
        for value in obj:
            child = nested_values(value)
            for name, values in child.items():
                found[name].extend(values)
    return found


def read_proxy_events(source_verified):
    path = ROOT / "proxy-events.jsonl"
    try:
        st, raw = read_guarded(path, MAX_EVENTS, uid=65534)
    except FileNotFoundError:
        return {"status": "EVENT_FILE_MISSING_UNKNOWN", "path": str(path),
                "events": [], "one_hold_one_release": None,
                "same_connection_id": None, "raw_lines_frames_payloads_emitted": False}
    except GuardError as exc:
        return {"status": "EVENT_FILE_GUARD_FAILED_UNKNOWN", "path": str(path),
                "safe_error": safe(str(exc)), "events": [], "one_hold_one_release": None,
                "same_connection_id": None, "raw_lines_frames_payloads_emitted": False}
    whole_sha = sha(raw)
    start = parse_ts(WINDOW_START)
    end = parse_ts(WINDOW_END)
    selected = []
    malformed = 0
    candidate_count = 0
    schema_counts = {k: 0 for k in ("event", "type", "task_id", "execution_or_id", "connection_id", "timestamp")}
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            malformed += 1
            continue
        if not isinstance(row, dict):
            continue
        fields = nested_values(row)
        ts = next((parse_ts(x) for x in fields["timestamp"] if parse_ts(x) is not None), None)
        if ts is None or ts < start or ts > end:
            continue
        for key in ("event", "type", "task_id", "connection_id"):
            if fields[key]:
                schema_counts[key] += 1
        if fields["execution_id"] or fields["id"]:
            schema_counts["execution_or_id"] += 1
        schema_counts["timestamp"] += 1
        event_name = next((safe(x) for x in fields["event"] if safe(x)), None)
        type_values = [x.upper() for x in fields["type"]]
        is_run = "RUN_EXECUTION" in type_values
        exec_ids = fields["execution_id"] + fields["id"]
        exact_exec = EXECUTION in exec_ids
        exact_task = TASK in fields["task_id"]
        lower_event = (event_name or "").lower()
        is_hold = "hold" in lower_event and "release" not in lower_event
        is_release = "release_held" in lower_event or "released_held" in lower_event
        if is_run and (exact_exec or exact_task):
            candidate_count += 1
        if is_run and exact_exec and exact_task and (is_hold or is_release):
            conns = [safe(x) for x in fields["connection_id"] if safe(x)]
            selected.append({"event": event_name, "time": ts.isoformat().replace("+00:00", "Z"),
                             "type": "RUN_EXECUTION", "execution_id": EXECUTION,
                             "task_id": TASK, "connection_id": conns[0] if conns else None,
                             "connection_id_count": len(set(conns))})
    holds = [x for x in selected if "hold" in (x["event"] or "").lower()
             and "release" not in (x["event"] or "").lower()]
    releases = [x for x in selected if "release_held" in (x["event"] or "").lower()
                or "released_held" in (x["event"] or "").lower()]
    same_connection = (len(holds) == 1 and len(releases) == 1
                       and holds[0]["connection_id"] is not None
                       and holds[0]["connection_id"] == releases[0]["connection_id"])
    hold_precedes_release = (len(holds) == 1 and len(releases) == 1
                             and holds[0]["time"] <= releases[0]["time"])
    schema_complete = all(schema_counts[k] > 0 for k in schema_counts)
    proof_ready = bool(source_verified and schema_complete)
    return {"status": "EVENT_SCHEMA_VERIFIED" if proof_ready else "EVENT_SCHEMA_UNKNOWN",
            "path": str(path), "dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid,
            "gid": st.st_gid, "mode": format(stat.S_IMODE(st.st_mode), "04o"),
            "size": st.st_size, "sha256": whole_sha, "window_utc": [WINDOW_START, WINDOW_END],
            "proxy_source_verified": source_verified, "schema_field_counts": schema_counts,
            "malformed_lines": malformed, "same_execution_or_task_run_event_count": candidate_count,
            "filtered_event_count": len(selected), "events": selected,
            "hold_count": len(holds), "release_held_count": len(releases),
            "one_hold_one_release": (len(holds) == 1 and len(releases) == 1) if proof_ready else None,
            "same_connection_id": same_connection if proof_ready and holds and releases else None,
            "hold_precedes_release": hold_precedes_release if proof_ready and holds and releases else None,
            "raw_lines_frames_payloads_emitted": False}


def main():
    out = {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "read_only": True,
           "node_id": NODE, "agent_id": AGENT, "task_id": TASK, "execution_id": EXECUTION,
           "attempt_nonce": NONCE, "secret_or_raw_event_content_emitted": False}
    st = ROOT.lstat()
    root_ok = (not ROOT.is_symlink() and ROOT.resolve() == ROOT
               and (st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode)) == (33, 135, 65534, 0o700))
    need(root_ok, "owned_root_guard_failed")
    out["root"] = {"dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid,
                   "mode": format(stat.S_IMODE(st.st_mode), "04o"), "guard_pass": True}

    manifest_path = ROOT / "owner-manifest.json"
    ms, manifest_raw = read_guarded(manifest_path, MAX_SMALL, uid=65534, gid=65534, mode=0o600)
    manifest_sha = sha(manifest_raw)
    need(manifest_sha == MANIFEST_SHA, "owner_manifest_sha_mismatch")
    manifest = json.loads(manifest_raw)
    need(isinstance(manifest, dict) and manifest.get("node_id") == NODE
         and manifest.get("agent_id") == AGENT and manifest.get("directory") == str(ROOT),
         "owner_manifest_identity_mismatch")
    proxy_declarations = recursively_find_proxy_source(manifest)
    proxy_file_checks = inspect_declared_proxy_source(manifest)
    source_verified = any(x.get("declared_sha_matches_expected") is True
                          and x.get("source_sha_matches_expected") is True
                          and x.get("source_sha_matches_manifest") is True
                          for x in proxy_file_checks)
    out["manifest"] = {"sha256": manifest_sha, "identity_matches": True,
                       "uid": ms.st_uid, "gid": ms.st_gid,
                       "mode": format(stat.S_IMODE(ms.st_mode), "04o"),
                       "proxy_source_declarations": proxy_declarations,
                       "proxy_source_file_checks": proxy_file_checks,
                       "proxy_source_status": "MATCH" if source_verified else "UNKNOWN_OR_MISMATCH"}

    child_cfg = ROOT / "child.yaml"
    cfg_st, cfg_raw = read_guarded(child_cfg, MAX_SMALL, uid=65534)
    need(sha(cfg_raw) == CONFIG_SHA, "child_config_sha_mismatch")
    out["child_config"] = {"dev": cfg_st.st_dev, "inode": cfg_st.st_ino,
                           "uid": cfg_st.st_uid, "mode": format(stat.S_IMODE(cfg_st.st_mode), "04o"),
                           "sha256_matches": True, "contents_emitted": False}

    u = unit_props()
    cg = u.get("ControlGroup", "")
    need(u.get("LoadState") == "loaded" and u.get("ActiveState") == "active"
         and u.get("MainPID") == "32487", "parent_unit_guard_failed")
    child = proc(CHILD[0])
    supervisor = proc(SUPERVISOR[0])
    child_cfg_arg = None
    for i, arg in enumerate(child["argv"][:-1]):
        if arg == "--config":
            child_cfg_arg = child["argv"][i + 1]
    child_ok = (child["present"] and child["state"] not in {"Z", "X"}
                and (child["ticks"], child["uid"], child["exe_sha256"]) == CHILD[1:]
                and child["cgroup"] == cg and child_cfg_arg == str(child_cfg))
    sup_ok = (supervisor["present"] and supervisor["state"] not in {"Z", "X"}
              and (supervisor["ticks"], supervisor["uid"], supervisor["exe_sha256"]) == SUPERVISOR[1:]
              and supervisor["cgroup"] == cg
              and any(a == str(ROOT / "supervisor.py") for a in supervisor["argv"]))
    need(child_ok and sup_ok, "child_or_supervisor_identity_mismatch")
    db_path = ROOT / "data/state.db"
    db_st = db_path.lstat()
    db_ok = (stat.S_ISREG(db_st.st_mode) and not db_path.is_symlink()
             and (db_st.st_dev, db_st.st_ino, db_st.st_uid) == (33, 155, 65534))
    need(db_ok, "owned_database_inode_guard_failed")
    out["database_path"] = {"dev": db_st.st_dev, "inode": db_st.st_ino,
                            "uid": db_st.st_uid, "mode": format(stat.S_IMODE(db_st.st_mode), "04o"),
                            "exact_owned_path_guard": True, "database_opened": False}
    out["unit"] = {"load_state": u.get("LoadState"), "active_state": u.get("ActiveState"),
                   "main_pid": int(u["MainPID"]), "cgroup_matches": True}
    out["processes"] = {"child": {"pid": CHILD[0], "startticks": CHILD[1], "uid": CHILD[2],
                                   "exe_sha256": CHILD[3], "config_path_matches": True},
                        "supervisor": {"pid": SUPERVISOR[0], "startticks": SUPERVISOR[1],
                                       "uid": SUPERVISOR[2], "exe_sha256": SUPERVISOR[3],
                                       "script_path_matches": True}}

    controls_st, controls_raw = read_guarded(ROOT / "controls.json", MAX_SMALL,
                                              uid=65534, gid=65534)
    controls = json.loads(controls_raw)
    need(isinstance(controls, dict), "controls_json_invalid")
    out["controls"] = {"path": str(ROOT / "controls.json"), "dev": controls_st.st_dev,
                       "inode": controls_st.st_ino, "uid": controls_st.st_uid,
                       "gid": controls_st.st_gid, "mode": format(stat.S_IMODE(controls_st.st_mode), "04o"),
                       "raw_bytes_sha256": sha(controls_raw), "empty": len(controls) == 0,
                       "keys": sorted(controls),
                       "hold_types": controls.get("hold_types") if isinstance(controls.get("hold_types"), list) else []}

    out["sidecars"] = [
        sidecar("arm-evidence-" + NONCE + ".json", {"ARM_GUARD_STARTED", "ARM_GUARD_FAILED", "HOLD_ARMED"}),
        sidecar("watchdog-ready-" + NONCE + ".json", {"WATCHDOG_READY"}),
        sidecar("watchdog-arm-" + NONCE + ".json", {"ARMED"}),
        sidecar("watchdog-result-" + NONCE + ".json", {"OWNED_HOLD_RELEASED", "NOOP_CONTROL_OWNER_MISMATCH", "WATCHDOG_GUARD_FAILED"}),
    ]

    watchdog_path = ROOT / ("qa-pending-ui-watchdog-" + WATCHDOG_SOURCE_SHA + ".py")
    try:
        ws, watchdog_source = read_guarded(watchdog_path, MAX_SMALL, uid=65534, gid=65534, mode=0o700)
        watchdog_sha = sha(watchdog_source)
        out["watchdog_source"] = {"status": "PRESENT", "path": str(watchdog_path),
                                  "dev": ws.st_dev, "inode": ws.st_ino, "uid": ws.st_uid,
                                  "gid": ws.st_gid, "mode": format(stat.S_IMODE(ws.st_mode), "04o"),
                                  "sha256": watchdog_sha,
                                  "matches_reviewed_sha": watchdog_sha == WATCHDOG_SOURCE_SHA}
    except FileNotFoundError:
        out["watchdog_source"] = {"status": "MISSING_UNKNOWN", "path": str(watchdog_path)}
    except (GuardError, OSError) as exc:
        out["watchdog_source"] = {"status": "GUARD_FAILED_UNKNOWN", "safe_error": safe(str(exc))}

    watchdog1 = proc(WATCHDOG_PID)
    if not watchdog1["present"]:
        out["watchdog_process"] = {"pid": WATCHDOG_PID, "state": "ABSENT", "live": False}
    elif watchdog1["state"] in {"Z", "X"}:
        watchdog2 = proc(WATCHDOG_PID)
        out["watchdog_process"] = {"pid": WATCHDOG_PID, "state": "ZOMBIE", "live": False,
                                   "stable_ticks": watchdog1["ticks"] == watchdog2["ticks"],
                                   "uid": watchdog2["uid"]}
    else:
        time.sleep(0.05)
        watchdog2 = proc(WATCHDOG_PID)
        argv = watchdog2["argv"]
        ok = (watchdog2["present"] and watchdog1["ticks"] == watchdog2["ticks"]
              and watchdog2["uid"] == 65534 and watchdog2["cgroup"] == cg
              and len(argv) > 1 and argv[1] == str(watchdog_path)
              and "--task-id" in argv and argv[argv.index("--task-id") + 1] == TASK
              and "--hold-nonce" in argv and argv[argv.index("--hold-nonce") + 1] == NONCE)
        out["watchdog_process"] = {"pid": WATCHDOG_PID,
                                   "state": "LIVE_MATCH" if ok else "IDENTITY_MISMATCH",
                                   "live": bool(watchdog2["present"]),
                                   "startticks": watchdog2["ticks"], "uid": watchdog2["uid"],
                                   "argv_task_nonce_root_match": ok}

    out["proxy_events"] = read_proxy_events(source_verified)
    out["proxy_source_expected_sha256"] = PROXY_SOURCE_SHA
    out["helper_status"] = "READONLY_INSPECTOR_COMPLETE"
    return out


try:
    result = main()
except Exception as exc:
    result = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
              "helper_status": "READONLY_INSPECTOR_BLOCKED",
              "safe_error": safe(str(exc)) or type(exc).__name__,
              "read_only": True, "secret_or_raw_event_content_emitted": False}
print(json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
