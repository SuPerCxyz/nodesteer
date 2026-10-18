#!/usr/bin/env python3
"""Owned child Agent factory for sys-b14 and batch10-hub-restart.

Creates the harness-owned `OwnedChild` objects used as FileTransfer source and
targets.  They run the EXACT repair Agent binary (d55dc5cd...) as plain
background processes under owned 0700 roots with owned 0600 configs; no
systemd unit is created and no protected Agent is touched.
"""
import sys
from pathlib import Path

import xferlib as X


def make_env(batch, here):
    """Return {'src': OwnedChild, 'tgt': OwnedChild, ...} for the given batch."""
    here = Path(here)
    nonce = (here / '.nonce').read_text().strip()
    specs = {
        'sysb14': {
            'src': ('native-1', 'src'),
            'tgt': ('native-2', 'tgt'),
        },
        'b10': {
            'src': ('native-1', 'src'),
            'tgt_a': ('native-2', 'tgt-a'),
            'tgt_b': ('native-3', 'tgt-b'),
        },
    }
    plan = specs[batch]
    env = {}
    for name, (label, tag) in plan.items():
        env[name] = X.OwnedChild(batch, X.NODE_IP[label], tag, nonce)
    return env


def get(batch, here, name):
    return make_env(batch, here)[name]
