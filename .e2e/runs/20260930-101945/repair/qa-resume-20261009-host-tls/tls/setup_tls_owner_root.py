#!/usr/bin/env python3
"""Create the unique TLS fixture root, keypairs, payloads, and stopped server Unit."""
import hashlib
import json
import time
from pathlib import Path

import api

HERE = Path(__file__).resolve().parent
PLAN = json.loads((HERE / "plan.json").read_text())
NODE_ID = PLAN["target_native2_node_id"]
ROOT = PLAN["remote_owned_root"]
NONCE = PLAN["nonce"]
EXPECTED_SHA = PLAN["expected_agent_sha256"]
SERVER_UNIT = PLAN["units"]["https_server"]
GOOD = (HERE / "application-payload.sh").read_bytes()
BAD = b"actual-tls-sha-mismatch-response-" + NONCE.encode() + b"\n"
EXPECTED_MISMATCH = b"expected-but-not-served-tls-payload-" + NONCE.encode() + b"\n"
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()
BAD_SHA = hashlib.sha256(BAD).hexdigest()
EXPECTED_MISMATCH_SHA = hashlib.sha256(EXPECTED_MISMATCH).hexdigest()

SERVER_SOURCE = r'''import hashlib,json,os,ssl,sys,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
R=Path(@@ROOT@@); N=@@NONCE@@; STATUS=R/"private"/"server-status.json"; LOG=R/"private"/"server-access.jsonl"
GOOD=R/"private"/"artifact.bin"; BAD=R/"private"/"sha-mismatch.bin"
srv=None; port=0
def write_status():
 tmp=STATUS.with_suffix(".tmp")
 tmp.write_text(json.dumps({"pid":os.getpid(),"port":port,"bind":"127.0.0.1","cert_sha256":hashlib.sha256((R/"private"/"server.crt").read_bytes()).hexdigest(),**srv.counts},separators=(",",":")))
 os.chmod(tmp,0o600); tmp.replace(STATUS)
def log_row(row):
 fd=os.open(LOG,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,"O_NOFOLLOW",0),0o600)
 with os.fdopen(fd,"a") as f:f.write(json.dumps(row,separators=(",",":"))+"\n")
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path=="/artifact/no-ca-"+N or self.path=="/artifact/wrong-ca-"+N or self.path=="/artifact/good-ca-"+N: p=GOOD; code=200
  elif self.path=="/sha-mismatch/"+N: p=BAD; code=200
  else: p=None; code=404
  b=p.read_bytes() if p else b"not found\n"
  self.send_response(code); self.send_header("Content-Type","application/octet-stream"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
  log_row({"at":time.time(),"kind":"http","path":self.path,"status":code,"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest()})
  srv.counts["http_requests"]+=1; write_status()
 def log_message(self,*args): return
class S(ThreadingHTTPServer):
 def get_request(self):
  try: return super().get_request()
  except ssl.SSLError as e:
   self.counts["tls_handshake_failures"]+=1
   log_row({"at":time.time(),"kind":"tls_handshake_failure","exception_type":type(e).__name__})
   write_status(); raise
 def handle_error(self,request,client_address):
  exc=sys.exc_info()[1]
  if isinstance(exc,ssl.SSLError):
   self.counts["tls_handshake_failures"]+=1
   log_row({"at":time.time(),"kind":"tls_handshake_failure","exception_type":type(exc).__name__})
   write_status()
srv=S(("127.0.0.1",0),H); srv.counts={"http_requests":0,"tls_handshake_failures":0}
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(str(R/"private"/"server.crt"),str(R/"private"/"server.key")); srv.socket=ctx.wrap_socket(srv.socket,server_side=True)
port=srv.server_address[1]
tmp=STATUS.with_suffix(".tmp"); tmp.write_text(json.dumps({"pid":os.getpid(),"port":port,"bind":"127.0.0.1","cert_sha256":hashlib.sha256((R/"private"/"server.crt").read_bytes()).hexdigest(),**srv.counts},separators=(",",":"))); os.chmod(tmp,0o600); tmp.replace(STATUS)
srv.serve_forever()
'''


