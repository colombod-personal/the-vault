// What the Lab page (public/views/lab.jsx) says about the server's answers: every number is the server's (docs/lab-design.md), and
// this file only words and formats them, so the page and the tests (tests/js/lab.test.mjs, tests/test_lab_page.py) read the same
// sentences. Nothing here adds, sorts or compares collection data beyond the choices the design names (which side of the profit
// and loss is empty, which action the chart offers).
window.VaultLab = (() => {
  const MINUS = '−';
  const plural = (n, one, many) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
  // $1,234.56: always cents, so a figure reconciles with the rows it sums.
  const money = (v) => '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  // +$12.00 / −$3.00
  const signed = (v) => (v < 0 ? MINUS : '+') + money(v);
  // "6 Oct" from the server's day (2026-10-06); null for no day.
  const shortDate = (iso) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
  };
  // Whole days between the day the prices are from and `now` (UTC days, as the server counts them); null when unknown.
  const priceAge = (iso, now = Date.now()) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    if (!m) return null;
    const today = Math.floor(now / 86400000);
    return Math.max(0, today - Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3]) / 86400000));
  };
  // The design: stale means the last refresh is more than a day old.
  const isStale = (iso, now) => { const d = priceAge(iso, now); return d != null && d > 1; };
  // Scryfall's own search for a card by name (the Vault names no seller and contacts none: the page lists the stores).
  const scryfallSearch = (name) => 'https://scryfall.com/search?q=' + encodeURIComponent(`!"${String(name).split(' // ')[0].trim()}"`);
  const sentence = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) + (/[.!?]$/.test(s) ? '' : '.') : '');

  // "Prices: Scryfall, 6 Oct" and, when stale, "(3 days old)".
  function pricesLine(iso, now) {
    const date = shortDate(iso);
    if (!date) return 'Prices: Scryfall, not synced yet';
    const age = priceAge(iso, now);
    return `Prices: Scryfall, ${date}` + (age > 1 ? ` (${age} days old)` : '');
  }

  // -- section 0: the decision strip (each counter reads named fields of its section's answer) -----------------------------
  // `dated` is the short date to carry on a price or total when the prices are stale, else null.
  const at = (dated) => (dated ? ` (${dated})` : '');

  function buyCounter(overlap, dated) {
    if (!overlap) return null;
    const s = overlap.summary;
    if (overlap.decks_checked === 0) return { quiet: true, headline: 'Nothing to decide', detail: 'No decks saved yet' };
    if (s.decks_analysed === 0) return { quiet: true, headline: 'Nothing to decide', detail: `${plural(overlap.decks_checked, 'saved deck', 'saved decks')} could not be read` };
    if (s.decks_needing_purchase === 0) {
      return { quiet: true, headline: 'Nothing to decide', detail: `${plural(s.decks_analysed, 'deck stands', 'decks stand')} on ${s.decks_analysed === 1 ? 'its' : 'their'} own` };
    }
    const unpriced = s.unpriced ? `, ${plural(s.unpriced, 'card has', 'cards have')} no price` : '';
    return {
      quiet: false,
      headline: `${plural(s.decks_needing_purchase, 'deck needs', 'decks need')} a purchase`,
      detail: `finish all: ${money(s.finish_all_cost)}${at(dated)}${unpriced}`,
    };
  }

  function sellCounter(spare, dated) {
    if (!spare) return null;
    if (spare.status === 'empty_collection') return { quiet: true, headline: 'Nothing to decide', detail: 'Your collection is empty' };
    if (spare.status === 'no_decks') return { quiet: true, headline: 'Needs decks first', detail: 'Save a deck to see spare copies' };
    const s = spare.summary;
    if (s.names === 0) return { quiet: true, headline: 'Nothing to decide', detail: 'your saved decks need every copy' };
    const unpriced = s.unpriced_copies ? `, ${plural(s.unpriced_copies, 'copy has', 'copies have')} no price` : '';
    return {
      quiet: false,
      headline: `${plural(s.names, 'card', 'cards')} you could sell`,
      detail: `spare value ${money(s.market_value)}${at(dated)}${unpriced}`,
    };
  }

  function pnlCounter(pnl, dated) {
    if (!pnl) return null;
    const s = pnl.summary;
    if (!s.biggest_gain && !s.biggest_loss) {
      return { quiet: true, headline: 'Nothing to decide', detail: s.reason ? sentence(s.reason).replace(/\.$/, '') : 'no priced holding is above or below cost' };
    }
    const loss = s.biggest_loss ? `Biggest known loss ${signed(s.biggest_loss.gain)}` : null;
    const gain = s.biggest_gain ? `biggest gain ${signed(s.biggest_gain.gain)}` : null;
    const tail = at(dated);
    if (loss && gain) return { quiet: false, headline: loss, detail: gain + tail };
    if (loss) return { quiet: false, headline: loss, detail: 'no holding is above cost' + tail };
    return { quiet: false, headline: `Biggest known gain ${signed(s.biggest_gain.gain)}`, detail: 'no holding is below cost' + tail };
  }

  // -- Buy ------------------------------------------------------------------------------------------------------------------
  function buyHeadline(overlap) {
    if (!overlap) return null;
    const s = overlap.summary;
    if (overlap.decks_checked === 0) return { kind: 'no_decks', text: 'Save a deck to see whether your decks can all be built at once.' };
    if (s.decks_analysed === 0) return { kind: 'unreadable', text: `None of your ${plural(overlap.decks_checked, 'saved deck', 'saved decks')} could be read.` };
    const skipped = overlap.decks_skipped_count;
    if (s.cards_to_buy === 0) {
      // An empty purchase list says nothing about a deck that was skipped: the line is qualified then (docs/deck-independence.md).
      const text = skipped
        ? `${s.decks_analysed} of ${overlap.decks_checked} decks checked; ${skipped} could not be read. The decks checked can be built at the same time from what you own.`
        : s.decks_analysed === 1 ? 'Your deck can be built from what you own.'
          : `All ${s.decks_analysed} decks can be built at the same time from what you own.`;
      return { kind: 'all_stand', text };
    }
    return { kind: 'buy', text: `${s.decks_needing_purchase} of ${plural(s.decks_analysed, 'deck', 'decks')} ${s.decks_needing_purchase === 1 ? 'needs' : 'need'} a purchase.`
      + (skipped ? ` ${skipped} could not be read.` : '') };
  }

  function deckStatus(deck) {
    if (deck.status === 'skipped') return { tone: 'skipped', text: 'could not be read' };
    if (deck.stands_alone) return { tone: 'ok', text: 'complete' };
    const lacks = deck.lacking_total != null ? deck.lacking_total : deck.lacking.length;
    return { tone: 'lacks', text: `lacks ${lacks}` };
  }
  function deckCost(deck, dated) {
    if (deck.status === 'skipped' || deck.stands_alone) return null;
    const parts = [];
    if (deck.cost_to_complete) parts.push(money(deck.cost_to_complete) + at(dated));
    if (deck.cost_unpriced) parts.push(`${deck.cost_unpriced} unpriced`);
    return parts.length ? parts.join(', ') : null;
  }
  // One lacking line: "2 to buy, 1 held by another deck".
  function lackingWords(l) {
    const parts = [];
    if (l.not_owned) parts.push(`${l.not_owned} to buy`);
    if (l.held_by_other_deck) parts.push(`${l.held_by_other_deck} held by another deck`);
    return parts.join(', ');
  }
  function purchaseOwnership(p) {
    const own = p.have ? `you own ${p.have}` : 'you own none';
    return `your decks need ${p.need_for_all}, ${own}`;
  }
  function purchaseBuy(p, dated) {
    return p.price_status === 'priced' ? `buy ${p.global_deficit} for ${money(p.cost)}${at(dated)}` : `buy ${p.global_deficit}, no price known`;
  }

  // -- Spare copies -----------------------------------------------------------------------------------------------------------
  function spareValueLine(s, dated) {
    if (!s || !s.copies) return null;
    const priced = s.priced_copies;
    const base = `${money(s.market_value)}${at(dated)} across ${plural(priced, 'priced copy', 'priced copies')}`;
    return s.unpriced_copies ? `${base}; ${s.unpriced_copies} ${s.unpriced_copies === 1 ? 'has' : 'have'} no price` : base;
  }
  function spareFigures(item, dated) {
    return {
      spare: `${item.spare} spare`,
      value: item.priced_copies ? money(item.market_value_of_spare) + at(dated) : 'no price',
      decks: item.deck_count === 0 ? 'in no deck' : `in ${plural(item.deck_count, 'deck', 'decks')}`,
    };
  }
  const humanize = (s) => (s ? s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '');
  function printingTitle(p) {
    const set = p.set && p.set.code ? p.set.code.toUpperCase() : '?';
    return `${set} #${p.collector_number}`;
  }
  function printingDetail(p) {
    return [p.printing, humanize(p.condition) !== 'Near mint' && humanize(p.condition) !== 'Mint' ? humanize(p.condition) : null]
      .filter(Boolean).join(' · ');
  }

  // -- Profit and loss --------------------------------------------------------------------------------------------------------
  function coverageLine(s) {
    const parts = [`Based on ${s.covered_copies.toLocaleString('en-US')} of ${plural(s.total_copies, 'copy', 'copies')}`];
    if (s.unknown_cost_copies) parts.push(`${s.unknown_cost_copies.toLocaleString('en-US')} ${s.unknown_cost_copies === 1 ? 'has' : 'have'} no price paid`);
    if (s.unpriced_market_copies) parts.push(`${s.unpriced_market_copies.toLocaleString('en-US')} ${s.unpriced_market_copies === 1 ? 'has' : 'have'} no current price`);
    return parts.join('; ') + '.';
  }
  const netLine = (net) => (net == null ? '' : net === 0 ? 'Together they are worth what you paid.'
    : `Together they are worth ${money(net)} ${net < 0 ? 'less' : 'more'} than you paid.`);
  const pnlHidden = (s) => (s && s.covered_copies === 0 ? `No profit and loss to show: ${s.reason || 'no priced holding'}.` : null);
  const pnlEmpty = (side) => `Nothing here: none of your priced holdings are ${side === 'winners' ? 'above' : 'below'} cost.`;
  const percent = (v) => (v == null ? '' : `${v < 0 ? MINUS : '+'}${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 1 })}%`);

  // -- Value over time (2c) ----------------------------------------------------------------------------------------------------
  // The one action the chart offers: from the profit and loss cohort's net gain (never from history, which mixes populations).
  function chartAction(pnl, spareStatus) {
    const net = pnl && pnl.summary ? pnl.summary.net_gain : null;
    if (net != null && net < 0) return { kind: 'losers', label: 'Show losers' };
    return spareStatus === 'ok' ? { kind: 'spare', label: 'Show spare copies' } : null;
  }
  function historyLine(summary) {
    if (!summary || summary.from == null || summary.market_start == null) return null;
    const change = summary.market_change;
    return `Market value ${shortDate(summary.from)} to ${shortDate(summary.to)}: ${money(summary.market_start)} to ${money(summary.market_end)}`
      + (change == null ? '' : ` (${change === 0 ? 'no change' : signed(change)})`);
  }

  return {
    plural, money, signed, shortDate, priceAge, isStale, sentence, pricesLine, scryfallSearch,
    buyCounter, sellCounter, pnlCounter,
    buyHeadline, deckStatus, deckCost, lackingWords, purchaseOwnership, purchaseBuy,
    spareValueLine, spareFigures, printingTitle, printingDetail, humanize,
    coverageLine, netLine, pnlHidden, pnlEmpty, percent,
    chartAction, historyLine,
  };
})();
