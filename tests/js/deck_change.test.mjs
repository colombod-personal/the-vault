// public/lib/deck_change.js: what the deck page's "Change this deck" flow says (docs/deck-ideas-lab-design.md, task 0 under #163).
// Run: node --test tests/js/*.test.mjs   (tests/test_web_lib.py runs it with pytest; tests/test_deck_change_page.py feeds it the server's real answers)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/deck_change.js', import.meta.url), 'utf8'), sandbox);
const V = sandbox.window.VaultDeckChange;
const J = (o) => JSON.parse(JSON.stringify(o));  // objects made in the sandbox have its prototypes

test('a swap in the address fills the flow in; anything that is not a list of card names is refused and nothing is filled in', () => {
  assert.equal(V.parseSwap(null), null);
  assert.equal(V.parseSwap(''), null);
  // the route carries the swap already read when it came from the Ideas view: an object is read like the JSON text
  assert.deepEqual(J(V.parseSwap({ cut: ['Cloudstone Curio'], add: [' Anointed Procession '] })), { cut: ['Cloudstone Curio'], add: ['Anointed Procession'] });
  assert.deepEqual(J(V.parseSwap('{"cut":["Cloudstone Curio"],"add":[" Anointed Procession "]}')), { cut: ['Cloudstone Curio'], add: ['Anointed Procession'] });
  assert.deepEqual(J(V.parseSwap('{"add":["Sol Ring"]}')), { cut: [], add: ['Sol Ring'] });
  for (const raw of ['not json', '[]', '"x"', '{}', '{"cut":[],"add":[]}', '{"cut":"Sol Ring"}', '{"cut":[1]}', '{"cut":[""]}', '{"add":["' + 'x'.repeat(301) + '"]}',
    '{"add":' + JSON.stringify(Array(61).fill('Sol Ring')) + '}']) {
    const got = V.parseSwap(raw);
    assert.ok(got && got.error && !got.cut, raw.slice(0, 40));
  }
  assert.ok(!V.parseSwap('{"add":' + JSON.stringify(Array(60).fill('Sol Ring')) + '}').error, 'the server takes 60');
});

test('the confirmation names what will change, in the words of the design', () => {
  assert.equal(V.summaryLine('Sliver Swarm', ['a', 'b'], ['c', 'd']), 'Cut 2 cards, add 2 cards to Sliver Swarm');
  assert.equal(V.summaryLine('Sliver Swarm', ['a'], ['c']), 'Cut 1 card, add 1 card to Sliver Swarm');
  assert.equal(V.summaryLine('Sliver Swarm', ['a', 'b', 'c'], []), 'Cut 3 cards from Sliver Swarm');
  assert.equal(V.summaryLine('Sliver Swarm', [], ['c']), 'Add 1 card to Sliver Swarm');
  const clean = { issues: [] }, flawed = { issues: [{ kind: 'not_legal' }] };
  assert.equal(V.confirmLabel('Sliver Swarm', ['a', 'b'], ['c', 'd'], clean), 'Cut 2 cards, add 2 cards to Sliver Swarm');
  assert.equal(V.confirmLabel('Sliver Swarm', ['a'], [], flawed), 'Save anyway: Cut 1 card from Sliver Swarm');
  assert.equal(V.savedLine('Sliver Swarm', ['a'], ['b']), 'Saved. Cut 1 card, add 1 card to Sliver Swarm. The earlier list is in the History tab.');
  assert.match(V.fromArchidekt, /Vault’s copy.*Archidekt does not/);
});

test('copies of one card are one chip, and the check is only for the cards as chosen', () => {
  assert.deepEqual(J(V.copies(['Forest', 'Sol Ring', 'forest'])), [{ name: 'Forest', n: 2 }, { name: 'Sol Ring', n: 1 }]);
  assert.equal(V.proposalKey('commander', ['Forest'], ['Sol Ring']), V.proposalKey('commander', ['forest'], ['SOL RING']));
  assert.notEqual(V.proposalKey('commander', ['Forest'], []), V.proposalKey('modern', ['Forest'], []));
  assert.notEqual(V.proposalKey('commander', ['Forest'], []), V.proposalKey('commander', ['Forest', 'Forest'], []));
  assert.equal(V.nameKey('Fire // Ice'), 'fire');
});

