#!/usr/bin/env python3
"""P16-09 / P16-10 stages: Agent artifact cache `.tmp`, verification and recovery."""
import hashlib
import json
import os
import sys
import time

import hub
import nodes
import p16
from run_b07 import (N3DIR, NATIVE3, NONCE, PREFIX, rec, save_state, load_state, now,
                     BASELINE_CACHE_SHA, sha_of)

ART_SIZE = 268435456  # 256 MiB


def stage_p16_setup(h, st):
    ev = {'at': now(), 'artifact_size': ART_SIZE}
    ev['baseline_cache'] = nodes.agent_cache_state()
    ev['baseline_db'] = nodes.agent_db_artifacts()
    content = os.urandom(ART_SIZE)
    sha = hashlib.sha256(content).hexdigest()
    ev['local_sha256'] = sha
    code, body = h.upload_artifact('%s-art-A' % PREFIX, '1.0.0', 'b07-payload.bin', content)
    ev['upload'] = {'http': code, 'body': body}
    if code != 200:
        return rec(st, 'P16-setup.json', ev, 'P16 setup (upload failed)')
    art = body
    ev['artifact'] = {'id': art['id'], 'name': art.get('name'), 'size': art.get('size'),
                      'sha256': art.get('sha256'), 'storage_path': art.get('storage_path')}
    ev['sha_matches_local'] = (art.get('sha256') == sha and art.get('size') == ART_SIZE)
    art_id = art['id']
    code2, app = h.post('/api/applications', {'name': '%s-app-A' % PREFIX, 'version': '1.0.0',
                                              'artifact_id': art_id, 'binary_path': '/usr/local/bin/b07',
                                              'unit_name': 'b07-app.service'})
    ev['application_create'] = {'http': code2, 'body': app if code2 != 200 else
                                {'id': app['id'], 'name': app.get('name'), 'artifact_id': app.get('artifact_id')}}
    if code2 != 200:
        return rec(st, 'P16-setup.json', ev, 'P16 setup (app failed)')
    st['created']['artifacts'].append({'id': art_id, 'name': art['name'], 'sha256': sha, 'size': ART_SIZE,
                                       'purpose': 'P16-09/P16-10 agent artifact cache'})
    st['created']['applications'].append({'id': app['id'], 'name': app['name'], 'artifact_id': art_id,
                                          'purpose': 'drive agent artifact prefetch (assign only, no deploy)'})
    st['budget']['artifacts'] += 1
    st['budget']['applications'] += 1
    save_state(st)
    ev['hub_blob'] = p16.hub_blob_sha(sha)
    ev['assigned_nodes_before'] = h.get('/api/applications/%s/nodes' % app['id'])[1]
    return rec(st, 'P16-setup.json', ev, 'P16 setup: owned artifact + application')


def _fault_state(sha):
    return {'cache_listing': p16.cache_listing(),
            'cache_sha_of_entry': p16.sh('sha256sum %s/%s 2>/dev/null | cut -d" " -f1 || echo MISSING' % (p16.CACHE, sha)),
            'tmp_present': p16.sh('ls -la %s/%s.tmp 2>/dev/null || echo none' % (p16.CACHE, sha)),
            'tmp_glob': p16.sh('ls -la %s/*.tmp 2>/dev/null || echo none' % p16.CACHE),
            'db': nodes.agent_db_artifacts(),
            'log': p16.agent_log('artifact|prefetch').get('stdout', '')}


