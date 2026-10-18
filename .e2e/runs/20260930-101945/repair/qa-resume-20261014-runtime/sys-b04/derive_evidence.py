#!/usr/bin/env python3
"""Derive the sub-second delivery-order evidence for the sys-b04 out-of-order seam
from the raw proxy ledger captured during the run (evidence/phase-frames.json).

This is a read-only post-processing step: it re-reads the raw ledger produced by
the harness frame proxy and writes the ordering facts as a separate evidence file
so the AR-12 claim is checkable without re-parsing the whole ledger.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ledger = json.loads((HERE / 'evidence/phase-frames.json').read_text())
frames = ledger['frames']
events = ledger['events']

ooo = json.loads((HERE / 'evidence/phase5-sys05-ar12-out-of-order.json').read_text())
deferred_frame = ooo['out_of_order']['deferred_frame']
deferred_rev = deferred_frame['global_revision']
oid = deferred_frame['object_id']

deferred = [f for f in frames if f.get('type') == 'CHANGE_NOTIFICATION'
            and f.get('global_revision') == deferred_rev and f.get('object_id') == oid]
passed_all = [f for f in frames if f.get('type') == 'CHANGE_NOTIFICATION'
              and f.get('object_id') == oid and f.get('action') == 'passed'
              and (f.get('global_revision') or 0) > deferred_rev]
passed_all.sort(key=lambda f: f.get('global_revision') or 0)
passed = passed_all[:1]
newer_rev = passed[0].get('global_revision') if passed else None
released = [e for e in events if e['event'] == 'deferred_released']

out = {
    'at_utc': ooo['at_utc'],
    'purpose': 'sub-second proof that the OLDER CHANGE_NOTIFICATION reached the owned child Agent '
               'AFTER the NEWER one (real out-of-order delivery)',
    'derived_from': 'evidence/phase-frames.json (raw harness frame-proxy ledger of the same run)',
    'object_id': oid,
    'older_revision_held': deferred_rev,
    'newer_revision_delivered_first': newer_rev,
    'held_frame_relayed_at': deferred[0]['at'] if deferred else None,
    'newer_frame_delivered_to_agent_at': passed[0]['at'] if passed else None,
    'older_frame_released_to_agent_at': released[0]['at'] if released else None,
    'older_reached_agent_after_newer': bool(deferred and passed and released
                                           and passed[0]['at'] < released[0]['at']),
    'newer_to_older_gap_seconds': (round(released[0]['at'] - passed[0]['at'], 3)
                                   if passed and released else None),
    'release_events': len(released),
    'final_child_state_is_hub_newest': ooo['out_of_order']['final_state_is_hub_newest'],
    'child_local_revision_monotonic_after_release':
        ooo['out_of_order']['child_local_revision_monotonic_after_release'],
}
assert out['older_reached_agent_after_newer'], 'out-of-order ordering not reproduced'
(HERE / 'evidence/phase5b-out-of-order-delivery-order.json').write_text(
    json.dumps(out, indent=2, ensure_ascii=False) + '\n')
print(json.dumps(out, indent=2, ensure_ascii=False))
