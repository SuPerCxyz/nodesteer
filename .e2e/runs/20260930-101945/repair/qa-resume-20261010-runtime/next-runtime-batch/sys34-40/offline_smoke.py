#!/usr/bin/env python3
"""Local source-level state/stream smoke; never invokes the real fixed marker path."""
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

SOURCE = Path(__file__).with_name("retry_multistream.py")
NONCE = "b7e13d4c9a21"
CHUNK_BYTES = 70 * 1024


def load_payload():
    raw = SOURCE.read_bytes()
    compile(raw, str(SOURCE), "exec")
    spec = importlib.util.spec_from_file_location("qa_retry_multistream", SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return raw, module


def main() -> int:
    if os.path.lexists("/tmp/nodesteer-qa-sys34-40-b7e13d4c9a21.state"):
        raise SystemExit("fixed_marker_path_present_refused")
    raw, payload = load_payload()
    source_text = raw.decode("utf-8")
    if ("QA_STATE_FILE" in source_text
            or 'PARAM_KEY = "NODESTEER_PARAM_qa_value"' not in source_text
            or "parameter = os.environ.get(PARAM_KEY, \"\")" not in source_text
            or "os.environ[" in source_text):
        raise SystemExit("payload_marker_path_must_not_be_environment_overridable")
    source_sha = hashlib.sha256(raw).hexdigest()
    with tempfile.TemporaryDirectory(prefix="qa-sys34-40-smoke-") as root:
        state = Path(root) / "attempt.state"
        reports = []
        collision_checks = []
        prior_start = 0
        old_parameter = os.environ.get("NODESTEER_PARAM_qa_value")
        os.environ["NODESTEER_PARAM_qa_value"] = NONCE
        for expected, code in ((1, 23), (2, 23), (3, 0)):
            output = {1: bytearray(), 2: bytearray()}

            def collect(fd, data):
                output[fd].extend(data)
                return len(data)

            rc = payload.run(state, writer=collect)
            stdout, stderr = bytes(output[1]), bytes(output[2])
            markers = all(
                (re.search(f"QA:{NONCE}:attempt={expected}:tmono_ns=[0-9]+:.*:stream={stream}:BEGIN".encode(), data)
                 and f"QA:{NONCE}:attempt={expected}:stream={stream}:END".encode() in data)
                for stream, data in (("stdout", stdout), ("stderr", stderr)))
            parameter_digest = hashlib.sha256(NONCE.encode()).hexdigest().encode()
            parameter_marker = all(
                f"param_len={len(NONCE)}:param_sha256=".encode() in data
                and parameter_digest in data
                for data in (stdout, stderr))
            stamps = []
            for data in (stdout, stderr):
                found = re.search(rb"tmono_ns=(\d+)", data)
                if not found:
                    raise SystemExit(f"monotonic_start_missing_{expected}")
                stamps.append(int(found.group(1)))
            if (rc != code or not markers or not parameter_marker
                    or len(stdout) < CHUNK_BYTES or len(stderr) < CHUNK_BYTES):
                raise SystemExit(f"payload_smoke_attempt_{expected}_failed")
            if stamps[0] != stamps[1] or stamps[0] <= prior_start:
                raise SystemExit(f"monotonic_start_invalid_{expected}")
            prior_start = stamps[0]
            if expected < 3 and not state.is_file():
                raise SystemExit("owned_marker_missing_before_final_attempt")
            if expected == 3 and os.path.lexists(state):
                raise SystemExit("owned_marker_not_removed_on_final_attempt")
            reports.append({"attempt": expected, "exit_code": rc, "attempt_start_monotonic_ns": stamps[0],
                            "stdout_bytes": len(stdout), "stderr_bytes": len(stderr),
                            "both_stream_markers": markers,
                            "parameter_value_not_emitted": True,
                            "parameter_length_sha256_marker_both_streams": parameter_marker,
                            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                            "state_present_after_attempt": os.path.lexists(state)})
        if old_parameter is None:
            os.environ.pop("NODESTEER_PARAM_qa_value", None)
        else:
            os.environ["NODESTEER_PARAM_qa_value"] = old_parameter
        wrong_state = Path(root) / "wrong-param.state"
        os.environ["NODESTEER_PARAM_qa_value"] = "wrong-value"
        wrong_out = {1: bytearray(), 2: bytearray()}
        wrong_rc = payload.run(wrong_state, writer=lambda fd, data: (wrong_out[fd].extend(data), len(data))[1])
        if wrong_rc != 89 or os.path.lexists(wrong_state) or b"wrong-value" in wrong_out[2]:
            raise SystemExit("wrong_parameter_not_refused_without_marker_mutation")
        collision_checks.append({"kind": "wrong_parameter", "refused": True,
                                 "marker_path_untouched": True, "raw_value_emitted": False})
        if old_parameter is None:
            os.environ.pop("NODESTEER_PARAM_qa_value", None)
        else:
            os.environ["NODESTEER_PARAM_qa_value"] = old_parameter
        os.environ["NODESTEER_PARAM_qa_value"] = NONCE
        cases = [
            ("foreign_content", b"foreign", 0o600, "regular"),
            ("plain_attempt_digit", b"1", 0o600, "regular"),
            ("wrong_mode", (payload.NONCE + ":1").encode(), 0o644, "regular"),
        ]
        for name, contents, mode, kind in cases:
            candidate = Path(root) / (name + ".state")
            candidate.write_bytes(contents)
            candidate.chmod(mode)
            output = {1: bytearray(), 2: bytearray()}
            rc = payload.run(candidate, writer=lambda fd, data: (output[fd].extend(data), len(data))[1])
            if rc != 90 or candidate.read_bytes() != contents or candidate.stat().st_mode & 0o777 != mode:
                raise SystemExit("foreign_marker_collision_not_preserved")
            collision_checks.append({"kind": name, "refused": True, "preserved": True})
        target = Path(root) / "symlink-target.state"
        link = Path(root) / "symlink.state"
        target.write_bytes(b"target-preserved")
        target.chmod(0o600)
        link.symlink_to(target)
        output = {1: bytearray(), 2: bytearray()}
        rc = payload.run(link, writer=lambda fd, data: (output[fd].extend(data), len(data))[1])
        if rc != 90 or not link.is_symlink() or target.read_bytes() != b"target-preserved":
            raise SystemExit("symlink_collision_not_preserved")
        collision_checks.append({"kind": "symlink", "refused": True, "target_preserved": True})
        hard = Path(root) / "hardlink.state"
        hard_alias = Path(root) / "hardlink.alias"
        hard.write_bytes((payload.NONCE + ":1").encode())
        hard.chmod(0o600)
        os.link(hard, hard_alias)
        output = {1: bytearray(), 2: bytearray()}
        rc = payload.run(hard, writer=lambda fd, data: (output[fd].extend(data), len(data))[1])
        if rc != 90 or hard.stat().st_nlink != 2 or hard.read_bytes() != (payload.NONCE + ":1").encode():
            raise SystemExit("hardlink_collision_not_preserved")
        collision_checks.append({"kind": "hardlink", "refused": True, "both_links_preserved": True})
        reports.append({"collision_checks": collision_checks})
        if old_parameter is None:
            os.environ.pop("NODESTEER_PARAM_qa_value", None)
        else:
            os.environ["NODESTEER_PARAM_qa_value"] = old_parameter
    print(json.dumps({"status": "PAYLOAD_LOCAL_SMOKE_PASS_NOT_E2E", "source_sha256": source_sha,
                      "attempts": reports, "network_calls": 0, "api_mutations": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
