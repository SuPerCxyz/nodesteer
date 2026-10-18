#!/usr/bin/env python3
"""Read-only Native3 owner, managed Application, Unit, and process oracle."""
import hashlib, json, os, re, sqlite3, stat, subprocess
from pathlib import Path
from urllib.parse import quote
NODE_ID = "37989297-3bc8-4c0c-b302-7c6efbdba630"
AGENT_ID = "agent-bb005487-7d84-418c-b598-f93d8b0c86a0"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
AGENT_UNIT = "nodesteer-agent-repair-20260930-101945-native-3.service"
NONCE = "b3ca14953c14"
APP_NAME = "qa-p11-05-app-b3ca14953c14"
APP_ROOT = Path("/opt/nodesteer-qa-p11-05-b3ca14953c14")
BINARY = Path("/opt/nodesteer-qa-p11-05-b3ca14953c14/bin/sleeper")
UNIT = "nodesteer-qa-p11-05-b3ca14953c14.service"
UNIT_FILE = Path("/etc/systemd/system") / UNIT
ARTIFACT_SHA = "37a674d655a6ac4111e488608d81d368364db8d8a13d4293d03e9f9eec8a7b8a"
APP_ID = os.environ.get("NODESTEER_PARAM_QA_APP_ID", "")
STAGE = os.environ.get("NODESTEER_PARAM_QA_STAGE", "")
class ProbeFailure(Exception): pass

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()

def proc(pid, want_argv=False):
    q = Path("/proc") / str(pid)
    raw = (q / "stat").read_text(); close = raw.rfind(")")
    if close < 0: raise ProbeFailure("proc_stat_shape_invalid")
    tail = raw[close + 2:].split()
    uidline = next((x for x in (q / "status").read_text().splitlines() if x.startswith("Uid:")), "")
    if len(tail) < 20 or len(uidline.split()) < 2: raise ProbeFailure("proc_identity_missing")
    exe = os.readlink(q / "exe")
    out = {"pid": int(pid), "ppid": int(tail[1]), "ticks": int(tail[19]), "state": tail[0],
           "uid": int(uidline.split()[1]), "exe_path": exe, "exe_name": Path(exe.removesuffix(" (deleted)")).name,
           "exe_sha": digest(q / "exe"), "cgroup": (q / "cgroup").read_text().strip()}
    if want_argv: out["argv"] = [x.decode("utf-8", "replace") for x in (q / "cmdline").read_bytes().split(b"\0") if x]
    return out

def show_unit(name):
    cp = subprocess.run(["systemctl", "show", "--no-pager",
        "--property=Id,LoadState,ActiveState,SubState,MainPID,ControlGroup,UnitFileState", name],
        capture_output=True, text=True, timeout=6, check=False)
    props = {}
    for line in cp.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1); props[k] = v
    return cp.returncode, props

def guard_agent():
    pid = os.getppid(); agent = None; depth = 0
    for depth in range(1, 33):
        try: p = proc(pid, True)
        except (OSError, ValueError, ProbeFailure): break
        if p["exe_sha"] == AGENT_SHA and p["exe_name"] == "nodesteer-agent":
            agent = p; break
        pid = p["ppid"]
        if pid <= 1: break
    if agent is None or agent["uid"] != 0 or AGENT_UNIT not in agent["cgroup"]:
        raise ProbeFailure("repair_agent_ancestor_guard_failed")
    argv = agent.pop("argv"); config_path = None
    for i, arg in enumerate(argv):
        if arg in ("--config", "-config", "--config-file") and i + 1 < len(argv): config_path = argv[i+1]
        elif arg.startswith("--config="): config_path = arg.split("=", 1)[1]
    disk = agent["exe_path"].removesuffix(" (deleted)"); ds = os.stat(disk)
    if digest(disk) != AGENT_SHA: raise ProbeFailure("repair_agent_disk_sha_mismatch")
    rc, props = show_unit(AGENT_UNIT)
    if (rc != 0 or props.get("Id") != AGENT_UNIT or props.get("ActiveState") != "active"
            or props.get("MainPID") != str(agent["pid"]) or props.get("ControlGroup") not in agent["cgroup"]):
        raise ProbeFailure("repair_agent_systemd_owner_guard_failed")
    agent.update({"disk_path": disk, "disk_sha": AGENT_SHA, "disk_dev": ds.st_dev,
        "disk_inode": ds.st_ino, "config_path_only": config_path, "ancestor_depth": depth})
    return agent

