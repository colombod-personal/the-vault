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
    constructor(status, message) { super(message); this.status = status; }
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
        if (retryable && RETRYABLE.has(resp.status) && attempt < MAX_RETRIES) { await sleep(backoff(attempt, resp)); continue; }
        let msg = 'HTTP ' + resp.status;
        try { const p = await resp.json(); msg = p.detail || p.title || msg; } catch {}
        throw new ApiError(resp.status, msg);
      }
      const type = resp.headers.get('content-type') || '';
      return type.includes('json') ? resp.json() : resp.text();
    }
  }
  // Creating things: safe to retry because the server replays the first answer for the same key.
  const create = (path, opts) => call(path, { method: 'POST', ...opts, headers: { ...(opts.headers || {}), 'Idempotency-Key': newKey() } });

  // Local copy of collections in IndexedDB, keyed by the collection's `version`: while the
  // version is unchanged, opening the vault costs one small request. Offline, the last copy is
  // shown. Cleared on sign-out and account deletion.
  const localStore = (() => {
    let dbp = null;
    const open = () => dbp || (dbp = new Promise((resolve, reject) => {
      const req = indexedDB.open('the-vault', 1);
      req.onupgradeneeded = () => req.result.createObjectStore('kv');
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }));
    const run = async (mode, fn) => {
      const db = await open();
      return new Promise((resolve, reject) => {
        const tx = db.transaction('kv', mode);
        const req = fn(tx.objectStore('kv'));
        tx.oncomplete = () => resolve(req.result);
        tx.onerror = () => reject(tx.error);
      });
    };
    return {
      get: (k) => run('readonly', (st) => st.get(k)).catch(() => undefined),
      set: (k, v) => run('readwrite', (st) => st.put(v, k)).catch(() => undefined),
      clear: () => run('readwrite', (st) => st.clear()).catch(() => undefined),
    };
  })();

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

  // Load a collection: from the local copy when its version is current, else page by page.
  async function loadCollection(base, onProgress) {
    const key = 'collection:' + base;
    let summary;
    try {
      summary = await call(base);
    } catch (e) {
      if (e.status === 401) await localStore.clear();
      const saved = e.status === 0 ? await localStore.get(key) : null; // offline: last copy
      if (saved) return assemble(saved, { offline: true, savedAt: saved.savedAt });
      throw e;
    }
    const saved = await localStore.get(key);
    if (saved && saved.version && saved.version === summary.version) {
      return assemble({ ...saved, summary }, { fromCache: true, savedAt: saved.savedAt });
    }
    const L = summary._links;
    const [items, sets, timeline, history] = await Promise.all([
      all(withLimit(L.cards.href), onProgress),
      all(withLimit(L.sets.href)),
      call(L.timeline.href),
      all(withLimit(L.history.href)),
    ]);
    const fresh = { version: summary.version, savedAt: new Date().toISOString(), summary, items, sets, timeline, history };
    localStore.set(key, fresh);
    return assemble(fresh, {});
  }

  // Assemble the shape the views were designed around from the paginated resources.
  function assemble({ summary, items, sets, timeline, history }, origin) {
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
        version: summary.version, source: summary.source, offline: !!origin.offline, fromCache: !!origin.fromCache, savedAt: origin.savedAt,
      },
      sets: sets.map((s) => ({ code: s.code, name: s.name, qty: s.copies, value: s.market_value, unique: s.printings })),
      timeline: timeline.months.map((m) => ({ month: m.month, qty: m.copies })),
      cards, byName, history,
    };
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

  return {
    ApiError, all, passkeys,
    providers: () => call('/api/auth/providers'),
    me: () => call(V1 + '/me'),
    collection: (onProgress) => loadCollection(V1 + '/collection', onProgress),
    imports: () => all(V1 + '/imports'),
    importCsv: (file) => {
      const body = new FormData();
      body.append('file', file);
      return create(V1 + '/imports', { body });
    },
    archidektDeck: (id) => call(V1 + '/archidekt/decks/' + encodeURIComponent(id)),
    logout: () => call('/api/auth/logout', { method: 'POST' }).finally(() => localStore.clear()),
    clearLocalData: () => localStore.clear(),
    devLogin: () => call('/api/auth/dev-login', { method: 'POST' }),

    // account & GDPR
    updateName: (name) => call(V1 + '/me', { method: 'PATCH', json: { name } }),
    exportUrl: V1 + '/me/export',
    exportFormats: () => call(V1 + '/collection/exports').then((r) => r.items),
    deleteAccount: () => call(V1 + '/me', { method: 'DELETE', json: { confirm: 'DELETE' } }),

    // agents: personal access tokens
    tokens: () => all(V1 + '/me/tokens'),
    createToken: (name, scopes) => call(V1 + '/me/tokens', { method: 'POST', json: { name, scopes } }),
    deleteToken: (id) => call(V1 + '/me/tokens/' + id, { method: 'DELETE' }),

    // decks
    parseDeck: (text) => call(V1 + '/decks/parse', { method: 'POST', json: { text } }),
    decks: () => all(V1 + '/decks'),
    deck: (id) => call(V1 + '/decks/' + id),
    saveDeck: (name, text, source_url) => create(V1 + '/decks', { json: { name, text, source_url } }),
    deleteDeck: (id) => call(V1 + '/decks/' + id, { method: 'DELETE' }),

    // sharing
    shares: () => all(V1 + '/shares'),
    createShare: (kind, deck_id, show_costs) => create(V1 + '/shares', { json: { kind, deck_id, show_costs } }),
    removeShare: (id) => call(V1 + '/shares/' + id, { method: 'DELETE' }),
    acceptInvite: (token) => call(V1 + '/shares/accept', { method: 'POST', json: { token } }),
    sharedWithMe: () => all(V1 + '/shared'),
    sharedCollection: (id, onProgress) => loadCollection(V1 + '/shared/' + id + '/collection', onProgress),
    sharedDeck: (id) => call(V1 + '/shared/' + id + '/deck'),
  };
})();
