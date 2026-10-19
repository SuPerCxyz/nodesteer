#!/usr/bin/env bash
set -u
cd /tmp/opencode/nodesteer-pw-20261018
export QA_FILE_TIMEOUT=${QA_FILE_TIMEOUT:-1800}
FILES=(
  p04-nodes p06-groups p07-scripts p09-tasks
  p12-schedules p14-applications p16-artifacts p17-executions p19-misc
  p22-transfers p23-detail-layout p24-task-schedules rbac
  e2e-flows gap-ui full-feature-ui safe-ui
)
for f in "${FILES[@]}"; do
  echo "=== START $f $(date -Is)" >> driver2.log
  timeout $((QA_FILE_TIMEOUT + 120)) python3 run-pw.py "$f" "tests/$f.spec.ts" >> driver2.log 2>&1
  echo "=== END $f rc=$? $(date -Is)" >> driver2.log
done
echo "=== DRIVER2 DONE $(date -Is)" >> driver2.log