def state_db(agent):
    root = Path("/proc") / str(agent["pid"]) / "fd"; found = {}
    for n in os.listdir(root):
        if not n.isdigit(): continue
        fd = root / n
        try: target, st = os.readlink(fd), os.stat(fd)
        except OSError: continue
        if target.endswith("/state.db") and stat.S_ISREG(st.st_mode): found[(target, st.st_dev, st.st_ino)] = st
    if len(found) != 1: raise ProbeFailure("agent_state_db_fd_missing_or_ambiguous")
    (path, dev, ino), opened = next(iter(found.items())); db = Path(path); st = db.lstat()
    if db.is_symlink() or not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (dev, ino):
        raise ProbeFailure("agent_state_db_path_identity_mismatch")
    uri = "file:" + quote(path, safe="/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=3)
    try:
        con.execute("PRAGMA query_only=ON")
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok": raise ProbeFailure("agent_state_db_quick_check_failed")
        units = con.execute("SELECT application_id,unit_name FROM unit_registry WHERE unit_name=?", (UNIT,)).fetchall()
        apps = con.execute("SELECT id,revision,definition_json FROM applications WHERE id=?", (APP_ID,)).fetchall() if APP_ID else []
    finally: con.close()
    after = db.lstat()
    if db.is_symlink() or (after.st_dev, after.st_ino) != (st.st_dev, st.st_ino): raise ProbeFailure("agent_state_db_changed")
    app_rows = []
    for row in apps:
        d = json.loads(row[2])
        app_rows.append({"id": row[0], "revision": row[1], "name_matches": d.get("name") == APP_NAME,
            "unit_matches": d.get("unit_name") == UNIT, "binary_matches": d.get("binary_path") == str(BINARY)})
    return {"quick_check": "ok", "dev": st.st_dev, "inode": st.st_ino,
        "unit_rows": [{"application_id": x[0], "unit_name": x[1]} for x in units], "app_rows": app_rows}

def file_info(path):
    try: st = path.lstat()
    except FileNotFoundError: return {"exists": False}
    if path.is_symlink() or not stat.S_ISREG(st.st_mode): raise ProbeFailure("owned_file_type_guard_failed")
    return {"exists": True, "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode),
        "size": st.st_size, "sha256": digest(path)}

def dir_info(path):
    try: st = path.lstat()
    except FileNotFoundError: return {"exists": False, "entries": 0}
    if path.is_symlink() or not stat.S_ISDIR(st.st_mode): raise ProbeFailure("owned_directory_type_guard_failed")
    names = sorted(x.name for x in path.iterdir())
    return {"exists": True, "dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid,
        "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode), "entry_count": len(names), "entries": names,
        "names_sha256": hashlib.sha256("\n".join(names).encode()).hexdigest()}

def service_info():
    rc, p = show_unit(UNIT)
    return {"returncode": rc, "id": p.get("Id"), "load_state": p.get("LoadState"),
        "active_state": p.get("ActiveState"), "sub_state": p.get("SubState"),
        "main_pid": int(p["MainPID"]) if p.get("MainPID", "").isdigit() else 0,
        "cgroup": p.get("ControlGroup"), "unit_file_state": p.get("UnitFileState")}

def pids_in_owned_cgroup(cgroup):
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit(): continue
        try: rows = (entry / "cgroup").read_text().splitlines()
        except (OSError, PermissionError, ProcessLookupError): continue
        if any(row.split(":", 2)[-1] == cgroup or row.split(":", 2)[-1].startswith(cgroup + "/") for row in rows):
            found.append(int(entry.name))
    return sorted(found)

def phase_baseline(db, unit, binary, dirs):
    if (APP_ID or db["unit_rows"] or unit["exists"] or binary["exists"]
            or dirs["app_root"]["exists"] or dirs["bin"]["exists"]):
        raise ProbeFailure("baseline_owned_resource_not_absent")
    if (unit.get("load_state") not in (None, "not-found") or unit.get("active_state") == "active"
            or unit.get("main_pid", 0) > 1):
        raise ProbeFailure("baseline_owned_unit_not_absent")
    return {"result": "BASELINE_EMPTY"}

def phase_deployed(db, unit, binary):
    if not APP_ID or len(db["app_rows"]) != 1 or not db["app_rows"][0]["name_matches"] or not db["app_rows"][0]["unit_matches"] or not db["app_rows"][0]["binary_matches"]:
        raise ProbeFailure("deployed_application_row_mismatch")
    if len(db["unit_rows"]) != 1 or db["unit_rows"][0]["application_id"] != APP_ID:
        raise ProbeFailure("deployed_unit_registry_mismatch")
    if not unit["exists"] or unit["uid"] != 0 or unit["mode"] != 0o644:
        raise ProbeFailure("deployed_unit_file_mismatch")
    if not binary["exists"] or binary["uid"] != 0 or binary["mode"] != 0o755 or binary["sha256"] != ARTIFACT_SHA:
        raise ProbeFailure("deployed_binary_sha_or_mode_mismatch")
    rows = [x for x in UNIT_FILE.read_text(encoding="utf-8").splitlines() if x.startswith("ExecStart=")]
    if rows != ['ExecStart="' + str(BINARY) + '"']:
        raise ProbeFailure("deployed_execstart_not_exact_owned_binary")
    if unit.get("id") != UNIT or unit.get("active_state") != "active" or unit.get("unit_file_state") != "enabled" or unit["main_pid"] <= 1:
        raise ProbeFailure("deployed_unit_not_active_enabled")
    p = proc(unit["main_pid"], True)
    if (p["uid"] != 0 or p["exe_name"] != "sleep" or p["argv"] != ["/bin/sleep", "3600"]
            or unit.get("cgroup") not in p["cgroup"]):
        raise ProbeFailure("deployed_process_not_allowlisted")
    p2 = proc(unit["main_pid"], True)
    if p["ticks"] != p2["ticks"]: raise ProbeFailure("deployed_process_identity_changed")
    return {"result": "DEPLOYED_UNIT_PROCESS_VERIFIED", "process": {"pid": p["pid"],
        "start_ticks": p["ticks"], "uid": p["uid"], "exe_basename": p["exe_name"],
        "proc_exe_sha256": p["exe_sha"], "argv_allowlisted": True, "argv_summary": ["sleep", "3600"],
        "cgroup_matches_unit": True}, "execstart_matches": True}

def phase_cleanup(db, unit, binary, dirs):
    if not APP_ID or db["unit_rows"] or db["app_rows"]: raise ProbeFailure("cleanup_local_rows_remain")
    unit_cgroup = "/system.slice/" + UNIT
    owned_pids = pids_in_owned_cgroup(unit_cgroup)
    if (unit["exists"] or binary["exists"] or unit.get("active_state") == "active"
            or unit.get("load_state") not in (None, "not-found") or unit.get("main_pid", 0) > 1
            or owned_pids):
        raise ProbeFailure("cleanup_owned_unit_or_files_remain")
    app_root, bin_dir = dirs["app_root"], dirs["bin"]
    if app_root["exists"] != bin_dir["exists"]:
        raise ProbeFailure("cleanup_fixture_directory_pair_mismatch")
    if app_root["exists"] and (app_root["entries"] != ["bin"] or bin_dir["entry_count"] != 0):
        raise ProbeFailure("cleanup_retained_directory_contents_mismatch")
    retained = []
    if app_root["exists"]:
        for path, info in ((str(APP_ROOT), app_root), (str(APP_ROOT / "bin"), bin_dir)):
            retained.append({"path": path, **{k: info[k] for k in
                ("dev", "inode", "uid", "gid", "mode", "entry_count")}})
    return {"result": "PRUNE_READBACK_FILES_AND_RUNTIME_ABSENT",
        "unit_cgroup": unit_cgroup, "unit_cgroup_pids": owned_pids,
        "retained_empty_fixture_directories": retained,
        "directory_cleanup_claim": "owned binary, Unit, registry and process absent; listed empty directories intentionally retained"}

def main():
    if STAGE not in ("baseline", "deployed", "cleanup"): raise ProbeFailure("stage_parameter_invalid")
    if APP_ID and not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", APP_ID):
        raise ProbeFailure("application_id_shape_invalid")
    before = guard_agent(); db = state_db(before); unit_file = file_info(UNIT_FILE); service = service_info()
    unit = {**unit_file, **service}; binary = file_info(BINARY)
    dirs = {"app_root": dir_info(APP_ROOT), "bin": dir_info(APP_ROOT / "bin")}
    if STAGE == "baseline": check = phase_baseline(db, unit, binary, dirs)
    elif STAGE == "deployed": check = phase_deployed(db, unit, binary)
    else: check = phase_cleanup(db, unit, binary, dirs)
    after = guard_agent()
    if (before["pid"], before["ticks"], before["exe_sha"], before["cgroup"]) != (after["pid"], after["ticks"], after["exe_sha"], after["cgroup"]):
        raise ProbeFailure("repair_agent_identity_changed_during_read")
    out = {"stage": STAGE, "node_id": NODE_ID, "agent_id": AGENT_ID,
        "agent_owner": {k: before[k] for k in ("pid", "ticks", "uid", "exe_sha", "disk_sha", "cgroup", "config_path_only", "ancestor_depth")},
        "database": db, "unit": {"name": UNIT, **unit}, "binary_path": str(BINARY),
        "binary": binary, "owned_directories": dirs, **check, "credentials_emitted": False}
    print(json.dumps(out, sort_keys=True, separators=(",", ":")), flush=True)

try: main()
except ProbeFailure as exc:
    print(json.dumps({"status": "READ_ONLY_GUARD_FAILED", "reason": str(exc), "stage": STAGE,
        "node_id": NODE_ID, "agent_id": AGENT_ID, "credentials_emitted": False}, separators=(",", ":")), flush=True)
    raise SystemExit(2)
except Exception as exc:
    print(json.dumps({"status": "READ_ONLY_PROBE_FAILED", "reason_type": type(exc).__name__, "stage": STAGE,
        "node_id": NODE_ID, "agent_id": AGENT_ID, "credentials_emitted": False}, separators=(",", ":")), flush=True)
    raise SystemExit(2)
