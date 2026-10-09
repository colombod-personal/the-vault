// public/lib/api.js against a stand-in for the Vault API: the server-side price refresh loop and
// the collection client (values, P&L and pages come from the server, nothing is computed here).
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const json = (status, body, headers = {}) => ({
  ok: status >= 200 && status < 300, status,
  headers: { get: (k) => headers[k.toLowerCase()] ?? (k.toLowerCase() === 'content-type' ? 'application/json' : null) },
  json: async () => body, text: async () => JSON.stringify(body),
});

// `route(url, init)` answers each request; returns the client, the requests made and every
// localStorage write.
function load(route, { cookie = '', indexedDB } = {}) {
  const calls = [], writes = [], headers = [];
  let keys = 0;
  const sandbox = {
    fetch: async (url, init = {}) => {
      if (url === '/api/v1' && !init.method) return json(200, { _links: {} });  // the twins check at start-up
      // a JSON body is recorded parsed; an uploaded file (FormData) as its fields, with the file's name
      const body = typeof init.body === 'string' ? JSON.parse(init.body)
        : init.body ? Object.fromEntries([...init.body].map(([k, v]) => [k, v.name || v])) : null;
      calls.push({ url, method: init.method || 'GET', body });
      headers.push(init.headers || {});
      return route(url, init);
    },
    localStorage: {
      getItem: () => null, setItem: (k, v) => writes.push(['set', k, v]), removeItem: (k) => writes.push(['remove', k]),
      clear: () => writes.push(['clear']), key: () => null, length: 0,
    },
    location: { href: 'https://vault.test/' }, document: { cookie }, indexedDB,
    crypto: { randomUUID: () => 'uuid-' + (++keys) }, FormData, Event: class { constructor(type) { this.type = type; } },
    dispatchEvent: () => true, URL, URLSearchParams, Headers: Map, clearTimeout, Promise, Date, Math, JSON, console,
    // long waits (the local copy's time limit) are shortened so the tests stay fast
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 20)),
  };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/api.js', import.meta.url), 'utf8'), sandbox);
  return { api: sandbox.window.VaultApi, calls, writes, headers };
}

const progress = (o) => ({ processed: 300, not_found: 0, unmatched_rows: 0, unavailable: false, prices_as_of: '2026-10-03',
  version: 'v', _links: {}, ...o });

test('the refresh loop calls the server until nothing remains, sending its cursor back', async () => {
  const answers = [
    json(200, progress({ done: 300, total: 700, remaining: 400, cursor: 'c1' })),
    json(200, progress({ done: 600, total: 700, remaining: 100, cursor: 'c2' })),
    json(200, progress({ done: 700, total: 700, remaining: 0, cursor: null, processed: 100 })),
  ];
  const { api, calls, writes } = load(() => answers.shift());
  const seen = [];
  const last = await api.refreshCollection({ onProgress: (p) => seen.push(p.done) });
  assert.equal(last.remaining, 0);
  assert.deepEqual(calls.map((c) => [c.url, c.method]), Array(3).fill(['/api/v1/collection/refresh', 'POST']));
  assert.deepEqual(calls.map((c) => c.body.cursor ?? null), [null, 'c1', 'c2']);
  assert.deepEqual(seen, [300, 600, 700]);
  assert.deepEqual(writes, [], 'the refresh keeps nothing in localStorage');
});

test('503 and 429 are waited out as Retry-After says, then the loop carries on', async () => {
  const answers = [
    json(503, { title: 'Service Unavailable', detail: "Scryfall didn't answer." }, { 'retry-after': '30' }),
    json(200, progress({ done: 300, total: 400, remaining: 100, cursor: 'c1' })),
    json(429, { title: 'Too Many Requests', detail: 'Slow down' }, { 'retry-after': '7' }),
    json(200, progress({ done: 400, total: 400, remaining: 0, cursor: null })),
  ];
  const { api, calls, writes } = load(() => answers.shift());
  const waits = [];
  const last = await api.refreshCollection({ wait: async (ms) => { waits.push(ms); } });
  assert.equal(last.remaining, 0);
  assert.deepEqual(waits, [30000, 7000]);
  assert.deepEqual(calls.map((c) => c.body.cursor ?? null), [null, null, 'c1', 'c1']);
  assert.deepEqual(writes, []);
});

