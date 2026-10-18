(() => {
  const leaf = (txt) =>
    [...document.querySelectorAll('h1,h2,h3,h4,div,span,p')].filter(
      (e) => e.children.length === 0 && e.textContent.trim() === txt
    );
  const cardFrom = (h, depth) => {
    let root = h;
    for (let i = 0; i < (depth || 7) && root.parentElement; i++) {
      root = root.parentElement;
      if (root.querySelector('[role=checkbox], a[href^="/applications/"]')) break;
    }
    return root;
  };
  const out = {
    path: location.pathname + location.search,
    headings: [...document.querySelectorAll('h1,h2,h3,h4')].map((x) => x.textContent.trim()),
  };
  // application editor: assign-nodes card (title '分配节点')
  const assignH = leaf('分配节点')[0];
  if (assignH) {
    const root = cardFrom(assignH);
    out.assign_nodes = [...root.querySelectorAll('label')].map((l) => ({
      text: l.innerText.trim().replace(/\s+/g, ' '),
      state: (l.querySelector('[role=checkbox]') || {}).getAttribute
        ? l.querySelector('[role=checkbox]').getAttribute('data-state')
        : null,
    }));
  }
  // agent/node detail: managed applications (anchors to /applications/{id})
  out.managed_app_links = [...document.querySelectorAll('a[href^="/applications/"]')].map((a) => ({
    href: a.getAttribute('href'),
    text: a.innerText.trim(),
  }));
  out.managed_apps_empty_text = document.body.innerText.includes('该节点暂无托管应用');
  // tables (artifact/user/list pages)
  const table = document.querySelector('table');
  if (table) {
    out.headers = [...table.querySelectorAll('thead th')].map((th) => ({
      text: th.innerText.trim(),
      ariaSort: th.getAttribute('aria-sort'),
    }));
    out.rows = [...table.querySelectorAll('tbody tr')].map((tr) =>
      [...tr.cells].map((c) => c.innerText.trim())
    );
  }
  return out;
})()
