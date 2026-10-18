#!/usr/bin/env python3
"""只采集独立 dummy Agent 对 localhost:18997 的重连 SYN 间隔。"""
import collections
import datetime
import json
import os
import pathlib
import queue
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

os.umask(0o077)
OUT = pathlib.Path(__file__).parent
PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
CONNECTION = PRIVATE / 'connection.json'
CFG = json.loads(CONNECTION.read_text())
NODE_ID = 'd77e2440-919f-4431-a488-733817dcd809'
NODE = next(n for n in CFG['nodes'] if n['id'] == NODE_ID)
GUEST_HELPER = OUT.parent / 'guest-exec.py'
RUN_TAG = secrets.token_hex(5)
OWN_DIR = f'/tmp/qa-20260930-101945-luna-jitter-{RUN_TAG}'
DUMMY_AGENT_ID = str(uuid.uuid4())
EXPECTED_EXE = '/usr/local/bin/nodesteer-agent'
EXPECTED_SHA = '43bf33d06e775bd6581f120a002d6d904de36de6c6d4e18eaef90f6862fb6998'
TCPDUMP_PID = None
AGENT_TIMEOUT_PID = None
TCPDUMP_PROC = None
AGENT_PROC = None
TCPDUMP_LINES = queue.Queue()
TCPDUMP_THREAD = None
AGENT_THREAD = None
TCPDUMP_EXIT = None
AGENT_EXIT = None
START_MONO = None
REPORT = {
    'run_id': CFG['run_id'],
    'case_id': 'SYS18',
    'status': 'running',
    'classification': 'ISOLATED_DUMMY_AGENT_RECONNECT_JITTER',
    'target_guest': {'node_id': NODE_ID, 'ssh_host': NODE['ssh_host']},
    'dummy': {'agent_id': DUMMY_AGENT_ID, 'hub_url': 'ws://127.0.0.1:18997/ws', 'data_dir': OWN_DIR},
    'capture': {
        'interface': 'lo',
        'filter': 'TCP dst port 18997 and SYN=1 ACK=0',
        'snaplen': 96,
        'payload_capture': False,
        'path': 'environment/jitter/reconnect-syn.txt',
        'dedup_key': 'source_port+tcp_sequence',
    },
    'code_reference': {'path': 'internal/agent/connection/manager.go', 'delay': '2 seconds + random [0, 2 seconds)', 'line': 229},
    'remote_primary_mutations': 0,
    'uses_primary_credentials': False,
    'uses_primary_config_or_data': False,
}


