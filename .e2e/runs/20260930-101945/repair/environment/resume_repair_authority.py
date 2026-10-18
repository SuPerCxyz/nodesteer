#!/usr/bin/env python3
"""Resume the owned Hub first-start after listener readiness, then enroll 4 fresh Agents."""
import datetime as dt
import hashlib
import json
import os
import argparse
from pathlib import Path
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
CONNECTION = PRIVATE / "repair-connection.json"
HUB_CONFIG_PRIVATE = PRIVATE / "repair-hub.yaml"
HUB_BIN = Path("/tmp/nodesteer-e2e-" + RUN + "-7wksl0_x/repair-build/nodesteer-hub")
HUB_SHA = "395f4b76fcf41d8cc5f827cd38f564fa80e87c4cab0fc1193a529f54bce1567e"
HUB_URL = "http://192.168.100.209:18080"
WS_URL = "ws://192.168.100.209:18080"
HUB_CONFIG = f"/etc/nodesteer/hub-repair-{RUN}.yaml"
HUB_DATA = f"/var/lib/nodesteer-hub-repair-{RUN}"
HUB_UNIT = f"nodesteer-hub-repair-{RUN}.service"
HUB_UNIT_PATH = f"/etc/systemd/system/{HUB_UNIT}"
REMOTE_ROOT = f"/opt/{PREFIX}"
HUB_BIN_REMOTE = REMOTE_ROOT + "/bin/nodesteer-hub"
AGENT_BIN_REMOTE = REMOTE_ROOT + "/agent/nodesteer-agent"
HEALTH = ROOT / ".e2e/runs" / RUN / "repair/qa/initial-health.json"
REPORT = PUBLIC / "deployment-result.json"
FAILURE = ROOT / ".e2e/runs" / RUN / "repair/qa/deployment-attempt.json"
PRIMARY_URL = "http://192.168.100.209:8080"
AGENT_SHA = "d54faba1c6a8989ca3ab5a45a3d45b5d1bbcd1659862c074ea362ef18bf83124"
AGENT_VERSION = "dddee4f0-e2e-repair-20261004"


def run(command, *, input_text=None, timeout=60):
    p = subprocess.run(command, input=input_text, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "command failed")[-500:])
    return p.stdout.strip()


def ssh(target, port, key, script, timeout=60):
    return run([
        "ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-i", os.path.expanduser(key), "-p", str(port), target, "sh", "-s",
    ], input_text=script, timeout=timeout)


def scp(local, target, port, key, remote_path):
    run([
        "scp", "-F", "/dev/null", "-B", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-i", os.path.expanduser(key), "-P", str(port), str(local), target + ":" + remote_path,
    ])


