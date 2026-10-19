#!/usr/bin/env python3
"""qa-resume-20261018-gate: full vitest run (web/src) + per-file flake reruns.

Command: cd web && npx vitest run --browser.headless
Known infra flake: "Cannot connect to the iframe" / "Cannot connect to the
server" (browser provider) — full runs may hang; on any non-clean exit every
real test file not clearly passed is re-run individually and classified.
Full-run output streams straight to vitest-full.log (visible even if hung);
timeouts kill the whole process group.
"""
import glob
import json
import os
import pathlib
import re
import signal
import subprocess
import time

WEB = pathlib.Path('/home/superc/code/nodesteer/web')
HERE = pathlib.Path(__file__).resolve().parent

all_files = sorted(
    p.relative_to(WEB).as_posix()
    for pattern in ('src/**/*.test.ts', 'src/**/*.test.tsx', 'src/**/*.spec.ts', 'src/**/*.spec.tsx')
    for p in map(pathlib.Path, glob.glob(str(WEB / pattern), recursive=True))
    if p.is_file()
)


def run_streamed(cmd, log_path, timeout):
    """Run cmd, stream stdout+stderr to log_path, kill process group on timeout."""
    t0 = time.time()
    timed_out = False
    with log_path.open('w') as stream:
        proc = subprocess.Popen(cmd, cwd=WEB, stdout=stream, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            rc = 124
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    return rc, timed_out, round(time.time() - t0, 1), log_path.read_text()


FILE_LINE = re.compile(r'^\s*([✓❯×])\s*\|chromium\|\s+(\S+)\s+\(([^)]*)\)', re.M)

exit_code, timed_out, dur, full_log = run_streamed(
    ['npx', 'vitest', 'run', '--browser.headless'], HERE / 'vitest-full.log', 600)

completed, failed = set(), {}
for mark, path, detail in FILE_LINE.findall(full_log):
    if mark == '✓':
        completed.add(path)
    else:
        failed[path] = detail

summary = {}
for label in ('Test Files', 'Tests', 'Duration'):
    m = re.search(rf'^\s*{label}\s+(.+)$', full_log, re.M)
    if m:
        summary[label] = m.group(1).strip()

num_passed = num_failed = num_total = None
m = re.search(r'^\s*Tests\s+(?:(\d+) failed \| )?(\d+) passed(?: \((\d+)\))?', full_log, re.M)
if m:
    num_failed = int(m.group(1)) if m.group(1) else 0
    num_passed = int(m.group(2))
    num_total = int(m.group(3)) if m.group(3) else num_passed + num_failed

flake_markers = ('Cannot connect to the iframe' in full_log) or ('Cannot connect to the server' in full_log)

missing = [f for f in all_files if f not in completed and f not in failed]
to_rerun = sorted(set(list(failed.keys()) + missing))

reruns = []
for path in to_rerun:
    if path not in all_files:
        # not a real test file (stale report line) — record and skip execution
        reruns.append({'file': path, 'exit': None, 'duration_sec': 0,
                       'passed': None, 'failed': None, 'reason': 'NOT_A_TEST_FILE', 'log': None})
        continue
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', path)
    log_rel = f'vitest-rerun-{safe}.log'
    rc, t_out, r_dur, log = run_streamed(
        ['npx', 'vitest', 'run', '--browser.headless', path], HERE / log_rel, 600)
    m = re.search(r'^\s*Tests\s+(?:(\d+) failed \| )?(\d+) passed', log, re.M)
    reruns.append({
        'file': path, 'exit': rc, 'timed_out': t_out, 'duration_sec': r_dur,
        'passed': int(m.group(2)) if m else None,
        'failed': int(m.group(1)) if (m and m.group(1)) else (0 if m else None),
        'reason': 'MISSING_FROM_FULL_RUN' if path in missing else 'FAILED_IN_FULL_RUN',
        'log': log_rel,
    })

real_reruns = [r for r in reruns if r['exit'] is not None]
result = {
    'command': 'cd web && npx vitest run --browser.headless',
    'exit_code': exit_code, 'timed_out': timed_out, 'duration_sec': dur,
    'files_total': len(all_files),
    'files_completed_in_full_run': len(completed),
    'files_failed_in_full_run': sorted(failed.keys()),
    'files_missing_from_full_run': missing,
    'summary_lines': summary,
    'num_total': num_total, 'num_passed': num_passed, 'num_failed': num_failed,
    'infra_flake_markers_present': flake_markers,
    'reruns': reruns,
    'rerun_all_real_files_passed': bool(real_reruns) and all(r['exit'] == 0 for r in real_reruns),
    'rerun_total_passed': sum(r['passed'] or 0 for r in real_reruns),
}
result['status'] = (
    'PASS' if (exit_code == 0 and not failed and not missing) else
    'FLAKE_RESOLVED' if result['rerun_all_real_files_passed'] else
    'FAIL'
)
(HERE / 'vitest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps({k: result[k] for k in ('exit_code', 'timed_out', 'duration_sec', 'files_total',
                                         'files_completed_in_full_run', 'files_failed_in_full_run',
                                         'files_missing_from_full_run', 'num_total', 'num_passed',
                                         'num_failed', 'infra_flake_markers_present',
                                         'rerun_all_real_files_passed', 'rerun_total_passed', 'status')},
                 ensure_ascii=False, indent=1))
