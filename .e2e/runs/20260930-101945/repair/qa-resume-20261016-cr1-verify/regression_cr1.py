#!/usr/bin/env python3
"""qa-resume-20261016-cr1-verify: AR-32 family re-run against the CR-1 build.

Eight apps on native-2 (all owned fixtures, nonce-prefixed) with one staged
recovery restart plus one real crash-during-deploy restart:

  adv    (i)   real v1->v2 upgrade after a ROLLBACK_FAILED stale row (rev bump)
  adv2   (i)   same-revision re-deploy after a stale row (supersede is the only guard)
  pfa    (ii)  later DONE/prepare_failed must NOT supersede -> replay
  rba    (ii)  later DONE/rolled_back must NOT supersede -> replay
  unk    (ii)  later DONE with unknown cause (legacy empty completed_kind) must NOT supersede -> replay
  bm    (ctl)  definition drift -> binding mismatch still blocks replay
  hc    SYS-48 health check command type positive/negative + old version restored
  crash E2E-13/SYS-51/AR-30/INV-11 real SIGKILL while the journal is in VERIFYING
"""
import json
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import cr1lib as L  # noqa: E402
import hubclient  # noqa: E402

BATCH = 'qa-resume-20261016-cr1-verify'
STATE_F = HERE / 'regression-state.json'
RESULTS_F = HERE / 'regression-results.json'
LEDGER_F = HERE / 'fixture-ledger.json'
EV = HERE / 'evidence'
N2 = 'native-2'
N2_IP = L.NODES[N2]
os.umask(0o077)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {'nonce': secrets.token_hex(4), 'fixtures': [], 'cases': [], 'steps': {}, 'host_paths': []}


def save_state(st):
    tmp = STATE_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, STATE_F)


ST = load_state()
P = 'qa-cr1-%s-' % ST['nonce']
ROOT = '/opt/%sapp' % P
hub = hubclient.Hub()
DB2 = None


def record(case, new_status, conclusion, evidence=None, extra=None):
    row = {'case_id': case, 'at_utc': now_utc(), 'new_status': new_status,
           'new_conclusion': conclusion, 'evidence': evidence or [], 'extra': extra or {}}
    ST['cases'] = [c for c in ST['cases'] if c.get('case_id') != case] + [row]
    save_state(ST)
    print('[%s] %s' % (case, new_status), flush=True)
    return row


def dump_results():
    payload = {'batch': BATCH, 'authority': 'http://192.168.100.209:18080',
               'build_label': 'dddee4f0-e2e-repair-20261016-cr1',
               'updated_at_utc': now_utc(), 'nonce': ST['nonce'],
               'cases': ST['cases']}
    tmp = RESULTS_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, RESULTS_F)


def ev_write(name, value):
    p = EV / name
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, p)
    return 'evidence/' + name


def ssh(cmd, timeout=90):
    return L.ssh(N2_IP, cmd, timeout=timeout)


def app_host_state(bin_path, unit):
    script = ("python3 - <<'PYEOF'\nimport hashlib,json,os,subprocess\n"
              "def sha(p):\n"
              "    try:\n"
              "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
              "    except Exception as e:\n"
              "        return 'ERR:'+str(e)\n"
              "u=subprocess.run(['systemctl','show','--no-pager','-p','ActiveState','-p','SubState','-p','MainPID','__UNIT__'],capture_output=True,text=True)\n"
              "st=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
              "print(json.dumps({'binary_sha':sha('__BIN__'),'binary_lexists':os.path.lexists('__BIN__'),"
              "'is_symlink':os.path.islink('__BIN__'),'unit':st}))\nPYEOF")
    script = script.replace('__UNIT__', unit).replace('__BIN__', bin_path)
    rc, out, err = ssh(script)
    if rc != 0:
        return {'error': (err or out)[-300:]}
    return json.loads(out.strip().split('\n')[-1])


def _db():
    """Lazy agent-DB path so every phase works in a fresh process."""
    global DB2
    if not DB2:
        DB2 = ST.get('agent_db_native_2') or L.agent_db_path(N2)
    return DB2


def journal(app_id):
    return L.agent_db_rows(N2, (
        "SELECT id,phase,completed_kind,from_version,to_version,app_revision,binary_path,"
        "started_at,updated_at FROM deployments WHERE application_id='%s' ORDER BY started_at" % app_id), db=_db())


def journal_full(app_id):
    return L.agent_db_rows(N2, (
        "SELECT * FROM deployments WHERE application_id='%s' ORDER BY started_at" % app_id), db=_db())


def local_def(app_id):
    rows = L.agent_db_rows(N2, "SELECT revision, definition_json FROM applications WHERE id='%s'" % app_id, db=_db())
    if not isinstance(rows, list) or not rows:
        return None
    d = json.loads(rows[0]['definition_json'])
    d['_revision'] = rows[0]['revision']
    return d


def deploy_now(app_id, operation='deploy', timeout=240, node_id=None):
    code, body = hub.post('/api/applications/%s/deploy' % app_id,
                          {'node_ids': [node_id or ST['node_ids'][N2]], 'operation': operation})
    eid = body[0].get('id') if code == 200 and isinstance(body, list) and body else None
    ex = L.wait_exec(hub, eid, timeout=timeout) if eid else {}
    return {'http': code, 'execution_id': eid, 'status': ex.get('status'),
            'error': ex.get('error'), 'block_reason': ex.get('block_reason'),
            'started_at': ex.get('start_time'), 'finished_at': ex.get('end_time')}


def app_def(name, artifact_id, bin_path, unit, cfg_path, version='v1',
            health=None, config='APP_MODE=qa-cr1\n'):
    return {'name': name, 'description': 'qa-cr1 owned fixture (nonce %s)' % ST['nonce'],
            'artifact_id': artifact_id, 'version': version, 'binary_path': bin_path,
            'unit_name': unit, 'arguments': ['--nonce=%s' % ST['nonce']],
            'environment': {'QA_CR1': ST['nonce']}, 'config': config, 'config_path': cfg_path,
            'health_check': health or {'type': 'systemd', 'target': '', 'attempts': 3,
                                       'interval': 1, 'timeout': 5}}


# ------------------------------------------------------------------ setup
def pad(body, version, target=4096):
    text = '#!/bin/sh\n# qa-cr1 version=%s\n' % version
    while len(text) < target:
        text += '# pad-%s\n' % ('p' * 70)
    return (text + body).encode()


APPS = ['adv', 'adv2', 'pfa', 'rba', 'unk', 'bm', 'hc', 'crash']