def request(path, method="GET", body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(request_url(path), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return parse_body(r.status, r.read())
    except urllib.error.HTTPError as e:
        return parse_body(e.code, e.read())
    except urllib.error.URLError:
        return 0, None


def parse_body(status, raw):
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, None


def parser_self_test():
    assert parse_body(200, b"ok") == (200, None)
    assert parse_body(200, b'{"role":"administrator"}') == (200, {"role": "administrator"})
    assert parse_body(200, b"") == (200, None)
    assert request_url("/healthz") == HUB_URL + "/healthz"
    assert request_url(PRIMARY_URL + "/healthz") == PRIMARY_URL + "/healthz"


def request_url(path):
    return path if path.startswith(("http://", "https://")) else HUB_URL + path


def ensure_private_content(path, content):
    if path.exists():
        if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.read_text() != content:
            raise RuntimeError(f"private owned file mismatch: {path.name}")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)


def ensure_remote_dir(target, port, key, path, mode):
    script = "\n".join([
        "set -eu",
        f"p={shlex.quote(path)}",
        'if test -L "$p"; then exit 61; fi',
        f'if test -e "$p"; then test -d "$p"; else mkdir -m {mode} "$p"; fi',
        f'test "$(stat -c %a "$p")" = {mode}',
        "",
    ])
    ssh(target, port, key, script)


def ensure_remote_file(target, port, key, local_path, remote_path, mode):
    digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
    script = "\n".join([
        "set -eu",
        f"p={shlex.quote(remote_path)}",
        'if test -L "$p"; then exit 62; fi',
        'if test -e "$p"; then',
        f'  test -f "$p"; test "$(stat -c %a "$p")" = {mode}',
        f'  test "$(sha256sum "$p" | cut -d " " -f1)" = {digest}',
        '  echo present',
        'else echo absent; fi',
        "",
    ])
    state = ssh(target, port, key, script)
    if state == "absent":
        scp(local_path, target, port, key, remote_path)
        ssh(target, port, key, "\n".join([
            "set -eu", f"chmod {mode} {shlex.quote(remote_path)}",
            f"test \"$(sha256sum {shlex.quote(remote_path)} | cut -d ' ' -f1)\" = {digest}", "",
        ]))
    elif state != "present":
        raise RuntimeError(f"unexpected remote file check result: {remote_path}")


def deploy():
    if not PRIVATE.is_dir() or stat.S_IMODE(PRIVATE.stat().st_mode) != 0o700:
        raise RuntimeError("repair private directory must be mode 0700")
    if not CONNECTION.is_file() or stat.S_IMODE(CONNECTION.stat().st_mode) != 0o600:
        raise RuntimeError("fresh repair connection missing or has unsafe mode")
    if not HUB_CONFIG_PRIVATE.is_file() or stat.S_IMODE(HUB_CONFIG_PRIVATE.stat().st_mode) != 0o600:
        raise RuntimeError("fresh repair Hub config missing or has unsafe mode")
    if REPORT.exists() or HEALTH.exists() or FAILURE.exists():
        raise RuntimeError("refusing to overwrite prior deployment results")
    connection = json.loads(CONNECTION.read_text())
    source = json.loads(SOURCE.read_text())
    hub = source["hub"]
    nodes = connection["nodes"]
    source_nodes = {n["ip"]: n for n in source["nodes"]}
    for node in nodes:
        actual = source_nodes[node["ssh_ip"]]
        node["config_path"] = node.get("config_path", node.get("config"))
        node["data_dir"] = node.get("data_dir", node.get("data"))
        node["service"] = node.get("service", node.get("unit"))
        node["ssh_identity_file"] = actual["ssh_identity_file"]
    hub_config_sha = hashlib.sha256(HUB_CONFIG_PRIVATE.read_bytes()).hexdigest()
    if connection.get("hub_url") != HUB_URL or len(nodes) != 4:
        raise RuntimeError("repair private connection scope mismatch")
    if hashlib.sha256(HUB_BIN.read_bytes()).hexdigest() != HUB_SHA:
        raise RuntimeError("final Hub binary SHA mismatch")
    if hashlib.sha256(HUB_CONFIG_PRIVATE.read_bytes()).hexdigest() == "":
        raise RuntimeError("invalid private Hub config")

    # Verify the stopped owned Hub is the one just prepared, and original service stays active.
    before = ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], "\n".join([
        "set -eu",
        f"test \"$(systemctl show nodesteer-hub.service -p ActiveState --value)\" = active",
        f"test \"$(sha256sum {shlex.quote(HUB_BIN_REMOTE)} | cut -d ' ' -f1)\" = {HUB_SHA}",
        f"test \"$(sha256sum {shlex.quote(HUB_CONFIG)} | cut -d ' ' -f1)\" = {hub_config_sha}",
        f"test \"$(systemctl show {shlex.quote(HUB_UNIT)} -p LoadState --value)\" = loaded",
        f"printf 'state='; systemctl show {shlex.quote(HUB_UNIT)} -p ActiveState --value",
        "",
    ]))
    if "state=inactive" not in before and "state=failed" not in before and "state=active" not in before:
        raise RuntimeError("owned Hub unit state is not restartable")

    hub_admin_user = connection["admin_username"]
    hub_admin_password = connection["admin_password"]
    registration_token = connection["registration_token"]
    if not hub_admin_user.startswith("qa-repair-" + RUN) or not registration_token:
        raise RuntimeError("fresh repair authority credential identity invalid")

    started = []
    health_gate_passed = False
    try:
        if "state=active" not in before:
            ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"],
                f"systemctl start {shlex.quote(HUB_UNIT)}\n", timeout=45)
            started.append((hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], HUB_UNIT))
        # Type=simple can report active before bind; retry connection refusal for up to 30 sec.
        deadline = time.monotonic() + 30
        healthz = readyz = 0
        while time.monotonic() < deadline:
            healthz, _ = request("/healthz")
            readyz, _ = request("/readyz")
            if healthz == 200 and readyz == 200:
                break
            time.sleep(1)
        if healthz != 200 or readyz != 200:
            raise RuntimeError(f"repair Hub readiness timeout: healthz={healthz}, readyz={readyz}")

        token = connection.get("token", "")
        login_status = 0
        if token:
            me_status, me = request("/api/me", token=token)
            role = me.get("role", me.get("Role")) if isinstance(me, dict) else None
        else:
            me_status, me = 0, None
            role = None
        if me_status != 200 or role != "administrator":
            login_status, login = request("/api/login", "POST",
                                          {"username": hub_admin_user, "password": hub_admin_password})
            if login_status != 200 or not isinstance(login, dict) or not login.get("token"):
                raise RuntimeError(f"fresh repair admin login failed: HTTP {login_status}")
            token = login["token"]
            me_status, me = request("/api/me", token=token)
            role = me.get("role", me.get("Role")) if isinstance(me, dict) else None
        me_status, me = request("/api/me", token=token)
        role = me.get("role", me.get("Role")) if isinstance(me, dict) else None
        if me_status != 200 or role != "administrator":
            raise RuntimeError(f"repair /api/me failed: HTTP {me_status}, role={role}")
        connection["token"] = token
        temp = PRIVATE / ".repair-connection.json.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(connection, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp, CONNECTION)
        CONNECTION.chmod(0o600)

        for node in nodes:
            private_config = PRIVATE / (node["name"] + ".yaml")
            private_unit = PRIVATE / node["service"]
            if not private_config.is_file() or stat.S_IMODE(private_config.stat().st_mode) != 0o600:
                raise RuntimeError(f"private Agent config missing/unsafe: {node['name']}")
            unit_text = "\n".join([
                "[Unit]", f"Description=NodeSteer repair E2E Agent {node['name']}",
                "After=network-online.target", "Wants=network-online.target", "",
                "[Service]", "Type=simple",
                f"ExecStart={AGENT_BIN_REMOTE} --config {node['config_path']}",
                "Restart=on-failure", "RestartSec=3", "NoNewPrivileges=false", "",
                "[Install]", "WantedBy=multi-user.target", "",
            ])
            ensure_private_content(private_unit, unit_text)
            target = next(n for n in source["nodes"] if n["ip"] == node["ssh_ip"])
            key = target["ssh_identity_file"]
            ensure_remote_dir(node["ssh_target"], node["ssh_port"], key, f"/etc/nodesteer/repair-{RUN}", "700")
            ensure_remote_dir(node["ssh_target"], node["ssh_port"], key, f"/var/lib/{PREFIX}", "700")
            ensure_remote_dir(node["ssh_target"], node["ssh_port"], key, node["data_dir"], "700")
            ensure_remote_file(node["ssh_target"], node["ssh_port"], key, private_config, node["config_path"], "600")
            ensure_remote_file(node["ssh_target"], node["ssh_port"], key, private_unit,
                               f"/etc/systemd/system/{node['service']}", "644")
            ssh(node["ssh_target"], node["ssh_port"], key, "\n".join([
                "set -eu", f"chmod 0600 {shlex.quote(node['config_path'])}",
                f"chmod 0644 /etc/systemd/system/{node['service']}", "systemctl daemon-reload",
                f"systemctl start {shlex.quote(node['service'])}",
                f"test \"$(systemctl show {shlex.quote(node['service'])} -p ActiveState --value)\" = active", "",
            ]), timeout=45)
            started.append((node["ssh_target"], node["ssh_port"], key, node["service"]))

        primary = json.loads(SOURCE.read_text())
        old_health = json.loads((ROOT / ".e2e/runs" / RUN / "main/current-health.json").read_text())
        old_ids = {x["id"] for x in old_health["nodes"]}
        by_name = {}
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            code, rows = request("/api/nodes", token=token)
            if code == 200 and isinstance(rows, list):
                by_name = {x.get("hostname", x.get("name")): x for x in rows}
                if all(n["name"] in by_name and by_name[n["name"]].get("status") == "online"
                       and by_name[n["name"]].get("sync_status") == "synced" for n in nodes):
                    break
            time.sleep(1)
        if not all(n["name"] in by_name for n in nodes):
            raise RuntimeError("not all four new Native Nodes enrolled within 60 sec")
        agents, new_ids = [], set()
        for n in nodes:
            row = by_name[n["name"]]
            node_id, agent_id = row.get("id"), row.get("agent_id")
            if not node_id or not agent_id:
                raise RuntimeError(f"missing new identity for {n['name']}")
            new_ids.add(node_id)
            agents.append({
                "name": n["name"], "node_ip_alias": n["node_ip_alias"], "ssh_ip": n["ssh_ip"],
                "node_id": node_id, "agent_id": agent_id, "status": row.get("status"),
                "sync_status": row.get("sync_status"), "agent_version": row.get("agent_version"),
                "deployment_mode": row.get("deployment_mode"),
            })
        if len(new_ids) != 4 or new_ids & old_ids:
            raise RuntimeError("new Native Node IDs duplicate or collide with primary IDs")
        if any(a["status"] != "online" or a["sync_status"] != "synced"
               or a["agent_version"] != AGENT_VERSION for a in agents):
            raise RuntimeError("four-Agent health/version gate failed")
        health_gate_passed = True

        # Primary :8080 is GET-only and uses the existing saved session; no new login or write.
        primary_health, _ = request(PRIMARY_URL + "/healthz")
        primary_ready, _ = request(PRIMARY_URL + "/readyz")
        primary_code, primary_nodes = request(PRIMARY_URL + "/api/nodes", token=primary.get("token"))
        primary_rows = []
        if primary_code == 200 and isinstance(primary_nodes, list):
            primary_rows = [{"id": x.get("id"), "status": x.get("status"), "sync_status": x.get("sync_status")}
                            for x in primary_nodes]
        safe = {
            "run_id": RUN, "status": "healthy_gate_passed",
            "hub": {"url": HUB_URL, "sha256": HUB_SHA, "healthz_http": healthz, "readyz_http": readyz,
                    "fresh_admin_login_http": login_status, "me_http": me_status, "me_role": role},
            "agents": agents, "new_node_ids_unique": len(new_ids) == 4,
            "new_ids_disjoint_from_primary": not bool(new_ids & old_ids),
            "all_four_online_synced_version_match": True,
            "primary_readonly": {"healthz_http": primary_health, "readyz_http": primary_ready,
                                "nodes_http": primary_code, "nodes": primary_rows, "no_login_or_write": True},
            "secrets_included": False,
        }
        HEALTH.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n")
        REPORT.write_text(json.dumps({
            "run_id": RUN, "status": "deployed_and_healthy", "hub_config": HUB_CONFIG,
            "hub_data": HUB_DATA, "hub_unit": HUB_UNIT,
            "agent_config_dir": f"/etc/nodesteer/repair-{RUN}",
            "agent_data_root": f"/var/lib/{PREFIX}", "agent_units": [n["service"] for n in nodes],
            "hub_sha256": HUB_SHA, "agent_sha256": AGENT_SHA,
            "private_connection": str(CONNECTION),
            "health_report": str(HEALTH.relative_to(ROOT)),
            "original_services_touched": False,
        }, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "deployed_and_healthy", "hub_health": [healthz, readyz],
                          "me": [me_status, role],
                          "agents": [{k: a[k] for k in ("name", "node_id", "status", "sync_status", "agent_version")} for a in agents],
                          "primary_get": [primary_health, primary_ready, primary_code],
                          "private_connection": str(CONNECTION)}, ensure_ascii=False))
    except Exception:
        # Stop only services this repair script started; preserve their owned config/data for diagnosis.
        if not health_gate_passed:
            for target, port, key, unit in reversed(started):
                try:
                    ssh(target, port, key, f"systemctl stop {shlex.quote(unit)} >/dev/null 2>&1 || true\n")
                except Exception:
                    pass
        raise


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--self-test-http-parser", action="store_true")
        args = parser.parse_args()
        if args.self_test_http_parser:
            parser_self_test()
            print("HTTP body parser self-test passed; no network used")
        else:
            deploy()
    except Exception as exc:
        print(f"repair resume failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
