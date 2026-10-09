// What the deck page's "Opening turns" tab says about the server's answers (docs/deck-simulation-design.md, #138): the intro and the
// "As played" line, the headline tiles, the four odds panels, the sample games turn by turn, the states (too few cards, asked too
// often, failed), the curve and its table, the provenance line.
// Every number is the server's (POST /decks/simulate and /decks/stats); this file only words them and sizes bars, so the page
// (public/views/deck_opening.jsx) and the tests (tests/js/deck_sim.test.mjs, tests/test_deck_sim_page.py) read the same sentences.
window.VaultDeckSim = (() => {
  const GAMES = 1000, SAMPLES = 5, DEFAULT_TURNS = 6, TURN_CHOICES = [4, 6, 8, 10];
  const MAX_SEED = 2 ** 31 - 1;          // what the endpoint accepts as a seed (SimulateIn in vault/api/deck_api.py)
  const ANALYSES_PER_MINUTE = 30;        // DECK_LIMIT in vault/api/deck_api.py (a test keeps the two equal)
  const HAND_SIZE = 7;                   // vault/simulate.py: a deck needs this many cards plus one for each turn
  const num = (n) => Number(n).toLocaleString('en-US');
  const pct = (v) => Number(v).toFixed(1) + '%';
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const list = (a) => a.join(', ');

  // The server sends each plan as one string, "Card: reason". The reasons never contain a colon but some card names do
  // ("Circle of Protection: Red"), so the split is at the last ": ".
  function splitPlan(s) {
    const i = String(s).lastIndexOf(': ');
    return i < 0 ? { card: String(s), why: '' } : { card: s.slice(0, i), why: s.slice(i + 2) };
  }

  // "Name · commander · 100 cards · commander: Xenagos · colours R G": the deck first (AGENTS.md section 4), from the answer's `deck.overview`.
  function deckLine(name, o) {
    const parts = [name];
    if (o) {
      if (o.format) parts.push(o.format);
      if (o.cards != null) parts.push(plural(o.cards, 'card', 'cards'));
      if (o.commanders && o.commanders.length) parts.push((o.commanders.length === 1 ? 'commander: ' : 'commanders: ') + list(o.commanders));
      if (o.color_identity) parts.push(o.color_identity === 'colorless' ? 'colourless' : 'colours ' + o.color_identity.split('').join(' '));
    }
    return parts.filter(Boolean).join(' · ');
  }

  // "at 1,000 games a percentage is good to about 3 points either way": margin_points is the server's (1.96 * sqrt(0.25 / games), in points).
  const marginText = (r) => {
    if (r.margin_points == null) return '';
    const m = r.margin_points;
    return `Run it again with a new seed and the figures move by a few points: at ${num(r.games)} games a percentage is good to about ${m >= 3 ? Math.round(m) : m} points either way.`;
  };
  const intro = (r) => ({
    lead: 'A simulation, not a prediction.',
    body: `The Vault played this list ${num(r.games)} times with a simple player (no opponent) and counted what happened in the first ${r.turns} turns.`,
    margin: marginText(r),
  });

  function asPlayed(r) {
    const draw = r.multiplayer ? 'multiplayer: everyone draws on turn 1' : r.on_the_play ? 'two players: no draw on turn 1 on the play' : 'two players';
    return [r.format, draw, r.on_the_play ? 'on the play' : 'on the draw', `${r.turns} turns`, `${num(r.games)} games`, `seed ${r.seed}`].join(' · ')
      + ' (same deck, same seed, same figures)';
  }

  // The player's rules, fixed copy about the player only; what is not modelled is the server's own `assumptions`.
  const HOW_TO_READ = [
    'The player keeps 7 cards with 2 to 5 lands, otherwise takes a mulligan (up to two).',
    'Every turn: draw, play one land, cast the biggest thing the mana allows (ramp first in the early turns).',
    'Figures are shares of the games, or averages over them.',
  ];

  // The four headline figures, named as the answer names them. The last three exist only from 5 turns on.
  const TILES = [['five_mana_by_turn_5', 'Five mana by turn 5'], ['missed_a_land_drop_by_turn_4', 'Missed a land drop by turn 4'],
    ['discarded_by_turn_5', 'Discarded by turn 5'], ['mulligan_rate', 'Mulligan rate']];
  const tiles = (r) => TILES.filter(([k]) => r.headline && r.headline[k] != null)
    .map(([key, label]) => ({ key, label, value: pct(r.headline[key]), unit: 'of simulated games' }));

  const turnRows = (r, field, say, scale) => r.per_turn.map((t) => ({
    label: 'Turn ' + t.turn, width: Math.max(0, Math.min(100, (t[field] / scale) * 100)), value: say(t[field]),
  }));
  const ofGames = (v) => pct(v) + ' of games';

  // One panel per question; each group is a list of rows (turn, bar width in per cent, the value in words) and a caption.
  // A bar is never the only carrier of the value: the number is always printed next to it.
  function oddsPanels(r) {
    const scale = (...fields) => Math.max(8, Math.ceil(Math.max(...fields.flatMap((f) => r.per_turn.map((t) => t[f])))));
    const manaScale = scale('mana_available', 'mana_spent'), handScale = scale('cards_in_hand');
    const plan = (r.discard_may_be_the_plan || []).map(splitPlan);
    return [
      { id: 'land', title: 'Land drops', blurb: 'Did the player play a land this turn?', groups: [
        { rows: turnRows(r, 'land_drop', ofGames, 100), tone: 'gold' },
        { caption: 'Every land drop made so far, with no miss since turn 1:', rows: turnRows(r, 'all_land_drops_so_far', ofGames, 100), tone: 'copper' }] },
      { id: 'mana', title: 'Mana by turn', blurb: 'Average mana the player had, and how much of it the simulated player spent.', groups: [
        { rows: turnRows(r, 'mana_available', (v) => v.toFixed(2) + ' available', manaScale), tone: 'gold' },
        { caption: 'Spent on spells (average):', rows: turnRows(r, 'mana_spent', (v) => v.toFixed(2) + ' spent', manaScale), tone: 'copper' },
        { caption: 'Stuck: every spell in hand cost more than the mana (turn 1 with no one-drops is normal):',
          rows: turnRows(r, 'every_spell_in_hand_cost_too_much', ofGames, 100), tone: 'copper' }] },
      { id: 'hand', title: 'Cards in hand', blurb: 'Average hand size at the end of each turn.', groups: [
        { rows: turnRows(r, 'cards_in_hand', (v) => v.toFixed(2) + ' cards', handScale), tone: 'gold' }] },
      // a deck that wants to discard is not told it has a fault: neutral bars when the deck's own plan says so
      { id: 'discard', title: 'Discard risk', blurb: 'Share of games in which the player has had to discard to hand size by this turn (cumulative).', groups: [
        { rows: turnRows(r, 'discarded_by_now', ofGames, 100), tone: plan.length ? 'neutral' : 'danger' }], plan: planNote(plan) },
    ];
  }

  function planNote(plan) {
    if (!plan.length) return null;
    return {
      title: 'Discarding may be part of this deck’s plan.', items: plan,
      note: (plan.some((p) => p.why === 'no maximum hand size') ? 'The simulation lifts the hand limit once a “no maximum hand size” card is in play. ' : '')
        + 'A deck that wants cards in its graveyard or swaps hands may be glad to discard: read the figures as a fact about the deck, not as a fault.',
    };
  }

  const unmatchedLine = (u) => (u && u.length ? `Not in the card catalog: ${list(u)}` : '');
  const UNMATCHED_NOTE = 'These cards are left out of the games, so the deck plays with fewer cards than the list has.';

  // A sample game: the first of the run, as it came up (never picked).
  function gameSummary(g, index) {
    const first = g.turns.slice(0, Math.min(3, g.turns.length));
    const made = first.filter((t) => t.land).length;
    return {
      title: `Game ${index + 1}`,
      facts: `${g.mulligans ? 'mulligan ' + g.mulligans + ', ' : ''}kept ${g.opening_hand.length} · land drops made in turns 1 to ${first.length}: ${made} of ${first.length}`,
      hand: list(g.opening_hand),
    };
  }
  const samplesIntro = (r) => `The first ${r.samples.length} of the ${num(r.games)} games, as they came up. They are not picked to be typical, lucky or unlucky.`;

  // One turn of a sample game, as the table's cells.
  function turnCells(t, r) {
    const skipped = r.on_the_play && !r.multiplayer && t.turn === 1;
    const notes = [];
    if (t.nothing_affordable) notes.push({ tone: 'warn', text: 'Nothing castable: every spell in hand costs more than the mana' });
    if (t.discarded && t.discarded.length) notes.push({ tone: 'bad', text: 'Discarded ' + list(t.discarded) });
    return {
      turn: t.turn,
      drew: t.drew || (skipped ? 'no draw (on the play)' : 'no card drawn'), drewNone: !t.drew,
      land: t.land || 'no land', landNone: !t.land,
      mana: `${t.mana} (spent ${t.spent})`,
      cast: t.cast.length ? list(t.cast) : 'nothing', castNone: !t.cast.length,
      hand: t.hand, notes,
    };
  }

  // What went wrong, in words, from an ApiError-like { status, message, retryAfter }.
  function problem(e, turns) {
    const status = e && e.status, message = (e && e.message) || '';
    if (status === 400 && /too few to play/.test(message)) {
      return { kind: 'toofew', title: `Not enough cards to play ${turns} turns.`, quote: message,
        advice: `Add cards, or choose fewer turns (a deck needs ${HAND_SIZE} cards plus one for each turn).` };
    }
    if (status === 429) {
      const seconds = Math.max(1, Math.round((e && e.retryAfter) || 60));
      return { kind: 'limit', title: 'Too many analyses this minute.', seconds,
        detail: `The Vault allows ${ANALYSES_PER_MINUTE} a minute for each person.` };
    }
    return { kind: 'other', title: 'Couldn’t work out the opening turns:', detail: message || 'Something went wrong.' };
  }
  const waitText = (s) => (s > 0 ? `You can play again in ${plural(s, 'second', 'seconds')}.` : 'You can play again now.');

  // The button: what it says, and whether it can be pressed.
  function playAgain({ busy, wait }) {
    if (wait > 0) return { label: `Play again in ${wait} s`, disabled: true };
    if (busy) return { label: 'Playing again…', disabled: true };
    return { label: 'Play again with a new seed', disabled: false };
  }

  // A new seed for "Play again": any whole number the endpoint accepts. `rand` is Math.random.
  const newSeed = (rand) => Math.floor(rand() * (MAX_SEED + 1));

  // The mana curve (cards that are not lands, by mana value), from POST /decks/stats: bars (height in per cent of the tallest), the
  // text alternative that lists every value, the caption, and the rows of the table disclosure.
  function curve(stats) {
    const entries = Object.entries(stats.curve);
    const max = Math.max(1, ...entries.map(([, n]) => n));
    const caption = `${plural(stats.lands, 'land', 'lands')}, ${plural(stats.nonland, 'other card', 'other cards')}, average mana value of the non-land cards ${stats.average_mana_value_nonland}.`
      + (stats.by_section && stats.by_section.commander ? ' The commander is in the command zone.' : '');
    return {
      bars: entries.map(([mv, n]) => ({ mv, n, height: (n / max) * 100 })),
      label: 'Mana curve, number of non-land cards by mana value. ' + entries.map(([mv, n]) => `${mv}: ${n}`).join(', ') + '.',
      caption,
    };
  }

  // The provenance line (docs/compliance.md): computed by the Vault, from which sources, as of when; the mana of rocks and creatures is
  // the Vault's reading of card text and says so. `notice` is the server's Fan Content notice, repeated as the sources require.
  function provenance(p) {
    const inputs = (p && p.inputs) || [];
    return {
      lead: 'Computed by the Vault from',
      sources: inputs.map((i) => ({ name: i.source, origin: i.origin || '', as_of: i.as_of || '', url: i.url || '' })),
      reading: 'The mana a card makes is the Vault’s reading of its text, not Scryfall’s or Wizards’ figure.',
      notice: (p && p.notice) || (inputs.find((i) => i.notice) || {}).notice || '',
    };
  }
  const sourceText = (s) => `${s.name}${s.origin ? ': ' + s.origin : ''}${s.as_of ? ', as of ' + s.as_of : ''}`;

  return { GAMES, SAMPLES, DEFAULT_TURNS, TURN_CHOICES, MAX_SEED, ANALYSES_PER_MINUTE, HOW_TO_READ, UNMATCHED_NOTE,
    splitPlan, deckLine, intro, asPlayed, tiles, oddsPanels, unmatchedLine, gameSummary, samplesIntro, turnCells,
    problem, waitText, playAgain, newSeed, curve, provenance, sourceText, pct };
})();
