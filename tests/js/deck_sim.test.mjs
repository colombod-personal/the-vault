// public/lib/deck_sim.js: what the deck page's "Opening turns" tab says (docs/deck-simulation-design.md, #138), and the client call it makes.
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest; tests/test_deck_sim_page.py feeds it the server's real answers)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/deck_sim.js', import.meta.url), 'utf8'), sandbox);
const S = sandbox.window.VaultDeckSim;
const J = (o) => JSON.parse(JSON.stringify(o));  // objects made in the sandbox have its prototypes

const perTurn = (turns, f = {}) => Array.from({ length: turns }, (_, i) => ({
  turn: i + 1, land_drop: 90 - i * 5, all_land_drops_so_far: 90 - i * 10, mana_available: i + 1, mana_spent: i, cards_in_hand: 6.5,
  discarded_by_now: i * 2, every_spell_in_hand_cost_too_much: 10 - i, ...f,
}));
const answer = (o = {}) => ({ games: 1000, turns: 6, on_the_play: true, multiplayer: true, seed: 42, format: 'commander', margin_points: 3.1,
  headline: { mulligan_rate: 14.1, five_mana_by_turn_5: 58.3, discarded_by_turn_5: 8.8, missed_a_land_drop_by_turn_4: 38 },
  per_turn: perTurn(6), samples: [], discard_may_be_the_plan: [], assumptions: ['a'], ...o });

test('a plan is "Card: reason", split at the last colon because some card names have one', () => {
  assert.deepEqual(J(S.splitPlan('Reliquary Tower: no maximum hand size')), { card: 'Reliquary Tower', why: 'no maximum hand size' });
  assert.deepEqual(J(S.splitPlan('Circle of Protection: Red: pays off discarding')), { card: 'Circle of Protection: Red', why: 'pays off discarding' });
  assert.deepEqual(J(S.splitPlan('No colon')), { card: 'No colon', why: '' });
});

test('the deck leads: name, format, cards, commander(s), colours', () => {
  const o = { format: 'commander', cards: 100, commanders: ['Xenagos, God of Revels'], color_identity: 'RG' };
  assert.equal(S.deckLine('My deck', o), 'My deck · commander · 100 cards · commander: Xenagos, God of Revels · colours R G');
  assert.equal(S.deckLine('Pair', { ...o, commanders: ['A', 'B'], color_identity: 'colorless' }), 'Pair · commander · 100 cards · commanders: A, B · colourless');
  assert.equal(S.deckLine('Pasted decklist', { format: null, cards: 1, commanders: [], color_identity: null }), 'Pasted decklist · 1 card');
  assert.equal(S.deckLine('Only a name', null), 'Only a name');
});

test('the intro says it is a simulation, and the margin is the server\'s, never a number of the page\'s own', () => {
  const i = S.intro(answer());
  assert.equal(i.lead, 'A simulation, not a prediction.');
  assert.equal(i.body, 'The Vault played this list 1,000 times with a simple player (no opponent) and counted what happened in the first 6 turns.');
  assert.match(i.margin, /at 1,000 games a percentage is good to about 3 points either way\.$/);
  assert.match(S.intro(answer({ games: 200, margin_points: 6.9 })).margin, /at 200 games .* about 7 points/);
  assert.match(S.intro(answer({ games: 5000, margin_points: 1.4 })).margin, /about 1\.4 points/);
  assert.equal(S.intro(answer({ margin_points: undefined })).margin, '', 'an answer without the field gets no margin sentence');
});

test('the "As played" line says who draws on turn 1, the seed, and that the same seed gives the same figures', () => {
  assert.equal(S.asPlayed(answer()), 'commander · multiplayer: everyone draws on turn 1 · on the play · 6 turns · 1,000 games · seed 42 (same deck, same seed, same figures)');
  assert.equal(S.asPlayed(answer({ multiplayer: false, format: 'modern' })), 'modern · two players: no draw on turn 1 on the play · on the play · 6 turns · 1,000 games · seed 42 (same deck, same seed, same figures)');
  assert.match(S.asPlayed(answer({ multiplayer: false, on_the_play: false })), /two players · on the draw/);
});

