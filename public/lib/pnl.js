// The one rule for cost and profit & loss: a card's cost is known when the owner shares prices
// paid (not `meta.costsHidden`) and a price paid was recorded for at least one copy. `pd` is the
// total paid and `pq` the number of copies it covers (copies without a price paid are left out),
// so P&L only ever compares what was paid against the market value of those same copies.
(function () {
  const knownQty = (card) => (card.pq == null ? ((card.pd || 0) > 0 ? card.q || 0 : 0) : card.pq);

  window.vaultCostKnown = (card, costsHidden) => !costsHidden && (card.pd || 0) > 0 && knownQty(card) > 0;

  // One card's P&L: { state: 'private' | 'unknown' | 'known', pnl, paid, market, qty }.
  window.vaultCardPnL = (card, costsHidden) => {
    if (costsHidden) return { state: 'private', pnl: null };
    if (!window.vaultCostKnown(card, false)) return { state: 'unknown', pnl: null };
    const qty = knownQty(card), market = (card.mk || 0) * qty;
    return { state: 'known', pnl: market - card.pd, paid: card.pd, market, qty };
  };

  // P&L over many cards, counting only copies with a known cost.
  window.vaultPnL = (cards, costsHidden) => {
    let paid = 0, market = 0, known = 0, unknown = 0;
    for (const c of cards) {
      const r = window.vaultCardPnL(c, costsHidden);
      if (r.state === 'known') { paid += r.paid; market += r.market; known++; }
      else unknown++;
    }
    const pnl = market - paid;
    return { hidden: !!costsHidden, known, unknown, paid, market, pnl: known ? pnl : null, pct: paid ? (pnl / paid) * 100 : null };
  };

  // Text for one card's "Spent" and "P&L" cells: "private" when the owner hides costs, "—" when unknown.
  window.vaultSpentText = (card, costsHidden) =>
    costsHidden ? 'private' : window.vaultCostKnown(card, false) ? `$${card.pd.toFixed(2)}` : '—';
  window.vaultPnLText = (card, costsHidden) => {
    const r = window.vaultCardPnL(card, costsHidden);
    if (r.state === 'private') return 'private';
    if (r.state === 'unknown') return '—';
    return `${r.pnl >= 0 ? '+' : '−'}$${Math.abs(r.pnl).toFixed(2)}`;
  };
})();