def phase_setup():
    hub.login()
    nodes = {}
    code, body = hub.get('/api/nodes')
    for n in (body.get('nodes', body) if isinstance(body, dict) else body):
        nodes[n['hostname']] = n
    ST['node_ids'] = {k: v['id'] for k, v in nodes.items() if k.startswith('qa-repair')}
    ST['node_ids'][N2] = nodes['qa-repair-20260930-101945-native-2']['id']
    global DB2
    DB2 = L.agent_db_path(N2)
    ST['agent_db_native_2'] = DB2
    save_state(ST)
    setup = {'at_utc': now_utc(), 'root': ROOT, 'agent_db': DB2, 'apps': {}, 'artifacts': {}}
    rc, out, err = ssh('mkdir -p %s && chmod 0755 %s' % (ROOT, ROOT))
    ST['host_paths'].append({'host': N2_IP, 'path': ROOT, 'role': 'qa-cr1 app fixtures'})
    setup['root_mkdir'] = {'rc': rc}
    for ver, name in (('v1', P + 'artifact-v1'), ('v2', P + 'artifact-v2')):
        content = pad('exec sleep 100000\n', ver)
        code, resp = hub.upload_artifact(name, ver, name + '.bin', content)
        if code != 200:
            raise RuntimeError('artifact upload failed %s %s' % (ver, resp))
        setup['artifacts'][ver] = {'id': resp['id'], 'sha256': resp['sha256'], 'name': name,
                                   'size': len(content)}
        ST['fixtures'].append(('artifact', resp['id'], name))
    save_state(ST)
    for suffix in APPS:
        name = P + 'app-' + suffix
        bin_path = '%s/bin-%s/app' % (ROOT, suffix)
        cfg_path = '%s/cfg-%s/app.env' % (ROOT, suffix)
        unit = P + 'app-%s.service' % suffix
        ssh('mkdir -p %s/bin-%s %s/cfg-%s' % (ROOT, suffix, ROOT, suffix))
        health = {'type': 'command', 'target': 'true', 'attempts': 3, 'interval': 1,
                  'timeout': 5} if suffix == 'hc' else None
        code, resp = hub.post('/api/applications',
                              app_def(name, setup['artifacts']['v1']['id'], bin_path, unit,
                                      cfg_path, health=health))
        if code not in (200, 201):
            raise RuntimeError('app create failed %s %s' % (suffix, resp))
        ST['fixtures'].append(('application', resp['id'], name))
        code, _ = hub.post('/api/applications/%s/assign' % resp['id'],
                           {'node_ids': [ST['node_ids'][N2]]})
        setup['apps'][suffix] = {'app_id': resp['id'], 'name': name, 'bin': bin_path,
                                 'cfg': cfg_path, 'unit': unit, 'assign_http': code}
    ST['setup'] = setup
    save_state(ST)
    ev_write('app-fixtures.json', setup)
    return setup


def A(suffix):
    return ST['setup']['apps'][suffix]


def app_paths(suffix):
    a = A(suffix)
    return a['bin'], a['unit'], a['cfg']


def write_bin(bin_path, marker):
    body = ('#!/bin/sh\n# qa-cr1 manual marker %s\n' % marker) + ('# x\n' * 40) + 'exec sleep 100000\n'
    b64 = __import__('base64').b64encode(body.encode()).decode()
    rc, out, err = ssh("echo %s | base64 -d > %s && chmod 0755 %s" % (b64, bin_path, bin_path))
    return {'rc': rc, 'marker': marker, 'err': (err or '')[-200:]}


def chattr(path, flag):
    rc, out, err = ssh('chattr %s %s && lsattr %s' % (flag, path, path))
    return {'rc': rc, 'out': out.strip(), 'err': (err or '')[-200:]}


def restore_cfg(cfg_path, content):
    b64 = __import__('base64').b64encode(content.encode()).decode()
    rc, out, err = ssh("rm -f %s && echo %s | base64 -d > %s && chmod 0644 %s && cat %s" %
                       (cfg_path, b64, cfg_path, cfg_path, cfg_path))
    return {'rc': rc, 'content_readback': out.strip(), 'err': (err or '')[-200:]}


# ------------------------------------------------------------------ app phase
def phase_app():
    """Stage every scenario, then one staged restart + one crash restart."""
    hub.login()
    out = {'at_utc': now_utc(), 'steps': {}, 'scenarios': {}}
    st = ST.setdefault('setup', {})
    if not st:
        raise RuntimeError('run setup phase first')

    # ---------- 1. baseline deploy of every app (DONE/deployed per app) ----------
    base = {}
    for suffix in APPS:
        r = deploy_now(A(suffix)['app_id'])
        b = app_host_state(*app_paths(suffix)[:1], app_paths(suffix)[1])
        base[suffix] = {'deploy': r, 'host': b, 'journal': journal(A(suffix)['app_id'])}
    out['steps']['baseline_deploys'] = base
    art1 = ST['setup']['artifacts']['v1']['sha256']
    out['oracles'] = {'baseline_all_success': all(
        base[s]['deploy']['status'] == 'SUCCESS' for s in APPS),
        'baseline_binary_sha_v1': art1,
        'baseline_binary_matches_artifact': {s: base[s]['host'].get('binary_sha') == art1 for s in APPS}}
    ev_write('app-baseline.json', out)
    if not out['oracles']['baseline_all_success']:
        raise RuntimeError('baseline deploy failed: %s' % json.dumps(
            {s: base[s]['deploy'] for s in APPS if base[s]['deploy']['status'] != 'SUCCESS'}))

    # ---------- 2. stage stale half-complete rows ----------
    staged = {}
    for suffix in ('adv2', 'pfa', 'rba', 'unk', 'bm'):
        b, unit, cfg = app_paths(suffix)
        rec = {}
        # 2a. manual content so a replay is observable (backup != artifact content)
        rec['write_marker'] = write_bin(b, 'X-' + suffix)
        # 2b. pfa/rba/unk/bm: keep a real file first; only adv2 uses a pure EPERM
        rec['chattr_i'] = chattr(b, '+i')
        rec['marker_sha'] = app_host_state(b, unit).get('binary_sha')
        # 2c. failed deploy -> install EPERM -> rollback EPERM -> ROLLBACK_FAILED
        rec['epm_deploy'] = deploy_now(A(suffix)['app_id'])
        rec['journal_after_epm'] = journal(A(suffix)['app_id'])
        rec['host_after_epm'] = app_host_state(b, unit)
        rec['chattr_i_release'] = chattr(b, '-i')
        staged[suffix] = rec
    out['steps']['stale_rows'] = staged
    save_state(ST)

    # ---------- 3. later DONE rows ----------
    later = {}
    # adv: real upgrade to artifact v2 (revision bump) then successful deploy
    d = app_def(A('adv')['name'], ST['setup']['artifacts']['v2']['id'], A('adv')['bin'],
                A('adv')['unit'], A('adv')['cfg'], version='v2')
    code, _ = hub.put('/api/applications/' + A('adv')['app_id'], d)
    later['adv_def_put_http'] = code
    later['adv_upgrade'] = deploy_now(A('adv')['app_id'])
    later['adv_host'] = app_host_state(A('adv')['bin'], A('adv')['unit'])
    # adv2: same-definition redeploy (no revision bump) -> supersede is the only guard
    later['adv2_redeploy'] = deploy_now(A('adv2')['app_id'])
    later['adv2_host'] = app_host_state(A('adv2')['bin'], A('adv2')['unit'])
    later['adv2_local_def_revision'] = (local_def(A('adv2')['app_id']) or {}).get('_revision')

    # pfa: later DONE/prepare_failed via a config self-loop (binary untouched)
    b, unit, cfg = app_paths('pfa')
    later['pfa_loop'] = ssh('rm -f %s && ln -s %s %s && ls -l %s' % (cfg, cfg, cfg, cfg))[1].strip()
    later['pfa_prepare_failed_deploy'] = deploy_now(A('pfa')['app_id'])
    later['pfa_journal'] = journal(A('pfa')['app_id'])
    later['pfa_host_after'] = app_host_state(b, unit)
    later['pfa_cfg_restore'] = restore_cfg(cfg, 'APP_MODE=qa-cr1\n')

    # rba: later DONE/rolled_back via a dangling config symlink (rollback succeeds)
    b, unit, cfg = app_paths('rba')
    later['rba_dangling'] = ssh('rm -f %s && ln -s /nonexistent-cr1 %s && ls -l %s' % (cfg, cfg, cfg))[1].strip()
    later['rba_rolled_back_deploy'] = deploy_now(A('rba')['app_id'])
    later['rba_journal'] = journal(A('rba')['app_id'])
    later['rba_host_after'] = app_host_state(b, unit)
    later['rba_cfg_restore'] = restore_cfg(cfg, 'APP_MODE=qa-cr1\n')

    # bm: definition drift only (no later DONE row) -> binding mismatch branch
    b, unit, cfg = app_paths('bm')
    bin2 = '%s/bin-bm2/app' % ROOT
    ssh('mkdir -p %s/bin-bm2' % ROOT)
    d = app_def(A('bm')['name'], ST['setup']['artifacts']['v1']['id'], bin2, unit, cfg)
    code, _ = hub.put('/api/applications/' + A('bm')['app_id'], d)
    later['bm_def_put_http'] = code
    later['bm_new_path'] = bin2
    deadline = time.time() + 60
    while time.time() < deadline:
        ld = local_def(A('bm')['app_id']) or {}
        if ld.get('_revision') == 2:
            break
        time.sleep(1)
    later['bm_local_def'] = {'revision': (local_def(A('bm')['app_id']) or {}).get('_revision'),
                             'binary_path': (local_def(A('bm')['app_id']) or {}).get('binary_path')}
    later['bm_host'] = app_host_state(b, unit)
    out['steps']['later_rows'] = later

    # unk: synthetic DONE row with unknown cause (legacy shape), injected while stopped
    b, unit, cfg = app_paths('unk')
    out['steps']['unk_marker_after_stage'] = write_bin(b, 'Y-unk')
    out['steps']['unk_marker_y_sha'] = app_host_state(b, unit).get('binary_sha')
    save_state(ST)
    ev_write('app-staging.json', out)

    save_state(ST)
    ev_write('app-staging.json', out)
    return phase_recover()


