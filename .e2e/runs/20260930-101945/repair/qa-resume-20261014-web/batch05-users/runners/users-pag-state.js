(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const btns = [...document.querySelectorAll('button')];
  const nav = (lbl) => btns.find(b => b.querySelector('.sr-only')?.textContent?.trim() === lbl);
  const st = (lbl) => { const b = nav(lbl); return b ? { disabled: b.disabled } : null; };
  const pageText = [...document.querySelectorAll('div,p')]
    .map(e => e.textContent?.trim())
    .filter(t => t && /第\s*\d+\s*页/.test(t) && t.length < 40);
  const combos = [...document.querySelectorAll('[role=combobox]')].map(e => e.textContent?.trim());
  const pageButtons = btns
    .map(b => b.querySelector('.sr-only')?.textContent?.trim())
    .filter(t => t && t.startsWith('跳转到第'));
  return {
    rows: rows.length,
    names: rows.map(r => r.cells[0].innerText.trim()),
    roles: rows.map(r => r.cells[1].innerText.trim()),
    first: st('跳转到第一页'), prev: st('上一页'),
    next: st('下一页'), last: st('跳转到最后一页'),
    pageText: [...new Set(pageText)],
    pageSizeSelect: combos[0] ?? null,
    pageButtons,
    lsTable: (() => { try { return localStorage.getItem('nodesteer.table.users'); } catch (e) { return 'ERR'; } })(),
  };
})()
