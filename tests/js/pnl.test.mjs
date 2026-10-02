// public/lib/pnl.js: profit & loss counts only the copies with a known price paid.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function load() {
  const sandbox = { Math };
  sandbox.window = sandbox;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/pnl.js', import.meta.url), 'utf8'), sandbox);
  return sandbox.window;
}

test('copies without a price paid are left out of P&L', () => {
  const w = load();
  // 4 copies at $2.00 market; $1.00 paid, recorded for 1 copy only
  const card = { q: 4, mk: 2, pd: 1, pq: 1 };
  assert.deepEqual({ ...w.vaultCardPnL(card, false) }, { state: 'known', pnl: 1, paid: 1, market: 2, qty: 1 });
  assert.equal(w.vaultPnLText(card, false), '+$1.00');
  const all = w.vaultPnL([card, { q: 2, mk: 5, pd: 0, pq: 0 }], false);
  assert.deepEqual([all.known, all.unknown, all.paid, all.market, all.pnl, all.pct], [1, 1, 1, 2, 1, 100]);
});

test('hidden or unrecorded costs give no P&L', () => {
  const w = load();
  assert.equal(w.vaultCardPnL({ q: 1, mk: 2, pd: 1, pq: 1 }, true).state, 'private');
  assert.equal(w.vaultCardPnL({ q: 1, mk: 2, pd: 0, pq: 0 }, false).state, 'unknown');
  assert.equal(w.vaultSpentText({ q: 1, mk: 2, pd: 0, pq: 0 }, false), '—');
  assert.equal(w.vaultPnL([], false).pnl, null);
});

test('a card without a known-copy count (older data) counts all its copies', () => {
  const w = load();
  assert.equal(w.vaultCardPnL({ q: 2, mk: 3, pd: 4 }, false).pnl, 2);
});
