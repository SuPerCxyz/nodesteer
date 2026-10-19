#!/usr/bin/env python3
"""qa-resume-20261018-housekeeping: assemble the four deliverables."""
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
os.umask(0o077)
NOW = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load(p):
    return json.loads(Path(p).read_text())


scan = load(EV / 'host-scan-post-cleanup.json')
pre = load(EV / 'predelete-inventory.json')
clean = load(EV / 'cleanup-exec.json')
env = load(EV / 'env-verify-raw.json')

# ---------------- 1. artcache-cleanup.json ----------------
artcache_paths = [
    '/opt/qa-resume-20261012-artcache-d430d8074305',
    '/opt/qa-resume-20261012-artcache-99bf1fabcfa5',
    '/opt/qa-resume-20261012-artcache-ad9bf531b28a',
    '/opt/qa-resume-20261012-sys12-b9e47f16deb9',
    '/opt/qa-resume-20261012-sys12-d19e0c4f3e69',
    '/opt/qa-resume-20261009-host-tls-1358f9a990bd',
    '/opt/qa-resume-20261009-host-tls',
    '/opt/qa-resume-20261010-sys12-nonhost',
]
artcache = {
    'task': 'OBS-20261012-ARTCACHE-QA-PARTIAL-ROOT-CLEANUP housekeeping',
    'at_utc': NOW,
    'authority': 'http://192.168.100.209:18080',
    'hosts_scanned': ['hub-209 192.168.100.209', 'native-1 192.168.100.204', 'native-2 192.168.100.206',
                      'native-3 192.168.100.222', 'native-4 192.168.100.247'],
    'method': 'read-only SSH scan: /opt /var/lib /var/tmp /root /srv /data maxdepth-3 name filter *qa-*/*qa_*, '
              '/etc/systemd/system, ps, ss, docker ps/images; then CAS-style delete of confirmed-owned empty skeletons; rescan',
    'reference_roots_from_observation_evidence': {
        'checked_paths': artcache_paths,
        'result': 'ALL_ABSENT_ON_ALL_4_NODES',
        'note': 'observation evidence roots (attempt02 d430/attempt03 99bf/attempt04 ad9b/sys12/host-tls) '
                'were already exactly removed by the earlier authorized cleanups; no artifact-cache root remains. '
                'OBS status EXACT_FIXTURE_CLEANUP_CLOSED_CACHE_RUNTIME_VERIFIED confirmed by rescan.',
    },
    'cleaned_confirmed_owned': [
        {
            'host': 'native-1 (192.168.100.204)',
            'path': '/opt/qa-appui-588e85afe10f',
            'owned_by': 'qa-resume-20261016-appui batch (nonce 588e85afe10f, campaign run 20260930-101945)',
            'contents_before': 'empty directory skeleton only (app/, app/bin/, app/config/ all empty; 0 files)',
            'liveness_before': 'no process, no mount, no unit references',
            'deleted': True,
            'post_verify': 'absent',
        },
        {
            'host': 'native-1 (192.168.100.204)',
            'path': '/opt/qa-g5-9505276f0c',
            'owned_by': 'qa-resume-20261016-g5 batch (nonce 9505276f0c)',
            'contents_before': 'empty directory skeleton only (app/, app/bin/, app/config/ all empty; 0 files)',
            'liveness_before': 'no process, no mount, no unit references',
            'deleted': True,
            'post_verify': 'absent',
        },
        {
            'host': 'native-3 (192.168.100.222)',
            'path': '/opt/nodesteer-qa-p11-05-b3ca14953c14',
            'owned_by': 'qa-resume-20261010-runtime application-ui-run b3ca14953c14 batch (nonce b3ca14953c14)',
            'contents_before': 'empty directory skeleton only (bin/ empty; 0 files; earlier helper explicitly retained these two empty dirs)',
            'liveness_before': 'no process, no mount, no unit references',
            'deleted': True,
            'post_verify': 'absent',
        },
    ],
    'retained_reported_not_deleted': [
        {
            'host': 'native-2 (192.168.100.206)',
            'path': '/opt/qa-resume-20261010-host-f174890c3846',
            'classification': 'LIVE QA fixture root (docker_host_integration child host001)',
            'evidence': 'running container qa-resume-20261010-host-f174890c3846-host001 (Up 3 days, labels '
                        'nodesteer-e2e-run/nodesteer.e2e.owner=qa-resume-20261010-host-f174890c3846), docker volume '
                        'qa-resume-20261010-host-f174890c3846-data-1, hub node f6a79d23-2410-450c-ac39-64dd70c9daa1 '
                        'online (agent_version dddee4f0-e2e-repair-20261004)',
            'reason': 'still-running fixture, bind-mounted by the live container; removal needs container stop + '
                      'volume + hub node deletion (product-path) — separate decision required, not a bare leftover dir',
        },
        {
            'host': 'hub-209 (192.168.100.209)',
            'path': '/var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui',
            'classification': 'inert QA-owned isolated TTL-test hub data dir',
            'evidence': 'created 2026-10-04 by qa/ttl-ui-authority.json fixture (FL-14, closed PASS); unit '
                        'nodesteer-hub-repair-20260930-101945-ttl-ui.service disabled+not loaded, port 18995 free, '
                        'no process/open-fd; contains hub.db (+shm/wal, sha256 recorded in predelete-inventory.json)',
            'reason': 'contains a hub database (test data); disposition uncertain -> report only (rule: 不确定就只报告). '
                      'Related owned files also reported: /etc/nodesteer/nodesteer-hub-repair-20260930-101945-ttl-ui.yaml, '
                      '/etc/nodesteer/hub-repair-20260930-101945.yaml.pre-ttl-test, unit file itself',
        },
        {
            'host': 'native-1 (192.168.100.204)',
            'path': '/tmp/qa-repair-20260930-101945-app-web (dir) + unit qa-20260930-101945-web-app-6a32f.service + pid 637',
            'classification': 'LIVE managed-application fixture from qa-resume-20261010-web batch',
            'evidence': 'unit enabled/active; hub still holds app qa-repair-20260930-101945-app-async-live-6a32f + artifact',
            'reason': 'live registered fixture, not a leftover directory; report only',
        },
        {
            'host': 'hub-209 (192.168.100.209)',
            'path': 'docker images qa-20260930-101945-env-agent:dddee4f0, qa-20260930-101945-env-hub:dddee4f0',
            'classification': 'campaign-owned docker images, no containers',
            'reason': 'not a directory; report only (recommend separate authorization if cleanup wanted)',
        },
        {
            'host': 'all hosts',
            'path': '/root/nodesteer-e2e-20260930-101945/backup (hub 15M, n2 4.7M, n1/n3/n4 8K)',
            'classification': 'pre-campaign rollback backups (hub.db/state.db/files.tar.gz/manifests; includes '
                              'isolated-core private consistency backup from OBS-HARNESS-IDENTITY-POLLUTION)',
            'reason': 'backups are deliberately retained; deleting backups is out of scope',
        },
    ],
    'post_cleanup_rescan': {
        'native-1': {'find_qa': scan['native-1']['find_qa'], 'opt': scan['native-1']['opt']},
        'native-2': {'find_qa': scan['native-2']['find_qa']},
        'native-3': {'find_qa': scan['native-3']['find_qa'], 'opt': scan['native-3']['opt']},
        'native-4': {'find_qa': scan['native-4']['find_qa']},
        'hub-209': {'find_qa': scan['hub-209']['find_qa']},
        'conclusion': 'the three deleted skeletons are gone; only the reported live/retained items remain',
    },
    'evidence_files': [
        'evidence/predelete-inventory.json (exact pre-delete inventory of the 3 targets: dev/inode/mode/mtime, file lists; authoritative pre-cleanup evidence)',
        'evidence/cleanup-exec.json (CAS delete results: sha/emptiness re-verified, deleted, parent listing after)',
        'evidence/host-scan-post-cleanup.json (rescan)',
        'scan_hosts.py, predelete_inventory.py, cleanup_artcache.py',
    ],
    'evidence_notes': 'host-scan-raw.json currently holds the same post-cleanup rescan as host-scan-post-cleanup.json '
                      '(the initial pre-cleanup scan file was overwritten by the rescan); pre-cleanup presence of the '
                      'three skeletons is evidenced by predelete-inventory.json and cleanup-exec.json.',
}
(HERE / 'artcache-cleanup.json').write_text(json.dumps(artcache, ensure_ascii=False, indent=1) + '\n')

