(() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const accName = (el) => {
    const al = el.getAttribute('aria-label'); if (al) return norm(al);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = document.getElementById(lb); if (t) return norm(t.innerText); }
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) {
      try { if (el.labels && el.labels.length) { const t = norm([...el.labels].map(l => l.innerText).join(' ')); if (t) return t; } } catch (e) {}
      const id = el.id;
      if (id) { try { const l = document.querySelector('label[for="' + CSS.escape(id) + '"]'); if (l) return norm(l.innerText); } catch (e) {} }
      const ph = el.getAttribute('placeholder'); if (ph) return norm(ph);
      const ttl = el.getAttribute('title'); if (ttl) return norm(ttl);
      if (el.type === 'submit' || el.type === 'button' || el.type === 'reset') return norm(el.value || el.innerText) || el.type;
      // 邻近标签兜底：同一表单分组内的 label 文本
      let sib = el.previousElementSibling;
      while (sib) { if (sib.tagName === 'LABEL') { const t = norm(sib.innerText); if (t) return t; } sib = sib.previousElementSibling; }
      const grp = el.closest('[class*=grid], [class*=space-y], fieldset, form');
      if (grp) { const l = grp.querySelector('label'); if (l) { const t = norm(l.innerText); if (t) return t; } }
      return el.name ? 'input:' + el.name : el.type;
    }
    return norm(el.innerText || el.textContent).slice(0, 100);
  };
  const visible = el => {
    try { const r = el.getBoundingClientRect(); const st = getComputedStyle(el); return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; } catch (e) { return false; }
  };
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=menuitem], [role=menuitemcheckbox], [role=menuitemradio], [role=tab], [role=checkbox], [role=switch], [role=option], [role=combobox], [role=link], [role=radio], [aria-haspopup], [contenteditable=true]';
  const out = [];
  const seen = new Set();
  document.querySelectorAll(sel).forEach(el => {
    const name = accName(el);
    if (!name && el.tagName !== 'INPUT' && el.tagName !== 'SELECT' && el.tagName !== 'TEXTAREA') return;
    const dlg = el.closest('[role=dialog], dialog');
    const row = el.closest('tr');
    const key = [el.tagName, el.getAttribute('type') || '', el.getAttribute('role') || '', name, el.getAttribute('href') || '', !!dlg, row ? [...row.parentNode.children].indexOf(row) : ''].join('|');
    if (seen.has(key)) return; seen.add(key);
    const sec = el.closest('section, form, [role=region], .card, [class*=card]');
    let sectionName = '';
    if (sec) { const h = sec.querySelector('h1,h2,h3,h4,legend'); sectionName = h ? norm(h.innerText).slice(0, 60) : ''; }
    out.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      name: name,
      href: el.getAttribute('href') || '',
      title: el.getAttribute('title') || '',
      haspopup: el.getAttribute('aria-haspopup') || '',
      expanded: el.getAttribute('aria-expanded') || '',
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      checked: el.checked === true ? true : (el.getAttribute('aria-checked') || ''),
      inTable: !!el.closest('table'),
      inDialog: !!dlg,
      rowIdx: row ? [...row.parentNode.children].indexOf(row) : -1,
      section: sectionName,
      visible: visible(el)
    });
  });
  const headings = [...document.querySelectorAll('h1,h2,h3')].filter(visible).map(h => h.tagName + ':' + norm(h.innerText).slice(0, 60));
  const tables = [...document.querySelectorAll('table')].map(t => ({
    headers: [...t.querySelectorAll('thead th')].map(th => norm(th.innerText)).filter(Boolean),
    rows: t.querySelectorAll('tbody tr').length
  }));
  const dialogs = [...document.querySelectorAll('[role=dialog], dialog[open]')].map(d => norm(d.innerText).slice(0, 120));
  return JSON.stringify({ url: location.href, title: document.title, headings: headings, tables: tables, dialogs: dialogs, controls: out });
})()
