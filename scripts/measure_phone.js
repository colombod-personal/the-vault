// The phone check of #95: run on the page that is open, with the viewport at 390 x 844 (the in-app browser's resize_window, or
// devtools device mode), signed in, one view at a time. Paste the whole file into the console, or evaluate it as one expression.
// It returns { view, width, overflowX, targets: {count, under44: [...]}, text: {count, small: [...]} }:
//   - targets: every visible button, field, switch and link styled as a button, chip or tab whose box is under 44 px high (or, with
//     no text, under 44 px wide). A link inside running text is exempt, as in scripts/measure_tap_targets.js.
//   - text: every visible element with its own text under 12 px, or under 11 px for a label (upper-case, mono, a chip, a tab, a
//     table heading or a <label>).
//   - overflowX: the page scrolls sideways (the document is wider than the screen). An empty list and false are what "phone-first" means.
(() => {
  const TARGET_MIN = 44, TEXT_MIN = 12, LABEL_MIN = 11;
  const sel = 'button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [tabindex="0"]';
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !el.closest('[aria-hidden="true"]');
  };
  const round = (n) => Math.round(n * 10) / 10;
  const under44 = [];
  let count = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    if (el.tagName === 'A' && !/\bbtn\b|\bchip\b|help-hint|help-toc|\bnav\b/.test(el.className)) continue;
    if (el.tagName === 'INPUT' && /checkbox|radio/.test(el.type) && el.closest('label')) { count++; continue; }
    count++;
    const r = el.getBoundingClientRect();
    if (r.height < TARGET_MIN - 0.5 || (!el.textContent.trim() && r.width < TARGET_MIN - 0.5)) {
      under44.push({ text: (el.textContent || el.getAttribute('aria-label') || el.placeholder || '').trim().slice(0, 40),
        tag: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 50), w: round(r.width), h: round(r.height) });
    }
  }
  const small = [];
  let textCount = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  for (let el = walker.currentNode; el; el = walker.nextNode()) {
    if (!['SCRIPT', 'STYLE', 'SVG', 'PATH', 'NOSCRIPT'].includes(el.tagName.toUpperCase()) && !el.closest('svg') && visible(el)) {
      const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ');
      if (own) {
        textCount++;
        const cs = getComputedStyle(el);
        const size = parseFloat(cs.fontSize);
        const label = cs.textTransform === 'uppercase' || /label|eyebrow|chip|badge|mono|tab/.test(String(el.className)) ||
          ['LABEL', 'TH', 'LEGEND', 'BUTTON'].includes(el.tagName);
        if (size < (label ? LABEL_MIN : TEXT_MIN) - 0.05) {
          small.push({ text: own.slice(0, 40), tag: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 50), px: round(size), label });
        }
      }
    }
  }
  const view = (location.hash || '#').replace(/^#\/?/, '').split('?')[0];
  return { view, width: innerWidth, overflowX: document.documentElement.scrollWidth > innerWidth + 1,
    targets: { count, under44 }, text: { count: textCount, small } };
})()
