#!/usr/bin/env python3
"""Native2 helper for an un-deployed Application ArtifactCache lifecycle; --execute required."""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import stat
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

IMAGE = "sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44"
IMAGE_OWNER = "qa-resume-20261010-host-f174890c3846"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
SHELL_SHA = "a6f559e00b69a4aa4d8cb607be18d9386c5aee55c509e2c075549dcf00e00fc7"
ORIGINAL_ROOT = Path("/opt/qa-resume-20261010-host-f174890c3846")
ORIGINAL_CONFIG = ORIGINAL_ROOT / "private/host001-agent.yaml"
REPAIR_IP = "192.168.100.209"
REPAIR_PORT = 18080
STATE = {"root": None, "network_id": None, "container_ids": []}


class SafeStop(Exception):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def docker(argv, timeout=30):
    p = subprocess.run(["docker", *argv], text=True, capture_output=True,
                       timeout=timeout, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def atomic_json(path, value, uid=0, gid=0, mode=0o600):
    tmp = path.with_name(path.name + ".tmp-" + os.urandom(5).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", closefd=False) as f:
            json.dump(value, f, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.close(fd)
    os.replace(tmp, path)


def owned_json(path, limit, uid, gid, mode):
    p = Path(path)
    st = p.lstat()
    if (not stat.S_ISREG(st.st_mode) or p.is_symlink() or p.resolve() != p
            or (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)) != (uid, gid, mode)
            or st.st_size > limit):
        raise SafeStop("owned_file_guard_failed")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino, fst.st_uid, fst.st_gid, stat.S_IMODE(fst.st_mode)) != (
                st.st_dev, st.st_ino, uid, gid, mode):
            raise SafeStop("owned_file_identity_changed")
        raw = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise SafeStop("owned_file_size_limit")
    return st, raw


def private_registration_key():
    root_st = ORIGINAL_ROOT.lstat()
    if (not stat.S_ISDIR(root_st.st_mode) or ORIGINAL_ROOT.is_symlink()
            or (root_st.st_dev, root_st.st_ino, root_st.st_uid, stat.S_IMODE(root_st.st_mode))
            != (65025, 70286, 0, 0o700)):
        raise SafeStop("registration_source_root_guard_failed")
    private = ORIGINAL_CONFIG.parent
    private_st = private.lstat()
    if (not stat.S_ISDIR(private_st.st_mode) or private.is_symlink()
            or private.resolve() != ORIGINAL_ROOT / "private"
            or private_st.st_uid != 0 or stat.S_IMODE(private_st.st_mode) != 0o700):
        raise SafeStop("registration_source_private_guard_failed")
    _, raw = owned_json(ORIGINAL_CONFIG, 1024 * 1024, 0, 0, 0o600)
    obj = {}
    try:
        for line in raw.decode("utf-8").splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() in {"hub_url", "registration_token"}:
                key = key.strip()
                if key in obj:
                    raise SafeStop("registration_source_duplicate_key")
                obj[key] = json.loads(value.strip())
    except (UnicodeDecodeError, ValueError):
        raise SafeStop("registration_source_parse_failed")
    if set(obj) != {"hub_url", "registration_token"} or not all(isinstance(v, str) and v for v in obj.values()):
        raise SafeStop("registration_source_required_fields_missing")
    u = urlsplit(obj["hub_url"])
    if (u.scheme not in {"http", "ws"} or u.hostname != REPAIR_IP or u.port != REPAIR_PORT
            or u.username or u.password or u.query or u.fragment or u.path not in {"", "/"}):
        raise SafeStop("repair_hub_url_guard_failed")
    return urlunsplit(("ws", f"{REPAIR_IP}:{REPAIR_PORT}", "/", "", "")), obj["registration_token"]


def resource_names(nonce):
    owner = "qa-resume-20261012-artcache-" + nonce
    return {"owner": owner, "root": Path("/opt") / owner,
            "node": "qa-artcache-" + nonce, "network": "nodesteer-qa-artcache-" + nonce,
            "a": owner + "-a", "b": owner + "-b"}


def exact_absent(kind, name):
    rc, _, err = docker([kind, "inspect", name])
    if rc == 0:
        raise SafeStop("exact_resource_name_collision")
    low = err.lower()
    exact = (("no such " + kind + ": " + name.lower()) in low
             or ("no such object: " + name.lower()) in low
             or (kind + " " + name.lower() + " not found") in low
             or (kind + " " + name.lower() + ": not found") in low)
    if not exact:
        raise SafeStop("exact_resource_name_unknown")


def inspect_image():
    rc, out, _ = docker(["image", "inspect", IMAGE])
    if rc:
        raise SafeStop("pinned_image_inspect_failed")
    rows = json.loads(out)
    if not isinstance(rows, list) or len(rows) != 1:
        raise SafeStop("pinned_image_cardinality_invalid")
    row = rows[0]
    labels = ((row.get("Config") or {}).get("Labels") or {})
    if row.get("Id") != IMAGE or labels.get("nodesteer.e2e.owner") != IMAGE_OWNER:
        raise SafeStop("pinned_image_identity_mismatch")


def root_paths(n):
    private = n["root"] / "private"
    data = n["root"] / "data"
    return private, private / "agent.yaml", data


def read_owner(n):
    root = n["root"]
    rs = root.lstat()
    if (not stat.S_ISDIR(rs.st_mode) or root.is_symlink() or root.resolve() != root
            or rs.st_uid != 0 or stat.S_IMODE(rs.st_mode) != 0o700):
        raise SafeStop("owned_root_guard_failed")
    _, marker_raw = owned_json(root / "owner.json", 65536, 0, 0, 0o600)
    marker = json.loads(marker_raw)
    if (marker.get("owner") != n["owner"] or marker.get("nonce") != n["nonce"]
            or marker.get("root") != str(root) or marker.get("root_dev") != rs.st_dev
            or marker.get("root_inode") != rs.st_ino or marker.get("image_id") != IMAGE
            or marker.get("network_name") != n["network"]):
        raise SafeStop("owned_marker_identity_mismatch")
    _, ledger_raw = owned_json(root / "resource-ledger.json", 131072, 0, 0, 0o600)
    ledger = json.loads(ledger_raw)
    if (ledger.get("owner") != n["owner"] or ledger.get("nonce") != n["nonce"]
            or ledger.get("root")!=str(root) or ledger.get("root_dev")!=rs.st_dev
            or ledger.get("root_inode")!=rs.st_ino or ledger.get("image_id")!=IMAGE
            or ledger.get("network_name")!=n["network"]):
        raise SafeStop("owned_ledger_identity_mismatch")
    private, config, data = root_paths(n)
    for path, uid, gid, mode, isdir in ((private, 65534, 65534, 0o700, True),
                                        (data, 65534, 65534, 0o700, True),
                                        (config, 65534, 65534, 0o600, False)):
        st = path.lstat()
        if (path.is_symlink() or path.resolve() != path
                or (not stat.S_ISDIR(st.st_mode) if isdir else not stat.S_ISREG(st.st_mode))
                or (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)) != (uid, gid, mode)):
            raise SafeStop("owned_private_data_path_guard_failed")
    return marker, ledger, root, private, config, data


