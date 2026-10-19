#!/usr/bin/env bash
set -u
cd /tmp/opencode/nodesteer-pw-20261018
export QA_FILE_TIMEOUT=${QA_FILE_TIMEOUT:-2400}
: > rerun.log
run() { echo "=== START $1 $(date -Is)" >> rerun.log; timeout $((QA_FILE_TIMEOUT+120)) python3 run-pw.py "$@" >> rerun.log 2>&1; echo "=== END $1 rc=$? $(date -Is)" >> rerun.log; }

run rerun-p23-detail-layout tests/p23-detail-layout.spec.ts
run rerun-rbac tests/rbac.spec.ts
run rerun-gap-subset tests/gap-ui.spec.ts -g "GAP-07|GAP-14|GAP-16|GAP-20"
run rerun-safe-subset tests/safe-ui.spec.ts -g "SAFE-01|SAFE-05|SAFE-21|SAFE-24"
echo "=== RERUN DONE $(date -Is)" >> rerun.log
