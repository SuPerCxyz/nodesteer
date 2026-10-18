#!/usr/bin/env python3
"""Read-only Native2 SYS-12 preflight; never creates or changes remote resources."""
import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path

IMAGE = "sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44"
IMAGE_OWNER = "qa-resume-20261010-host-f174890c3846"
ROOT = Path("/opt/qa-resume-20261010-host-f174890c3846")
ARCHIVE = ROOT / "agent-rootfs.tar"
PRIVATE_CFG = ROOT / "private/host001-agent.yaml"
ARCHIVE_SHA = "d012a9301f242511460aa212608f8965879866ae6d64d8e2b8c40b7c0a0da490"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
SH_SHA = "a6f559e00b69a4aa9d8cb607be18d9386c5aee55c509e2c075549dcf00e00fc7"
NAMES = {
    "containers": ["qa-resume-20261010-sys12-a", "qa-resume-20261010-sys12-b"],
    "network": "nodesteer-qa-sys12-20261010",
    "volume": "nodesteer-qa-sys12-20261010-data",
}


def run(argv):
    p = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=12, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inspect_collision(kind, name):
    rc, out, err = run(["docker", kind, "inspect", name])
    if rc:
        missing = "no such" in err.lower()
        return {"collision": False if missing else None, "inspect_rc": rc,
                "safe_error": "not_found" if missing else "inspect_failed"}
    try:
        rows = json.loads(out)
        return {"collision": bool(rows), "immutable_id": rows[0].get("Id") if rows else None}
    except (ValueError, IndexError, AttributeError):
        return {"collision": None, "inspect_rc": rc, "parse_error": True}


def read_private_config_keys():
    keys = {k: False for k in ("hub_url", "registration_token", "node_name", "node_ip", "agent_id")}
    root_st = ROOT.lstat()
    root_ok = (stat.S_ISDIR(root_st.st_mode) and not ROOT.is_symlink()
               and ROOT.resolve() == ROOT
               and (root_st.st_dev, root_st.st_ino, root_st.st_uid,
                    stat.S_IMODE(root_st.st_mode)) == (65025, 70286, 0, 0o700))
    private_dir = PRIVATE_CFG.parent
    dir_st = private_dir.lstat()
    dir_ok = (stat.S_ISDIR(dir_st.st_mode) and not private_dir.is_symlink()
              and private_dir.resolve() == ROOT / "private"
              and private_dir.resolve().parent == ROOT.resolve()
              and dir_st.st_uid == 0 and stat.S_IMODE(dir_st.st_mode) == 0o700)
    if not (root_ok and dir_ok):
        return {"root_guard_pass": root_ok, "private_directory_guard_pass": dir_ok,
                "config_read": False, "field_present": keys, "values_emitted": False}
    st = PRIVATE_CFG.lstat()
    file_ok = (stat.S_ISREG(st.st_mode) and not PRIVATE_CFG.is_symlink()
               and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600
               and PRIVATE_CFG.resolve(strict=True).parent == private_dir.resolve())
    if not file_ok:
        return {"root_guard_pass": True, "private_directory_guard_pass": True,
                "config_guard_pass": False, "config_read": False,
                "field_present": keys, "values_emitted": False}
    fd = os.open(PRIVATE_CFG, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fs = os.fstat(fd)
        same = (fs.st_dev, fs.st_ino, fs.st_uid, stat.S_IMODE(fs.st_mode)) == (
            st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode))
        if not same:
            return {"root_guard_pass": True, "private_directory_guard_pass": True,
                    "config_guard_pass": False, "config_read": False,
                    "field_present": keys, "values_emitted": False}
        data = bytearray()
        while len(data) <= 1024 * 1024:
            block = os.read(fd, min(65536, 1024 * 1024 + 1 - len(data)))
            if not block:
                break
            data.extend(block)
    finally:
        os.close(fd)
    if len(data) > 1024 * 1024:
        return {"root_guard_pass": True, "private_directory_guard_pass": True,
                "config_guard_pass": False, "config_read": False,
                "field_present": keys, "values_emitted": False}
    raw = bytes(data)
    for key in keys:
        keys[key] = re.search(rb"(?m)^\s*" + key.encode() + rb"\s*:", raw) is not None
    return {"root_guard_pass": True, "private_directory_guard_pass": True,
            "config_guard_pass": True, "config_read": True, "uid": st.st_uid,
            "mode": oct(stat.S_IMODE(st.st_mode)), "size": st.st_size,
            "field_present": keys, "values_emitted": False}