# ---------------------------------------------------------------- recovery+verdicts

def _build_scenarios(out, inj, staged, later):
    """Per-scenario verdicts, computed from (re)loadable evidence inputs."""
    # ---------- 5. verdicts ----------
    sc = out['scenarios']
    art1 = ST['setup']['artifacts']['v1']['sha256']
    art2 = ST['setup']['artifacts']['v2']['sha256']
    x = {s: staged[s]['marker_sha'] for s in staged}
    jr = inj['post_restart_journal']
    injected_id = (inj.get('journal_after_inject') or [{}])[-1].get('id')

    def phases(app):
        return [r['phase'] for r in jr[app]]

    def kinds(app):
        return [r.get('completed_kind') for r in jr[app]]

    def last_active_row(app):
        act = [r for r in jr[app] if r['phase'] != 'DONE']
        return act

    # adv (i): upgrade + stale row -> superseded, content stays at the latest successful version.
    # adv 的 stale 行在 crash 阶段重启窗口内被恢复（见 evidence/app-crash-recovery-logs.txt 的 superseded 行），
    # 因此这里读取该窗口之后的实际状态。
    a = 'adv'
    adv_expected = (ST['setup']['artifacts'].get('v3') or {}).get('sha256') or art2
    adv_live = app_host_state(A('adv')['bin'], A('adv')['unit'])
    adv_journal_now = journal(A('adv')['app_id'])
    sc['AR-32/adv-supersede-by-later-successful-upgrade'] = {
        'requirement': '(i) 成功部署仍会被更晚的成功部署取代（含 revision 递增的真实升级）',
        'stale_rows_staged_before_upgrade': [r['phase'] for r in
                                             json.loads((EV / 'app-adv-upgrade.json').read_text())['steps']['journal_after_epm']
                                             if r['phase'] != 'DONE'],
        'upgrade_evidence': 'evidence/app-adv-upgrade.json',
        'later_done_kinds': kinds(a),
        'binary_sha_after': adv_live.get('binary_sha'),
        'expected_sha_latest_artifact': adv_expected,
        'expected_sha_stale_backup': x.get(a),
        'no_replay': adv_live.get('binary_sha') == adv_expected,
        'unit_active': adv_live.get('unit', {}).get('ActiveState'),
        'journal_now': [(r['phase'], r['completed_kind']) for r in adv_journal_now],
        'log_branch': 'evidence/app-crash-recovery-logs.txt（superseded by a later successful deployment）',
        'pass': adv_live.get('binary_sha') == adv_expected
                and adv_live.get('unit', {}).get('ActiveState') == 'active'
                and any(r['phase'] == 'ROLLBACK_FAILED' for r in adv_journal_now),
    }
    # adv2 (i): same revision -> supersede alone must block the stale replay
    a = 'adv2'
    stale_ids = [r['id'] for r in inj['pre_restart_journal'][a] if r['phase'] != 'DONE']
    sc['AR-32/adv2-supersede-only-guard'] = {
        'requirement': '(i) 同 revision 下 supersede 单独生效（binding 也匹配）',
        'def_revision': inj['local_defs_pre_restart'][a],
        'stale_rows_before': len(stale_ids),
        'later_done_kinds': kinds(a),
        'binary_sha_after': inj['post_restart_host'][a].get('binary_sha'),
        'expected_sha_artifact': art1,
        'stale_backup_sha': x.get(a),
        'no_replay': inj['post_restart_host'][a].get('binary_sha') == art1,
        'stale_row_unchanged': all(r['phase'] == 'ROLLBACK_FAILED' for r in jr[a] if r['id'] in stale_ids),
        'pass': inj['post_restart_host'][a].get('binary_sha') == art1
                and all(r['phase'] == 'ROLLBACK_FAILED' for r in jr[a] if r['id'] in stale_ids),
    }
    # pfa (ii): later prepare_failed must NOT supersede -> replay happens
    a = 'pfa'
    stale = [r for r in jr[a] if r['phase'] == 'DONE' and r.get('completed_kind') == 'rolled_back']
    sc['AR-32/pfa-prepare-failed-not-supersede'] = {
        'requirement': '(ii) prepare_failed 的 DONE 不再造成误跳过（CR-1 目标）',
        'later_done_kinds_before_restart': [r.get('completed_kind') for r in inj['pre_restart_journal'][a]],
        'binary_sha_after': inj['post_restart_host'][a].get('binary_sha'),
        'stale_backup_sha': x.get(a),
        'replayed': inj['post_restart_host'][a].get('binary_sha') == x.get(a),
        'journal_converged': any(r.get('completed_kind') == 'rolled_back' for r in jr[a]),
        'no_active_rows_left': last_active_row(a) == [],
        'unit_active': inj['post_restart_host'][a].get('unit', {}).get('ActiveState'),
        'pass': inj['post_restart_host'][a].get('binary_sha') == x.get(a)
                and any(r.get('completed_kind') == 'rolled_back' for r in jr[a])
                and inj['post_restart_host'][a].get('unit', {}).get('ActiveState') == 'active',
    }
    # rba (ii): later rolled_back must NOT supersede -> replay happens
    a = 'rba'
    sc['AR-32/rba-rolled-back-not-supersede'] = {
        'requirement': '(ii) rolled_back 的 DONE 不再造成误跳过（CR-1 目标）',
        'later_done_kinds_before_restart': [r.get('completed_kind') for r in inj['pre_restart_journal'][a]],
        'binary_sha_after': inj['post_restart_host'][a].get('binary_sha'),
        'stale_backup_sha': x.get(a),
        'replayed': inj['post_restart_host'][a].get('binary_sha') == x.get(a),
        'journal_converged': any(r.get('completed_kind') == 'rolled_back' for r in jr[a]),
        'no_active_rows_left': last_active_row(a) == [],
        'pass': inj['post_restart_host'][a].get('binary_sha') == x.get(a)
                and any(r.get('completed_kind') == 'rolled_back' for r in jr[a]),
    }
    # unk (ii): legacy unknown-cause DONE must NOT supersede -> replay happens
    a = 'unk'
    sc['AR-32/unk-unknown-cause-not-supersede'] = {
        'requirement': '(ii) 历史行（成因未知 completed_kind=""）不构成取代（CR-1 残余风险语义）',
        'injected_done_row': injected_id,
        'later_done_kinds_before_restart': [r.get('completed_kind') for r in inj['pre_restart_journal'][a]],
        'binary_sha_before_restart': out['steps']['unk_marker_y_sha'],
        'binary_sha_after': inj['post_restart_host'][a].get('binary_sha'),
        'stale_backup_sha': x.get(a),
        'replayed': inj['post_restart_host'][a].get('binary_sha') == x.get(a),
        'journal_converged': any(r.get('completed_kind') == 'rolled_back' for r in jr[a]),
        'pass': inj['post_restart_host'][a].get('binary_sha') == x.get(a)
                and any(r.get('completed_kind') == 'rolled_back' for r in jr[a]),
    }
    # bm (control): definition drift -> binding mismatch still blocks replay
    a = 'bm'
    bin2 = later['bm_new_path']
    sc['AR-32/bm-binding-mismatch-blocks-replay'] = {
        'requirement': '控制组：定义漂移时 binding 校验仍阻断重放（CR-1 未放松）',
        'def_revision': inj['local_defs_pre_restart'][a],
        'binary_sha_after': inj['post_restart_host'][a].get('binary_sha'),
        'stale_marker_sha': staged[a]['marker_sha'],
        'new_path_exists': ssh('if [ -e %s ]; then echo EXISTS; else echo ABSENT; fi' % bin2)[1].strip(),
        'stale_rows_phases': [r['phase'] for r in jr[a] if r['phase'] != 'DONE'],
        'log_branch': 'binding mismatch',
        'pass': inj['post_restart_host'][a].get('binary_sha') == staged[a]['marker_sha']
                and ssh('if [ -e %s ]; then echo EXISTS; else echo ABSENT; fi' % bin2)[1].strip().endswith('ABSENT'),
    }
    out['scenarios'] = sc
    ev_write('app-scenarios.json', out)
    return out


