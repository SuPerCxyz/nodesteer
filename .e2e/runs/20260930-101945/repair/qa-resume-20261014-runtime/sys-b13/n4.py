"""SSH helper for repair native-4 (192.168.100.247) used by sys-b13.

Only used for read-only inspection plus owned-resource docker operations.
Never touches nodesteer-agent.service, nodesteer-hub.service, the Host001
container/volume/bridge, or any other batch's resources.
"""
import json
import shlex
import subprocess
from pathlib import Path

HOST = '192.168.100.247'
KEY = str(Path.home() / '.ssh' / 'id_ed25519')
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR', '-i', KEY, 'root@' + HOST]


def rsh(script, timeout=180, check=True):
    """Run a bash script on native-4. Returns (rc, stdout, stderr)."""
    cp = subprocess.run(SSH + ['bash', '-s'], input=script, capture_output=True,
                        text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError('remote_sh_failed rc=%s: %s' % (cp.returncode, (cp.stderr[-600:] or cp.stdout[-600:])))
    return cp.returncode, cp.stdout, cp.stderr


def rpy(code, timeout=180, check=True):
    """Run python3 on native-4, return the JSON object printed on the last stdout line."""
    script = "python3 - <<'B13PYEOF'\n" + code + "\nB13PYEOF\n"
    rc, out, err = rsh(script, timeout=timeout, check=check)
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError('remote_py_empty_output: ' + err[-400:])
    return json.loads(lines[-1])


def dargs(args):
    return 'docker ' + ' '.join(shlex.quote(a) for a in args)


def docker(args, timeout=120, check=True):
    return rsh(dargs(args), timeout=timeout, check=check)


def docker_json(args, timeout=120):
    rc, out, err = docker(args + ['--format', '{{json .}}'] if False else args, timeout=timeout)
    return json.loads(out) if out.strip() else None


def container_inspect(cid, timeout=60):
    rc, out, err = docker(['container', 'inspect', cid], timeout=timeout, check=False)
    if rc != 0:
        return None
    rows = json.loads(out)
    return rows[0] if isinstance(rows, list) and rows else None


def image_inspect(ref, timeout=60):
    rc, out, err = docker(['image', 'inspect', ref], timeout=timeout, check=False)
    if rc != 0:
        return None
    rows = json.loads(out)
    return rows[0] if isinstance(rows, list) and rows else None


def exists(kind, name, timeout=60):
    rc, _, _ = docker([kind, 'inspect', name], timeout=timeout, check=False)
    return rc == 0


def sha256_of_host_file(path, timeout=90):
    rc, out, _ = rsh('sha256sum %s' % shlex.quote(path), timeout=timeout, check=False)
    if rc != 0:
        return None
    return out.split()[0]