def atomic_owner(root, marker, ledger):
    atomic_json(root / "owner.json", marker)
    atomic_json(root / "resource-ledger.json", ledger)


def config_bytes(node_name):
    ws_url, token = private_registration_key()
    return ("hub_url: " + json.dumps(ws_url) + "\n"
            + "registration_token: " + json.dumps(token) + "\n"
            + "node_name: " + json.dumps(node_name) + "\n"
            + "deployment_mode: \"docker\"\n"
            + "data_dir: \"/var/lib/nodesteer\"\n").encode()


def create_root_and_bridge(n):
    if os.geteuid() != 0 or n["root"].exists() or n["root"].is_symlink():
        raise SafeStop("owned_root_unique_or_privilege_guard_failed")
    if not Path("/opt").is_dir() or Path("/opt").is_symlink():
        raise SafeStop("owned_root_parent_guard_failed")
    exact_absent("container", n["a"])
    exact_absent("container", n["b"])
    exact_absent("network", n["network"])
    inspect_image()
    content = config_bytes(n["node"])
    root = n["root"]
    root.mkdir(mode=0o700)
    os.chown(root, 0, 0)
    os.chmod(root, 0o700)
    private, config, data = root_paths(n)
    private.mkdir(mode=0o700)
    data.mkdir(mode=0o700)
    for path in (private, data):
        os.chown(path, 65534, 65534)
        os.chmod(path, 0o700)
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchown(fd, 65534, 65534)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.close(fd)
    rs = root.lstat()
    marker = {"owner": n["owner"], "nonce": n["nonce"], "root": str(root),
              "root_dev": rs.st_dev, "root_inode": rs.st_ino, "image_id": IMAGE,
              "network_name": n["network"], "node_name": n["node"],
              "container_a_name": n["a"], "container_b_name": n["b"],
              "container_a_id": None, "container_b_id": None, "network_id": None,
              "network_removed_id": None,"children_removed":False,
              "artifact_sha256": n["artifact_sha256"]}
    ledger = {"owner": n["owner"], "nonce": n["nonce"], "root":str(root),
              "root_dev":rs.st_dev,"root_inode":rs.st_ino,"image_id":IMAGE,
              "network_name":n["network"],"phase": "ROOT_CREATED",
              "container_ids": [], "secret_values_written": False, "network_id": None}
    atomic_owner(root, marker, ledger)
    rc, out, _ = docker(["network", "create", "--driver", "bridge", "--label",
                         "nodesteer.e2e.owner=" + n["owner"], n["network"]])
    netid = out.strip()
    if rc or not re.fullmatch(r"[0-9a-f]{64}", netid):
        raise SafeStop("owned_network_create_failed")
    rc, out, _ = docker(["network", "inspect", netid])
    try:
        rows = json.loads(out) if not rc else None
    except ValueError:
        rows = None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SafeStop("owned_network_inspect_shape_invalid")
    row = rows[0]
    if (row.get("Id") != netid or row.get("Name") != n["network"] or row.get("Driver") != "bridge"
            or row.get("Scope") != "local" or row.get("Internal") is not False
            or row.get("Attachable") is not False
            or (row.get("Labels") or {}).get("nodesteer.e2e.owner") != n["owner"]):
        raise SafeStop("owned_network_inspect_identity_mismatch")
    marker["network_id"] = ledger["network_id"] = netid
    ledger["phase"] = "NETWORK_CREATED"
    atomic_owner(root, marker, ledger)
    return marker, ledger, root, private, config, data


def inspect_container(n, cid, cname, config, data):
    rc, out, _ = docker(["container", "inspect", cid])
    if rc:
        raise SafeStop("owned_container_inspect_failed")
    rows = json.loads(out)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SafeStop("owned_container_inspect_cardinality")
    row = rows[0]
    labels = (row.get("Config") or {}).get("Labels") or {}
    hc = row.get("HostConfig") or {}
    state = row.get("State") or {}
    mounts = row.get("Mounts") or []
    binds = [m for m in mounts if m.get("Type") == "bind"]
    nonbind = [m for m in mounts if m.get("Type") != "bind"]
    bydest = {m.get("Destination"): m for m in binds}
    cfg = bydest.get("/run/nodesteer/agent.yaml") or {}
    dat = bydest.get("/var/lib/nodesteer") or {}
    sec = [str(x).lower() for x in (hc.get("SecurityOpt") or [])]
    nnp = any(x.replace("=", ":").startswith("no-new-privileges") for x in sec)
    default_seccomp = not any(x.startswith("seccomp") or "unconfined" in x for x in sec)
    pidmode = hc.get("PidMode") or ""
    tmpfs = hc.get("Tmpfs") or {}
    tmpfsok = set(tmpfs) == {"/tmp"} and all(x in str(tmpfs.get("/tmp", "")).lower()
                                               for x in ("noexec", "nosuid", "nodev", "size=16m"))
    if (row.get("Id") != cid or row.get("Name") != "/" + cname or row.get("Image") != IMAGE
            or labels.get("nodesteer.e2e.owner") != n["owner"]
            or (row.get("Config") or {}).get("User") != "65534:65534"
            or hc.get("Privileged") is not False or hc.get("ReadonlyRootfs") is not True
            or hc.get("CapAdd") or "ALL" not in [str(x).upper() for x in hc.get("CapDrop", [])]
            or pidmode not in {"", "private"} or not nnp or not default_seccomp or not tmpfsok
            or hc.get("NetworkMode") != n["network"] or hc.get("PortBindings") or hc.get("Devices")
            or len(binds) != 2 or cfg.get("Source") != str(config) or cfg.get("RW") is not False
            or cfg.get("Destination") != "/run/nodesteer/agent.yaml"
            or dat.get("Source") != str(data) or dat.get("RW") is not True
            or dat.get("Destination") != "/var/lib/nodesteer"
            or any(m.get("Type") != "tmpfs" or m.get("Destination") != "/tmp" for m in nonbind)):
        raise SafeStop("container_identity_policy_or_mount_guard_failed")
    netrows = ((row.get("NetworkSettings") or {}).get("Networks") or {})
    if set(netrows) != {n["network"]}:
        raise SafeStop("container_network_membership_mismatch")
    net = netrows[n["network"]]
    running = state.get("Running")
    ip = net.get("IPAddress")
    if not isinstance(running, bool):
        raise SafeStop("container_running_state_invalid")
    if running or ip not in (None, ""):
        try:
            ipaddress.ip_address(ip)
        except (ValueError, TypeError):
            raise SafeStop("container_dynamic_ip_invalid")
    return {"container_id": cid, "name": cname, "running": running,
            "pid": state.get("Pid") if isinstance(state.get("Pid"), int) else None,
            "state_status": state.get("Status"), "assigned_ip": ip,
            "network_membership_exact": True}


