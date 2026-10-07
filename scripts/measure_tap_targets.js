// Measures the tap targets on the page that is open, for the phone check of docs/graph-and-lab-review.md (#90).
//
// How to run it: sign in to the app in a browser, make the viewport 390 x 844 (devtools device mode), open a view, paste
// this whole file into the console. It returns { width, count, small: [...] }: every visible button, link, field and
// switch whose box is under 36 px high (or, for a control with no text, under 36 px wide), with the text, the classes
// and the size. An empty `small` list is what "tap targets of 36 px or more" means on that view.
// (The in-app browser tool returns the same object when this file is evaluated as one expression.)
(() => {
  const MIN = 36;
  const sel = 'button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [tabindex="0"]';
  const small = [];
  let count = 0;
  for (const el of document.querySelectorAll(sel)) {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (el.closest('[aria-hidden="true"]')) continue;
    // A plain link in running text or the footer is exempt (WCAG 2.5.8 inline exception): only links styled as buttons, chips or tabs count.
    if (el.tagName === 'A' && !/\bbtn\b|\bchip\b|help-hint|help-toc/.test(el.className)) continue;
    // A checkbox or radio inside its label is tapped through the label.
    if (el.tagName === 'INPUT' && /checkbox|radio/.test(el.type) && el.closest('label')) { count++; continue; }
    count++;
    if (r.height < MIN - 0.5 || (!el.textContent.trim() && r.width < MIN - 0.5)) {
      small.push({
        text: (el.textContent || el.getAttribute('aria-label') || el.placeholder || '').trim().slice(0, 40),
        tag: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 60),
        w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
      });
    }
  }
  return { width: innerWidth, count, small };
})()