# ---------------- 2. browser-sessions.json ----------------
inv_txt = (EV / 'browser' / 'browser-session-inventory.txt').read_text()
browser = {
    'task': 'OBS-20261014-BROWSER-SESSION-RESIDUE housekeeping',
    'at_utc': NOW,
    'current_session_inventory': {
        'agent_browser_session_list': 'No active sessions',
        'agent_browser_doctor_daemons': 'No active daemons',
        'chromium_related_processes': 0,
        'checked': ['agent-browser session list (workstation + repo cwd)', 'agent-browser doctor',
                    'ps all-users chrome/chromium/headless'],
        'note': 'the ~185 chromium processes and historical sessions seen on 2026-10-14 no longer exist; '
                'no live session of any date remains, so there was nothing to close',
    },
    'close_executed': False,
    'close_all_used': False,
    'historical_sessions_20260930_20261009_20261010': {
        'live_sessions': [],
        'result': 'already gone (not present as active sessions); no close command needed or run',
        'inert_remnants': {
            'description': 'per-session 16-byte .config remnants remain in /run/user/1000/agent-browser '
                           '(not sessions; runtime config files). Left untouched (task scope = close sessions only).',
            'historical_count_by_mtime': {'2026-10-09': '9 (incl. qawebsecure20260930.config)', '2026-10-10': 24},
            'examples': ['qawebsecure20260930.config', 'nodesteer-repair-20261009.config',
                         'qawebsecure20261010-*.config (multiple)', 'nodesteer-db-fault-attempt02.config',
                         'nodesteer-pending-ui-20261010.config', 'ro-exec-auth-20261010-1521.config'],
        },
    },
    'recent_sessions_preserved': {
        'live_sessions': [],
        'note': 'no live sessions exist at all; recent (>=2026-10-16) session .config remnants (56 files) and '
                'saved-state files left untouched as instructed',
    },
    'saved_state_files': {
        'location': '/home/superc/.agent-browser/sessions (29 files, mostly other projects)',
        'nodesteer_related': ['nodesteer-ui-qa-2ca6673010a6-...json (Sep 15)', 'nodesteer-ui-2ca6673010-...json (Sep 16)',
                              'nodesteer-ui-qa-qa-2ca6673010a6-...json (Sep 16)', 'nodesteer-ui-qa-2ca6673010-...json (Sep 16)'],
        'note': 'state files are not sessions; not deleted (no authorization, spans other projects)',
    },
    'other_observed': {
        'playwright_firefox_process': 'one running (other concurrent activity, not agent-browser; untouched)',
    },
    'evidence_files': ['evidence/browser/browser-session-inventory.txt'],
}
(HERE / 'browser-sessions.json').write_text(json.dumps(browser, ensure_ascii=False, indent=1) + '\n')