def save():
    REPORT['recorded_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT / 'sys18-jitter.json').write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + '\n')


def run_guest(script, timeout=30):
    p = subprocess.run(
        ['python3', str(GUEST_HELPER), '--node', NODE_ID, '--timeout', str(timeout), '--connection', str(CONNECTION)],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout + 5,
        check=False,
    )
    if p.returncode:
        raise RuntimeError(f'guest read/action failed with exit {p.returncode}')
    return p.stdout


def primary_node_snapshot():
    req = urllib.request.Request(CFG['base_url'] + '/api/nodes', headers={'Authorization': 'Bearer ' + CFG['admin_token']})
    with urllib.request.urlopen(req, timeout=12) as r:
        nodes = json.loads(r.read())
        status = r.status
    node = next(x for x in nodes if x.get('id') == NODE_ID)
    return {
        'http': status,
        'id': node.get('id'),
        'agent_id': node.get('agent_id'),
        'status': node.get('status'),
        'sync_status': node.get('sync_status'),
        'agent_version': node.get('agent_version'),
    }


def remote_baseline():
    result = run_guest(r'''set -eu
service=nodesteer-agent.service
active=$(systemctl is-active "$service")
pid=$(systemctl show -p MainPID --value "$service")
exe=$(readlink -f "/proc/$pid/exe")
sha=$(sha256sum "$exe" | cut -d' ' -f1)
listeners=$(ss -Hlnpt '( sport = :18997 )')
timeout_bin=$(command -v timeout)
tcpdump_bin=$(command -v tcpdump)
python_bin=$(command -v python3)
printf '{"service_active":"%s","pid":%s,"exe":"%s","sha256":"%s","listener18997":"%s","timeout":"%s","tcpdump":"%s","python3":"%s"}\n' "$active" "$pid" "$exe" "$sha" "$listeners" "$timeout_bin" "$tcpdump_bin" "$python_bin"
''')
    row = json.loads(result.strip().splitlines()[-1])
    expected = row['service_active'] == 'active' and row['pid'] == 398003 and row['exe'] == EXPECTED_EXE and row['sha256'] == EXPECTED_SHA and not row['listener18997'] and all(row[x] for x in ['timeout', 'tcpdump', 'python3'])
    if not expected:
        raise RuntimeError('guest baseline differs from authorized SYS18 precondition')
    return row


def launch_remote(script, name):
    proc = subprocess.Popen(
        ['python3', str(GUEST_HELPER), '--node', NODE_ID, '--timeout', '240', '--connection', str(CONNECTION)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    proc.stdin.write(script)
    proc.stdin.close()
    return proc


def pipe_lines(stream, dest, output_path=None):
    out = open(output_path, 'a', encoding='utf-8') if output_path else None
    try:
        for line in stream:
            if out:
                out.write(line)
                out.flush()
            dest.put(line.rstrip('\n'))
    finally:
        if out:
            out.close()


def wait_marker(q, prefix, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = q.get(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise TimeoutError(f'{prefix} readiness marker timed out')


def verify_owned_process(pid, fragment):
    script = f'''set -eu
pid={int(pid)}
kill -0 "$pid"
cmd=$(tr '\\000' ' ' < "/proc/$pid/cmdline")
case "$cmd" in *{fragment}*) printf '{{"pid":%s,"alive":true,"command_matches":true}}\\n' "$pid" ;; *) exit 79 ;; esac
'''
    rows = [x for x in run_guest(script).splitlines() if x.startswith('{')]
    return json.loads(rows[-1])


def capture_rows():
    path = OUT / 'reconnect-syn.txt'
    regex = re.compile(r'^(\d+\.\d+) IP 127\.0\.0\.1\.(\d+) > 127\.0\.0\.1\.18997: Flags \[S\], seq (\d+)')
    rows = {}
    tcpdump_stats = {}
    if not path.exists():
        return rows, tcpdump_stats
    for line in path.read_text(errors='replace').splitlines():
        m = regex.search(line)
        if m:
            timestamp, src_port, seq = m.groups()
            rows.setdefault((src_port, seq), float(timestamp))
        for key, pattern in [
            ('captured_packets', r'^(\d+) packets captured$'),
            ('received_by_filter', r'^(\d+) packets received by filter$'),
            ('dropped_by_kernel', r'^(\d+) packets dropped by kernel$'),
        ]:
            stat = re.search(pattern, line.strip())
            if stat:
                tcpdump_stats[key] = int(stat.group(1))
    return rows, tcpdump_stats


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    idx = (len(sorted_values) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (idx - lo)


def analyze_samples():
    rows, stats = capture_rows()
    times = sorted(rows.values())
    gaps = [b - a for a, b in zip(times, times[1:])]
    bins = collections.Counter(round(gap * 10) / 10 for gap in gaps)
    whole_second_bins = collections.Counter(int(gap) for gap in gaps)
    spread = max(gaps) - min(gaps) if gaps else 0
    jitter_demonstrated = len(times) >= 30 and spread >= 0.8 and whole_second_bins.get(2, 0) > 0 and whole_second_bins.get(3, 0) > 0
    return {
        'unique_syn_attempts': len(times),
        'duplicate_syn_packets_removed': max(0, sum(1 for line in (OUT / 'reconnect-syn.txt').read_text(errors='replace').splitlines() if 'Flags [S]' in line) - len(times)) if (OUT / 'reconnect-syn.txt').exists() else 0,
        'unique_inter_attempt_gaps': len(gaps),
        'gap_seconds': {
            'min': round(min(gaps), 3) if gaps else None,
            'max': round(max(gaps), 3) if gaps else None,
            'p50': round(percentile(sorted(gaps), 0.50), 3) if gaps else None,
            'p95': round(percentile(sorted(gaps), 0.95), 3) if gaps else None,
        },
        'observed_gap_spread_seconds': round(spread, 3) if gaps else None,
        'whole_second_bins': {str(k): whole_second_bins[k] for k in sorted(whole_second_bins)},
        'seconds_scale_jitter_demonstrated': jitter_demonstrated,
        'gap_bins_0_1s': {f'{k:.1f}': bins[k] for k in sorted(bins)},
        'tcpdump_statistics': stats,
    }


def check_remote_own_state():
    script = f'''set -eu
python3 - {OWN_DIR} <<'PY'
import json, pathlib, sqlite3, sys
root=pathlib.Path(sys.argv[1])
files=[p for p in root.rglob('*') if p.is_file()]
dbs=[]
for path in files:
 try:
  db=sqlite3.connect(f"file:{{path}}?mode=ro",uri=True,timeout=2)
  tables=[r[0] for r in db.execute("select name from sqlite_master where type='table' order by name")]
  if tables:
   counts={{}}
   for table in tables:
    if table.lower() in {{'tasks','schedules','deployments','executions'}}:
     counts[table]=db.execute('select count(*) from "'+table.replace('"','""')+'"').fetchone()[0]
   dbs.append({{'file':str(path.relative_to(root)),'tables':tables,'business_counts':counts}})
  db.close()
 except (sqlite3.DatabaseError,OSError):
  pass
print(json.dumps({{'file_count':len(files),'sqlite_databases':dbs}}))
PY
python3 - {OWN_DIR}/agent.log <<'PY'
import json,pathlib,re,sys
p=pathlib.Path(sys.argv[1]); text=p.read_text(errors='replace') if p.exists() else ''
print(json.dumps({{'log_present':p.exists(),'line_count':len(text.splitlines()),'reconnect_error_lines':sum(bool(re.search(r'connect|reconnect|dial|refused',x,re.I)) for x in text.splitlines()),'failures_total_mentions':text.count('failures_total')}}))
PY
'''
    lines = run_guest(script, timeout=35).splitlines()
    decoded = [json.loads(line) for line in lines if line.startswith('{')]
    return decoded


def cleanup_remote_dir():
    script = f'''set -eu
dir={OWN_DIR}
case "$dir" in /tmp/qa-20260930-101945-luna-jitter-*) ;; *) exit 77 ;; esac
if [ -d "$dir" ]; then rm -rf -- "$dir"; fi
test ! -e "$dir"
printf '{{"own_dir_removed":true}}\\n'
'''
    rows = [x for x in run_guest(script, timeout=30).splitlines() if x.startswith('{')]
    return json.loads(rows[-1])


def stop_owned_remote(pid, signal_name, expected):
    if pid is None:
        return {'requested': False, 'reason': 'process was not launched'}
    # Signal exactly the recorded PID after validating its live command line.
    script = f'''set -eu
pid={int(pid)}
if ! kill -0 "$pid" 2>/dev/null; then printf '{{"stopped":true,"already_exited":true}}\\n'; exit 0; fi
cmd=$(tr '\\000' ' ' < "/proc/$pid/cmdline")
case "$cmd" in *{expected}*) ;; *) exit 78 ;; esac
kill -{signal_name} "$pid"
printf '{{"stopped":true,"signal":"{signal_name}","target":"{expected}"}}\\n'
'''
    rows = [x for x in run_guest(script, timeout=25).splitlines() if x.startswith('{')]
    return json.loads(rows[-1])


try:
    OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    pre = remote_baseline()
    api_pre = primary_node_snapshot()
    if api_pre['http'] != 200 or api_pre['status'] != 'online' or api_pre['sync_status'] != 'synced':
        raise RuntimeError('primary Node2 API baseline is not online/synced')
    REPORT['baseline'] = {'guest': pre, 'primary_node2': api_pre}
    save()

    tcpdump_script = r'''tcpdump -i lo -nn -tt -l -s96 'tcp dst port 18997 and (tcp[tcpflags] & (tcp-syn|tcp-ack) = tcp-syn)' 2>&1 &
pid=$!
printf '__JITTER_TCPDUMP_READY__%s\n' "$pid"
wait "$pid"
rc=$?
printf '__JITTER_TCPDUMP_EXIT__%s\n' "$rc"
exit 0
'''
    TCPDUMP_PROC = launch_remote(tcpdump_script, 'tcpdump')
    tcpdump_q = queue.Queue()
    TCPDUMP_THREAD = threading.Thread(target=pipe_lines, args=(TCPDUMP_PROC.stdout, tcpdump_q, OUT / 'reconnect-syn.txt'), daemon=True)
    TCPDUMP_THREAD.start()
    TCPDUMP_PID = int(wait_marker(tcpdump_q, '__JITTER_TCPDUMP_READY__'))
    REPORT['tcpdump'] = {'pid': TCPDUMP_PID, 'local_pgid': TCPDUMP_PROC.pid, 'local_session': True, 'listening_confirmed': verify_owned_process(TCPDUMP_PID, 'tcpdump')}
    save()

    setup = f'''set -eu
dir={OWN_DIR}
umask 077
mkdir -m 700 "$dir"
chmod 700 "$dir"
test -z "$(ls -A "$dir")"
printf '{{"data_dir_created":true,"mode":"0700","empty_before_launch":true}}\\n'
'''
    setup_rows = [x for x in run_guest(setup).splitlines() if x.startswith('{')]
    REPORT['dummy']['data_dir_preflight'] = json.loads(setup_rows[-1])
    agent_script = f'''set -u
umask 077
/usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin NODESTEER_HUB_URL=ws://127.0.0.1:18997/ws NODESTEER_DATA_DIR={OWN_DIR} NODESTEER_AGENT_ID={DUMMY_AGENT_ID} NODESTEER_REGISTRATION_TOKEN=qa-never-primary NODESTEER_DEPLOYMENT_MODE=native /usr/bin/timeout -s TERM -k 5s 180s {EXPECTED_EXE} > {OWN_DIR}/agent.log 2>&1 &
pid=$!
printf '__JITTER_AGENT_READY__%s\\n' "$pid"
wait "$pid"
rc=$?
printf '__JITTER_AGENT_EXIT__%s\\n' "$rc"
exit 0
'''
    AGENT_PROC = launch_remote(agent_script, 'dummy-agent')
    agent_q = queue.Queue()
    AGENT_THREAD = threading.Thread(target=pipe_lines, args=(AGENT_PROC.stdout, agent_q, None), daemon=True)
    AGENT_THREAD.start()
    AGENT_TIMEOUT_PID = int(wait_marker(agent_q, '__JITTER_AGENT_READY__'))
    START_MONO = time.monotonic()
    REPORT['agent_process'] = {'timeout_pid': AGENT_TIMEOUT_PID, 'local_pgid': AGENT_PROC.pid, 'local_session': True, 'timeout_seconds': 180, 'started_monotonic_recorded': True}
    save()

    while time.monotonic() - START_MONO < 185:
        samples = analyze_samples()
        REPORT['samples'] = samples
        REPORT['elapsed_seconds'] = round(time.monotonic() - START_MONO, 1)
        save()
        if TCPDUMP_PROC.poll() is not None:
            raise RuntimeError('task-owned tcpdump exited before sample window completed')
        if AGENT_PROC.poll() is not None:
            AGENT_EXIT = AGENT_PROC.returncode
            break
        time.sleep(10)
    if AGENT_EXIT is None and AGENT_PROC.poll() is None:
        raise TimeoutError('remote timeout/Agent wrapper exceeded its expected 180 second window')
    AGENT_EXIT = AGENT_PROC.returncode
    AGENT_THREAD.join(timeout=3)
    REPORT['agent_process']['wrapper_exit'] = AGENT_EXIT
    REPORT['duration_seconds'] = round(time.monotonic() - START_MONO, 2)

    TCPDUMP_STOP = stop_owned_remote(TCPDUMP_PID, 'INT', 'tcpdump')
    REPORT['tcpdump']['stop'] = TCPDUMP_STOP
    TCPDUMP_PROC.wait(timeout=25)
    TCPDUMP_THREAD.join(timeout=5)
    REPORT['tcpdump']['wrapper_exit'] = TCPDUMP_PROC.returncode
    REPORT['samples'] = analyze_samples()
    REPORT['agent_private_observations'] = check_remote_own_state()
    REPORT['post_baseline'] = {'guest': remote_baseline(), 'primary_node2': primary_node_snapshot()}
    REPORT['post_baseline']['guest_unchanged'] = all(REPORT['baseline']['guest'][k] == REPORT['post_baseline']['guest'][k] for k in ['service_active','pid','exe','sha256'])
    REPORT['post_baseline']['primary_node2_unchanged'] = all(REPORT['baseline']['primary_node2'][k] == REPORT['post_baseline']['primary_node2'][k] for k in ['id','agent_id','status','sync_status','agent_version'])
    REPORT['cleanup'] = {'tcpdump_stopped': TCPDUMP_PROC.poll() is not None, 'agent_timeout_completed': AGENT_EXIT is not None}
    REPORT['cleanup']['own_data_dir'] = cleanup_remote_dir()
    samples = REPORT['samples']
    REPORT['remaining_dimensions'] = ['Successful Hub HELLO and revision-cycle jitter were not exercised because localhost:18997 intentionally has no listener.']
    REPORT['status'] = 'PARTIAL' if samples['unique_syn_attempts'] < 30 or not samples['seconds_scale_jitter_demonstrated'] or not REPORT['post_baseline']['guest_unchanged'] or not REPORT['post_baseline']['primary_node2_unchanged'] or not REPORT['cleanup']['own_data_dir'].get('own_dir_removed') else 'PASS'
except Exception as exc:
    REPORT['status'] = 'PARTIAL'
    REPORT['error'] = {'type': type(exc).__name__, 'message': 'SYS18 isolated probe interrupted; only task-owned processes/temporary data are eligible for cleanup'}
finally:
    try:
        if AGENT_TIMEOUT_PID is not None and AGENT_PROC is not None and AGENT_PROC.poll() is None:
            REPORT.setdefault('cleanup_actions', []).append(stop_owned_remote(AGENT_TIMEOUT_PID, 'TERM', 'timeout'))
            AGENT_PROC.wait(timeout=15)
            REPORT['cleanup_actions'].append({'agent_wrapper_exit': AGENT_PROC.returncode})
    except Exception as exc:
        REPORT.setdefault('cleanup_errors', []).append(type(exc).__name__)
    try:
        if TCPDUMP_PID is not None and TCPDUMP_PROC is not None and TCPDUMP_PROC.poll() is None:
            REPORT.setdefault('cleanup_actions', []).append(stop_owned_remote(TCPDUMP_PID, 'INT', 'tcpdump'))
            TCPDUMP_PROC.wait(timeout=15)
            if TCPDUMP_THREAD:
                TCPDUMP_THREAD.join(timeout=5)
    except Exception as exc:
        REPORT.setdefault('cleanup_errors', []).append(type(exc).__name__)
    try:
        if OWN_DIR:
            REPORT.setdefault('cleanup', {})['own_data_dir'] = cleanup_remote_dir()
    except Exception as exc:
        REPORT.setdefault('cleanup_errors', []).append(type(exc).__name__)
    try:
        REPORT['samples'] = analyze_samples()
    except Exception:
        pass
    save()
    print(json.dumps({'case_id': 'SYS18', 'status': REPORT['status'], 'attempts': REPORT.get('samples', {}).get('unique_syn_attempts'), 'duration': REPORT.get('duration_seconds'), 'cleanup_errors': REPORT.get('cleanup_errors', [])}), flush=True)
