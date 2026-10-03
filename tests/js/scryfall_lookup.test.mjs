// public/lib/scryfall.js against a stand-in for POST /api/v1/cards/lookup.
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function load(answer, store = new Map(), calls = []) {
  const sandbox = {
    localStorage: {
      getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k),
      key: (i) => [...store.keys()][i] ?? null, get length() { return store.size; },
    },
    fetch: async (url, init) => { calls.push(init); return { ok: true, status: 200, json: async () => answer }; },
    setTimeout, clearTimeout, Promise, Date, Math, JSON, Map, Set, console,
  };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/scryfall.js', import.meta.url), 'utf8'), sandbox);
  return sandbox.window.Scryfall;
}

const fireIce = { id: 'f1', name: 'Fire // Ice', set: 'mh2', collector_number: '290', prices: { usd: '0.50' },
  image_uris: {}, finishes: ['nonfoil'] };

test('a double-faced card looked up by its front face name is found', async () => {
  const scry = load({ data: [fireIce], not_found: [] });
  const [card] = await scry.collection([{ name: 'Fire' }]);
  assert.ok(card, 'the server returned Fire // Ice for "Fire"; the result must not be dropped');
  assert.equal(card.name, 'Fire // Ice');
});

test('the full name still works, and so does the back face', async () => {
  const scry = load({ data: [fireIce], not_found: [] });
  const results = await scry.collection([{ name: 'Fire // Ice' }, { name: 'Ice' }]);
  assert.ok(results[0] && results[1]);
});

test('each finish is priced by its own Scryfall price, etched included', () => {
  const scry = load({ data: [], not_found: [] });
  const prices = { usd: '1.00', usd_foil: '3.00', usd_etched: '5.00' };
  assert.equal(scry.priceFor(prices, { fin: 'nonfoil', p: '' }), 1);
  assert.equal(scry.priceFor(prices, { fin: 'foil', p: 'Foil' }), 3);
  assert.equal(scry.priceFor(prices, { fin: 'etched', p: 'Etched' }), 5);
  assert.equal(scry.priceFor(prices, { p: 'Etched' }), 5, 'older saved copies have only the printing');
  assert.equal(scry.priceFor({ usd: '1.00' }, { fin: 'etched' }), null, 'no etched price: keep the stored one');
});

test('the cropped art is kept for the graph, for double-faced cards too', async () => {
  const uris = (id) => ({ normal: `https://cards.scryfall.io/normal/front/${id}.jpg`, art_crop: `https://cards.scryfall.io/art_crop/front/${id}.jpg` });
  const solRing = { id: 's1', name: 'Sol Ring', set: 'c21', collector_number: '263', prices: {}, image_uris: uris('s1') };
  const delver = { id: 'd1', name: 'Delver of Secrets // Insectile Aberration', set: 'isd', collector_number: '51', prices: {},
    card_faces: [{ image_uris: uris('d1-front') }, { image_uris: uris('d1-back') }] };
  const scry = load({ data: [solRing, delver], not_found: [] });
  const [ring, flip] = await scry.collection([{ name: 'Sol Ring' }, { name: 'Delver of Secrets' }]);
  assert.equal(ring.img_art, 'https://cards.scryfall.io/art_crop/front/s1.jpg');
  assert.equal(flip.img_art, 'https://cards.scryfall.io/art_crop/front/d1-front.jpg');
});

test('owned cards answer from the collection, with no lookup', async () => {
  const calls = [];
  const scry = load({ data: [fireIce], not_found: [] }, new Map(), calls);
  const sol = { name: 'Sol Ring', type_line: 'Artifact', prices: { usd: '1.00' } };
  scry.own([{ n: 'Sol Ring', s: 'C21', cn: '263', scry: sol }, { n: 'Unsynced', s: 'X', cn: '1', scry: null }]);
  assert.equal(scry.cached('Sol Ring', 'C21', '263'), sol);
  assert.equal(scry.cached('Sol Ring'), sol, 'by name too, for views that aggregate by name');
  assert.equal(scry.cached('Unsynced', 'X', '1'), null);
  const [card] = await scry.collection([{ name: 'Sol Ring', set: 'C21', collector_number: '263' }]);
  assert.equal(card, sol);
  assert.equal(calls.length, 0);
});

test('lookups stay in memory, and the old localStorage card caches are removed', async () => {
  const store = new Map([['scry_cache_v3', '{}'], ['scry_cache_v2', '{}'], ['vault_tweaks', 'keep']]);
  const scry = load({ data: [fireIce], not_found: [] }, store);
  assert.deepEqual([...store.keys()], ['vault_tweaks']);
  await scry.collection([{ name: 'Fire' }]);
  await new Promise((r) => setTimeout(r, 300));
  assert.deepEqual([...store.keys()], ['vault_tweaks']);
  assert.equal(scry.cached('Fire').name, 'Fire // Ice');
});

test('a forced refresh asks the Vault even for owned cards, and its answer wins', async () => {
  const calls = [];
  const fresh = { ...fireIce, prices: { usd: '0.75' } };
  const scry = load({ data: [fresh], not_found: [] }, new Map(), calls);
  scry.own([{ n: 'Fire // Ice', s: 'MH2', cn: '290', scry: { name: 'Fire // Ice', prices: { usd: '0.50' } } }]);
  await scry.collection([{ name: 'Fire // Ice', set: 'MH2', collector_number: '290' }], null, { force: true });
  assert.equal(calls.length, 1);
  assert.equal(scry.cached('Fire // Ice', 'MH2', '290').prices.usd, '0.75');
});
