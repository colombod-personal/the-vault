// public/lib/scryfall.js against a stand-in for POST /api/v1/cards/lookup.
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function load(answer) {
  const store = new Map();
  const sandbox = {
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
    fetch: async () => ({ ok: true, status: 200, json: async () => answer }),
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
