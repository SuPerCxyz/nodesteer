#!/usr/bin/env python3
"""Read-only exact-App deployment observer; guarded SIGSTOP/SIGKILL branch is parent-review gated."""
import argparse
import hashlib
import json
import os
import pathlib
import re
import signal
import sqlite3
import subprocess
import time

ROOT = pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee')
OWNER_NONCE = '870358cd92ee'
CHILD_NODE_ID = 'c77b8a84-8085-4ea8-8532-de171761854b'
CHILD_AGENT_ID = 'agent-426aa507-af4c-4005-858f-ce497d2fc67d'
CHILD_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service'
PARENT_UNIT = 'nodesteer-agent-repair-20260930-101945-native-1.service'
CHILD_CONFIG = str(ROOT / 'agent.yaml')
CHILD_BINARY = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
CHILD_BINARY_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
STATE_DB = ROOT / 'state/state.db'
APP_ID = '2e4fecee-afc8-43a8-9d93-dd36cfc10bf9'
APP_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'
APP_UNIT_PATH = pathlib.Path('/etc/systemd/system') / APP_UNIT
APP_BINARY = ROOT / 'app/bin/qa-app'
APP_CONFIG = ROOT / 'app/config/qa-app.env'
V2_ARTIFACT_SHA = '145333b2830f512362ca1ef187d92599cb022b07666e8a53e6f8f0dd5bd16317'
V1_BINARY_SHA = '35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c'
V1_CONFIG_SHA = '5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34'
V1_UNIT_SHA = '6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'
POLL_SECONDS = 0.001
OBSERVE_TIMEOUT_SECONDS = 45


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def unit_props(unit):
    p = subprocess.run(['systemctl', 'show', unit, '-p', 'LoadState', '-p',
                        'ActiveState', '-p', 'SubState', '-p', 'MainPID',
                        '-p', 'ControlGroup'], text=True, capture_output=True,
                       timeout=5)
    result = {'rc': p.returncode}
    for line in p.stdout.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            result[k] = v
    return result


def process_fingerprint(pid):
    raw = open(f'/proc/{pid}/stat').read()
    rest = raw[raw.rfind(')') + 2:].split()
    state = rest[0]
    status = open(f'/proc/{pid}/status').read()
    uid_match = re.search(r'^Uid:\s+(\d+)', status, re.M)
    argv_raw = open(f'/proc/{pid}/cmdline', 'rb').read()
    argv = [x.decode(errors='replace') for x in argv_raw.split(b'\0') if x]
    return {
        'pid': pid,
        'ppid': int(rest[1]),
        'state': state,
        'starttime_ticks': int(rest[19]),
        'uid': int(uid_match.group(1)) if uid_match else None,
        'argv': argv,
        'argv_sha256': hashlib.sha256(argv_raw).hexdigest(),
        'exe_path': os.readlink(f'/proc/{pid}/exe'),
        'exe_sha256': digest(f'/proc/{pid}/exe'),
        'cgroup': open(f'/proc/{pid}/cgroup').read().strip().splitlines(),
    }


def observer_parent_context():
    current = os.getppid()
    seen = set()
    for _ in range(12):
        if current <= 1 or current in seen:
            break
        seen.add(current)
        try:
            proc = process_fingerprint(current)
        except Exception:
            break
        cgroup = proc['cgroup'][0].split(':', 2)[-1] if proc['cgroup'] else ''
        if cgroup == '/system.slice/' + PARENT_UNIT:
            return {
                'valid': (proc['uid'] == 0 and proc['exe_path'] == CHILD_BINARY and
                          proc['exe_sha256'] == CHILD_BINARY_SHA and
                          proc['argv'][:1] == [CHILD_BINARY] and
                          proc['pid'] != int(unit_props(CHILD_UNIT).get('MainPID', '0') or 0)),
                'parent_unit': PARENT_UNIT,
                'process': {k: proc.get(k) for k in
                            ['pid', 'ppid', 'starttime_ticks', 'uid', 'argv',
                             'argv_sha256', 'exe_path', 'exe_sha256', 'cgroup']},
            }
        current = proc['ppid']
    return {'valid': False, 'parent_unit': PARENT_UNIT,
            'reason': 'observer process is not a descendant of the approved Native-1 repair Agent'}