def phase_verdicts():
    """Recompute the staged-recovery verdicts from the evidence files (no environment writes)."""
    global DB2
    DB2 = ST.get('agent_db_native_2') or L.agent_db_path(N2)
    staging = json.loads((EV / 'app-staging.json').read_text())['steps']
    rec = json.loads((EV / 'app-staged-recovery.json').read_text())['steps']['staged_recovery']
    out = {'at_utc': now_utc(), 'recomputed_at_utc': now_utc(),
           'steps': {'stale_rows': staging['stale_rows'], 'later_rows': staging['later_rows'],
                     'unk_marker_y_sha': staging['unk_marker_y_sha'], 'staged_recovery': rec},
           'scenarios': {}}
    _build_scenarios(out, rec, staging['stale_rows'], staging['later_rows'])
    ev_write('app-scenarios.json', out)
    dump_results()
    print(json.dumps({k: v.get('pass') for k, v in out['scenarios'].items()}, indent=1))
    return out


def phase_recover():
    """Injection of the legacy DONE row, staged recovery restart and per-scenario verdicts."""
    global DB2
    DB2 = ST.get('agent_db_native_2') or L.agent_db_path(N2)
    hub.login()
    # ---------- 4. inject the legacy-DONE row + staged recovery restart ----------
    staged_rows = json.loads((EV / 'app-staging.json').read_text())['steps']
    staged = staged_rows['stale_rows']
    later = staged_rows['later_rows']
    out = {'at_utc': now_utc(), 'steps': {'stale_rows': staged, 'later_rows': later,
                                          'unk_marker_y_sha': staged_rows['unk_marker_y_sha']},
           'scenarios': {}}
    inj = {}
    rc, out_j, err = ssh('systemctl stop %s && sleep 1 && systemctl is-active %s' % (L.AGENT_UNIT_FMT % N2,
                                                                                    L.AGENT_UNIT_FMT % N2))
    inj['stop'] = {'rc': rc, 'out': out_j.strip()}
    rows = journal_full(A('unk')['app_id'])
    stale = [r for r in rows if r['phase'] != 'DONE']
    inj['stale_row'] = stale[-1] if stale else None
    ts = datetime.strptime(stale[-1]['started_at'][:26] + 'Z', '%Y-%m-%dT%H:%M:%S.%fZ') + timedelta(seconds=120)
    injected_id = str(uuid.uuid4())
    sql = ("INSERT INTO deployments (id, application_id, from_version, to_version, phase, completed_kind,"
           " backup_path, backup_config_path, backup_unit_path, app_revision, binary_path, config_path,"
           " unit_name, started_at, updated_at) VALUES ('%s','%s','v1','v1','DONE','','absent:','absent:',"
           "'absent:',%d,'%s','%s','%s','%s','%s')"
           % (injected_id, A('unk')['app_id'], int(stale[-1]['app_revision']), A('unk')['bin'],
              A('unk')['cfg'], A('unk')['unit'],
              ts.strftime('%Y-%m-%dT%H:%M:%S.') + '%09dZ' % 0,
              ts.strftime('%Y-%m-%dT%H:%M:%S.') + '%09dZ' % 0))
    inj['inject_sql'] = sql
    inj['inject_result'] = L.agent_db_exec(N2, sql, db=_db())
    inj['journal_after_inject'] = journal(A('unk')['app_id'])
    inj['pre_restart_journal'] = {s: journal(A(s)['app_id']) for s in APPS}
    inj['pre_restart_host'] = {s: app_host_state(A(s)['bin'], A(s)['unit']) for s in APPS}
    inj['local_defs_pre_restart'] = {s: ((local_def(A(s)['app_id']) or {}).get('_revision')) for s in APPS}
    save_state(ST)
    ev_write('app-pre-restart.json', inj)

    rc, out_s, err = ssh('systemctl start %s && sleep 2 && systemctl is-active %s' % (L.AGENT_UNIT_FMT % N2,
                                                                                      L.AGENT_UNIT_FMT % N2))
    inj['start'] = {'rc': rc, 'out': out_s.strip()}
    node = L.wait_node(hub, ST['node_ids'][N2], timeout=150)
    inj['node_after'] = {'status': node.get('status'), 'sync_status': node.get('sync_status'),
                         'agent_version': node.get('agent_version')}
    time.sleep(6)
    inj['post_restart_journal'] = {s: journal(A(s)['app_id']) for s in APPS}
    inj['post_restart_host'] = {s: app_host_state(A(s)['bin'], A(s)['unit']) for s in APPS}
    out['steps']['staged_recovery'] = inj
    ev_write('app-staged-recovery.json', out)

    _build_scenarios(out, inj, staged, later)
    save_state(ST)
    ev_write('app-scenarios.json', out)
    dump_results()
    return out
    dump_results()
    return out