def container_argv(n, cname, config, data):
    return ["run", "--detach", "--name", cname, "--label", "nodesteer.e2e.owner=" + n["owner"],
            "--user", "65534:65534", "--cap-drop", "ALL", "--read-only",
            "--security-opt", "no-new-privileges", "--network", n["network"],
            "--mount", "type=bind,src=" + str(config) + ",dst=/run/nodesteer/agent.yaml,readonly",
            "--mount", "type=bind,src=" + str(data) + ",dst=/var/lib/nodesteer",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m", IMAGE]


def create_container(n, cname, config, data, marker, ledger):
    field = "container_a_id" if cname == n["a"] else "container_b_id"
    rc, out, _ = docker(container_argv(n, cname, config, data), timeout=35)
    cid = out.strip()
    if rc or not re.fullmatch(r"[0-9a-f]{64}", cid):
        rc2, inspect_out, _ = docker(["container", "inspect", cname])
        try:
            rows = json.loads(inspect_out) if not rc2 else None
        except ValueError:
            rows = None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SafeStop("exact_container_create_recovery_unknown")
        cid = rows[0].get("Id")
        if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise SafeStop("exact_container_create_recovery_id_invalid")
    info = inspect_container(n, cid, cname, config, data)
    if not info.get("running") or not info.get("pid"):
        raise SafeStop("owned_container_not_running_after_create")
    marker[field] = cid
    marker.setdefault("created_container_ids", []).append(cid)
    ledger.setdefault("container_ids", []).append(cid)
    ledger["phase"] = "CONTAINER_CREATED"
    atomic_owner(n["root"], marker, ledger)
    return cid


def proc_snapshot(pid, cid, dbdev, dbino):
    proc = Path("/proc") / str(pid)
    try:
        statraw = (proc / "stat").read_text()
        close = statraw.rfind(")")
        tail = statraw[close + 1:].split()
        if close < 0 or len(tail) < 20 or tail[0] == "Z":
            raise SafeStop("agent_process_stat_or_state_invalid")
        ticks = tail[19]
        uid = None
        for line in (proc / "status").read_text().splitlines():
            if line.startswith("Uid:"):
                fields = line.split()
                uid = int(fields[1]) if len(fields) > 1 else None
                break
        if uid != 65534 or cid.lower() not in (proc / "cgroup").read_text().lower():
            raise SafeStop("agent_process_uid_or_cgroup_mismatch")
        h = hashlib.sha256()
        fd = os.open(proc / "exe", os.O_RDONLY)
        try:
            for block in iter(lambda: os.read(fd, 1024 * 1024), b""):
                h.update(block)
        finally:
            os.close(fd)
        if h.hexdigest() != AGENT_SHA:
            raise SafeStop("agent_process_executable_sha_mismatch")
        inroot = os.stat(proc / "root/var/lib/nodesteer/state.db")
        if (inroot.st_dev, inroot.st_ino) != (dbdev, dbino):
            raise SafeStop("agent_db_root_identity_mismatch")
        fdcnt = 0
        for name in os.listdir(proc / "fd"):
            if name.isdigit():
                try:
                    st = os.stat(proc / "fd" / name)
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    continue
                if (st.st_dev, st.st_ino) == (dbdev, dbino):
                    fdcnt += 1
        if fdcnt < 1:
            raise SafeStop("agent_db_fd_not_open")
        return {"pid": pid, "start_ticks": ticks, "uid": uid, "exe_sha256": h.hexdigest(),
                "container_id": cid, "container_db_dev": inroot.st_dev,
                "container_db_inode": inroot.st_ino, "matching_db_fd_count": fdcnt}
    except SafeStop:
        raise
    except (OSError, ValueError, IndexError):
        raise SafeStop("agent_proc_readback_unavailable")


def read_cache(n, data, app_id, artifact_id, artifact_sha, unit_name, expected_node, expected_agent, pid):
    cid=n.get("active_container_id", "")
    db = data / "state.db"
    ds = data.lstat()
    if (not stat.S_ISDIR(ds.st_mode) or data.is_symlink() or ds.st_uid != 65534
            or stat.S_IMODE(ds.st_mode) != 0o700):
        raise SafeStop("data_directory_guard_failed")
    st = db.lstat()
    if (not stat.S_ISREG(st.st_mode) or db.is_symlink() or st.st_uid != 65534
            or st.st_dev != ds.st_dev or db.resolve() != data.resolve() / "state.db"):
        raise SafeStop("state_db_path_guard_failed")
    process_before = proc_snapshot(pid, cid, st.st_dev, st.st_ino)
    fd = os.open(db, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    con = None
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino, fst.st_uid) != (st.st_dev, st.st_ino, 65534):
            raise SafeStop("state_db_open_identity_changed")
        con = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True, timeout=3)
        con.execute("PRAGMA query_only=ON")
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            raise SafeStop("state_db_quick_check_failed")
        ident = dict(con.execute("SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')"))
        revision = con.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0]
        approw = con.execute("SELECT revision,definition_json FROM applications WHERE id=?", (app_id,)).fetchone()
        if not approw:
            raise SafeStop("assigned_application_not_persisted")
        app = json.loads(approw[1])
        if app.get("artifact_id") != artifact_id:
            raise SafeStop("local_app_artifact_identity_mismatch")
        cache = con.execute("SELECT path FROM artifacts WHERE sha256=?", (artifact_sha,)).fetchone()
        if not cache:
            raise SafeStop("artifact_cache_db_row_missing")
        count = con.execute("SELECT COUNT(*) FROM executions WHERE task_id=?", (app_id,)).fetchone()[0]
        if count != 0:
            raise SafeStop("unexpected_app_deploy_execution")
    finally:
        if con is not None:
            con.close()
        opened_after = os.fstat(fd)
        path_after = db.lstat()
        os.close(fd)
    if (opened_after.st_dev, opened_after.st_ino) != (st.st_dev, st.st_ino) or (
            path_after.st_dev, path_after.st_ino) != (st.st_dev, st.st_ino):
        raise SafeStop("state_db_changed_during_read")
    if ident.get("node_id") != expected_node or ident.get("agent_id") != expected_agent:
        raise SafeStop("local_agent_identity_mismatch")
    expected_rel = "/var/lib/nodesteer/artifacts/" + artifact_sha
    if cache[0] != expected_rel:
        raise SafeStop("artifact_cache_db_path_mismatch")
    after_db = db.lstat()
    if (after_db.st_dev, after_db.st_ino) != (st.st_dev, st.st_ino):
        raise SafeStop("stopped_state_db_changed_during_read")
    artifact_dir=data/"artifacts"
    try: ads=artifact_dir.lstat()
    except FileNotFoundError: raise SafeStop("artifact_cache_file_not_yet_present")
    if (artifact_dir.is_symlink() or not stat.S_ISDIR(ads.st_mode) or (ads.st_uid,ads.st_gid)!=(65534,65534)
            or ads.st_dev!=ds.st_dev or artifact_dir.resolve()!=data.resolve()/"artifacts"):
        raise SafeStop("artifact_cache_directory_guard_failed")
    target = artifact_dir / artifact_sha
    try:
        fs = target.lstat()
    except FileNotFoundError:
        raise SafeStop("artifact_cache_file_not_yet_present")
    if (not stat.S_ISREG(fs.st_mode) or target.is_symlink() or fs.st_uid != 65534
            or fs.st_gid != 65534 or fs.st_size <= 0):
        raise SafeStop("artifact_cache_file_guard_failed")
    afd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    ah = hashlib.sha256()
    size = 0
    try:
        fst = os.fstat(afd)
        if (fst.st_dev, fst.st_ino, fst.st_uid, fst.st_gid) != (fs.st_dev, fs.st_ino, 65534, 65534):
            raise SafeStop("artifact_cache_file_identity_changed")
        for block in iter(lambda: os.read(afd, 1024 * 1024), b""):
            size += len(block)
            ah.update(block)
        after = os.fstat(afd)
    finally:
        os.close(afd)
    path_after = target.lstat()
    if (after.st_dev, after.st_ino, after.st_size) != (fs.st_dev, fs.st_ino, fs.st_size) or (
            path_after.st_dev, path_after.st_ino) != (fs.st_dev, fs.st_ino):
        raise SafeStop("artifact_cache_file_changed_during_read")
    if size != 32 or ah.hexdigest() != artifact_sha:
        raise SafeStop("artifact_cache_size_or_sha_mismatch")
    process = proc_snapshot(pid, cid, st.st_dev, st.st_ino)
    if process["start_ticks"] != process_before["start_ticks"] or process["exe_sha256"] != process_before["exe_sha256"]:
        raise SafeStop("agent_process_changed_during_cache_read")
    unit = Path("/proc") / str(pid) / "root/etc/systemd/system" / unit_name
    unit_present = unit.exists() or unit.is_symlink()
    if unit_present:
        raise SafeStop("unexpected_managed_unit_present")
    return {"status": "ok", "quick_check": quick, "db_dev": st.st_dev, "db_inode": st.st_ino,
            "global_revision": revision, "identity": ident,
            "application": {"id": app_id, "revision": approw[0], "artifact_id": app.get("artifact_id")},
            "artifact_cache": {"sha256": artifact_sha, "path_relative": "artifacts/" + artifact_sha,
                               "path_db": cache[0], "size_bytes": size, "file_dev": fs.st_dev,
                               "file_inode": fs.st_ino, "file_uid": fs.st_uid, "file_gid": fs.st_gid},
            "app_deploy_execution_count": int(count), "unit_file_present_in_container": False,
            "process_db_open_proof": process}




