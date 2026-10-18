#!/usr/bin/env python3
"""Drive the NodeSteer transfers form through agent-browser (real UI actions).

Subcommands:
  add-target <target-hostname> <dest-path>   select target Agent + dest path + 添加目标
  set-source  <src-hostname> <src-path>      select source Agent + source path
  submit                                     click 开始传输
  dump <outfile>                             snapshot the form state to JSON

Refs are re-resolved from a fresh snapshot before every interaction, because
radix menus and re-renders renumber the accessibility refs.
"""
import json, re, subprocess, sys, time

SESSION = "qaweb1410b04"
CLI = ["agent-browser", "--session", SESSION]


def run(args, timeout=60):
    cp = subprocess.run(CLI + args, capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def snap():
    _, out, _ = run(["snapshot", "-i"])
    return out


REF_RE = re.compile(r"\[(?:[^\]]*?)ref=(e\d+)\]")


def lines_with(snapshot, needle):
    return [l for l in snapshot.splitlines() if needle in l]


def ref_of(snapshot, needle, occurrence=0):
    cands = lines_with(snapshot, needle)
    if len(cands) <= occurrence:
        return None, cands
    m = REF_RE.search(cands[occurrence])
    return (m.group(1) if m else None), cands


def click_ref(ref):
    rc, out, err = run(["click", "@" + ref])
    return rc, (out + err).strip()[:200]


def fill_ref(ref, value):
    rc, out, err = run(["fill", "@" + ref, value])
    return rc, (out + err).strip()[:200]


def select_option(combo_label, option_text):
    s = snap()
    ref, cands = ref_of(s, 'combobox "%s"' % combo_label)
    if not ref:
        return {"ok": False, "reason": "combo not found", "cands": cands}
    click_ref(ref)
    time.sleep(0.7)
    s2 = snap()
    lines = lines_with(s2, 'option "%s"' % option_text)
    if not lines:
        return {"ok": False, "reason": "option not found", "combo": combo_label, "option": option_text}
    m = REF_RE.search(lines[0])
    click_ref(m.group(1))
    time.sleep(0.6)
    s3 = snap()
    cur = lines_with(s3, 'combobox "%s"' % combo_label)
    return {"ok": True, "combo": combo_label, "option": option_text, "after": cur[0].strip() if cur else None}


def fill_field(label, value):
    s = snap()
    ref, cands = ref_of(s, 'textbox "%s"' % label)
    if not ref:
        return {"ok": False, "reason": "textbox not found", "label": label, "cands": cands}
    rc, msg = fill_ref(ref, value)
    time.sleep(0.4)
    s2 = snap()
    cur = lines_with(s2, 'textbox "%s"' % label)
    return {"ok": True, "label": label, "value_set": value,
            "after": cur[0].strip() if cur else None, "cli": msg}


def click_button(name):
    s = snap()
    ref, cands = ref_of(s, 'button "%s"' % name)
    if not ref:
        return {"ok": False, "reason": "button not found", "name": name}
    rc, msg = click_ref(ref)
    return {"ok": True, "name": name, "cli": msg}


def form_state(outfile):
    s = snap()
    keep = [l.strip() for l in s.splitlines()
            if any(k in l for k in ('combobox "源 Agent"', 'combobox "目标 Agent"', 'textbox "源文件路径"',
                                    'textbox "目标文件路径"', 'button "添加目标"', 'button "开始传输"',
                                    'button "移除"'))]
    targets = [l.strip() for l in s.splitlines() if re.search(r'/tmp/qa-w1410b04', l) and 'textbox' not in l]
    doc = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "form_lines": keep,
           "target_lines": targets}
    json.dump(doc, open(outfile, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    return doc


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "set-source":
        print(json.dumps(select_option("源 Agent", sys.argv[2]), ensure_ascii=False))
        print(json.dumps(fill_field("源文件路径", sys.argv[3]), ensure_ascii=False))
    elif cmd == "add-target":
        print(json.dumps(select_option("目标 Agent", sys.argv[2]), ensure_ascii=False))
        print(json.dumps(fill_field("目标文件路径", sys.argv[3]), ensure_ascii=False))
        print(json.dumps(click_button("添加目标"), ensure_ascii=False))
    elif cmd == "submit":
        print(json.dumps(click_button("开始传输"), ensure_ascii=False))
    elif cmd == "dump":
        form_state(sys.argv[2])
    else:
        raise SystemExit("unknown cmd " + cmd)
