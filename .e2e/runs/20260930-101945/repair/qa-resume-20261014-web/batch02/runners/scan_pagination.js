(() => {
  const btns = Array.from(document.querySelectorAll('button'));
  const nav = btns
    .map((b) => {
      const sr = b.querySelector('.sr-only');
      const srText = sr ? sr.textContent.trim() : '';
      return srText ? { srText, disabled: b.disabled, ariaSort: null } : null;
    })
    .filter(Boolean);
  const bodyText = document.body.innerText;
  return {
    path: location.pathname + location.search,
    navButtons: nav,
    pageOf: (bodyText.match(/第 \d+ 页，共 \d+ 页/g) || []),
    totalRowsText: (bodyText.match(/共 \d+ 条/g) || []),
    pageSize: (document.querySelector('[role="combobox"]') || {}).innerText || null,
    storageTasks: localStorage.getItem('nodesteer.table.tasks')
  };
})()
