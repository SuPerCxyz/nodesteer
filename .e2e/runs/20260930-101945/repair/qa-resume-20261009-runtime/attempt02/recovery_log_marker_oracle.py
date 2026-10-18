import json,pathlib,re,time
p=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/log/agent.log');deployment='89297017-0032-472f-a1a1-d1e9cac58a67';patterns={'recovering_incomplete_deployment':'recovering incomplete deployment','preparing_deployment_aborted':'preparing deployment aborted; files preserved, recovery requires inspection','restore_preparing_service_failed':'restore preparing service failed'}
if not p.is_file():raise SystemExit('owned child Agent log missing')
with p.open('rb') as f:
 f.seek(max(0,p.stat().st_size-4*1024*1024));tail=f.read().decode(errors='replace')
lines=tail.splitlines();events=[]
for i,line in enumerate(lines):
 if deployment not in line:continue
 for key,needle in patterns.items():
  if needle in line:
   stamp=(line[:40] if re.match(r'^(\d{4}-\d\d-\d\d[T ]\S+)',line) else '')
   events.append({'event':key,'deployment_id_match':True,'timestamp_prefix':stamp,'line_sha256':__import__('hashlib').sha256(line.encode()).hexdigest(),'contains_expected_phase':('PREPARING' in line or 'phase=PREPARING' in line or 'phase PREPARING' in line) if key=='recovering_incomplete_deployment' else None})
print(json.dumps({'oracle':'FILTERED_OWNED_AGENT_RECOVERY_LOG_MARKERS','path':str(p),'tail_bytes_scanned':len(tail.encode()),'deployment_id':deployment,'events':events,'captured_unix':int(time.time())},sort_keys=True),flush=True)