def stage_p16_mismatch(h, st):
    """SHA256 verification failure: download completes, content must not be installed."""
    arts = st['created']['artifacts']
    apps = st['created']['applications']
    sha = arts[0]['sha256']
    app_id = apps[0]['id']
    ev = {'at': now(), 'sha256': sha, 'blob': p16.hub_blob(sha)}
    ev['before'] = _fault_state(sha)
    # ensure no valid cached copy so the prefetch really downloads again
    ev['cache_cleanup_for_test'] = p16.sh('rm -f %s/%s; ls -la %s/ | tail -3' % (p16.CACHE, sha, p16.CACHE))
    ev['blob_before_fault'] = p16.hub_blob_sha(sha)
    p16.stop_watcher()
    ev['watcher'] = p16.start_watcher(sha, seconds=120)
    ev['fault'] = nodes.hubsh_out('printf "b07-corruption" >> %s && stat -c "%%s" %s' % (p16.hub_blob(sha), p16.hub_blob(sha)))
    ev['blob_sha_after_fault'] = p16.hub_blob_sha(sha)
    ev['trigger'] = p16.trigger_prefetch(h, app_id, NATIVE3)
    time.sleep(12)
    ev['watch'] = p16.read_watch()
    ev['after'] = _fault_state(sha)
    # restore the blob exactly (append-only corruption, so truncate back)
    ev['blob_restore'] = nodes.hubsh_out('truncate -s %d %s; sha256sum %s | cut -d" " -f1' % (arts[0]['size'], p16.hub_blob(sha), p16.hub_blob(sha)))
    ev['blob_restored_matches'] = (ev['blob_restore']['stdout'].strip().splitlines()[-1].strip() == sha) if ev['blob_restore']['stdout'] else False
    return rec(st, 'P16-09-sha-mismatch.json', ev, 'P16-09 download-time SHA mismatch')


def stage_p16_notfound(h, st):
    """Download failure (artifact removed while assigned) -> .tmp cleanup -> retry succeeds."""
    arts = st['created']['artifacts']
    apps = st['created']['applications']
    sha = arts[0]['sha256']
    app_id = apps[0]['id']
    ev = {'at': now(), 'sha256': sha, 'blob': p16.hub_blob(sha)}
    ev['before'] = _fault_state(sha)
    ev['cache_cleanup_for_test'] = p16.sh('rm -f %s/%s; echo cleaned' % (p16.CACHE, sha))
    p16.stop_watcher()
    ev['watcher'] = p16.start_watcher(sha, seconds=120)
    ev['fault'] = nodes.hubsh_out('mv -f %s %s.b07aside && ls -la %s.b07aside' % (p16.hub_blob(sha), p16.hub_blob(sha), p16.hub_blob(sha)))
    ev['trigger'] = p16.trigger_prefetch(h, app_id, NATIVE3)
    time.sleep(10)
    ev['watch'] = p16.read_watch()
    ev['after'] = _fault_state(sha)
    ev['hub_aside_state'] = nodes.hubsh_out('ls -la %s.b07aside 2>/dev/null || echo MISSING' % p16.hub_blob(sha))
    # restore and retry: same desired state change
    ev['blob_restore'] = nodes.hubsh_out('mv -f %s.b07aside %s && sha256sum %s | cut -d" " -f1' % (p16.hub_blob(sha), p16.hub_blob(sha), p16.hub_blob(sha)))
    ev['blob_restored_matches'] = (ev['blob_restore']['stdout'].strip().splitlines()[-1].strip() == sha) if ev['blob_restore']['stdout'] else False
    p16.stop_watcher()
    ev['retry_watcher'] = p16.start_watcher(sha, seconds=180)
    ev['retry_trigger'] = p16.trigger_prefetch(h, app_id, NATIVE3, mode='reassign')
    ev['retry_wait'] = p16.wait_cache_state(sha, 'file', timeout=90)
    time.sleep(4)
    ev['retry_watch'] = p16.read_watch()
    ev['after_retry'] = _fault_state(sha)
    ev['cache_file_sha'] = p16.sh('sha256sum %s/%s | cut -d" " -f1' % (p16.CACHE, sha))
    ev['cache_file_size'] = p16.sh('stat -c "%%s %%a" %s/%s' % (p16.CACHE, sha))
    return rec(st, 'P16-10-notfound-cleanup-retry.json', ev, 'P16-10 download failure + retry')


