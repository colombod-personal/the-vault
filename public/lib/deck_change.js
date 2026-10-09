// What the deck page's "Change this deck" flow says about the server's answers (docs/deck-ideas-lab-design.md, task 0 under #163):
// the cards to cut and add, the check from POST /decks/validate-changes in plain words, the confirmation that names what will change.
// Every number and every reason is the server's; this file only words them and reads the address, so the page
// (public/views/deck_change.jsx) and the tests (tests/js/deck_change.test.mjs, tests/test_deck_change_page.py) read the same sentences.
window.VaultDeckChange = (() => {
  const MAX_ITEMS = 60, MAX_NAME = 300;  // the server's limits on cuts and adds (ChangesIn in vault/api/deck_api.py)
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const money = (v) => '$' + Number(v).toFixed(2);
  const signed = (v) => (v < 0 ? '−' : '+') + money(Math.abs(v));
  const nameKey = (n) => String(n).split(' // ')[0].trim().toLowerCase();

  // ?swap={"cut":["Card"],"add":["Card"]} (the Ideas view's "Swap into the deck"): null when the address has none,
  // { error } when it cannot be read, else { cut, add } with the names as they were written. Nothing here is trusted
  // beyond that: the adds are looked up in the card catalog and the whole proposal is checked by the server.
  function parseSwap(raw) {
    if (raw == null || raw === '') return null;
    const bad = { error: 'The swap in the address could not be read, so nothing was filled in. Choose the cards below.' };
    let o;
    try { o = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { return bad; }  // the route carries it already read (lib/ideas.js parseSwap) or as written
    if (!o || typeof o !== 'object' || Array.isArray(o)) return bad;
    const names = (v) => {
      if (v == null) return [];
      if (!Array.isArray(v)) return null;
      const out = v.map((s) => (typeof s === 'string' ? s.trim() : null));
      return out.some((s) => !s || s.length > MAX_NAME) ? null : out;
    };
    const cut = names(o.cut), add = names(o.add);
    if (!cut || !add || cut.length > MAX_ITEMS || add.length > MAX_ITEMS || cut.length + add.length === 0) return bad;
    return { cut, add };
  }

  // The same cards as `name × copies`, in the order they were first chosen.
  function copies(names) {
    const out = [], at = {};
    for (const n of names) {
      const k = nameKey(n);
      if (k in at) out[at[k]].n++; else { at[k] = out.length; out.push({ name: n, n: 1 }); }
    }
    return out;
  }

  // "Cut 2 cards, add 2 cards to Sliver Swarm": the one sentence that names what will change.
  function summaryLine(deckName, cuts, adds) {
    const c = cuts.length, a = adds.length;
    if (c && a) return `Cut ${plural(c, 'card', 'cards')}, add ${plural(a, 'card', 'cards')} to ${deckName}`;
    if (c) return `Cut ${plural(c, 'card', 'cards')} from ${deckName}`;
    if (a) return `Add ${plural(a, 'card', 'cards')} to ${deckName}`;
    return `No change to ${deckName}`;
  }

  // What the check answered for exactly this proposal; a different one has to be checked again.
  const proposalKey = (format, cuts, adds) => JSON.stringify([format, cuts.map(nameKey), adds.map(nameKey)]);

  const KINDS = {
    cut_not_in_deck: 'Not in the deck', unknown_card: 'Not in the card catalog', not_legal: 'Not legal',
    color_identity: 'Outside the deck’s colour identity', no_price: 'No price known', over_budget: 'Over budget',
    deck_size: 'Deck size', sideboard_size: 'Sideboard size', too_many_copies: 'Too many copies', no_commander: 'No commander',
  };
  function issueLabel(kind) {
    const after = String(kind).startsWith('result_');
    const base = String(kind).replace(/^result_/, '');
    const label = KINDS[base] || base.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
    return after ? `${label} (after the change)` : label;
  }
  // { label, card, detail }: the label is the Vault's plain name for the kind, the detail is the server's own words.
  const issueLine = (i) => ({ label: issueLabel(i.kind), card: i.card || null, detail: i.detail || '' });

  // The check's verdict. A plan the check cannot make sense of (a card that is not in the deck, one the catalog does not know)
  // cannot be saved; any other problem is shown and may be saved over, with the button saying so.
  const BLOCKING = ['cut_not_in_deck', 'unknown_card'];
  function verdict(result) {
    const n = result.issues.length;
    return n === 0 ? { good: true, text: 'The check found no problems.' }
      : { good: false, text: `The check found ${plural(n, 'problem', 'problems')}.` };
  }
  function blocked(result, cuts, adds) {
    if (!cuts.length && !adds.length) return 'Choose a card to cut or add first.';
    const hit = result.issues.find((i) => BLOCKING.includes(i.kind));
    return hit ? `${issueLabel(hit.kind)}: ${hit.card || ''}. Take it out of the change to save.` : null;
  }
  function existingLine(result) {
    const n = (result.existing_issues || []).length;
    return n ? `${plural(n, 'problem', 'problems')} the deck already had ${n === 1 ? 'is' : 'are'} not counted against this change.` : null;
  }
  const countsLine = (result) => `The list would have ${plural(result.cards_after, 'card', 'cards')} (cut ${result.cuts}, add ${result.adds}).`;

  // What the change does to the price: the adds, the cuts and the deck. Prices are Scryfall's cheapest known (the Vault's daily load).
  function costLines(result) {
    const out = [];
    if (result.adds) out.push(`The cards to add cost ${money(result.added_cost_usd)}.`);
    if (result.cuts) {
      const unpriced = result.cut_unpriced || [];
      out.push(`The cards cut are worth ${money(result.cut_value_usd)}` + (unpriced.length ? ` (no price known for ${unpriced.join(', ')})` : '') + '; a cut is not refunded.');
    }
    out.push(`The deck’s price goes from ${money(result.deck_cost_before_usd)} to ${money(result.deck_cost_after_usd)} (${signed(result.net_change_usd)}).`);
    return out;
  }

  // What the person owns of a card to add: the coverage line the server gave for the new list, found by name.
  function coverageByName(coverage) {
    const by = {};
    for (const l of (coverage && coverage.cards) || []) by[nameKey(l.name)] = l;
    return by;
  }
  function ownershipLine(name, line) {
    if (!line) return `${name}: not checked`;
    if (line.have >= line.need) return `${name}: you own ${line.need > 1 ? `all ${line.need}` : 'it'}`;
    if (line.have > 0) return `${name}: you own ${line.have} of ${line.need}`;
    return `${name}: you do not own it` + (line.unit_price != null ? ` (${money(line.unit_price)} to buy)` : '');
  }
  function toBuyLine(coverage) {
    if (!coverage) return null;
    if (!coverage.missing_cost && !coverage.missing_unpriced) return 'After the change you would own every card in this deck.';
    return `To finish the deck after the change: ${coverage.missing_unpriced ? '≥ ' : ''}${money(coverage.missing_cost || 0)}` +
      (coverage.missing_unpriced ? ` (${plural(coverage.missing_unpriced, 'card', 'cards')} without a price)` : '') + '.';
  }

  // The confirmation button names the change; with problems it says it saves over them.
  const confirmLabel = (deckName, cuts, adds, result) => (result.issues.length ? 'Save anyway: ' : '') + summaryLine(deckName, cuts, adds);
  const fromArchidekt = 'Only the Vault’s copy of this deck changes: the deck on Archidekt does not. Cards are checked against the Vault’s saved copy.';
  const savedLine = (deckName, cuts, adds) => `Saved. ${summaryLine(deckName, cuts, adds)}. The earlier list is in the History tab.`;
  const savedArchidekt = 'This page still shows the list on Archidekt, and “Update saved copy” would put that list back over this change.';

  return { MAX_ITEMS, parseSwap, copies, summaryLine, proposalKey, issueLabel, issueLine, verdict, blocked, existingLine, countsLine,
    costLines, coverageByName, ownershipLine, toBuyLine, confirmLabel, fromArchidekt, savedLine, savedArchidekt, nameKey };
})();
