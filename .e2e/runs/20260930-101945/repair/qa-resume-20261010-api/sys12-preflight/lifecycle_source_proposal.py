#!/usr/bin/env python3
"""Draft lifecycle source for one shared-root noHost Docker SYS-12 fixture; not executed."""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    import sqlite3
except ImportError:
    sqlite3 = None

IMAGE = "sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44"
IMAGE_OWNER = "qa-resume-20261010-host-f174890c3846"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
ORIGINAL_ROOT = Path("/opt/qa-resume-20261010-host-f174890c3846")
ORIGINAL_CONFIG = ORIGINAL_ROOT / "private/host001-agent.yaml"
ARCHIVE = ORIGINAL_ROOT / "agent-rootfs.tar"
ARCHIVE_SHA = "d012a9301f242511460aa212608f8965879866ae6d64d8e2b8c40b7c0a0da490"
REPAIR_IP = "192.168.100.209"
REPAIR_PORT = 18080
STATE = {"root": None, "network_id": None, "container_ids": [], "partial_ledger": None}


class SafeStop(Exception):
    pass


def docker(argv, timeout=20):
    p = subprocess.run(["docker", *argv], text=True, capture_output=True, timeout=timeout, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic_json(path, value, uid=0, gid=0, mode=0o600):
    tmp = path.with_name(path.name + ".tmp-" + os.urandom(5).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(payload)
            f.flush()
            os.fsync(fd)
        os.replace(tmp, path)
    finally:
        os.close(fd)


def owned_file(path, limit, uid, gid, mode=None):
    p = Path(path)
    st = p.lstat()
    if not stat.S_ISREG(st.st_mode) or p.is_symlink() or st.st_uid != uid or st.st_gid != gid:
        raise SafeStop("owned_file_guard_failed")
    if mode is not None and stat.S_IMODE(st.st_mode) != mode:
        raise SafeStop("owned_file_mode_guard_failed")
    if st.st_size > limit:
        raise SafeStop("owned_file_size_guard_failed")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino, fs.st_uid, fs.st_gid) != (st.st_dev, st.st_ino, uid, gid):
            raise SafeStop("owned_file_changed_during_open")
        raw = bytearray()
        while len(raw) <= limit:
            b = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not b:
                break
            raw.extend(b)
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise SafeStop("owned_file_read_limit")
    return st, bytes(raw)


def root_private_guard():
    root = ORIGINAL_ROOT
    rs = root.lstat()
    if (not stat.S_ISDIR(rs.st_mode) or root.is_symlink() or root.resolve() != root
            or (rs.st_dev, rs.st_ino, rs.st_uid, stat.S_IMODE(rs.st_mode)) != (65025, 70286, 0, 0o700)):
        raise SafeStop("original_root_guard_failed")
    private = ORIGINAL_CONFIG.parent
    ps = private.lstat()
    if (not stat.S_ISDIR(ps.st_mode) or private.is_symlink() or private.resolve() != root / "private"
            or ps.st_uid != 0 or stat.S_IMODE(ps.st_mode) != 0o700):
        raise SafeStop("original_private_dir_guard_failed")
    st, raw = owned_file(ORIGINAL_CONFIG, 1024 * 1024, 0, 0, 0o600)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise SafeStop("original_config_encoding_invalid")
    parsed = {}
    for line in lines:
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key in {"hub_url", "registration_token"}:
            if key in parsed:
                raise SafeStop("original_config_duplicate_required_key")
            try:
                parsed[key] = json.loads(value.strip())
            except ValueError:
                raise SafeStop("original_config_required_value_invalid")
    if set(parsed) != {"hub_url", "registration_token"} or not all(isinstance(x, str) and x for x in parsed.values()):
        raise SafeStop("original_config_required_fields_missing")
    u = urlsplit(parsed["hub_url"])
    if (u.scheme not in {"http", "ws"} or u.hostname != REPAIR_IP or u.port != REPAIR_PORT
            or u.username or u.password or u.query or u.fragment or u.path not in {"", "/"}):
        raise SafeStop("repair_hub_url_guard_failed")
    ws_url = urlunsplit(("ws", f"{REPAIR_IP}:{REPAIR_PORT}", "/", "", ""))
    return rs, ps, st, ws_url, parsed["registration_token"]


def check_archive_shell(expected_sha):
    root_private_guard()
    st, _ = owned_file(ARCHIVE, 64 * 1024 * 1024, 0, 0)
    fd = os.open(ARCHIVE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        fs = os.fstat(f.fileno())
        if (fs.st_dev, fs.st_ino, fs.st_uid) != (st.st_dev, st.st_ino, st.st_uid):
            raise SafeStop("archive_changed_during_open")
        h = hashlib.sha256()
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
        if h.hexdigest() != ARCHIVE_SHA:
            raise SafeStop("archive_sha_guard_failed")
        f.seek(0)
        with tarfile.open(fileobj=f, mode="r:*") as tf:
            names = [x for x in ("rootfs/bin/sh", "bin/sh") if x in set(tf.getnames())]
            if len(names) != 1:
                raise SafeStop("archive_shell_member_ambiguous")
            m = tf.getmember(names[0])
            if not m.isfile() or m.mode & 0o111 == 0:
                raise SafeStop("archive_shell_not_executable_regular")
            actual = hash_bytes(tf.extractfile(m).read())
            if actual != expected_sha:
                raise SafeStop("archive_shell_sha_guard_failed")


def exact_absent(kind, name):
    rc, out, err = docker([kind, "inspect", name])
    if rc == 0:
        raise SafeStop("exact_resource_name_collision")
    low = err.lower()
    exact_missing = ("no such " + kind + ": " + name.lower()) in low or (
        "no such object: " + name.lower()) in low or (
        kind + " " + name.lower() + " not found") in low or (
        kind + " " + name.lower() + ": not found") in low
    if not exact_missing:
        raise SafeStop("exact_resource_name_unknown")


def recover_owned_network_id(name, owner):
    rc, out, _ = docker(["network", "inspect", name])
    if rc:
        exact_absent("network", name)
        return None
    try:
        rows = json.loads(out)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise SafeStop("exact_owned_network_inspect_cardinality")
        row = rows[0]
    except (ValueError, TypeError):
        raise SafeStop("exact_owned_network_inspect_shape_invalid")
    if (row.get("Name") != name or row.get("Driver") != "bridge"
            or row.get("Scope") != "local" or row.get("Internal") is not False
            or row.get("Attachable") is not False
            or (row.get("Labels") or {}).get("nodesteer.e2e.owner") != owner
            or not isinstance(row.get("Id"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row.get("Id", ""))):
        raise SafeStop("network_create_response_owner_mismatch")
    return row["Id"]


def inspect_image():
    rc, out, _ = docker(["image", "inspect", IMAGE])
    if rc:
        raise SafeStop("pinned_image_inspect_failed")
    rows = json.loads(out)
    if len(rows) != 1:
        raise SafeStop("pinned_image_cardinality_invalid")
    row = rows[0]
    labels = ((row.get("Config") or {}).get("Labels") or {})
    if row.get("Id") != IMAGE or labels.get("nodesteer.e2e.owner") != IMAGE_OWNER:
        raise SafeStop("pinned_image_identity_mismatch")


def names(args):
    node = "qa-sys12-docker-" + args.nonce
    owner = "qa-resume-20261012-sys12-" + args.nonce
    return node, owner, owner + "-a", owner + "-b", "nodesteer-qa-sys12-" + args.nonce


def root_paths(owner):
    root = Path("/opt") / owner
    return root, root / "private", root / "private/agent.yaml", root / "data"


def marker_data(args, owner, node_name, network_name, root):
    rs = root.lstat()
    return {"owner": owner, "nonce": args.nonce, "root": str(root),
            "root_dev": rs.st_dev, "root_inode": rs.st_ino, "root_uid": rs.st_uid,
            "root_mode": format(stat.S_IMODE(rs.st_mode), "04o"), "node_name": node_name,
            "image_id": IMAGE, "network_name": network_name, "container_a_name": owner + "-a",
            "container_b_name": owner + "-b", "created_container_ids": [],
            "network_id": None}


def load_owner(args):
    node_name, owner, ca, cb, network = names(args)
    root, private, config, data = root_paths(owner)
    rs = root.lstat()
    if not stat.S_ISDIR(rs.st_mode) or root.is_symlink() or root.resolve() != root:
        raise SafeStop("owned_root_guard_failed")
    ms, mraw = owned_file(root / "owner.json", 65536, 0, 0, 0o600)
    marker = json.loads(mraw)
    if (marker.get("owner") != owner or marker.get("nonce") != args.nonce
            or marker.get("root") != str(root)
            or (marker.get("root_dev"), marker.get("root_inode"), marker.get("root_uid"), marker.get("root_mode"))
            != (rs.st_dev, rs.st_ino, rs.st_uid, format(stat.S_IMODE(rs.st_mode), "04o"))
            or marker.get("image_id") != IMAGE or marker.get("network_name") != network):
        raise SafeStop("owned_marker_identity_mismatch")
    _, ledger_raw = owned_file(root / "resource-ledger.json", 131072, 0, 0, 0o600)
    ledger = json.loads(ledger_raw)
    if ledger.get("owner") != owner or ledger.get("nonce") != args.nonce:
        raise SafeStop("resource_ledger_identity_mismatch")
    private_st = private.lstat()
    data_st = data.lstat()
    config_st = config.lstat()
    if (not stat.S_ISDIR(private_st.st_mode) or private.is_symlink() or private.resolve() != private
            or private_st.st_uid != 65534 or private_st.st_gid != 65534
            or stat.S_IMODE(private_st.st_mode) != 0o700
            or not stat.S_ISDIR(data_st.st_mode) or data.is_symlink() or data.resolve() != data
            or data_st.st_uid != 65534 or data_st.st_gid != 65534
            or stat.S_IMODE(data_st.st_mode) != 0o700
            or not stat.S_ISREG(config_st.st_mode) or config.is_symlink() or config.resolve() != config
            or config_st.st_uid != 65534 or config_st.st_gid != 65534
            or stat.S_IMODE(config_st.st_mode) != 0o600):
        raise SafeStop("owned_private_or_data_path_guard_failed")
    STATE["root"] = str(root)
    STATE["network_id"] = marker.get("network_id")
    STATE["container_ids"] = list(marker.get("created_container_ids", []))
    STATE["partial_ledger"] = ledger
    return marker, ledger, root, private, config, data, owner, network


def atomic_owner(root, marker, ledger):
    atomic_json(root / "owner.json", marker)
    atomic_json(root / "resource-ledger.json", ledger)
    STATE["partial_ledger"] = ledger


def config_content(args, node_name):
    _, _, _, ws_url, token = root_private_guard()
    # Token stays only in this local byte buffer and the private file content; never logs or result JSON.
    return ("hub_url: " + json.dumps(ws_url) + "\n"
            + "registration_token: " + json.dumps(token) + "\n"
            + "node_name: " + json.dumps(node_name) + "\n").encode()


def create_root_and_network(args):
    node, owner, ca, cb, network = names(args)
    root, private, config, data = root_paths(owner)
    if os.geteuid() != 0 or root.exists() or root.is_symlink():
        raise SafeStop("root_privilege_or_unique_root_guard_failed")
    if not Path("/opt").is_dir() or Path("/opt").is_symlink():
        raise SafeStop("owned_root_parent_guard_failed")
    exact_absent("container", ca)
    exact_absent("container", cb)
    exact_absent("network", network)
    inspect_image()
    cfg_data = config_content(args, node)
    check_archive_shell(args.shell_sha256)

    root.mkdir(mode=0o700)
    os.chown(root, 0, 0)
    os.chmod(root, 0o700)
    STATE["root"] = str(root)
    marker = marker_data(args, owner, node, network, root)
    ledger = {"owner": owner, "nonce": args.nonce, "node_name": node,
              "phase": "ROOT_CREATED", "container_ids": [], "container_names": [],
              "network_id": None, "docker_calls": [], "secret_values_written_to_ledger": False}
    atomic_owner(root, marker, ledger)
    STATE["root"] = str(root)
    private.mkdir(mode=0o700)
    data.mkdir(mode=0o700)
    for p in (private, data):
        os.chown(p, 65534, 65534)
        os.chmod(p, 0o700)
    ledger.update({"phase": "PRIVATE_ROOT_AND_DATA_CREATED", "private_config_path": str(config),
                   "private_directory": str(private), "data_path": str(data),
                   "private_config_uid_gid_mode": "65534:65534:0600",
                   "private_directory_uid_gid_mode": "65534:65534:0700",
                   "data_uid_gid_mode": "65534:65534:0700"})
    atomic_owner(root, marker, ledger)
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchown(fd, 65534, 65534)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(cfg_data)
            f.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    cs = config.lstat()
    if (cs.st_uid, cs.st_gid, stat.S_IMODE(cs.st_mode)) != (65534, 65534, 0o600):
        raise SafeStop("derived_private_config_guard_failed")
    rc, network_create_out, _ = docker(["network", "create", "--driver", "bridge",
                                       "--label", "nodesteer.e2e.owner=" + owner, network])
    network_id = network_create_out.strip()
    if rc or not re.fullmatch(r"[0-9a-f]{64}", network_id):
        recovered = recover_owned_network_id(network, owner)
        if recovered:
            marker["network_id"] = recovered
            ledger.update({"phase": "NETWORK_ID_RECOVERED_AFTER_CREATE_RESPONSE", "network_id": recovered,
                           "network_name": network})
            STATE["network_id"] = recovered
            atomic_owner(root, marker, ledger)
        raise SafeStop("owned_network_create_failed")
    marker["network_id"] = network_id
    ledger.update({"phase": "NETWORK_CREATE_RETURNED_ID", "network_id": network_id,
                   "network_name": network})
    STATE["network_id"] = network_id
    atomic_owner(root, marker, ledger)
    rc, inspect_out, _ = docker(["network", "inspect", network_id])
    try:
        inspect_rows = json.loads(inspect_out) if not rc else None
    except (ValueError, TypeError):
        inspect_rows = None
    if not isinstance(inspect_rows, list) or len(inspect_rows) != 1 or not isinstance(inspect_rows[0], dict):
        raise SafeStop("owned_network_inspect_shape_invalid")
    nrow = inspect_rows[0]
    nlabels = nrow.get("Labels") or {}
    if (nrow.get("Id") != network_id or nrow.get("Name") != network
            or nrow.get("Driver") != "bridge" or nrow.get("Scope") != "local"
            or nrow.get("Internal") is not False or nrow.get("Attachable") is not False
            or nlabels.get("nodesteer.e2e.owner") != owner
            or recover_owned_network_id(network, owner) != network_id):
        raise SafeStop("owned_network_identity_mismatch")
    ledger.update({"phase": "NETWORK_CREATED", "network_id": nrow.get("Id"),
                   "network_name": network, "private_config_path": str(config),
                   "private_config_uid_gid_mode": "65534:65534:0600",
                   "data_path": str(data), "data_uid_gid_mode": "65534:65534:0700"})
    atomic_owner(root, marker, ledger)
    return marker, ledger, root, private, config, data, owner, network


def container_argv(owner, name, network, config, data):
    return ["run", "--detach", "--name", name, "--label", "nodesteer.e2e.owner=" + owner,
            "--user", "65534:65534", "--cap-drop", "ALL", "--read-only",
            "--security-opt", "no-new-privileges", "--network", network,
            "--mount", "type=bind,src=" + str(config) + ",dst=/run/nodesteer/agent.yaml,readonly",
            "--mount", "type=bind,src=" + str(data) + ",dst=/var/lib/nodesteer",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m", IMAGE]


def inspect_container(container_id, expected_name, owner, network, config, data):
    rc, out, _ = docker(["container", "inspect", container_id])
    if rc:
        raise SafeStop("owned_container_inspect_failed")
    rows = json.loads(out)
    if len(rows) != 1:
        raise SafeStop("owned_container_inspect_cardinality")
    row = rows[0]
    labels = ((row.get("Config") or {}).get("Labels") or {})
    hc = row.get("HostConfig") or {}
    state = row.get("State") or {}
    mount_rows = row.get("Mounts") or []
    binds = [m for m in mount_rows if m.get("Type") == "bind"]
    others = [m for m in mount_rows if m.get("Type") != "bind"]
    by_dst = {m.get("Destination"): m for m in binds}
    cfg_mount = by_dst.get("/run/nodesteer/agent.yaml") or {}
    data_mount = by_dst.get("/var/lib/nodesteer") or {}
    nnp = any(str(x).lower().replace("=", ":").startswith("no-new-privileges")
              for x in (hc.get("SecurityOpt") or []))
    security_options = [str(x).lower() for x in (hc.get("SecurityOpt") or [])]
    default_seccomp = not any(x.startswith("seccomp") or "unconfined" in x for x in security_options)
    pid_mode = hc.get("PidMode") or ""
    tmpfs = hc.get("Tmpfs") or {}
    tmpfs_value = str(tmpfs.get("/tmp", "")).lower()
    tmpfs_ok = (set(tmpfs) == {"/tmp"} and all(x in tmpfs_value for x in
                 ("noexec", "nosuid", "nodev", "size=16m")))
    valid = (row.get("Id") == container_id and row.get("Name") == "/" + expected_name
             and row.get("Image") == IMAGE and labels.get("nodesteer.e2e.owner") == owner
             and (row.get("Config") or {}).get("User") == "65534:65534"
             and hc.get("ReadonlyRootfs") is True and hc.get("Privileged") is False
             and not hc.get("CapAdd") and "ALL" in [str(x).upper() for x in hc.get("CapDrop", [])]
             and pid_mode in {"", "private"} and nnp
             and not hc.get("PortBindings") and not hc.get("Devices")
             and default_seccomp and tmpfs_ok and hc.get("NetworkMode") == network
             and len(binds) == 2 and cfg_mount.get("Source") == str(config)
             and cfg_mount.get("Destination") == "/run/nodesteer/agent.yaml" and cfg_mount.get("RW") is False
             and data_mount.get("Source") == str(data)
             and data_mount.get("Destination") == "/var/lib/nodesteer" and data_mount.get("RW") is True
             and all(m.get("Type") == "tmpfs" and m.get("Destination") == "/tmp" for m in others))
    if not valid:
        raise SafeStop("container_readback_policy_or_mount_guard_failed")
    net = ((row.get("NetworkSettings") or {}).get("Networks") or {}).get(network) or {}
    network_keys = set(((row.get("NetworkSettings") or {}).get("Networks") or {}).keys())
    assigned_ip = net.get("IPAddress")
    if network_keys != {network}:
        raise SafeStop("container_network_membership_mismatch")
    running = state.get("Running")
    if not isinstance(running, bool):
        raise SafeStop("container_running_state_invalid")
    if running or assigned_ip not in (None, ""):
        try:
            ipaddress.ip_address(assigned_ip)
        except (ValueError, TypeError):
            raise SafeStop("docker_dynamic_ip_readback_invalid")
    return {"container_id": container_id, "name": expected_name, "running": running,
            "pid": state.get("Pid") if isinstance(state.get("Pid"), int) else None,
            "state_status": state.get("Status"),
            "image_id": row.get("Image"), "uid": "65534:65534", "cap_add_empty": not hc.get("CapAdd"),
            "cap_drop_all": "ALL" in [str(x).upper() for x in hc.get("CapDrop", [])],
            "privileged": hc.get("Privileged"), "read_only_rootfs": hc.get("ReadonlyRootfs"),
            "pid_private": pid_mode in {"", "private"}, "no_new_privileges": nnp,
            "default_seccomp": default_seccomp, "tmpfs_private_only": tmpfs_ok,
            "mounts": [{"type": m.get("Type"), "source": m.get("Source"),
                        "destination": m.get("Destination"), "rw": m.get("RW")} for m in mount_rows],
            "network_name": network, "network_membership_exact": True,
            "assigned_ip": assigned_ip, "owner_label_matches": True}


def proc_snapshot(pid, container_id, db_dev, db_inode):
    proc = Path("/proc") / str(pid)
    try:
        stat_raw = (proc / "stat").read_text()
        close = stat_raw.rfind(")")
        if close < 0:
            raise SafeStop("agent_proc_stat_shape_invalid")
        tail = stat_raw[close + 1:].split()
        if len(tail) < 20:
            raise SafeStop("agent_proc_stat_shape_invalid")
        state = tail[0]
        start_ticks = tail[19]
        if state == "Z":
            raise SafeStop("agent_process_zombie")
        uid = None
        for line in (proc / "status").read_text().splitlines():
            if line.startswith("Uid:"):
                fields = line.split()
                uid = int(fields[1]) if len(fields) >= 2 else None
                break
        if uid != 65534:
            raise SafeStop("agent_process_uid_mismatch")
        cgroup = (proc / "cgroup").read_text()
        if container_id.lower() not in cgroup.lower():
            raise SafeStop("agent_process_container_cgroup_mismatch")
        fd = os.open(proc / "exe", os.O_RDONLY)
        try:
            exe_hash = hashlib.sha256()
            for block in iter(lambda: os.read(fd, 1024 * 1024), b""):
                exe_hash.update(block)
        finally:
            os.close(fd)
        exe_sha = exe_hash.hexdigest()
        if exe_sha != AGENT_SHA:
            raise SafeStop("agent_process_exe_sha_mismatch")
        in_root = os.stat(proc / "root/var/lib/nodesteer/state.db")
        if (in_root.st_dev, in_root.st_ino) != (db_dev, db_inode):
            raise SafeStop("agent_container_db_mount_identity_mismatch")
        matches = 0
        fd_dir = proc / "fd"
        for name in os.listdir(fd_dir):
            if not name.isdigit():
                continue
            try:
                fdst = os.stat(fd_dir / name)
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if (fdst.st_dev, fdst.st_ino) == (db_dev, db_inode):
                matches += 1
        if matches < 1:
            raise SafeStop("agent_process_has_no_open_fd_for_owned_database")
        return {"container_id": container_id, "pid": pid, "proc_state": state,
                "start_ticks": start_ticks, "uid": uid, "exe_sha256": exe_sha,
                "cgroup_matches_container": True, "container_db_dev": in_root.st_dev,
                "container_db_inode": in_root.st_ino, "matching_db_fd_count": matches}
    except SafeStop:
        raise
    except (OSError, ValueError, IndexError):
        raise SafeStop("agent_proc_readback_unavailable")


def prove_agent_opened_database(container_id, expected_name, owner, network, config, data,
                                db_identity):
    fresh = inspect_container(container_id, expected_name, owner, network, config, data)
    if not fresh.get("running") or not fresh.get("pid"):
        raise SafeStop("owned_agent_container_not_running_for_db_open_proof")
    first = proc_snapshot(fresh["pid"], container_id, db_identity["db_dev"], db_identity["db_inode"])
    second = proc_snapshot(fresh["pid"], container_id, db_identity["db_dev"], db_identity["db_inode"])
    final = inspect_container(container_id, expected_name, owner, network, config, data)
    if (not final.get("running") or final.get("container_id") != container_id
            or final.get("pid") != fresh.get("pid")):
        raise SafeStop("agent_container_pid_changed_during_db_open_proof")
    if (first["start_ticks"], first["exe_sha256"], first["uid"]) != (
            second["start_ticks"], second["exe_sha256"], second["uid"]):
        raise SafeStop("agent_process_identity_changed_during_db_open_proof")
    return first


def create_container(name, owner, network, config, data, root, marker, ledger, phase):
    attempts = ledger.setdefault("container_create_attempts", [])
    attempts.append({"name": name, "phase": phase, "request_started": True})
    atomic_owner(root, marker, ledger)
    rc, out, _ = docker(container_argv(owner, name, network, config, data), timeout=35)
    container_id = out.strip()
    if rc or not re.fullmatch(r"[0-9a-f]{64}", container_id):
        # Recover only this nonce-derived exact name after full owner/image/policy/mount validation.
        recovered = recover_container_by_name(name, owner, network, config, data, root, marker, ledger)
        if recovered:
            info = inspect_container(recovered, name, owner, network, config, data)
            attempts[-1]["returned_id_recovered_by_exact_name"] = True
            atomic_owner(root, marker, ledger)
            if info.get("running") and info.get("pid"):
                return recovered
        raise SafeStop("owned_container_start_failed")
    STATE["container_ids"].append(container_id)
    marker.setdefault("created_container_ids", []).append(container_id)
    ledger.setdefault("container_ids", []).append(container_id)
    attempts[-1]["container_id"] = container_id
    atomic_owner(root, marker, ledger)
    return container_id


def recover_container_by_name(name, owner, network, config, data, root, marker, ledger):
    rc, out, err = docker(["container", "inspect", name])
    if rc:
        exact_absent("container", name)
        return None
    try:
        rows = json.loads(out)
        if len(rows) != 1:
            raise SafeStop("exact_container_recovery_cardinality_invalid")
        candidate = rows[0]
        cid = candidate.get("Id")
        if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise SafeStop("exact_container_recovery_id_invalid")
        expected_name = name
        inspect_container(cid, expected_name, owner, network, config, data)
    except SafeStop:
        raise
    except (ValueError, TypeError, IndexError):
        raise SafeStop("exact_container_recovery_shape_invalid")
    field = "container_a_id" if name == marker.get("container_a_name") else "container_b_id"
    marker[field] = cid
    if cid not in marker.setdefault("created_container_ids", []):
        marker["created_container_ids"].append(cid)
    if cid not in ledger.setdefault("container_ids", []):
        ledger["container_ids"].append(cid)
    ledger.setdefault("recovered_container_ids", []).append(cid)
    STATE["container_ids"] = list(marker["created_container_ids"])
    atomic_owner(root, marker, ledger)
    return cid


def script_dir_state(data):
    path = data / "scripts"
    try:
        st = path.lstat()
        if (not stat.S_ISDIR(st.st_mode) or path.is_symlink() or st.st_uid != 65534
                or path.resolve() != data.resolve() / "scripts"):
            return {"status": "guard_failed", "materialized_script_cache_supported_by_source": False}
        names = sorted(x.name for x in path.iterdir())
        if len(names) > 64:
            return {"status": "entry_limit", "materialized_script_cache_supported_by_source": False}
        return {"status": "ok", "uid": st.st_uid, "entry_count": len(names), "entry_names": names,
                "materialized_script_cache_supported_by_source": False}
    except FileNotFoundError:
        return {"status": "absent", "materialized_script_cache_supported_by_source": False}
    except OSError:
        return {"status": "unavailable", "materialized_script_cache_supported_by_source": False}


def read_database(data, schedule_id, execution_id, script_id):
    if sqlite3 is None:
        return {"status": "sqlite_module_unavailable", "credential_selected": False}
    db = data / "state.db"
    try:
        data_st = data.lstat()
        if (not stat.S_ISDIR(data_st.st_mode) or data.is_symlink() or data_st.st_uid != 65534
                or stat.S_IMODE(data_st.st_mode) != 0o700 or data.resolve() != data):
            return {"status": "data_dir_path_guard_failed", "credential_selected": False}
        st = db.lstat()
        if (not stat.S_ISREG(st.st_mode) or db.is_symlink() or st.st_uid != 65534
                or st.st_dev != data_st.st_dev or db.resolve() != data / "state.db"):
            return {"status": "db_path_guard_failed"}
        dbfd = os.open(db, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        con = None
        try:
            opened = os.fstat(dbfd)
            if (opened.st_dev, opened.st_ino, opened.st_uid) != (st.st_dev, st.st_ino, 65534):
                raise SafeStop("db_open_identity_mismatch")
            con = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True, timeout=3)
            con.execute("PRAGMA query_only=ON")
            quick = con.execute("PRAGMA quick_check").fetchone()[0]
            if quick != "ok":
                raise SafeStop("sqlite_quick_check_failed")
            ident = {k: v for k, v in con.execute(
                "SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')")}
            revision = con.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0]
            schedule = None
            if schedule_id:
                row = con.execute("SELECT id,revision,enabled,definition_json FROM schedules WHERE id=?",
                                  (schedule_id,)).fetchone()
                if row:
                    definition = json.loads(row[3])
                    schedule = {"id": row[0], "revision": row[1], "enabled": bool(row[2]),
                                "task_id": definition.get("task_id"), "execution_owner": definition.get("execution_owner")}
            execution = None
            if execution_id:
                row = con.execute("SELECT id,task_id,status,exit_code,script_id,script_revision FROM executions WHERE id=?",
                                  (execution_id,)).fetchone()
                if row:
                    execution = {"id": row[0], "task_id": row[1], "status": row[2], "exit_code": row[3],
                                 "script_id": row[4], "script_revision": row[5]}
            script = None
            if script_id:
                row = con.execute("SELECT id,name,content,revision,sha256,enabled FROM scripts WHERE id=?",
                                  (script_id,)).fetchone()
                if row:
                    content = row[2].encode("utf-8")
                    script = {"id": row[0], "name": row[1], "revision": row[3], "stored_sha256": row[4],
                              "content_bytes": len(content), "content_sha256": hash_bytes(content),
                              "enabled": bool(row[5]), "content_emitted": False}
        finally:
            if con is not None:
                con.close()
            opened_after = os.fstat(dbfd)
            path_after = db.lstat()
            os.close(dbfd)
        if ((opened_after.st_dev, opened_after.st_ino, opened_after.st_uid)
                != (st.st_dev, st.st_ino, 65534)
                or (path_after.st_dev, path_after.st_ino, path_after.st_uid)
                != (st.st_dev, st.st_ino, 65534)):
            raise SafeStop("db_path_identity_changed_during_read")
        return {"status": "ok", "db_dev": st.st_dev, "db_inode": st.st_ino, "uid": st.st_uid,
                "quick_check": quick, "identity": ident, "global_revision": revision,
                "schedule": schedule, "execution": execution, "script": script,
                "script_directory": script_dir_state(data), "credential_selected": False}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return {"status": "unavailable_or_invalid", "credential_selected": False}


def wait_database_file(data, deadline_seconds=25):
    deadline = time.monotonic() + deadline_seconds
    last_error = "absent"
    while time.monotonic() < deadline:
        try:
            data_st = data.lstat()
            if (not stat.S_ISDIR(data_st.st_mode) or data.is_symlink() or data_st.st_uid != 65534
                    or stat.S_IMODE(data_st.st_mode) != 0o700 or data.resolve() != data):
                raise SafeStop("data_dir_path_guard_failed")
            db = data / "state.db"
            st = db.lstat()
            if not stat.S_ISREG(st.st_mode) or db.is_symlink() or st.st_uid != 65534 or st.st_dev != data_st.st_dev:
                raise SafeStop("db_path_guard_failed")
            fd = os.open(db, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(fd)
                path_after = db.lstat()
                if ((opened.st_dev, opened.st_ino, opened.st_uid)
                        != (st.st_dev, st.st_ino, 65534)
                        or (path_after.st_dev, path_after.st_ino, path_after.st_uid)
                        != (st.st_dev, st.st_ino, 65534)):
                    raise SafeStop("db_open_identity_mismatch")
            finally:
                os.close(fd)
            return {"db_dev": st.st_dev, "db_inode": st.st_ino, "uid": st.st_uid}
        except FileNotFoundError:
            last_error = "absent"
            time.sleep(0.2)
        except SafeStop:
            raise
        except OSError:
            last_error = "unavailable"
            time.sleep(0.2)
    raise SafeStop("bounded_database_file_readiness_timeout_" + last_error)


def persistence_view(db):
    if db.get("status") != "ok":
        raise SafeStop("local_persistence_snapshot_unavailable")
    return {k: db.get(k) for k in ("db_dev", "db_inode", "quick_check", "identity", "schedule", "execution", "script")}


def require_no_regression(before, after):
    if after.get("status") != "ok" or persistence_view(before) != persistence_view(after):
        raise SafeStop("owned_local_persistence_changed_across_recreate")
    if after.get("global_revision", -1) < before.get("global_revision", -1):
        raise SafeStop("local_global_revision_regressed_across_recreate")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=("prepare", "inspect", "recreate", "cleanup-runtime", "cleanup-root"))
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--shell-sha256", required=True)
    ap.add_argument("--schedule-id", default="")
    ap.add_argument("--execution-id", default="")
    ap.add_argument("--script-id", default="")
    ap.add_argument("--task-id", default="")
    ap.add_argument("--script-sha256", default="")
    ap.add_argument("--script-revision", type=int, default=1)
    ap.add_argument("--expected-node-id", default="")
    ap.add_argument("--expected-agent-id", default="")
    ap.add_argument("--hub-desired-revision", type=int, default=-1)
    ap.add_argument("--stop-after-inspection", action="store_true")
    ap.add_argument("--hub-objects-deleted", action="store_true")
    ap.add_argument("--execute", action="store_true", help="resource action; separately Root-approved only")
    args = ap.parse_args()
    if not re.fullmatch(r"[0-9a-f]{12}", args.nonce) or not re.fullmatch(r"[0-9a-f]{64}", args.shell_sha256):
        raise SafeStop("nonce_or_shell_hash_shape_invalid")
    if not args.execute:
        print(json.dumps({"status": "DRAFT_NO_ACTION", "phase": args.phase,
                          "resource_actions": False, "credentials_emitted": False}))
        return
    node, owner, ca, cb, network = names(args)
    if args.phase == "prepare":
        marker, ledger, root, private, config, data, owner, network = create_root_and_network(args)
        cid = create_container(ca, owner, network, config, data, root, marker, ledger, "prepare")
        ids = list(marker.get("created_container_ids", []))
        marker["container_a_id"] = cid
        ledger.update({"phase": "CONTAINER_A_CREATED", "container_ids": ids,
                       "container_names": [ca], "container_a_id": cid})
        atomic_owner(root, marker, ledger)
        safe_a = inspect_container(cid, ca, owner, network, config, data)
        if not safe_a.get("running") or not safe_a.get("pid"):
            raise SafeStop("container_a_not_running_after_start")
        ledger.update({"phase": "CONTAINER_A_STARTED", "container_a": safe_a})
        atomic_owner(root, marker, ledger)
        STATE["root"] = str(root)
        print(json.dumps({"status": "CONTAINER_A_STARTED", "owner": owner,
                          "owner_json": str(root / "owner.json"), "resource_ledger": ledger,
                          "credentials_emitted": False}, separators=(",", ":")))
        return
    marker, ledger, root, private, config, data, owner, network = load_owner(args)
    if args.phase == "inspect":
        out = []
        active = None
        active_key = None
        for key, name in (("container_a_id", ca), ("container_b_id", cb)):
            cid = marker.get(key)
            if cid and cid != marker.get("container_a_removed_id"):
                info = inspect_container(cid, name, owner, network, config, data)
                out.append(info)
                if info.get("running") and info.get("pid"):
                    active, active_key = info, key
        process_before = None
        process_after = None
        db_identity = wait_database_file(data) if active else None
        if active:
            active_name = cb if active_key == "container_b_id" else ca
            process_before = prove_agent_opened_database(
                active["container_id"], active_name, owner, network, config, data, db_identity)
        db = read_database(data, args.schedule_id, args.execution_id, args.script_id)
        if db.get("status") != "ok":
            raise SafeStop("local_database_readback_failed")
        if db_identity and (db.get("db_dev"), db.get("db_inode")) != (
                db_identity["db_dev"], db_identity["db_inode"]):
            raise SafeStop("db_identity_changed_between_proc_and_sqlite_read")
        if ((args.expected_node_id and db.get("identity", {}).get("node_id") != args.expected_node_id)
                or (args.expected_agent_id and db.get("identity", {}).get("agent_id") != args.expected_agent_id)):
            raise SafeStop("local_identity_does_not_match_hub_node")
        if args.schedule_id and not db.get("schedule"):
            raise SafeStop("owned_schedule_missing_from_local_cache")
        if args.schedule_id and db["schedule"].get("enabled") is not False:
            raise SafeStop("owned_schedule_not_disabled_in_local_cache")
        if args.script_id:
            script = db.get("script") or {}
            if (script.get("id") != args.script_id or script.get("content_sha256") != script.get("stored_sha256")
                    or not isinstance(script.get("content_bytes"), int)
                    or script.get("revision") != args.script_revision
                    or (args.script_sha256 and script.get("content_sha256") != args.script_sha256)):
                raise SafeStop("owned_script_cache_sha_or_identity_mismatch")
        if args.execution_id:
            execution = db.get("execution") or {}
            if (execution.get("id") != args.execution_id or execution.get("status") != "SUCCESS"
                    or execution.get("exit_code") != 0 or execution.get("script_id") != args.script_id
                    or execution.get("script_revision") != (db.get("script") or {}).get("revision")
                    or (args.task_id and execution.get("task_id") != args.task_id)):
                raise SafeStop("owned_script_execution_journal_mismatch")
        pre_b = ledger.get("pre_b_local_persistence")
        if marker.get("container_b_id") and pre_b:
            require_no_regression(pre_b, db)
        if active_key == "container_a_id" and not pre_b:
            ledger["inspect_a_local_persistence"] = db
            atomic_owner(root, marker, ledger)
        if active:
            confirm_db = read_database(data, args.schedule_id, args.execution_id, args.script_id)
            require_no_regression(db, confirm_db)
            process_after = proc_snapshot(process_before["pid"], active["container_id"],
                                          db["db_dev"], db["db_inode"])
            if process_before["start_ticks"] != process_after["start_ticks"]:
                raise SafeStop("agent_process_changed_during_sqlite_readback")
        else:
            confirm_db = db
        if (args.hub_desired_revision >= 0 and db.get("status") == "ok"
                and db.get("global_revision", -1) < args.hub_desired_revision):
            raise SafeStop("local_revision_below_sampled_hub_desired_revision")
        stopped_b = None
        db_after_stop = None
        if args.stop_after_inspection:
            cid = marker.get("container_b_id")
            if not cid:
                raise SafeStop("stop_after_inspection_requires_owned_container_b")
            info = inspect_container(cid, cb, owner, network, config, data)
            if not info.get("running") or not info.get("pid"):
                raise SafeStop("owned_container_b_not_running_before_final_stop")
            ledger["verified_local_database_before_final_stop"] = db
            ledger.update({"phase": "STOPPING_CONTAINER_B_AFTER_INSPECTION", "container_b_stop_id": cid})
            atomic_owner(root, marker, ledger)
            rc, _, _ = docker(["stop", "--time", "10", cid], timeout=20)
            if rc:
                raise SafeStop("exact_owned_container_b_stop_failed")
            stopped_b = inspect_container(cid, cb, owner, network, config, data)
            if stopped_b.get("running") or stopped_b.get("pid") not in {0, None}:
                raise SafeStop("exact_owned_container_b_stop_readback_failed")
            db_after_stop = read_database(data, args.schedule_id, args.execution_id, args.script_id)
            require_no_regression(confirm_db, db_after_stop)
            ledger["container_b_stopped_after_inspection"] = stopped_b
            ledger["local_database_after_container_b_stop"] = db_after_stop
            ledger.update({"phase": "CONTAINER_B_STOPPED_AFTER_INSPECTION", "container_b_stop_id": cid})
            atomic_owner(root, marker, ledger)
        print(json.dumps({"status": "INSPECTION_COMPLETE", "owner": owner,
                          "sampled_hub_desired_revision": args.hub_desired_revision,
                          "containers": out, "local_database": db,
                          "agent_db_open_proof_before_read": process_before,
                          "agent_db_open_proof_after_read": process_after,
                          "container_b_stopped": stopped_b, "local_database_after_stop": db_after_stop,
                          "credentials_emitted": False},
                         separators=(",", ":")))
        return
    if args.phase == "recreate":
        cid = marker.get("container_a_id")
        if not cid:
            raise SafeStop("container_a_id_missing")
        old = inspect_container(cid, ca, owner, network, config, data)
        if not old.get("running") or not old.get("pid"):
            raise SafeStop("container_a_not_running_before_recreate")
        ledger.update({"phase": "STOPPING_CONTAINER_A", "container_a_stop_id": cid})
        atomic_owner(root, marker, ledger)
        rc, _, _ = docker(["stop", "--time", "10", cid], timeout=20)
        if rc:
            raise SafeStop("exact_container_a_stop_failed")
        stopped = inspect_container(cid, ca, owner, network, config, data)
        if stopped.get("running") or stopped.get("pid") not in {0, None}:
            raise SafeStop("container_a_stop_readback_failed")
        ledger.update({"phase": "CONTAINER_A_STOPPED", "container_a_stop_id": cid})
        atomic_owner(root, marker, ledger)
        ledger.update({"phase": "REMOVING_CONTAINER_A", "container_a_remove_id": cid})
        atomic_owner(root, marker, ledger)
        rc, _, _ = docker(["rm", cid], timeout=20)
        if rc:
            raise SafeStop("exact_container_a_remove_failed")
        exact_absent("container", cid)
        marker["container_a_removed_id"] = cid
        ledger.update({"phase": "CONTAINER_A_REMOVED", "container_a_removed_id": cid})
        pre_b_db = read_database(data, args.schedule_id, args.execution_id, args.script_id)
        if pre_b_db.get("status") != "ok":
            raise SafeStop("pre_b_database_snapshot_unavailable")
        if ledger.get("inspect_a_local_persistence"):
            require_no_regression(ledger["inspect_a_local_persistence"], pre_b_db)
        ledger["pre_b_local_persistence"] = pre_b_db
        atomic_owner(root, marker, ledger)
        new_id = create_container(cb, owner, network, config, data, root, marker, ledger, "recreate")
        marker["container_b_id"] = new_id
        ledger.update({"phase": "CONTAINER_B_CREATED", "container_b_id": new_id})
        atomic_owner(root, marker, ledger)
        new = inspect_container(new_id, cb, owner, network, config, data)
        if not new.get("running") or not new.get("pid"):
            raise SafeStop("container_b_not_running_after_start")
        db_identity = wait_database_file(data)
        process_open = prove_agent_opened_database(new_id, cb, owner, network, config, data,
                                                   db_identity)
        first_open_db = read_database(data, args.schedule_id, args.execution_id, args.script_id)
        if (first_open_db.get("status") != "ok"
                or (first_open_db.get("db_dev"), first_open_db.get("db_inode"))
                != (db_identity["db_dev"], db_identity["db_inode"])):
            raise SafeStop("b_database_changed_between_fd_and_sqlite_read")
        confirm_open_db = read_database(data, args.schedule_id, args.execution_id, args.script_id)
        require_no_regression(pre_b_db, confirm_open_db)
        process_after_read = proc_snapshot(process_open["pid"], new_id,
                                           confirm_open_db["db_dev"], confirm_open_db["db_inode"])
        if process_open["start_ticks"] != process_after_read["start_ticks"]:
            raise SafeStop("agent_process_changed_during_first_open_readback")
        ledger["container_b_first_open_local_persistence"] = confirm_open_db
        ledger["container_b_process_db_open_proof"] = process_open
        ledger.update({"phase": "CONTAINER_B_STARTED", "container_b": new})
        atomic_owner(root, marker, ledger)
        ledger.update({"phase": "CONTAINER_B_STARTED", "container_b": new})
        atomic_owner(root, marker, ledger)
        print(json.dumps({"status": "CONTAINER_B_STARTED", "owner": owner,
                          "container_a_removed_id": cid, "container_b": new,
                          "b_start_local_persistence": confirm_open_db,
                          "b_process_db_open_proof": process_open,
                          "b_process_db_open_proof_after_read": process_after_read,
                          "resource_ledger": ledger, "credentials_emitted": False}, separators=(",", ":")))
        return
    if args.phase == "cleanup-runtime":
        removed = []
        for key, expected_name in (("container_b_id", cb), ("container_a_id", ca)):
            cid = marker.get(key)
            if cid == marker.get("container_a_removed_id"):
                continue
            if not cid:
                cid = recover_container_by_name(expected_name, owner, network, config, data,
                                                root, marker, ledger)
            if not cid:
                continue
            irc, _, ierr = docker(["container", "inspect", cid])
            if irc:
                # A prior cleanup may have removed the exact ID before its ledger write.
                exact_absent("container", expected_name)
                marker[key] = None
                if key == "container_a_id":
                    marker["container_a_removed_id"] = cid
                removed.append(cid)
                ledger.update({"phase": "CONTAINER_ALREADY_ABSENT_DURING_RECOVERY",
                               "removed_container_ids": removed})
                atomic_owner(root, marker, ledger)
                continue
            info = inspect_container(cid, expected_name, owner, network, config, data)
            if info.get("running"):
                ledger.update({"phase": "STOPPING_DURING_CLEANUP", "cleanup_stop_id": cid})
                atomic_owner(root, marker, ledger)
                rc, _, _ = docker(["stop", "--time", "10", cid], timeout=20)
                if rc:
                    raise SafeStop("exact_owned_container_stop_failed")
                ledger.update({"phase": "STOPPED_DURING_CLEANUP", "cleanup_stop_id": cid})
                atomic_owner(root, marker, ledger)
            ledger.update({"phase": "REMOVING_DURING_CLEANUP", "cleanup_remove_id": cid})
            atomic_owner(root, marker, ledger)
            rc, _, _ = docker(["rm", cid], timeout=20)
            if rc:
                raise SafeStop("exact_owned_container_remove_failed")
            removed.append(cid)
            ledger.update({"phase": "CONTAINER_REMOVED_DURING_CLEANUP", "removed_container_ids": removed})
            marker[key] = None
            if key == "container_a_id":
                marker["container_a_removed_id"] = cid
            atomic_owner(root, marker, ledger)
        network_id = marker.get("network_id")
        if not network_id:
            network_id = recover_owned_network_id(network, owner)
            if network_id:
                marker["network_id"] = network_id
                ledger.update({"phase": "NETWORK_ID_RECOVERED_FOR_CLEANUP", "network_id": network_id})
                atomic_owner(root, marker, ledger)
        if network_id:
            rc, out, _ = docker(["network", "inspect", network_id])
            if rc:
                current_by_name = recover_owned_network_id(network, owner)
                if current_by_name is not None:
                    raise SafeStop("owned_network_id_mismatch_during_recovery")
                marker["network_id"] = None
                ledger.update({"phase": "NETWORK_ALREADY_ABSENT_DURING_RECOVERY",
                               "network_removed_id": network_id})
                atomic_owner(root, marker, ledger)
                network_id = None
            else:
                if recover_owned_network_id(network, owner) != network_id:
                    raise SafeStop("exact_owned_network_identity_mismatch")
                row = json.loads(out)[0]
                if row.get("Containers") or not isinstance(row.get("Containers"), dict):
                    raise SafeStop("exact_owned_network_guard_failed_or_attached")
                ledger.update({"phase": "REMOVING_OWNED_NETWORK", "network_remove_id": network_id})
                atomic_owner(root, marker, ledger)
                rc, _, _ = docker(["network", "rm", network_id])
                if rc:
                    raise SafeStop("exact_owned_network_remove_failed")
                marker["network_id"] = None
                ledger.update({"phase": "OWNED_NETWORK_REMOVED", "network_removed_id": network_id})
                atomic_owner(root, marker, ledger)
        ledger.update({"phase": "DOCKER_RESOURCES_REMOVED", "removed_container_ids": removed,
                       "network_removed_id": network_id})
        marker["container_a_id"] = None
        marker["container_b_id"] = None
        marker["network_id"] = None
        atomic_owner(root, marker, ledger)
        print(json.dumps({"status": "DOCKER_RESOURCES_REMOVED_ROOT_RETAINED", "owner": owner,
                          "removed_container_ids": removed, "network_removed_id": network_id,
                          "root": str(root), "credentials_emitted": False}, separators=(",", ":")))
        return
    if args.phase == "cleanup-root":
        if not args.hub_objects_deleted or marker.get("container_a_id") or marker.get("container_b_id") or marker.get("network_id"):
            raise SafeStop("root_cleanup_requires_hub_404_and_no_docker_resources")
        rs = root.lstat()
        if (not stat.S_ISDIR(rs.st_mode) or root.is_symlink()
                or (rs.st_dev, rs.st_ino, rs.st_uid, stat.S_IMODE(rs.st_mode))
                != (marker.get("root_dev"), marker.get("root_inode"), 0, 0o700)):
            raise SafeStop("exact_owned_root_guard_failed")
        private = root / "private"
        data = root / "data"
        if (not private.is_dir() or private.is_symlink() or private.stat().st_uid != 65534
                or stat.S_IMODE(private.stat().st_mode) != 0o700
                or not data.is_dir() or data.is_symlink() or data.stat().st_uid != 65534
                or stat.S_IMODE(data.stat().st_mode) != 0o700):
            raise SafeStop("owned_data_tree_guard_failed")
        # Never follow symlinks or remove an unexpected path; leave the root for manual review.
        owned_files = {"owner.json", "resource-ledger.json", "private/agent.yaml", "data/state.db",
                       "data/state.db-wal", "data/state.db-shm"}
        owned_dirs = {"private", "data", "data/scripts", "data/artifacts", "data/applications",
                      "data/executions", "data/logs", "data/tmp"}
        for parent, dirs, files in os.walk(root, topdown=True, followlinks=False):
            for name in dirs + files:
                if Path(parent, name).is_symlink():
                    raise SafeStop("symlink_found_in_owned_root_preserved")
                child = Path(parent, name)
                rel = child.relative_to(root).as_posix()
                allowed = (rel in {"owner.json", "resource-ledger.json", "private", "data",
                                   "private/agent.yaml", "data/state.db", "data/state.db-wal",
                                   "data/state.db-shm", "data/scripts", "data/artifacts",
                                   "data/applications", "data/executions", "data/logs", "data/tmp"})
                if not allowed:
                    raise SafeStop("unexpected_owned_root_path_preserved")
                child_st = child.lstat()
                if rel in owned_files and not stat.S_ISREG(child_st.st_mode):
                    raise SafeStop("special_owned_root_file_preserved")
                if rel in owned_dirs and not stat.S_ISDIR(child_st.st_mode):
                    raise SafeStop("special_owned_root_directory_preserved")
                if rel in owned_files:
                    expected_uid = 65534 if rel.startswith("private/") or rel.startswith("data/") else 0
                    if child_st.st_uid != expected_uid:
                        raise SafeStop("owned_root_file_owner_mismatch_preserved")
                if rel in owned_dirs and child_st.st_uid != 65534:
                    raise SafeStop("owned_root_subdirectory_owner_mismatch_preserved")
        for cache_dir in ("scripts", "artifacts", "applications", "executions", "logs", "tmp"):
            directory = data / cache_dir
            if not directory.is_dir() or directory.is_symlink() or list(directory.iterdir()):
                raise SafeStop("unexpected_data_subdirectory_content_preserved")
        shutil.rmtree(root)
        print(json.dumps({"status": "EXACT_OWNED_ROOT_REMOVED", "owner": owner,
                          "root_dev": rs.st_dev, "root_inode": rs.st_ino,
                          "hub_objects_deleted_guard": True, "credentials_emitted": False}, separators=(",", ":")))


try:
    main()
except SafeStop as exc:
    print(json.dumps({"status": "SAFE_STOP", "reason": str(exc), "partial_state": STATE,
                      "credentials_emitted": False}, separators=(",", ":")))
except Exception as exc:
    print(json.dumps({"status": "SAFE_STOP", "reason": "unexpected_" + type(exc).__name__,
                      "partial_state": STATE, "credentials_emitted": False}, separators=(",", ":")))
