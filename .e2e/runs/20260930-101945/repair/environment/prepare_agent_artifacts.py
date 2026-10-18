#!/usr/bin/env python3
"""Stage the frozen Agent binary and make a unique, stopped-ready test image."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

PUBLIC = Path(__file__).resolve().parent
ROOT = PUBLIC.parents[4]
RUN = "20260930-101945"
PREFIX = "nodesteer-repair-" + RUN
PRIVATE = Path("/tmp/nodesteer-e2e-" + RUN + "-repair")
CONNECTION = Path("/tmp/nodesteer-e2e-" + RUN + "-7wksl0_x/connection.json")
BINARY = Path("/tmp/nodesteer-e2e-" + RUN + "-7wksl0_x/repair-build/nodesteer-agent")
MANIFEST = ROOT / ".e2e/runs" / RUN / "repair/agent-build-manifest.json"
SHA = "d54faba1c6a8989ca3ab5a45a3d45b5d1bbcd1659862c074ea362ef18bf83124"
SIZE = 16878773
BUILD_LABEL = "dddee4f0-e2e-repair-20261004"
BASE_IMAGE = "qa-" + RUN + "-env-agent:dddee4f0"
BASE_IMAGE_ID = "sha256:93d27860a3807172e963eeb6a05b7165c47acab3c7a94a0744833501202b752a"
IMAGE_TAG = "nodesteer/agent:repair-" + RUN
REMOTE_ROOT = "/opt/" + PREFIX
REMOTE_DIR = REMOTE_ROOT + "/agent"
REMOTE_AGENT = REMOTE_DIR + "/nodesteer-agent"
REMOTE_DOCKERFILE = REMOTE_DIR + "/Dockerfile"
REPORT = PUBLIC / "agent-artifact-staging-final.json"
EXPECTED_IPS = {"192.168.100.207", "192.168.100.222", "192.168.100.204", "192.168.100.247"}


def run(command, *, input_text=None, timeout=60):
    result = subprocess.run(command, input=input_text, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed")[-500:]
        raise RuntimeError(f"exit={result.returncode}: {detail}")
    return result.stdout.strip()


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


def fixtures(nodes):
    rows = []
    for i, node in enumerate(sorted(nodes, key=lambda x: x["ip"]), 1):
        suffix, mode, hostint = f"native-{i}", "native", False
        rows.append(fixture(suffix, mode, hostint, node, f"/var/lib/{PREFIX}/native-{i}"))
    for suffix, mode, hostint, node, data_dir in [
        ("docker-1", "docker", False, nodes[1], f"/var/lib/{PREFIX}/docker-1"),
        ("compose-1", "docker", False, nodes[0], f"/var/lib/{PREFIX}/compose-1"),
        ("hostint-1", "docker_host_integration", True, nodes[3], f"/var/lib/{PREFIX}/hostint-1"),
    ]:
        rows.append(fixture(suffix, mode, hostint, node, data_dir))
    return rows


def fixture(suffix, mode, hostint, node, data_dir):
    name = f"qa-repair-{RUN}-{suffix}"
    return {
        "name": name, "mode": mode, "host_integration": hostint,
        "node_ip_alias": name + ".invalid", "ssh_target": node["ssh_target"], "ssh_ip": node["ip"],
        "data_dir": data_dir, "config_path": f"/etc/nodesteer/repair-{RUN}/{suffix}.yaml",
        "agent_id": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="upload and build after all preflight checks pass")
    args = parser.parse_args()
    if not PRIVATE.is_dir() or stat.S_IMODE(PRIVATE.stat().st_mode) != 0o700:
        raise RuntimeError("repair private directory must exist with mode 0700")
    if REPORT.exists():
        raise RuntimeError("refusing to overwrite repair evidence")
    if not BINARY.is_file() or BINARY.stat().st_size != SIZE:
        raise RuntimeError("frozen Agent artifact missing or size mismatch")
    if hashlib.sha256(BINARY.read_bytes()).hexdigest() != SHA:
        raise RuntimeError("frozen Agent artifact SHA mismatch")
    built = json.loads(MANIFEST.read_text())
    manifest_row = next((x for x in built.get("artifacts", []) if x.get("name") == "nodesteer-agent" and x.get("architecture") == "amd64"), None)
    if not manifest_row or (manifest_row.get("sha256"), manifest_row.get("size"), manifest_row.get("build_label")) != (SHA, SIZE, BUILD_LABEL):
        raise RuntimeError("frozen Agent manifest mismatch")

    config = json.loads(CONNECTION.read_text())
    nodes, hub = config["nodes"], config["hub"]
    if len(nodes) != 4 or {node.get("ip") for node in nodes} != EXPECTED_IPS or hub.get("ip") != "192.168.100.209":
        raise RuntimeError("private connection differs from the authorized five-VM inventory")
    rows = fixtures(nodes)
    template_paths = [PRIVATE / (row["name"] + ".yaml.template") for row in rows]
    if any(path.exists() for path in template_paths):
        raise RuntimeError("refusing to overwrite private Agent templates")

    targets = [{"role": "native-agent", **node} for node in nodes]
    targets.append({"role": "hub-image-build", **hub})
    checks = []
    native_index = {row["ssh_ip"]: row["name"].rsplit("-", 1)[-1] for row in rows if row["mode"] == "native"}
    for i, target in enumerate(targets):
        unit = (f"nodesteer-agent-repair-{RUN}-native-{native_index[target['ip']]}.service"
                if i < 4 else f"nodesteer-hub-repair-{RUN}.service")
        lines = [
            "set -eu",
            f"root={shlex.quote(REMOTE_ROOT)}",
            'test ! -e "$root"',
            f"test ! -e /etc/systemd/system/{unit}",
            f"test ! -e /var/lib/{PREFIX}",
            f"test ! -e /etc/nodesteer/repair-{RUN}",
        ]
        for row in (f for f in rows if f["ssh_ip"] == target["ip"]):
            lines += [f"test ! -e {shlex.quote(row['data_dir'])}"]
            if row["host_integration"]:
                lines += [
                    f"test ! -e /opt/qa-repair-{RUN}",
                    f"test ! -e /etc/systemd/system/qa-repair-{RUN}-hostint.service",
                ]
            if row["mode"] != "native":
                suffix = row["name"].split(f"qa-repair-{RUN}-", 1)[1]
                volume = f"qa-repair-{RUN}-{suffix}-data"
                lines += [
                    f"if docker container inspect {shlex.quote(row['name'])} >/dev/null 2>&1; then exit 32; fi",
                    f"if docker volume inspect {shlex.quote(volume)} >/dev/null 2>&1; then exit 33; fi",
                ]
                if row["name"].endswith("compose-1"):
                    lines.append(f"if docker network inspect {shlex.quote(row['name'] + '_default')} >/dev/null 2>&1; then exit 34; fi")
        if i == 4:
            base_id_query = f"docker image inspect {shlex.quote(BASE_IMAGE)} --format {shlex.quote('{{.Id}}')}"
            lines += [
                f'test "$({base_id_query})" = {shlex.quote(BASE_IMAGE_ID)}',
                f"if docker image inspect {shlex.quote(IMAGE_TAG)} >/dev/null 2>&1; then exit 31; fi",
            ]
        try:
            result = ssh(target["ssh_target"], target["ssh_port"], target["ssh_identity_file"], "\n".join(lines) + "\n")
        except Exception as exc:
            raise RuntimeError(f"read-only preflight failed for {target['role']} {target['ip']}: {exc}") from exc
        checks.append({"target_ip": target["ip"], "role": target["role"], "preflight": result})

    if not args.execute:
        print(json.dumps({"status": "preflight_passed_no_writes", "agent_sha256": SHA, "checks": checks}, ensure_ascii=False))
        return

    created, private_files = [], []
    build_attempted = False
    try:
        for target in targets:
            reserve = "\n".join([
                "set -eu",
                f"mkdir -m 0755 {shlex.quote(REMOTE_ROOT)}",
                f"if ! mkdir -m 0755 {shlex.quote(REMOTE_DIR)}; then rmdir {shlex.quote(REMOTE_ROOT)}; exit 1; fi",
                "",
            ])
            ssh(target["ssh_target"], target["ssh_port"], target["ssh_identity_file"], reserve)
            created.append(target)
            scp(BINARY, target["ssh_target"], target["ssh_port"], target["ssh_identity_file"], REMOTE_AGENT)
            if target["role"] == "hub-image-build":
                scp(PUBLIC / "agent-repair.Dockerfile", target["ssh_target"], target["ssh_port"], target["ssh_identity_file"], REMOTE_DOCKERFILE)
            verify = "\n".join([
                "set -eu",
                f"chmod 0755 {shlex.quote(REMOTE_AGENT)}",
                f'test "$(stat -c %s {shlex.quote(REMOTE_AGENT)})" = {SIZE}',
                f'actual=$(sha256sum {shlex.quote(REMOTE_AGENT)}); test "${{actual%% *}}" = {SHA}',
                "",
            ])
            ssh(target["ssh_target"], target["ssh_port"], target["ssh_identity_file"], verify)

        image_inspect_format = ('{{.Id}}|{{json .RepoDigests}}|'
                                '{{index .Config.Labels "org.nodesteer.repair.agent-sha256"}}|'
                                '{{index .Config.Labels "org.nodesteer.repair.build-label"}}')
        build = "\n".join([
            "set -eu",
            f"docker build --network=none --build-arg REPAIR_BASE_IMAGE={shlex.quote(BASE_IMAGE)} "
            f"--build-arg REPAIR_AGENT_SHA={SHA} --build-arg REPAIR_BUILD_LABEL={BUILD_LABEL} "
            f"--tag {shlex.quote(IMAGE_TAG)} --file {shlex.quote(REMOTE_DOCKERFILE)} {shlex.quote(REMOTE_DIR)}",
            f"docker image inspect {shlex.quote(IMAGE_TAG)} --format {shlex.quote(image_inspect_format)}",
            "",
        ])
        build_attempted = True
        result = ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], build, timeout=240).splitlines()[-1]
        image_id, repo_digests, image_sha, image_label = result.split("|", 3)
        if image_sha != SHA or image_label != BUILD_LABEL or not image_id.startswith("sha256:"):
            raise RuntimeError("built image ID/labels do not match the frozen Agent artifact")

        for path in template_paths:
            path.touch(mode=0o600, exist_ok=False)
            private_files.append(path)
        for path, row in zip(template_paths, rows):
            lines = [
                'hub_url: "ws://192.168.100.209:18080"',
                'registration_token: "REPLACE_FROM_FRESH_REPAIR_HUB"',
                f'node_name: "{row["name"]}"',
                f'node_ip: "{row["node_ip_alias"]}"',
                f'deployment_mode: "{row["mode"]}"',
                f'host_integration: {str(row["host_integration"]).lower()}',
                f'data_dir: "{row["data_dir"]}"',
            ]
            if row["host_integration"]:
                lines += [
                    'host_root: "/host"', "host_path_allowlist:",
                    f'  - "/opt/qa-repair-{RUN}"',
                    f'  - "/etc/systemd/system/qa-repair-{RUN}-hostint.service"',
                ]
            path.write_text("\n".join(lines) + "\n")
            path.chmod(0o600)

        report = {
            "run_id": RUN, "status": "staged_not_started",
            "agent_binary": {"sha256": SHA, "size": SIZE, "build_label": BUILD_LABEL},
            "agent_uploads": [{"role": target["role"], "target_ip": target["ip"], "path": REMOTE_AGENT, "sha256": SHA} for target in targets],
            "agent_image": {"tag": IMAGE_TAG, "base_image": BASE_IMAGE, "base_image_id": BASE_IMAGE_ID,
                            "image_id": image_id, "repo_digests": json.loads(repo_digests),
                            "embedded_agent_sha256": image_sha, "build_label": image_label},
            "preflight_checks": checks,
            "fixtures": rows,
            "private_templates": [str(path) for path in template_paths],
            "agent_id_rule": "AgentID omitted; fresh identity generated from each unique data_dir at first enrollment.",
            "authority_rule": "No primary credentials copied. registration_token in private templates is a placeholder until Root provides the fresh Hub authority.",
            "runtime_started": False,
        }
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": report["status"], "agent_sha256": SHA, "uploads": [x["ip"] for x in targets],
                          "image_tag": IMAGE_TAG, "image_id": image_id, "repo_digests": json.loads(repo_digests),
                          "runtime_started": False}, ensure_ascii=False))
    except Exception:
        if build_attempted:
            label_format = '{{index .Config.Labels "org.nodesteer.repair.agent-sha256"}}'
            label_query = f"docker image inspect {shlex.quote(IMAGE_TAG)} --format {shlex.quote(label_format)}"
            cleanup_image = "\n".join([
                f'label=$({label_query} 2>/dev/null || true)',
                f'if [ "$label" = {SHA} ]; then docker image rm {shlex.quote(IMAGE_TAG)} >/dev/null 2>&1 || true; fi',
                "",
            ])
            try:
                ssh(hub["ssh_target"], hub["ssh_port"], hub["ssh_identity_file"], cleanup_image)
            except Exception:
                pass
        for target in reversed(created):
            extra = f"rm -f {shlex.quote(REMOTE_DOCKERFILE)}\n" if target["role"] == "hub-image-build" else ""
            cleanup = "\n".join([
                "set +e", extra + f"rm -f {shlex.quote(REMOTE_AGENT)}",
                f"rmdir {shlex.quote(REMOTE_DIR)} 2>/dev/null",
                f"rmdir {shlex.quote(REMOTE_ROOT)} 2>/dev/null", "",
            ])
            try:
                ssh(target["ssh_target"], target["ssh_port"], target["ssh_identity_file"], cleanup)
            except Exception:
                pass
        for path in private_files:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"preparation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
