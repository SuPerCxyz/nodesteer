#!/usr/bin/env python3
"""qa-resume-20261018-gate: Go build/vet/test with per-command capture.

`go test ./internal/... -count=1`; if a package fails, that package is re-run
once with -count=1 to classify a pre-existing intermittent flake (known for
internal/agent) versus a deterministic failure.
"""
import json
import pathlib
import re
import subprocess
import time

REPO = pathlib.Path('/home/superc/code/nodesteer')
HERE = pathlib.Path(__file__).resolve().parent
LOG = HERE / 'go-tests.log'

results = {'commands': [], 'package_reruns': []}
log_lines = []


def run(cmd, timeout=1800, cwd=REPO):
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    dur = round(time.time() - t0, 1)
    log_lines.append(f'$ {" ".join(cmd)}  (exit={proc.returncode}, {dur}s)')
    log_lines.append(proc.stdout)
    log_lines.append(proc.stderr)
    return {'cmd': ' '.join(cmd), 'exit': proc.returncode, 'duration_sec': dur,
            'stdout_tail': proc.stdout[-4000:], 'stderr_tail': proc.stderr[-4000:]}


results['commands'].append(run(['go', 'build', './...']))
results['commands'].append(run(['go', 'vet', './...']))
test = run(['go', 'test', './internal/...', '-count=1'])
results['commands'].append(test)

# classify failing packages on the test command
failing = set(re.findall(r'^(FAIL)\s+(\S+)', test['stdout_tail'] + '\n' + test['stderr_tail'], re.M))
failing_pkgs = sorted({m[1] for m in re.findall(r'^(FAIL)\s+(\S+)', test['stdout_tail'] + '\n' + test['stderr_tail'], re.M)})
results['failing_packages_first_run'] = failing_pkgs
for pkg in failing_pkgs:
    rerun = run(['go', 'test', pkg, '-count=1'])
    results['package_reruns'].append({'package': pkg, 'exit': rerun['exit'], 'duration_sec': rerun['duration_sec'],
                                      'stdout_tail': rerun['stdout_tail'], 'stderr_tail': rerun['stderr_tail']})

results['build_ok'] = results['commands'][0]['exit'] == 0
results['vet_ok'] = results['commands'][1]['exit'] == 0
results['test_ok'] = test['exit'] == 0
results['all_reruns_ok'] = all(r['exit'] == 0 for r in results['package_reruns'])
results['status'] = 'PASS' if (results['build_ok'] and results['vet_ok'] and results['test_ok']) else (
    'FLAKE_RESOLVED' if (results['all_reruns_ok'] and failing_pkgs) else 'FAIL')

LOG.write_text('\n'.join(log_lines))
(HERE / 'go-tests.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps({k: results[k] for k in ('build_ok', 'vet_ok', 'test_ok', 'failing_packages_first_run', 'all_reruns_ok', 'status')}, ensure_ascii=False))
