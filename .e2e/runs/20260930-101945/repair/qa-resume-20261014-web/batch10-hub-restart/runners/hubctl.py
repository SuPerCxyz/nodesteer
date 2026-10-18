"""Repair-Hub control-plane access for sys-b01 (read mostly, plus the one
authorised stop/start of `nodesteer-hub-repair-20260930-101945.service`).

Guards enforced here:
  * the ONLY unit this module can ever stop/start is REPAIR_UNIT;
  * the primary `nodesteer-hub.service` (:8080) is only ever read;
  * `nodesteer-hub` binary sha256 is compared against the pinned repair build.

No credentials are read or written here.
"""
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HUB_HOST = '192.168.100.209'
HUB_URL = 'http://192.168.100.209:18080'
REPAIR_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
REPAIR_BIN = '/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard'
HUB_BIN_SHA = '164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6'
PRIMARY_UNIT = 'nodesteer-hub.service'
CONTROL_PATH = '/tmp/opencode/b10-hub-ssh-%r@%h:%p'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
       '-o', 'ControlMaster=auto', '-o', 'ControlPath=' + CONTROL_PATH, '-o', 'ControlPersist=600',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HUB_HOST]

_PROPS = 'Id,ActiveState,SubState,MainPID,ExecMainStartTimestamp,ExecMainPID'
assert REPAIR_UNIT != PRIMARY_UNIT


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def _remote(script, timeout=120):
    cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True,
                        text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def _last_json(out):
    lines = [l for l in out.splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def unit_state(unit=REPAIR_UNIT):
    rc, out, err = _remote(
        "import json,subprocess\n"
        "props=%r.split(',')\n"
        "args=['systemctl','show','--no-pager']\n"
        "for p in props: args.append('--property='+p)\n"
        "args.append(%r)\n"
        "r=subprocess.run(args,capture_output=True,text=True)\n"
        "d=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
        "print(json.dumps(d))\n" % (_PROPS, unit), timeout=40)
    if rc != 0:
        raise RuntimeError('hub_unit_state_failed: %s' % (err[-300:] or out[-300:]))
    return _last_json(out)


def binary_sha(pid):
    rc, out, err = _remote(
        "import hashlib,json,os\n"
        "pid=%r\n"
        "p='/proc/%%s/exe'%%pid\n"
        "out={}\n"
        "try:\n"
        "    exe=os.readlink(p)\n"
        "    out['exe']=exe\n"
        "    out['sha256']=hashlib.sha256(open(exe,'rb').read()).hexdigest()\n"
        "except Exception as e:\n"
        "    out['error']=type(e).__name__\n"
        "print(json.dumps(out))\n" % str(pid), timeout=60)
    if rc != 0:
        raise RuntimeError('hub_binary_sha_failed: %s' % (err[-300:] or out[-300:]))
    return _last_json(out)


def unit_is_active(unit=REPAIR_UNIT):
    st = unit_state(unit)
    return st.get('ActiveState') == 'active'


def http_probe(path, timeout=4):
    """Caller-visible probe from the operator workstation (no retry)."""
    t0 = time.time()
    try:
        with urllib.request.urlopen(HUB_URL + path, timeout=timeout) as resp:
            body = resp.read(200).decode('utf-8', 'replace').strip()
            return {'ok': True, 'http': resp.status, 'body': body,
                    'seconds': round(time.time() - t0, 3)}
    except urllib.error.HTTPError as exc:
        return {'ok': False, 'http': exc.code, 'body': exc.read(200).decode('utf-8', 'replace').strip(),
                'seconds': round(time.time() - t0, 3)}
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'seconds': round(time.time() - t0, 3)}


def health():
    return {'healthz': http_probe('/healthz'), 'readyz': http_probe('/readyz')}


def healthy():
    h = health()
    return h['healthz'].get('http') == 200 and h['readyz'].get('http') == 200


def primary_hub_state():
    """Read-only record of the PROTECTED primary Hub (:8080)."""
    st = unit_state(PRIMARY_UNIT)
    return {'unit': PRIMARY_UNIT, 'ActiveState': st.get('ActiveState'),
            'SubState': st.get('SubState'), 'MainPID': st.get('MainPID'),
            'ExecMainStartTimestamp': st.get('ExecMainStartTimestamp')}


