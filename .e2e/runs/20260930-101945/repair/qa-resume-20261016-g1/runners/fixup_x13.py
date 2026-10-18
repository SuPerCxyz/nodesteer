"""X13 verdict 修正：path 值来自 CLI 输出带引号，重算布尔；并汇总 X13 两部分证据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def unq(v):
    if isinstance(v, str):
        return v.strip().strip('"')
    return v


def main() -> None:
    p = C.EVID / "X13-other-tab-logout-token-replacement.json"
    ev = json.loads(p.read_text())
    lo_reload = unq(ev["logout_effect"]["after_self_reload"]["path"])
    rp_reload = unq(ev["replace_effect"]["after_self_reload"]["path"])
    ev["verdict"]["converges_on_self_reload_after_logout"] = lo_reload.startswith("/sign-in")
    ev["verdict"]["converges_on_self_reload_after_replace"] = rp_reload.startswith("/sign-in")
    ev["verdict"]["other_tab_passive_redirect_after_logout_seconds"] = \
        ev["logout_effect"]["other_tab_passive_redirect_seconds"]
    ev["verdict"]["other_tab_passive_redirect_after_replace_seconds"] = \
        ev["replace_effect"]["other_tab_passive_redirect_seconds"]
    ev["verdict_summary"] = {
        "logout_in_other_tab": {
            "regression": "none (protected page stays open and usable)",
            "passive_convergence_seconds": ev["logout_effect"]["other_tab_passive_redirect_seconds"],
            "convergence_on_reload": "immediate (first bootstrapping /api/me → 401 → /sign-in)",
            "server_session_after_ui_logout": ev["tab1_probe_after_logout"]["stash_token_me"],
        },
        "token_replaced_in_other_tab": {
            "regression": "none (protected page keeps using its in-memory token)",
            "passive_convergence_seconds": ev["replace_effect"]["other_tab_passive_redirect_seconds"],
            "convergence_on_reload": "immediate",
            "forged_token_status": ev["tab2_after_replace"]["ls_token_me"],
            "old_token_status": ev["tab1_probe_after_replace"]["stash_token_me"],
        },
    }
    p.write_text(json.dumps(ev, ensure_ascii=False, indent=1) + "\n")

    # 汇总：X13 两部分
    part1 = json.loads((C.EVID / "X13-multi-session-role-logout-convergence.json").read_text())
    summary = {"case_id": "X13", "at_utc": C.utc(),
               "evidence_files": ["evidence/X13-multi-session-role-logout-convergence.json",
                                  "evidence/X13-other-tab-logout-token-replacement.json"],
               "dimensions": {
                   "cross_page_cache": part1["cross_page_cache"],
                   "two_session_edit_and_role_change_api": part1["old_session_role_change_api_path"],
                   "role_change_ui_path": part1["old_session_role_change_ui_path"],
                   "other_tab_logout": ev["logout_effect"],
                   "other_tab_token_replacement": ev["replace_effect"],
                   "server_side_contrast": ev["server_side_contrast"],
               },
               "verdict": {**part1["verdict"], **ev["verdict"]},
               "observations": part1["observations"]}
    C.write_evidence("X13-summary.json", summary)
    print(json.dumps({"fixed": ev["verdict_summary"], "summary_verdict": summary["verdict"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
