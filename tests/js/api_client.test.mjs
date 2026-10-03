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
function load(route) {
  const calls = [], writes = [];
  const sandbox = {
    fetch: async (url, init = {}) => {
      if (url === '/api/v1' && !init.method) return json(200, { _links: {} });  // the twins check at start-up
      calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
      return route(url, init);
    },
    localStorage: {
      getItem: () => null, setItem: (k, v) => writes.push(['set', k, v]), removeItem: (k) => writes.push(['remove', k]),
      clear: () => writes.push(['clear']), key: () => null, length: 0,
    },
    location: { href: 'https://vault.test/' }, document: { cookie: '' },
    crypto: { randomUUID: () => 'uuid' }, Event: class { constructor(type) { this.type = type; } },
    dispatchEvent: () => true, URL, URLSearchParams, Headers: Map, setTimeout, clearTimeout, Promise, Date, Math, JSON, console,
  };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/api.js', import.meta.url), 'utf8'), sandbox);
  return { api: sandbox.window.VaultApi, calls, writes };
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
