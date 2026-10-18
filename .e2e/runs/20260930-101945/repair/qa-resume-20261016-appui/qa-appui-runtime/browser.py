"""agent-browser wrapper for the APP-UI-20261016 batch (own session only).

Every browser action goes through `agent-browser --session <owned-session>`.
Secrets are never passed through argv: credential values only travel on stdin
via `eval --stdin`, and are never written into evidence.
"""
import json
import subprocess
import time

AB = 'agent-browser'


class Browser:
    def __init__(self, session):
        self.session = session

    # ---------- raw ----------
    def run(self, *args, timeout=120, stdin=None, check=True):
        cmd = [AB, '--session', self.session] + [str(a) for a in args]
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
        out = (cp.stdout or '').strip()
        err = (cp.stderr or '').strip()
        if check and cp.returncode != 0:
            raise RuntimeError('browser_cmd_failed(%s rc=%s): %s' % (args[0], cp.returncode,
                                                                    (err or out)[-500:]))
        return cp.returncode, out, err

    # ---------- navigation ----------
    def open(self, url, width=1600, height=1000):
        return self.run('open', url, timeout=150)[1]

    def url(self):
        return self.run('get', 'url')[1].strip()

    def wait_url(self, pattern, timeout=40):
        return self.run('wait', '--url', pattern, timeout=timeout + 30, check=False)

    def wait_text(self, text, timeout=40):
        return self.run('wait', '--text', text, timeout=timeout + 30, check=False)

    def wait_ms(self, ms):
        return self.run('wait', str(ms), timeout=ms // 1000 + 30, check=False)

    # ---------- reading ----------
    def snapshot(self, interactive=True, compact=True, json_out=False, extra=()):
        args = ['snapshot']
        if interactive:
            args.append('-i')
        if compact:
            args.append('-c')
        if json_out:
            args.append('--json')
        args.extend(extra)
        return self.run(*args)[1]

    def text(self):
        return self.evaljs('document.body ? document.body.innerText : ""') or ''

    def evaljs(self, js, timeout=120):
        rc, out, err = self.run('eval', '--stdin', timeout=timeout, stdin=js, check=False)
        if rc != 0:
            raise RuntimeError('eval_failed: %s' % (err or out)[-400:])
        return _parse_eval(out)

    def get_text(self, sel):
        return self.run('get', 'text', sel)[1]

    # ---------- interaction ----------
    def click(self, sel):
        return self.run('click', sel)[1]

    def click_js(self, js):
        """Click exactly one element located by JS (returns count of clicks)."""
        return self.evaljs(
            "(function(){const el=(%s);if(!el)return 0;el.scrollIntoView({block:'center'});"
            "el.click();return 1;})()" % js)

    def fill(self, sel, value):
        return self.run('fill', sel, value)[1]

    def fill_js(self, sel_js, value):
        """React-safe fill for controlled inputs, value never in argv."""
        js = ("(function(){const el=(%s);if(!el)return 0;"
              "const proto=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el, %s);"
              "el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));"
              "return 1;})()" % (sel_js, json.dumps(value)))
        return self.evaljs(js)

    def select_js(self, sel_js, value):
        """Select for native <select> / Radix listbox opening is handled by callers."""
        js = ("(function(){const el=(%s);if(!el)return 0;"
              "const d=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value');"
              "d.set.call(el, %s);"
              "el.dispatchEvent(new Event('change',{bubbles:true}));return 1;})()" % (sel_js, json.dumps(value)))
        return self.evaljs(js)

    def set_checkbox(self, sel_js, checked):
        js = ("(function(){const el=(%s);if(!el)return 'missing';"
              "if(el.checked===%s)return 'already';"
              "el.scrollIntoView({block:'center'});"
              "el.click();return el.checked?'checked':'unchecked';})()"
              % (sel_js, 'true' if checked else 'false'))
        return self.evaljs(js)

    def press(self, key):
        return self.run('press', key)[1]

    def find(self, *args, timeout=60):
        return self.run('find', *args, timeout=timeout, check=False)

    # ---------- evidence ----------
    def screenshot(self, path, full=True):
        args = ['screenshot']
        if full:
            args.append('--full')
        args.append(path)
        return self.run(*args, timeout=150)[1]

    def net_requests(self, url_filter=None):
        args = ['network', 'requests']
        if url_filter:
            args.append('--filter')
            args.append(url_filter)
        args.append('--json')
        rc, out, err = self.run(*args, check=False)
        try:
            return json.loads(out)
        except Exception:
            return {'_raw': out[-2000:]}

    def close(self):
        return self.run('close', timeout=60, check=False)


def _parse_eval(out):
    out = out.strip()
    if not out:
        return None
    for candidate in (out, out.splitlines()[-1]):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return out