def main():
    node = api.get_json("/api/nodes/" + NODE_ID)
    if node.get("id") != NODE_ID or node.get("status") != "online" or node.get("sync_status") != "synced":
        raise RuntimeError("native2_not_online_synced_before_owned_setup")
    replacements = {
        "@@ROOT@@": repr(ROOT),
        "@@NONCE@@": repr(NONCE),
        "@@EXPECTED_SHA@@": repr(EXPECTED_SHA),
        "@@SERVER_UNIT@@": repr(SERVER_UNIT),
        "@@NATIVE2_AGENT_PID@@": str(json.loads((HERE / "preflight-readonly.json").read_text())["probe"]["repair_agent_ancestor"]["pid"]),
        "@@NATIVE2_AGENT_START@@": str(json.loads((HERE / "preflight-readonly.json").read_text())["probe"]["repair_agent_ancestor"]["starttime"]),
        "@@GOOD@@": repr(GOOD),
        "@@BAD@@": repr(BAD),
        "@@SERVER_SOURCE@@": repr(SERVER_SOURCE.replace("@@ROOT@@", repr(ROOT)).replace("@@NONCE@@", repr(NONCE))),
    }
    source = r'''import hashlib,json,os,stat,subprocess,time,uuid
from pathlib import Path
ROOT=Path(@@ROOT@@); NONCE=@@NONCE@@; EXPECTED_SHA=@@EXPECTED_SHA@@; SERVER_UNIT=@@SERVER_UNIT@@; GOOD=@@GOOD@@; BAD=@@BAD@@
def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def run(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=30)
 if p.returncode:raise RuntimeError("command_failed:"+args[0]+":"+str(p.returncode))
 return p.stdout.strip()
if ROOT.exists() or ROOT.is_symlink():raise RuntimeError("owned_tls_root_must_be_absent")
if ROOT.parent.resolve()!=Path("/opt").resolve():raise RuntimeError("owned_tls_root_parent_gate")
unit=Path("/etc/systemd/system")/SERVER_UNIT
if unit.exists() or unit.is_symlink():raise RuntimeError("server_unit_path_must_be_absent")
load=run(["systemctl","show","--property=LoadState","--value",SERVER_UNIT])
if load!="not-found":raise RuntimeError("server_unit_already_loaded")
agent_pid=@@NATIVE2_AGENT_PID@@; agent_start=@@NATIVE2_AGENT_START@@; agent_sha=EXPECTED_SHA
statrow=Path("/proc",str(agent_pid),"stat").read_text(); start=int(statrow[statrow.rfind(")")+2:].split()[19])
exe=os.readlink("/proc/"+str(agent_pid)+"/exe")
if start!=agent_start or sha(exe)!=agent_sha or exe!="/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent":raise RuntimeError("original_native2_agent_owner_guard_failed")
os.umask(0o077); os.mkdir(ROOT,0o700); root_stat=ROOT.lstat()
if stat.S_IMODE(root_stat.st_mode)!=0o700 or root_stat.st_uid!=0 or ROOT.resolve()!=ROOT:raise RuntimeError("owned_tls_root_owner_mode_realpath_gate")
private=ROOT/"private"; appbin=ROOT/"app"/"bin"; appcfg=ROOT/"app"/"config"; data=ROOT/"agent-data"
for p in (private,appbin,appcfg,data):p.mkdir(mode=0o700,parents=True,exist_ok=False);os.chown(p,0,0);os.chmod(p,0o700)
marker=str(uuid.uuid4());(ROOT/"owner.marker").write_text(marker);os.chown(ROOT/"owner.marker",0,0);os.chmod(ROOT/"owner.marker",0o600)
ca_key=private/"ca.key";ca_crt=private/"ca.crt";wrong_key=private/"wrong-ca.key";wrong_crt=private/"wrong-ca.crt";server_key=private/"server.key";csr=private/"server.csr";server_crt=private/"server.crt";ext=private/"server.ext"
run(["/usr/bin/openssl","genpkey","-algorithm","RSA","-pkeyopt","rsa_keygen_bits:2048","-out",str(ca_key)]);os.chmod(ca_key,0o600)
run(["/usr/bin/openssl","req","-x509","-new","-key",str(ca_key),"-sha256","-days","2","-subj","/CN=qa-resume-"+NONCE+"-private-ca","-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,keyCertSign,cRLSign","-out",str(ca_crt)]);os.chmod(ca_crt,0o644)
run(["/usr/bin/openssl","genpkey","-algorithm","RSA","-pkeyopt","rsa_keygen_bits:2048","-out",str(wrong_key)]);os.chmod(wrong_key,0o600)
run(["/usr/bin/openssl","req","-x509","-new","-key",str(wrong_key),"-sha256","-days","2","-subj","/CN=qa-resume-"+NONCE+"-wrong-ca","-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,keyCertSign,cRLSign","-out",str(wrong_crt)]);os.chmod(wrong_crt,0o644)
run(["/usr/bin/openssl","genpkey","-algorithm","RSA","-pkeyopt","rsa_keygen_bits:2048","-out",str(server_key)]);os.chmod(server_key,0o600)
run(["/usr/bin/openssl","req","-new","-key",str(server_key),"-subj","/CN=127.0.0.1","-out",str(csr)])
ext.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n");os.chmod(ext,0o600)
run(["/usr/bin/openssl","x509","-req","-in",str(csr),"-CA",str(ca_crt),"-CAkey",str(ca_key),"-CAcreateserial","-days","2","-sha256","-extfile",str(ext),"-out",str(server_crt)]);os.chmod(server_crt,0o644)
run(["/usr/bin/openssl","verify","-CAfile",str(ca_crt),str(server_crt)])
san=run(["/usr/bin/openssl","x509","-in",str(server_crt),"-noout","-ext","subjectAltName"])
if "IP Address:127.0.0.1" not in san:raise RuntimeError("server_cert_loopback_san_missing")
(private/"artifact.bin").write_bytes(GOOD);os.chmod(private/"artifact.bin",0o644)
(private/"sha-mismatch.bin").write_bytes(BAD);os.chmod(private/"sha-mismatch.bin",0o644)
server_source=@@SERVER_SOURCE@@
script=private/"https_artifact_server.py";script.write_text(server_source);os.chmod(script,0o600)
unit_body=("[Unit]\nDescription=NodeSteer isolated TLS E2E server %s\nAfter=network.target\n\n[Service]\nType=simple\nUser=root\nGroup=root\nExecStart=/usr/bin/python3 %s\nRestart=on-failure\nRestartSec=1\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\nProtectSystem=strict\nProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\nRestrictSUIDSGID=yes\nCapabilityBoundingSet=\nReadWritePaths=%s\n\n[Install]\nWantedBy=multi-user.target\n")%(NONCE,script,ROOT)
unit.write_text(unit_body);os.chmod(unit,0o644);os.chown(unit,0,0)
manifest={"schema_version":1,"nonce":NONCE,"root":str(ROOT),"root_dev":root_stat.st_dev,"root_ino":root_stat.st_ino,"root_uid":root_stat.st_uid,"root_mode":oct(stat.S_IMODE(root_stat.st_mode)),"owner_marker":marker,"original_native2_agent":{"pid":agent_pid,"starttime":agent_start,"exe_sha256":agent_sha},"expected_repair_agent_sha256":EXPECTED_SHA,"units":{"https_server":{"name":SERVER_UNIT,"path":str(unit),"sha256":sha(unit),"pid":None}},"paths":{"ca_key":str(ca_key),"ca_cert":str(ca_crt),"wrong_ca_key":str(wrong_key),"wrong_ca_cert":str(wrong_crt),"server_key":str(server_key),"server_cert":str(server_crt),"server_script":str(script),"server_status":str(private/"server-status.json"),"server_log":str(private/"server-access.jsonl"),"data_dir":str(data),"artifact_good":str(private/"artifact.bin"),"artifact_bad":str(private/"sha-mismatch.bin")},"keys":{"ca_key_mode":oct(stat.S_IMODE(ca_key.stat().st_mode)),"wrong_ca_key_mode":oct(stat.S_IMODE(wrong_key.stat().st_mode)),"server_key_mode":oct(stat.S_IMODE(server_key.stat().st_mode)),"ca_key_uid":ca_key.stat().st_uid,"server_key_uid":server_key.stat().st_uid},"payload_sha256":sha(private/"artifact.bin"),"bad_response_sha256":sha(private/"sha-mismatch.bin"),"server_started":False,"created_at_epoch":time.time()}
mp=ROOT/"owner-manifest.json";mp.write_text(json.dumps(manifest,separators=(",",":")));os.chmod(mp,0o600);os.chown(mp,0,0)
cert=run(["/usr/bin/openssl","x509","-in",str(server_crt),"-noout","-subject","-issuer","-fingerprint","-sha256"])
out={"status":"PASS","root":str(ROOT),"root_identity":{"dev":root_stat.st_dev,"ino":root_stat.st_ino,"uid":root_stat.st_uid,"mode":oct(stat.S_IMODE(root_stat.st_mode))},"owner_marker_sha256":hashlib.sha256(marker.encode()).hexdigest(),"manifest_sha256":sha(mp),"manifest_uid":mp.stat().st_uid,"manifest_mode":oct(stat.S_IMODE(mp.stat().st_mode)),"original_native2_agent_guard":True,"server_unit":{"name":SERVER_UNIT,"file_sha256":sha(unit),"mode":oct(stat.S_IMODE(unit.stat().st_mode)),"uid":unit.stat().st_uid,"loaded":False,"started":False},"openssl":{"cert_summary":cert,"san":san,"ca_verify":True},"private_file_modes":{"ca_key":oct(stat.S_IMODE(ca_key.stat().st_mode)),"wrong_ca_key":oct(stat.S_IMODE(wrong_key.stat().st_mode)),"server_key":oct(stat.S_IMODE(server_key.stat().st_mode)),"server_script":oct(stat.S_IMODE(script.stat().st_mode))},"payload_sha256":sha(private/"artifact.bin"),"bad_response_sha256":sha(private/"sha-mismatch.bin"),"private_key_material_emitted":False,"server_not_started":True}
print(json.dumps(out,separators=(",",":")))
'''
    for token, value in replacements.items():
        source = source.replace(token, value)
    command="python3 - <<'PY'\n"+source.rstrip()+"\nPY"
    task_name="qa-resume-20261009-host-tls-owner-root-"+NONCE
    task=api.create_task(task_name,command,NODE_ID)
    result=None;cleanup=None
    try:result=api.run_task(task)
    finally:cleanup=api.cleanup_task(task["id"],NODE_ID)
    if result.get("status")!="SUCCESS":raise RuntimeError("tls_owner_root_setup_task_failed")
    try:out=json.loads(result.get("stdout") or "")
    except (ValueError,TypeError):raise RuntimeError("tls_owner_root_setup_output_invalid")
    if out.get("status")!="PASS" or out.get("root")!=ROOT or out.get("private_key_material_emitted") is not False or out.get("server_not_started") is not True:raise RuntimeError("tls_owner_root_setup_assertion_failed")
    api.write_json("owner-root-created.json",{"result":out,"task_id":task["id"],"execution_id":result["id"],"task_cleanup":cleanup,"payload_sha256_local":GOOD_SHA,"mismatch_expected_sha256_local":EXPECTED_MISMATCH_SHA,"bad_response_sha256_local":BAD_SHA,"source_sha256":hashlib.sha256(source.encode()).hexdigest()})
    print(json.dumps({"status":"PASS","nonce":NONCE,"root_dev":out["root_identity"]["dev"],"root_ino":out["root_identity"]["ino"],"manifest_sha256":out["manifest_sha256"],"server_unit":SERVER_UNIT,"openssl_san_verified":True,"server_not_started":True,"task_deleted_get404":cleanup.get("get_after_delete_http")==404}))


if __name__=="__main__":main()