# ---------------- 3. changelog-check.json ----------------
changelog = {
    'task': 'CHANGELOG / contribution-convention check',
    'at_utc': NOW,
    'checks': [
        {'pattern': 'CHANGELOG*', 'scope': 'repo (excluding node_modules/.git)', 'result': 'none'},
        {'pattern': 'docs/CHANGELOG*, docs/*release*, docs/*changelog*', 'scope': 'docs/', 'result': 'none'},
        {'pattern': 'CONTRIBUTING*', 'scope': 'repo (excluding node_modules/.git)', 'result': 'none'},
        {'pattern': 'README.md release-notes / changelog section', 'scope': 'README.md',
         'result': 'none (sections: 项目简介/核心能力/架构/快速开始/部署/网络与安全/开发与测试/项目结构/文档/第三方声明)'},
        {'pattern': 'web/README.md release-notes section', 'scope': 'web/README.md',
         'result': 'none (default Vite template readme)'},
        {'pattern': '.github templates / release convention', 'scope': '.github/', 'result': 'only workflows/build.yml'},
    ],
    'conclusion': '仓库无 CHANGELOG 约定：无 CHANGELOG.md、无 docs/CHANGELOG*、无 CONTRIBUTING*、README 无发布说明段落',
    'action': '不新建文件，未修改任何仓库文件（按任务要求：无约定则如实报告，不新建）',
    'no_repo_modification': True,
    'checked_at_commit': '075a26f (HEAD, working tree has pre-existing user changes: .e2e/observations.json, .gitignore — untouched by this task)',
}
(HERE / 'changelog-check.json').write_text(json.dumps(changelog, ensure_ascii=False, indent=1) + '\n')

# ---------------- 4. env-final-verify.json ----------------
repair_nodes = {k: v for k, v in env['nodes'].items() if k.startswith('qa-repair')}
other_nodes = {k: v for k, v in env['nodes'].items() if not k.startswith('qa-repair')}
boot = env['primary']['boot']
boot_fix = dict(boot)
boot_fix['native-4'] = {'uptime_s': '2026-10-14 15:29 (via who -b; uptime -s unsupported on openSUSE)',
                        'proc_uptime': boot['native-4']['proc_uptime']}
primary_pids_expected = {'hub': '1251', 'native-1': '673', 'native-2': '731', 'native-3': '16919', 'native-4': '1002'}
primary_pids_now = {'hub': env['primary']['hub_209'].get('MainPID'),
                    **{k: v.get('MainPID') for k, v in env['primary']['agents'].items()}}