test('a Scryfall outage that does not end gives up with the 503', async () => {
  const { api, calls } = load(() => json(503, { detail: 'down' }, { 'retry-after': '30' }));
  const waits = [];
  await assert.rejects(api.refreshCollection({ wait: async (ms) => { waits.push(ms); }, maxWaits: 2 }),
    (e) => e.status === 503 && e.retryAfter === 30);
  assert.equal(calls.length, 3);
  assert.equal(waits.length, 2);
});

test('a call that moves nothing forward stops the loop instead of spinning', async () => {
  const { api, calls } = load(() => json(200, progress({ done: 0, total: 5, remaining: 5, cursor: 'c', processed: 0 })));
  await api.refreshCollection({ wait: async () => {} });
  assert.equal(calls.length, 1);
});

const summary = {
  version: 'v1', source: 'dragonshield', copies: 5, printings: 2, cards: 2, sets: 1, market_value: 12.5, paid: 4,
  costs_hidden: false, priced_by_scryfall: 5, by_printing: { Normal: 5 }, by_condition: { near_mint: 5 },
  imported_at: '2026-10-01', prices_as_of: '2026-10-02', owner: null,
  pnl: 1.5, pnl_pct: 37.5, known_cost_paid: 4, known_cost_market: 5.5, known_cost_copies: 2, unknown_cost_copies: 3,
  _links: {},
};
const card = (id, gain) => ({
  id, name: 'Sol Ring', set: { code: 'C21', name: 'Commander 2021' }, collector_number: '263', printing: 'Normal',
  finish: 'nonfoil', condition: 'near_mint', language: 'en', quantity: 2, paid: 1, paid_quantity: 1, gain,
  price: { market: 2, low: null, mid: null, currency: 'USD', source: 'scryfall' }, value: 4,
  acquired: { first: '2024-01-01', last: '2024-02-01' }, scryfall_id: 's1', card: null, _links: { self: { href: '/x' } },
});

test('the summary carries the server P&L, and printings carry its value and gain', async () => {
  const { api, calls } = load((url) => (url === '/api/v1/collection' ? json(200, summary)
    : json(200, { items: [card('a', 1.0)], count: 1, total: 1, value_total: 4, _links: {} })));
  const data = await api.collection();
  assert.equal(data.meta.pnl, 1.5);
  assert.equal(data.meta.pnlPct, 37.5);
  assert.equal(data.meta.knownCostCopies, 2);
  assert.equal(data.meta.unknownCostCopies, 3);
  assert.equal(data.meta.pricesAsOf, '2026-10-02');
  const page = await data.api.cards({ sort: '-value', printing: 'Foil', limit: 12 });
  assert.equal(calls.at(-1).url, '/api/v1/collection/cards?sort=-value&printing=Foil&limit=12');
  assert.deepEqual([page.items[0].v, page.items[0].gain, page.items[0].n, page.value_total], [4, 1, 'Sol Ring', 4]);
  assert.equal(page.more, null, 'no next link: no more pages');
});

test('names follow the server next links up to the asked number, with filters as parameters', async () => {
  const row = (name) => ({ name, copies: 1, market_value: 1, unit_price: 1, printings: 1, sets: ['C21'], color: 'W',
    color_identity: ['W'], type: 'Creature', _links: {} });
  const { api, calls } = load((url) => {
    if (url === '/api/v1/shared/7/collection') return json(200, { ...summary, costs_hidden: true, pnl: null });
    if (url.includes('cursor=')) return json(200, { items: [row('c'), row('d')], count: 2, total: 4, _links: {} });
    return json(200, { items: [row('a'), row('b')], count: 2, total: 4, _links: { next: { href: '/api/v1/shared/7/collection/names?cursor=x' } } });
  });
  const data = await api.sharedCollection(7);
  const names = await data.api.names({ colors: ['W', 'M'], min_value: 5, sort: '-value' }, 3);
  assert.deepEqual(Array.from(names.items, (n) => n.name), ['a', 'b', 'c']);
  assert.equal(names.total, 4);
  assert.equal(calls[1].url, '/api/v1/shared/7/collection/names?colors=W%2CM&min_value=5&sort=-value&limit=3');
  assert.equal(calls[2].url, '/api/v1/shared/7/collection/names?cursor=x');
});

