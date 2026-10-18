#!/usr/bin/env python3
"""Run the approved repair UI regression batches with private credentials/artifacts."""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

QA = Path(__file__).resolve().parent
ROOT = QA.parents[4]
WEB = ROOT / "web"
PRIVATE = Path(os.environ["NODESTEER_RESUME_PRIVATE_DIR"])
CONNECTION = json.loads((PRIVATE / "repair-connection.json").read_text())
BATCHES = {
    "safe": ("SAFE-(02|10|12|18)\\b", "e2e/tests/safe-ui.spec.ts"),
    "gap": ("GAP-(02|07|12)\\b", "e2e/tests/gap-ui.spec.ts"),
    "gap07": ("GAP-07\\b", "e2e/tests/gap-ui.spec.ts"),
    "gap07b": ("GAP-07\\b", "e2e/tests/gap-ui.spec.ts"),
    "safe_more": ("SAFE-(01|04|05|06|07|08|09|11|14|15|17|20|21|22|25)\\b", "e2e/tests/safe-ui.spec.ts"),
    "safe01_21": ("SAFE-(01|21)\\b", "e2e/tests/safe-ui.spec.ts"),
    "safe01_retry": ("SAFE-01\\b", "e2e/tests/safe-ui.spec.ts"),
    "gap_more": ("GAP-(01|03|05|06|08|09|10|13|19|20|21|22|23|24)\\b", "e2e/tests/gap-ui.spec.ts"),
}


def write_private(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(data)


def write_public(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        stream.write(data)


def scrub(value, secrets):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(mark in key.lower() for mark in ("password", "token", "secret", "cookie", "authorization", "credential")) else scrub(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    return value


def run(batch: str) -> int:
    if batch not in BATCHES:
        raise SystemExit("batch must be a preapproved safe/gap batch")
    label = "resume-20261004-" + batch
    grep, spec = BATCHES[batch]
    base = CONNECTION["hub_url"].rstrip("/")
    request = urllib.request.Request(base + "/api/nodes", headers={"Authorization": "Bearer " + CONNECTION["token"]})
    with urllib.request.urlopen(request, timeout=15) as response:
        nodes = json.loads(response.read())
    if len(nodes) != 4 or any(n.get("status") != "online" or n.get("sync_status") != "synced" for n in nodes):
        raise SystemExit("repair API did not return exactly four online/synced nodes")

    private_run = PRIVATE / "playwright" / label
    private_run.mkdir(mode=0o700, parents=True, exist_ok=False)
    evidence_stage = private_run / "evidence"
    artifacts = private_run / "artifacts"
    evidence_stage.mkdir(mode=0o700)
    artifacts.mkdir(mode=0o700)
    resources = private_run / "resources.jsonl"
    write_private(resources, "")
    public_evidence = QA / "evidence" / label
    public_evidence.mkdir(mode=0o755, parents=True, exist_ok=False)

    env = os.environ.copy()
    env.update({
        "BASE_URL": base,
        "NODESTEER_E2E_USERNAME": CONNECTION["admin_username"],
        "NODESTEER_E2E_PASSWORD": CONNECTION["admin_password"],
        "NODESTEER_E2E_PREFIX": "qa-resume-20261004-" + batch + "-",
        "NODESTEER_E2E_NODE_IDS": ",".join(node["id"] for node in nodes),
        "NODESTEER_E2E_LABEL_NODE_ID": nodes[-1]["id"],
        "NODESTEER_E2E_OPERATOR_USERNAME": "",
        "NODESTEER_E2E_VIEWER_USERNAME": "",
        "NODESTEER_E2E_RBAC_PASSWORD": "",
        "NODESTEER_E2E_OUTPUT_DIR": str(artifacts),
        "NODESTEER_E2E_PRIVATE_DIR": str(private_run),
        "NODESTEER_E2E_RESOURCE_LOG": str(resources),
        "NODESTEER_E2E_EVIDENCE_DIR": str(evidence_stage),
    })
    command = ["node", "node_modules/playwright/cli.js", "test", "--config", "e2e/playwright.config.ts", "--reporter=json", "--grep", grep, spec]
    completed = subprocess.run(command, cwd=WEB, env=env, text=True, capture_output=True, timeout=900)
    write_private(private_run / "reporter.raw.json", completed.stdout)

    secrets = [CONNECTION.get("admin_password", ""), CONNECTION.get("token", "")]
    users_log = private_run / "ui-users.jsonl"
    if users_log.exists():
        for line in users_log.read_text().splitlines():
            try:
                row = json.loads(line)
                secrets.extend(str(value) for key, value in row.items() if any(mark in key.lower() for mark in ("password", "token", "secret")))
            except ValueError:
                pass
    try:
        parsed = json.loads(completed.stdout)
        safe_report = scrub(parsed, secrets)
    except ValueError:
        safe_report = {"parse_error": True}

    result_path = QA / (label + "-results.json")
    write_public(result_path, json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n")
    for evidence in evidence_stage.glob("*.json"):
        try:
            safe_evidence = scrub(json.loads(evidence.read_text()), secrets)
        except ValueError:
            continue
        write_public(public_evidence / evidence.name, json.dumps(safe_evidence, ensure_ascii=False, indent=2) + "\n")

    stats = safe_report.get("stats", {}) if isinstance(safe_report, dict) else {}
    run_summary = {
        "label": label,
        "grep": grep,
        "spec": spec,
        "base_url": base,
        "exit_code": completed.returncode,
        "status": "passed" if completed.returncode == 0 and stats.get("failed", 0) == 0 else "failed",
        "stats": stats,
        "node_ids": [node["id"] for node in nodes],
        "evidence_directory": str(public_evidence.relative_to(ROOT)),
        "report": str(result_path.relative_to(ROOT)),
        "stderr_redacted": scrub(completed.stderr[-1400:], secrets),
        "raw_report_private_path": str(private_run / "reporter.raw.json"),
        "resources_private_path": str(resources),
    }
    write_public(QA / (label + "-run.json"), json.dumps(run_summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"label": label, "exit_code": completed.returncode, "status": run_summary["status"], "stats": stats, "report": str(result_path.relative_to(ROOT))}, ensure_ascii=False))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
