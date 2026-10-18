#!/usr/bin/env python3
"""In-guest watcher for the Agent artifact cache `.tmp` lifecycle (native-3).

Runs detached inside native-3 and records, with 2 ms granularity:
  - first appearance of <sha>.tmp (create), size samples while it grows
  - when <sha>.tmp disappears (cleanup) or is renamed to <sha> (install)
  - when the final <sha> cache file appears/disappears

Usage (on the node): python3 watch_tmp.py <data_dir> <sha> <out.jsonl> <seconds>
Never reads agent config; only lists the cache directory.
"""
import json
import os
import sys
import time

data_dir, sha, out, seconds = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
cache = os.path.join(data_dir, 'artifacts')
tmp = os.path.join(cache, sha + '.tmp')
final = os.path.join(cache, sha)
start = time.time()
samples = []
state = {'tmp_created_at': None, 'tmp_first_size': None, 'tmp_max_size': None,
         'tmp_gone_at': None, 'final_appeared_at': None, 'errors': []}
with open(out, 'a') as fh:
    fh.write(json.dumps({'event': 'watch_start', 'at': time.time(), 'sha': sha,
                         'existing_cache': sorted(os.listdir(cache)) if os.path.isdir(cache) else None}) + '\n')
    fh.flush()
    while time.time() - start < seconds:
        try:
            st = os.stat(tmp)
            if state['tmp_created_at'] is None:
                state['tmp_created_at'] = time.time()
                state['tmp_first_size'] = st.st_size
                fh.write(json.dumps({'event': 'tmp_created', 'at': time.time(), 'size': st.st_size}) + '\n')
                fh.flush()
            state['tmp_max_size'] = st.st_size if state['tmp_max_size'] is None else max(state['tmp_max_size'], st.st_size)
            samples.append(st.st_size)
        except FileNotFoundError:
            if state['tmp_created_at'] is not None and state['tmp_gone_at'] is None:
                state['tmp_gone_at'] = time.time()
                fh.write(json.dumps({'event': 'tmp_gone', 'at': time.time(),
                                     'lifetime_ms': round((state['tmp_gone_at'] - state['tmp_created_at']) * 1000, 1),
                                     'tmp_max_size': state['tmp_max_size'],
                                     'final_exists': os.path.exists(final)}) + '\n')
                fh.flush()
        if state['final_appeared_at'] is None and os.path.exists(final):
            state['final_appeared_at'] = time.time()
            fh.write(json.dumps({'event': 'final_appeared', 'at': time.time(),
                                 'size': os.path.getsize(final),
                                 'samples_observed': len(samples),
                                 'sample_min': min(samples) if samples else None,
                                 'sample_max': max(samples) if samples else None}) + '\n')
            fh.flush()
        time.sleep(0.002)
    fh.write(json.dumps({'event': 'watch_end', 'at': time.time(), 'state': state,
                         'samples': len(samples),
                         'cache_listing': sorted(os.listdir(cache)) if os.path.isdir(cache) else None}) + '\n')
