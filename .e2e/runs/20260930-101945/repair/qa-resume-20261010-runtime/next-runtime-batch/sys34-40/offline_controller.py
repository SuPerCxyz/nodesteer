#!/usr/bin/env python3
"""No-action controller for local SYS-34/40 harness preparation; never talks to Hub."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN = HERE.parent.parent / "next-runtime-batch-plan-20261012.json"
PAYLOAD = HERE / "retry_multistream.py"
SMOKE = HERE / "offline_smoke.py"
VERIFIER = HERE / "readonly_verifier.py"
EXPECTED_PAYLOAD = "a6aedafdcbd87e1e79223bc3a086a8cb52a6aa99d54c97a321cc452a0de2931c"
EXPECTED_VERIFIER = "b02e3d54b447b0d17aaa26e0d8a22821925c759bb361b03906db09efac371a66"
EXPECTED_SMOKE = "ffaaabbfffe8ee35ad1a285c06b5ae5b706fa1306e25e83e70e6a13491fbfc24"
ORDER = [
    "fresh_readonly_native3_owner_and_api_guard",
    "create_readonly_verifier_task_and_run_preflight",
    "ui_create_script_and_readback_exact_sha",
    "ui_create_script_task_and_readback_retry2_target",
    "ui_run_once_and_wait_terminal",
    "read_same_execution_logs_status_and_audit",
    "reuse_verifier_task_and_run_marker_absence_check",
    "delete_verifier_task_script_task_script_and_get404",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def no_network(*args, **kwargs):
    raise RuntimeError("offline_smoke_network_attempt_blocked")


def validate() -> dict:
    if sha(PAYLOAD) != EXPECTED_PAYLOAD:
        raise RuntimeError("payload_sha_pin_mismatch")
    if sha(VERIFIER) != EXPECTED_VERIFIER:
        raise RuntimeError("readonly_verifier_sha_pin_mismatch")
    if sha(SMOKE) != EXPECTED_SMOKE:
        raise RuntimeError("offline_smoke_sha_pin_mismatch")
    verifier_source = VERIFIER.read_bytes()
    compile(verifier_source, str(VERIFIER), "exec")
    if any(token in verifier_source.decode("utf-8") for token in ("urllib", "requests", "Authorization", "token", "password")):
        raise RuntimeError("readonly_verifier_contains_forbidden_api_or_secret_path")
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    case = next(x for x in plan["candidates"] if "SYS-34" in x["case_ids"])
    budget = case["budget"]
    if (budget.get("script_task") != 1 or budget.get("script") != 1
            or budget.get("readonly_verifier_task") != 1
            or budget.get("business_ui_run_posts") != 1
            or budget.get("readonly_verifier_run_posts") != 2
            or budget.get("fallback_exact_cleanup_task_max") != 1
            or budget.get("fallback_exact_cleanup_run_posts_max") != 1
            or budget.get("internal_process_attempts") != 3
            or budget.get("schedule") != 0):
        raise RuntimeError("plan_resource_budget_mismatch")
    payload_source = PAYLOAD.read_text(encoding="utf-8")
    if any(token in payload_source for token in ("urllib", "requests", "http.client", "socket.",
                                                 "QA_STATE_FILE", "os.environ[")):
        raise RuntimeError("payload_unexpected_network_client")
    if ('PARAM_KEY = "NODESTEER_PARAM_qa_value"' not in payload_source
            or "parameter = os.environ.get(PARAM_KEY, \"\")" not in payload_source
            or "parameter_value_not_emitted" not in SMOKE.read_text(encoding="utf-8")):
        raise RuntimeError("task_parameter_env_contract_missing")
    return {"plan_counts": plan["canonical_snapshot"]["counts"],
            "fixture_budget": budget, "execution_order": ORDER,
            "readonly_verifier_sha256": EXPECTED_VERIFIER}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-offline", action="store_true")
    args = parser.parse_args()
    if not args.smoke_offline:
        print(json.dumps({"status": "DRAFT_NO_ACTION", "api_calls": 0,
                          "api_mutations": 0, "tasks": 0, "runs": 0}, sort_keys=True))
        return 0
    summary = validate()
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    try:
        with tempfile.TemporaryDirectory(prefix="qa-no-network-") as guard_dir:
            site = Path(guard_dir) / "sitecustomize.py"
            site.write_text("""
import socket
def _blocked(*args, **kwargs):
    raise RuntimeError('offline_network_connect_blocked')
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.socket.sendto = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
""", encoding="utf-8")
            env = {"PATH": "/usr/bin:/bin", "LANG": "C", "PYTHONPATH": guard_dir,
                   "PYTHONNOUSERSITE": "1", "NO_PROXY": "*", "no_proxy": "*",
                   "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
                   "NODESTEER_PARAM_qa_value": "b7e13d4c9a21"}
            p = subprocess.run([sys.executable, str(SMOKE)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=20, check=False, text=True)
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
    if p.returncode or "PAYLOAD_LOCAL_SMOKE_PASS_NOT_E2E" not in p.stdout:
        raise RuntimeError("offline_payload_smoke_failed")
    print(json.dumps({"status": "OFFLINE_CONTROLLER_SMOKE_PASS_NOT_E2E",
                      "api_calls": 0, "api_mutations": 0, "docker_actions": 0,
                      "task_creates": 0, "run_posts": 0,
                      "network_connect_attempts": 0, "summary": summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