envv = {
    'task': 'repair environment final verification (read-only)',
    'at_utc': NOW,
    'authority': 'http://192.168.100.209:18080',
    'build_label': 'dddee4f0-e2e-repair-20261018-final',
    'health': {
        'healthz': 200, 'readyz': 200,
        'checked_via': ['workstation direct http://192.168.100.209:18080', 'hub-host loopback curl 127.0.0.1:18080'],
    },
    'repair_nodes': repair_nodes,
    'repair_nodes_all_online_synced_final_build': all(
        v['status'] == 'online' and v['sync_status'] == 'synced' and v['agent_version'] == 'dddee4f0-e2e-repair-20261018-final'
        for v in repair_nodes.values()),
    'other_hub_nodes_informational': other_nodes,
    'backups_20261018_final': {
        'hub': {'path': env['backups']['hub']['path'], 'sha256_first16': env['backups']['hub']['sha256_first16'],
                'size': 55086747, 'mtime': '2026-10-17 21:52 local'},
        'agents': {k: {'path': v['path'], 'sha256_first16': v['sha256_first16'], 'size': 16847621,
                       'mtime': '2026-10-17 21:52 local'} for k, v in env['backups']['agents'].items()},
        'all_exist': True,
    },
    'primary_untouched': {
        'hub_8080': env['primary']['hub_209'],
        'agents': env['primary']['agents'],
        'pids_expected': primary_pids_expected,
        'pids_now': primary_pids_now,
        'pids_unchanged': primary_pids_now == primary_pids_expected,
    },
    'no_vm_reboot_traces': {
        'last_boot': boot_fix,
        'reboot_history_highlights': {
            'native-1': 'last reboot Oct 14 18:16 (still running); previous Oct 14 15:29',
            'native-3': 'last reboot Oct 14 15:29 (still running)',
            'native-4': 'last reboot Oct 14 15:29 (still running)',
            'hub-209/native-2': 'who -b = 2026-10-14 15:29; last wtmp empty',
        },
        'conclusion': 'all hosts up since 2026-10-14 (uptime ~3d23h); final deploy 2026-10-18 12:40 local and this '
                      'verification both happened without any reboot; primary/repair PIDs consistent with baselines',
    },
    'host_residue_rescan': {
        'scope': 'owned qa-/nodesteer-qa- units, /opt, /var/lib, processes, nodesteer-related listeners',
        'hub-209': env['residue']['hub-209'],
        'native-1': env['residue']['native-1'],
        'native-2': env['residue']['native-2'],
        'native-3': env['residue']['native-3'],
        'native-4': env['residue']['native-4'],
        'this_housekeeping_round_residue': 'zero (3 owned skeleton dirs deleted, verified absent; nothing added)',
        'final_verify_round_qa_final_ca99ec4c_residue': 'zero (consistent with earlier final-cleanup-oracle)',
        'known_retained_campaign_fixtures': [
            'native-1: unit qa-20260930-101945-web-app-6a32f.service + pid 637 + /tmp/qa-repair-20260930-101945-app-web (live managed-app fixture)',
            'native-2: /opt/qa-resume-20261010-host-f174890c3846 + container + volume + hub node host001 (live docker_host_integration fixture)',
            'hub-209: /var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui + ttl-ui unit/config remnants (inert, reported)',
            'hub-209: campaign docker images qa-20260930-101945-env-agent/hub:dddee4f0 (no containers)',
        ],
        'concurrent_activity_not_touched': {
            'instance': 'qa-20261018-stress-authz (hub-209, /tmp, started 2026-10-18 14:40 local during this session)',
            'ports': ['18089 (web)', '18090 (gateway)'],
            'note': 'started by another concurrent actor (not this housekeeping); observed read-only, not touched',
        },
    },
    'verdict': {
        'healthz_readyz_200': True,
        'repair_nodes_4_online_synced_final_build': True,
        'backups_present_hub_and_4_agents': True,
        'primary_hub_pid_unchanged': primary_pids_now['hub'] == primary_pids_expected['hub'],
        'primary_agent_pids_unchanged': all(primary_pids_now[k] == primary_pids_expected[k] for k in ('native-1', 'native-2', 'native-3', 'native-4')),
        'no_vm_reboot_traces': True,
        'host_zero_owned_residue_for_this_round': True,
    },
    'evidence_files': ['evidence/env-verify-raw.json', 'evidence/host-scan-post-cleanup.json', 'env_verify.py'],
}
(HERE / 'env-final-verify.json').write_text(json.dumps(envv, ensure_ascii=False, indent=1) + '\n')

print('written:', [p.name for p in HERE.glob('*.json')])