test('the four tiles carry the answer\'s own names; with fewer than 5 turns only the mulligan rate exists', () => {
  assert.deepEqual(J(S.tiles(answer())).map((t) => [t.label, t.value, t.unit]), [
    ['Five mana by turn 5', '58.3%', 'of simulated games'], ['Missed a land drop by turn 4', '38.0%', 'of simulated games'],
    ['Discarded by turn 5', '8.8%', 'of simulated games'], ['Mulligan rate', '14.1%', 'of simulated games']]);
  assert.deepEqual(J(S.tiles(answer({ headline: { mulligan_rate: 9 } }))).map((t) => t.label), ['Mulligan rate']);
});

test('each odds panel has a row per turn with the value written out, and the discard bars are neutral when discarding is the plan', () => {
  const r = answer({ per_turn: perTurn(4) });
  const panels = J(S.oddsPanels(r));
  assert.deepEqual(panels.map((p) => p.id), ['land', 'mana', 'hand', 'discard']);
  const land = panels[0].groups[0];
  assert.deepEqual(land.rows.map((x) => x.label), ['Turn 1', 'Turn 2', 'Turn 3', 'Turn 4']);
  assert.equal(land.rows[0].value, '90.0% of games');
  assert.equal(land.rows[0].width, 90);
  assert.equal(panels[1].groups[0].rows[2].value, '3.00 available');
  assert.equal(panels[1].groups[1].rows[2].value, '2.00 spent');
  assert.equal(panels[2].groups[0].rows[0].value, '6.50 cards');
  assert.equal(panels[3].groups[0].tone, 'danger');
  assert.equal(panels[3].plan, null);
  const planned = J(S.oddsPanels(answer({ discard_may_be_the_plan: ['Reliquary Tower: no maximum hand size', 'Wheel of Fortune: wheels hands'] })))[3];
  assert.equal(planned.groups[0].tone, 'neutral');
  assert.deepEqual(planned.plan.items, [{ card: 'Reliquary Tower', why: 'no maximum hand size' }, { card: 'Wheel of Fortune', why: 'wheels hands' }]);
  assert.match(planned.plan.note, /^The simulation lifts the hand limit once a “no maximum hand size” card is in play\./);
  const wheelOnly = J(S.oddsPanels(answer({ discard_may_be_the_plan: ['Wheel of Fortune: wheels hands'] })))[3];
  assert.doesNotMatch(wheelOnly.plan.note, /lifts the hand limit/, 'the hand-limit sentence only where a card gives no maximum hand size');
});

test('a bar is never wider than its track, and the mana scale grows with the numbers', () => {
  const big = J(S.oddsPanels(answer({ per_turn: perTurn(10, { mana_available: 12, mana_spent: 11 }) })))[1].groups[0].rows;
  assert.ok(big.every((x) => x.width <= 100) && big[0].width === 100);
  const small = J(S.oddsPanels(answer()))[1].groups[0].rows;
  assert.equal(small[5].width, 75, 'turn 6 at 6 mana is 6 of 8');
});

const game = (o = {}) => ({ mulligans: 0, opening_hand: ['Forest', 'Forest', 'Mountain', 'Elf', 'Bear', 'Ogre', 'Drake'], turns: [
  { turn: 1, drew: null, land: 'Forest', mana: 1, spent: 0, cast: [], hand: 6, discarded: [], nothing_affordable: true },
  { turn: 2, drew: 'Sol Ring', land: 'Forest', mana: 2, spent: 2, cast: ['Elf', 'Sol Ring'], hand: 5, discarded: [], nothing_affordable: false },
  { turn: 3, drew: 'Island', land: null, mana: 2, spent: 0, cast: [], hand: 8, discarded: ['Island', 'Titan'], nothing_affordable: false }], ...o });