def read_cache_snapshot(n, data, app_id, artifact_id, artifact_sha, unit_name,
                        expected_node, expected_agent, pid=None, cid=None):
    if pid is not None and cid:
        n["active_container_id"] = cid
        return read_cache(n, data, app_id, artifact_id, artifact_sha, unit_name,
                          expected_node, expected_agent, pid)
    # Stopped-A snapshot: query-only DB and physical file oracles, no process claim.
    db = data / "state.db"
    ds=data.lstat()
    if data.is_symlink() or not stat.S_ISDIR(ds.st_mode) or (ds.st_uid,ds.st_gid,stat.S_IMODE(ds.st_mode))!=(65534,65534,0o700):
        raise SafeStop("data_directory_guard_failed")
    st = db.lstat()
    if not stat.S_ISREG(st.st_mode) or db.is_symlink() or st.st_uid != 65534 or db.resolve()!=data.resolve()/"state.db":
        raise SafeStop("state_db_path_guard_failed")
    dbfd=os.open(db,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    fst=os.fstat(dbfd)
    if (fst.st_dev,fst.st_ino,fst.st_uid)!=(st.st_dev,st.st_ino,65534):
        os.close(dbfd); raise SafeStop("state_db_open_identity_changed")
    con = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True, timeout=3)
    try:
        con.execute("PRAGMA query_only=ON")
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            raise SafeStop("state_db_quick_check_failed")
        ident = dict(con.execute("SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')"))
        revision = con.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0]
        row = con.execute("SELECT revision,definition_json FROM applications WHERE id=?", (app_id,)).fetchone()
        cache = con.execute("SELECT path FROM artifacts WHERE sha256=?", (artifact_sha,)).fetchone()
        executions = con.execute("SELECT COUNT(*) FROM executions WHERE task_id=?", (app_id,)).fetchone()[0]
    finally:
        con.close()
        db_after_fd=os.fstat(dbfd); os.close(dbfd)
    db_after_path=db.lstat()
    if ((db_after_fd.st_dev,db_after_fd.st_ino)!=(st.st_dev,st.st_ino)
            or (db_after_path.st_dev,db_after_path.st_ino)!=(st.st_dev,st.st_ino)):
        raise SafeStop("stopped_state_db_changed_during_read")
    if (not row or json.loads(row[1]).get("artifact_id") != artifact_id or not cache
            or executions != 0 or ident.get("node_id") != expected_node
            or ident.get("agent_id") != expected_agent):
        raise SafeStop("stopped_cache_db_or_identity_mismatch")
    artifact_dir=data/"artifacts"
    ads=artifact_dir.lstat()
    if (artifact_dir.is_symlink() or not stat.S_ISDIR(ads.st_mode) or (ads.st_uid,ads.st_gid)!=(65534,65534)
            or ads.st_dev!=ds.st_dev or artifact_dir.resolve()!=data.resolve()/"artifacts"):
        raise SafeStop("stopped_artifact_cache_directory_guard_failed")
    target = artifact_dir / artifact_sha
    fs = target.lstat()
    if (target.resolve()!=artifact_dir.resolve()/artifact_sha or target.is_symlink() or not stat.S_ISREG(fs.st_mode) or (fs.st_uid, fs.st_gid) != (65534,65534)
            or fs.st_size != 32 or cache[0] != "/var/lib/nodesteer/artifacts/" + artifact_sha):
        raise SafeStop("stopped_cache_file_guard_failed")
    afd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fst = os.fstat(afd)
        if (fst.st_dev, fst.st_ino, fst.st_uid, fst.st_gid) != (fs.st_dev, fs.st_ino, 65534, 65534):
            raise SafeStop("stopped_cache_file_identity_changed")
        raw = os.read(afd, 33)
        after_file = os.fstat(afd)
    finally:
        os.close(afd)
    after_path = target.lstat()
    if (after_file.st_dev, after_file.st_ino, after_path.st_dev, after_path.st_ino) != (fs.st_dev, fs.st_ino, fs.st_dev, fs.st_ino):
        raise SafeStop("stopped_cache_file_changed_during_read")
    if len(raw) != 32 or digest(raw) != artifact_sha:
        raise SafeStop("stopped_cache_sha_mismatch")
    return {"status":"ok","quick_check":quick,"db_dev":st.st_dev,"db_inode":st.st_ino,
            "global_revision":revision,"identity":ident,
            "application":{"id":app_id,"revision":row[0],"artifact_id":artifact_id},
            "artifact_cache":{"sha256":artifact_sha,"path_relative":"artifacts/"+artifact_sha,
                              "path_db":cache[0],"size_bytes":len(raw),
                              "file_dev":fs.st_dev,"file_inode":fs.st_ino,"file_uid":fs.st_uid,
                              "file_gid":fs.st_gid},"app_deploy_execution_count":int(executions),
            "process_db_open_proof":None,"unit_file_present_in_container":None}


def output(value):
    value.setdefault("credentials_emitted", False)
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def load_state(nonce):
    n = resource_names(nonce)
    n["nonce"] = nonce
    marker, ledger, root, private, config, data = read_owner(n)
    return n, marker, ledger, root, private, config, data


