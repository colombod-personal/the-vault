// What the "Where to buy" menu says about the server's answer (docs/where-to-buy-design.md, #212). The links, their order for a saved
// country and the stores a person typed are the server's (GET /buy/menu); this file words the menu's lines and, only while no country is
// saved, picks which of the server's four shop orders to show from the browser's own language. That choice is made here on purpose: the
// language never leaves the browser, and nothing is stored (design section 4.5). Nothing here builds a shop address or reads a position.
// tests/js/buy.test.mjs and tests/test_buy_menu_page.py read the same sentences.
window.VaultBuy = (() => {
  const REGION = /^[A-Za-z]{2,3}[-_]([A-Za-z]{2})(?:[-_]|$)/;
  // "en-GB" -> "GB"; a language without a region ("en", "fr") -> null; anything that is not two letters -> null.
  function regionOfLanguage(lang) {
    const m = REGION.exec(String(lang || ''));
    if (m) return m[1].toUpperCase();
    const script = /^[A-Za-z]{2,3}[-_][A-Za-z]{4}[-_]([A-Za-z]{2})(?:[-_]|$)/.exec(String(lang || ''));  // zh-Hant-TW
    return script ? script[1].toUpperCase() : null;
  }
  const regionKey = (menu, region) => (region === 'GB' || region === 'US' ? region : (menu.europe_countries || []).includes(region) ? 'EU' : 'other');

  // The menu to show: the server's order when a country is saved, else the order of the browser's region (when it has one and the
  // server sent the four orders), else the server's neutral one. `source` says which, so the menu can say so.
  function arrange(menu, region) {
    const all = [...menu.shops, ...menu.more_shops];
    const byId = Object.fromEntries(all.map((s) => [s.id, s]));
    if (menu.country || !region || !menu.region_orders) {
      return { shops: menu.shops, more: menu.more_shops, source: menu.country ? 'account' : 'neutral', region: menu.country || null };
    }
    const key = regionKey(menu, region);
    const first = (menu.region_orders[key] || []).filter((id) => byId[id]);
    return { shops: first.map((id) => byId[id]), more: all.filter((s) => !first.includes(s.id)), source: 'browser', region, key };
  }

  const regionName = (code, names) => {
    try { return new Intl.DisplayNames(['en'], { type: 'region' }).of(code) || code; } catch { return (names && names[code]) || code; }
  };

  // The line at the top of the menu: whose order this is, and the way to change it.
  function shopsLine(menu, arranged) {
    if (arranged.source === 'account') return `Shops for ${menu.country_name || menu.country}.`;
    if (arranged.source === 'browser') return `Ordered for ${regionName(arranged.region)} from your browser's language. Nothing is stored.`;
    return 'Pick where you buy to see the right shops first.';
  }
  const changeLabel = (arranged) => (arranged.source === 'account' ? 'Change' : 'Set where you buy');

  const storeNote = (row) => (row.opens === 'the store\'s page' ? 'opens its page' : 'searches for the card');
  const storeLabel = (row) => `My store: ${row.name}`;
  const LOCATOR_LABEL = 'Find a store near me (Wizards\' official locator)';
  const LOCATOR_NOTE = 'Opens its page: type your town or postcode there. The Vault is not told.';
  const FOOTER_SHORT = 'Plain links, no prices. Not affiliated with any shop.';

  const countryOption = (c) => ({ value: c.code, label: c.name });
  // The countries as a list for the picker, with "not set" first.
  const countryChoices = (items) => [{ value: '', label: 'Not set' }, ...items.map(countryOption)];

  // Errors the settings form shows come from the server's own sentences (it validates); this only fills in the blank form.
  const emptyStore = () => ({ name: '', url: '', search_url: '' });
  // A store left completely blank (Add a store, then changed their mind) is not sent; a half-filled one is, and the server says what is missing.
  const blankStore = (s) => !String(s.name || '').trim() && !String(s.url || '').trim() && !String(s.search_url || '').trim();
  const storesForSave = (stores) => stores.filter((s) => !blankStore(s))
    .map((s) => ({ name: s.name, url: s.url, ...(s.search_url && s.search_url.trim() ? { search_url: s.search_url.trim() } : {}) }));

  return { regionOfLanguage, regionKey, arrange, regionName, shopsLine, changeLabel, storeNote, storeLabel, LOCATOR_LABEL, LOCATOR_NOTE, FOOTER_SHORT,
    countryChoices, emptyStore, storesForSave };
})();
