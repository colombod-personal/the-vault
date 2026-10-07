// Runs the deck page's own code against the Vault's answer for an Archidekt deck and prints what the page sends.
// Not a test by itself (it has no .test.mjs name): tests/test_web_parity.py runs it and replays the requests
// against the server, next to the MCP tools (#91).
//
//   stdin  { archidekt: <the answer of GET /api/v1/archidekt/decks/{id}?detail=cards>, url, format? }
//   stdout { text, format, requests: [{ url, method, body }] }
//
// What is real here: public/lib/deck.js (DeckSrc.fetchUrl, which reads the cards the way the page does), public/lib/api.js
// (VaultApi, which builds each request), and the list builder of public/views/deck.jsx (mergeDeckCards, deckListText,
// isCommander), taken from the source text between two markers, so the page cannot change without this following.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const input = JSON.parse(readFileSync(0, 'utf8'));

const requests = [];
const reply = (body) => ({
  ok: true, status: 200,
  headers: { get: (k) => (k.toLowerCase() === 'content-type' ? 'application/json' : null) },
  json: async () => body, text: async () => JSON.stringify(body),
});
const sandbox = {
  fetch: async (url, init = {}) => {
    if (url === '/api/v1' && !init.method) return reply({ _links: {} }); // the twins check at start-up
    if (url.startsWith('/api/v1/archidekt/decks/')) return reply(input.archidekt);
    requests.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
    return reply({});
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {}, clear() {}, key: () => null, length: 0 },
  location: { href: 'https://vault.test/' }, document: { cookie: '' },
  crypto: { randomUUID: () => 'uuid' }, Event: class { constructor(type) { this.type = type; } },
  dispatchEvent: () => true, URL, URLSearchParams, Headers: Map, clearTimeout, Promise, Date, Math, JSON, console, setTimeout,
};
sandbox.window = sandbox;
const ctx = vm.createContext(sandbox);
vm.runInContext(read('../../public/lib/api.js'), ctx);
vm.runInContext(read('../../public/lib/deck.js'), ctx);

// The page's list builder: from the commander comment to the line before `const money`.
const jsx = read('../../public/views/deck.jsx').replace(/\r\n/g, '\n');
const from = jsx.indexOf("// Only the deck's actual Commander category");
const to = jsx.indexOf('const money =');
if (from < 0 || to < from) throw new Error('deck.jsx no longer has the list builder where the driver looks for it');
vm.runInContext(jsx.slice(from, to), ctx);
const { isCommander, mergeDeckCards, deckListText } = vm.runInContext('({ isCommander, mergeDeckCards, deckListText })', ctx);

// DeckPage.load(): read the deck, merge repeated cards, write the list; the format defaults as the page does it.
const deck = await sandbox.window.DeckSrc.fetchUrl(input.url);
const merged = mergeDeckCards(deck.cards);
const text = deckListText(merged);
const format = input.format || (merged.some(isCommander) ? 'commander' : 'standard');

// What each tab asks for with `text`, and what Save sends (DeckPage.save / DeckStats / DeckLegality / DeckBuyList).
const api = sandbox.window.VaultApi;
await api.deckCoverage(text);
await api.deckStats(text);
await api.deckLegality(text, format);
await api.deckShopping(text);
await api.saveDeck(deck.title, text, deck.url || null, deck.author || null);
process.stdout.write(JSON.stringify({ text, format, title: deck.title, requests }));
