"""Current repair/Native2 and exact Docker/Host ownership preflight."""
import hashlib, json, os, pathlib, shlex, stat, subprocess, time
import api

HERE = pathlib.Path(__file__).resolve().parent
PLAN = json.loads((HERE / "host-fixture-plan-proposal.json").read_text())
REVIEW = json.loads((HERE.parent / "qa-resume-20261010-root" / "host-plan-review.json").read_text())
RUN = PLAN["identifiers"]["run"]
NODE = PLAN["target_native2_node_id"]
EXPECTED_SHA = PLAN["preflight"]["repair_agent_sha256"]
IMAGE = PLAN["identifiers"]["image_tag"]
BRIDGE = PLAN["identifiers"]["bridge"]
CONTAINERS = PLAN["identifiers"]["containers"]
VOLUMES = PLAN["identifiers"]["volumes"]
ROOT = PLAN["identifiers"]["root"]

def api_readiness():
    out = {}
    for name, path in (("healthz", "/healthz"), ("readyz", "/readyz"), ("me", "/api/me")):
        status, raw = api.request("GET", path)
        out[name + "_http"] = status
        if status != 200:
            raise RuntimeError("repair_readiness_failed_" + name + "_" + str(status))
        if name == "me":
            obj = json.loads(raw)
            out["me_role"] = obj.get("role") or obj.get("Role")
    if REVIEW.get("proposal_sha256") != hashlib.sha256((HERE / "host-fixture-plan-proposal.json").read_bytes()).hexdigest():
        raise RuntimeError("reviewed_proposal_sha_mismatch")
    if REVIEW.get("status") != "APPROVED_WITH_SPECIFIC_CORRECTIONS" or RUN != "qa-resume-20261010-host-f174890c3846":
        raise RuntimeError("review_scope_or_nonce_mismatch")
    return out

def build_command():
    source = r'''import hashlib,json,os,shutil,stat,subprocess
from pathlib import Path
NODE=%r; EXPECTED=%r; ROOT=%r; IMAGE=%r; BRIDGE=%r; CONTAINERS=%r; VOLUMES=%r; SOURCES=%r

def run(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=20)
 return {'rc':p.returncode,'out':p.stdout.strip(),'err':p.stderr.strip()[:160]}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 d=Path('/proc')/str(pid);raw=(d/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(d/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(d/'exe');argv=[x.decode(errors='replace') for x in (d/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'starttime':start,'uid':uid,'argv':argv,'exe':exe,'exe_sha256':sha(exe),'cgroup':(d/'cgroup').read_text().strip()}
agent='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';matches=[]
for e in Path('/proc').iterdir():
 if not e.name.isdigit():continue
 try:
  if os.readlink(e/'exe')==agent:matches.append(proc(int(e.name)))
 except (FileNotFoundError,PermissionError,ProcessLookupError):pass
if len(matches)!=1 or matches[0]['exe_sha256']!=EXPECTED or matches[0]['uid']!=0:raise RuntimeError('native2_agent_identity_guard_failed')
unit='nodesteer-agent-repair-20260930-101945-native-2.service';props=run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath'])
if props['rc']!=0:raise RuntimeError('native2_unit_show_failed')
kv={}
for line in props['out'].splitlines():
 if '=' in line:
  k,v=line.split('=',1);kv[k]=v
if int(kv.get('MainPID','0'))!=matches[0]['pid'] or kv.get('ActiveState')!='active':raise RuntimeError('native2_unit_owner_mismatch')
# Read-only recursive metadata fingerprint: no unit file contents emitted.
def tree(root):
 root=Path(root);out=[]
 for parent,dirs,files in os.walk(root,followlinks=False):
  dirs.sort();files.sort()
  for name in dirs+files:
   p=Path(parent)/name;st=p.lstat();row={'path':str(p.relative_to(root)),'mode':oct(stat.S_IMODE(st.st_mode)),'uid':st.st_uid,'gid':st.st_gid,'dev':st.st_dev,'ino':st.st_ino,'type':'dir' if stat.S_ISDIR(st.st_mode) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else 'other'}
   if stat.S_ISREG(st.st_mode):row.update({'size':st.st_size,'sha256':sha(p)})
   elif stat.S_ISLNK(st.st_mode):row['sha256']=hashlib.sha256(os.readlink(p).encode()).hexdigest()
   out.append(row)
 return out
sd=Path('/etc/systemd/system');systemd=tree(sd)
for x in SOURCES:
 p=Path(x['source_on_native2'])
 if not p.is_file() or p.resolve()!=p or sha(p)!=x['sha256'] or p.stat().st_size!=x['size_bytes']:raise RuntimeError('runtime_source_sha_guard_failed')
if os.path.lexists(ROOT):raise RuntimeError('owned_root_already_exists')
# Check exact resource identifiers are absent; do not mutate Docker.
docker=shutil.which('docker')
if not docker:raise RuntimeError('docker_missing')
info=run([docker,'info','--format','{{.ServerVersion}}|{{.DockerRootDir}}|{{.Driver}}|{{json .SecurityOptions}}'])
if info['rc']!=0:raise RuntimeError('docker_info_failed')
images=run([docker,'image','ls','--no-trunc','--format','{{.Repository}}:{{.Tag}} {{.ID}}']);containers=run([docker,'ps','-a','--no-trunc','--format','{{.Names}} {{.ID}} {{.Label "nodesteer-e2e-run"}}']);networks=run([docker,'network','ls','--no-trunc','--format','{{.Name}} {{.ID}}']);volumes=run([docker,'volume','ls','--format','{{.Name}} {{.Driver}}'])
if any(x in images['out'] for x in [IMAGE]) or any(x in containers['out'] for x in CONTAINERS) or any(x in networks['out'].splitlines() for x in [BRIDGE]) or any(x.split()[0] in VOLUMES for x in volumes['out'].splitlines() if x.split()):raise RuntimeError('exact_owned_docker_resource_collision')
print(json.dumps({'status':'PASS','native2_agent':matches[0],'native2_systemd_unit':kv,'docker_info':info['out'],'exact_image_tag_absent':IMAGE not in images['out'],'exact_container_names_absent':all(n not in containers['out'] for n in CONTAINERS),'exact_network_absent':BRIDGE not in networks['out'].splitlines(),'exact_volumes_absent':not any(x.split() and x.split()[0] in VOLUMES for x in volumes['out'].splitlines()),'root_absent':True,'systemd_tree_metadata':systemd},separators=(',',':')))
''' % (NODE, EXPECTED_SHA, ROOT, IMAGE, BRIDGE, CONTAINERS, VOLUMES, PLAN["rootfs"]["files"])
    command = "python3 - <<'PY'\n" + source.rstrip() + "\nPY"
    api.validate_command(command, "host-native2-execution-preflight")
    return command

