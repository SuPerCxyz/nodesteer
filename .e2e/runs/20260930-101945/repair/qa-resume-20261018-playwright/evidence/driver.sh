#!/usr/bin/env bash
# Sequential per-file driver for the repair-env Playwright E2E suite.
set -u
cd /tmp/opencode/nodesteer-pw-20261018
export QA_FILE_TIMEOUT=${QA_FILE_TIMEOUT:-1800}
FILES=(
  p02-layout p03-dashboard p04-nodes p06-groups p07-scripts p09-tasks
  p12-schedules p14-applications p16-artifacts p17-executions p19-misc
  p22-transfers p23-detail-layout p24-task-schedules rbac
  e2e-flows gap-ui full-feature-ui safe-ui
)
: > driver.log
for f in "${FILES[@]}"; do
  echo "=== START $f $(date -Is)" >> driver.log
  timeout $((QA_FILE_TIMEOUT + 120)) python3 run-pw.py "$f" "tests/$f.spec.ts" >> driver.log 2>&1
  echo "=== END $f rc=$? $(date -Is)" >> driver.log
done
echo "=== DRIVER DONE $(date -Is)" >> driver.log
