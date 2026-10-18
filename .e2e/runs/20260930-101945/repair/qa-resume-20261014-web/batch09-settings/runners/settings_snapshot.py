#!/usr/bin/env python3
"""Settings snapshot / restore oracle for batch09-settings.

`capture()` takes a complete, read-only picture of the runtime settings through
all three views that exist:

  * the management API view           GET /api/settings
  * the Hub storage view              hub.db `settings` table (mode=ro)
  * the Hub config file view          /etc/nodesteer/repair-hub.yaml (key names +
                                      sha256 only; values are never persisted)

`diff(snapshot, other)` compares snapshots field by field so the write-up can
prove "settings-after == settings-before" instead of asserting it.

The managed keys are exactly the five the API exposes.
"""
import hashlib
import json
import subprocess
import time
from pathlib import Path

HUB_HOST = '192.168.100.209'
HUB_DB = '/var/lib/nodesteer-hub-repair-20260930-101945/hub.db'
HUB_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')


API_KEYS = [
    'heartbeat_interval_sec',
    'revision_check_interval_sec',
    'changelog_window',
    'max_log_bytes',
    'agent_gateway_base_url',
]

PROBE = r'''
import json, sqlite3, os, hashlib, subprocess
c = sqlite3.connect('file:%s?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
out = {}
out['settings_rows'] = {r[0]: r[1] for r in c.execute('SELECT key,value FROM settings ORDER BY key')}
out['revision_sequence'] = c.execute('SELECT value FROM revision_sequence LIMIT 1').fetchone()[0]
out['quick_check'] = c.execute('PRAGMA quick_check').fetchone()[0]
cfg = '/etc/nodesteer/repair-hub.yaml'
alt = sorted(__import__('glob').glob('/etc/nodesteer/*hub*.yaml'))
out['config_candidates'] = alt
path = cfg if os.path.isfile(cfg) else (alt[0] if alt else None)
if path:
    raw = open(path, 'rb').read()
    out['config_file'] = {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                          'top_level_key_names': sorted({l.split(':')[0].strip() for l in raw.decode('utf-8','replace').splitlines() if ':' in l and not l.startswith((' ', '\t'))})}
else:
    out['config_file'] = None
r = subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,SubState,MainPID,ExecMainStartTimestamp',%r], capture_output=True, text=True)
out['hub_unit'] = dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)
b = subprocess.run(['systemctl','show','--no-pager','--property=ExecMainPID','nodesteer-hub.service'], capture_output=True, text=True)
out['primary_hub_unit'] = dict(x.split('=',1) for x in b.stdout.splitlines() if '=' in x)
print(json.dumps(out))
''' % (HUB_DB, HUB_UNIT)


def _ssh(program, timeout=60):
    ssh = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=8', '-i', SSH_KEY, 'root@' + HUB_HOST]
    cp = subprocess.run(ssh + ['python3', '-'], input=program, capture_output=True,
                        text=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError('hub_probe_failed: %s' % (cp.stderr[-300:] or cp.stdout[-300:]))
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    return json.loads(lines[-1])


def capture(hub):
    """hub = logged-in hubapi.Hub."""
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'endpoint': hub.base}
    api = hub.json('/api/settings')
    if isinstance(api, list):
        api = {s.get('key'): s.get('value') for s in api}
    out['api_settings'] = api
    out['api_managed_keys_present'] = sorted(api.keys())
    out['api_managed_keys_absent'] = sorted(set(API_KEYS) - set(api.keys()))
    db = _ssh(PROBE)
    out['hub_db_settings_managed'] = {k: db['settings_rows'].get(k) for k in API_KEYS}
    out['hub_db_settings_all_keys'] = sorted(db['settings_rows'].keys())
    out['hub_db_settings_cred_rows'] = sorted(
        [k for k in db['settings_rows'] if k.startswith('cred:')])
    out['hub_db_revision_sequence'] = db['revision_sequence']
    out['hub_db_quick_check'] = db['quick_check']
    out['hub_config_file'] = db['config_file']
    out['hub_unit'] = db['hub_unit']
    out['primary_hub_unit'] = db['primary_hub_unit']
    return out


def diff(before, after):
    """Field-by-field comparison of the two snapshots (the restore proof).

    The Hub `settings` table is a generic key/value table: besides the runtime
    settings it also holds one `cred:<node_id>` row per enrolled node. Those rows
    are created/removed by the owned child node enrollment/deletion that happens
    during the run, so the restore verdict is scoped to the non-credential keys
    and the credential-row drift is reported separately.
    """
    def managed_keys(snap):
        return sorted(set(snap['hub_db_settings_all_keys'])
                      - set(snap['hub_db_settings_cred_rows']))

    checks = {}
    checks['api_settings_equal'] = before['api_settings'] == after['api_settings']
    checks['api_settings_before'] = before['api_settings']
    checks['api_settings_after'] = after['api_settings']
    checks['api_settings_delta'] = {
        k: {'before': before['api_settings'].get(k), 'after': after['api_settings'].get(k)}
        for k in sorted(set(before['api_settings']) | set(after['api_settings']))
        if before['api_settings'].get(k) != after['api_settings'].get(k)}
    checks['hub_db_settings_managed_equal'] = (
        before['hub_db_settings_managed'] == after['hub_db_settings_managed'])
    checks['hub_db_settings_managed_before'] = before['hub_db_settings_managed']
    checks['hub_db_settings_managed_after'] = after['hub_db_settings_managed']
    checks['managed_key_set_before'] = managed_keys(before)
    checks['managed_key_set_after'] = managed_keys(after)
    checks['managed_key_set_equal'] = managed_keys(before) == managed_keys(after)
    checks['cred_row_keys_before'] = before['hub_db_settings_cred_rows']
    checks['cred_row_keys_after'] = after['hub_db_settings_cred_rows']
    checks['cred_row_delta'] = {
        'added': sorted(set(after['hub_db_settings_cred_rows'])
                        - set(before['hub_db_settings_cred_rows'])),
        'removed': sorted(set(before['hub_db_settings_cred_rows'])
                          - set(after['hub_db_settings_cred_rows']))}
    checks['config_file_sha256_before'] = (before['hub_config_file'] or {}).get('sha256')
    checks['config_file_sha256_after'] = (after['hub_config_file'] or {}).get('sha256')
    checks['config_file_unchanged'] = (
        checks['config_file_sha256_before'] == checks['config_file_sha256_after'])
    checks['hub_unit_MainPID_unchanged'] = (
        before['hub_unit'].get('MainPID') == after['hub_unit'].get('MainPID'))
    checks['primary_hub_MainPID_unchanged'] = (
        before['primary_hub_unit'].get('MainPID') == after['primary_hub_unit'].get('MainPID'))
    checks['all_equal'] = all([
        checks['api_settings_equal'], checks['hub_db_settings_managed_equal'],
        checks['managed_key_set_equal'], checks['config_file_unchanged']])
    return checks
