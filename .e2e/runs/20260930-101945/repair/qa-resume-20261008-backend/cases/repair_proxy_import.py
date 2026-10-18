#!/usr/bin/env python3
"""精确修补本例私密proxy的stdlib import并重启owned进程。"""
import ast
import hashlib
import json
from pathlib import Path
import backend_cases as h
import recover_proxy_observer as recovery

SOURCE = recovery.SOURCE
SOURCE = SOURCE.replace('475c97667c8475ddb6dc4cc8d24a1f225af47e8af949de17fa8354dafc76b940', '29908ca7cb3a72beb958649a0800ef261aee81c9a4868a5dc11e1bc962c19817')
SOURCE = SOURCE.replace('41195191953e7c0a95a0951d2991fb9459414c99a69a868c540a6febc6882537', 'd9ac209a20ddda8a6101e80a9d42ddaa9be49af7892f403a98ef6e3b57c0cd57')
SOURCE = SOURCE.replace('import hashlib,json,os,pwd,stat,subprocess,time', 'import hashlib,json,os,pwd,signal,stat,subprocess,time')
anchor = '''if sha(owned/"nodesteer-agent")!=binary_sha or sha(owned/"ws_proxy.py")!=patched_proxy: raise RuntimeError("recovery_binary_or_proxy_sha_guard_failed")'''
replacement = anchor + '''\nproxy=owned/"ws_proxy.py"\nsource=proxy.read_text(); import_anchor="import argparse\\n"\nif "import hashlib\\n" not in source:\n if source.count(import_anchor)!=1: raise RuntimeError("proxy_import_patch_anchor_mismatch")\n source=source.replace(import_anchor,import_anchor+"import hashlib\\n",1)\npatched_proxy=hashlib.sha256(source.encode()).hexdigest()\nif patched_proxy!=sha(proxy):\n proxy.write_text(source); os.chmod(proxy,0o400); os.chown(proxy,65534,65534)'''
if SOURCE.count(anchor) != 1:
    raise RuntimeError('recovery_source_anchor_mismatch')
SOURCE = SOURCE.replace(anchor, replacement)
live_guard = '''if not gone(int(owner["child"]["pid"])) or not gone(int(owner["supervisor"]["pid"])): raise RuntimeError("unexpected_owned_process_alive_recovery_aborted")'''
safe_stop = '''def matches(row):
 pid=int(row["pid"]); return start(pid)==int(row["starttime"]) and uid(pid)==int(row["uid"])==65534 and sha("/proc/"+str(pid)+"/exe")==row["exe_sha256"] and any(a==str(owned) or a.startswith(str(owned)+os.sep) for a in argv(pid))
def stop_owned(row):
 pid=int(row["pid"])
 if not matches(row): raise RuntimeError("owned_process_identity_guard_failed_no_signal")
 os.kill(pid,signal.SIGTERM); end=time.monotonic()+6
 while time.monotonic()<end:
  try:
   if start(pid)!=int(row["starttime"]): return
  except OSError: return
  time.sleep(.1)
 if matches(row): os.kill(pid,signal.SIGKILL)
 end=time.monotonic()+3
 while time.monotonic()<end:
  try:
   if start(pid)!=int(row["starttime"]): return
  except OSError: return
  time.sleep(.1)
 raise RuntimeError("owned_process_stop_timeout")
stop_owned(owner["child"]); stop_owned(owner["supervisor"])'''
if SOURCE.count(live_guard) != 1:
    raise RuntimeError('owned_process_guard_anchor_mismatch')
SOURCE = SOURCE.replace(live_guard, safe_stop)
good_argv = '[owner["supervisor"]["exe_path"],str(supervisor_path),str(owned),owner["proxy"]["bind_ip"],"192.168.100.209","18080"'
if SOURCE.count(good_argv) != 1:
    raise RuntimeError('supervisor_argv_guard_mismatch')
ast.parse(SOURCE)


def main():
    result = h.create_native4_command('BE-SCRIPT-SHA-002-repair-proxy-import', SOURCE, timeout=45)
    if result.get('status') != 'SUCCESS' or not isinstance(result.get('output'), dict) or result['output'].get('status') != 'PASS':
        raise RuntimeError('proxy_import_repair_failed')
    output = result['output']
    h.atomic(h.HERE / 'owned-process-ledger.json', output)
    h.checkpoint('BE-SCRIPT-SHA-002', 'proxy-import-repaired', proxy_source_sha256=output['proxy_source_sha256'], proxy_port=output['proxy_port'], child=output['child'], supervisor=output['supervisor'], owner_manifest_sha256=output['owner_manifest_sha256'])
    print(json.dumps({'status': 'PASS', 'proxy_source_sha256': output['proxy_source_sha256'], 'proxy_port': output['proxy_port'], 'manifest_sha256': output['owner_manifest_sha256'], 'remote_source_sha256': hashlib.sha256(SOURCE.encode()).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
