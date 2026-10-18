#!/usr/bin/env python3
"""Install and start only the unique repair Hub and four fresh Native Agents."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

PUBLIC = Path(__file__).resolve().parent
ROOT = PUBLIC.parents[4]
RUN = "20260930-101945"
PREFIX = "nodesteer-repair-" + RUN
PRIVATE = Path("/tmp/nodesteer-e2e-" + RUN + "-repair")
SOURCE = Path("/tmp/nodesteer-e2e-" + RUN + "-7wksl0_x/connection.json")
HUB_BIN = Path("/tmp/nodesteer-e2e-" + RUN + "-7wksl0_x/repair-build/nodesteer-hub")
MANIFEST = ROOT / ".e2e/runs" / RUN / "repair/build-manifest.json"
HUB_SHA = "395f4b76fcf41d8cc5f827cd38f564fa80e87c4cab0fc1193a529f54bce1567e"
HUB_SIZE = 55132094
AGENT_SHA = "d54faba1c6a8989ca3ab5a45a3d45b5d1bbcd1659862c074ea362ef18bf83124"
AGENT_VERSION = "dddee4f0-e2e-repair-20261004"
HUB_URL = "http://192.168.100.209:18080"
WS_URL = "ws://192.168.100.209:18080"
HUB_CONFIG = f"/etc/nodesteer/hub-repair-{RUN}.yaml"
HUB_DATA = f"/var/lib/nodesteer-hub-repair-{RUN}"
HUB_UNIT = f"nodesteer-hub-repair-{RUN}.service"
HUB_UNIT_PATH = f"/etc/systemd/system/{HUB_UNIT}"
RROOT = f"/opt/{PREFIX}"
HUB_BIN_REMOTE = RROOT + "/bin/nodesteer-hub"
AGENT_BIN_REMOTE = RROOT + "/agent/nodesteer-agent"
CONNECTION = PRIVATE / "repair-connection.json"
HEALTH = ROOT / ".e2e/runs" / RUN / "repair/qa/initial-health.json"
REPORT = PUBLIC / "deployment-result.json"
EXPECTED_IPS = {"192.168.100.207", "192.168.100.222", "192.168.100.204", "192.168.100.247"}
PRIMARY_URL = "http://192.168.100.209:8080"


def run(cmd, *, input_text=None, timeout=60):
    p = subprocess.run(cmd, input=input_text, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "command failed")[-500:])
    return p.stdout.strip()


def ssh(target, port, key, body, timeout=60):
    return run([
        "ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-i", os.path.expanduser(key), "-p", str(port), target, "sh", "-s",
    ], input_text=body, timeout=timeout)


def scp(local, target, port, key, dest):
    run([
        "scp", "-F", "/dev/null", "-B", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-i", os.path.expanduser(key), "-P", str(port), str(local), target + ":" + dest,
    ])


def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    path.chmod(0o600)


def y(key, value):
    return f"{key}: {json.dumps(value, ensure_ascii=False)}"


def api(url, method="GET", body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None


def main():
    if not PRIVATE.is_dir() or stat.S_IMODE(PRIVATE.stat().st_mode) != 0o700:
        raise RuntimeError("repair private directory must be mode 0700")
    if any(p.exists() for p in (CONNECTION, HEALTH, REPORT, PRIVATE / "repair-hub.yaml")):
        raise RuntimeError("refusing to overwrite existing repair state")
    if not HUB_BIN.is_file() or HUB_BIN.stat().st_size != HUB_SIZE:
        raise RuntimeError("Hub binary missing or size mismatch")
    if hashlib.sha256(HUB_BIN.read_bytes()).hexdigest() != HUB_SHA:
        raise RuntimeError("Hub SHA mismatch")
    manifest = json.loads(MANIFEST.read_text())
    hub_row = next((a for a in manifest.get("artifacts", []) if a.get("name") == "nodesteer-hub"), None)
    if not hub_row or (hub_row.get("sha256"), hub_row.get("size")) != (HUB_SHA, HUB_SIZE):
        raise RuntimeError("Hub manifest mismatch")

    old = json.loads(SOURCE.read_text())
    hub, nodes = old["hub"], sorted(old["nodes"], key=lambda x: x["ip"])
    if hub.get("ip") != "192.168.100.209" or len(nodes) != 4 or {n.get("ip") for n in nodes} != EXPECTED_IPS:
        raise RuntimeError("private transport inventory differs from authorized scope")
    original = json.loads((ROOT / ".e2e/runs" / RUN / "main/current-health.json").read_text())
    old_ids = {x["id"] for x in original["nodes"]}
    native = []
    for i, n in enumerate(nodes, 1):
        name = f"qa-repair-{RUN}-native-{i}"
        native.append({
            "name": name, "node_ip_alias": name + ".invalid", "ssh_ip": n["ip"],
            "ssh_target": n["ssh_target"], "ssh_port": n["ssh_port"], "key": n["ssh_identity_file"],
            "config": f"/etc/nodesteer/repair-{RUN}/native-{i}.yaml",
            "data": f"/var/lib/{PREFIX}/native-{i}",
            "unit": f"nodesteer-agent-repair-{RUN}-native-{i}.service",
        })

    # Read-only collision and original-service preflight on all five VMs.
    baseline = {"hub": None, "agents": []}
    hub_check = "\n".join([
        "set -eu",
        "test \"$(systemctl show nodesteer-hub.service -p ActiveState --value)\" = active",
        f"test ! -e {shlex.quote(HUB_BIN_REMOTE)}", f"test ! -e {shlex.quote(HUB_CONFIG)}",
        f"test ! -e {shlex.quote(HUB_DATA)}", f"test ! -e {shlex.quote(HUB_UNIT_PATH)}",
        f"load=$(systemctl show {shlex.quote(HUB_UNIT)} -p LoadState --value 2>/dev/null || true)",
        'test -z "$load" || test "$load" = not-found',
        "if ss -H -lnt | awk '{print $4}' | grep -Eq ':18080$'; then exit 41; fi",
        "printf 'pid='; systemctl show nodesteer-hub.service -p MainPID --value", "",
    ])
    hub_before = ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], hub_check)
    baseline["hub"] = {"ip": hub["ip"], "pid": hub_before.split("=")[-1], "state": "active"}
    for n, r in zip(nodes, native):
        check = "\n".join([
            "set -eu",
            "test \"$(systemctl show nodesteer-agent.service -p ActiveState --value)\" = active",
            f"test ! -e {shlex.quote(r['config'])}", f"test ! -e {shlex.quote(r['data'])}",
            f"test ! -e /etc/systemd/system/{r['unit']}",
            f"test \"$(sha256sum {shlex.quote(AGENT_BIN_REMOTE)} | cut -d ' ' -f1)\" = {AGENT_SHA}",
            "printf 'pid='; systemctl show nodesteer-agent.service -p MainPID --value", "",
        ])
        before = ssh(n["ssh_target"], n["ssh_port"], n["ssh_identity_file"], check)
        baseline["agents"].append({"ip": n["ip"], "pid": before.split("=")[-1], "state": "active"})

    # Fresh authority credentials are generated locally and never printed.
    admin_user = f"qa-repair-{RUN}-admin"
    admin_password = secrets.token_urlsafe(32)
    registration_token = secrets.token_urlsafe(32)
    private_write(PRIVATE / "repair-hub.yaml", "\n".join([
        y("web_addr", ":18080"), y("gateway_addr", ":18080"), y("base_url", HUB_URL),
        y("registration_token", registration_token), y("data_dir", HUB_DATA),
        y("artifact_dir", HUB_DATA + "/artifacts"), y("admin_username", admin_user),
        y("admin_password", admin_password), y("session_ttl", "24h"), "",
    ]))
    connection = {
        "run_id": RUN, "base_url": HUB_URL, "hub_url": HUB_URL,
        "admin_username": admin_user, "admin_password": admin_password,
        "registration_token": registration_token, "token": "",
        "nodes": [{k: r[k] for k in ("name", "node_ip_alias", "ssh_ip", "ssh_target",
                                     "ssh_port", "config", "data", "unit")} for r in native],
    }
    private_write(CONNECTION, json.dumps(connection, ensure_ascii=False, indent=2) + "\n")
    for r in native:
        private_write(PRIVATE / (r["name"] + ".yaml"), "\n".join([
            y("hub_url", WS_URL), y("registration_token", registration_token),
            y("node_name", r["name"]), y("node_ip", r["node_ip_alias"]),
            y("deployment_mode", "native"), "host_integration: false", y("data_dir", r["data"]), "",
        ]))

    hub_unit_text = "\n".join([
        "[Unit]", f"Description=NodeSteer repair E2E Hub {RUN}",
        "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=simple", "User=nodesteer", "Group=nodesteer",
        f"ExecStart={RROOT}/bin/nodesteer-hub --config {HUB_CONFIG}",
        "Restart=on-failure", "RestartSec=3", "NoNewPrivileges=true",
        "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true",
        f"ReadWritePaths={HUB_DATA}", "", "[Install]", "WantedBy=multi-user.target", "",
    ])
    local_hub_unit = PRIVATE / HUB_UNIT
    private_write(local_hub_unit, hub_unit_text)
    started = []
    try:
        ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], "\n".join([
            "set -eu", f"mkdir -m 0755 {shlex.quote(RROOT + '/bin')}",
            f"install -d -m 0750 -o nodesteer -g nodesteer {shlex.quote(HUB_DATA)}",
            f"install -d -m 0750 -o nodesteer -g nodesteer {shlex.quote(HUB_DATA + '/artifacts')}", "",
        ]))
        scp(HUB_BIN, hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], HUB_BIN_REMOTE)
        scp(PRIVATE / "repair-hub.yaml", hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], HUB_CONFIG)
        scp(local_hub_unit, hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], HUB_UNIT_PATH)
        ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], "\n".join([
            "set -eu", f"chmod 0755 {shlex.quote(HUB_BIN_REMOTE)}",
            f"test \"$(sha256sum {shlex.quote(HUB_BIN_REMOTE)} | cut -d ' ' -f1)\" = {HUB_SHA}",
            f"chown nodesteer:nodesteer {shlex.quote(HUB_CONFIG)}", f"chmod 0600 {shlex.quote(HUB_CONFIG)}",
            f"chmod 0644 {shlex.quote(HUB_UNIT_PATH)}", "systemctl daemon-reload",
            f"systemctl start {shlex.quote(HUB_UNIT)}",
            f"test \"$(systemctl show {shlex.quote(HUB_UNIT)} -p ActiveState --value)\" = active", "",
        ]), timeout=45)
        started.append((hub, HUB_UNIT))

        deadline = time.monotonic() + 30
        healthz = readyz = 0
        while time.monotonic() < deadline:
            healthz, _ = api(HUB_URL + "/healthz")
            readyz, _ = api(HUB_URL + "/readyz")
            if healthz == 200 and readyz == 200:
                break
            time.sleep(1)
        if healthz != 200 or readyz != 200:
            raise RuntimeError(f"repair Hub health gate failed: {healthz}/{readyz}")

        login_status, login = api(HUB_URL + "/api/login", "POST",
                                  {"username": admin_user, "password": admin_password})
        if login_status != 200 or not isinstance(login, dict) or not login.get("token"):
            raise RuntimeError(f"repair Hub fresh admin login failed: HTTP {login_status}")
        token = login["token"]
        me_status, me = api(HUB_URL + "/api/me", token=token)
        role = me.get("role", me.get("Role")) if isinstance(me, dict) else None
        if me_status != 200 or role != "administrator":
            raise RuntimeError(f"repair Hub /api/me gate failed: HTTP {me_status}, role={role}")
        connection["token"] = token
        tmp = PRIVATE / ".repair-connection.json.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(connection, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, CONNECTION)
        CONNECTION.chmod(0o600)

        for r in native:
            unit_text = "\n".join([
                "[Unit]", f"Description=NodeSteer repair E2E Agent {r['name']}",
                "After=network-online.target", "Wants=network-online.target", "",
                "[Service]", "Type=simple",
                f"ExecStart={AGENT_BIN_REMOTE} --config {r['config']}",
                "Restart=on-failure", "RestartSec=3", "NoNewPrivileges=false", "",
                "[Install]", "WantedBy=multi-user.target", "",
            ])
            local_unit = PRIVATE / r["unit"]
            private_write(local_unit, unit_text)
            ssh(r["ssh_target"], r["ssh_port"], next(n["ssh_identity_file"] for n in nodes if n["ip"] == r["ssh_ip"]),
                "\n".join(["set -eu", f"mkdir -m 0700 /etc/nodesteer/repair-{RUN}",
                           f"mkdir -m 0700 {shlex.quote(r['data'])}", ""]))
            key = next(n["ssh_identity_file"] for n in nodes if n["ip"] == r["ssh_ip"])
            scp(PRIVATE / (r["name"] + ".yaml"), r["ssh_target"], r["ssh_port"], key, r["config"])
            scp(local_unit, r["ssh_target"], r["ssh_port"], key, f"/etc/systemd/system/{r['unit']}")
            ssh(r["ssh_target"], r["ssh_port"], key, "\n".join([
                "set -eu", f"chmod 0600 {shlex.quote(r['config'])}",
                f"chmod 0644 /etc/systemd/system/{r['unit']}", "systemctl daemon-reload",
                f"systemctl start {shlex.quote(r['unit'])}",
                f"test \"$(systemctl show {shlex.quote(r['unit'])} -p ActiveState --value)\" = active", "",
            ]), timeout=45)
            started.append((next(n for n in nodes if n["ip"] == r["ssh_ip"]), r["unit"]))

        by_name = {}
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            code, rows = api(HUB_URL + "/api/nodes", token=token)
            if code == 200 and isinstance(rows, list):
                by_name = {x.get("hostname", x.get("name")): x for x in rows}
                if all(r["name"] in by_name and by_name[r["name"]].get("status") == "online"
                       and by_name[r["name"]].get("sync_status") == "synced" for r in native):
                    break
            time.sleep(2)
        if not all(r["name"] in by_name for r in native):
            raise RuntimeError("not all four new Native Nodes enrolled")
        states, new_ids = [], set()
        for r in native:
            item = by_name[r["name"]]
            if not item.get("id") or not item.get("agent_id"):
                raise RuntimeError(f"fresh identity missing for {r['name']}")
            new_ids.add(item["id"])
            states.append({
                "name": r["name"], "node_ip_alias": r["node_ip_alias"], "ssh_ip": r["ssh_ip"],
                "node_id": item["id"], "agent_id": item["agent_id"], "status": item.get("status"),
                "sync_status": item.get("sync_status"), "agent_version": item.get("agent_version"),
                "deployment_mode": item.get("deployment_mode"),
            })
        if len(new_ids) != 4 or new_ids & old_ids:
            raise RuntimeError("repair IDs duplicate or collide with primary Node IDs")
        if any(x["status"] != "online" or x["sync_status"] != "synced" or x["agent_version"] != AGENT_LABEL for x in states):
            raise RuntimeError("repair Agent health/version gate failed")

        # Primary authority is GET-only, using only its existing saved session.
        primary = json.loads(SOURCE.read_text())
        primary_health, _ = api(PRIMARY_URL + "/healthz")
        primary_ready, _ = api(PRIMARY_URL + "/readyz")
        primary_code, primary_nodes = api(PRIMARY_URL + "/api/nodes", token=primary.get("token"))
        primary_rows = []
        if primary_code == 200 and isinstance(primary_nodes, list):
            primary_rows = [{"id": x.get("id"), "status": x.get("status"), "sync_status": x.get("sync_status")}
                            for x in primary_nodes]
        health = {
            "run_id": RUN, "status": "healthy_gate_passed",
            "hub": {"url": HUB_URL, "sha256": HUB_SHA, "healthz_http": healthz, "readyz_http": readyz,
                    "fresh_login_http": login_status, "me_http": me_status, "me_role": role},
            "agents": states, "new_ids_unique": len(new_ids) == 4,
            "new_ids_disjoint_from_primary": not bool(new_ids & old_ids),
            "all_four_online_synced_version_match": True,
            "primary_readonly": {"healthz_http": primary_health, "readyz_http": primary_ready,
                                "nodes_http": primary_code, "nodes": primary_rows, "no_login_or_write": True},
            "secrets_included": False,
        }
        HEALTH.write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n")
        REPORT.write_text(json.dumps({
            "run_id": RUN, "status": "deployed_and_healthy", "hub_config": HUB_CONFIG,
            "hub_data": HUB_DATA, "hub_unit": HUB_UNIT, "agent_config_dir": f"/etc/nodesteer/repair-{RUN}",
            "agent_data_root": f"/var/lib/{PREFIX}", "agent_units": [r["unit"] for r in native],
            "hub_sha256": HUB_SHA, "agent_sha256": AGENT_SHA, "private_connection": str(CONNECTION),
            "health_report": str(HEALTH.relative_to(ROOT)), "original_services_touched": False,
        }, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "deployed_and_healthy", "hub_health": [healthz, readyz],
                          "me": [me_status, role], "agents": [
                              {k: n[k] for k in ("name", "node_id", "status", "sync_status", "agent_version")} for n in states],
                          "primary_get": [primary_health, primary_ready, primary_code],
                          "private_connection": str(CONNECTION)}, ensure_ascii=False))
    except Exception:
        for target, unit in reversed(started):
            try:
                ssh(target["ssh_target"], target["ssh_port"], target["ssh_identity_file"],
                    f"systemctl stop {shlex.quote(unit)} >/dev/null 2>&1 || true\n")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"repair deployment failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
