// Local development against the digital twin universe (python -m twins, docs/twins.md): when
// the API root links to the twins, the browser's own Scryfall calls go there too. In production
// there is no such link and nothing changes.
(() => {
  const TWIN_HOSTS = ['api.scryfall.com', 'cards.scryfall.io', 'svgs.scryfall.io'];
  const realFetch = window.fetch.bind(window);
  const twins = realFetch('/api/v1', { credentials: 'same-origin' })
    .then((r) => r.json()).then((j) => (j._links && j._links.twins && j._links.twins.href) || null)
    .catch(() => null);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    let u = null;
    try { u = new URL(url, location.href); } catch {}
    if (u && TWIN_HOSTS.includes(u.host)) {
      const base = await twins;
      if (base) {
        const headers = new Headers((init && init.headers) || {});
        headers.set('X-Twin-Browser', '1'); // image and icon URLs in the answer point at the twins too
        return realFetch(`${base}/h/${u.host}${u.pathname}${u.search}`, { ...(init || {}), headers });
      }
    }
    return realFetch(input, init);
  };
})();

// Client for the Vault API (/api/v1; see /api/docs). Same origin, session cookie.
// Lists are cursor-paginated with HAL `_links`; this client follows `next` links.
window.VaultApi = (() => {
  const V1 = '/api/v1';

  class ApiError extends Error {
    // retryAfter: seconds from the answer's Retry-After header (429, 503), else null
    constructor(status, message, retryAfter = null) { super(message); this.status = status; this.retryAfter = retryAfter; }
  }

  // Retries: GETs, and POSTs carrying an Idempotency-Key, are retried on network errors and on
  // 408/425/429/5xx with exponential backoff (honouring Retry-After), so a flaky connection costs
  // one page, not the whole load.
  const RETRYABLE = new Set([408, 425, 429, 500, 502, 503, 504]);
  const MAX_RETRIES = 4;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const backoff = (attempt, resp) => {
    const after = resp && Number(resp.headers.get('retry-after'));
    return after > 0 ? Math.min(after, 30) * 1000 : Math.min(8000, 400 * 2 ** attempt) * (0.75 + Math.random() / 2);
  };
  const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2));

  async function call(path, opts = {}) {
    if (opts.json !== undefined) {
      opts = { ...opts, body: JSON.stringify(opts.json), headers: { ...(opts.headers || {}), 'Content-Type': 'application/json' } };
      delete opts.json;
    }
    const method = (opts.method || 'GET').toUpperCase();
    const retryable = method === 'GET' || !!(opts.headers && opts.headers['Idempotency-Key']);
    for (let attempt = 0; ; attempt++) {
      let resp;
      try {
        resp = await fetch(path, { credentials: 'same-origin', ...opts });
      } catch (e) {
        if (retryable && attempt < MAX_RETRIES) { await sleep(backoff(attempt)); continue; }
        throw new ApiError(0, 'Network error: ' + e.message);
      }
      if (!resp.ok) {
        // The session ended (expired, or signed out in another tab): the app shows the sign-in screen.
        if (resp.status === 401 && !path.startsWith('/api/auth')) window.dispatchEvent(new Event('vault:unauthorized'));
        if (retryable && RETRYABLE.has(resp.status) && attempt < MAX_RETRIES) { await sleep(backoff(attempt, resp)); continue; }
        let msg = 'HTTP ' + resp.status;
        try { const p = await resp.json(); msg = p.detail || p.title || msg; } catch {}
        throw new ApiError(resp.status, msg, Number(resp.headers.get('retry-after')) || null);
      }
      const type = resp.headers.get('content-type') || '';
      return type.includes('json') ? resp.json() : resp.text();
    }
  }
  // Creating things: safe to retry because the server replays the first answer for the same key.
  const create = (path, opts) => call(path, { method: 'POST', ...opts, headers: { ...(opts.headers || {}), 'Idempotency-Key': newKey() } });

  // Local copy in IndexedDB of the collection resources this browser has read (the summary, and
  // each page of cards, sets, names, stats, breakdowns, valuation, history it asked for), stored
  // by URL with the collection's `version`. While the version is unchanged, a resource is answered
  // from here without a request; offline, the last copy is shown. Everything is computed by the
  // server: this is only a cache of its answers. Cleared on sign-out and account deletion.
  // The copy is only a cache: it never holds up the app. An open that is blocked (another tab
  // holding the database), a transaction that aborts (e.g. the origin's quota is full) or one that
  // takes longer than STORE_WAIT_MS counts as a miss, and the server answers instead.
  const STORE_WAIT_MS = 3000;
  const localStore = (() => {
    let dbp = null;
    const open = () => dbp || (dbp = new Promise((resolve, reject) => {
      const req = indexedDB.open('the-vault', 1);
      req.onupgradeneeded = () => req.result.createObjectStore('kv');
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error('local copy blocked'));
    }).catch((e) => { dbp = null; throw e; }));  // try again next time
    const run = (mode, fn) => {
      const work = open().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction('kv', mode);
        const req = fn(tx.objectStore('kv'));
        tx.oncomplete = () => resolve(req.result);
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error('aborted'));
      }));
      let timer;
      const late = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('local copy too slow')), STORE_WAIT_MS); });
      return Promise.race([work, late]).finally(() => clearTimeout(timer));
    };
    return {
      get: (k) => run('readonly', (st) => st.get(k)).catch(() => undefined),
      set: (k, v) => run('readwrite', (st) => st.put(v, k)).catch(() => undefined),
      del: (k) => run('readwrite', (st) => st.delete(k)).catch(() => undefined),
      clear: () => run('readwrite', (st) => st.clear()).catch(() => undefined),
    };
  })();

  // Follow `next` links and return every item. The browser revalidates each page with its
  // ETag, so unchanged pages come back as cheap 304s.
  async function all(href, onPage) {
    const items = [], seen = new Set();
    for (let url = href; url; ) {
      seen.add(url);
      const page = await call(url);
      items.push(...page.items);
      if (onPage) onPage(items.length, page.total);
      url = nextHref(page, seen);
    }
    return items;
  }
  // The page's `next` link, or null when following it can't add anything: no link, an empty page,
  // or a link already followed in this list. Every paging loop goes through here, so a server
  // (or a stored copy) answering a cycle ends the list instead of looping forever. With the local
  // copy, a cycle would never reach the network: the browser would spin, growing the list.
  function nextHref(page, seen) {
    const next = page._links && page._links.next ? page._links.next.href : null;
    if (!next || !page.items || !page.items.length || seen.has(next)) return null;
    return next;
  }

  const CONDITION = { mint: 'Mint', near_mint: 'NearMint', excellent: 'Excellent', good: 'Good',
    light_played: 'LightPlayed', played: 'Played', poor: 'Poor' };
  const LANGUAGE = { en: 'English', es: 'Spanish', fr: 'French', de: 'German', it: 'Italian', pt: 'Portuguese',
    ja: 'Japanese', ko: 'Korean', ru: 'Russian', zhs: 'Simplified Chinese', zht: 'Traditional Chinese' };

  // The signed-in account, as the server names it in the readable `vault_account` cookie (an
  // opaque hash, set on sign-in, cleared on sign-out). Local copies are stored under it, so a
  // browser never shows one account's copy to another, even offline.
  const account = () => {
    const m = document.cookie.match(/(?:^|;\s*)vault_account=([0-9a-f]+)/);
    return m ? m[1] : null;
  };

  // FORMAT changes when what is stored does (3: server answers by URL; 2 held the whole
  // collection's items under `collection:…`), so older copies are dropped and refetched.
  const FORMAT = 3;
  const versions = {};  // collection base URL -> the version of its latest summary

  // GET one collection resource: from the local copy while the collection's version is unchanged,
  // else from the server (then stored). Offline, the last copy. Searches (`q=`) aren't stored.
  async function cachedGet(base, href) {
    const who = account();
    const version = versions[base];
    const key = who && version && !/[?&]q=/.test(href) ? `res:${who}:${href}` : null;
    if (key) {
      const saved = await localStore.get(key);
      if (saved && saved.format === FORMAT && saved.version === version) return saved.body;
    }
    try {
      const body = await call(href);
      if (key) localStore.set(key, { format: FORMAT, version, body });
      return body;
    } catch (e) {
      const saved = e.status === 0 && who ? await localStore.get(`res:${who}:${href}`) : null;
      if (saved && saved.format === FORMAT) return saved.body;
      throw e;
    }
  }

  const query = (params) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) if (v != null && v !== '') q.set(k, String(v));
    const s = q.toString();
    return s ? '?' + s : '';
  };

  // Open a collection (yours, or one shared with you): its summary, and a client for everything
  // else about it. Views ask the server for what they show (P&L, breakdowns, valuation, pages of
  // printings, …); nothing is computed in the browser.
  // A scope is the bucket and/or the tag the analytics are limited to (#130): { bucket: 3, tag: 'trade' }, either or both,
  // or {} for the whole inventory. The server limits every answer; the scope is part of each request's URL (so of its local
  // copy's key) and of the summary's `version`.
  const scopeOf = (s) => ({ ...(s && s.bucket ? { bucket: Number(s.bucket) } : {}), ...(s && s.tag ? { tag: String(s.tag) } : {}) });

  async function loadCollection(base, scope) {
    const who = account();
    const scoped = scopeOf(scope);
    const href = base + query(scoped);  // the summary of the selection; its `version` stands for every answer about it
    let summary, origin = {};
    try {
      summary = await call(href);
    } catch (e) {
      if (e.status === 401) await localStore.clear();
      const saved = e.status === 0 && who ? await localStore.get(`summary:${who}:${href}`) : null; // offline: last copy
      if (!saved || saved.format !== FORMAT) throw e;
      summary = saved.body;
      origin = { offline: true, savedAt: saved.savedAt };
    }
    localStore.del('collection:' + base);  // copies saved by older versions (formats 1 and 2)
    if (who && !origin.offline) {
      localStore.del(`collection:${who}:${base}`);
      localStore.set(`summary:${who}:${href}`, { format: FORMAT, savedAt: new Date().toISOString(), body: summary });
    }
    versions[href] = summary.version;
    return assemble(summary, base, origin, scoped, href);
  }

  // A collection item's `card` (Scryfall's data, kept in Postgres by the daily sync) in the shape
  // the views use for card data (Scryfall.slimCard's). Null until the sync has the printing.
  const money = (v) => (v == null ? null : v.toFixed(2));  // Scryfall writes prices as strings
  function slimCard(d) {
    if (!d) return null;
    const p = d.prices || {};
    return {
      id: d.scryfall_id, name: d.name, set: d.set_code, set_name: d.set_name, collector_number: d.collector_number,
      colors: d.colors || [], color_identity: d.color_identity || [], type_line: d.type_line || '',
      mana_cost: d.mana_cost || '', cmc: d.cmc, rarity: d.rarity,
      img_small: d.image.small || null, img_normal: d.image.normal || null,
      prices: { usd: money(p.usd), usd_foil: money(p.usd_foil), usd_etched: money(p.usd_etched),
        eur: money(p.eur), eur_foil: money(p.eur_foil), eur_etched: money(p.eur_etched) },
      scryfall_uri: d.scryfall_uri, artist: d.image.artist || null, oracle_text: d.oracle_text || '',
      power: d.power, toughness: d.toughness, loyalty: d.loyalty, layout: d.layout,
    };
  }

  // One printing (a /cards item, or a /stats one) in the shape the views were designed around.
  // `v` is the server's value (copies × price), `gain` its P&L over the copies with a known cost.
  function cardItem(c) {
    return {
      key: c.id, n: c.name, s: c.set.code, sn: c.set.name, cn: c.collector_number, p: c.printing,
      c: CONDITION[c.condition] || c.condition, l: LANGUAGE[c.language] || c.language, q: c.quantity,
      pd: c.paid, pq: c.paid_quantity, gain: c.gain ?? null, lo: c.price.low, mi: c.price.mid, mk: c.price.market,
      v: c.value, fd: (c.acquired && c.acquired.first) || '', ld: (c.acquired && c.acquired.last) || '',
      id: c.scryfall_id, fin: c.finish, src: c.price.source, href: c._links && c._links.self ? c._links.self.href : null,
      scry: slimCard(c.card),
      // tags are the owner's own: a shared collection has none (undefined). `tagsDetail` says who wrote each (#128).
      tags: c.tags, tagsDetail: c.tags_detail,
    };
  }
  const setItem = (s) => ({ code: s.code, name: s.name, qty: s.copies, value: s.market_value, unique: s.printings,
    colors: s.colors || {}, released: s.released_at || null });

  // A page of a list, with `more()` for the next page (null on the last).
  function pager(key, mapItem) {
    const wrap = (seen) => (page) => {
      const next = nextHref(page, seen);
      return {
        ...page, items: page.items.map(mapItem),
        more: next ? () => { seen.add(next); return cachedGet(key, next).then(wrap(seen)); } : null,
      };
    };
    return (href) => cachedGet(key, href).then(wrap(new Set([href])));
  }
  // Up to `n` items of a list (every item when n is Infinity), following `next` links.
  async function upTo(first, n) {
    let page = await first;
    const items = [...page.items];
    while (items.length < n && page.more) {
      page = await page.more();
      items.push(...page.items);
    }
    return { ...page, items: items.slice(0, n) };
  }

  // Everything a view can ask about one collection; `base` is /api/v1/collection or
  // /api/v1/shared/{id}/collection. Each call is one server resource (docs/api.md). With a `scope` (the bucket and/or tag, #130)
  // every call passes it, so each figure is the server's for that selection; `key` is the address of the selection's summary,
  // whose version decides when a stored answer is stale.
  function collectionApi(base, scope = {}, key = base) {
    const cardsPage = pager(key, cardItem);
    const setsPage = pager(key, setItem);
    const plainPage = pager(key, (x) => x);
    const inScope = (params) => ({ ...params, ...scope });
    const scopeQs = query(scope);
    return {
      base, scope,
      // The same questions about a bucket and/or a tag: loads that selection's summary (`meta`, as `collection()` does) and the
      // client that asks about it. Own collection only (a shared one answers 404).
      scoped: (next) => loadCollection(base, next),
      // printings, one page: q, set, name, printing, finish, condition, limit; sort: name, -name,
      // -value, value, -quantity, set, -acquired, acquired. The page has `total` and `value_total`.
      cards: (params) => cardsPage(base + '/cards' + query(inScope(params))),
      // the most valuable printing of a card name (to open a card from a per-name list)
      topPrinting: (name) => cardsPage(base + '/cards' + query(inScope({ name: name.split(' // ')[0], sort: '-value', limit: 1 })))
        .then((p) => p.items[0] || null),
      // every set: q; sort: -value, value, -quantity, quantity, -unique, unique, name, code, release, -release
      sets: (params) => upTo(setsPage(base + '/sets' + query(inScope({ ...params, limit: 500 }))), Infinity),
      // one row per card name, `n` rows at most: sort, colors (array of W U B R G M C), type, min_value
      names: (params = {}, n = 100) => upTo(plainPage(base + '/names' + query(inScope({
        ...params, colors: params.colors && params.colors.length ? params.colors.join(',') : null,
        limit: Math.min(500, n) }))), n),
      stats: (limit = 8) => cachedGet(key, base + '/stats' + query(inScope({ limit }))).then((s) => ({
        ...s,
        most_valuable: (s.most_valuable || []).map(cardItem),
        biggest_gains: (s.biggest_gains || []).map(cardItem),
        biggest_losses: (s.biggest_losses || []).map(cardItem),
      })),
      breakdowns: () => cachedGet(key, base + '/breakdowns' + scopeQs),
      valuation: () => cachedGet(key, base + '/valuation' + scopeQs),
      timeline: () => cachedGet(key, base + '/timeline' + scopeQs).then((t) => t.months),
      history: () => upTo(plainPage(base + '/history' + query(inScope({ limit: 500 }))), Infinity).then((p) => p.items),
      // The Lab (docs/lab-design.md). Own account only: a shared collection has no such routes (404). Not kept in the local copy:
      // spare copies depend on the saved decks as well as on the collection's version, and the server's ETag already makes a
      // repeated read cheap. `spare`/`pnl` give the first page (`limit` items), `history` the days since `since`, with the
      // server's market-only `summary`; `follow` reads a `next` link; `card` opens one printing in the card drawer's shape.
      spare: (limit) => call(base + '/spare' + query({ limit })),
      pnl: (side, limit) => call(base + '/pnl' + query({ side, limit })),
      historySince: (since) => call(base + '/history' + query({ since, limit: 500 })),
      follow: (href) => call(href),
      card: (href) => cachedGet(key, href).then(cardItem),
    };
  }

  // The summary in the shape the views use (`meta`), plus the client for the rest (`api`).
  function assemble(summary, base, origin, scope = {}, key = base) {
    const conditions = {};
    for (const [k, v] of Object.entries(summary.by_condition)) conditions[CONDITION[k] || k] = v;
    return {
      meta: {
        totalQty: summary.copies, totalPaid: summary.paid || 0, totalMarket: summary.market_value,
        uniqueEntries: summary.printings, uniqueSets: summary.sets, uniqueNames: summary.cards,
        printings: summary.by_printing, conditions,
        generatedAt: summary.prices_as_of || summary.imported_at, pricesAsOf: summary.prices_as_of || null,
        importedAt: summary.imported_at,
        pricedFromScryfall: summary.priced_by_scryfall, costsHidden: summary.costs_hidden, sharedBy: summary.owner,
        version: summary.version, source: summary.source, offline: !!origin.offline, savedAt: origin.savedAt,
        // P&L over the copies with a known cost, computed by the server (null when costs are hidden)
        pnl: summary.pnl ?? null, pnlPct: summary.pnl_pct ?? null, knownCostPaid: summary.known_cost_paid ?? null,
        knownCostMarket: summary.known_cost_market ?? null, knownCostCopies: summary.known_cost_copies ?? null,
        unknownCostCopies: summary.unknown_cost_copies ?? null,
      },
      scope,  // the bucket and/or tag these figures are limited to ({} = the whole inventory)
      api: collectionApi(base, scope, key),
    };
  }

  // Refresh your collection's card data and today's prices on the server, one chunk per call,
  // until nothing remains (POST /collection/refresh). The server talks to Scryfall; the browser
  // only reports progress. 503 (Scryfall down) and 429 (too many calls) are waited out as
  // Retry-After says, at most `maxWaits` times in a row; then the error is thrown. Stores nothing.
  async function refreshCollection({ force = false, onProgress, wait = sleep, maxWaits = 5 } = {}) {
    let cursor = null, waits = 0;
    for (;;) {
      let r;
      try {
        r = await call(V1 + '/collection/refresh', { method: 'POST', json: cursor ? { cursor, force } : { force } });
      } catch (e) {
        if ((e.status === 503 || e.status === 429) && waits < maxWaits) {
          waits++;
          await wait(Math.min(e.retryAfter || 30, 120) * 1000);
          continue;
        }
        throw e;
      }
      waits = 0;
      if (onProgress) onProgress({ done: r.done, total: r.total, remaining: r.remaining });
      // Finished, or (defensively) a call that moved nothing forward: stop rather than spin.
      // A cursor handed back unchanged would ask for the same chunk again: stop there too.
      if (r.remaining === 0 || !r.cursor || r.cursor === cursor || (!r.processed && !r.unavailable)) return r;
      cursor = r.cursor;
    }
  }

  // -- passkeys (WebAuthn) ------------------------------------------------------------------
  // The server speaks WebAuthn JSON (base64url). Newer browsers convert natively
  // (parse*OptionsFromJSON / credential.toJSON()); older ones use these helpers.
  const fromB64u = (s) => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)),
    (c) => c.charCodeAt(0)).buffer;
  const toB64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const creationOptions = (o) => (PublicKeyCredential.parseCreationOptionsFromJSON
    ? PublicKeyCredential.parseCreationOptionsFromJSON(o)
    : { ...o, challenge: fromB64u(o.challenge), user: { ...o.user, id: fromB64u(o.user.id) },
        excludeCredentials: (o.excludeCredentials || []).map((c) => ({ ...c, id: fromB64u(c.id) })) });
  const requestOptions = (o) => (PublicKeyCredential.parseRequestOptionsFromJSON
    ? PublicKeyCredential.parseRequestOptionsFromJSON(o)
    : { ...o, challenge: fromB64u(o.challenge),
        allowCredentials: (o.allowCredentials || []).map((c) => ({ ...c, id: fromB64u(c.id) })) });
  function credentialJSON(c) {
    if (typeof c.toJSON === 'function') return c.toJSON();
    const r = c.response;
    const out = { id: c.id, rawId: toB64u(c.rawId), type: c.type, authenticatorAttachment: c.authenticatorAttachment,
      clientExtensionResults: c.getClientExtensionResults ? c.getClientExtensionResults() : {},
      response: { clientDataJSON: toB64u(r.clientDataJSON) } };
    if (r.attestationObject) {
      out.response.attestationObject = toB64u(r.attestationObject);
      out.response.transports = r.getTransports ? r.getTransports() : [];
    } else {
      out.response.authenticatorData = toB64u(r.authenticatorData);
      out.response.signature = toB64u(r.signature);
      out.response.userHandle = r.userHandle ? toB64u(r.userHandle) : null;
    }
    return out;
  }
  function deviceName() {
    const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    const ua = navigator.userAgent;
    if (/iPhone/.test(ua)) return 'iPhone';
    if (/iPad/.test(ua)) return 'iPad';
    if (/Android/.test(ua)) return 'Android';
    if (/Mac/.test(p)) return 'Mac';
    if (/Win/.test(p)) return 'Windows PC';
    if (/Linux/.test(p)) return 'Linux';
    return 'Passkey';
  }
  const PK = '/api/auth/passkey';
  const passkeys = {
    supported: () => !!window.PublicKeyCredential && !!navigator.credentials,
    signUp: async (name) => {
      const o = await call(PK + '/signup/options', { method: 'POST', json: { name: name || null } });
      const c = await navigator.credentials.create({ publicKey: creationOptions(o) });
      return call(PK + '/signup/verify', { method: 'POST', json: { credential: credentialJSON(c), name: deviceName() } });
    },
    signIn: async () => {
      const o = await call(PK + '/login/options', { method: 'POST', json: {} });
      const c = await navigator.credentials.get({ publicKey: requestOptions(o) });
      return call(PK + '/login/verify', { method: 'POST', json: { credential: credentialJSON(c) } });
    },
    add: async () => {
      const o = await call(PK + '/register/options', { method: 'POST', json: {} });
      const c = await navigator.credentials.create({ publicKey: creationOptions(o) });
      return call(PK + '/register/verify', { method: 'POST', json: { credential: credentialJSON(c), name: deviceName() } });
    },
    list: () => all(V1 + '/me/passkeys'),
    remove: (id) => call(V1 + '/me/passkeys/' + id, { method: 'DELETE' }),
    // Friendly text for the browser's WebAuthn errors.
    explain: (e) => (e && e.name === 'NotAllowedError' ? 'Cancelled, or no passkey was chosen.'
      : e && e.name === 'InvalidStateError' ? 'This device already has a passkey for your account.'
      : (e && e.message) || String(e)),
  };

  // Every way to sign in (passkeys and linked providers), for "Sign out everywhere" (#347): the server decides what counts as recent.
  const signInMethods = {
    all: () => call(V1 + '/me/sign-in-methods?limit=500'),
    // Ends every other browser's session and re-issues this browser's cookie (the first step of Sign out everywhere).
    signOutOthers: () => call('/api/auth/sign-out-others', { method: 'POST' }),
    removeRecent: () => call(V1 + '/me/sign-in-methods/recent', { method: 'DELETE' }),
    remove: (m) => call(V1 + (m.kind === 'passkey' ? '/me/passkeys/' : '/me/identities/') + m.id, { method: 'DELETE' }),
  };

  return {
    ApiError, all, passkeys, signInMethods,
    providers: () => call('/api/auth/providers'),
    me: () => call(V1 + '/me'),
    collection: () => loadCollection(V1 + '/collection'),
    // server-side refresh of your printings' card data and prices: {onProgress, force}
    refreshCollection,
    imports: () => all(V1 + '/imports'),
    // The newest entries of the import history (files and changes made through an assistant), each with `undoable` when it
    // is the one change that can be undone now. Undo asks first (preview: what it will change, and a confirmation), then
    // applies exactly that preview: the same two steps an assistant takes (POST /collection/changes/undo).
    recentImports: (limit = 20) => call(V1 + '/imports?limit=' + limit).then((p) => p.items),
    undoPreview: () => call(V1 + '/collection/changes/undo', { method: 'POST', json: {} }),
    undoApply: (confirmation) => call(V1 + '/collection/changes/undo', { method: 'POST', json: { confirmation } }),
    importCsv: (file) => {
      const body = new FormData();
      body.append('file', file);
      return create(V1 + '/imports', { body });
    },
    // Import into one bucket (#122, #124): the file is compared with that bucket's copies only and replaces only them. The preview
    // changes nothing and names the bucket and what it leaves alone (`bucket`, `untouched`); importInto applies the same file, with a
    // fresh Idempotency-Key for each confirm. 400 unreadable file, 404 bucket, 409 conflict, 429 limit: the server's words.
    importPreview: (file, bucketId) => {
      const body = new FormData();
      body.append('file', file);
      return call(V1 + '/imports/preview?bucket_id=' + encodeURIComponent(bucketId), { method: 'POST', body });
    },
    importInto: (file, bucketId) => {
      const body = new FormData();
      body.append('file', file);
      return create(V1 + '/imports?bucket_id=' + encodeURIComponent(bucketId), { body });
    },
    // Reset the collection (#129): the preview changes nothing and says what would go; resetApply sends the preview's confirmation with
    // the same options (a fresh Idempotency-Key for each confirm). The undo of the latest reset is kept 7 days: resetInfo is null when
    // there is none. 404 bucket, 409 stale or changed collection, 422, 429: the server's words.
    resetPreview: (options) => call(V1 + '/collection/reset', { method: 'POST', json: options }),
    resetApply: (options, confirmation, typed) => create(V1 + '/collection/reset', { json: { ...options, confirmation, typed } }),
    resetInfo: () => call(V1 + '/collection/reset').catch((e) => { if (e.status === 404) return null; throw e; }),
    resetUndo: () => create(V1 + '/collection/reset/undo', { json: { confirm: true } }),
    // buckets (#125): the places copies live in; a move writes the target's name as the copies' folder and is recorded as a change
    buckets: () => all(V1 + '/collection/buckets'),
    createBucket: (name) => create(V1 + '/collection/buckets', { json: { name } }),
    renameBucket: (id, name) => call(V1 + '/collection/buckets/' + id, { method: 'PATCH', json: { name } }),
    deleteBucket: (id) => call(V1 + '/collection/buckets/' + id, { method: 'DELETE' }),
    moveCards: (from, to, lines, confirm) => create(V1 + '/collection/buckets/' + from + '/move', { json: { to, lines, confirm } }),
    cardDetail: (href) => call(href),
    // tags (#128): labels on cards, the person's own. A tag is on the card, so any printing's id tags it; more than 25 cards come back
    // `applied: false` and are applied only when sent again with confirm.
    tags: () => all(V1 + '/collection/tags'),
    tagDetail: (tag) => call(V1 + '/collection/tags/' + encodeURIComponent(tag)),
    tagCards: (tag, ids, confirm) => create(V1 + '/collection/tags/' + encodeURIComponent(tag) + '/cards',
      { json: confirm ? { card_ids: ids, confirm: true } : { card_ids: ids } }),
    untagCards: (tag, ids, confirm) => create(V1 + '/collection/tags/' + encodeURIComponent(tag) + '/cards/remove',
      { json: confirm ? { card_ids: ids, confirm: true } : { card_ids: ids } }),
    renameTag: (tag, name) => call(V1 + '/collection/tags/' + encodeURIComponent(tag), { method: 'PATCH', json: { name } }),
    deleteTag: (tag) => call(V1 + '/collection/tags/' + encodeURIComponent(tag), { method: 'DELETE' }),
    // what the person and their assistants keep on a card (vault_metadata): read only in the app
    cardMetadata: (id) => call(V1 + '/collection/cards/' + encodeURIComponent(id) + '/metadata'),
    archidektDeck: (id, refresh = false) => call(V1 + '/archidekt/decks/' + encodeURIComponent(id) + '?detail=cards' + (refresh ? '&refresh=true' : '')),
    logout: (everywhere = false) => call('/api/auth/logout' + (everywhere ? '?everywhere=true' : ''), { method: 'POST' })
      .finally(() => localStore.clear()),
    clearLocalData: () => localStore.clear(),
    devLogin: () => call('/api/auth/dev-login', { method: 'POST' }),

    // account & GDPR
    updateName: (name) => call(V1 + '/me', { method: 'PATCH', json: { name } }),
    exportUrl: V1 + '/me/export',
    exportFormats: () => call(V1 + '/collection/exports').then((r) => r.items),
    deleteAccount: () => call(V1 + '/me', { method: 'DELETE', json: { confirm: 'DELETE' } }),

    // agents: personal access tokens
    tokens: () => all(V1 + '/me/tokens'),
    createToken: (name, scopes) => create(V1 + '/me/tokens', { json: { name, scopes } }),
    deleteToken: (id) => call(V1 + '/me/tokens/' + id, { method: 'DELETE' }),
    apps: () => all(V1 + '/me/apps'),
    disconnectApp: (id) => call(V1 + '/me/apps/' + id, { method: 'DELETE' }),

    // decks
    parseDeck: (text) => call(V1 + '/decks/parse', { method: 'POST', json: { text } }),
    // a decklist checked against your collection, priced by the server (missing cost, owned printings)
    deckCoverage: (text) => call(V1 + '/decks/coverage', { method: 'POST', json: { text } }),
    decks: (summary) => all(V1 + '/decks' + (summary ? '?summary=true' : '')), // summary: owned / missing / cost to finish of each
    deck: (id) => call(V1 + '/decks/' + id),
    saveDeck: (name, text, source_url, source_author) => create(V1 + '/decks', { json: { name, text, source_url, source_author } }),
    // A copy saved before authors were kept: record the author it shows now that the source answered.
    // The saved deck held by the view is updated too, so a later offline fallback in this session credits the author.
    rememberDeckAuthor: (saved, author) => (saved && saved.source_url && !saved.source_author && author && author.trim()
      ? create(V1 + '/decks/' + saved.id + '/source-author', { json: { source_url: saved.source_url, source_author: author } })
        // Only while the deck still has this link: if another tab moved it to another deck meanwhile, that
        // deck's author isn't this link's author.
        .then((deck) => { if (deck && deck.source_author && deck.source_url === saved.source_url) saved.source_author = deck.source_author; return deck; })
        .catch(() => null)
      : Promise.resolve(null)),
    updateDeck: (id, name, text, source_url, source_author) => call(V1 + '/decks/' + id, { method: 'PUT', json: { name, text, source_url, source_author } }),
    deleteDeck: (id) => call(V1 + '/decks/' + id, { method: 'DELETE' }),
    // a saved deck's versions (at most 20), and the open: records the list the page showed if its cards changed and says what
    // changed since the person last looked
    deckVersions: (id) => call(V1 + '/decks/' + id + '/versions'),
    deckVersion: (id, versionId) => call(V1 + '/decks/' + id + '/versions/' + versionId),
    deckSeen: (id, text) => call(V1 + '/decks/' + id + '/seen', { method: 'POST', json: text ? { text } : {} }),
    // deck analysis, computed by the server from the card catalog (each answer is { result, provenance })
    deckStats: (text) => call(V1 + '/decks/stats', { method: 'POST', json: { text } }),
    // how the list plays (#138): the odds over `games` games and `samples` of them turn by turn; the same seed gives the same answer, and
    // without one the server takes it from the list. A POST without an Idempotency-Key is not retried, so a refusal (429) reaches the page.
    deckSimulate: (text, { format, on_the_play, turns, games, samples, seed }) => call(V1 + '/decks/simulate', { method: 'POST',
      json: { text, format, on_the_play, turns, games, samples, ...(seed != null ? { seed } : {}) } }),
    // the deck page's change flow (#163): a saved deck with cuts and adds checked, applying nothing; `result.deck_text` is the list the
    // check ran on, which updateDeck then saves. A card name is looked up in the card catalog (an exact name, else near names).
    deckValidateChanges: (deck_id, format, cuts, adds) => call(V1 + '/decks/validate-changes', { method: 'POST', json: { deck_id, format, cuts, adds, include_text: true } }),
    catalogCard: (name) => call(V1 + '/catalog/cards' + query({ name })),
    deckLegality: (text, format) => call(V1 + '/decks/legality', { method: 'POST', json: { text, format } }),
    deckUpgrades: (text, format, budget_usd, roles) => call(V1 + '/decks/upgrades', { method: 'POST', json: { text, format, budget_usd, use_collection: true, ...(roles ? { roles } : {}) } }),
    deckCombos: (text) => call(V1 + '/decks/combos', { method: 'POST', json: { text } }),
    deckShopping: (text) => call(V1 + '/decks/shopping-list', { method: 'POST', json: { text } }),
    // can the saved decks all be built at once (the Lab's Buy section, docs/deck-independence.md): the root (summary, counts,
    // the first page of decks), the purchases (cheapest first, paged; follow `_links.next` with `follow`), and the paste-ready
    // shopping list, whose pages are joined here until the server has no `next`
    overlap: () => call(V1 + '/decks/overlap'),
    overlapPurchases: (limit) => call(V1 + '/decks/overlap/purchases' + query({ limit })),
    follow: (href) => call(href),
    overlapText: async () => {
      const chunks = [], seen = new Set();
      for (let url = V1 + '/decks/overlap/purchases?format=text&limit=500'; url && !seen.has(url); ) {
        seen.add(url);
        const page = await call(url);
        if (page.text) chunks.push(page.text);
        url = page._links && page._links.next ? page._links.next.href : null;
      }
      return chunks.join('\n');
    },

    // the deck ideas lab (docs/deck-ideas-lab-design.md): a saved deck in role lanes (`lane`, `limit` and `cursor` page one lane;
    // `include_combos` also asks Commander Spellbook), and the owned cards that could stand in for one card of it
    deckIdeas: (id, params) => call(V1 + '/decks/' + encodeURIComponent(id) + '/ideas' + query(params)),
    deckAlternatives: (id, card, params) => call(V1 + '/decks/' + encodeURIComponent(id) + '/ideas/alternatives' + query({ card, ...params })),

    // sharing
    shares: () => all(V1 + '/shares'),
    createShare: (kind, deck_id, show_costs) => create(V1 + '/shares', { json: { kind, deck_id, show_costs } }),
    removeShare: (id) => call(V1 + '/shares/' + id, { method: 'DELETE' }),
    acceptInvite: (token) => create(V1 + '/shares/accept', { json: { token } }),  // keyed: a lost answer can be retried
    sharedWithMe: () => all(V1 + '/shared'),
    sharedCollection: (id) => loadCollection(V1 + '/shared/' + id + '/collection'),
    sharedDeck: (id) => call(V1 + '/shared/' + id + '/deck'),
  };
})();