test('a problem is named in plain words with the server’s own detail; a plan that cannot be saved says why', () => {
  assert.deepEqual(J(V.issueLine({ kind: 'color_identity', card: 'Counterspell', detail: 'outside the deck\'s color identity' })),
    { label: 'Outside the deck’s colour identity', card: 'Counterspell', detail: 'outside the deck\'s color identity' });
  assert.equal(V.issueLabel('result_deck_size'), 'Deck size (after the change)');
  assert.equal(V.issueLabel('result_too_many_copies'), 'Too many copies (after the change)');
  assert.equal(V.issueLabel('something_new'), 'Something new', 'a kind the page does not know is still shown');
  assert.deepEqual(J(V.verdict({ issues: [] })), { good: true, text: 'The check found no problems.' });
  assert.equal(V.verdict({ issues: [{}, {}] }).text, 'The check found 2 problems.');
  assert.equal(V.verdict({ issues: [{}] }).text, 'The check found 1 problem.');
  assert.equal(V.blocked({ issues: [] }, [], []), 'Choose a card to cut or add first.');
  assert.equal(V.blocked({ issues: [] }, ['a'], []), null);
  assert.equal(V.blocked({ issues: [{ kind: 'not_legal', card: 'x' }] }, ['a'], ['x']), null, 'a problem is shown and may be saved over');
  assert.match(V.blocked({ issues: [{ kind: 'cut_not_in_deck', card: 'Ghost' }] }, ['Ghost'], []), /^Not in the deck: Ghost\./);
  assert.match(V.blocked({ issues: [{ kind: 'unknown_card', card: 'Ghost' }] }, [], ['Ghost']), /^Not in the card catalog: Ghost\./);
  assert.equal(V.existingLine({ existing_issues: [] }), null);
  assert.equal(V.existingLine({ existing_issues: [{}] }), '1 problem the deck already had is not counted against this change.');
  assert.equal(V.existingLine({ existing_issues: [{}, {}] }), '2 problems the deck already had are not counted against this change.');
});

test('what it does to the list, the price and what the person owns is the server’s numbers in words', () => {
  const r = { cards_after: 100, cuts: 2, adds: 2, added_cost_usd: 8.1, cut_value_usd: 3.3, cut_unpriced: ['Test Mountain'], deck_cost_before_usd: 216.03, deck_cost_after_usd: 220.83, net_change_usd: 4.8 };
  assert.equal(V.countsLine(r), 'The list would have 100 cards (cut 2, add 2).');
  assert.deepEqual(J(V.costLines(r)), ['The cards to add cost $8.10.', 'The cards cut are worth $3.30 (no price known for Test Mountain); a cut is not refunded.',
    'The deck’s price goes from $216.03 to $220.83 (+$4.80).']);
  assert.deepEqual(J(V.costLines({ ...r, adds: 0, cuts: 0, net_change_usd: -1.5, deck_cost_after_usd: 214.53 })), ['The deck’s price goes from $216.03 to $214.53 (−$1.50).']);
  const cov = V.coverageByName({ cards: [{ name: 'Sol Ring', have: 3, need: 1 }, { name: 'Forest', have: 1, need: 3 }, { name: 'Fire // Ice', have: 0, need: 1, unit_price: 0.5 }] });
  assert.equal(V.ownershipLine('Sol Ring', cov[V.nameKey('Sol Ring')]), 'Sol Ring: you own it');
  assert.equal(V.ownershipLine('Forest', cov[V.nameKey('Forest')]), 'Forest: you own 1 of 3');
  assert.equal(V.ownershipLine('Fire', cov[V.nameKey('Fire')]), 'Fire: you do not own it ($0.50 to buy)');
  assert.equal(V.ownershipLine('Ghost', undefined), 'Ghost: not checked');
  assert.equal(V.toBuyLine({ missing_cost: 0, missing_unpriced: 0 }), 'After the change you would own every card in this deck.');
  assert.equal(V.toBuyLine({ missing_cost: 3.3, missing_unpriced: 0 }), 'To finish the deck after the change: $3.30.');
  assert.equal(V.toBuyLine({ missing_cost: 3.3, missing_unpriced: 2 }), 'To finish the deck after the change: ≥ $3.30 (2 cards without a price).');
  assert.equal(V.toBuyLine(null), null);
});
