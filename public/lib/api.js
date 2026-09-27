// Client for the Vault API (/api/v1; see /api/docs). Same origin, session cookie.
// Lists are cursor-paginated with HAL `_links`; this client follows `next` links.
window.VaultApi = (() => {
  const V1 = '/api/v1';

  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  async function call(path, opts = {}) {
    if (opts.json !== undefined) {
      opts = { ...opts, body: JSON.stringify(opts.json), headers: { 'Content-Type': 'application/json' } };
      delete opts.json;
    }
    const resp = await fetch(path, { credentials: 'same-origin', ...opts });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try { const p = await resp.json(); msg = p.detail || p.title || msg; } catch {}
      throw new ApiError(resp.status, msg);
    }
    const type = resp.headers.get('content-type') || '';
    return type.includes('json') ? resp.json() : resp.text();
  }

  // Follow `next` links and return every item. The browser revalidates each page with its
  // ETag, so unchanged pages come back as cheap 304s.
  async function all(href, onPage) {
    const items = [];
    for (let url = href; url; ) {
      const page = await call(url);
      items.push(...page.items);
      if (onPage) onPage(items.length, page.total);
      url = page._links.next ? page._links.next.href : null;
    }
    return items;
  }
  const withLimit = (href, n = 500) => href + (href.includes('?') ? '&' : '?') + 'limit=' + n;

  const CONDITION = { mint: 'Mint', near_mint: 'NearMint', excellent: 'Excellent', good: 'Good',
    light_played: 'LightPlayed', played: 'Played', poor: 'Poor' };
  const LANGUAGE = { en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian', pt: 'Portuguese',
    ja: 'Japanese', ko: 'Korean', ru: 'Russian', zhs: 'Simplified Chinese', zht: 'Traditional Chinese' };

  // Assemble the shape the views were designed around from the paginated resources.
  async function loadCollection(base, onProgress) {
    const summary = await call(base);
    const L = summary._links;
    const [items, sets, timeline, history] = await Promise.all([
      all(withLimit(L.cards.href), onProgress),
      all(withLimit(L.sets.href)),
      call(L.timeline.href),
      all(withLimit(L.history.href)),
    ]);
    const cards = items.map((c) => ({
      key: c.id, n: c.name, s: c.set.code, sn: c.set.name, cn: c.collector_number, p: c.printing,
      c: CONDITION[c.condition] || c.condition, l: LANGUAGE[c.language] || c.language, q: c.quantity,
      pd: c.paid || 0, lo: c.price.low, mi: c.price.mid, mk: c.price.market,
      fd: c.acquired.first || '', ld: c.acquired.last || '', id: c.scryfall_id, fin: c.finish, src: c.price.source,
      href: c._links.self.href,
    }));
    const byName = {};
    for (const c of cards) {
      const k = c.n.toLowerCase();
      const b = byName[k] || (byName[k] = { name: c.n, total: 0, value: 0, entries: [] });
      b.total += c.q;
      b.value += c.mk * c.q;
      b.entries.push({ s: c.s, sn: c.sn, cn: c.cn, p: c.p, c: c.c, q: c.q, mk: c.mk });
    }
    const conditions = {};
    for (const [k, v] of Object.entries(summary.by_condition)) conditions[CONDITION[k] || k] = v;
    return {
      meta: {
        totalQty: summary.copies, totalPaid: summary.paid || 0, totalMarket: summary.market_value,
        uniqueEntries: summary.printings, uniqueSets: summary.sets, printings: summary.by_printing, conditions,
        generatedAt: summary.prices_as_of || summary.imported_at, importedAt: summary.imported_at,
        pricedFromScryfall: summary.priced_by_scryfall, costsHidden: summary.costs_hidden, sharedBy: summary.owner,
      },
      sets: sets.map((s) => ({ code: s.code, name: s.name, qty: s.copies, value: s.market_value, unique: s.printings })),
      timeline: timeline.months.map((m) => ({ month: m.month, qty: m.copies })),
      cards, byName, history,
    };
  }

  return {
    ApiError, all,
    providers: () => call('/api/auth/providers'),
    me: () => call(V1 + '/me'),
    collection: (onProgress) => loadCollection(V1 + '/collection', onProgress),
    imports: () => all(V1 + '/imports'),
    importCsv: (file) => {
      const body = new FormData();
      body.append('file', file);
      return call(V1 + '/imports', { method: 'POST', body });
    },
    archidektDeck: (id) => call(V1 + '/archidekt/decks/' + encodeURIComponent(id)),
    logout: () => call('/api/auth/logout', { method: 'POST' }),
    devLogin: () => call('/api/auth/dev-login', { method: 'POST' }),

    // account & GDPR
    updateName: (name) => call(V1 + '/me', { method: 'PATCH', json: { name } }),
    exportUrl: V1 + '/me/export',
    deleteAccount: () => call(V1 + '/me', { method: 'DELETE', json: { confirm: 'DELETE' } }),

    // decks
    parseDeck: (text) => call(V1 + '/decks/parse', { method: 'POST', json: { text } }),
    decks: () => all(V1 + '/decks'),
    deck: (id) => call(V1 + '/decks/' + id),
    saveDeck: (name, text, source_url) => call(V1 + '/decks', { method: 'POST', json: { name, text, source_url } }),
    deleteDeck: (id) => call(V1 + '/decks/' + id, { method: 'DELETE' }),

    // sharing
    shares: () => all(V1 + '/shares'),
    createShare: (kind, deck_id, show_costs) => call(V1 + '/shares', { method: 'POST', json: { kind, deck_id, show_costs } }),
    removeShare: (id) => call(V1 + '/shares/' + id, { method: 'DELETE' }),
    acceptInvite: (token) => call(V1 + '/shares/accept', { method: 'POST', json: { token } }),
    sharedWithMe: () => all(V1 + '/shared'),
    sharedCollection: (id, onProgress) => loadCollection(V1 + '/shared/' + id + '/collection', onProgress),
    sharedDeck: (id) => call(V1 + '/shared/' + id + '/deck'),
  };
})();