def phase_prepare(nonce, artifact_sha):
    n = resource_names(nonce)
    n.update(nonce=nonce, artifact_sha256=artifact_sha)
    marker, ledger, root, private, config, data = create_root_and_bridge(n)
    cid = create_container(n, n["a"], config, data, marker, ledger)
    rs=root.lstat()
    output({"status":"CONTAINER_A_STARTED","owner":n["owner"],"root":str(root),
            "root_dev":rs.st_dev,"root_inode":rs.st_ino,
            "network_id":marker["network_id"],"container_a_id":cid,
            "artifact_sha256":artifact_sha,"config_secret_values_emitted":False})


def phase_inspect_a(nonce, app_id, artifact_id, artifact_sha, unit_name,
                    node_id, agent_id, hub_revision):
    n, marker, ledger, root, private, config, data = load_state(nonce)
    if marker.get("artifact_sha256") != artifact_sha or not marker.get("container_a_id"):
        raise SafeStop("A_owner_artifact_binding_mismatch")
    c = inspect_container(n, marker["container_a_id"], n["a"], config, data)
    if not c["running"] or not c["pid"]:
        raise SafeStop("A_not_running")
    deadline=time.monotonic()+60
    while True:
        try:
            snap = read_cache_snapshot(n,data,app_id,artifact_id,artifact_sha,unit_name,node_id,agent_id,c["pid"],marker["container_a_id"])
            break
        except SafeStop as exc:
            if str(exc) not in {"assigned_application_not_persisted", "artifact_cache_db_row_missing", "artifact_cache_file_not_yet_present"} or time.monotonic() >= deadline:
                raise
            time.sleep(.5)
    if snap["global_revision"] < hub_revision:
        raise SafeStop("A_local_revision_behind_hub")
    output({"status":"ARTIFACT_CACHE_A_VERIFIED","container_a":c,"local_database":snap,
            "source_sha256":AGENT_SHA,"host_integration":False})


def phase_recreate_b(nonce, app_id, artifact_id, artifact_sha, unit_name, node_id, agent_id):
    n, marker, ledger, root, private, config, data = load_state(nonce)
    aid = marker.get("container_a_id")
    if not aid or marker.get("artifact_sha256") != artifact_sha:
        raise SafeStop("A_owner_binding_missing")
    a = inspect_container(n,aid,n["a"],config,data)
    if not a["running"]:
        raise SafeStop("A_expected_running_before_stop")
    rc,_,_=docker(["container","stop",aid],timeout=20)
    if rc:
        raise SafeStop("exact_A_stop_failed")
    stopped=inspect_container(n,aid,n["a"],config,data)
    if stopped["running"]: raise SafeStop("A_stop_not_observed")
    pre_b=read_cache_snapshot(n,data,app_id,artifact_id,artifact_sha,unit_name,node_id,agent_id)
    docker(["container","rm",aid],timeout=20)
    exact_absent("container",aid); exact_absent("container",n["a"])
    marker["container_a_removed_id"]=aid
    marker["container_a_id"]=None
    ledger.setdefault("removed_container_ids",[])
    if aid not in ledger["removed_container_ids"]: ledger["removed_container_ids"].append(aid)
    ledger["phase"]="A_REMOVED_PRE_B_SNAPSHOT"
    ledger["a_stop_snapshot"]=pre_b
    atomic_owner(root,marker,ledger)
    bid=create_container(n,n["b"],config,data,marker,ledger)
    b=inspect_container(n,bid,n["b"],config,data)
    output({"status":"CONTAINER_B_STARTED","container_a_removed_id":aid,
            "pre_b_persistence":pre_b,"container_b":b})


def phase_inspect_b(nonce, app_id, artifact_id, artifact_sha, unit_name,
                    node_id, agent_id, hub_revision):
    n,marker,ledger,root,private,config,data=load_state(nonce)
    bid=marker.get("container_b_id")
    if not bid or marker.get("artifact_sha256")!=artifact_sha: raise SafeStop("B_owner_binding_missing")
    b=inspect_container(n,bid,n["b"],config,data)
    if not b["running"] or not b["pid"]: raise SafeStop("B_not_running")
    snap=read_cache_snapshot(n,data,app_id,artifact_id,artifact_sha,unit_name,node_id,agent_id,b["pid"],bid)
    pre=ledger.get("a_stop_snapshot")
    if not isinstance(pre,dict) or (snap["db_dev"],snap["db_inode"])!=(pre["db_dev"],pre["db_inode"]):
        raise SafeStop("B_did_not_reopen_same_database")
    if snap["global_revision"]<max(hub_revision,pre["global_revision"]):
        raise SafeStop("B_revision_regressed_or_behind_hub")
    if snap["artifact_cache"] != pre["artifact_cache"]:
        raise SafeStop("B_artifact_cache_changed")
    if snap["application"] != pre["application"] or snap["identity"] != pre["identity"]:
        raise SafeStop("B_persisted_identity_or_assignment_changed")
    rc,_,_=docker(["container","stop",bid],timeout=20)
    if rc: raise SafeStop("exact_B_stop_failed")
    bs=inspect_container(n,bid,n["b"],config,data)
    if bs["running"]: raise SafeStop("B_stop_not_observed")
    output({"status":"ARTIFACT_CACHE_PERSISTED_A_TO_B","container_b":b,
            "local_database":snap,"container_b_stopped":bs,
            "app_deploy_execution_count":0,"managed_unit_present":False})


def _container_by_id_or_absent(n, cid, cname, config, data):
    rc,out,_=docker(["container","inspect",cid])
    if rc:
        exact_absent("container",cid)
        exact_absent("container",cname)
        return None
    try: rows=json.loads(out)
    except ValueError: raise SafeStop("container_inspect_json_invalid")
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict) or rows[0].get("Id")!=cid:
        raise SafeStop("container_inspect_identity_invalid")
    return inspect_container(n,cid,cname,config,data)


def require_known_container_absent(n,cid,cname,config,data):
    info=_container_by_id_or_absent(n,cid,cname,config,data)
    if info is not None:
        raise SafeStop("known_removed_container_id_still_present")
    return True


def _network_row(n, rows, expected_id=None):
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):
        raise SafeStop("owned_network_inspect_shape_invalid")
    row=rows[0]
    if (row.get("Name")!=n["network"] or row.get("Driver")!="bridge" or row.get("Scope")!="local"
            or row.get("Internal") is not False or row.get("Attachable") is not False
            or (row.get("Labels") or {}).get("nodesteer.e2e.owner")!=n["owner"]
            or not isinstance(row.get("Id"),str) or not re.fullmatch(r"[0-9a-f]{64}",row["Id"])
            or (expected_id is not None and row.get("Id")!=expected_id)):
        raise SafeStop("owned_network_identity_mismatch")
    return row


