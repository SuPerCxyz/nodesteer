import hashlib
import json
import os
import shutil
import signal
import stat
import time
from pathlib import Path

OWNED_DIR = Path(__OWNED_DIR_JSON__)
EXPECTED_MANIFEST_SHA256 = __EXPECTED_MANIFEST_SHA256__


def emit(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def starttime(pid):
    raw = Path('/proc', str(pid), 'stat').read_text()
    return int(raw[raw.rfind(')') + 2:].split()[19])


def uid_pair(pid):
    for line in Path('/proc', str(pid), 'status').read_text().splitlines():
        if line.startswith('Uid:'):
            values = list(map(int, line.split()[1:5]))
            return values[0], values[1]
    raise ValueError('uid_missing')


def argv(pid):
    return [item.decode(errors='replace') for item in Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\0') if item]


def process_matches(row, directory):
    pid = int(row['pid'])
    if starttime(pid) != int(row['starttime']):
        return False
    if uid_pair(pid) != (int(row['uid']), int(row['uid'])):
        return False
    exe = os.readlink('/proc/' + str(pid) + '/exe')
    if exe.endswith(' (deleted)') or sha256_file(exe) != row['exe_sha256']:
        return False
    return any(value == str(directory) or value.startswith(str(directory) + os.sep) for value in argv(pid))


def stop(row, directory):
    pid = int(row['pid'])
    if not process_matches(row, directory):
        return not Path('/proc', str(pid)).exists()
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        try:
            if not process_matches(row, directory):
                return True
        except (OSError, ValueError, IndexError):
            return True
        time.sleep(0.1)
    try:
        if process_matches(row, directory):
            os.kill(pid, signal.SIGKILL)
    except (OSError, ValueError, IndexError):
        pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            if not process_matches(row, directory):
                return True
        except (OSError, ValueError, IndexError):
            return True
        time.sleep(0.1)
    return False


def find_owned_processes(directory):
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if any(value == str(directory) or value.startswith(str(directory) + os.sep) for value in argv(int(entry.name))):
                found.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return found


try:
    if not str(OWNED_DIR).startswith('/tmp/qa-resume-20261008-child-') or OWNED_DIR.is_symlink():
        raise RuntimeError('owned_directory_gate_failed')
    directory_stat = OWNED_DIR.lstat()
    manifest_path = OWNED_DIR / 'owner-manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError('owner_manifest_missing_or_symlink')
    raw = manifest_path.read_bytes()
    actual_manifest_sha = hashlib.sha256(raw).hexdigest()
    if actual_manifest_sha != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError('owner_manifest_sha_mismatch')
    owner = json.loads(raw)
    marker_path = OWNED_DIR / 'owner.marker'
    if marker_path.is_symlink() or marker_path.read_text() != owner.get('owner_marker'):
        raise RuntimeError('owner_marker_mismatch')
    if (directory_stat.st_dev, directory_stat.st_ino) != (owner.get('directory_dev'), owner.get('directory_ino')):
        raise RuntimeError('owned_directory_dev_inode_mismatch')
    if owner.get('directory') != str(OWNED_DIR) or owner.get('directory_uid') == 0:
        raise RuntimeError('owned_directory_manifest_identity_mismatch')
    processes = [owner['child'], owner['supervisor']]
    if not all(process_matches(row, OWNED_DIR) for row in processes):
        raise RuntimeError('owned_process_identity_guard_failed_no_signal_sent')
    stopped = []
    for row in processes:
        stopped.append(stop(row, OWNED_DIR))
    if not all(stopped):
        raise RuntimeError('owned_process_stop_failed')
    if find_owned_processes(OWNED_DIR):
        raise RuntimeError('owned_process_remains_after_stop')
    current = OWNED_DIR.lstat()
    if OWNED_DIR.is_symlink() or (current.st_dev, current.st_ino) != (owner['directory_dev'], owner['directory_ino']):
        raise RuntimeError('owned_directory_changed_before_cleanup')
    if marker_path.read_text() != owner['owner_marker']:
        raise RuntimeError('owner_marker_changed_before_cleanup')
    shutil.rmtree(OWNED_DIR)
    emit({'status': 'PASS', 'child_pid_stopped_after_identity_guard': True, 'supervisor_pid_stopped_after_identity_guard': True, 'remaining_owned_processes': 0, 'directory_removed': not OWNED_DIR.exists(), 'directory_dev_inode_guard_passed': True, 'owner_manifest_sha256': actual_manifest_sha})
except BaseException as error:
    emit({'status': 'BLOCKED', 'code': str(error), 'signal_sent_only_after_identity_guard': True})
