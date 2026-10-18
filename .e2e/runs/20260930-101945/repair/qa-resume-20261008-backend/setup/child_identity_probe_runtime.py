import hashlib
import json
import os
import sqlite3
import stat
import urllib.parse
from pathlib import Path

OWNED_DIR = Path(__OWNED_DIR_JSON__)
EXPECTED_MANIFEST_SHA256 = __EXPECTED_MANIFEST_SHA256__
EXPECTED_NODE_ID = __EXPECTED_NODE_ID__
EXPECTED_AGENT_ID = __EXPECTED_AGENT_ID__
EXPECTED_NODE_NAME = __EXPECTED_NODE_NAME__
EXPECTED_NODE_IP = __EXPECTED_NODE_IP__


def fail(code):
    print(json.dumps({'status': 'BLOCKED', 'code': code}, separators=(',', ':')))
    raise SystemExit(0)


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
    return [part.decode(errors='replace') for part in Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\0') if part]


def identity_matches(row):
    pid = int(row['pid'])
    if starttime(pid) != int(row['starttime']) or uid_pair(pid) != (int(row['uid']), int(row['uid'])):
        return False
    exe = os.readlink('/proc/' + str(pid) + '/exe')
    if exe.endswith(' (deleted)') or sha256_file(exe) != row['exe_sha256']:
        return False
    return any(value == str(OWNED_DIR) or value.startswith(str(OWNED_DIR) + os.sep) for value in argv(pid))


def yaml_value(lines, key):
    rows = [line for line in lines if line.startswith(key + ': ')]
    if len(rows) != 1:
        fail('child_config_' + key + '_missing_or_duplicate')
    raw = rows[0].split(': ', 1)[1]
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        fail('child_config_' + key + '_parse_failed')


try:
    if not str(OWNED_DIR).startswith('/tmp/qa-resume-20261008-child-') or OWNED_DIR.is_symlink():
        fail('owned_directory_gate_failed')
    directory = OWNED_DIR.lstat()
    manifest_path = OWNED_DIR / 'owner-manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail('owner_manifest_missing_or_symlink')
    raw_manifest = manifest_path.read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != EXPECTED_MANIFEST_SHA256:
        fail('owner_manifest_sha_mismatch')
    owner = json.loads(raw_manifest)
    if (directory.st_dev, directory.st_ino) != (owner.get('directory_dev'), owner.get('directory_ino')):
        fail('directory_dev_inode_mismatch')
    if stat.S_IMODE(directory.st_mode) != 0o700 or directory.st_uid != owner.get('directory_uid') or directory.st_uid == 0:
        fail('owned_directory_mode_or_uid_mismatch')
    marker_path = OWNED_DIR / 'owner.marker'
    if marker_path.is_symlink() or marker_path.read_text() != owner.get('owner_marker'):
        fail('owner_marker_mismatch')
    if owner.get('node_id') != EXPECTED_NODE_ID or owner.get('agent_id') != EXPECTED_AGENT_ID:
        fail('manifest_node_identity_mismatch')
    child_alive = identity_matches(owner['child'])
    supervisor_alive = identity_matches(owner['supervisor'])
    if not child_alive or not supervisor_alive:
        fail('owned_child_or_supervisor_process_identity_mismatch')

    config_path = Path(owner['config']['path'])
    config_stat = config_path.lstat()
    if config_path.is_symlink() or not stat.S_ISREG(config_stat.st_mode):
        fail('child_config_not_regular')
    if stat.S_IMODE(config_stat.st_mode) != 0o600 or config_stat.st_uid != owner['directory_uid']:
        fail('child_config_mode_or_owner_mismatch')
    lines = config_path.read_text().splitlines()
    config_agent_id = yaml_value(lines, 'agent_id')
    config_name = yaml_value(lines, 'node_name')
    config_ip = yaml_value(lines, 'node_ip')
    config_hub = yaml_value(lines, 'hub_url')
    config_token = yaml_value(lines, 'registration_token')
    if config_agent_id != EXPECTED_AGENT_ID or config_name != EXPECTED_NODE_NAME or config_ip != EXPECTED_NODE_IP:
        fail('child_config_identity_mismatch')
    if not isinstance(config_token, str) or not config_token or hashlib.sha256(config_token.encode()).hexdigest()[:16] != owner['registration_token_fingerprint']:
        fail('child_config_grant_fingerprint_mismatch')
    parsed = urllib.parse.urlsplit(config_hub)
    if parsed.scheme != 'ws' or parsed.hostname != EXPECTED_NODE_IP or parsed.port != owner['proxy']['port'] or parsed.username or parsed.password or parsed.query or parsed.fragment:
        fail('child_config_loopback_proxy_endpoint_mismatch')
    del config_token, lines

    db_path = Path(OWNED_DIR, 'data', 'state.db')
    if db_path.is_symlink() or not db_path.is_file():
        fail('child_identity_database_missing_or_symlink')
    db_uri = 'file:' + urllib.parse.quote(str(db_path), safe='/') + '?mode=ro'
    connection = sqlite3.connect(db_uri, uri=True, timeout=2)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'identity' not in tables:
            fail('child_identity_table_missing')
        values = {key: value for key, value in connection.execute('SELECT key,value FROM identity WHERE key IN (?,?)', ('node_id', 'agent_id'))}
    finally:
        connection.close()
    if values.get('node_id') != EXPECTED_NODE_ID or values.get('agent_id') != EXPECTED_AGENT_ID:
        fail('child_database_identity_mismatch')
    print(json.dumps({
        'status': 'PASS',
        'node_id_matches_enrollment': True,
        'agent_id_matches_enrollment': True,
        'database_identity_matches': True,
        'database_opened_mode_ro': True,
        'owner_manifest_sha_matches': True,
        'directory_dev_inode_matches': True,
        'owner_marker_matches': True,
        'child_process_identity_matches': True,
        'supervisor_process_identity_matches': True,
        'child_config_mode_0600_owner_nonroot': True,
        'child_config_grant_fingerprint_matches': True,
        'child_proxy_url_loopback_matches': True,
        'secret_values_emitted': False,
    }, separators=(',', ':')))
except SystemExit:
    raise
except BaseException:
    fail('identity_probe_internal_failure')