// -- load freeze regressions: every paging loop ends, and the local copy never holds up a load --

// A minimal IndexedDB: one 'kv' store in a Map, answered asynchronously like the real one.
// `block`: the open is blocked (another tab holds the database); `hang`: it never answers.
function fakeIndexedDB(store = new Map(), { block = false, hang = false } = {}) {
  const later = (fn) => setTimeout(fn, 0);
  const request = (tx, fn) => { const r = {}; later(() => { r.result = fn(); if (tx.oncomplete) tx.oncomplete(); }); return r; };
  const db = {
    transaction: () => {
      const tx = {};
      tx.objectStore = () => ({
        get: (k) => request(tx, () => (store.has(k) ? structuredClone(store.get(k)) : undefined)),
        put: (v, k) => request(tx, () => { store.set(k, structuredClone(v)); }),
        delete: (k) => request(tx, () => { store.delete(k); }),
        clear: () => request(tx, () => { store.clear(); }),
      });
      return tx;
    },
  };
  return {
    store,
    open: () => {
      const req = {};
      later(() => {
        if (hang) return;
        if (block) { if (req.onblocked) req.onblocked(); return; }
        req.result = db;
        req.onsuccess();
      });
      return req;
    },
  };
}
const set = (code) => ({ code, name: code, copies: 1, market_value: 1, printings: 1, colors: {}, released_at: null, _links: {} });
// /sets whose second page links to itself: a server (or a stored copy) answering a cycle.
const cyclingSets = (url) => {
  if (url === '/api/v1/collection') return json(200, summary);
  if (url.includes('cursor=p2')) {
    return json(200, { items: [set('B')], count: 1, total: 2, _links: { next: { href: '/api/v1/collection/sets?limit=500&cursor=p2' } } });
  }
  return json(200, { items: [set('A')], count: 1, total: 2, _links: { next: { href: '/api/v1/collection/sets?limit=500&cursor=p2' } } });
};

test('a next link that points back at a page ends the list', { timeout: 5000 }, async () => {
  const { api, calls } = load(cyclingSets);
  const data = await api.collection();
  const sets = await data.api.sets();
  assert.deepEqual(Array.from(sets.items, (s) => s.code), ['A', 'B']);
  assert.equal(calls.filter((c) => c.url.includes('/sets')).length, 2, 'each page asked for once');
});

test('a cycle answered from the local copy ends without spinning in the browser', { timeout: 5000 }, async () => {
  const idb = fakeIndexedDB();
  const { api, calls } = load(cyclingSets, { cookie: 'vault_account=abc', indexedDB: idb });
  await (await api.collection()).api.sets();
  assert.ok([...idb.store.keys()].some((k) => k.startsWith('res:abc:/api/v1/collection/sets')), 'pages are stored');
  const before = calls.length;
  // the next visit, same version: every page comes from the local copy, none from the server
  const sets = await (await api.collection()).api.sets();
  assert.deepEqual(Array.from(sets.items, (s) => s.code), ['A', 'B']);
  assert.deepEqual(calls.slice(before).map((c) => c.url), ['/api/v1/collection'], 'only the summary is asked again');
});

test('following next links (all) stops at a link already followed or an empty page', { timeout: 5000 }, async () => {
  const deck = (id) => ({ id, name: 'd' + id, _links: {} });
  const { api, calls } = load((url) => (url.includes('cursor=2')
    ? json(200, { items: [deck(2)], count: 1, total: 2, _links: { next: { href: '/api/v1/decks?cursor=2' } } })
    : json(200, { items: [deck(1)], count: 1, total: 2, _links: { next: { href: '/api/v1/decks?cursor=2' } } })));
  const decks = await api.decks();
  assert.deepEqual(Array.from(decks, (d) => d.id), [1, 2]);
  assert.equal(calls.length, 2);
  const empty = load(() => json(200, { items: [], count: 0, total: 0, _links: { next: { href: '/api/v1/decks?cursor=x' } } }));
  assert.equal((await empty.api.decks()).length, 0);
  assert.equal(empty.calls.length, 1);
});

