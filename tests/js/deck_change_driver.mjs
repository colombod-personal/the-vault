// Runs the deck page's change flow wording (public/lib/deck_change.js) and the address handling of public/app.jsx on real answers
// and prints what the page would say. Not a test by itself (no .test.mjs name): tests/test_deck_change_page.py runs it.
//
//   stdin  { deck, cuts, adds, validation, coverage, hashes }   `validation` is the `result` of POST /decks/validate-changes (include_text),
//          `coverage` the answer of POST /decks/coverage for its deck_text, `hashes` addresses to read as routes
//   stdout { verdict, counts, cost, existing, issues, owned, toBuy, confirm, blocked, saved, routes, rebuilt }
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const input = JSON.parse(readFileSync(0, 'utf8'));
const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(read('../../public/lib/deck_change.js'), sandbox);
const V = sandbox.window.VaultDeckChange;
const J = (o) => JSON.parse(JSON.stringify(o));
const { deck, cuts, adds, validation: r, coverage } = input;

// The page's address handling: from the views list to the url builder of public/app.jsx, as the page has it.
const jsx = read('../../public/app.jsx').replace(/\r\n/g, '\n');
const from = jsx.indexOf('const VAULT_VIEWS');
const to = jsx.indexOf('const vaultUrlFor');
if (from < 0 || to < from) throw new Error('app.jsx no longer has the route functions where the driver looks for them');
// the route code also reads the Ideas view's address helpers (public/lib/ideas.js), loaded the way the page loads them
const ideasSandbox = {};
ideasSandbox.window = ideasSandbox;
vm.runInNewContext(read('../../public/lib/ideas.js'), ideasSandbox);
const ctx = vm.createContext({ window: ideasSandbox.window, URLSearchParams, decodeURIComponent, encodeURIComponent, JSON, Number, Set, location: { hash: '' }, helpHashFor: (id) => '#/help' + (id ? '/' + id : '') });
vm.runInContext(jsx.slice(from, to), ctx);
const routes = input.hashes.map((hash) => { ctx.location.hash = hash; return J(vm.runInContext("vaultRouteFromHash('dashboard')", ctx)); });
const rebuilt = routes.map((route) => { ctx.route = route; return vm.runInContext('vaultHashFor(route)', ctx); });

const by = V.coverageByName(coverage);
console.log(JSON.stringify({
  verdict: V.verdict(r), counts: V.countsLine(r), cost: V.costLines(r), existing: V.existingLine(r),
  issues: r.issues.map((i) => V.issueLine(i)),
  owned: V.copies(adds).map((c) => V.ownershipLine(c.name, by[V.nameKey(c.name)])), toBuy: V.toBuyLine(coverage),
  confirm: V.confirmLabel(deck, cuts, adds, r), blocked: V.blocked(r, cuts, adds), saved: V.savedLine(deck, cuts, adds), routes, rebuilt,
}));
