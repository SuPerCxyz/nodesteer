(() => {
  const out = {
    path: location.pathname,
    headings: [...document.querySelectorAll('h1,h2,h3,h4')].map((x) => x.textContent.trim()),
    fields: {},
    links: [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')).filter((h) => h && h.startsWith('/')),
    body: document.body.innerText.replace(/\s+\n/g, '\n').slice(0, 2600),
  };
  document.querySelectorAll('dt,label,span,p,div').forEach((el) => {
    if (el.children.length) return;
    const t = el.textContent.trim();
    if (!t || t.length > 40) return;
    const sib = el.nextElementSibling;
    if (sib && !sib.children.length && sib.textContent.trim()) out.fields[t] = sib.textContent.trim();
  });
  return out;
})()