def main():
    result = {"read_only": True, "writes_or_signals": False}
    rc, out, _ = run(["docker", "image", "inspect", IMAGE])
    if rc:
        result["image"] = {"status": "inspect_failed", "rc": rc}
    else:
        rows = json.loads(out)
        i = rows[0]
        cfg = i.get("Config") or {}
        labels = cfg.get("Labels") or {}
        result["image"] = {
            "status": "ok", "id": i.get("Id"), "os": i.get("Os"),
            "architecture": i.get("Architecture"), "size": i.get("Size"),
            "entrypoint": cfg.get("Entrypoint"), "cmd": cfg.get("Cmd"),
            "user": cfg.get("User"), "owner_label_matches":
                labels.get("nodesteer.e2e.owner") == IMAGE_OWNER,
        }

    result["name_collisions"] = {
        "containers": {n: inspect_collision("container", n) for n in NAMES["containers"]},
        "volume": inspect_collision("volume", NAMES["volume"]),
        "network": inspect_collision("network", NAMES["network"]),
    }

    try:
        root_st = ROOT.lstat()
        arc_st = ARCHIVE.lstat()
        safe_paths = (stat.S_ISDIR(root_st.st_mode) and not ROOT.is_symlink()
                      and (root_st.st_dev, root_st.st_ino, root_st.st_uid,
                           stat.S_IMODE(root_st.st_mode)) == (65025, 70286, 0, 0o700)
                      and stat.S_ISREG(arc_st.st_mode) and not ARCHIVE.is_symlink())
        arc_hash = digest(ARCHIVE) if safe_paths else None
        members = {}
        archive_ok = safe_paths and arc_hash == ARCHIVE_SHA
        if archive_ok:
            with tarfile.open(ARCHIVE, "r:*") as tf:
                names = set(tf.getnames())
                for target in ("bin/sh", "opt/nodesteer/nodesteer-agent", "var/lib/nodesteer"):
                    candidates = ("rootfs/" + target, target)
                    try:
                        name = next(x for x in candidates if x in names)
                        m = tf.getmember(name)
                        row = {"present": True, "regular": m.isfile(), "directory": m.isdir(),
                               "mode": oct(m.mode), "size": m.size,
                               "archive_name": name}
                        if m.isfile() and target == "bin/sh":
                            h = hashlib.sha256(tf.extractfile(m).read()).hexdigest()
                            row["sha256_matches_expected"] = h == SH_SHA
                        if m.isfile() and target == "opt/nodesteer/nodesteer-agent":
                            h = hashlib.sha256(tf.extractfile(m).read()).hexdigest()
                            row["sha256_matches_expected"] = h == AGENT_SHA
                        members[target] = row
                    except (KeyError, StopIteration):
                        members[target] = {"present": False}
        result["archive"] = {
            "path_guard_ok": safe_paths, "archive_hash_verified_before_member_read": archive_ok,
            "root_dev_inode": [root_st.st_dev, root_st.st_ino],
            "root_uid_mode": [root_st.st_uid, oct(stat.S_IMODE(root_st.st_mode))],
            "archive_size": arc_st.st_size, "sha256_matches_expected": arc_hash == ARCHIVE_SHA,
            "members": members,
        }
    except (OSError, tarfile.TarError):
        result["archive"] = {"status": "unavailable_or_invalid"}

    try:
        result["private_original_config"] = read_private_config_keys()
    except OSError:
        result["private_original_config"] = {"status": "unavailable", "config_read": False,
                                              "values_emitted": False}

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
