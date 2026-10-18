#!/usr/bin/env python3
"""Build the pinned read-only SYS-09 probe command without any API calls."""
import base64
import hashlib
import shlex
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "readonly_probe.py"
PARAMETERS = [
    {"name": "phase", "type": "string", "required": True},
    {"name": "script_id", "type": "string", "required": False},
    {"name": "task_id", "type": "string", "required": False},
    {"name": "schedule_id", "type": "string", "required": False},
    {"name": "execution_id", "type": "string", "required": False},
    {"name": "expected_scheduled_time", "type": "string", "required": False},
    {"name": "expected_hub_revision", "type": "string", "required": True},
]


def build_command(source_bytes=None):
    source_bytes = SOURCE.read_bytes() if source_bytes is None else source_bytes
    compile(source_bytes, str(SOURCE), "exec")
    encoded = base64.b64encode(zlib.compress(source_bytes, 9)).decode("ascii")
    inner = (
        "import base64,zlib;"
        "exec(compile(zlib.decompress(base64.b64decode(\""
        + encoded
        + "\")),\"sys09_readonly_probe.py\",\"exec\"))"
    )
    return "python3 -c " + shlex.quote(inner)


def task_parameters():
    return [dict(item) for item in PARAMETERS]


def summary():
    source = SOURCE.read_bytes()
    command = build_command(source)
    return {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "command_bytes": len(command.encode()),
        "task_parameter_names": [item["name"] for item in PARAMETERS],
        "command_body_emitted": False,
        "external_calls": 0,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), sort_keys=True))