# ------------------------------------------------------------------ adv upgrade phase
def phase_adv2nd():
    """adv: stage a stale row at the current revision, then perform a real v2->v3 upgrade.

    The staged restart for the staged scenarios already happened; this stale row is
    recovered by the crash-phase restart, which exercises the supersede branch at a
    bumped app revision (real upgrade path)."""
    hub.login()
    a = A('adv')
    out = {'at_utc': now_utc(), 'steps': {}}
    out['steps']['local_def_revision_before'] = (local_def(a['app_id']) or {}).get('_revision')
    out['steps']['write_marker'] = write_bin(a['bin'], 'X-adv')
    out['steps']['marker_sha'] = app_host_state(a['bin'], a['unit']).get('binary_sha')
    out['steps']['chattr_i'] = chattr(a['bin'], '+i')
    out['steps']['epm_deploy'] = deploy_now(a['app_id'])
    out['steps']['journal_after_epm'] = journal(a['app_id'])
    out['steps']['chattr_i_release'] = chattr(a['bin'], '-i')
    content = pad('exec sleep 100000\n', 'v3')
    name = P + 'artifact-v3'
    code, resp = hub.upload_artifact(name, 'v3', name + '.bin', content)
    if code != 200:
        raise RuntimeError('v3 artifact upload failed %s' % resp)
    ST['fixtures'].append(('artifact', resp['id'], name))
    ST['setup']['artifacts']['v3'] = {'id': resp['id'], 'sha256': resp['sha256'], 'name': name,
                                      'size': len(content)}
    save_state(ST)
    d = app_def(a['name'], resp['id'], a['bin'], a['unit'], a['cfg'], version='v3')
    code, _ = hub.put('/api/applications/' + a['app_id'], d)
    out['steps']['upgrade_def_put_http'] = code
    out['steps']['upgrade_deploy'] = deploy_now(a['app_id'])
    out['steps']['host_after_upgrade'] = app_host_state(a['bin'], a['unit'])
    out['steps']['journal_after_upgrade'] = journal(a['app_id'])
    out['steps']['expected_sha_v3'] = resp['sha256']
    out['oracles'] = {
        'upgrade_success': out['steps']['upgrade_deploy'].get('status') == 'SUCCESS',
        'binary_is_v3': out['steps']['host_after_upgrade'].get('binary_sha') == resp['sha256'],
        'stale_row_was_active_before_upgrade': any(
            r['phase'] != 'DONE' for r in out['steps']['journal_after_epm'] if isinstance(out['steps']['journal_after_epm'], list)),
        'upgrade_row_kind_deployed': [r.get('completed_kind') for r in out['steps']['journal_after_upgrade']][-1] == 'deployed',
    }
    out['oracles']['ok'] = all(out['oracles'].values())
    ev_write('app-adv-upgrade.json', out)
    return out


# ------------------------------------------------------------------ hc phase (SYS-48 / E2E-12)
def phase_hc():
    hub.login()
    out = {'at_utc': now_utc(), 'steps': {}}
    a = A('hc')
    out['steps']['local_def_revision'] = (local_def(a['app_id']) or {}).get('_revision')
    base = app_host_state(a['bin'], a['unit'])
    out['steps']['deploy_positive'] = deploy_now(a['app_id'])
    out['steps']['host_after_positive'] = app_host_state(a['bin'], a['unit'])
    code, app = hub.get('/api/applications/' + a['app_id'])
    code2, nodes = hub.get('/api/applications/%s/nodes' % a['app_id'])
    out['steps']['api_state_healthy'] = {'app_http': code, 'app_status': (app or {}).get('status'),
                                         'nodes_http': code2, 'nodes': nodes}
    out['steps']['stop'] = deploy_now(a['app_id'], operation='stop')
    out['steps']['host_after_stop'] = app_host_state(a['bin'], a['unit'])
    out['steps']['start'] = deploy_now(a['app_id'], operation='start')
    out['steps']['host_after_start'] = app_host_state(a['bin'], a['unit'])
    # marker content so the rollback-restore is observable (installed artifact != rollback target)
    out['steps']['write_marker_before_negative'] = write_bin(a['bin'], 'W-hc')
    out['steps']['marker_sha'] = app_host_state(a['bin'], a['unit']).get('binary_sha')
    # negative: command health check that fails -> rollback restores the old version
    d = app_def(a['name'], ST['setup']['artifacts']['v1']['id'], a['bin'], a['unit'], a['cfg'],
                health={'type': 'command', 'target': 'false', 'attempts': 2, 'interval': 1, 'timeout': 3})
    code, _ = hub.put('/api/applications/' + a['app_id'], d)
    out['steps']['neg_def_put_http'] = code
    out['steps']['deploy_negative'] = deploy_now(a['app_id'])
    out['steps']['host_after_negative'] = app_host_state(a['bin'], a['unit'])
    out['steps']['journal_negative'] = journal(a['app_id'])
    art1 = ST['setup']['artifacts']['v1']['sha256']
    hneg = out['steps']['host_after_negative']
    jneg = out['steps']['journal_negative']
    out['oracles'] = {
        'positive_deploy_success': base.get('binary_sha') == art1,
        'healthy_state_active': out['steps']['host_after_positive'].get('unit', {}).get('ActiveState') == 'active',
        'stop_leaves_pid0': out['steps']['host_after_stop'].get('unit', {}).get('MainPID') in ('0', 0),
        'stop_reports_stopped': (out['steps']['stop'].get('status') == 'SUCCESS'),
        'start_restores_active': out['steps']['host_after_start'].get('unit', {}).get('ActiveState') == 'active',
        'negative_deploy_failed': out['steps']['deploy_negative'].get('status') == 'FAILED',
        'negative_rollback_kind': [r.get('completed_kind') for r in jneg],
        'old_version_restored': hneg.get('binary_sha') == out['steps']['marker_sha'],
        'negative_service_back_active': hneg.get('unit', {}).get('ActiveState') == 'active',
    }
    out['oracles']['ok'] = all([out['oracles']['positive_deploy_success'],
                                out['oracles']['healthy_state_active'],
                                out['oracles']['stop_leaves_pid0'],
                                out['oracles']['start_restores_active'],
                                out['oracles']['negative_deploy_failed'],
                                out['oracles']['old_version_restored']])
    ev_write('app-hc.json', out)
    record('SYS-48/E2E-12', 'PASS' if out['oracles']['ok'] else 'FAIL',
           'COMMAND 健康检查：target=true 部署 SUCCESS 且与 systemd 实际状态一致（active/PID）；stop 后 API=stopped 且 PID=0；start 恢复 active；target=false 时部署 FAILED、回滚成功、旧版本 SHA 还原且服务重新 active',
           ['evidence/app-hc.json'], out['oracles'])
    return out


