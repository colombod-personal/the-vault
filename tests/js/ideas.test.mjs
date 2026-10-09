// public/lib/ideas.js: the Ideas page's wording, addresses and windowing (docs/deck-ideas-lab-design.md).
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/ideas.js', import.meta.url), 'utf8'), sandbox);
const I = sandbox.window.VaultIdeas;
const J = (o) => JSON.parse(JSON.stringify(o));  // objects made in the sandbox have its prototypes

const card = (over) => ({ card: 'Cloudstone Curio', need: 1, have: 0, gets: 0, not_owned: 1, held_by_other_deck: 0, status: 'missing', borrowed: false,
  borrowed_from: null, also_wanted_by: [], ...over });

test('the header words the server counts and never adds them', () => {
  const s = { copies: 100, covered: 92, lacking: 8, cards: 96, missing: 8, borrowed: 3, partial: 0, complete: false };
  assert.deepEqual(J(I.headline(s)), { covered: '92 of 100 covered', missing: '8 missing', borrowed: '3 borrowed' });
  assert.equal(I.isCovered(s), false);
  assert.equal(I.isCovered({ ...s, covered: 100, lacking: 0, missing: 0, borrowed: 0, complete: true }), true);
  // complete but a copy is borrowed: not "fully covered" (the design: every card is owned and no copy is borrowed)
  assert.equal(I.isCovered({ ...s, covered: 100, lacking: 0, missing: 0, borrowed: 1, complete: true }), false);
});

test('the page is in one of the five states of the design', () => {
  const missing = { complete: false, missing: 8, borrowed: 3 }, covered = { complete: true, missing: 0, borrowed: 0 };
  assert.equal(I.pageState({}), 'start');
  assert.equal(I.pageState({ deckId: '1', summary: missing }), 'deck');
  assert.equal(I.pageState({ deckId: '1', summary: covered }), 'covered');
  assert.equal(I.pageState({ deckId: '1', summary: missing, card: 'X', target: { reason: null } }), 'alternatives');
  assert.equal(I.pageState({ deckId: '1', summary: missing, card: 'X', target: { reason: 'none_found' } }), 'no-alternative');
  assert.equal(I.pageState({ deckId: '1', summary: missing, card: 'X', target: { reason: 'no_role' } }), 'no-role');  // the Vault does not know the card: not "nothing found"
});

test('a row says missing, borrowed, partly held or owned, with the numbers the server gave', () => {
  assert.deepEqual(J(I.rowStatus(card())), { tone: 'missing', word: 'missing', note: '1 to buy' });
  assert.deepEqual(J(I.rowStatus(card({ not_owned: 0, held_by_other_deck: 1, borrowed: true, borrowed_from: 'Avatar Aang' }))),
    { tone: 'borrowed', word: 'borrowed', note: 'held by Avatar Aang' });
  // the mixed shortage: one copy to buy and one held by another deck, both said
  assert.deepEqual(J(I.rowStatus(card({ need: 2, have: 1, gets: 0, not_owned: 1, held_by_other_deck: 1, borrowed_from: 'First' }))),
    { tone: 'missing', word: 'missing', note: '1 to buy, 1 held by First' });
  assert.deepEqual(J(I.rowStatus(card({ need: 2, have: 1, gets: 1, status: 'partial', not_owned: 1 }))), { tone: 'partial', word: '1 of 2', note: '1 to buy' });
  assert.deepEqual(J(I.rowStatus(card({ status: 'owned', gets: 1, not_owned: 0, also_wanted_by: ['Big Deck'] }))), { tone: 'owned', word: 'owned', note: 'also wanted by Big Deck' });
  assert.match(I.rowLabel(card(), 'Ramp'), /^Cloudstone Curio, missing, 1 to buy, Ramp lane$/);
});

test('what needs a decision keeps the lanes order, missing before partly held; borrowed uses the server flag', () => {
  const lanes = [
    { label: 'Ramp', items: [card({ card: 'A', status: 'partial', gets: 1, need: 2 }), card({ card: 'B', status: 'owned', borrowed: true }), card({ card: 'C' })] },
    { label: 'Draw', items: [card({ card: 'D' }), card({ card: 'E', status: 'owned' })] },
  ];
  assert.deepEqual(I.decisionCards(lanes).map((c) => c.card), ['C', 'D', 'A']);
  assert.deepEqual(I.borrowedCards(lanes).map((c) => c.card), ['B']);
  assert.equal(I.defaultFilter({ missing: 2, borrowed: 1 }), 'missing');
  assert.equal(I.defaultFilter({ missing: 0, borrowed: 1 }), 'borrowed');
  assert.equal(I.defaultFilter({ missing: 0, borrowed: 0 }), 'all');
});

test('a lane is titled with the server count and its note', () => {
  const lane = { label: 'Ramp', cards: 10, missing: 1, borrowed: 2, total: 40 };
  assert.equal(I.laneTitle(lane), 'Ramp (10)');
  assert.equal(I.laneNote(lane), '1 missing · 2 borrowed');
  assert.equal(I.laneNote({ missing: 0, borrowed: 0 }), '');
  assert.equal(I.moreLabel({ label: 'Other', total: 50 }, 25), 'Show 25 more of other (25 left)');
});