def phase_cleanup_runtime(nonce):
    n=resource_names(nonce); n["nonce"]=nonce
    if not n["root"].exists() and not n["root"].is_symlink():
        exact_absent("container",n["a"]); exact_absent("container",n["b"]); exact_absent("network",n["network"])
        output({"status":"NO_RUNTIME_ROOT_RESOURCES","root_absent":True,"containers_absent":True,"network_absent":True})
        return
    n,marker,ledger,root,private,config,data=load_state(nonce)
    removed=[]
    for key,name in (("container_b_id",n["b"]),("container_a_id",n["a"])):
        cid=marker.get(key)
        if not cid:
            rc,out,_=docker(["container","inspect",name])
            if rc:
                exact_absent("container",name)
                removed_key=key.replace("_id","_removed_id")
                known_removed=marker.get(removed_key)
                if known_removed:
                    require_known_container_absent(n,known_removed,name,config,data)
                    ledger.setdefault("removed_container_ids",[])
                    if known_removed not in ledger["removed_container_ids"]: ledger["removed_container_ids"].append(known_removed)
                    atomic_owner(root,marker,ledger)
                continue
            try: rows=json.loads(out)
            except ValueError: raise SafeStop("exact_name_container_inspect_invalid")
            if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):
                raise SafeStop("exact_name_container_inspect_shape_invalid")
            cid=rows[0].get("Id")
            if not isinstance(cid,str) or not re.fullmatch(r"[0-9a-f]{64}",cid):
                raise SafeStop("exact_name_container_id_invalid")
            inspect_container(n,cid,name,config,data)
            marker[key]=cid
            marker.setdefault("created_container_ids",[])
            if cid not in marker["created_container_ids"]: marker["created_container_ids"].append(cid)
            ledger.setdefault("container_ids",[])
            if cid not in ledger["container_ids"]: ledger["container_ids"].append(cid)
            atomic_owner(root,marker,ledger)
        info=_container_by_id_or_absent(n,cid,name,config,data)
        if info is None:
            marker[key.replace("_id","_removed_id")]=cid
            marker[key]=None
            ledger.setdefault("removed_container_ids",[])
            if cid not in ledger["removed_container_ids"]: ledger["removed_container_ids"].append(cid)
            ledger["last_removed_container_id"]=cid
            atomic_owner(root,marker,ledger)
            removed.append(cid)
            continue
        if info["running"]:
            ledger["container_stop_requested_id"]=cid; atomic_owner(root,marker,ledger)
            docker(["container","stop",cid],timeout=20)
            after_stop=_container_by_id_or_absent(n,cid,name,config,data)
            if after_stop is None:
                marker[key.replace("_id","_removed_id")]=cid
                marker[key]=None
                ledger.setdefault("removed_container_ids",[])
                if cid not in ledger["removed_container_ids"]: ledger["removed_container_ids"].append(cid)
                ledger["last_removed_container_id"]=cid
                atomic_owner(root,marker,ledger); removed.append(cid); continue
            if after_stop["running"]: raise SafeStop("exact_owned_container_stop_unconfirmed")
        ledger["container_remove_requested_id"]=cid; atomic_owner(root,marker,ledger)
        docker(["container","rm",cid],timeout=20)
        after_rm=_container_by_id_or_absent(n,cid,name,config,data)
        if after_rm is not None:
            raise SafeStop("exact_owned_container_still_present_after_remove")
        marker[key.replace("_id","_removed_id")]=cid
        marker[key]=None
        ledger.setdefault("removed_container_ids",[])
        if cid not in ledger["removed_container_ids"]: ledger["removed_container_ids"].append(cid)
        ledger["last_removed_container_id"]=cid
        atomic_owner(root,marker,ledger)
        removed.append(cid)

    nid=marker.get("network_id")
    if not nid:
        rc,out,_=docker(["network","inspect",n["network"]])
        if rc:
            exact_absent("network",n["network"])
            marker["network_removed_id"]="ABSENT_EXACT_NONCE_NAME"
            atomic_owner(root,marker,ledger)
        else:
            try: rows=json.loads(out)
            except ValueError: raise SafeStop("exact_name_network_inspect_invalid")
            row=_network_row(n,rows)
            nid=row["Id"]; marker["network_id"]=nid; atomic_owner(root,marker,ledger)
    if nid:
        rc,out,_=docker(["network","inspect",nid])
        if rc:
            exact_absent("network",nid)
            exact_absent("network",n["network"])
            marker["network_removed_id"]=nid; marker["network_id"]=None
            atomic_owner(root,marker,ledger)
        else:
            try: rows=json.loads(out)
            except ValueError: raise SafeStop("owned_network_inspect_json_invalid")
            _network_row(n,rows,nid)
            ledger["network_remove_requested_id"]=nid; atomic_owner(root,marker,ledger)
            docker(["network","rm",nid],timeout=20)
            rc2,out2,_=docker(["network","inspect",nid])
            if rc2:
                exact_absent("network",nid)
                exact_absent("network",n["network"])
                marker["network_removed_id"]=nid; marker["network_id"]=None
                atomic_owner(root,marker,ledger)
            else:
                try: rows2=json.loads(out2)
                except ValueError: raise SafeStop("post_remove_network_inspect_invalid")
                _network_row(n,rows2,nid)
                raise SafeStop("exact_owned_network_still_present_after_remove")
    if not marker.get("network_removed_id"):
        raise SafeStop("owned_network_absence_not_proven")
    ledger["phase"]="RUNTIME_REMOVED_ROOT_RETAINED"; atomic_owner(root,marker,ledger)
    output({"status":"RUNTIME_REMOVED_ROOT_RETAINED","removed_container_ids":removed,
            "network_removed":True,"root":str(root)})