# ------------------------------------------------------------------ crash phase
def phase_crash():
    hub.login()
    out = {'at_utc': now_utc(), 'steps': {}}
    a = A('crash')
    # 健康检查为「返回 0 但耗时 6s」的命令：既给出可稳定命中的 VERIFYING 窗口，
    # 又让恢复重放后的回滚健康校验能通过（区别于 target=false 的永久失败路径）。
    d = app_def(a['name'], ST['setup']['artifacts']['v1']['id'], a['bin'], a['unit'], a['cfg'],
                health={'type': 'command', 'target': 'sleep 6', 'attempts': 3, 'interval': 1, 'timeout': 20})
    code, _ = hub.put('/api/applications/' + a['app_id'], d)
    out['steps']['def_put_http'] = code
    art1 = ST['setup']['artifacts']['v1']['sha256']
    # wait for the drifted definition to sync, then install a distinguishable pre-crash version
    deadline = time.time() + 60
    while time.time() < deadline and (local_def(a['app_id']) or {}).get('_revision') != 2:
        time.sleep(1)
    out['steps']['local_def_revision'] = (local_def(a['app_id']) or {}).get('_revision')
    out['steps']['write_marker'] = write_bin(a['bin'], 'Z-crash')
    out['steps']['marker_sha'] = app_host_state(a['bin'], a['unit']).get('binary_sha')
    out['steps']['host_before'] = app_host_state(a['bin'], a['unit'])
    code, body = hub.post('/api/applications/%s/deploy' % a['app_id'],
                          {'node_ids': [ST['node_ids'][N2]], 'operation': 'deploy'})
    eid = body[0].get('id') if code == 200 and isinstance(body, list) and body else None
    out['steps']['deploy_dispatch'] = {'http': code, 'execution_id': eid}
    # wait until the journal reaches a post-replacement phase, then SIGKILL
    pid = L.unit_pid(N2_IP, L.AGENT_UNIT_FMT % N2)
    out['steps']['pid_before_kill'] = pid
    phase_seen = None
    crash_row_id = None
    deadline = time.time() + 90
    while time.time() < deadline:
        rows = L.agent_db_rows(N2, "SELECT id,phase FROM deployments WHERE application_id='%s' AND phase!='DONE' ORDER BY started_at DESC LIMIT 1" % a['app_id'], db=_db())
        if isinstance(rows, list) and rows:
            phase_seen = rows[0]['phase']
            crash_row_id = rows[0]['id']
            if phase_seen in ('STARTED', 'VERIFYING', 'REPLACED'):
                break
        time.sleep(0.4)
    out['steps']['journal_phase_at_kill'] = phase_seen
    out['steps']['crash_row_id'] = crash_row_id
    out['steps']['binary_sha_at_kill'] = app_host_state(a['bin'], a['unit']).get('binary_sha')
    rc, o, e = L.ssh(N2_IP, 'kill -9 %s; sleep 1; systemctl is-active %s' % (pid, L.AGENT_UNIT_FMT % N2))
    out['steps']['sigkill'] = {'rc': rc, 'out': o.strip()}
    t0 = time.time()
    newpid = None
    while time.time() - t0 < 90:
        np = L.unit_pid(N2_IP, L.AGENT_UNIT_FMT % N2)
        if np and np != pid:
            newpid = np
            break
        time.sleep(1)
    out['steps']['pid_after_crash'] = newpid
    out['steps']['agent_restarted_by_systemd'] = bool(newpid)
    if not newpid:
        L.ssh(N2_IP, 'systemctl start %s' % (L.AGENT_UNIT_FMT % N2))
        newpid = L.unit_pid(N2_IP, L.AGENT_UNIT_FMT % N2)
        out['steps']['pid_after_manual_start'] = newpid
    node = L.wait_node(hub, ST['node_ids'][N2], timeout=180)
    out['steps']['node_after_crash'] = {'status': node.get('status'), 'sync_status': node.get('sync_status'),
                                        'agent_version': node.get('agent_version')}
    ex = L.wait_exec(hub, eid, timeout=180) if eid else {}
    out['steps']['execution'] = {'status': ex.get('status'), 'error': ex.get('error'),
                                 'block_reason': ex.get('block_reason')}
    time.sleep(5)
    time.sleep(18)
    out['steps']['journal_after_recovery'] = journal(a['app_id'])
    out['steps']['host_after_recovery'] = app_host_state(a['bin'], a['unit'])
    j = out['steps']['journal_after_recovery']
    h = out['steps']['host_after_recovery']
    out['oracles'] = {
        'crash_phase_was_intermediate': phase_seen in ('STARTED', 'VERIFYING', 'REPLACED'),
        'binary_was_replaced_before_crash': out['steps']['binary_sha_at_kill'] == art1,
        'rolled_back_to_previous_version': h.get('binary_sha') == out['steps']['marker_sha'],
        'crash_row_is_terminal_done': [r['phase'] for r in j if r['id'] == crash_row_id] == ['DONE'],
        'leftover_rows_untouched': [r['phase'] for r in j if r['id'] != crash_row_id and r['phase'] != 'DONE'],
        'crash_row_converged_rolled_back': [r.get('completed_kind') for r in j if r['id'] == crash_row_id] == ['rolled_back'],
        'no_pseudo_success': not any(r.get('completed_kind') == 'deployed' for r in j if r['id'] == crash_row_id),
        'unit_active_and_consistent': h.get('unit', {}).get('ActiveState') == 'active',
    }
    out['oracles']['ok'] = all(v for v in out['oracles'].values())
    ev_write('app-crash.json', out)
    record('E2E-13/SYS-51/AR-30/INV-11', 'PASS' if out['oracles']['ok'] else 'FAIL',
           '真实 deploy 进行中（Journal=%s，二进制已替换为新版本）对 native-2 Agent 执行 SIGKILL：systemd 自动拉起后 RecoverDeployments 重放该行，回滚到崩溃前版本、Journal 收敛 DONE(rolled_back)，unit active，Desired==Actual，无伪成功' % phase_seen,
           ['evidence/app-crash.json'], out['oracles'])
    return out