def owner_marker():
    value = json.loads((ROOT / 'owner.json').read_text())
    expected = {
        'owner_nonce': OWNER_NONCE,
        'node_id': CHILD_NODE_ID,
        'agent_id': CHILD_AGENT_ID,
        'unit': CHILD_UNIT,
        'binary_path': CHILD_BINARY,
        'binary_sha256': CHILD_BINARY_SHA,
        'config_path': CHILD_CONFIG,
        'data_dir': str(ROOT / 'state'),
    }
    mismatches = {key: {'actual': value.get(key), 'expected': expected_value}
                  for key, expected_value in expected.items()
                  if value.get(key) != expected_value}
    return {'owner_nonce': value.get('owner_nonce'), 'node_id': value.get('node_id'),
            'agent_id': value.get('agent_id'), 'unit': value.get('unit'),
            'mismatches': mismatches, 'valid': not mismatches}


def read_v2_deployments():
    con = sqlite3.connect('file:' + str(STATE_DB) + '?mode=ro', uri=True,
                          timeout=0.2)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=200')
    rows = con.execute(
        'SELECT id,application_id,from_version,to_version,phase,backup_path,'
        'backup_config_path,backup_unit_path,started_at,updated_at '
        'FROM deployments WHERE application_id=? AND to_version=? ORDER BY started_at DESC',
        (APP_ID, 'v2-attempt2')).fetchall()
    result = [dict(row) for row in rows]
    con.close()
    return result


def v2_cache_metadata():
    path = ROOT / 'state/artifacts' / V2_ARTIFACT_SHA
    con = sqlite3.connect('file:' + str(STATE_DB) + '?mode=ro', uri=True,
                          timeout=0.2)
    con.row_factory = sqlite3.Row
    row = con.execute('SELECT sha256,path FROM artifacts WHERE sha256=?',
                      (V2_ARTIFACT_SHA,)).fetchone()
    con.close()
    return {
        'path': str(path),
        'exists': path.is_file(),
        'size': path.stat().st_size if path.is_file() else None,
        'registered_sha256': row['sha256'] if row else None,
        'registered_path': row['path'] if row else None,
    }


def app_physical_state():
    props = unit_props(APP_UNIT)
    active = subprocess.run(['systemctl', 'is-active', APP_UNIT], text=True,
                             capture_output=True, timeout=5)
    values = {
        'binary_sha256': digest(APP_BINARY) if APP_BINARY.is_file() else None,
        'config_sha256': digest(APP_CONFIG) if APP_CONFIG.is_file() else None,
        'unit_sha256': digest(APP_UNIT_PATH) if APP_UNIT_PATH.is_file() else None,
        'unit_active': active.stdout.strip() == 'active' and active.returncode == 0,
        'unit_status': active.stdout.strip(),
        'unit_main_pid': props.get('MainPID'),
        'unit_properties': props,
    }
    values['matches_v1'] = (
        values['binary_sha256'] == V1_BINARY_SHA and
        values['config_sha256'] == V1_CONFIG_SHA and
        values['unit_sha256'] == V1_UNIT_SHA and values['unit_active'])
    return values


def child_guard(expected=None, require_running=True):
    owner = owner_marker()
    if not owner['valid']:
        return {'valid': False, 'reason': 'owner marker mismatch', 'owner': owner}
    if digest(CHILD_BINARY) != CHILD_BINARY_SHA:
        return {'valid': False, 'reason': 'repair binary path SHA changed', 'owner': owner}
    props = unit_props(CHILD_UNIT)
    pid = int(props.get('MainPID', '0') or 0)
    if pid <= 0:
        return {'valid': False, 'reason': 'child Unit has no MainPID', 'owner': owner,
                'unit': props}
    try:
        proc = process_fingerprint(pid)
    except Exception as exc:
        return {'valid': False, 'reason': 'cannot read exact child PID',
                'error': type(exc).__name__, 'owner': owner, 'unit': props}
    expected_argv = [CHILD_BINARY, '--config', CHILD_CONFIG]
    cgroup = proc['cgroup'][0].split(':', 2)[-1] if proc['cgroup'] else ''
    checks = {
        'main_pid_matches': str(pid) == props.get('MainPID'),
        'uid_root': proc['uid'] == 0,
        'argv_exact_owned_config': proc['argv'] == expected_argv,
        'exe_path_exact': proc['exe_path'] == CHILD_BINARY,
        'exe_sha_expected': proc['exe_sha256'] == CHILD_BINARY_SHA,
        'unit_control_group_exact': cgroup == props.get('ControlGroup') == '/system.slice/' + CHILD_UNIT,
        'unit_state_active': props.get('ActiveState') == 'active' if require_running else True,
        'process_not_zombie': proc['state'] != 'Z',
    }
    if expected is not None:
        for field in ['pid', 'starttime_ticks', 'uid', 'argv_sha256', 'exe_sha256', 'cgroup']:
            checks['same_' + field] = proc.get(field) == expected.get(field)
    return {'valid': all(checks.values()), 'checks': checks, 'owner': owner,
            'unit': props, 'process': proc}


