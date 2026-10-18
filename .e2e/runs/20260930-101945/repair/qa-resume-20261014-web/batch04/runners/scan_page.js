(() => {
  const leaf = (txt) =>
    [...document.querySelectorAll('h1,h2,h3,h4,div,span,p')].filter(
      (e) => e.children.length === 0 && e.textContent.trim() === txt
    );
  const out = {
    path: location.pathname + location.search,
    headings: [...document.querySelectorAll('h1,h2,h3,h4')].map((x) => x.textContent.trim()),
    managed_app_links: [...document.querySelectorAll('a[href^="/applications/"]')].map((a) => ({
      href: a.getAttribute('href'),
      text: a.innerText.trim().replace(/\s+/g, ' '),
    })),
    empty_texts: ['暂无托管应用', '该节点暂无托管应用', '暂无数据'].filter((t) =>
      document.body.innerText.includes(t)
    ),
  };
  const assignH = leaf('分配节点')[0];
  if (assignH) {
    let root = assignH;
    for (let i = 0; i < 7 && root.parentElement; i++) {
      root = root.parentElement;
      if (root.querySelector('[role=checkbox], a[href^="/applications/"]')) break;
    }
    out.assign_nodes = [...root.querySelectorAll('label')].map((l) => ({
      text: l.innerText.trim().replace(/\s+/g, ' '),
      state: l.querySelector('[role=checkbox]')
        ? l.querySelector('[role=checkbox]').getAttribute('data-state')
        : null,
    }));
  }
  const table = document.querySelector('table');
  if (table) {
    out.headers = [...table.querySelectorAll('thead th')].map((th) => th.innerText.trim());
    out.rows = [...table.querySelectorAll('tbody tr')].map((tr) =>
      [...tr.cells].map((c) => c.innerText.trim().replace(/\s+/g, ' '))
    );
  }
  return out;
})()
