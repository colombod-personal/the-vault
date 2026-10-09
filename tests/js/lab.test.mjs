// public/lib/lab.js: the Lab page's wording and formatting (docs/lab-design.md).
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/lab.js', import.meta.url), 'utf8'), sandbox);
const L = sandbox.window.VaultLab;
const J = (o) => JSON.parse(JSON.stringify(o));  // objects made in the sandbox have its prototypes
const day = (iso) => Date.parse(iso + 'T12:00:00Z');

test('money always shows cents and signs use a real minus', () => {
  assert.equal(L.money(6.6), '$6.60');
  assert.equal(L.money(1204), '$1,204.00');
  assert.equal(L.signed(-310), '−$310.00');
  assert.equal(L.signed(1204.5), '+$1,204.50');
});

test('prices are stale when the last refresh is more than a day old, and the header says how old', () => {
  assert.equal(L.isStale('2026-10-06', day('2026-10-06')), false);
  assert.equal(L.isStale('2026-10-06', day('2026-10-07')), false);  // one day: not stale
  assert.equal(L.isStale('2026-10-03', day('2026-10-06')), true);
  assert.equal(L.pricesLine('2026-10-06', day('2026-10-06')), 'Prices: Scryfall, 6 Oct');
  assert.equal(L.pricesLine('2026-10-03', day('2026-10-06')), 'Prices: Scryfall, 3 Oct (3 days old)');
  assert.equal(L.pricesLine(null), 'Prices: Scryfall, not synced yet');
  assert.equal(L.isStale(null), false);
});

test('the counters read the named fields and say "nothing to decide" for an empty side', () => {
  const overlap = { decks_checked: 3, summary: { decks_analysed: 3, decks_needing_purchase: 2, finish_all_cost: 6.6, unpriced: 0, cards_to_buy: 1 } };
  assert.deepEqual(J(L.buyCounter(overlap, null)), { quiet: false, headline: '2 decks need a purchase', detail: 'finish all: $6.60' });
  assert.equal(L.buyCounter(overlap, '3 Oct').detail, 'finish all: $6.60 (3 Oct)');
  const alone = { decks_checked: 2, summary: { decks_analysed: 2, decks_needing_purchase: 0, finish_all_cost: 0, unpriced: 0, cards_to_buy: 0 } };
  assert.equal(L.buyCounter(alone).headline, 'Nothing to decide');
  const gain = { gain: 1204 }, loss = { gain: -310 };
  const pnl = (g, l) => ({ summary: { biggest_gain: g, biggest_loss: l, net_gain: 5, reason: null } });
  assert.deepEqual(J(L.pnlCounter(pnl(gain, loss))), { quiet: false, headline: 'Biggest known loss −$310.00', detail: 'biggest gain +$1,204.00' });
  assert.equal(L.pnlCounter(pnl(gain, null)).headline, 'Biggest known gain +$1,204.00');
  assert.equal(L.pnlCounter(pnl(null, loss)).detail, 'no holding is above cost');
  assert.equal(L.pnlCounter(pnl(null, null)).headline, 'Nothing to decide');
});

test('profit and loss says what its figures cover, or why there are none', () => {
  const s = { total_copies: 21950, covered_copies: 21745, unknown_cost_copies: 205, unpriced_market_copies: 0 };
  assert.equal(L.coverageLine(s), 'Based on 21,745 of 21,950 copies; 205 have no price paid.');
  assert.equal(L.coverageLine({ ...s, unpriced_market_copies: 3 }), 'Based on 21,745 of 21,950 copies; 205 have no price paid; 3 have no current price.');
  assert.equal(L.pnlHidden({ covered_copies: 0, reason: 'no price paid is known' }), 'No profit and loss to show: no price paid is known.');
  assert.equal(L.pnlHidden(s), null);
  assert.equal(L.pnlEmpty('losers'), 'Nothing here: none of your priced holdings are below cost.');
});

test('the chart offers Show losers only when the counted holdings are below cost in total', () => {
  assert.equal(L.chartAction({ summary: { net_gain: -1 } }, 'ok').kind, 'losers');
  assert.equal(L.chartAction({ summary: { net_gain: 20 } }, 'ok').kind, 'spare');
  assert.equal(L.chartAction({ summary: { net_gain: null } }, 'ok').kind, 'spare');  // no known cost: market line only
  assert.equal(L.chartAction({ summary: { net_gain: 20 } }, 'no_decks'), null);  // the spare list is hidden without decks
});

test('a spare card with no priced copy says so instead of showing $0', () => {
  const none = { spare: 2, priced_copies: 0, market_value_of_spare: 0, deck_count: 0 };
  assert.deepEqual(J(L.spareFigures(none, null)), { spare: '2 spare', value: 'no price', decks: 'in no deck' });
  assert.equal(L.spareFigures({ ...none, priced_copies: 2, market_value_of_spare: 135, deck_count: 4 }, '3 Oct').value, '$135.00 (3 Oct)');
  assert.equal(L.spareValueLine({ copies: 44, market_value: 842, priced_copies: 41, unpriced_copies: 3 }, null),
    '$842.00 across 41 priced copies; 3 have no price');
});