test('long lanes are windowed above 40 cards and only the rows in view are drawn', () => {
  assert.equal(I.needsWindow(40), false);
  assert.equal(I.needsWindow(41), true);
  assert.deepEqual(J(I.windowRange(0, 36, 432, 200)), { start: 0, end: 16 });
  assert.deepEqual(J(I.windowRange(3600, 36, 432, 200)), { start: 96, end: 116 });
  assert.equal(I.windowRange(6768, 36, 432, 200).end, 200);  // the end never passes the last row
});

test('only thumbnails are asked for, and the first render stays under the weight budget', () => {
  assert.equal(I.thumbUrl('https://cards.scryfall.io/normal/front/a/b/abc.jpg?1'), 'https://cards.scryfall.io/small/front/a/b/abc.jpg?1');
  assert.equal(I.thumbUrl(null), null);
  assert.equal(I.MAX_IMAGES, 12);
  assert.ok(I.ALT_PAGE + 1 <= I.MAX_IMAGES, 'the target and one page of alternatives fit in the image budget');
  assert.equal(I.WINDOW_AT, 40);
});

test('the addresses: #/ideas, #/ideas/{deck}, #/ideas/{deck}/{card}', () => {
  assert.deepEqual(J(I.ideasRoute()), { view: 'ideas' });
  assert.deepEqual(J(I.ideasRoute('12', 'Fire // Ice')), { view: 'ideas', deckId: '12', card: 'Fire // Ice' });
  assert.deepEqual(J(I.ideasRoute('not-a-number', 'X')), { view: 'ideas' });  // an address that is not a deck id is ignored
  assert.deepEqual(J(I.ideasRoute(undefined, 'X')), { view: 'ideas' });       // a card needs its deck
  assert.equal(I.ideasHash({ view: 'ideas' }), '#/ideas');
  assert.equal(I.ideasHash({ view: 'ideas', deckId: '12' }), '#/ideas/12');
  assert.equal(I.ideasHash(I.ideasRoute('12', 'Fire // Ice')), '#/ideas/12/Fire%20%2F%2F%20Ice');
});

test('the contract with the deck page: #/decks/{id}?swap={"cut":[...],"add":[...]}', () => {
  const hash = I.swapHash('7', ['Cloudstone Curio'], ['Mind Stone']);
  assert.equal(hash, '#/decks/7?swap=' + encodeURIComponent('{"cut":["Cloudstone Curio"],"add":["Mind Stone"]}'));
  const search = hash.split('?')[1];
  assert.deepEqual(J(I.parseSwap(new URLSearchParams(search).get('swap'))), { cut: ['Cloudstone Curio'], add: ['Mind Stone'] });
  // a cut or an add alone is a proposal too (Move); names are trimmed
  assert.deepEqual(J(I.parseSwap('{"cut":[],"add":[" A "]}')), { cut: [], add: ['A'] });
  // anything that is not that shape is ignored, never half-read
  for (const bad of [null, '', 'nope', '[]', '{}', '{"cut":[],"add":[]}', '{"cut":"A","add":["B"]}', '{"cut":[1],"add":[]}', '{"cut":[""],"add":[]}',
    JSON.stringify({ cut: ['x'.repeat(301)], add: [] }), JSON.stringify({ cut: Array(21).fill('A'), add: [] })]) {
    assert.equal(I.parseSwap(bad), null, String(bad).slice(0, 30));
  }
});

