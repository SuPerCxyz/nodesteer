import hashlib
import json
import os
import re
import time


def inspect(pid):
    out = {'pid': pid}
    try:
        raw_stat = open(f'/proc/{pid}/stat').read()
        rest = raw_stat[raw_stat.rfind(')') + 2:].split()
        out['ppid'] = int(rest[1])
        out['starttime_ticks'] = int(rest[19])
        status = open(f'/proc/{pid}/status').read()
        uid = re.search(r'^Uid:\s+(\d+)', status, re.M)
        out['uid'] = int(uid.group(1)) if uid else None
        raw_argv = open(f'/proc/{pid}/cmdline', 'rb').read()
        out['argv_sha256'] = hashlib.sha256(raw_argv).hexdigest()
        argv = [x.decode(errors='replace') for x in raw_argv.split(b'\0') if x]
        out['argv0'] = argv[0] if argv else ''
        config_path = None
        for i, arg in enumerate(argv[:-1]):
            if arg == '--config':
                config_path = argv[i + 1]
                break
            if arg.startswith('--config='):
                config_path = arg.split('=', 1)[1]
                break
        if config_path and re.search(r'(token|password|secret|credential|key)', config_path, re.I):
            config_path = '[redacted]'
        out['config_path'] = config_path
        out['exe_path'] = os.readlink(f'/proc/{pid}/exe')
        h = hashlib.sha256()
        with open(f'/proc/{pid}/exe', 'rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
        out['exe_sha256'] = h.hexdigest()
        out['cgroup'] = open(f'/proc/{pid}/cgroup').read().strip().splitlines()
    except Exception as exc:
        out['inspection_error'] = type(exc).__name__
    return out


pid = os.getpid()
parent = os.getppid()
chain = []
seen = set()
while parent > 0 and parent not in seen and len(chain) < 20:
    seen.add(parent)
    entry = inspect(parent)
    chain.append(entry)
    parent = entry.get('ppid', 0)

print(json.dumps({
    'captured_unix_time': int(time.time()),
    'probe_pid': pid,
    'os_getppid': os.getppid(),
    'parent_chain': chain,
}, sort_keys=True))