def stage_p16_interrupt(h, st):
    """Interrupt the artifact download mid-stream, then recover."""
    arts = st['created']['artifacts']
    apps = st['created']['applications']
    sha = arts[0]['sha256']
    app_id = apps[0]['id']
    size = arts[0]['size']
    ev = {'at': now(), 'sha256': sha, 'artifact_size': size}
    ev['before'] = _fault_state(sha)
    ev['cache_cleanup_for_test'] = p16.sh('rm -f %s/%s; echo cleaned' % (p16.CACHE, sha))
    p16.stop_watcher()
    ev['watcher'] = p16.start_watcher(sha, seconds=180)
    ev['trigger'] = p16.trigger_prefetch(h, app_id, NATIVE3)
    # host-side loop: watch the in-guest .tmp grow, then cut the Hub-side stream
    samples = []
    cut = None
    deadline = time.time() + 120
    while time.time() < deadline:
        r = p16.sh('stat -c %%s %s/%s.tmp 2>/dev/null || echo none' % (p16.CACHE, sha))
        v = r['stdout'].strip().splitlines()[-1] if r['stdout'] else 'none'
        samples.append({'at_s': round(time.time(), 2), 'tmp_bytes': v})
        try:
            grew = int(v)
        except Exception:
            grew = None
        if grew is not None and grew > int(size * 0.25):
            cut = nodes.hubsh_out('cp -a %s %s.b07full && truncate -s 4096 %s && stat -c "%%s" %s'
                                  % (p16.hub_blob(sha), p16.hub_blob(sha), p16.hub_blob(sha), p16.hub_blob(sha)))
            samples.append({'at_s': round(time.time(), 2), 'action': 'hub_blob_truncated_to_4096', 'tmp_at_cut': v})
            break
        time.sleep(0.05)
    ev['interrupt_loop'] = {'samples': samples[-12:], 'cut': cut}
    time.sleep(10)
    ev['watch'] = p16.read_watch()
    ev['after'] = _fault_state(sha)
    # restore the full blob from the backup copy
    ev['blob_restore'] = nodes.hubsh_out('mv -f %s.b07full %s && sha256sum %s | cut -d" " -f1 && stat -c "%%s" %s'
                                         % (p16.hub_blob(sha), p16.hub_blob(sha), p16.hub_blob(sha), p16.hub_blob(sha)))
    ev['blob_restored_matches'] = (ev['blob_restore']['stdout'].strip().splitlines()[0].strip() == sha) if ev['blob_restore']['stdout'] else False
    p16.stop_watcher()
    ev['retry_watcher'] = p16.start_watcher(sha, seconds=180)
    ev['retry_trigger'] = p16.trigger_prefetch(h, app_id, NATIVE3, mode='reassign')
    ev['retry_wait'] = p16.wait_cache_state(sha, 'file', timeout=120)
    time.sleep(4)
    ev['retry_watch'] = p16.read_watch()
    ev['after_retry'] = _fault_state(sha)
    ev['cache_file_sha'] = p16.sh('sha256sum %s/%s | cut -d" " -f1' % (p16.CACHE, sha))
    ev['cache_file_size'] = p16.sh('stat -c "%%s %%a" %s/%s' % (p16.CACHE, sha))
    ev['baseline_entry_intact'] = p16.sh('ls -la %s/%s' % (p16.CACHE, BASELINE_CACHE_SHA))
    return rec(st, 'P16-10-interrupt-retry.json', ev, 'P16-10 mid-download interruption + retry')


