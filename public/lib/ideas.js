// What the Ideas page (public/views/ideas.jsx) says about the server's answers: every number is the server's (docs/deck-ideas-lab-design.md),
// and this file only words and formats them, so the page and its tests (tests/js/ideas.test.mjs, tests/test_ideas_page.py) read the same
// sentences. Nothing here adds, ranks or compares collection data: the lanes, their order, the statuses, the counts and the matches
// are computed by the server (GET /decks/{id}/ideas and /ideas/alternatives); the browser filters what it was sent and places it in a grid.
window.VaultIdeas = (() => {
  const plural = (n, one, many) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
  const money = (v) => '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  // "6 Oct" from the server's day (2026-10-06); null for no day.
  const shortDate = (iso) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
  };
  const scryfallSearch = (name) => 'https://scryfall.com/search?q=' + encodeURIComponent(`!"${String(name).split(' // ')[0].trim()}"`);

  // The formats the server's analysis supports (vault/deck_tools.py FORMATS; tests/test_ideas_page.py compares the two).
  const FORMATS = ['commander', 'standard', 'pioneer', 'modern', 'legacy', 'vintage', 'pauper', 'brawl', 'standardbrawl',
    'historic', 'timeless', 'oathbreaker', 'paupercommander', 'premodern', 'penny', 'duel', 'predh', 'oldschool', 'gladiator', 'alchemy'];

  // The weight budget of the design: a first render requests at most MAX_IMAGES images, none larger than a thumbnail; a lane with
  // more than WINDOW_AT cards is drawn windowed; alternatives are asked ALT_PAGE at a time (the target's image and the alternatives'
  // thumbnails stay under MAX_IMAGES).
  const MAX_IMAGES = 12, ALT_PAGE = 10, WINDOW_AT = 40, LANE_PAGE = 25, WINDOW_ROWS = 12;

  // -- addresses: #/ideas, #/ideas/{deck}, #/ideas/{deck}/{card}; and the deck page's change flow, #/decks/{deck}?swap={json} --------------
  const DECK_ID = /^[0-9]{1,9}$/;
  const ideasRoute = (deckId, card) => ({ view: 'ideas', ...(deckId && DECK_ID.test(String(deckId)) ? { deckId: String(deckId), ...(card ? { card: String(card).slice(0, 300) } : {}) } : {}) });
  const ideasHash = (route) => '#/ideas' + (route && route.deckId ? '/' + encodeURIComponent(route.deckId) + (route.card ? '/' + encodeURIComponent(route.card) : '') : '');
  // The contract with the deck page: `swap` is {"cut":["Card"],"add":["Card"]}, names only; anything else is ignored.
  const SWAP_MAX = 20, NAME_MAX = 300;
  const cleanNames = (v) => (Array.isArray(v) && v.length <= SWAP_MAX && v.every((n) => typeof n === 'string' && n.trim() && n.length <= NAME_MAX) ? v.map((n) => n.trim()) : null);
  function parseSwap(text) {
    if (!text) return null;
    let o;
    try { o = JSON.parse(text); } catch { return null; }
    if (!o || typeof o !== 'object' || Array.isArray(o)) return null;
    const cut = cleanNames(o.cut === undefined ? [] : o.cut), add = cleanNames(o.add === undefined ? [] : o.add);
    return cut && add && (cut.length || add.length) ? { cut, add } : null;
  }
  const swapQuery = (swap) => (swap ? 'swap=' + encodeURIComponent(JSON.stringify({ cut: swap.cut || [], add: swap.add || [] })) : '');
  const swapHash = (deckId, cut, add) => `#/decks/${encodeURIComponent(deckId)}?${swapQuery({ cut, add })}`;

  // -- the header ----------------------------------------------------------------------------------------------------------------
  function headline(summary) {
    return {
      covered: `${summary.covered.toLocaleString('en-US')} of ${summary.copies.toLocaleString('en-US')} covered`,
      missing: `${summary.missing.toLocaleString('en-US')} missing`,
      borrowed: `${summary.borrowed.toLocaleString('en-US')} borrowed`,
    };
  }
  // "Fully covered": nothing lacking, and no copy is borrowed (the deck's cards may be wanted by other decks: that is the other deck's problem).
  const isCovered = (s) => !!s && s.complete === true && s.missing === 0 && s.borrowed === 0;
  const COVERED_TEXT = 'Every card is in your collection and no copy is borrowed. Nothing to decide here.';
  // Which state the page is in: the five of the design, and the one the Vault must not confuse with "nothing found" (no role known).
  function pageState({ deckId, summary, card, target }) {
    if (!deckId) return 'start';
    if (card) return target && target.reason === 'no_role' ? 'no-role' : target && target.reason ? 'no-alternative' : 'alternatives';
    return isCovered(summary) ? 'covered' : 'deck';
  }
  function deckFacts(overview, chosenFormat) {
    const o = overview || {};
    const out = [];
    out.push(chosenFormat || o.format || 'format unknown');
    if (o.commanders && o.commanders.length) out.push(o.commanders.join(' + '));
    if (o.cards != null) out.push(plural(o.cards, 'card', 'cards'));
    return out;
  }
  const colourLetters = (ci) => (typeof ci === 'string' ? ci.split('').filter((c) => 'WUBRGC'.includes(c)) : Array.isArray(ci) ? ci : []);

  // -- the start: a saved deck against the collection (the deck list's summary) ----------------------------------------------------
  function tileLine(deck) {
    const s = deck.summary;
    if (!s) return { owned: 'Checking your collection…', width: 0 };
    return { owned: `${s.have.toLocaleString('en-US')} of ${s.need.toLocaleString('en-US')} cards owned`, width: s.need ? Math.round((s.have / s.need) * 100) : 100,
      missing: s.missing ? `${s.missing.toLocaleString('en-US')} missing` : 'complete' };
  }

  // -- the lanes and their rows ----------------------------------------------------------------------------------------------------
  const laneTitle = (lane) => `${lane.label} (${lane.cards.toLocaleString('en-US')})`;
  function laneNote(lane) {
    const parts = [];
    if (lane.missing) parts.push(`${lane.missing.toLocaleString('en-US')} missing`);
    if (lane.borrowed) parts.push(`${lane.borrowed.toLocaleString('en-US')} borrowed`);
    return parts.join(' · ');
  }
  // The row's dot and word: from the server's status and counts. Colour is never the only signal: the word is always there.
  function rowStatus(c) {
    const toBuy = c.not_owned > 0, held = c.held_by_other_deck > 0;
    if (c.status === 'owned') {
      const alsoWanted = c.also_wanted_by && c.also_wanted_by.length;
      return { tone: 'owned', word: c.need > 1 ? `${c.gets} of ${c.need}` : 'owned', note: alsoWanted ? `also wanted by ${c.also_wanted_by.join(', ')}` : '' };
    }
    const where = held ? `held by ${c.borrowed_from || 'another deck'}` : '';
    if (toBuy && held) return { tone: 'missing', word: c.status === 'partial' ? `${c.gets} of ${c.need}` : 'missing', note: `${c.not_owned} to buy, ${c.held_by_other_deck} ${where}` };
    if (held) return { tone: 'borrowed', word: c.status === 'partial' ? `${c.gets} of ${c.need}` : 'borrowed', note: where };
    return { tone: c.status === 'partial' ? 'partial' : 'missing', word: c.status === 'partial' ? `${c.gets} of ${c.need}` : 'missing', note: toBuy ? `${c.not_owned} to buy` : '' };
  }
  const rowLabel = (c, laneLabel) => {
    const s = rowStatus(c);
    return `${c.card}, ${s.word}${s.note ? ', ' + s.note : ''}${laneLabel ? ', ' + laneLabel + ' lane' : ''}`;
  };
  // What needs a decision (the phone's "Missing" and "Borrowed" lists): the server's own flags, in the lanes' order.
  const needsDecision = (c) => c.status !== 'owned';
  const decisionCards = (lanes) => {
    const all = lanes.flatMap((l) => l.items.map((c) => ({ ...c, laneLabel: l.label })));
    return all.filter((c) => c.status === 'missing').concat(all.filter((c) => c.status === 'partial'));
  };
  const borrowedCards = (lanes) => lanes.flatMap((l) => l.items.filter((c) => c.borrowed).map((c) => ({ ...c, laneLabel: l.label })));
  // The filter the phone opens on: what needs a decision first.
  const defaultFilter = (summary) => (summary.missing > 0 ? 'missing' : summary.borrowed > 0 ? 'borrowed' : 'all');
  const moreLabel = (lane, shown) => {
    const left = Math.max(0, lane.total - shown);
    return left ? `Show ${Math.min(left, LANE_PAGE).toLocaleString('en-US')} more of ${lane.label.toLowerCase()} (${left.toLocaleString('en-US')} left)` : 'Show more';
  };

  // Windowing: which rows of a long lane are drawn for a scroll position (a fixed row height, a few rows either side).
  const needsWindow = (n) => n > WINDOW_AT;
  function windowRange(scrollTop, rowH, viewH, total, overscan = 4) {
    const start = Math.max(0, Math.floor(scrollTop / rowH) - overscan);
    const end = Math.min(total, Math.ceil((scrollTop + viewH) / rowH) + overscan);
    return { start, end };
  }

  // -- the selected card -------------------------------------------------------------------------------------------------------------
  // The Vault's own roles (docs/functional-equivalents.md): a plain name and its strength; every one is found by a rule over the Oracle text.
  const lowerFirst = (s) => (s ? s.charAt(0).toLowerCase() + s.slice(1) : s);
  const roleWords = (roles) => (roles || []).map((r) => `${lowerFirst(r.name)} (${r.strength})`);
  const coreRoles = (roles) => (roles || []).filter((r) => r.strength === 'core');
  const roleLine = (roles) => {
    const core = coreRoles(roles).map((r) => `${lowerFirst(r.name)} (core)`), side = (roles || []).filter((r) => r.strength !== 'core').map((r) => lowerFirst(r.name));
    if (!core.length && !side.length) return 'The Vault knows no role for this card yet';
    return `Does: ${core.length ? core.join(', ') : 'nothing as its main job'}${side.length ? `; on the side: ${side.join(', ')}` : ''}`;
  };
  // What a candidate does, what of the card's jobs it does not do, and what it adds: from the server's role lists, never free text.
  const altDoes = (a) => { const core = coreRoles(a.roles).map((r) => lowerFirst(r.name)); return core.length ? `Does: ${core.join(', ')}` : ''; };
  const altLacks = (a, targetName) => (a.lacks && a.lacks.length ? `Does not: ${a.lacks.map((r) => lowerFirst(r.name)).join(', ')} (${targetName} does)` : '');
  const altExtra = (a) => (a.extra && a.extra.length ? `Also: ${a.extra.map((r) => lowerFirst(r.name)).join(', ')}` : '');
  const article = (word) => (/^[aeiou]/i.test(word) ? 'an' : 'a');
  const typeNote = (a, targetName) => (a.type_note ? `${article(a.type_note.candidate)} ${a.type_note.candidate.toLowerCase()}, where ${targetName} is ${article(a.type_note.target)} ${a.type_note.target.toLowerCase()}.` : '');
  const TIERS = { same_job: 'Same job', similar: 'Similar, with a difference' };
  const tierHeading = (tier, n) => `${TIERS[tier]} (${n.toLocaleString('en-US')})`;
  const similarToggle = (n, open) => (open ? `Hide similar, with a difference (${n.toLocaleString('en-US')})` : `Show similar, with a difference (${n.toLocaleString('en-US')})`);
  const byTier = (items, tier) => (items || []).filter((x) => x.tier === tier);
  function targetStatus(t) {
    if (!t || t.status == null) return { tone: 'owned', word: 'not in the deck', note: '' };
    return rowStatus({ ...t, card: t.card, also_wanted_by: [] });
  }
  // "The deck lists 2; this deck holds 1 under the allocation; 1 to buy" built from the server's fields only.
  function allocationLine(t) {
    if (!t || t.status == null) return 'This card is not in the deck.';
    const bits = [`The deck lists ${t.need}`, `this deck holds ${t.gets}`];
    if (t.not_owned > 0) bits.push(`${t.not_owned} to buy`);
    if (t.held_by_other_deck > 0) bits.push(`${t.held_by_other_deck} held by ${t.borrowed_from || 'another deck'}`);
    return bits.join('; ') + '.';
  }
  const heading = (t) => (t && t.status === 'owned' ? 'Other cards you own that do the same job' : t && t.status == null ? 'Owned cards that do this job' : 'Owned alternatives');
  const TEXT_CREDIT = 'Oracle text is Wizards of the Coast\'s, via Scryfall. Unofficial Fan Content, not endorsed by Wizards or Scryfall.';
  const ownedText = (a) => `${a.copies_owned} owned` + (a.borrowed ? '' : a.copies_free < a.copies_owned ? `, ${a.copies_free} free` : '');
  function altBadges(a) {
    const out = [];
    if (a.borrowed) out.push({ kind: 'borrowed', text: `borrowed from ${a.borrowed_from || 'another deck'}` });
    if (a.in_deck > 0) out.push({ kind: 'in-deck', text: `${a.in_deck} already in the deck` });
    return out;
  }
  const mv = (n) => (n == null ? null : `MV ${Number.isInteger(n) ? n : n.toFixed(1)}`);

  // Buy: the cheapest known price, dated; the Vault contacts no shop, so it points to the card's Scryfall page.
  function buyLabel(buy) {
    if (!buy || buy.price_status !== 'priced' || buy.cost == null) return 'Buy (no price known)';
    const day = shortDate(buy.price_date);
    return `Buy ${buy.quantity > 1 ? buy.quantity + ' for ' : ''}${money(buy.cost)} (Scryfall${day ? ', ' + day : ''})`;
  }
  const buyPlain = (buy) => {
    if (!buy || buy.price_status !== 'priced' || buy.cost == null) return 'no price known';
    const day = shortDate(buy.price_date);
    return `${money(buy.cost)} (Scryfall${day ? ', ' + day : ''})`;
  };
  const moveText = (move) => `Move from ${move.from_deck.name}`;
  const moveSteps = (move, cardName, toDeckName) =>
    `Move ${move.quantity > 1 ? move.quantity + ' copies' : 'a copy'} of ${cardName} from ${move.from_deck.name} to ${toDeckName}. The Vault changes no deck by itself: take the card out of ${move.from_deck.name}, then add it here.`;
  const PRICE_NOTE = (date) => `Prices are Scryfall's cheapest known${shortDate(date) ? ', from ' + shortDate(date) : ''}; the Vault contacts no shop.`;
  const ROLES_NOTE = 'Roles are the Vault\'s own, found by rules over the Oracle text: a hint, not proof that two cards play alike.';
  const NO_ROLE_NOTE = 'The Vault knows no role for this card yet, so it cannot look for cards that do the same job. That is not the same as "you own nothing like it".';
  const thumbAlt = (name, artist) => `${name}${artist ? ', illustrated by ' + artist : ''}`;
  // Scryfall serves each image at fixed sizes; the lane and the panel ask for the smallest (146 px wide), the zoom for the normal one.
  const thumbUrl = (url) => (typeof url === 'string' ? url.replace('/normal/', '/small/') : url);

  // -- combos (asked for on request: the deck's card names go to Commander Spellbook only then) ----------------------------------------
  const ownedCombos = (combos) => (combos && combos.checked !== false && combos.combos ? combos.combos.filter((c) => c.owned) : []);
  const comboKeys = (combos) => new Set(ownedCombos(combos).flatMap((c) => c.cards.map((n) => n.toLowerCase())));
  const comboLine = (c) => `${c.cards.join(' + ')}${c.produces && c.produces.length ? ': ' + c.produces.slice(0, 3).join(', ') : ''}`;

  const errorText = (e) => {
    if (e && e.status === 0) return "The Vault couldn't be reached. Check your connection.";
    if (e && e.status === 429) return 'That is a lot of requests in a minute. Wait a moment and try again.';
    return (e && e.message) || 'Unknown error';
  };

  return {
    FORMATS, MAX_IMAGES, ALT_PAGE, WINDOW_AT, LANE_PAGE, WINDOW_ROWS, COVERED_TEXT,
    plural, money, shortDate, scryfallSearch,
    ideasRoute, ideasHash, parseSwap, swapQuery, swapHash,
    headline, isCovered, pageState, deckFacts, colourLetters, tileLine,
    laneTitle, laneNote, rowStatus, rowLabel, needsDecision, decisionCards, borrowedCards, defaultFilter, moreLabel,
    needsWindow, windowRange,
    roleLine, roleWords, coreRoles, altDoes, altLacks, altExtra, typeNote, tierHeading, similarToggle, byTier, TIERS, TEXT_CREDIT,
    allocationLine, targetStatus, heading, ownedText, altBadges, mv,
    buyLabel, buyPlain, moveText, moveSteps, PRICE_NOTE, ROLES_NOTE, NO_ROLE_NOTE, thumbAlt, thumbUrl,
    ownedCombos, comboKeys, comboLine, errorText,
  };
})();