test('a refresh answer that hands back the same cursor stops the loop', { timeout: 5000 }, async () => {
  const { api, calls } = load(() => json(200, progress({ done: 300, total: 700, remaining: 400, cursor: 'same' })));
  await api.refreshCollection({ wait: async () => {} });
  assert.equal(calls.length, 2, 'the second answer repeats the cursor it was sent: no third call');
});

test('an old whole-collection copy is dropped on load without being read', { timeout: 5000 }, async () => {
  const old = { format: 2, version: 'old', items: Array.from({ length: 20000 }, (_, i) => ({ id: i, name: 'Card ' + i })) };
  const idb = fakeIndexedDB(new Map([['collection:abc:/api/v1/collection', old], ['collection:/api/v1/collection', old]]));
  const { api, calls } = load((url) => (url === '/api/v1/collection' ? json(200, summary)
    : json(200, { items: [card('a', 1)], count: 1, total: 1, value_total: 4, _links: {} })), { cookie: 'vault_account=abc', indexedDB: idb });
  const data = await api.collection();
  await data.api.cards({ sort: '-value', limit: 12 });
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual([...idb.store.keys()].filter((k) => k.startsWith('collection:')), [], 'old copies removed');
  assert.ok(idb.store.has('summary:abc:/api/v1/collection'));
  assert.deepEqual(calls.map((c) => c.url), ['/api/v1/collection', '/api/v1/collection/cards?sort=-value&limit=12']);
});

for (const [what, opts] of [['blocked', { block: true }], ['never answers', { hang: true }]]) {
  test(`a local copy that is ${what} doesn't hold up the load: the server answers`, { timeout: 5000 }, async () => {
    const idb = fakeIndexedDB(new Map(), opts);
    const { api } = load((url) => (url === '/api/v1/collection' ? json(200, summary)
      : json(200, { items: [card('a', 1)], count: 1, total: 1, value_total: 4, _links: {} })), { cookie: 'vault_account=abc', indexedDB: idb });
    const data = await api.collection();
    const page = await data.api.cards({ sort: '-value', limit: 12 });
    assert.equal(page.items[0].n, 'Sol Ring');
  });
}

test('an older copy keeps the author it recorded, never the author of a link it no longer has', async () => {
  const A = 'https://archidekt.com/decks/1', B = 'https://archidekt.com/decks/2';
  // Recorded: the answer still has this copy's link, so the copy now credits that author.
  let answer = { id: 7, source_url: A, source_author: 'Michael', recorded: true, _links: {} };
  const { api, calls } = load(async () => json(200, answer));
  const saved = { id: 7, source_url: A, source_author: null };
  await api.rememberDeckAuthor(saved, 'Michael');
  assert.equal(calls.at(-1).url, '/api/v1/decks/7/source-author');
  assert.deepEqual(calls.at(-1).body, { source_url: A, source_author: 'Michael' });
  assert.equal(saved.source_author, 'Michael');
  // Moved to another deck in another tab meanwhile: the server answers that deck and its author,
  // which must not be copied onto the copy that still points at link A.
  answer = { id: 8, source_url: B, source_author: 'Ana', recorded: false, _links: {} };
  const stale = { id: 8, source_url: A, source_author: null };
  await api.rememberDeckAuthor(stale, 'Michael');
  assert.equal(stale.source_author, null);
  // Nothing to record: no request at all.
  const before = calls.length;
  await api.rememberDeckAuthor({ id: 9, source_url: A, source_author: 'Kept' }, 'Other');
  await api.rememberDeckAuthor({ id: 9, source_url: null, source_author: null }, 'Other');
  await api.rememberDeckAuthor({ id: 9, source_url: A, source_author: null }, '   ');
  assert.equal(calls.length, before);
});

test('the web app\'s Undo asks first, then applies exactly the preview it was shown (#81)', async () => {
  const { api, calls } = load((url, init) => {
    const body = init.body ? JSON.parse(init.body) : {};
    if (url === '/api/v1/collection/changes/undo') return json(200, body.confirmation ? { undone: 7 } : { undoes: 7, ready: true, confirmation: 'tok', lines: [] });
    return json(200, { items: [{ id: 7, kind: 'assistant', undoable: true }] });
  });
  const shown = await api.undoPreview();
  assert.equal(shown.confirmation, 'tok');
  assert.deepEqual(calls[0], { url: '/api/v1/collection/changes/undo', method: 'POST', body: {} });  // no confirmation: a preview
  assert.deepEqual(await api.undoApply(shown.confirmation), { undone: 7 });
  assert.deepEqual(calls[1].body, { confirmation: 'tok' });
  assert.equal((await api.recentImports())[0].undoable, true);
  assert.equal(calls[2].url, '/api/v1/imports?limit=20');
});

