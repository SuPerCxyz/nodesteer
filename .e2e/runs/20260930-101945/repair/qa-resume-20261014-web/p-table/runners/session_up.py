"""P-TABLE：在隔离实例上完成真实 UI 登录，并做会话/版本守卫。

密码通过 stdin 传入 agent-browser auth vault；不进入 argv，不写证据。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    nonce = led["nonce"]
    creds = C.load_creds(nonce)
    origin = C.origin()
    profile = f"ptable-{nonce}"

    C.ab("open", f"{origin}/sign-in", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    cp = subprocess.run(
        ["agent-browser", "--session", C.SESSION, "auth", "save", profile,
         "--url", f"{origin}/sign-in",
         "--username", creds["admin_username"],
         "--password-stdin",
         "--username-selector", 'input[autocomplete="username"]',
         "--password-selector", 'input[autocomplete="current-password"]',
         "--submit-selector", 'button[type="submit"]'],
        input=creds["admin_password"], capture_output=True, text=True, timeout=40)
    login_rc = cp.returncode
    C.ab("auth", "login", profile, timeout=60)
    C.ab("wait", "--load", "networkidle", timeout=40)
    url = C.ab("get", "url").stdout.strip()
    me = C.ab_json("""
      const t = localStorage.getItem('nodesteer_token');
      const out = { token_present: !!t, token_len: t ? t.length : 0, url: location.pathname };
      if (t) {
        const r = await fetch('/api/me', { headers: { Authorization: 'Bearer ' + t } });
        out.me_status = r.status;
        out.me_role = (await r.json().catch(() => ({}))).role || null;
      }
      return out;
    """)
    version = C.ab_json("return { title: document.title, base: location.origin };")
    ev = {
        "captured_at_utc": C.utc(),
        "instance_id": led["instance_id"],
        "origin": origin,
        "auth_save_rc": login_rc,
        "landed_url": url,
        "session": me,
        "page": version,
        "credential_in_evidence": False,
        "server_writes": 0,
    }
    rel = C.write_evidence("P-TABLE-00-session.json", ev)
    print(json.dumps({"evidence": rel, "landed": url, "me_status": me.get("me_status"),
                      "role": me.get("me_role")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