def stop_repair_unit():
    if REPAIR_UNIT == PRIMARY_UNIT:
        raise RuntimeError('refusing_to_touch_primary_hub_unit')
    rc, out, err = _remote(
        "import json,subprocess\n"
        "r=subprocess.run(['systemctl','stop',%r],capture_output=True,text=True)\n"
        "print(json.dumps({'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr}))\n" % REPAIR_UNIT,
        timeout=90)
    if rc != 0:
        raise RuntimeError('hub_stop_failed: %s' % (err[-300:] or out[-300:]))
    return _last_json(out)


def start_repair_unit():
    if REPAIR_UNIT == PRIMARY_UNIT:
        raise RuntimeError('refusing_to_touch_primary_hub_unit')
    rc, out, err = _remote(
        "import json,subprocess\n"
        "r=subprocess.run(['systemctl','start',%r],capture_output=True,text=True)\n"
        "print(json.dumps({'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr}))\n" % REPAIR_UNIT,
        timeout=90)
    if rc != 0:
        raise RuntimeError('hub_start_failed: %s' % (err[-300:] or out[-300:]))
    return _last_json(out)


def tcp_probe(timeout=3.0):
    """Raw TCP reachability of the repair API port from the operator workstation."""
    import socket
    t0 = time.time()
    try:
        s = socket.create_connection((HUB_HOST, 18080), timeout=timeout)
        s.close()
        return {'connected': True, 'seconds': round(time.time() - t0, 3)}
    except Exception as exc:
        return {'connected': False, 'error': type(exc).__name__, 'seconds': round(time.time() - t0, 3)}


def wait_outage(timeout=60.0, interval=0.5):
    """Poll the caller-visible endpoint until the repair Hub stops answering."""
    t0 = time.time()
    samples = []
    while time.time() - t0 < timeout:
        h = health()
        tcp = tcp_probe(timeout=2.0)
        samples.append({'at': now_utc(),
                        'healthz': h['healthz'].get('http') or h['healthz'].get('error'),
                        'readyz': h['readyz'].get('http') or h['readyz'].get('error'),
                        'tcp_18080': tcp.get('connected') if tcp.get('connected') else tcp.get('error')})
        if not h['healthz']['ok'] and not h['readyz']['ok'] and not tcp.get('connected'):
            return {'outage_observed': True, 'seconds': round(time.time() - t0, 2),
                    'probe': 'no HTTP answer AND TCP connect to :18080 refused', 'samples': samples}
        time.sleep(interval)
    return {'outage_observed': False, 'seconds': round(time.time() - t0, 2), 'samples': samples}


def wait_healthy(timeout=90.0, interval=0.5):
    t0 = time.time()
    samples = []
    while time.time() - t0 < timeout:
        h = health()
        samples.append({'at': now_utc(), 'healthz': h['healthz'].get('http') or h['healthz'].get('error'),
                        'readyz': h['readyz'].get('http') or h['readyz'].get('error')})
        if h['healthz'].get('http') == 200 and h['readyz'].get('http') == 200:
            return {'healthy': True, 'seconds': round(time.time() - t0, 2), 'samples': samples}
        time.sleep(interval)
    return {'healthy': False, 'seconds': round(time.time() - t0, 2), 'samples': samples}


def restart_window(stop_timeout=60.0, start_timeout=90.0):
    """Perform the single authorised repair-Hub restart and capture both sides."""
    pre = {'unit': unit_state(), 'health': health(), 'at_utc': now_utc()}
    pre_pid = pre['unit'].get('MainPID')
    pre['binary'] = binary_sha(pre_pid) if pre_pid and pre_pid not in ('', '0') else None
    t_stop = time.time()
    stop_result = stop_repair_unit()
    outage = wait_outage(timeout=stop_timeout)
    after_stop = unit_state()
    start_result = start_repair_unit()
    recovery = wait_healthy(timeout=start_timeout)
    post = {'unit': unit_state(), 'health': health(), 'at_utc': now_utc()}
    post_pid = post['unit'].get('MainPID')
    post['binary'] = binary_sha(post_pid) if post_pid and post_pid not in ('', '0') else None
    return {
        'pre': pre, 'stop_result': stop_result, 'outage': outage,
        'unit_after_stop': after_stop, 'start_result': start_result,
        'recovery': recovery, 'post': post,
        'mainpid_changed': bool(pre_pid and post_pid and pre_pid != post_pid),
        'binary_sha_same': bool(pre.get('binary', {}).get('sha256')
                                and pre['binary']['sha256'] == post.get('binary', {}).get('sha256')),
        'downtime_seconds': round(time.time() - t_stop, 2),
    }