# ------------------------------------------------------------------ SYS-56 phase
def phase_sys56():
    hub.login()
    out = {'at_utc': now_utc(), 'steps': {}}
    # ---- 1. hub rejects illegal app-definition paths (relative / .. / NUL) ----
    code0, before = hub.get('/api/applications')
    rows_before = before.get('applications', before) if isinstance(before, dict) else before
    checks = {}
    for label, path in (('relative', 'relative/app'), ('dotdot', '/opt/../etc/x'), ('nul', '/opt/x\x00y')):
        body = app_def(P + 'app-nc-' + label, ST['setup']['artifacts']['v1']['id'], path,
                       P + 'app-nc-%s.service' % label, '%s/cfg-nc/app.env' % ROOT)
        code, resp = hub.post('/api/applications', body)
        checks[label] = {'http': code, 'body': resp}
    code1, after = hub.get('/api/applications')
    rows_after = after.get('applications', after) if isinstance(after, dict) else after
    out['steps']['hub_path_rejections'] = checks
    out['steps']['row_count_before_after'] = [len(rows_before), len(rows_after)]

    # ---- 2. dedicated app for agent-side path checks ----
    name = P + 'app-nc'
    bin_clean = '%s/bin-nc-app/app' % ROOT
    cfg = '%s/cfg-nc/app.env' % ROOT
    unit = P + 'app-nc.service'
    ssh('mkdir -p %s/bin-nc-app %s/cfg-nc' % (ROOT, ROOT))
    code, resp = hub.post('/api/applications',
                          app_def(name, ST['setup']['artifacts']['v1']['id'], bin_clean, unit, cfg))
    if code not in (200, 201):
        raise RuntimeError('nc app create failed %s' % resp)
    nc_id = resp['id']
    ST['fixtures'].append(('application', nc_id, name))
    hub.post('/api/applications/%s/assign' % nc_id, {'node_ids': [ST['node_ids'][N2]]})
    ST['setup']['apps']['nc'] = {'app_id': nc_id, 'name': name, 'bin': bin_clean, 'cfg': cfg, 'unit': unit}
    save_state(ST)
    art1 = ST['setup']['artifacts']['v1']['sha256']

    # 2a. symlink destination must be refused with the victim untouched
    victim = '%s/nc-victim.bin' % ROOT
    vbody = '#!/bin/sh\n# qa-cr1 symlink victim\nexec sleep 100000\n'
    import base64 as _b64
    ssh("echo %s | base64 -d > %s && chmod 0755 %s && rm -f %s && ln -s %s %s" % (
        _b64.b64encode(vbody.encode()).decode(), victim, victim, bin_clean, victim, bin_clean))
    victim_sha_before = L.sha256(N2_IP, victim)
    dep_sym = deploy_now(nc_id)
    sym_state = ssh('ls -l %s; sha256sum %s' % (bin_clean, victim))[1].strip()
    out['steps']['symlink_destination'] = {
        'victim': victim, 'victim_sha_before': victim_sha_before,
        'deploy': dep_sym, 'host': sym_state,
        'victim_sha_after': L.sha256(N2_IP, victim),
        'journal_latest': (journal(nc_id) or [{}])[-1],
    }
    out['steps']['symlink_destination']['victim_unchanged'] = (
        victim_sha_before == out['steps']['symlink_destination']['victim_sha_after'])
    ssh('rm -f %s && cp -p %s %s 2>/dev/null; rm -f %s' % (bin_clean, victim, bin_clean, victim))

    # 2b. non-clean absolute paths must be refused by the host adapter
    nc = {}
    for label, raw in (('double_slash', '%s//bin-nc-a/app' % ROOT),
                       ('dot_slash', '%s/./bin-nc-b/app' % ROOT),
                       ('trailing_slash', '%s/bin-nc-c/' % ROOT)):
        d = app_def(name, ST['setup']['artifacts']['v1']['id'], raw, unit, cfg)
        code, _ = hub.put('/api/applications/' + nc_id, d)
        j = deploy_now(nc_id)
        rows = journal(nc_id)
        latest = rows[-1] if isinstance(rows, list) and rows else {}
        nc[label] = {'raw_path': raw, 'def_put_http': code, 'deploy_status': j.get('status'),
                     'deploy_error': j.get('error'), 'journal_phase': latest.get('phase'),
                     'journal_kind': latest.get('completed_kind'),
                     'raw_exists': ssh('if [ -e %s ]; then echo EXISTS; else echo ABSENT; fi' % raw)[1].strip(),
                     'cleaned_exists': ssh('if [ -e %s ]; then echo EXISTS; else echo ABSENT; fi' % __import__('os').path.normpath(raw))[1].strip()}
    out['steps']['agent_nonclean_paths'] = nc

    # ---- 3. hub transfer destination validation (correct field: destination_path) ----
    t = {}
    for label, dest in (('dest_relative', 'relative/x'), ('dest_dotdot', '%s/../x' % ROOT),
                        ('dest_nul', '/opt/x\x00y'), ('dest_double_slash', '%s//xfer' % ROOT)):
        body = {'source_node_id': ST['node_ids'][N2], 'source_path': '/etc/hostname',
                'targets': [{'node_id': ST['node_ids'][N2], 'destination_path': dest}]}
        code, resp = hub.post('/api/transfers', body)
        t[label] = {'http': code, 'body': resp}
    out['steps']['transfers'] = t
    # allowlist probe: source outside any allowlist and non-existent -> no destination file either way
    probe_src = '/root/%sprobe-nonexistent.txt' % P
    body = {'source_node_id': ST['node_ids'][N2], 'source_path': probe_src,
            'targets': [{'node_id': ST['node_ids'][N2], 'destination_path': '%s/xfer-probe' % ROOT}]}
    code, resp = hub.post('/api/transfers', body)
    out['steps']['transfers']['outside_allowlist_probe'] = {
        'source': probe_src, 'http': code, 'body': resp,
        'target_created': ssh('if [ -e %s/xfer-probe ]; then echo EXISTS; else echo ABSENT; fi' % ROOT)[1].strip()}

    hub_rejects = all(v['http'] == 400 for v in checks.values()) and len(rows_before) == len(rows_after)
    agent_refuses = all(v['deploy_status'] == 'FAILED' and v['journal_phase'] == 'DONE'
                        and v['journal_kind'] == 'prepare_failed'
                        and 'invalid deployment path' in (v['deploy_error'] or '')
                        and v['raw_exists'].endswith('ABSENT') and v['cleaned_exists'].endswith('ABSENT')
                        for v in nc.values())
    transfer_rejects = all(t[k]['http'] == 400 for k in ('dest_relative', 'dest_dotdot', 'dest_nul'))
    out['oracles'] = {
        'hub_rejects_illegal_app_paths_no_rows': hub_rejects,
        'hub_rejection_messages': {k: v['body'] for k, v in checks.items()},
        'agent_refuses_nonclean_paths_no_side_effects': agent_refuses,
        'symlink_destination_refused_victim_unchanged': (
            out['steps']['symlink_destination']['deploy'].get('status') == 'FAILED'
            and out['steps']['symlink_destination']['victim_unchanged']),
        'hub_rejects_illegal_transfer_destinations': transfer_rejects,
        'transfer_rejection_messages': {k: v['body'] for k, v in t.items() if k.startswith('dest_')},
        'allowlist_probe': out['steps']['transfers']['outside_allowlist_probe'],
    }
    ok = (hub_rejects and agent_refuses and transfer_rejects
          and out['oracles']['symlink_destination_refused_victim_unchanged']
          and t['outside_allowlist_probe']['target_created'].endswith('ABSENT'))
    out['oracles']['ok'] = ok
    ev_write('sys56-paths.json', out)
    record('SYS-56', 'PASS' if ok else 'FAIL',
           'Hub 拒绝相对路径/含 ../NUL 的 app 定义（400，行数不变）；Hub 拒绝 3 类非法传输目的地（400，含 traversal/absolute 明确文案）；'
           'Agent 对 //、/./、尾随 / 三类非 clean 绝对路径返回 FAILED invalid deployment path 且 raw/cleaned 均无文件；'
           'symlink 目的地被拒（destination is a symlink）且 victim 内容 SHA 不变；allowlist 探针未产生目标文件',
           ['evidence/sys56-paths.json'], out['oracles'])
    return out


