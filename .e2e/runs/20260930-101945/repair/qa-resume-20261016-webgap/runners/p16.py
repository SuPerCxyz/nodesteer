#!/usr/bin/env python3
"""P16-02 / P16-04: 制品上传页（/artifacts/new）四字段与边界。

- 真实 UI 上传（agent-browser upload）到 repair :18080
- 所有产物均为 owned fixture，记录 id 供后续清理
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
    print("---", label, json.dumps(obj, ensure_ascii=False)[:700])


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
        heading: (document.querySelector('h1')||{}).innerText || null,
        name: name ? {present:true, required:name.required, value:name.value, label:(name.closest('label')||{}).innerText} : null,
        version: version ? {present:true, required:version.required, value:version.value, label:(version.closest('label')||{}).innerText} : null,
        arch: arch ? {present:true, value:arch.value, options:[...arch.options].map(o=>o.value), label:(arch.closest('label')||{}).innerText} : null,
        file: file ? {present:true, type:file.type, required:file.required, accept:file.accept||null, files:file.files.length, label:(file.closest('label')||{}).innerText} : null,
        submit: btn ? {text:btn.innerText.trim(), disabled:btn.disabled, type:btn.type} : null,
        bodyText: document.body.innerText.slice(0,200)
      };
    })()''')


def fill(name, version, arch):
    ui.js(r'''(async () => {
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
    return {"rc": rc, "out": out.strip()[:200], "err": err.strip()[:200]}


def submit_capture(label, wait_shot=None):
    """Click submit and capture the button state right after (uploading window)."""
    res = ui.js(r'''(async () => {
      const btn = [...document.querySelectorAll('button[type=submit]')].pop();
      const before = {text: btn.innerText.trim(), disabled: btn.disabled};
      btn.click();
      const samples = [];
      for (let i=0;i<40;i++) {
        await new Promise(r=>setTimeout(r,200));
        const b = [...document.querySelectorAll('button[type=submit]')].pop();
        if (!b) { samples.push({t:i*200, gone:true}); break; }
        samples.push({t:i*200, text:b.innerText.trim(), disabled:b.disabled, url:location.pathname});
        if (location.pathname === '/artifacts' || location.pathname === '/artifacts/') break;
      }
      const btn2 = [...document.querySelectorAll('button[type=submit]')].pop();
      return {before, samples, url: location.pathname,
              after: btn2 ? {text: btn2.innerText.trim(), disabled: btn2.disabled} : null,
              error: (document.querySelector('[role=alert]')||{}).innerText || null};
    })()''')
    step(label, res)
    return res


def main():
    ui.run("network", "requests", "--clear")
    ui.open_("/artifacts/new", wait=2.0)
    step("p16-02-form-initial", form_state())
    ui.shot("P16-02-01-form.png")

    # --- P16-02 #1: amd64 real upload ---
    fill(PFX + "-art-amd64", "1.0.0", "amd64")
    step("p16-02-filled-amd64", form_state())
    ui.shot("P16-02-02-form-filled-amd64.png")
    step("p16-02-upload-small", upload_file(SRC / "small.bin"))
    step("p16-02-file-selected", form_state())
    ui.shot("P16-02-03-file-selected.png")
    r = submit_capture("p16-02-submit-uploading-window")
    ui.shot("P16-02-04-after-upload.png")

    lst = art_list()
    amd = [a for a in lst if a.get("name") == PFX + "-art-amd64"]
    step("p16-02-api-verify-amd64", [{"id": a["id"], "name": a["name"], "version": a["version"],
                                     "architecture": a["architecture"], "size": a["size"],
                                     "sha256": a["sha256"], "filename": a.get("filename")} for a in amd])
    for a in amd:
        OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"],
                               "version": a["version"], "arch": a["architecture"], "size": a["size"],
                               "sha256": a["sha256"], "batch_case": "P16-02"})

    # list page evidence
    ui.open_("/artifacts", wait=2.0)
    row = ui.js(r'''(async () => {
      const rows = [...document.querySelectorAll('tbody tr')];
      const hit = rows.find(r => (r.innerText||'').includes(%s));
      return {rows: rows.map(r=>r.innerText.replace(/\n/g,' | ').slice(0,200)),
              hit: hit ? {text: hit.innerText.replace(/\n/g,' | '), html: hit.innerHTML.slice(0,900)} : null};
    })()''' % json.dumps(PFX + "-art-amd64"))
    step("p16-02-list-row", row)
    ui.shot("P16-02-05-list-amd64.png")

    # --- P16-02 #2: arm64 real upload ---
    ui.open_("/artifacts/new", wait=1.6)
    fill(PFX + "-art-arm64", "2.0.0", "arm64")
    step("p16-02-filled-arm64", form_state())
    upload_file(SRC / "small.bin")
    r2 = submit_capture("p16-02-submit-arm64")
    lst = art_list()
    arm = [a for a in lst if a.get("name") == PFX + "-art-arm64"]
    step("p16-02-api-verify-arm64", [{"id": a["id"], "architecture": a["architecture"],
                                      "size": a["size"], "sha256": a["sha256"]} for a in arm])
    for a in arm:
        OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"],
                               "version": a["version"], "arch": a["architecture"], "size": a["size"],
                               "sha256": a["sha256"], "batch_case": "P16-02"})

    # --- P16-04 #1: duplicate (same name+version, files identical) ---
    ui.open_("/artifacts/new", wait=1.6)
    ui.run("network", "requests", "--clear")
    fill(PFX + "-art-amd64", "1.0.0", "amd64")
    upload_file(SRC / "small.bin")
    r3 = submit_capture("p16-04-submit-duplicate")
    dup = [a for a in art_list() if a.get("name") == PFX + "-art-amd64"]
    step("p16-04-duplicate-result", {"count": len(dup), "ids": [a["id"] for a in dup],
                                     "readme": "1 次真实 UI 上传后同名同版本条目数"})
    for a in dup:
        if not any(c["id"] == a["id"] for c in OUT["created"]):
            OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"],
                                   "version": a["version"], "arch": a["architecture"], "size": a["size"],
                                   "sha256": a["sha256"], "batch_case": "P16-04-duplicate"})

    # --- P16-04 #2: empty file ---
    ui.open_("/artifacts/new", wait=1.6)
    fill(PFX + "-art-empty", "0.0.0", "amd64")
    upload_file(SRC / "empty.bin")
    step("p16-04-empty-selected", form_state())
    r4 = submit_capture("p16-04-submit-empty")
    emp = [a for a in art_list() if a.get("name") == PFX + "-art-empty"]
    step("p16-04-empty-result", [{"id": a["id"], "size": a["size"], "sha256": a["sha256"]} for a in emp])
    for a in emp:
        OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"],
                               "version": a["version"], "arch": a["architecture"], "size": a["size"],
                               "sha256": a["sha256"], "batch_case": "P16-04-empty"})

    # --- P16-04 #3: oversized (64 MiB) + uploading state capture ---
    ui.open_("/artifacts/new", wait=1.6)
    fill(PFX + "-art-large64", "1.0.0", "amd64")
    upload_file(SRC / "large64.bin")
    step("p16-04-large-selected", form_state())
    ui.run("network", "requests", "--clear")
    r5 = submit_capture("p16-04-submit-large64-uploading-window")
    big = [a for a in art_list() if a.get("name") == PFX + "-art-large64"]
    step("p16-04-large-result", [{"id": a["id"], "size": a["size"], "sha256": a["sha256"]} for a in big])
    for a in big:
        OUT["created"].append({"kind": "artifact", "id": a["id"], "name": a["name"],
                               "version": a["version"], "arch": a["architecture"], "size": a["size"],
                               "sha256": a["sha256"], "batch_case": "P16-04-large"})

    reqs = ui.requests(300)
    OUT["post_artifact_requests"] = [r for r in reqs if "/api/artifacts" in r]
    OUT["uploading_samples_summary"] = {
        "large64": r5.get("samples", [])[:6] if isinstance(r5, dict) else None,
    }
    p = Path(ui.BATCH) / "evidence" / "P16-artifact-upload.json"
    p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
    print("WROTE", p)
    print("created:", json.dumps(OUT["created"], ensure_ascii=False))


main()
