#!/usr/bin/env python3
"""Read-only follow-up to resolve the shell digest and one exact network inspect."""
import hashlib
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path

ROOT = Path("/opt/qa-resume-20261010-host-f174890c3846")
ARCHIVE = ROOT / "agent-rootfs.tar"
IMAGE = "sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44"
OWNER = "qa-resume-20261010-host-f174890c3846"
ARCHIVE_SHA = "d012a9301f242511460aa212608f8965879866ae6d64d8e2b8c40b7c0a0da490"
EXPECTED_SH_SHA = "a6f559e00b69a4aa9d8cb607be18d9386c5aee55c509e2c075549dcf00e00fc7"
NETWORK = "nodesteer-qa-sys12-20261010"


def run(argv):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=12, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def inspect_image():
    rc, out, _ = run(["docker", "image", "inspect", IMAGE])
    if rc:
        return {"status": "inspect_failed", "rc": rc}
    try:
        rows = json.loads(out)
        i = rows[0]
        labels = ((i.get("Config") or {}).get("Labels") or {})
        return {"status": "ok", "id_matches": i.get("Id") == IMAGE,
                "architecture": i.get("Architecture"), "owner_label_matches":
                labels.get("nodesteer.e2e.owner") == OWNER}
    except (ValueError, IndexError, AttributeError):
        return {"status": "invalid_inspect_json"}


def archive_shell():
    root_st = ROOT.lstat()
    root_ok = (stat.S_ISDIR(root_st.st_mode) and not ROOT.is_symlink() and ROOT.resolve() == ROOT
               and (root_st.st_dev, root_st.st_ino, root_st.st_uid,
                    stat.S_IMODE(root_st.st_mode)) == (65025, 70286, 0, 0o700))
    if not root_ok:
        return {"status": "root_guard_failed"}
    st = ARCHIVE.lstat()
    if not stat.S_ISREG(st.st_mode) or ARCHIVE.is_symlink():
        return {"status": "archive_not_regular"}
    fd = os.open(ARCHIVE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        fs = os.fstat(f.fileno())
        if (fs.st_dev, fs.st_ino, fs.st_uid) != (st.st_dev, st.st_ino, st.st_uid):
            return {"status": "archive_changed_during_open"}
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
        archive_sha = h.hexdigest()
        out = {"path_guard_pass": True, "dev": st.st_dev, "inode": st.st_ino,
               "uid": st.st_uid, "mode": format(stat.S_IMODE(st.st_mode), "04o"),
               "size": st.st_size, "sha256": archive_sha,
               "sha256_matches_expected": archive_sha == ARCHIVE_SHA}
        if archive_sha != ARCHIVE_SHA:
            out["status"] = "archive_sha_mismatch_member_read_skipped"
            return out
        f.seek(0)
        try:
            with tarfile.open(fileobj=f, mode="r:*") as tf:
                names = set(tf.getnames())
                members = []
                for name in ("rootfs/bin/sh", "bin/sh"):
                    if name not in names:
                        continue
                    m = tf.getmember(name)
                    entry = {"archive_name": name, "regular": m.isfile(), "mode": oct(m.mode),
                             "size": m.size}
                    if m.isfile():
                        data = tf.extractfile(m).read()
                        entry["actual_sha256"] = hashlib.sha256(data).hexdigest()
                        entry["matches_proposal_sha256"] = entry["actual_sha256"] == EXPECTED_SH_SHA
                    members.append(entry)
                out.update({"status": "archive_members_read_after_sha_pass", "shell_entries": members})
                return out
        except tarfile.TarError:
            return {**out, "status": "archive_parse_failed"}


def exact_network_inspect():
    rc, out, err = run(["docker", "network", "inspect", NETWORK])
    if rc == 0:
        try:
            rows = json.loads(out)
            if len(rows) != 1:
                return {"status": "unexpected_inspect_cardinality", "collision": None}
            row = rows[0]
            return {"status": "exists", "collision": True,
                    "id": row.get("Id"), "name_matches": row.get("Name") == NETWORK,
                    "owner_label_matches": ((row.get("Labels") or {}).get("nodesteer.e2e.owner") == OWNER)}
        except (ValueError, IndexError, AttributeError):
            return {"status": "inspect_json_invalid", "collision": None}
    low = err.lower()
    exact_missing = ("no such network: " + NETWORK.lower()) in low or (
        "network " + NETWORK.lower() + " not found") in low or (
        "network " + NETWORK.lower() + ": not found") in low
    if exact_missing:
        return {"status": "not_found", "collision": False, "rc": rc,
                "basis": "exact_network_name_not_found_error"}
    # Fall back to an exact-name filter only. A successful empty result means absent;
    # daemon/query errors and non-exact rows remain unknown.
    rc, listed, _ = run(["docker", "network", "ls", "--filter", "name=^" + NETWORK + "$",
                         "--format", "{{.Name}}"])
    if rc != 0:
        return {"status": "inspect_failed_unknown", "collision": None, "rc": rc}
    names = [x for x in listed.splitlines() if x]
    if names == []:
        return {"status": "not_found", "collision": False, "basis": "exact_filtered_list_empty"}
    if names == [NETWORK]:
        return {"status": "exists", "collision": True, "basis": "exact_filtered_list_match"}
    return {"status": "inspect_failed_unknown", "collision": None,
            "basis": "filtered_list_unexpected_rows"}


print(json.dumps({"read_only": True, "writes_or_signals": False,
                  "image": inspect_image(), "archive_shell": archive_shell(),
                  "exact_network": exact_network_inspect()}, sort_keys=True, separators=(",", ":")))