test('the web app tag calls name the tag in the path, tag the card ids it was given and send confirm only when asked (#128)', async () => {
  const { api, calls } = load((url) => json(200, url.endsWith('/tags') ? { items: [{ tag: 'deck:sliver', cards: 2, by_source: { person: 1, assistant: 1, system: 0 } }], _links: {} } : { applied: true }));
  assert.equal((await api.tags())[0].by_source.assistant, 1);
  assert.equal(calls[0].url, '/api/v1/collection/tags');
  await api.tagCards('deck:sliver', ['a', 'b']);
  assert.deepEqual(calls[1], { url: '/api/v1/collection/tags/deck%3Asliver/cards', method: 'POST', body: { card_ids: ['a', 'b'] } });  // no confirm: more than 25 cards would only be shown
  await api.untagCards('trade', ['a'], true);
  assert.deepEqual(calls[2], { url: '/api/v1/collection/tags/trade/cards/remove', method: 'POST', body: { card_ids: ['a'], confirm: true } });
  await api.cardMetadata('abc');
  assert.equal(calls[3].url, '/api/v1/collection/cards/abc/metadata');
  assert.equal(calls[3].method, 'GET');  // the notes are read only in the app
});

test('the web app imports a file into one bucket: a preview first (nothing written), then the same file with a fresh key each time (#122)', async () => {
  const preview = { bucket: { id: 7, name: 'Trade box', rows: 3, copies: 7 }, untouched: { buckets: 3, rows: 8, copies: 86, note: 'n' }, changes: { added: 1 } };
  const { api, calls, headers } = load((url) => json(url.startsWith('/api/v1/imports/preview') ? 200 : 201, url.startsWith('/api/v1/imports/preview') ? preview : { id: 9 }));
  const file = new File(['Folder Name,Quantity\n'], 'trade.csv', { type: 'text/csv' });
  assert.deepEqual(await api.importPreview(file, 7), preview);
  assert.deepEqual(calls[0], { url: '/api/v1/imports/preview?bucket_id=7', method: 'POST', body: { file: 'trade.csv' } });
  assert.equal(headers[0]['Idempotency-Key'], undefined);  // a preview writes nothing, so it carries no key
  await api.importInto(file, 7);
  await api.importInto(file, 7);
  assert.deepEqual(calls[1], { url: '/api/v1/imports?bucket_id=7', method: 'POST', body: { file: 'trade.csv' } });
  assert.deepEqual([headers[1]['Idempotency-Key'], headers[2]['Idempotency-Key']], ['uuid-1', 'uuid-2']);  // each confirm is its own request
  await api.importCsv(file);
  assert.equal(calls[3].url, '/api/v1/imports');  // the top bar's import still takes the whole collection
});

test('an import into a bucket shows the server\'s own words for a bad file, a missing bucket, a conflict and the limit (#122)', async () => {
  for (const [status, detail] of [[400, 'No cards found in the file.'], [404, 'No such bucket'], [409, 'The collection changed'], [429, 'Too many imports']]) {
    const { api } = load(() => json(status, { detail }));
    const file = new File(['x'], 'a.csv');
    await assert.rejects(api.importPreview(file, 3), (e) => e.status === status && e.message === detail);
    await assert.rejects(api.importInto(file, 3), (e) => e.status === status && e.message === detail);
  }
});

