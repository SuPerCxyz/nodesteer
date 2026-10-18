import json
import re
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

SERVICE = 'nodesteer-agent-repair-20260930-101945-native-4.service'
EXPECTED_BINARY = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'


def fail(code):
    print(json.dumps({'status': 'BLOCKED', 'code': code}, separators=(',', ':')))
    raise SystemExit(0)


def systemd_value(prop):
    p = subprocess.run(
        ['systemctl', 'show', SERVICE, '-p', prop, '--value'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, text=True, timeout=10,
    )
    if p.returncode != 0:
        fail('systemd_read_failed')
    return p.stdout.strip()


def exact_config_path(raw):
    match = re.search(r'argv\[\]=(.*?)(?=\s*;\s*[a-z_]+=|}\s*$)', raw, re.S)
    if match:
        try:
            argv = shlex.split(match.group(1))
        except ValueError:
            fail('execstart_argv_parse_failed')
    else:
        m = re.search(r'\bpath=([^ ;}]+)', raw)
        argv = [m.group(1)] if m else []
        m = re.search(r'--config(?:=|\s+)(?:"([^"]+)"|\'([^\']+)\'|([^ ;}]+))', raw)
        if m:
            argv.extend(['--config', next(v for v in m.groups() if v is not None)])
    if not argv or Path(argv[0]).resolve() != Path(EXPECTED_BINARY):
        fail('execstart_binary_mismatch')
    for i, arg in enumerate(argv):
        if arg == '--config' and i + 1 < len(argv):
            value = argv[i + 1]
            break
        if arg.startswith('--config='):
            value = arg.split('=', 1)[1]
            break
    else:
        fail('execstart_config_missing')
    if not Path(value).is_absolute():
        fail('execstart_config_not_absolute')
    return value


def yaml_scalar(lines, key):
    rows = [line for line in lines if re.match(r'^\s*' + re.escape(key) + r'\s*:', line)]
    if len(rows) != 1:
        fail('config_hub_url_missing_or_duplicate')
    scalar = rows[0].split(':', 1)[1].strip()
    if scalar.startswith('"'):
        try:
            value = json.loads(scalar)
        except (TypeError, ValueError):
            fail('config_hub_url_quoted_parse_failed')
    elif scalar.startswith("'") and scalar.endswith("'") and len(scalar) >= 2:
        value = scalar[1:-1].replace("''", "'")
    else:
        value = scalar.split('#', 1)[0].strip()
    if not isinstance(value, str) or not value:
        fail('config_hub_url_empty')
    return value


raw_exec = systemd_value('ExecStart')
config_path = exact_config_path(raw_exec)
del raw_exec
active = systemd_value('ActiveState')
main_pid = systemd_value('MainPID')
if active != 'active' or not main_pid.isdigit() or int(main_pid) <= 1:
    fail('repair_service_not_active')
config = Path(config_path)
st = config.lstat()
if config.is_symlink() or not config.is_file():
    fail('repair_config_not_regular')
url_value = yaml_scalar(config.read_text().splitlines(), 'hub_url')
try:
    parsed = urlsplit(url_value)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port
except ValueError:
    fail('config_hub_url_invalid')
if not scheme or not host:
    fail('config_hub_url_missing_scheme_or_host')
print(json.dumps({
    'status': 'PASS',
    'service_active': True,
    'main_pid_present': True,
    'config_path_from_exact_execstart': config_path,
    'config_regular_non_symlink': True,
    'config_mode': oct(st.st_mode & 0o777),
    'hub_url': {
        'scheme': scheme,
        'host': host,
        'port': port,
        'path': parsed.path or '/',
        'userinfo_removed': parsed.username is not None or parsed.password is not None,
        'query_removed': bool(parsed.query),
        'fragment_removed': bool(parsed.fragment),
    },
    'connection_attempted': False,
}, separators=(',', ':')))