test('a sample game: a heading with the mulligans and the land drops of the first three turns, then each turn\'s cells and flags', () => {
  assert.deepEqual(J(S.gameSummary(game(), 0)), { title: 'Game 1', facts: 'kept 7 · land drops made in turns 1 to 3: 2 of 3', hand: 'Forest, Forest, Mountain, Elf, Bear, Ogre, Drake' });
  assert.equal(S.gameSummary(game({ mulligans: 1, opening_hand: ['a', 'b', 'c', 'd', 'e', 'f'] }), 2).facts.startsWith('mulligan 1, kept 6'), true);
  assert.equal(S.gameSummary(game({ mulligans: 1, opening_hand: ['a', 'b', 'c', 'd', 'e', 'f'] }), 2).title, 'Game 3');
  const r = answer({ on_the_play: true, multiplayer: false });
  const [t1, t2, t3] = game().turns.map((t) => J(S.turnCells(t, r)));
  assert.equal(t1.drew, 'no draw (on the play)');
  assert.equal(t1.cast, 'nothing');
  assert.deepEqual(t1.notes, [{ tone: 'warn', text: 'Nothing castable: every spell in hand costs more than the mana' }]);
  assert.equal(t2.mana, '2 (spent 2)'); assert.equal(t2.cast, 'Elf, Sol Ring'); assert.deepEqual(t2.notes, []);
  assert.equal(t3.land, 'no land'); assert.deepEqual(t3.notes, [{ tone: 'bad', text: 'Discarded Island, Titan' }]);
  assert.equal(J(S.turnCells(game().turns[0], answer({ multiplayer: true }))).drew, 'no card drawn', 'in multiplayer everyone draws, so a missing card is not "on the play"');
});

test('the states: too few cards quotes the server, asked too often counts down from Retry-After, anything else says what the server said', () => {
  const few = S.problem({ status: 400, message: 'The deck has 9 cards: too few to play 6 turns.' }, 6);
  assert.deepEqual(J(few), { kind: 'toofew', title: 'Not enough cards to play 6 turns.', quote: 'The deck has 9 cards: too few to play 6 turns.',
    advice: 'Add cards, or choose fewer turns (a deck needs 7 cards plus one for each turn).' });
  const limit = S.problem({ status: 429, message: 'Too many requests', retryAfter: 41 }, 6);
  assert.equal(limit.kind, 'limit'); assert.equal(limit.seconds, 41); assert.equal(limit.title, 'Too many analyses this minute.');
  assert.equal(limit.detail, 'The Vault allows 30 a minute for each person.');
  assert.equal(S.problem({ status: 429, message: 'x' }, 6).seconds, 60, 'without Retry-After, a minute');
  assert.equal(S.waitText(41), 'You can play again in 41 seconds.'); assert.equal(S.waitText(1), 'You can play again in 1 second.'); assert.equal(S.waitText(0), 'You can play again now.');
  const other = S.problem({ status: 0, message: 'Network error: Failed to fetch' }, 6);
  assert.deepEqual(J(other), { kind: 'other', title: 'Couldn’t work out the opening turns:', detail: 'Network error: Failed to fetch' });
  assert.equal(S.problem({ status: 400, message: 'Unknown format' }, 6).kind, 'other', 'another 400 is not a too-few-cards answer');
  assert.equal(S.problem({ status: 500, message: '' }, 6).detail, 'Something went wrong.');
});

test('the button says what it does: play again, playing again, or how long to wait', () => {
  assert.deepEqual(J(S.playAgain({ busy: false, wait: 0 })), { label: 'Play again with a new seed', disabled: false });
  assert.deepEqual(J(S.playAgain({ busy: true, wait: 0 })), { label: 'Playing again…', disabled: true });
  assert.deepEqual(J(S.playAgain({ busy: false, wait: 41 })), { label: 'Play again in 41 s', disabled: true });
});

test('a new seed is a whole number the endpoint accepts, so it can be sent and the same answer asked for again', () => {
  assert.equal(S.newSeed(() => 0), 0);
  assert.ok(S.newSeed(() => 0.9999999999) <= 2 ** 31 - 1 && Number.isInteger(S.newSeed(() => 0.5)));
  assert.equal(S.MAX_SEED, 2147483647);
});