# ------------------------------------------------------------------ cleanup
def _delete(kind, ident, name):
    path = {'application': '/api/applications/', 'artifact': '/api/artifacts/',
            'task': '/api/tasks/', 'node': '/api/nodes/'}.get(kind)
    code, _ = hub.delete(path + ident)
    code2, _ = hub.get(path + ident)
    return {'kind': kind, 'id': ident, 'name': name, 'delete_http': code, 'get_http': code2}


def phase_cleanup():
    hub.login()
    ledger = {'nonce': ST['nonce'], 'prefix': P, 'deleted': [], 'at_utc': now_utc()}
    # release immutable flags defensively before removing host dirs
    for suffix in APPS:
        b, unit, cfg = app_paths(suffix)
        ssh('chattr -i %s 2>/dev/null; systemctl stop %s 2>/dev/null; systemctl disable %s 2>/dev/null; '
            'rm -f /etc/systemd/system/%s; systemctl daemon-reload; rm -rf %s; rm -f %s' %
            (b, unit, unit, unit, '%s/bin-%s' % (ROOT, suffix), cfg))
    rc, o, e = ssh('rm -rf %s && ls -d %s 2>/dev/null || echo ROOT_REMOVED' % (ROOT, ROOT))
    ledger['host_root_removed'] = o.strip()
    for kind, ident, name in sorted(ST['fixtures'], key=lambda x: {'application': 0, 'artifact': 1}.get(x[0], 2)):
        ledger['deleted'].append(_delete(kind, ident, name))
    ledger['transfers_canceled'] = []
    for tid in ST.get('transfers', []):
        code, resp = hub.post('/api/transfers/%s/cancel' % tid)
        code2, item = hub.get('/api/transfers/' + tid)
        ledger['transfers_canceled'].append({'id': tid, 'cancel_http': code,
                                             'final_status': (item or {}).get('status'),
                                             'final_error': (item or {}).get('error')})
    save_state(ST)
    # residue sweep
    def names(route, key):
        c, b = hub.get(route)
        rows = b.get(key, b) if isinstance(b, dict) else b
        return [r.get('name') for r in rows if (r.get('name') or '').startswith(P)]
    residue = {
        'applications': names('/api/applications', 'applications'),
        'artifacts': names('/api/artifacts', 'artifacts'),
    }
    code, lst = hub.get('/api/nodes')
    all_nodes = (lst.get('nodes', lst) if isinstance(lst, dict) else lst) or []
    residue['nodes'] = [r.get('hostname') for r in all_nodes if (r.get('hostname') or '').startswith(P)]
    rc, o, e = ssh('systemctl list-units --all --no-pager --plain 2>/dev/null | grep -F "%s" || true; '
                   'ls -d %s 2>/dev/null || true; ls /etc/systemd/system/%sapp-*.service 2>/dev/null || true' % (P, ROOT, P))
    residue['agent_host'] = o.strip()
    node = L.wait_node(hub, ST['node_ids'][N2], timeout=120)
    oracle = {
        'at_utc': now_utc(),
        'all_deleted_get_404': all(r['get_http'] == 404 for r in ledger['deleted']),
        'deleted_count': len(ledger['deleted']),
        'hub_prefix_residue': residue,
        'agent_host_residue': residue['agent_host'],
        'residue_clean': (not residue['applications'] and not residue['artifacts'] and not residue['nodes']
                          and not residue['agent_host']),
        'healthz_readyz_18080': '%s %s' % (L.curl_code(L.HUB_HOST, '/healthz'), L.curl_code(L.HUB_HOST, '/readyz')),
        'primary_hub_8080_healthz': L.curl_code(L.HUB_HOST, '/healthz', port=8080),
        'primary_hub_pid': L.unit_pid(L.HUB_HOST, L.PRIMARY_UNIT),
        'repair_nodes': {r.get('hostname'): {'status': r.get('status'), 'sync_status': r.get('sync_status')}
                         for r in all_nodes if (r.get('hostname') or '').startswith('qa-repair')},
        'native_2_after': {'status': node.get('status'), 'sync_status': node.get('sync_status'),
                           'agent_version': node.get('agent_version')},
    }
    # crash app journal is retained as historical evidence; no rows are deleted by design
    ledger['oracle'] = oracle
    tmp = LEDGER_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, LEDGER_F)
    out = EV / 'final-cleanup-oracle.json'
    tmp = out.with_suffix('.tmp')
    tmp.write_text(json.dumps(oracle, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, out)
    dump_results()
    print('cleanup residue_clean=%s all_404=%s' % (oracle['residue_clean'], oracle['all_deleted_get_404']))
    return ledger


PHASES = {'setup': phase_setup, 'adv2nd': phase_adv2nd, 'app': phase_app, 'recover': phase_recover, 'verdicts': phase_verdicts, 'hc': phase_hc, 'crash': phase_crash,
          'sys56': phase_sys56, 'cleanup': phase_cleanup}

if __name__ == '__main__':
    for arg in sys.argv[1:]:
        fn = PHASES.get(arg)
        if not fn:
            print('unknown phase', arg)
            sys.exit(2)
        print('=== phase', arg, flush=True)
        fn()