def stage_p16_cache_cleanup(h, st):
    """Remove the owned cache entry (file + SQLite row) and verify the baseline is untouched."""
    arts = st['created']['artifacts']
    sha = arts[0]['sha256']
    ev = {'at': now(), 'sha256': sha}
    ev['before'] = _fault_state(sha)
    p16.stop_watcher()
    ev['remove_file'] = p16.sh('rm -f %s/%s %s/%s.tmp; ls -la %s/' % (p16.CACHE, sha, p16.CACHE, sha, p16.CACHE))
    ev['db_before'] = nodes.agent_db_artifacts()
    ev['remove_row'] = p16.sh("""
python3 - <<'PY'
import sqlite3, os, shutil, tempfile, json
src='%s/state.db'
c=sqlite3.connect(src)
c.execute("delete from artifacts where sha256=?", ('%s',))
c.commit()
rows=list(c.execute("select sha256,path from artifacts"))
print(json.dumps({'deleted_sha':'%s','remaining':rows}))
c.close()
PY
""" % (p16.AGENT_DATA, sha, sha))
    ev['db_after'] = nodes.agent_db_artifacts()
    ev['cache_after'] = p16.cache_listing()
    ev['baseline_entry_intact'] = p16.sh('ls -la %s/%s; sha256sum %s/%s | cut -d" " -f1'
                                         % (p16.CACHE, BASELINE_CACHE_SHA, p16.CACHE, BASELINE_CACHE_SHA))
    return rec(st, 'P16-cache-cleanup.json', ev, 'P16 owned cache entry cleanup')




def stage_p16_happy(h, st):
    """P16-09 happy path: assign -> real prefetch -> <.tmp> observed -> atomic install."""
    arts = st['created']['artifacts']
    apps = st['created']['applications']
    sha = arts[0]['sha256']
    app_id = apps[0]['id']
    ev = {'at': now(), 'sha256': sha, 'artifact_size': arts[0]['size'],
          'trigger': 'POST /api/applications/{id}/assign (same endpoint the UI editor calls)'}
    ev['before'] = _fault_state(sha)
    ev['cache_cleanup_for_test'] = p16.sh('rm -f %s/%s %s/%s.tmp; echo cleaned' % (p16.CACHE, sha, p16.CACHE, sha))
    p16.stop_watcher()
    ev['watcher'] = p16.start_watcher(sha, seconds=240)
    ev['assign'] = p16.trigger_prefetch(h, app_id, NATIVE3)
    ev['assign_state'] = h.get('/api/applications/%s/nodes' % app_id)[1]
    ev['cache_wait'] = p16.wait_cache_state(sha, 'file', timeout=180)
    time.sleep(5)
    ev['watch'] = p16.read_watch()
    ev['cache_file_sha'] = p16.sh('sha256sum %s/%s | cut -d" " -f1' % (p16.CACHE, sha))
    ev['cache_file_stat'] = p16.sh('stat -c "%%s %%a %%Y" %s/%s' % (p16.CACHE, sha))
    ev['tmp_glob_after'] = p16.sh('ls -la %s/*.tmp 2>/dev/null || echo none' % p16.CACHE)
    ev['db'] = nodes.agent_db_artifacts()
    ev['agent_log'] = p16.agent_log('artifact').get('stdout', '')
    ev['baseline_entry_intact'] = p16.sh('ls -la %s/%s; sha256sum %s/%s | cut -d" " -f1'
                                        % (p16.CACHE, BASELINE_CACHE_SHA, p16.CACHE, BASELINE_CACHE_SHA))
    ev['local_expected'] = {'sha': sha, 'size': arts[0]['size']}
    p16.stop_watcher()
    return rec(st, 'P16-09-happy-install.json', ev, 'P16-09 happy install with .tmp observation')