test('the curve: bars by the tallest, a text alternative that lists every value, the commander sentence only with one', () => {
  const stats = { lands: 37, nonland: 62, average_mana_value_nonland: 2.9, by_section: { commander: 1, deck: 99 },
    curve: { 0: 1, 1: 11, 2: 18, 3: 13, 4: 8, 5: 6, 6: 3, '7+': 2 } };
  const c = J(S.curve(stats));
  assert.equal(c.bars[2].height, 100); assert.equal(c.bars[0].n, 1);
  assert.equal(c.label, 'Mana curve, number of non-land cards by mana value. 0: 1, 1: 11, 2: 18, 3: 13, 4: 8, 5: 6, 6: 3, 7+: 2.');
  assert.equal(c.caption, '37 lands, 62 other cards, average mana value of the non-land cards 2.9. The commander is in the command zone.');
  assert.equal(J(S.curve({ ...stats, by_section: { deck: 60 } })).caption.includes('commander'), false);
  assert.equal(J(S.curve({ ...stats, lands: 1, nonland: 1 })).caption.startsWith('1 land, 1 other card,'), true);
});

test('provenance: computed by the Vault, from which sources and when, with the notice the sources require', () => {
  const p = J(S.provenance({ kind: 'computed', source: 'The Vault', notice: 'Fan Content notice', inputs: [
    { kind: 'source', source: 'Scryfall', origin: 'Wizards of the Coast (card text)', url: 'https://scryfall.com/docs/api/bulk-data', as_of: '2026-10-08', notice: 'Fan Content notice' }] }));
  assert.equal(p.lead, 'Computed by the Vault from');
  assert.equal(S.sourceText(p.sources[0]), 'Scryfall: Wizards of the Coast (card text), as of 2026-10-08');
  assert.equal(p.sources[0].url, 'https://scryfall.com/docs/api/bulk-data');
  assert.match(p.reading, /the Vault’s reading of its text, not Scryfall’s or Wizards’ figure/);
  assert.equal(p.notice, 'Fan Content notice');
});

test('the client sends the page\'s list, the settings and 1,000 games; a seed only when there is one; and a refusal is not retried', async () => {
  const calls = [];
  const reply = (status, body, headers = {}) => ({ ok: status < 300, status, headers: { get: (k) => headers[k.toLowerCase()] ?? (k.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => body, text: async () => JSON.stringify(body) });
  const sb = {
    fetch: async (url, init = {}) => {
      if (url === '/api/v1' && !init.method) return reply(200, { _links: {} });
      calls.push({ url, method: init.method, body: JSON.parse(init.body) });
      return calls.length === 3 ? reply(429, { detail: 'Too many' }, { 'retry-after': '41' }) : reply(200, { result: {} });
    },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {}, clear() {}, key: () => null, length: 0 },
    location: { href: 'https://vault.test/' }, document: { cookie: '' }, crypto: { randomUUID: () => 'k' }, Event: class {}, dispatchEvent: () => true,
    URL, URLSearchParams, Headers: Map, clearTimeout, Promise, Date, Math, JSON, console, setTimeout: (fn) => setTimeout(fn, 1),
  };
  sb.window = sb;
  vm.runInNewContext(readFileSync(new URL('../../public/lib/api.js', import.meta.url), 'utf8'), sb);
  const settings = { format: 'commander', on_the_play: false, turns: 8, games: S.GAMES, samples: S.SAMPLES };
  await sb.window.VaultApi.deckSimulate('1 Sol Ring', settings);
  await sb.window.VaultApi.deckSimulate('1 Sol Ring', { ...settings, seed: 7 });
  assert.deepEqual(J(calls[0]), { url: '/api/v1/decks/simulate', method: 'POST', body: { text: '1 Sol Ring', format: 'commander', on_the_play: false, turns: 8, games: 1000, samples: 5 } });
  assert.equal(calls[1].body.seed, 7);
  await assert.rejects(sb.window.VaultApi.deckSimulate('1 Sol Ring', settings), (e) => e.status === 429 && e.retryAfter === 41);
  assert.equal(calls.length, 3, 'one request: the 429 reaches the page, which shows its own countdown');
});