def deployment_matches(row):
    return (row.get('phase') == 'PREPARING' and row.get('backup_path') == '' and
            row.get('backup_config_path') == '' and row.get('backup_unit_path') == '')


def maybe_continue(pid, baseline, wait_seconds=1.0):
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            current = process_fingerprint(pid)
        except Exception:
            return {'continued': False, 'reason': 'PID no longer readable; do not signal a reused PID'}
        same = all(current.get(k) == baseline.get(k) for k in
                    ['pid', 'starttime_ticks', 'uid', 'argv_sha256', 'exe_sha256', 'cgroup'])
        if not same:
            return {'continued': False, 'reason': 'PID owner fingerprint changed; do not signal'}
        if current.get('state') in {'T', 't'}:
            try:
                os.kill(pid, signal.SIGCONT)
            except Exception as exc:
                return {'continued': False, 'reason': 'SIGCONT failed after exact owner check',
                        'error_type': type(exc).__name__}
            return {'continued': True, 'pid': pid,
                    'starttime_ticks': baseline['starttime_ticks']}
        time.sleep(.01)
    return {'continued': False, 'reason': 'exact PID did not enter stopped state before continuation deadline'}


def main(args):
    started = time.monotonic()
    result = {
        'observer': 'P0-002 exact-AppID read-only Deployment Journal observer',
        'app_id': APP_ID, 'child_node_id': CHILD_NODE_ID,
        'child_unit': CHILD_UNIT, 'owner_nonce': OWNER_NONCE,
        'v2_artifact_sha256': V2_ARTIFACT_SHA,
        'v1_expected': {'binary_sha256': V1_BINARY_SHA,
                        'config_sha256': V1_CONFIG_SHA, 'unit_sha256': V1_UNIT_SHA},
        'mode': 'signal-gated; explicit --execute-fault and exact --owner-nonce are required',
        'execution_enabled': bool(args.execute_fault and args.owner_nonce == OWNER_NONCE),
    }
    if not args.execute_fault or args.owner_nonce != OWNER_NONCE:
        result['observer_status'] = 'PREPARE_ONLY_NO_SIGNAL_FLAG_OR_NONCE_MISMATCH'
        return result
    result['observer_context'] = observer_parent_context()
    if not result['observer_context'].get('valid'):
        result['observer_status'] = 'WRONG_EXECUTION_CONTEXT'
        return result
    initial_guard = child_guard(require_running=True)
    result['initial_child_guard'] = initial_guard
    result['initial_app_physical'] = app_physical_state()
    result['initial_v2_cache'] = v2_cache_metadata()
    cache_ready = (result['initial_v2_cache'].get('exists') and
                   result['initial_v2_cache'].get('size') == 67108864 and
                   result['initial_v2_cache'].get('registered_sha256') == V2_ARTIFACT_SHA and
                   result['initial_v2_cache'].get('registered_path') == result['initial_v2_cache'].get('path'))
    if (not initial_guard.get('valid') or not result['initial_app_physical'].get('matches_v1')
            or not cache_ready):
        result['observer_status'] = 'INITIAL_OWNER_OR_V1_STATE_GUARD_FAILED'
        return result
    baseline = initial_guard['process']
    print(json.dumps({
        'observer_status': 'READY_ARMED_WAITING_FOR_EXACT_PREPARING',
        'owner_nonce': OWNER_NONCE,
        'app_id': APP_ID,
        'child_node_id': CHILD_NODE_ID,
        'child_pid': baseline['pid'],
        'child_starttime_ticks': baseline['starttime_ticks'],
        'child_unit': CHILD_UNIT,
        'v2_artifact_sha256': V2_ARTIFACT_SHA,
        'v1_physical_state_matches': result['initial_app_physical']['matches_v1'],
        'v2_cache_size': result['initial_v2_cache']['size'],
    }, sort_keys=True), flush=True)
    killed = False
    deadline = time.monotonic() + OBSERVE_TIMEOUT_SECONDS
    polls = 0
    deployment_rows_seen = 0
    while time.monotonic() < deadline:
        polls += 1
        try:
            rows = read_v2_deployments()
        except Exception as exc:
            result['observer_status'] = 'READ_ONLY_SQLITE_ERROR'
            result['error_type'] = type(exc).__name__
            result['polls'] = polls
            return result
        deployment_rows_seen = max(deployment_rows_seen, len(rows))
        if len(rows) > 1:
            result['observer_status'] = 'AMBIGUOUS_MULTIPLE_V2_DEPLOYMENTS'
            result['candidate_deployment_ids'] = [row.get('id') for row in rows]
            result['polls'] = polls
            return result
        row = rows[0] if rows else None
        if not row or not deployment_matches(row):
            time.sleep(POLL_SECONDS)
            continue

        result['deployment_candidate'] = row
        before_guard = child_guard(expected=baseline, require_running=True)
        before_app = app_physical_state()
        before_cache = v2_cache_metadata()
        current_rows = read_v2_deployments()
        current = next((item for item in current_rows if item.get('id') == row['id']), None)
        cache_unchanged = (before_cache.get('exists') and before_cache.get('size') == 67108864 and
                           before_cache.get('registered_sha256') == V2_ARTIFACT_SHA and
                           before_cache.get('registered_path') == before_cache.get('path'))
        if (not before_guard.get('valid') or not before_app.get('matches_v1') or not cache_unchanged or
                current is None or not deployment_matches(current)):
            result['observer_status'] = 'PRE_SIGNAL_GUARD_REJECTED'
            result['pre_signal_child_guard'] = before_guard
            result['pre_signal_app_physical'] = before_app
            result['pre_signal_v2_cache'] = before_cache
            result['pre_signal_deployment'] = current
            result['polls'] = polls
            return result

        result['pre_signal_child_guard'] = before_guard
        result['pre_signal_app_physical'] = before_app
        result['pre_signal_deployment'] = current
        result['sigstop_time_unix_ns'] = time.time_ns()
        os.kill(baseline['pid'], signal.SIGSTOP)
        result['sigstop_sent'] = True
        try:
            time.sleep(.05)
            frozen_guard = child_guard(expected=baseline, require_running=False)
            frozen_row_list = read_v2_deployments()
            frozen_row = next((item for item in frozen_row_list if item.get('id') == row['id']), None)
            frozen_app = app_physical_state()
            frozen_cache = v2_cache_metadata()
            result['frozen_child_guard'] = frozen_guard
            result['frozen_deployment'] = frozen_row
            result['frozen_app_physical'] = frozen_app
            result['frozen_v2_cache'] = frozen_cache
            is_stopped = (frozen_guard.get('valid') and
                          frozen_guard.get('process', {}).get('state') in {'T', 't'})
            still_preparing = frozen_row is not None and deployment_matches(frozen_row)
            cache_still_ready = (frozen_cache.get('exists') and frozen_cache.get('size') == 67108864 and
                                 frozen_cache.get('registered_sha256') == V2_ARTIFACT_SHA and
                                 frozen_cache.get('registered_path') == frozen_cache.get('path'))
            if not is_stopped or not still_preparing or not frozen_app.get('matches_v1') or not cache_still_ready:
                result['continue_result'] = maybe_continue(baseline['pid'], baseline)
                result['observer_status'] = 'PREPARING_WINDOW_MISSED_AFTER_SIGSTOP_CONTINUED'
                result['polls'] = polls
                return result

            # Final identity, journal, and old-file guard immediately before the only SIGKILL.
            final_guard = child_guard(expected=baseline, require_running=False)
            final_rows = read_v2_deployments()
            final_row = next((item for item in final_rows if item.get('id') == row['id']), None)
            final_app = app_physical_state()
            final_cache = v2_cache_metadata()
            if (not final_guard.get('valid') or
                    final_guard.get('process', {}).get('state') not in {'T', 't'} or
                    final_row is None or not deployment_matches(final_row) or
                    not final_app.get('matches_v1') or not final_cache.get('exists') or
                    final_cache.get('size') != 67108864 or
                    final_cache.get('registered_sha256') != V2_ARTIFACT_SHA or
                    final_cache.get('registered_path') != final_cache.get('path')):
                result['continue_result'] = maybe_continue(baseline['pid'], baseline)
                result['observer_status'] = 'FINAL_SIGKILL_GUARD_REJECTED_CONTINUED'
                result['final_child_guard'] = final_guard
                result['final_deployment'] = final_row
                result['final_app_physical'] = final_app
                result['polls'] = polls
                return result

            result['sigkill_precheck'] = {
                'deployment_id': row['id'], 'application_id': APP_ID,
                'phase': final_row['phase'], 'backup_fields_empty': True,
                'v1_binary_config_unit_match_and_active': final_app['matches_v1'],
                'pid': baseline['pid'], 'starttime_ticks': baseline['starttime_ticks'],
                'uid': baseline['uid'], 'argv_sha256': baseline['argv_sha256'],
                'exe_sha256': baseline['exe_sha256'],
                'control_group': baseline['cgroup'],
            'v2_cache_metadata': final_cache,
            }
            result['sigkill_time_unix_ns'] = time.time_ns()
            try:
                os.kill(baseline['pid'], signal.SIGKILL)
            except Exception as exc:
                result['sigkill_error_type'] = type(exc).__name__
                result['continue_result'] = maybe_continue(baseline['pid'], baseline)
                result['observer_status'] = 'SIGKILL_FAILED_CONTINUE_ATTEMPTED'
                result['polls'] = polls
                return result
            killed = True
            result['sigkill_sent'] = True
            result['observer_status'] = 'SIGKILL_SENT_OWNED_CHILD_ONLY'
            result['post_signal'] = {}
            wait_deadline = time.monotonic() + 3
            while time.monotonic() < wait_deadline:
                props = unit_props(CHILD_UNIT)
                result['post_signal']['child_unit'] = props
                if int(props.get('MainPID', '0') or 0) == 0 or props.get('ActiveState') == 'failed':
                    break
                time.sleep(.05)
            result['post_signal']['app_physical'] = app_physical_state()
            result['polls'] = polls
            result['elapsed_seconds'] = time.monotonic() - started
            return result
        except BaseException as exc:
            result['post_sigstop_error_type'] = type(exc).__name__
            result['observer_status'] = ('SIGKILL_ALREADY_SENT_POST_SIGNAL_OBSERVER_ERROR'
                                         if killed else 'POST_SIGSTOP_EXCEPTION_CONTINUE_DEFERRED_TO_FINALLY')
            result['polls'] = polls
        finally:
            if result.get('sigstop_sent') and not killed and not result.get('continue_result', {}).get('continued'):
                result['sigcont_finally'] = maybe_continue(baseline['pid'], baseline)
                if result['sigcont_finally'].get('continued'):
                    result['continue_result'] = result['sigcont_finally']
                    result['observer_status'] = result.get('observer_status', 'SIGCONT_SENT_IN_FINALLY')
                    if result['observer_status'] in {
                        'PREPARING_WINDOW_MISSED_AFTER_SIGSTOP_CONTINUED',
                        'FINAL_SIGKILL_GUARD_REJECTED_CONTINUED',
                        'POST_SIGSTOP_EXCEPTION_CONTINUE_DEFERRED_TO_FINALLY',
                    }:
                        result['observer_status'] = 'SIGCONT_SENT_NO_SIGKILL'
                elif result.get('observer_status') == 'POST_SIGSTOP_EXCEPTION_CONTINUE_DEFERRED_TO_FINALLY':
                    result['observer_status'] = 'POST_SIGSTOP_EXCEPTION_CONTINUE_NOT_CONFIRMED'
        if result.get('sigstop_sent'):
            result['elapsed_seconds'] = time.monotonic() - started
            return result

    result['observer_status'] = 'NO_PREPARING_WINDOW_MATCHED; NO_SIGNAL_SENT'
    result['polls'] = polls
    result['deployment_rows_seen'] = deployment_rows_seen
    result['elapsed_seconds'] = time.monotonic() - started
    result['final_child_guard'] = child_guard(expected=baseline, require_running=True)
    result['final_app_physical'] = app_physical_state()
    return result


parser = argparse.ArgumentParser()
parser.add_argument('--execute-fault', action='store_true')
parser.add_argument('--owner-nonce', default='')


def handle_termination(signum, frame):
    raise KeyboardInterrupt('observer command interrupted')


signal.signal(signal.SIGTERM, handle_termination)
signal.signal(signal.SIGINT, handle_termination)
try:
    print(json.dumps(main(parser.parse_args()), sort_keys=True), flush=True)
except Exception as exc:
    # The source is reviewed before execution. Errors are type-only, never payload/credential values.
    print(json.dumps({'observer_status': 'OBSERVER_EXCEPTION',
                      'error_type': type(exc).__name__,
                      'child_unit': CHILD_UNIT, 'app_id': APP_ID}, sort_keys=True))