test('a scoped client passes the bucket and tag on every analytics call and keeps the whole inventory unscoped (#130)', async () => {
  const { api, calls } = load((url) => {
    if (url === '/api/v1/collection') return json(200, summary);
    if (url.startsWith('/api/v1/collection?bucket')) return json(200, { ...summary, version: 'vb', copies: 2, market_value: 4 });
    if (url.startsWith('/api/v1/collection')) return json(200, url.includes('/sets') || url.includes('/cards') || url.includes('/history') || url.includes('/names')
      ? { items: [], count: 0, total: 0, _links: {} } : url.includes('/timeline') ? { months: [] } : {});
    return json(200, summary);
  });
  const whole = await api.collection();
  assert.equal(JSON.stringify(whole.scope), '{}');
  await whole.api.sets();
  assert.equal(calls.at(-1).url, '/api/v1/collection/sets?limit=500', 'no scope: the same calls as before');
  const picked = await whole.api.scoped({ bucket: 3, tag: 'trade' });
  assert.equal(calls.at(-1).url, '/api/v1/collection?bucket=3&tag=trade', 'the summary is that of the selection');
  assert.deepEqual([JSON.stringify(picked.scope), picked.meta.totalQty, picked.meta.totalMarket, picked.meta.version], ['{"bucket":3,"tag":"trade"}', 2, 4, 'vb']);
  const scope = 'bucket=3&tag=trade';
  const asked = async (fn) => { await fn(); return calls.at(-1).url; };
  assert.equal(await asked(() => picked.api.sets({ sort: 'code' })), `/api/v1/collection/sets?sort=code&limit=500&${scope}`);
  assert.equal(await asked(() => picked.api.cards({ sort: '-value', limit: 12 })), `/api/v1/collection/cards?sort=-value&limit=12&${scope}`);
  assert.equal(await asked(() => picked.api.timeline()), `/api/v1/collection/timeline?${scope}`);
  assert.equal(await asked(() => picked.api.valuation()), `/api/v1/collection/valuation?${scope}`);
  assert.equal(await asked(() => picked.api.breakdowns()), `/api/v1/collection/breakdowns?${scope}`);
  assert.equal(await asked(() => picked.api.history()), `/api/v1/collection/history?limit=500&${scope}`);
  assert.equal(await asked(() => picked.api.stats(5)), `/api/v1/collection/stats?limit=5&${scope}`);
  assert.equal(await asked(() => picked.api.names({}, 10)), `/api/v1/collection/names?limit=10&${scope}`);
  const bucketOnly = await whole.api.scoped({ bucket: 3 });
  assert.equal(await asked(() => bucketOnly.api.valuation()), '/api/v1/collection/valuation?bucket=3');
  assert.equal(await asked(() => whole.api.valuation()), '/api/v1/collection/valuation', 'the inventory client is unchanged');
});

test('the web app resets in two steps: a preview with no key and no confirmation, then the same options with the preview\'s confirmation and a fresh key (#129)', async () => {
  const seen = { applied: false, confirmation: 'tok-123456', removes: { copies: 5 } };
  const { api, calls, headers } = load((url, init) => (url === '/api/v1/collection/reset' && init.method === 'POST' && !JSON.parse(init.body).confirmation)
    ? json(200, seen) : json(200, { applied: true, removed: { copies: 5 }, undo: { available: true } }));
  const options = { bucket_id: 7, keep_tags: true, keep_history: true, no_undo: false };
  assert.deepEqual(await api.resetPreview(options), seen);
  assert.deepEqual(calls[0], { url: '/api/v1/collection/reset', method: 'POST', body: options });
  assert.equal(headers[0]['Idempotency-Key'], undefined);  // a preview writes nothing
  await api.resetApply(options, 'tok-123456');
  await api.resetApply(options, 'tok-123456');
  assert.deepEqual(calls[1].body, { ...options, confirmation: 'tok-123456' });
  assert.deepEqual([headers[1]['Idempotency-Key'], headers[2]['Idempotency-Key']], ['uuid-1', 'uuid-2']);  // each confirm is its own request
});

test('the web app asks for the undo of a reset, treats "no reset" (404) as nothing to show and shows the server\'s words for a refusal (#129)', async () => {
  const { api, calls } = load((url, init) => url === '/api/v1/collection/reset' && (init.method || 'GET') === 'GET'
    ? json(404, { detail: 'There is no reset to undo' }) : json(409, { detail: 'The collection changed since the reset' }));
  assert.equal(await api.resetInfo(), null);
  await assert.rejects(api.resetUndo(), (e) => e.status === 409 && e.message === 'The collection changed since the reset');
  assert.deepEqual(calls[1], { url: '/api/v1/collection/reset/undo', method: 'POST', body: { confirm: true } });
});