def main():
    ready = api_readiness()
    original = json.loads((HERE.parent / "qa-resume-20261009-backend" / "preflight.json").read_text())["original_repair_nodes"]
    nodes = []
    for n in original:
        obj = api.get_json("/api/nodes/" + n["node_id"])
        nodes.append({"node_id": n["node_id"], "status": obj.get("status"), "sync_status": obj.get("sync_status")})
    if len(nodes) != 4 or any(n["status"] != "online" or n["sync_status"] != "synced" for n in nodes):
        raise RuntimeError("original_repair_nodes_not_healthy")
    command = build_command()
    task = api.create_task("qa-resume-20261010-host-tls-native2-execution-preflight-f174890c3846", command, NODE)
    try:
        result = api.run_task(task)
    finally:
        cleanup = api.cleanup_task(task["id"], NODE)
    if result.get("status") != "SUCCESS":
        raise RuntimeError("native2_execution_preflight_task_failed")
    remote = json.loads(result.get("stdout") or "")
    if remote.get("status") != "PASS":
        raise RuntimeError("native2_execution_preflight_invalid")
    for n in original:
        obj = api.get_json("/api/nodes/" + n["node_id"])
        if obj.get("status") != "online" or obj.get("sync_status") != "synced":
            raise RuntimeError("original_repair_node_changed_during_preflight")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    api.write_json("host-execution-preflight.json", {"status":"PASS","actual_at":stamp,"repair_authority":"http://192.168.100.209:18080","api_readiness":ready,"original_four_nodes":nodes,"remote":remote,"task_id":task["id"],"execution_id":result["id"],"task_cleanup":cleanup})
    progress=json.loads((HERE/"progress.json").read_text());progress.update({"actual_at":stamp,"status":"HOST_EXECUTION_PREFLIGHT_PASS","stage":"host-live-owner-preflight-complete","current_operation":"Reconnected only to repair :18080; health/readiness/me and four original repair nodes verified. Fresh Native2 Task checked live Agent PID/starttime/UID/argv/exe SHA/cgroup, exact systemd Unit, all seven source hashes, exact Docker identifier absence, owned-root absence and systemd directory metadata.","next_action":"Create the exact approved private rootfs/image and inspect by tag/label/image ID; then perform read-only nsenter smoke before any Host-001 application write.","persisted_evidence":sorted(set(progress.get("persisted_evidence",[])+[str((HERE/"host-execution-preflight.json").resolve()),str((HERE/("task-cleanup-"+task["id"]+".json")).resolve())]))});api.write_json("progress.json",progress)
    print(json.dumps({"status":"PASS","at":stamp,"repair":ready,"original_nodes_healthy":True,"native2_agent_pid":remote["native2_agent"]["pid"],"agent_sha256":remote["native2_agent"]["exe_sha256"],"owned_root_absent":remote["root_absent"],"docker_ids_absent":remote["exact_image_tag_absent"] and remote["exact_container_names_absent"] and remote["exact_network_absent"] and remote["exact_volumes_absent"],"systemd_metadata_entries":len(remote["systemd_tree_metadata"]),"task_get404":cleanup["get_after_delete_http"]==404}))

if __name__ == "__main__": main()
