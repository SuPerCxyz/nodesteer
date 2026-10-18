import hashlib
import json
import subprocess

OWNER = "qa-resume-20261012-sys12-d19e0c4f3e69"
TARGETS = (
    ("container", "88a04edab1fa3f2ed05b885e05056981d499bdd8496b69f2961363b3ead2dd53"),
    ("network", "5fcc323b477f67cdd12cdcce46a705a611a4a12f80f60e9287edef6d72c240a9"),
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def exact_missing(stderr, kind, resource_id):
    lower = stderr.lower().rstrip()
    if lower.endswith(("no such object: " + resource_id).encode()):
        return "EXACT_NO_SUCH_OBJECT"
    if kind == "container" and lower.endswith(("no such container: " + resource_id).encode()):
        return "EXACT_NO_SUCH_CONTAINER"
    if kind == "network" and lower.endswith(("no such network: " + resource_id).encode()):
        return "EXACT_NO_SUCH_NETWORK"
    return "UNKNOWN_DIAGNOSTIC"


def inspect(kind, resource_id):
    stdout = b""
    stderr = b""
    try:
        proc = subprocess.run(["docker", kind, "inspect", resource_id],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=4, check=False)
        rc = proc.returncode
        stdout = proc.stdout or b""
        stderr = proc.stderr or b""
        result = {
            "kind": kind,
            "expected_id": resource_id,
            "returncode": rc,
            "stdout_bytes": len(stdout),
            "stdout_sha256": sha(stdout),
            "stderr_bytes": len(stderr),
            "stderr_sha256": sha(stderr),
            "diagnostic": "FOUND" if rc == 0 else exact_missing(stderr, kind, resource_id),
        }
        if rc == 0:
            try:
                rows = json.loads(stdout)
            except (ValueError, TypeError):
                rows = None
            row = rows[0] if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) else None
            labels = ((row.get("Config") or {}).get("Labels") or {}) if kind == "container" and row else (
                (row.get("Labels") or {}) if row else {})
            result.update({
                "json_count": len(rows) if isinstance(rows, list) else None,
                "id_match": bool(row and row.get("Id") == resource_id),
                "owner_label_match": bool(row and labels.get("nodesteer.e2e.owner") == OWNER),
            })
        return result
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        return {
            "kind": kind,
            "expected_id": resource_id,
            "returncode": None,
            "timed_out": True,
            "stdout_bytes": len(stdout),
            "stdout_sha256": sha(stdout),
            "stderr_bytes": len(stderr),
            "stderr_sha256": sha(stderr),
            "diagnostic": "UNKNOWN_DIAGNOSTIC",
        }


print(json.dumps({"owner": OWNER, "inspections": [inspect(*item) for item in TARGETS]},
                 separators=(",", ":")))
