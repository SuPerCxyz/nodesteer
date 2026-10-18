#!/usr/bin/env python3
"""Switch only this run's isolated Hub/Native Agents to frozen static artifacts."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import time
import urllib.error
import urllib.request

RUN = "20260930-101945"
ROOT = Path(__file__).resolve().parents[5]
PRIVATE = Path(f"/tmp/nodesteer-e2e-{RUN}-repair")
CONNECTION = PRIVATE / "repair-connection.json"
SOURCE = Path(f"/tmp/nodesteer-e2e-{RUN}-7wksl0_x/connection.json")
MANIFEST = ROOT / ".e2e/runs" / RUN / "repair/build-manifest.json"
REPORT = ROOT / ".e2e/runs" / RUN / "repair/environment/static-cutover.json"
HEALTH = ROOT / ".e2e/runs" / RUN / "repair/qa/static-health.json"
HUB_URL = "http://192.168.100.209:18080"
PRIMARY_URL = "http://192.168.100.209:8080"
LABEL = "dddee4f0-e2e-repair-20261004"
OLD_HUB_SHA = "395f4b76fcf41d8cc5f827cd38f564fa80e87c4cab0fc1193a529f54bce1567e"
OLD_AGENT_SHA = "d54faba1c6a8989ca3ab5a45a3d45b5d1bbcd1659862c074ea362ef18bf83124"
HUB_PATH = f"/opt/nodesteer-repair-{RUN}/bin/nodesteer-hub"
AGENT_PATH = f"/opt/nodesteer-repair-{RUN}/agent/nodesteer-agent"
HUB_UNIT = f"nodesteer-hub-repair-{RUN}.service"
IMAGE_TAG = f"nodesteer/agent:repair-{RUN}-static-cgo0"
BASE_IMAGE = f"qa-{RUN}-env-agent:dddee4f0"


def run(cmd, *, input_text=None, timeout=90):
    p = subprocess.run(cmd, input=input_text, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"command failed ({p.returncode}): {(p.stderr or p.stdout)[-300:]}")
    return p.stdout.strip()


def ssh(target, port, key, script, timeout=90):
    return run(["ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "LogLevel=ERROR", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-i", os.path.expanduser(key), "-p", str(port), target, "sh", "-s"], input_text=script, timeout=timeout)


def scp(local, target, port, key, remote):
    run(["scp", "-F", "/dev/null", "-B", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "LogLevel=ERROR", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-i", os.path.expanduser(key), "-P", str(port), str(local), f"{target}:{remote}"])


def http(url, method="GET", data=None, token=None):
    raw = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json"} if raw is not None else {}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    except urllib.error.URLError:
        return 0, None
    try:
        return status, json.loads(body) if body else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, None


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if PRIVATE.stat().st_mode & 0o777 != 0o700 or CONNECTION.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError("private repair credentials have unsafe permissions")
    if REPORT.exists() or HEALTH.exists():
        raise RuntimeError("refusing to overwrite static cutover evidence")
    build = json.loads(MANIFEST.read_text())
    if build.get("build_label") != LABEL:
        raise RuntimeError("manifest build label mismatch")
    rows = {x.get("name"): x for x in build.get("artifacts", [])}
    hub_row, agent_row = rows.get("nodesteer-hub-static"), rows.get("nodesteer-agent-static")
    if not hub_row or not agent_row:
        raise RuntimeError("static artifacts missing from frozen manifest")
    for row in (hub_row, agent_row):
        if row.get("cgo_enabled") is not False or row.get("architecture") != "amd64":
            raise RuntimeError("static artifact architecture/CGO gate failed")
        artifact = Path(row["path"])
        if not artifact.is_file() or artifact.stat().st_size != row["size"] or sha(artifact) != row["sha256"]:
            raise RuntimeError(f"frozen artifact mismatch: {row['name']}")
    conn = json.loads(CONNECTION.read_text())
    source = json.loads(SOURCE.read_text())
    hub = source["hub"]
    nodes = conn["nodes"]
    source_nodes = {n["ip"]: n for n in source["nodes"]}
    for n in nodes:
        n["ssh_identity_file"] = source_nodes[n["ssh_ip"]]["ssh_identity_file"]
        n["service"] = n.get("service", n.get("unit"))
    if len(nodes) != 4 or not conn.get("token"):
        raise RuntimeError("repair authority connection invalid")
    if conn.get("hub_url") != HUB_URL:
        raise RuntimeError("refusing authority URL outside isolated :18080")

    hub_old = ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], "\n".join([
        "set -eu", f"test \"$(systemctl show {shlex.quote(HUB_UNIT)} -p ActiveState --value)\" = active",
        f"test \"$(sha256sum {shlex.quote(HUB_PATH)} | cut -d ' ' -f1)\" = {OLD_HUB_SHA}",
        f"test ! -e {shlex.quote(HUB_PATH + '.static-cgo0-stage')}",
        f"test ! -e {shlex.quote(HUB_PATH + '.dynamic-cgo1.bak')}",
        "test \"$(systemctl show nodesteer-hub.service -p ActiveState --value)\" = active",
        "test \"$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/healthz)\" = 200", "" ]))
    native_before = []
    for n in nodes:
        state = ssh(n["ssh_target"], n["ssh_port"], n["ssh_identity_file"], "\n".join([
            "set -eu", f"test \"$(systemctl show {shlex.quote(n['service'])} -p ActiveState --value)\" = active",
            f"test \"$(sha256sum {shlex.quote(AGENT_PATH)} | cut -d ' ' -f1)\" = {OLD_AGENT_SHA}",
            f"test ! -e {shlex.quote(AGENT_PATH + '.static-cgo0-stage')}",
            f"test ! -e {shlex.quote(AGENT_PATH + '.dynamic-cgo1.bak')}", "" ]))
        native_before.append({"name": n["name"], "ssh_ip": n["ssh_ip"], "preflight": "owned_active_old_sha_verified"})

    # Stage immutable siblings first; only replace owned executable paths after all SHAs validate.
    hub_stage = HUB_PATH + ".static-cgo0-stage"
    agent_stage = AGENT_PATH + ".static-cgo0-stage"
    scp(Path(hub_row["path"]), hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], hub_stage)
    hub_dir = str(Path(HUB_PATH).parent)
    agent_dir = str(Path(AGENT_PATH).parent)
    ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], "\n".join([
        "set -eu", f"chmod 0755 {shlex.quote(hub_stage)}",
        f"test \"$(stat -c %s {shlex.quote(hub_stage)})\" = {hub_row['size']}",
        f"test \"$(sha256sum {shlex.quote(hub_stage)} | cut -d ' ' -f1)\" = {hub_row['sha256']}", "" ]))
    for n in nodes:
        scp(Path(agent_row["path"]), n["ssh_target"], n["ssh_port"], n["ssh_identity_file"], agent_stage)
        ssh(n["ssh_target"], n["ssh_port"], n["ssh_identity_file"], "\n".join([
            "set -eu", f"chmod 0755 {shlex.quote(agent_stage)}",
            f"test \"$(stat -c %s {shlex.quote(agent_stage)})\" = {agent_row['size']}",
            f"test \"$(sha256sum {shlex.quote(agent_stage)} | cut -d ' ' -f1)\" = {agent_row['sha256']}", "" ]))

    # Create a unique static image without replacing the old dynamic image tag.
    base_id = ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"],
                  f"docker image inspect {shlex.quote(BASE_IMAGE)} --format '{{{{.Id}}}}'")
    ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"],
        f"set -eu; if docker image inspect {shlex.quote(IMAGE_TAG)} >/dev/null 2>&1; then exit 41; fi")
    docker_dir = f"/opt/nodesteer-repair-{RUN}/agent-static"
    dockerfile = Path(__file__).with_name("agent-repair.Dockerfile")
    ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"],
        f"set -eu; test ! -e {shlex.quote(docker_dir)}; mkdir -m 0755 {shlex.quote(docker_dir)}")
    scp(Path(agent_row["path"]), hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], docker_dir + "/nodesteer-agent")
    scp(dockerfile, hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], docker_dir + "/Dockerfile")
    docker_build = "\n".join([
        "set -eu", f"chmod 0755 {shlex.quote(docker_dir + '/nodesteer-agent')}",
        f"test \"$(sha256sum {shlex.quote(docker_dir + '/nodesteer-agent')} | cut -d ' ' -f1)\" = {agent_row['sha256']}",
        f"docker build --network=none --build-arg REPAIR_BASE_IMAGE={shlex.quote(BASE_IMAGE)} "
        f"--build-arg REPAIR_AGENT_SHA={agent_row['sha256']} --build-arg REPAIR_BUILD_LABEL={LABEL} "
        f"--tag {shlex.quote(IMAGE_TAG)} --file {shlex.quote(docker_dir + '/Dockerfile')} {shlex.quote(docker_dir)}",
        "docker image inspect " + shlex.quote(IMAGE_TAG) + " --format '{{.Id}}|{{index .Config.Labels \"org.nodesteer.repair.agent-sha256\"}}|{{index .Config.Labels \"org.nodesteer.repair.build-label\"}}'", "" ])
    image_row = ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], docker_build, timeout=240).splitlines()[-1]
    image_id, image_sha, image_label = image_row.split("|", 2)
    if image_sha != agent_row["sha256"] or image_label != LABEL:
        raise RuntimeError("static Agent image identity mismatch")

    # Keep the old dynamic executables as exact recovery copies, then atomically install static siblings.
    hub_backup, agent_backup = HUB_PATH + ".dynamic-cgo1.bak", AGENT_PATH + ".dynamic-cgo1.bak"
    hub_switch = "\n".join(["set -eu", f"test ! -e {shlex.quote(hub_backup)}",
        f"cp -p {shlex.quote(HUB_PATH)} {shlex.quote(hub_backup)}",
        f"test \"$(sha256sum {shlex.quote(hub_backup)} | cut -d ' ' -f1)\" = {OLD_HUB_SHA}",
        f"mv -f {shlex.quote(hub_stage)} {shlex.quote(HUB_PATH)}",
        f"test \"$(sha256sum {shlex.quote(HUB_PATH)} | cut -d ' ' -f1)\" = {hub_row['sha256']}",
        f"systemctl restart {shlex.quote(HUB_UNIT)}", ""])
    ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], hub_switch, timeout=90)

    deadline = time.monotonic() + 45
    hz = rz = 0
    while time.monotonic() < deadline:
        hz, _ = http(HUB_URL + "/healthz")
        rz, _ = http(HUB_URL + "/readyz")
        if hz == 200 and rz == 200:
            break
        time.sleep(1)
    if hz != 200 or rz != 200:
        raise RuntimeError(f"static Hub health gate failed: {hz}/{rz}")
    login_code, login = http(HUB_URL + "/api/login", "POST", {"username": conn["admin_username"], "password": conn["admin_password"]})
    if login_code != 200 or not isinstance(login, dict) or not login.get("token"):
        raise RuntimeError(f"fresh admin login failed: HTTP {login_code}")
    token = login["token"]
    me_code, me = http(HUB_URL + "/api/me", token=token)
    role = (me.get("role") or me.get("Role")) if isinstance(me, dict) else None
    if me_code != 200 or role != "administrator":
        raise RuntimeError(f"repair admin /api/me failed: HTTP {me_code}")
    conn["token"] = token
    temp = PRIVATE / ".repair-connection-static.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conn, f, ensure_ascii=False, indent=2); f.write("\n")
    os.replace(temp, CONNECTION); CONNECTION.chmod(0o600)

    for n in nodes:
        script = "\n".join(["set -eu", f"test ! -e {shlex.quote(agent_backup)}",
            f"cp -p {shlex.quote(AGENT_PATH)} {shlex.quote(agent_backup)}",
            f"test \"$(sha256sum {shlex.quote(agent_backup)} | cut -d ' ' -f1)\" = {OLD_AGENT_SHA}",
            f"mv -f {shlex.quote(agent_stage)} {shlex.quote(AGENT_PATH)}",
            f"test \"$(sha256sum {shlex.quote(AGENT_PATH)} | cut -d ' ' -f1)\" = {agent_row['sha256']}",
            f"systemctl restart {shlex.quote(n['service'])}",
            f"test \"$(systemctl show {shlex.quote(n['service'])} -p ActiveState --value)\" = active", ""])
        ssh(n["ssh_target"], n["ssh_port"], n["ssh_identity_file"], script, timeout=90)

    old_health = json.loads((ROOT / ".e2e/runs" / RUN / "repair/qa/initial-health.json").read_text())
    old_ids = {a["node_id"] for a in old_health["agents"]}
    nodes_status = []
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        code, data = http(HUB_URL + "/api/nodes", token=token)
        if code == 200 and isinstance(data, list):
            by_name = {x.get("hostname", x.get("name")): x for x in data}
            nodes_status = [by_name.get(n["name"]) for n in nodes]
            if all(x and x.get("status") == "online" and x.get("sync_status") == "synced"
                   and x.get("agent_version") == LABEL for x in nodes_status):
                break
        time.sleep(1)
    if len(nodes_status) != 4 or any(not x for x in nodes_status):
        raise RuntimeError("four repair Agents did not reappear")
    ids = {x.get("id") for x in nodes_status}
    if len(ids) != 4 or ids != old_ids:
        raise RuntimeError("static restart changed or duplicated repair Node IDs")
    if any(x.get("status") != "online" or x.get("sync_status") != "synced" or x.get("agent_version") != LABEL for x in nodes_status):
        raise RuntimeError("static Agent health/version gate failed")
    proc = ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"],
        f"set -eu; p=$(systemctl show {shlex.quote(HUB_UNIT)} -p MainPID --value); test \"$p\" -gt 1; "
        f"test \"$(sha256sum /proc/$p/exe | cut -d ' ' -f1)\" = {hub_row['sha256']}; echo hub_pid=$p")
    agent_procs = []
    for n in nodes:
        result = ssh(n["ssh_target"], n["ssh_port"], n["ssh_identity_file"],
            f"set -eu; p=$(systemctl show {shlex.quote(n['service'])} -p MainPID --value); test \"$p\" -gt 1; "
            f"test \"$(sha256sum /proc/$p/exe | cut -d ' ' -f1)\" = {agent_row['sha256']}; echo pid=$p")
        agent_procs.append({"name": n["name"], "process_exe_sha_verified": True, "pid_report": result})
    primary_health, _ = http(PRIMARY_URL + "/healthz")
    primary_ready, _ = http(PRIMARY_URL + "/readyz")
    primary_code, primary_nodes = http(PRIMARY_URL + "/api/nodes", token=source.get("token"))
    health = {"run_id": RUN, "status": "static_cutover_healthy", "build_label": LABEL,
        "hub": {"sha256": hub_row["sha256"], "healthz_http": hz, "readyz_http": rz,
                "admin_login_http": login_code, "me_http": me_code, "me_role": role, "process_exe_sha_verified": True},
        "agents": [{"name": n["name"], "id": x.get("id"), "status": x.get("status"),
                    "sync_status": x.get("sync_status"), "agent_version": x.get("agent_version"),
                    "process_exe_sha_verified": True} for n, x in zip(nodes, nodes_status)],
        "ids_unchanged": ids == old_ids, "primary_get_only": {"healthz_http": primary_health,
            "readyz_http": primary_ready, "nodes_http": primary_code,
            "node_count": len(primary_nodes) if isinstance(primary_nodes, list) else None},
        "secrets_included": False, "old_dynamic_binaries_preserved": True,
        "static_agent_image": {"tag": IMAGE_TAG, "image_id": image_id, "agent_sha256": image_sha}}
    HEALTH.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n")
    REPORT.write_text(json.dumps({"run_id": RUN, "status": "static_cutover_healthy",
        "manifest": str(MANIFEST.relative_to(ROOT)), "hub_sha256": hub_row["sha256"],
        "agent_sha256": agent_row["sha256"], "hub_dynamic_backup": hub_backup,
        "agent_dynamic_backup": agent_backup, "agent_image_tag": IMAGE_TAG,
        "agent_image_id": image_id, "repair_node_ids_preserved": True,
        "original_services_touched": False, "private_connection": str(CONNECTION),
        "health_report": str(HEALTH.relative_to(ROOT))}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "static_cutover_healthy", "hub_http": [hz, rz],
        "admin": [login_code, me_code, role], "native_agents": len(ids),
        "ids_preserved": True, "primary_get": [primary_health, primary_ready, primary_code],
        "agent_image_tag": IMAGE_TAG, "hub_process_sha_verified": True,
        "agent_process_sha_verified": True, "pid_check": proc}))


if __name__ == "__main__":
    main()
