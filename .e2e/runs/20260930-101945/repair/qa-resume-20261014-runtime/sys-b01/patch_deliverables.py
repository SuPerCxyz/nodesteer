#!/usr/bin/env python3
"""Mechanical post-run bookkeeping patch (both batches).

The case-result.json evidence arrays are generated from the evidence/ directory
listing at run time.  Files that were added *after* the run (an independent
re-check snapshot for sys-b01, a derived ordering artifact for sys-b04) must be
appended so the report references every artifact that exists now.  No claim or
status is changed by this script; it only appends file names and records the
addendum.
"""
import json
import sys
from pathlib import Path

BATCHES = {
    'sys-b01': {
        'extra_evidence': ['final-cleanup-oracle.json',
                           'evidence/phase0-untouched-units-after.json',
                           'evidence/phase0-untouched-units.json',
                           'node_units.py'],
        'addendum': {
            'post_run_additions': [
                'evidence/phase0-untouched-units-after.json - identical read-only unit probe repeated AFTER the '
                'batch; compared with evidence/phase0-untouched-units.json it shows every protected Agent and '
                'the primary Hub kept the same MainPID, while only the repair Hub MainPID changed (authorised '
                'restart).',
                'evidence/attempt01-harness-bug/ - artifacts of the first attempt, kept unmodified. It failed on '
                'a harness bug (the repair-Hub restart invalidated the management API session, so the client did '
                'not re-authenticate; plus a %-formatting bug in the child remove helper). Both were fixed and '
                'the batch was re-run completely (EXIT=0); the report above is the second, complete run.'],
            'claims_changed': False,
        },
    },
    'sys-b04': {
        'extra_evidence': ['final-cleanup-oracle.json',
                           'evidence/phase5b-out-of-order-delivery-order.json',
                           'evidence/attempt01-cleanup-bookkeeping-bug/',
                           'derive_evidence.py'],
        'addendum': {
            'post_run_additions': [
                'evidence/phase5b-out-of-order-delivery-order.json - derived (read-only post-processing of the '
                'same run\'s raw proxy ledger) sub-second ordering proof: the older CHANGE_NOTIFICATION '
                '(global_revision 2647) was released to the Agent 0.235 s AFTER the newer one (global_revision '
                '2648).',
                'evidence/attempt01-cleanup-bookkeeping-bug/ - artifacts of the first attempt, kept unmodified. '
                'All phases passed with identical assertions; it failed only in the cleanup bookkeeping '
                '(a KeyError while assembling the GET404 summary for fixtures deleted inside the prune probe). '
                'The bug was fixed and the whole batch re-run (EXIT=0); the report above is the second run.'],
            'claims_changed': False,
        },
    },
}


def main(root):
    root = Path(root)
    for batch, spec in BATCHES.items():
        p = root / batch / 'case-result.json'
        doc = json.loads(p.read_text())
        for case in doc['cases']:
            for name in spec['extra_evidence']:
                if name not in case['evidence']:
                    case['evidence'].append(name)
                case['evidence'] = sorted(set(case['evidence']))
        doc['post_run_addendum'] = spec['addendum']
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
        (root / batch / 'post-run-addendum.json').write_text(
            json.dumps(spec['addendum'], ensure_ascii=False, indent=2) + '\n')
        print(batch, '->', {c['case_id']: c['proposed_after'] for c in doc['cases']},
              '| evidence entries per case:', len(doc['cases'][0]['evidence']))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1
         else '/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime')