def phase_cleanup_root(nonce, hub_objects_404, artifact_sha, expected_root_dev, expected_root_inode):
    if not hub_objects_404: raise SafeStop("hub_objects_404_gate_required")
    n=resource_names(nonce); n["nonce"]=nonce; root=n["root"]
    if (type(expected_root_dev) is not int or type(expected_root_inode) is not int
            or not re.fullmatch(r"[a-f0-9]{64}",artifact_sha)):
        raise SafeStop("cleanup_root_external_pin_invalid")
    try: rs=root.lstat()
    except FileNotFoundError:
        output({"status":"EXACT_OWNED_ROOT_ALREADY_ABSENT","root_absent":True,"hub_objects_404_gate":True})
        return
    if (not stat.S_ISDIR(rs.st_mode) or root.is_symlink() or root.resolve()!=root
            or (rs.st_dev,rs.st_ino,rs.st_uid,stat.S_IMODE(rs.st_mode))!=(expected_root_dev,expected_root_inode,0,0o700)):
        raise SafeStop("cleanup_root_path_pin_mismatch")
    names={p.name for p in root.iterdir()}
    if not names:
        root.rmdir()
        output({"status":"EXACT_OWNED_ROOT_REMOVED","root_absent":True,
                "empty_residual_removed_by_external_nonce_dev_inode_pin":True,"hub_objects_404_gate":True})
        return
    allowed={"owner.json","resource-ledger.json","private","data"}
    temps={name for name in names if re.fullmatch(r"(?:owner|resource-ledger)\.json\.tmp-[0-9a-f]{10}",name)}
    if names-allowed-temps: raise SafeStop("owned_root_unknown_entry")
    marker=None; ledger=None
    owner_path=root/"owner.json"; ledger_path=root/"resource-ledger.json"
    if "owner.json" in names:
        _,raw=owned_json(owner_path,65536,0,0,0o600); marker=json.loads(raw)
    if "resource-ledger.json" in names:
        _,raw=owned_json(ledger_path,131072,0,0,0o600); ledger=json.loads(raw)
    expected_owner="qa-resume-20261012-artcache-"+nonce
    def valid_binding(obj):
        return (isinstance(obj,dict) and obj.get("owner")==expected_owner and obj.get("nonce")==nonce
                and obj.get("root")==str(root) and obj.get("root_dev")==expected_root_dev
                and obj.get("root_inode")==expected_root_inode and obj.get("image_id")==IMAGE
                and obj.get("network_name")==n["network"])
    if marker is not None and not valid_binding(marker): raise SafeStop("cleanup_owner_marker_identity_mismatch")
    if ledger is not None and not valid_binding(ledger): raise SafeStop("cleanup_resource_ledger_identity_mismatch")
    if marker is None and ledger is None:
        raise SafeStop("nonempty_unmarked_root_refused")
    for name in temps:
        temp=root/name; ts=temp.lstat()
        if temp.is_symlink() or not stat.S_ISREG(ts.st_mode) or (ts.st_uid,ts.st_gid,stat.S_IMODE(ts.st_mode))!=(0,0,0o600) or ts.st_size>131072:
            raise SafeStop("owned_metadata_temp_guard_failed")
    if marker is not None and marker.get("artifact_sha256")!=artifact_sha:
        raise SafeStop("cleanup_artifact_marker_mismatch")
    if marker is not None and ledger is None and marker.get("children_removed") is not True:
        raise SafeStop("owner_only_residual_root_not_proven")
    if marker is not None and marker.get("children_removed") is True and ("private" in names or "data" in names):
        raise SafeStop("children_reappeared_after_cleanup_marker")
    if ledger is not None and ledger.get("phase") not in {"RUNTIME_REMOVED_ROOT_RETAINED","ROOT_CHILDREN_CLEANUP","ROOT_CHILDREN_REMOVED"}:
        raise SafeStop("cleanup_runtime_removal_not_recorded")
    if marker is not None and (marker.get("container_a_id") or marker.get("container_b_id")
            or marker.get("network_id") is not None or not marker.get("network_removed_id")):
        raise SafeStop("cleanup_active_runtime_id_or_unproven_network_removal")
    if marker is not None and marker.get("created_container_ids"):
        removed=set((ledger or {}).get("removed_container_ids",[]))
        removed.update(x for x in (marker.get("container_a_removed_id"),marker.get("container_b_removed_id")) if x)
        if not set(marker["created_container_ids"]).issubset(removed):
            raise SafeStop("cleanup_created_container_ids_not_all_removed")
    # Missing marker is only accepted after the known children are gone; an empty directory is handled above.
    if marker is None and ("private" in names or "data" in names):
        raise SafeStop("cleanup_children_without_owner_marker")
    private=root/"private"; data=root/"data"; config=private/"agent.yaml"
    if "private" in names:
        ps=private.lstat()
        if private.is_symlink() or not stat.S_ISDIR(ps.st_mode) or (ps.st_uid,ps.st_gid,stat.S_IMODE(ps.st_mode))!=(65534,65534,0o700) or private.resolve()!=root.resolve()/"private":
            raise SafeStop("cleanup_private_directory_guard_failed")
        if {x.name for x in private.iterdir()}-{"agent.yaml"}: raise SafeStop("private_root_unknown_entry")
        private_names={x.name for x in private.iterdir()}
        if "agent.yaml" in private_names:
            cs=config.lstat()
            if config.is_symlink() or not stat.S_ISREG(cs.st_mode) or (cs.st_uid,cs.st_gid,stat.S_IMODE(cs.st_mode))!=(65534,65534,0o600) or config.resolve()!=private.resolve()/"agent.yaml":
                raise SafeStop("cleanup_private_config_guard_failed")
    cleanup_dirs={}
    cache_entry=None
    if "data" in names:
        ds=data.lstat()
        if data.is_symlink() or not stat.S_ISDIR(ds.st_mode) or (ds.st_uid,ds.st_gid,stat.S_IMODE(ds.st_mode))!=(65534,65534,0o700) or data.resolve()!=root.resolve()/"data":
            raise SafeStop("cleanup_data_directory_guard_failed")
        if ds.st_dev != rs.st_dev:
            raise SafeStop("cleanup_data_filesystem_mismatch")
        approved_data_names={"state.db","state.db-wal","state.db-shm","artifacts",
                             "scripts","applications","executions","logs","tmp"}
        if {x.name for x in data.iterdir()}-approved_data_names:
            raise SafeStop("owned_data_unknown_entry")
        data_names={x.name for x in data.iterdir()}
        for dirname in ("scripts","applications","executions","logs","tmp"):
            if dirname not in data_names:
                continue
            directory=data/dirname
            st=directory.lstat()
            if (directory.is_symlink() or not stat.S_ISDIR(st.st_mode)
                    or (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))!=(65534,65534,0o755)
                    or st.st_dev!=ds.st_dev or directory.resolve()!=data.resolve()/dirname):
                raise SafeStop("agent_init_directory_guard_failed")
            with os.scandir(directory) as entries:
                if next(entries,None) is not None:
                    raise SafeStop("agent_init_directory_not_empty")
            cleanup_dirs[dirname]=(directory,st.st_dev,st.st_ino,st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))
        artifact_dir=data/"artifacts"
        if "artifacts" in data_names:
            ads=artifact_dir.lstat()
            if (artifact_dir.is_symlink() or not stat.S_ISDIR(ads.st_mode)
                    or (ads.st_uid,ads.st_gid,stat.S_IMODE(ads.st_mode))!=(65534,65534,0o755)
                    or ads.st_dev!=ds.st_dev or artifact_dir.resolve()!=data.resolve()/"artifacts"):
                raise SafeStop("artifact_cache_dir_unsafe")
            entries=list(artifact_dir.iterdir())
            if len(entries)>1 or any(x.name!=artifact_sha or x.is_symlink() or not x.is_file() for x in entries):
                raise SafeStop("artifact_cache_unknown_entry")
            if entries:
                x=entries[0]; fs=x.lstat()
                if (not stat.S_ISREG(fs.st_mode)
                        or (fs.st_dev,fs.st_uid,fs.st_gid,fs.st_size)!=(ds.st_dev,65534,65534,32)
                        or digest(x.read_bytes())!=artifact_sha):
                    raise SafeStop("approved_artifact_cache_file_guard_failed")
                cache_entry=(x,fs.st_dev,fs.st_ino,fs.st_uid,fs.st_gid,fs.st_size)
            cleanup_dirs["artifacts"]=(artifact_dir,ads.st_dev,ads.st_ino,ads.st_uid,ads.st_gid,stat.S_IMODE(ads.st_mode))
        for fname in ("state.db-wal","state.db-shm","state.db"):
            f=data/fname
            if fname in data_names:
                fs=f.lstat()
                if f.is_symlink() or not stat.S_ISREG(fs.st_mode) or fs.st_uid!=65534 or fs.st_dev!=ds.st_dev:
                    raise SafeStop("state_file_cleanup_guard_failed")
    # Restartable children-first deletion; owner proof remains until all data/config paths are gone.
    if ledger is not None and marker is not None:
        ledger["phase"]="ROOT_CHILDREN_CLEANUP"; atomic_json(ledger_path,ledger)
    if "data" in names:
        data_names={x.name for x in data.iterdir()}
        artifact_dir=data/"artifacts"
        if "artifacts" in data_names:
            if cache_entry is not None:
                entry,dev,ino,uid,gid,size=cache_entry
                current=entry.lstat()
                if (entry.is_symlink() or not stat.S_ISREG(current.st_mode)
                        or (current.st_dev,current.st_ino,current.st_uid,current.st_gid,current.st_size)
                        !=(dev,ino,uid,gid,size) or digest(entry.read_bytes())!=artifact_sha):
                    raise SafeStop("approved_artifact_cache_file_changed_before_unlink")
                entry.unlink()
            current_dir=artifact_dir.lstat(); _,saved_dev,saved_ino,saved_uid,saved_gid,saved_mode=cleanup_dirs["artifacts"]
            if (artifact_dir.is_symlink() or not stat.S_ISDIR(current_dir.st_mode)
                    or (current_dir.st_dev,current_dir.st_ino,current_dir.st_uid,current_dir.st_gid,stat.S_IMODE(current_dir.st_mode))
                    !=(saved_dev,saved_ino,saved_uid,saved_gid,saved_mode)):
                raise SafeStop("artifact_cache_dir_changed_before_rmdir")
            artifact_dir.rmdir()
        for dirname in ("scripts","applications","executions","logs","tmp"):
            if dirname not in data_names:
                continue
            directory,dev,ino,uid,gid,mode=cleanup_dirs[dirname]
            current=directory.lstat()
            if (directory.is_symlink() or not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev,current.st_ino,current.st_uid,current.st_gid,stat.S_IMODE(current.st_mode))
                    !=(dev,ino,uid,gid,mode)):
                raise SafeStop("agent_init_directory_changed_before_rmdir")
            with os.scandir(directory) as entries:
                if next(entries,None) is not None:
                    raise SafeStop("agent_init_directory_gained_entry_before_rmdir")
            directory.rmdir()
        for fname in ("state.db-wal","state.db-shm","state.db"):
            f=data/fname
            if fname in data_names: f.unlink()
        data.rmdir()
    if "private" in names:
        if "agent.yaml" in private_names: config.unlink()
        private.rmdir()
    # Remove only exact atomic-write temp names after current owner metadata has passed its guards.
    for name in temps:
        (root/name).unlink()
    # Once children are gone, persist a final phase before removing root metadata.
    if ledger is not None and marker is not None:
        marker["children_removed"]=True
        ledger["phase"]="ROOT_CHILDREN_REMOVED"
        atomic_owner(root,marker,ledger)
    remaining={p.name for p in root.iterdir()}
    if remaining=={"owner.json","resource-ledger.json"}:
        (root/"resource-ledger.json").unlink()
        remaining={p.name for p in root.iterdir()}
    if remaining=={"owner.json"}:
        _,raw=owned_json(owner_path,65536,0,0,0o600); last=json.loads(raw)
        if not valid_binding(last) or last.get("children_removed") is not True:
            raise SafeStop("owner_only_residual_root_not_proven")
        owner_path.unlink()
        remaining={p.name for p in root.iterdir()}
    if remaining:
        raise SafeStop("cleanup_root_residual_unknown_entry")
    root.rmdir()
    if root.exists() or root.is_symlink(): raise SafeStop("owned_root_still_present")
    output({"status":"EXACT_OWNED_ROOT_REMOVED","root_absent":True,"approved_cache_sha256":artifact_sha,
            "hub_objects_404_gate":True,"unknown_files_removed":False})


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--phase",required=True,choices=("prepare","inspect-a","recreate-b","inspect-b","cleanup-runtime","cleanup-root"))
    ap.add_argument("--nonce",required=True); ap.add_argument("--shell-sha256",required=True)
    ap.add_argument("--execute",action="store_true")
    ap.add_argument("--app-id",default=""); ap.add_argument("--artifact-id",default="")
    ap.add_argument("--artifact-sha256",default=""); ap.add_argument("--unit-name",default="")
    ap.add_argument("--node-id",default=""); ap.add_argument("--agent-id",default="")
    ap.add_argument("--hub-revision",type=int,default=0); ap.add_argument("--hub-objects-404",action="store_true")
    ap.add_argument("--root-dev",type=int,default=-1); ap.add_argument("--root-inode",type=int,default=-1)
    a=ap.parse_args()
    if not a.execute or a.shell_sha256!=SHELL_SHA or not re.fullmatch(r"[a-f0-9]{12}",a.nonce):
        raise SafeStop("execution_or_pinned_binary_guard_failed")
    if a.phase=="prepare":
        if not re.fullmatch(r"[a-f0-9]{64}",a.artifact_sha256): raise SafeStop("artifact_sha_invalid")
        phase_prepare(a.nonce,a.artifact_sha256)
    elif a.phase=="cleanup-runtime": phase_cleanup_runtime(a.nonce)
    elif a.phase=="cleanup-root": phase_cleanup_root(a.nonce,a.hub_objects_404,a.artifact_sha256,a.root_dev,a.root_inode)
    else:
        required=(a.app_id,a.artifact_id,a.artifact_sha256,a.unit_name,a.node_id,a.agent_id)
        if not all(required) or not re.fullmatch(r"[a-f0-9]{64}",a.artifact_sha256): raise SafeStop("phase_identity_args_missing")
        if a.phase=="inspect-a": phase_inspect_a(a.nonce,*required,a.hub_revision)
        elif a.phase=="recreate-b": phase_recreate_b(a.nonce,*required)
        else: phase_inspect_b(a.nonce,*required,a.hub_revision)


