#!/usr/bin/env python3
"""Native3 readonly command payload. Emits only allowlisted owner and marker facts."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

NODE_ID = "37989297-3bc8-4c0c-b302-7c6efbdba630"
AGENT_ID = "agent-bb005487-7d84-418c-b598-f93d8b0c86a0"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
UNIT = "nodesteer-agent-repair-20260930-101945-native-3.service"
CGROUP = "/system.slice/" + UNIT
CONFIG = "/etc/nodesteer/repair-20260930-101945/native-3.yaml"
STATE = Path("/tmp/nodesteer-qa-sys34-40-b7e13d4c9a21.state")


class GuardError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def proc_record(pid):
    p = Path("/proc") / str(pid)
    raw_stat = (p / "stat").read_text(encoding="ascii")
    tail = raw_stat[raw_stat.rfind(")") + 2:].split()
    if len(tail) < 20:
        raise GuardError("proc_stat_shape_invalid")
    uid_line = next((x for x in (p / "status").read_text(encoding="ascii").splitlines()
                     if x.startswith("Uid:")), None)
    if uid_line is None:
        raise GuardError("proc_uid_missing")
    uid = int(uid_line.split()[1])
    exe = os.readlink(p / "exe")
    argv = [x.decode("utf-8", "replace") for x in (p / "cmdline").read_bytes().split(b"\0") if x]
    return {
        "pid": pid,
        "ppid": int(tail[1]),
        "startticks": int(tail[19]),
        "uid": uid,
        "exe_basename": Path(exe.removesuffix(" (deleted)")).name,
        "exe_path": exe,
        "proc_exe_sha256": digest(p / "exe"),
        "argv": argv,
        "cgroup_lines": (p / "cgroup").read_text(encoding="ascii").splitlines(),
    }


def has_exact_config_argv(argv):
    paths = []
    for i, value in enumerate(argv):
        if value in ("--config", "-config", "--config-file") and i + 1 < len(argv):
            paths.append(argv[i + 1])
        elif value.startswith("--config="):
            paths.append(value.split("=", 1)[1])
    return paths == [CONFIG]


def find_agent_ancestor():
    candidates = []
    cur = os.getppid()
    for _ in range(32):
        if cur <= 1:
            break
        try:
            p = proc_record(cur)
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, OSError):
            break
        if (p["exe_basename"] == "nodesteer-agent" and p["uid"] == 0
                and p["proc_exe_sha256"] == AGENT_SHA and f"0::{CGROUP}" in p["cgroup_lines"]
                and has_exact_config_argv(p["argv"])):
            candidates.append(p)
        cur = p["ppid"]
    if len(candidates) != 1:
        raise GuardError("unique_exact_native3_agent_ancestor_not_found")
    agent = candidates[0]
    disk_path = agent["exe_path"].removesuffix(" (deleted)")
    if " (deleted)" in agent["exe_path"] or not os.path.isfile(disk_path):
        raise GuardError("agent_disk_binary_unavailable")
    disk_sha = digest(disk_path)
    if disk_sha != AGENT_SHA:
        raise GuardError("agent_disk_sha_mismatch")
    agent["disk_sha256"] = disk_sha
    return agent


def verify_unit(agent):
    env = {"PATH": "/usr/bin:/bin", "LANG": "C"}
    try:
        p = subprocess.run(["systemctl", "show", UNIT, "--no-pager",
                            "--property=Id,ActiveState,MainPID,ControlGroup"],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=5, check=False, text=True)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise GuardError("native3_unit_readback_unavailable")
    props = {}
    for line in p.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    if (p.returncode != 0 or props.get("Id") != UNIT or props.get("ActiveState") != "active"
            or props.get("MainPID", "") != str(agent["pid"])
            or props.get("ControlGroup") != CGROUP):
        raise GuardError("native3_unit_owner_or_active_guard_failed")
    return True


def marker_absent():
    try:
        STATE.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        raise GuardError("marker_lstat_failed")
    # Never inspect contents or remove a present/unknown file in this verifier.
    return False


def run():
    if not shutil.which("python3"):
        raise GuardError("python3_unavailable")
    agent = find_agent_ancestor()
    unit_ok = verify_unit(agent)
    absent = marker_absent()
    if not absent:
        raise GuardError("exact_marker_path_not_absent")
    return {
        "status": "READONLY_PREFLIGHT_OR_POSTCHECK_PASS",
        "node_id": NODE_ID,
        "agent_id": AGENT_ID,
        "agent_pid": agent["pid"],
        "agent_startticks": agent["startticks"],
        "agent_uid": agent["uid"],
        "proc_exe_sha256": agent["proc_exe_sha256"],
        "disk_exe_sha256": agent["disk_sha256"],
        "config_argv_exact_path_match": True,
        "unit_exact_active_mainpid_match": unit_ok,
        "python3_available": True,
        "exact_marker_path_absent": True,
        "config_contents_read": False,
        "raw_argv_or_environment_emitted": False,
    }


def main():
    try:
        result = run()
    except GuardError as exc:
        result = {"status": "READONLY_GUARD_FAILED", "reason_code": exc.code,
                  "node_id": NODE_ID, "agent_id": AGENT_ID,
                  "config_contents_read": False, "raw_argv_or_environment_emitted": False}
    except Exception:
        result = {"status": "READONLY_GUARD_FAILED", "reason_code": "unexpected_readonly_probe_error",
                  "node_id": NODE_ID, "agent_id": AGENT_ID,
                  "config_contents_read": False, "raw_argv_or_environment_emitted": False}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0 if result["status"] == "READONLY_PREFLIGHT_OR_POSTCHECK_PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