def stage_p16_interrupt2(h, st):
    """Second mid-download interruption attempt: single-round-trip truncate on the Hub blob.

    Attempt 1 (P16-10-interrupt-retry.json) missed the window because the interrupt call
    itself copied the 256 MiB blob first; the download had already finished.  Here the
    backup is taken before the trigger and the interrupt is one `truncate` round trip.
    """
    arts = st['created']['artifacts']
    apps = st['created']['applications']
    sha = arts[0]['sha256']
    app_id = apps[0]['id']
    size = arts[0]['size']
    blob = p16.hub_blob(sha)
    ev = {'at': now(), 'sha256': sha, 'artifact_size': size, 'blob': blob}
    ev['blob_backup'] = nodes.hubsh_out('rm -f %s.b07keep; cp -a %s %s.b07keep && sha256sum %s.b07keep | cut -d" " -f1'
                                       % (blob, blob, blob, blob))
    ev['cache_cleanup_for_test'] = p16.sh('rm -f %s/%s %s/%s.tmp; echo cleaned' % (p16.CACHE, sha, p16.CACHE, sha))
    p16.stop_watcher()
    ev['watcher'] = p16.start_watcher(sha, seconds=180)
    ev['trigger'] = p16.trigger_prefetch(h, app_id, NATIVE3, mode='reassign')
    samples = []
    cut = None
    deadline = time.time() + 120
    while time.time() < deadline:
        r = p16.sh('stat -c %%s %s/%s.tmp 2>/dev/null || echo none' % (p16.CACHE, sha))
        v = (r['stdout'] or '').strip().splitlines()[-1] if r['stdout'] else 'none'
        try:
            n = int(v)
        except Exception:
            n = None
        samples.append({'tmp_bytes': v, 'elapsed_s': round(time.time() - deadline + 120, 2)})
        if n is not None and int(size * 0.25) < n < int(size * 0.85):
            cut = nodes.hubsh_out('truncate -s 4096 %s; stat -c "%%s" %s' % (blob, blob))
            samples.append({'action': 'truncate_blob_4096', 'tmp_at_cut': v})
            break
        time.sleep(0.02)
    ev['interrupt_loop'] = {'samples': samples[-14:], 'cut': cut}
    time.sleep(10)
    ev['watch'] = p16.read_watch()
    ev['after'] = _fault_state(sha)
    ev['log_after'] = p16.agent_log('artifact').get('stdout', '')
    ev['blob_restore'] = nodes.hubsh_out('mv -f %s.b07keep %s && sha256sum %s | cut -d" " -f1 && stat -c "%%s" %s'
                                         % (blob, blob, blob, blob))
    ev['blob_restored_matches'] = (ev['blob_restore']['stdout'].strip().splitlines()[0].strip() == sha) if ev['blob_restore']['stdout'] else False
    p16.stop_watcher()
    ev['retry_watcher'] = p16.start_watcher(sha, seconds=180)
    ev['retry_trigger'] = p16.trigger_prefetch(h, app_id, NATIVE3, mode='reassign')
    ev['retry_wait'] = p16.wait_cache_state(sha, 'file', timeout=180)
    time.sleep(5)
    ev['retry_watch'] = p16.read_watch()
    ev['after_retry'] = _fault_state(sha)
    ev['cache_file_sha'] = p16.sh('sha256sum %s/%s | cut -d" " -f1' % (p16.CACHE, sha))
    ev['cache_file_size'] = p16.sh('stat -c "%%s %%a" %s/%s' % (p16.CACHE, sha))
    ev['log_after_retry'] = p16.agent_log('artifact').get('stdout', '')
    ev['baseline_entry_intact'] = p16.sh('sha256sum %s/%s | cut -d" " -f1' % (p16.CACHE, BASELINE_CACHE_SHA))
    p16.stop_watcher()
    return rec(st, 'P16-10-interrupt-retry2.json', ev, 'P16-10 mid-download interruption attempt 2')


STAGES = {'p16-interrupt2': stage_p16_interrupt2, 'p16-happy': stage_p16_happy, 'p16-setup': stage_p16_setup, 'p16-mismatch': stage_p16_mismatch,
          'p16-notfound': stage_p16_notfound, 'p16-interrupt': stage_p16_interrupt,
          'p16-cache-cleanup': stage_p16_cache_cleanup}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in STAGES:
        print('usage: run_p16.py <%s>' % '|'.join(STAGES))
        return 2
    st = load_state()
    h = hub.Hub()
    h.login()
    STAGES[sys.argv[1]](h, st)
    save_state(st)
    print(json.dumps({'stage': sys.argv[1], 'ok': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