def safe_prepare_root_pin():
    extra={}
    try:
        parsed=argparse.ArgumentParser(add_help=False)
        parsed.add_argument("--phase"); parsed.add_argument("--nonce")
        args,_=parsed.parse_known_args()
        if args.phase=="prepare" and re.fullmatch(r"[a-f0-9]{12}",args.nonce or ""):
            n=resource_names(args.nonce); root=n["root"]; rs=root.lstat()
            if stat.S_ISDIR(rs.st_mode) and not root.is_symlink() and (rs.st_uid,stat.S_IMODE(rs.st_mode))==(0,0o700):
                _,raw=owned_json(root/"owner.json",65536,0,0,0o600); m=json.loads(raw)
                if (m.get("owner")==n["owner"] and m.get("nonce")==args.nonce
                        and m.get("root")==str(root) and m.get("root_dev")==rs.st_dev
                        and m.get("root_inode")==rs.st_ino and m.get("image_id")==IMAGE):
                    extra={"root_dev":rs.st_dev,"root_inode":rs.st_ino,"owner_root_marker_valid":True}
    except Exception:
        extra={}
    return extra


if __name__=="__main__":
    try:
        main()
    except SafeStop as exc:
        output({"status":"SAFE_STOP","safe_error":str(exc),**safe_prepare_root_pin()})
    except Exception as exc:
        output({"status":"SAFE_STOP","safe_error":"unexpected_"+type(exc).__name__,**safe_prepare_root_pin()})
    raise SystemExit(0)
