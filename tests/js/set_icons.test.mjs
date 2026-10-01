// public/lib/setIcons.js against a stand-in for GET /api/v1/catalog/sets.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SETS = Array.from({ length: 60 }, (_, i) => ({ code: `s${i}`, name: `Set ${i}`, icon_svg_uri: `https://svgs/s${i}.svg` }));
const PAGE = (items) => ({ items, count: items.length, total: items.length, aliases: { gk2_orzhov: 'gk2' }, _links: {} });

function load({ store = new Map(), pages, now = Date.now() }) {
  const calls = [];
  const sandbox = {
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) },
    fetch: async (url) => { calls.push(url); return { ok: true, json: async () => pages.shift() } },
    Date: { now: () => now }, setTimeout, Promise, JSON, Object, Set, Map, Math, console,
  };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/setIcons.js', import.meta.url), 'utf8'), sandbox);
  return { icons: sandbox.window.SetIcons, calls, store };
}

test('a Dragon Shield set code finds its set icon', async () => {
  const { icons } = load({ pages: [PAGE([...SETS, { code: 'gk2', name: 'RNA Guild Kit', icon_svg_uri: 'https://svgs/gk2.svg' }])] });
  await icons.loadAll();
  assert.equal(icons.get('GK2_ORZHOV')?.icon, 'https://svgs/gk2.svg');
  assert.equal(icons.get('gk2')?.icon, 'https://svgs/gk2.svg');
});

test('the saved set list is refreshed after a day', async () => {
  const day = 24 * 3600 * 1000;
  const first = load({ pages: [PAGE(SETS)], now: 0 });
  await first.icons.loadAll();
  assert.equal(first.calls.length, 1);
  const sameDay = load({ store: first.store, pages: [PAGE(SETS)], now: day / 2 });
  await sameDay.icons.loadAll();
  assert.equal(sameDay.calls.length, 0, 'fresh copy: no request');
  const nextDay = load({ store: first.store, pages: [PAGE([...SETS, { code: 'new', name: 'New', icon_svg_uri: 'x' }])], now: day + 1 });
  await nextDay.icons.loadAll();
  assert.equal(nextDay.calls.length, 1, 'stale copy: fetched again');
  assert.ok(nextDay.icons.get('new'), 'a set released since is found');
});

test('a guild-kit code missing from the alias table follows the prefix rule', async () => {
  const page = { ...PAGE([{ code: 'gk1', name: 'GRN Guild Kit', icon_svg_uri: 'https://svgs/gk1.svg' }]), alias_prefixes: ['gk1_', 'gk2_'] };
  const { icons } = load({ pages: [page] });
  await icons.loadAll();
  assert.equal(icons.get('GK1_NEWGUILD')?.icon, 'https://svgs/gk1.svg');
});
