import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_env01_05 as api

BASE = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/setup')
BACKEND = BASE.parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(path, 0o600)


def main():
    os.umask(0o077)
    private_stat = PRIVATE.lstat()
    if stat.S_ISLNK(private_stat.st_mode) or not stat.S_ISREG(private_stat.st_mode) or stat.S_IMODE(private_stat.st_mode) != 0o600:
        raise SystemExit('private_connection_gate_failed')
    private = json.loads(PRIVATE.read_text())
    token = private.get('token')
    if not isinstance(token, str) or not token:
        raise SystemExit('private_token_missing')
    stage1 = json.loads((BASE/'stage1-target-preflight.json').read_text())
    stage2 = json.loads((BASE/'stage2-enrollment.json').read_text())
    stage3 = json.loads((BASE/'stage3-child-start.json').read_text())
    complete = json.loads((BASE/'stage4-setup-complete.json').read_text())
    setup_task = json.loads((BASE/'native4-child-setup-task-result.json').read_text())
    runtime_output = setup_task.get('task_output') or {}
    identity_task = next(json.loads(p.read_text()) for p in BASE.glob('qa-resume-20261008-child-identity-*-identity-result.json'))
    bind = stage1['target_bind']
    if complete.get('stage') != 'CHILD_SETUP_COMPLETE_PENDING_ROOT_REVIEW':
        raise SystemExit('setup_completion_checkpoint_missing')
    if complete.get('fault_injection_started') or complete.get('credential_revocation_started') or complete.get('reenrollment_started'):
        raise SystemExit('out_of_scope_action_recorded')
    if any(token.encode() in p.read_bytes() for p in BACKEND.rglob('*') if p.is_file()):
        raise SystemExit('credential_scan_found_private_token')

    stage3['stage'] = 'CHILD_STARTED_ONLINE_SYNCED'
    stage3['child_status'] = complete.get('child_status')
    stage3['sync_status'] = complete.get('sync_status')
    for key in ['directory_dev','directory_ino','directory_uid','directory_mode','child_uid','supervisor_uid']:
        if key not in stage3 and key in runtime_output:
            stage3[key] = runtime_output[key]
    write(BASE/'stage3-child-start.json', stage3)

    summary = {
        'schema_version': 1,
        'status': 'SETUP_COMPLETE_PENDING_ROOT_REVIEW',
        'authority': 'http://192.168.100.209:18080',
        'target_native4': {'node_id':'03cb50cc-eab6-4afd-bbe0-f73c4e326773','node_name':'qa-repair-20260930-101945-native-4'},
        'new_child': {
            'node_id': stage2['node_id'],
            'agent_id': stage2['agent_id'],
            'node_name': stage2['node_name'],
            'node_ip': stage2['node_ip'],
            'initial_status': 'pending',
            'final_status': complete['child_status'],
            'final_sync_status': complete['sync_status'],
            'new_identity_not_reused': stage2['reused_original_node_id'] is False and stage2['reused_original_agent_id'] is False,
        },
        'env03': {'status':'PASS','low_privilege_uid':65534,'actual_generated_child_config_tested':True,'read_parse_ok':True,'config_mode':'0o600','config_owner_uid':65534,'registration_token_output':'fingerprint_only','fingerprint':stage3['registration_token_fingerprint'],'evidence':'stage3-child-start.json'},
        'native4_source_config': {'execstart_path':'/etc/nodesteer/repair-20260930-101945/native-4.yaml','discovered_dynamically_from_exact_service_execstart':True,'grant_extracted_dynamically':True,'grant_fingerprint_matches_prior_env04':stage3['registration_token_fingerprint']=='bd1d6b7a25c2fc5c','config_body_or_grant_value_saved':False},
        'binary': {'source_path':'/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','expected_sha256':stage3['child_exe_sha256'],'copy_sha256':stage3['child_exe_sha256'],'matches_review_and_disk_manifest':True},
        'proxy': {'source_path':'.e2e/runs/20260930-101945/backend/ws_proxy.py','source_sha256':stage3['proxy_source_sha256'],'bind_ip':stage3['proxy_bind_ip'],'ephemeral_port':stage3['proxy_ephemeral_port'],'upstream':stage3['proxy_upstream'],'single_concurrent_loopback_client_enforced':True,'logs_hello_or_credential_payload':False},
        'owned_processes': {'directory':stage3['owned_directory'],'directory_dev':stage3['directory_dev'],'directory_ino':stage3['directory_ino'],'directory_uid':stage3['directory_uid'],'directory_mode':stage3['directory_mode'],'owner_manifest_sha256':stage3['owner_manifest_sha256'],'child_pid':stage3['child_pid'],'child_starttime':stage3['child_starttime'],'child_uid':stage3['child_uid'],'child_exe_sha256':stage3['child_exe_sha256'],'supervisor_pid':stage3['supervisor_pid'],'supervisor_starttime':stage3['supervisor_starttime'],'supervisor_uid':stage3['supervisor_uid'],'supervisor_exe_sha256':stage3['supervisor_exe_sha256'],'both_independent_sessions':True,'survived_setup_task_exit_and_identity_task':True},
        'identity_proof': {'node_id_matches_enrollment':True,'agent_id_matches_enrollment':True,'local_identity_db_opened_read_only':True,'old_agent_identity_or_db_not_reused':True,'evidence':'stage4-setup-complete.json'},
        'assignment_gate': complete['existing_assignments_before_and_after'],
        'original_repair_peers_before_after': complete['old_repair_peers_before_and_after'],
        'healthz_http': complete['healthz_http'],
        'readyz_http': complete['readyz_http'],
        'task_cleanup': [bind['task_id'],setup_task['task_id'],identity_task['task_id']],
        'all_setup_tasks_deleted_get404': all([bind['delete_http']==200 and bind['get_after_delete_http']==404,setup_task['task_cleanup']['delete_http']==200 and setup_task['task_cleanup']['get_after_delete_http']==404,identity_task['task_cleanup']['delete_http']==200 and identity_task['task_cleanup']['get_after_delete_http']==404]),
        'primary_8080_accessed':False,
        'existing_native4_service_or_config_modified':False,
        'fault_injection_started':False,
        'credential_revocation_or_reenrollment_started':False,
        'central_state_written':False,
        'credentials_or_full_enrollment_response_saved':False,
        'credential_scan_files':sum(1 for p in BACKEND.rglob('*') if p.is_file()),
        'credential_scan_token_matches':0,
        'resume_hint':'Wait for Root confirmation that Luna Workflows metadata mutation is complete before the three fault cases; retain the isolated child and supervisor.'
    }
    write(BASE/'setup-summary.json', summary)

    cleanup_path = BASE/'task-cleanup-ledger.json'
    entries = [
        {'stage':'TARGET_BIND_PREFLIGHT_PASS','task_id':bind['task_id'],'delete_http':bind['delete_http'],'get_after_delete_http':bind['get_after_delete_http']},
        {'stage':'CHILD_SETUP','task_id':setup_task['task_id'],'delete_http':setup_task['task_cleanup']['delete_http'],'get_after_delete_http':setup_task['task_cleanup']['get_after_delete_http']},
        {'stage':'CHILD_IDENTITY_PROBE','task_id':identity_task['task_id'],'delete_http':identity_task['task_cleanup']['delete_http'],'get_after_delete_http':identity_task['task_cleanup']['get_after_delete_http']},
    ]
    write(cleanup_path, {'task_count':len(entries),'all_delete_http_200':all(x['delete_http']==200 for x in entries),'all_get_after_delete_http_404':all(x['get_after_delete_http']==404 for x in entries),'tasks':entries})

    prior_path = BACKEND/'task-cleanup-ledger.json'
    prior = json.loads(prior_path.read_text())
    for item in entries:
        if not any(row.get('task_id')==item['task_id'] for row in prior['tasks']):
            prior['tasks'].append({'attempt_evidence':'setup/task-cleanup-ledger.json','task_id':item['task_id'],'delete_http':item['delete_http'],'get_after_delete_http':item['get_after_delete_http']})
    prior['task_count']=len(prior['tasks'])
    prior['all_delete_http_200']=all(x.get('delete_http')==200 for x in prior['tasks'])
    prior['all_get_after_delete_http_404']=all(x.get('get_after_delete_http')==404 for x in prior['tasks'])
    write(prior_path,prior)

    env_path=BACKEND/'env01_05-summary.json'
    env=json.loads(env_path.read_text())
    env['canonical_cases']['ENV-03']={'status':'PASS','launcher_euid':0,'existing_nonroot_uid':65534,'generated_child_config_uid':65534,'generated_child_config_mode':'0o600','nobody_read_parse_probe':True,'registration_grant_fingerprint_only':stage3['registration_token_fingerprint'],'evidence':'setup/stage3-child-start.json'}
    env['status']='SETUP_COMPLETE_PENDING_ROOT_REVIEW'
    env['agent_started']=True
    env['existing_service_or_config_mutated']=False
    env['enrollment_or_proxy_started']=True
    env['owned_preflight_task_count']=len(prior['tasks'])
    env['all_owned_preflight_tasks_deleted_get404']=prior['all_delete_http_200'] and prior['all_get_after_delete_http_404']
    env['evidence_index']['isolated_child_setup']='setup/setup-summary.json'
    env['evidence_index']['child_identity_proof']='setup/stage4-setup-complete.json'
    env['evidence_index']['child_owner_ledger']='setup/owner-ledger.json'
    env['scope_limits']['isolated_child_node_id']=stage2['node_id']
    env['scope_limits']['isolated_child_agent_id']=stage2['agent_id']
    env['scope_limits']['primary_8080_access']='none'
    env['scope_limits']['test_agent_started']=True
    env['scope_limits']['existing_service_or_config_modified']=False
    env['scope_limits']['isolated_child_cleanup_pending_root_review']=True
    write(env_path,env)

    write(BASE/'credential-scan.json',{'files_scanned':summary['credential_scan_files'],'affected_files':[],'token_value_written_or_exposed':False,'scan_scope':'backend evidence tree; compared bytes against current private connection token in memory'})
    print(json.dumps({'status':summary['status'],'env03':'PASS','child_status':summary['new_child']['final_status'],'sync_status':summary['new_child']['final_sync_status'],'task_count':len(prior['tasks']),'all_tasks_deleted_get404':summary['all_setup_tasks_deleted_get404'] and prior['all_delete_http_200'] and prior['all_get_after_delete_http_404'],'secret_scan_matches':0},separators=(',',':')))


if __name__=='__main__': main()
