#!/usr/bin/env bash
# Submit the prepared transfer form, capture the form state right after the create
# request resolves (P22-08 reset check), then wait for the terminal state.
# Usage: run_submit_observe.sh <tag> <marker> [poll_seconds] [settle_seconds]
set -u
B=/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261014-web/batch04
S=qaweb1410b04
TAG="$1"; MARKER="$2"; POLL="${3:-30}"; SETTLE="${4:-14}"
cd "$B" || exit 1
TMP=/tmp/opencode
python3 runners/form_transfer.py dump "evidence/${TAG}-01-form-before-submit.json" >/dev/null 2>&1
python3 runners/poll_transfers.py "evidence/${TAG}-timeline.json" "$POLL" > "$TMP/${TAG}-poll.log" 2>&1 &
POLL_PID=$!
sleep 0.4
agent-browser --session "$S" network requests --clear >/dev/null 2>&1
agent-browser --session "$S" find role button click --name "开始传输" >"$TMP/${TAG}-startclick.txt" 2>&1
sleep 0.6
python3 runners/form_transfer.py dump "evidence/${TAG}-02-form-after-submit.json" >/dev/null 2>&1
sleep "$SETTLE"
python3 - "$MARKER" > "$TMP/${TAG}-marker.js" <<'PY'
import json,sys
print("window.__b04Marker=%s; 'marker-set'" % json.dumps(sys.argv[1]))
PY
cat > "$TMP/${TAG}-rowscan.js" <<'JS'
(() => {
  const marker = window.__b04Marker;
  const rows=[...document.querySelectorAll('table tbody tr')];
  const row=rows.find(r=>r.innerText.includes(marker));
  return {found:Boolean(row), text:row?row.innerText.replace(/\s+/g,' ').slice(0,300):null,
          cells:row?[...row.cells].map(c=>c.innerText.replace(/\s+/g,' ').slice(0,160)):null,
          actionCellButtons:row?(function(){const c=row.cells[row.cells.length-1];return c?[...c.querySelectorAll('button')].map(b=>b.getAttribute('aria-label')):null})():null};
})()
JS
agent-browser --session "$S" eval --stdin < "$TMP/${TAG}-marker.js" >/dev/null 2>&1
agent-browser --session "$S" eval --stdin < "$TMP/${TAG}-rowscan.js" > "evidence/${TAG}-03-terminal-row.json" 2>&1
agent-browser --session "$S" screenshot "evidence/${TAG}-04-terminal.png" >/dev/null 2>&1
agent-browser --session "$S" network requests --json >"$TMP/${TAG}-net.json" 2>/dev/null
wait "$POLL_PID"
cat "evidence/${TAG}-03-terminal-row.json"