test('alternatives say how many are owned, why they match, who holds them, and what a copy costs', () => {
  const alt = { card: 'Mind Stone', copies_owned: 2, copies_free: 2, borrowed: false, borrowed_from: null, in_deck: 0 };
  assert.equal(I.ownedText(alt), '2 owned');
  assert.equal(I.ownedText({ ...alt, copies_owned: 3, copies_free: 1 }), '3 owned, 1 free');
  const held = { ...alt, copies_owned: 1, copies_free: 0, borrowed: true, borrowed_from: 'Rock Deck' };
  assert.equal(I.ownedText(held), '1 owned');
  assert.deepEqual(J(I.altBadges(held)), [{ kind: 'borrowed', text: 'borrowed from Rock Deck' }]);
  assert.deepEqual(J(I.altBadges({ ...alt, in_deck: 1 })), [{ kind: 'in-deck', text: '1 already in the deck' }]);
  const buy = { quantity: 1, unit_price: 6.6, cost: 6.6, price_date: '2026-10-06', price_status: 'priced' };
  assert.equal(I.buyLabel(buy), 'Buy $6.60 (Scryfall, 6 Oct)');
  assert.equal(I.buyLabel({ ...buy, quantity: 2, cost: 13.2 }), 'Buy 2 for $13.20 (Scryfall, 6 Oct)');
  assert.equal(I.buyLabel({ quantity: 1, unit_price: null, cost: null, price_date: null, price_status: 'unpriced' }), 'Buy (no price known)');
  assert.equal(I.buyPlain(buy), '$6.60 (Scryfall, 6 Oct)');
  assert.equal(I.moveText({ from_deck: { id: 2, name: 'Avatar Aang' }, quantity: 1 }), 'Move from Avatar Aang');
  assert.match(I.moveSteps({ from_deck: { id: 2, name: 'Avatar Aang' }, quantity: 1 }, 'Doubling Season', 'Sliver Swarm'), /Move a copy of Doubling Season from Avatar Aang to Sliver Swarm\. The Vault changes no deck by itself/);
  assert.match(I.PRICE_NOTE('2026-10-06'), /Scryfall's cheapest known, from 6 Oct; the Vault contacts no shop\./);
  assert.match(I.ROLES_NOTE, /Vault's own, found by rules over the Oracle text/);
});

test("roles are the Vault's own, said with their strength; a card with none says so, and that is not 'nothing like it'", () => {
  const roles = [{ role: 'token-doubler', name: 'Token doubler', strength: 'core', basis: 'computed' }, { role: 'counter-doubler', name: 'Counter doubler', strength: 'core', basis: 'computed' },
    { role: 'lifegain', name: 'Lifegain', strength: 'incidental', basis: 'computed' }];
  assert.equal(I.roleLine(roles), 'Does: token doubler (core), counter doubler (core); on the side: lifegain');
  assert.equal(I.roleLine([roles[2]]), 'Does: nothing as its main job; on the side: lifegain');
  assert.equal(I.roleLine([]), 'The Vault knows no role for this card yet');
  assert.match(I.NO_ROLE_NOTE, /not the same as "you own nothing like it"/);
  assert.deepEqual(I.roleWords(roles.slice(0, 1)), ['token doubler (core)']);
  const t = { in_deck: 1, status: 'missing', need: 2, gets: 0, not_owned: 1, held_by_other_deck: 1, borrowed_from: 'First' };
  assert.equal(I.allocationLine(t), 'The deck lists 2; this deck holds 0; 1 to buy; 1 held by First.');
  assert.equal(I.allocationLine({ in_deck: 0, status: null }), 'This card is not in the deck.');
});

test("a candidate says what it does, what it does not do, what it adds and how its type differs, from the server's lists", () => {
  const alt = { tier: 'same_job', roles: [{ name: 'Token doubler', strength: 'core' }, { name: 'Lifegain', strength: 'incidental' }],
    lacks: [{ role: 'counter-doubler', name: 'Counter doubler' }], extra: [{ role: 'free-counterspell', name: 'Free counterspell' }],
    type_note: { target: 'Enchantment', candidate: 'Creature' } };
  assert.equal(I.altDoes(alt), 'Does: token doubler');
  assert.equal(I.altLacks(alt, 'Doubling Season'), 'Does not: counter doubler (Doubling Season does)');
  assert.equal(I.altExtra(alt), 'Also: free counterspell');
  assert.equal(I.typeNote(alt, 'Doubling Season'), 'a creature, where Doubling Season is an enchantment.');
  assert.equal(I.typeNote({ type_note: { target: 'Sorcery', candidate: 'Instant' } }, 'X'), 'an instant, where X is a sorcery.');
  assert.equal(I.altLacks({ lacks: [] }, 'X') + I.altExtra({ extra: [] }) + I.typeNote({ type_note: null }, 'X') + I.altDoes({ roles: [] }), '');
  assert.equal(I.tierHeading('same_job', 1), 'Same job (1)');
  assert.equal(I.tierHeading('similar', 1234), 'Similar, with a difference (1,234)');
  assert.equal(I.similarToggle(3, false), 'Show similar, with a difference (3)');
  assert.equal(I.similarToggle(3, true), 'Hide similar, with a difference (3)');
  assert.deepEqual(I.byTier([{ tier: 'same_job', card: 'A' }, { tier: 'similar', card: 'B' }], 'similar').map((x) => x.card), ['B']);
  assert.match(I.TEXT_CREDIT, /Wizards of the Coast's, via Scryfall\. Unofficial Fan Content/);
});

test('combos mark only the cards of combos the person holds in full', () => {
  const combos = { checked: true, combos: [{ cards: ['A', 'B'], owned: true, url: 'u', produces: ['Infinite mana'] }, { cards: ['C', 'D'], owned: false }] };
  assert.deepEqual([...I.comboKeys(combos)].sort(), ['a', 'b']);
  assert.equal(I.comboLine(combos.combos[0]), 'A + B: Infinite mana');
  assert.equal(I.ownedCombos({ checked: false, reason: 'down' }).length, 0);
  assert.equal(I.ownedCombos(null).length, 0);
});

test('the format choices are the ones the deck page offers', () => {
  assert.equal(I.FORMATS[0], 'commander');
  assert.equal(new Set(I.FORMATS).size, I.FORMATS.length);
});

test('errors read like the Lab: unreachable, rate limited, or the server message', () => {
  assert.match(I.errorText({ status: 0 }), /couldn't be reached/);
  assert.match(I.errorText({ status: 429, message: 'x' }), /lot of requests/);
  assert.equal(I.errorText({ status: 404, message: 'No such deck' }), 'No such deck');
});
