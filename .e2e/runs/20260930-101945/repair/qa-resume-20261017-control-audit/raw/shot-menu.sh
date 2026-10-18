#!/usr/bin/env bash
# Usage: shot-menu.sh <out-name> <url> <find-spec...>
set -uo pipefail
AUDIT_DIR="/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261017-control-audit"
SESSION="ctrlaudit-20261017"
NAME="$1"; URL="$2"; shift 2
FIND_SPEC="$*"
agent-browser --session "$SESSION" open "$URL" >/dev/null 2>&1
agent-browser --session "$SESSION" wait --load networkidle >/dev/null 2>&1 || agent-browser --session "$SESSION" wait 2000 >/dev/null 2>&1
sleep 1
if [ -n "$FIND_SPEC" ]; then
  eval "agent-browser --session \"$SESSION\" find $FIND_SPEC" >/dev/null 2>&1 || echo "FIND-FAIL $NAME"
  sleep 1
fi
agent-browser --session "$SESSION" screenshot "$AUDIT_DIR/evidence/$NAME.png" >/dev/null 2>&1 && echo "SHOT $NAME"
if [ -n "$FIND_SPEC" ]; then agent-browser --session "$SESSION" press Escape >/dev/null 2>&1; fi
