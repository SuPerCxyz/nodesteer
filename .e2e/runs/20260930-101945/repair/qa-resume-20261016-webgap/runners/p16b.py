#!/usr/bin/env python3
"""P16-02 / P16-04 (续): arm64 / 重复 / 空文件 / 64MiB 超大文件 + 上传中按钮禁用与重复点击。

第一个 amd64 制品已在 p16.py 中通过真实 UI 上传成功（q16-02 证据见 P16-artifact-upload-1.json）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402
from api import j, login  # noqa: E402

PFX = "qa-webgap-6456ada2"
SRC = Path("/tmp/opencode/webgap-6456ada2")
OUT = {"case_ids": ["P16-02", "P16-04"], "created": [], "steps": [], "session": ui.SESSION,
       "origin": ui.ORIGIN, "prefix": PFX}
tok = login()


def art_list():
    st, d = j("GET", "/api/artifacts", tok)
    return d if isinstance(d, list) else []


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:900])
    return obj


def form_state():
    return ui.js(r'''
    (() => {
      const name = document.querySelector('input[name=name]');
      const version = document.querySelector('input[name=version]');
      const arch = document.querySelector('select[name=architecture]');
      const file = document.querySelector('#artifact-file');
      const btn = [...document.querySelectorAll('button[type=submit]')].pop();
      return {
        url: location.pathname,
        name: name ? {required:name.required, value:name.value} : null,
        version: version ? {required:version.required, value:version.value} : null,
        arch: arch ? {value:arch.value, options:[...arch.options].map(o=>o.value)} : null,
        file: file ? {required:file.required, files:file.files.length, label:(file.closest('label')||{}).innerText} : null,
        submit: btn ? {text:btn.innerText.trim(), disabled:btn.disabled} : null,
      };
    })()''')


def fill(name, version, arch):
    return ui.js(r'''(async () => {
      const setVal = (el, v) => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; s.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true})); };
      const n = document.querySelector('input[name=name]'); const v = document.querySelector('input[name=version]');
      n.value=''; setVal(n, %s); setVal(v, %s);
      const a = document.querySelector('select[name=architecture]');
      const sel = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;
      sel.call(a, %s); a.dispatchEvent(new Event('change',{bubbles:true}));
      await new Promise(r=>setTimeout(r,250));
      return {name:n.value, version:v.value, arch:a.value};
    })()''' % (json.dumps(name), json.dumps(version), json.dumps(arch)))


def upload_file(path):
    out, err, rc = ui.run("upload", "#artifact-file", str(path))
    return {"rc": rc, "out": out.strip()[:120], "err": err.strip()[:160]}


def click_submit_now():
    return ui.js(r'''(() => {
      const btn = [...document.querySelectorAll('button[type=submit]')].pop();
      const before = {text: btn.innerText.trim(), disabled: btn.disabled, url: location.pathname};
      btn.click();
      return before;
    })()''')


def btn_state():
    try:
        return ui.js(r'''(() => {
          const btn = [...document.querySelectorAll('button[type=submit]')].pop();
          return {url: location.pathname, text: btn ? btn.innerText.trim() : null,
                  disabled: btn ? btn.disabled : null};
        })()''')
    except Exception as e:
        return {"error": str(e)[:200]}


def wait_artifacts_page(timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            u = ui.url()
        except Exception:
            u = ""
        if u.endswith("/artifacts") or u.endswith("/artifacts/"):
            return {"ok": True, "waited_s": round(time.time() - t0, 2), "url": u}
        time.sleep(0.6)
    return {"ok": False, "waited_s": round(time.time() - t0, 2)}


def simple_upload(name, version, arch, fname, file):
    ui.open_("/artifacts/new", wait=1.6)
    ui.run("network", "requests", "--clear")
    f = fill(name, version, arch)
    up = upload_file(file)
    st = form_state()
    before = click_submit_now()
    w = wait_artifacts_page(120)
    time.sleep(0.8)
    rows = [a for a in art_list() if a.get("name") == name]
    return {"fill": f, "upload": up, "form": st, "click_before": before, "wait": w,
            "api_rows": [{"id": a["id"], "version": a["version"], "arch": a["architecture"],
                          "size": a["size"], "sha256": a["sha256"]} for a in rows]}


def main():
    # 已由 p16.py 完成：amd64 small 上传
    amd = [a for a in art_list() if a.get("name") == PFX + "-art-amd64"]
    step("p16-02-amd64-already-created", [{"id": a["id"], "version": a["version"], "arch": a["architecture"],
                                           "size": a["size"], "sha256": a["sha256"]} for a in amd])

    # 列表页行渲染证据（长 SHA / 行操作）
    ui.open_("/artifacts", wait=2.0)
    rows = ui.js(r'''(() => {
      const tr = [...document.querySelectorAll('tbody tr')];
      return tr.map(r => ({text: r.innerText.replace(/\n/g,' | '), html: r.innerHTML.slice(0,1200)}));
    })()''')
    step("p16-02-list-rows", rows)
    ui.shot("P16-02-05-artifacts-list.png")

    # --- P16-02 arm64 ---
    r = simple_upload(PFX + "-art-arm64", "2.0.0", "arm64", "small.bin", SRC / "small.bin")
    step("p16-02-arm64", r)
    ui.shot("P16-02-06-list-after-arm64.png")
    for a in [x for x in art_list() if x.get("name") == PFX + "-art-arm64"]:
        OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"], "version": a["version"],
                               "arch": a["architecture"], "size": a["size"], "sha256": a["sha256"],
                               "batch_case": "P16-02-arm64"})

    # --- P16-04 duplicate ---
    r = simple_upload(PFX + "-art-amd64", "1.0.0", "amd64", "small.bin", SRC / "small.bin")
    step("p16-04-duplicate", {"wait": r["wait"], "api_rows": r["api_rows"],
                              "rows_after": len([a for a in art_list() if a.get("name") == PFX + "-art-amd64"])})
    for a in [x for x in art_list() if x.get("name") == PFX + "-art-amd64"]:
        if not any(c["id"] == a["id"] for c in OUT["created"]):
            OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"], "version": a["version"],
                                   "arch": a["architecture"], "size": a["size"], "sha256": a["sha256"],
                                   "batch_case": "P16-04-duplicate"})

    # --- P16-04 empty file ---
    r = simple_upload(PFX + "-art-empty", "0.0.0", "amd64", "empty.bin", SRC / "empty.bin")
    step("p16-04-empty", r)
    for a in [x for x in art_list() if x.get("name") == PFX + "-art-empty"]:
        OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"], "version": a["version"],
                               "arch": a["architecture"], "size": a["size"], "sha256": a["sha256"],
                               "batch_case": "P16-04-empty"})

    # --- P16-04 64MiB：上传中按钮禁用 + 重复点击不产生第二次 POST ---
    ui.open_("/artifacts/new", wait=1.6)
    f = fill(PFX + "-art-large64", "1.0.0", "amd64")
    up = upload_file(SRC / "large64.bin")
    st = form_state()
    step("p16-04-large-selected", {"fill": f, "upload": up, "form": st})
    ui.shot("P16-04-01-large-selected.png")
    ui.run("network", "requests", "--clear")
    before = click_submit_now()
    time.sleep(0.35)
    s1 = btn_state()
    ui.shot("P16-04-02-uploading.png")
    dbl = ui.js(r'''(() => {
      const btn = [...document.querySelectorAll('button[type=submit]')].pop();
      const state = {text: btn ? btn.innerText.trim() : null, disabled: btn ? btn.disabled : null};
      if (btn) btn.click();   // 第二次点击尝试
      return state;
    })()''')
    time.sleep(0.4)
    s2 = btn_state()
    ui.shot("P16-04-03-uploading-2.png")
    w = wait_artifacts_page(180)
    time.sleep(1.0)
    reqs = ui.requests(300)
    posts = [x for x in reqs if "POST" in x.upper() and "/api/artifacts" in x]
    big = [a for a in art_list() if a.get("name") == PFX + "-art-large64"]
    step("p16-04-large-result", {"samples": {"t0.35": s1, "t0.75": s2, "click_attempt": dbl},
                                 "wait": w, "post_count_during_large": len(posts), "posts": posts,
                                 "api_rows": [{"id": a["id"], "size": a["size"], "sha256": a["sha256"]} for a in big]})
    for a in big:
        OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"], "version": a["version"],
                               "arch": a["architecture"], "size": a["size"], "sha256": a["sha256"],
                               "batch_case": "P16-04-large64"})

    ui.open_("/artifacts", wait=2.0)
    ui.shot("P16-04-04-list-after-large.png")
    p = Path(ui.BATCH) / "evidence" / "P16-artifact-upload-2.json"
    p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
    print("WROTE", p)
    print("created:", json.dumps(OUT["created"], ensure_ascii=False))


main()
