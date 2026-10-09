// public/lib/buy.js: what the "Where to buy" menu says (docs/where-to-buy-design.md, #212). The links and their order for a saved country
// are the server's; the browser only picks one of the server's four orders from its own language while no country is saved.
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sandbox = { Intl };
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/buy.js', import.meta.url), 'utf8'), sandbox);
const B = sandbox.window.VaultBuy;
const J = (o) => JSON.parse(JSON.stringify(o));

const shop = (id, name) => ({ id, name, host: `${id}.example`, url: `https://${id}.example/?q=Sol+Ring`, opens: 'a search' });
const SHOPS = { magicmadhouse: shop('magicmadhouse', 'Magic Madhouse'), cardkingdom: shop('cardkingdom', 'Card Kingdom'), cardmarket: shop('cardmarket', 'Cardmarket') };
const ORDERS = { GB: ['magicmadhouse', 'cardmarket'], US: ['cardkingdom', 'cardmarket'], EU: ['cardmarket'], other: ['cardmarket', 'cardkingdom'] };
const ids = (rows) => J(rows.map((r) => r.id));  // plain arrays: the sandbox has its own prototypes
const neutral = (over) => ({ country: null, country_name: null, shops: [SHOPS.cardmarket, SHOPS.cardkingdom], more_shops: [SHOPS.magicmadhouse],
  region_orders: ORDERS, europe_countries: ['DE', 'FR', 'NO', 'CH'], ...over });
const saved = (over) => ({ country: 'GB', country_name: 'United Kingdom', shops: [SHOPS.magicmadhouse, SHOPS.cardmarket], more_shops: [SHOPS.cardkingdom], ...over });

test('the region of a browser language is its country, and a language alone has none', () => {
  assert.equal(B.regionOfLanguage('en-GB'), 'GB');
  assert.equal(B.regionOfLanguage('en_us'), 'US');
  assert.equal(B.regionOfLanguage('de-DE'), 'DE');
  assert.equal(B.regionOfLanguage('zh-Hant-TW'), 'TW');
  assert.equal(B.regionOfLanguage('en'), null);
  assert.equal(B.regionOfLanguage('fr'), null);
  assert.equal(B.regionOfLanguage(''), null);
  assert.equal(B.regionOfLanguage(undefined), null);
  assert.equal(B.regionOfLanguage('en-419'), null);  // a region that is a number is not a country
});

test('a saved country wins, in the server\'s order, whatever the browser says', () => {
  const a = B.arrange(saved(), 'US');
  assert.deepEqual(ids(a.shops), ['magicmadhouse', 'cardmarket']);
  assert.deepEqual(ids(a.more), ['cardkingdom']);
  assert.equal(a.source, 'account');
});

test('with no country saved the browser\'s own region picks one of the server\'s four orders', () => {
  assert.deepEqual(ids(B.arrange(neutral(), 'GB').shops), ['magicmadhouse', 'cardmarket']);
  assert.deepEqual(ids(B.arrange(neutral(), 'GB').more), ['cardkingdom']);
  assert.deepEqual(ids(B.arrange(neutral(), 'US').shops), ['cardkingdom', 'cardmarket']);
  assert.deepEqual(ids(B.arrange(neutral(), 'DE').shops), ['cardmarket']);
  assert.deepEqual(ids(B.arrange(neutral(), 'DE').more).sort(), ['cardkingdom', 'magicmadhouse']);
  assert.deepEqual(ids(B.arrange(neutral(), 'AU').shops), ['cardmarket', 'cardkingdom']);
  const a = B.arrange(neutral(), 'GB');
  assert.equal(a.source, 'browser');
  assert.equal(a.region, 'GB');
  // every shop is somewhere, once
  assert.deepEqual([...ids(a.shops), ...ids(a.more)].sort(), ['cardkingdom', 'cardmarket', 'magicmadhouse']);
});

test('a language without a region, or no orders from the server, leaves the neutral order', () => {
  const n = neutral();
  assert.deepEqual(J(B.arrange(n, null)), { shops: J(n.shops), more: J(n.more_shops), source: 'neutral', region: null });
  assert.equal(B.arrange({ ...n, region_orders: undefined }, 'GB').source, 'neutral');
});

test('the top line says whose order it is, that a browser guess is not stored, and offers the change', () => {
  assert.equal(B.shopsLine(saved(), B.arrange(saved(), null)), 'Shops for United Kingdom.');
  assert.equal(B.changeLabel(B.arrange(saved(), null)), 'Change');
  assert.equal(B.shopsLine(neutral(), B.arrange(neutral(), 'GB')), 'Ordered for United Kingdom from your browser\'s language. Nothing is stored.');
  assert.equal(B.changeLabel(B.arrange(neutral(), 'GB')), 'Set where you buy');
  assert.equal(B.shopsLine(neutral(), B.arrange(neutral(), null)), 'Pick where you buy to see the right shops first.');
});

test('a typed store says whether it opens its page or searches, and the locator says the Vault is not told the place', () => {
  assert.equal(B.storeNote({ opens: 'the store\'s page' }), 'opens its page');
  assert.equal(B.storeNote({ opens: 'a search for the card on the store\'s own site' }), 'searches for the card');
  assert.equal(B.storeLabel({ name: 'The Games Shop' }), 'My store: The Games Shop');
  assert.match(B.LOCATOR_LABEL, /Find a store near me/);
  assert.match(B.LOCATOR_NOTE, /type your town or postcode there/);
  assert.match(B.LOCATOR_NOTE, /The Vault is not told/);
});

test('the country list starts with "Not set" and the settings form drops a store left blank but keeps a half-filled one for the server to judge', () => {
  const choices = B.countryChoices([{ code: 'GB', name: 'United Kingdom' }, { code: 'US', name: 'United States' }]);
  assert.deepEqual(J(choices), [{ value: '', label: 'Not set' }, { value: 'GB', label: 'United Kingdom' }, { value: 'US', label: 'United States' }]);
  assert.deepEqual(J(B.storesForSave([B.emptyStore(), { name: 'A', url: 'https://a.example.com/', search_url: '  ' }, { name: 'B', url: '', search_url: '' },
    { name: 'C', url: 'https://c.example.com/', search_url: ' https://c.example.com/?q={card} ' }])),
  [{ name: 'A', url: 'https://a.example.com/' }, { name: 'B', url: '' }, { name: 'C', url: 'https://c.example.com/', search_url: 'https://c.example.com/?q={card}' }]);
});

test('nothing in the menu\'s library builds a shop address, asks for a position or reads a language other than the one it is given', () => {
  const source = readFileSync(new URL('../../public/lib/buy.js', import.meta.url), 'utf8');
  for (const bad of ['geolocation', 'navigator', 'fetch(', 'XMLHttpRequest', 'localStorage', 'sessionStorage', 'cardmarket.com', 'cardkingdom.com', 'magicmadhouse.co.uk', 'locator.wizards.com']) {
    assert.ok(!source.includes(bad), bad);
  }
});
