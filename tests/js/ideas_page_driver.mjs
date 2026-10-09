// Runs the Ideas page's own wording (public/lib/ideas.js) on the server's answers and prints what the page would say.
// Not a test by itself (it has no .test.mjs name): tests/test_ideas_page.py feeds it real answers of the two routes and compares every
// number it prints with the field it reads (docs/deck-ideas-lab-design.md: "every number comes from the server").
//
//   stdin  { ideas, alternatives }   the answers of GET /decks/{id}/ideas and GET /decks/{id}/ideas/alternatives?card=...
//   stdout { headline, state, covered, lanes: [{ title, note, rows: [{ card, tone, word, note, label }] }], decisions, borrowed, alternatives: [...], target, buy }
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(readFileSync(0, 'utf8'));
const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/ideas.js', import.meta.url), 'utf8'), sandbox);
const I = sandbox.window.VaultIdeas;
const J = (o) => JSON.parse(JSON.stringify(o));

const ideas = input.ideas, alt = input.alternatives;
const lanes = ideas.lanes.filter((l) => l.total > 0);
console.log(JSON.stringify(J({
  headline: I.headline(ideas.summary),
  covered: I.isCovered(ideas.summary),
  state: I.pageState({ deckId: String(ideas.deck.id), summary: ideas.summary }),
  facts: I.deckFacts(ideas.deck.overview),
  lanes: lanes.map((l) => ({ title: I.laneTitle(l), note: I.laneNote(l), rows: l.items.map((c) => ({ card: c.card, ...I.rowStatus(c), label: I.rowLabel(c, l.label) })) })),
  decisions: I.decisionCards(lanes).map((c) => c.card),
  borrowed: I.borrowedCards(lanes).map((c) => c.card),
  alternatives: alt ? alt.items.map((a) => ({ card: a.card, owned: I.ownedText(a), badges: I.altBadges(a), buy: a.buy ? I.buyLabel(a.buy) : null,
    move: a.move ? I.moveText(a.move) : null, swap: I.swapHash(ideas.deck.id, [alt.card.card], [a.card]), thumb: a.image ? I.thumbUrl(a.image.normal) : null,
    tier: a.tier, does: I.altDoes(a), lacks: I.altLacks(a, alt.card.card), extra: I.altExtra(a), type: I.typeNote(a, alt.card.card), why: a.why })) : null,
  tiers: alt ? { same: I.tierHeading('same_job', alt.tiers.same_job), similar: I.tierHeading('similar', alt.tiers.similar), toggle: I.similarToggle(alt.tiers.similar, false),
    sameCards: I.byTier(alt.items, 'same_job').map((a) => a.card), similarCards: I.byTier(alt.items, 'similar').map((a) => a.card) } : null,
  target: alt ? { status: I.targetStatus(alt.card), role: I.roleLine(alt.card.roles), allocation: I.allocationLine(alt.card), buy: I.buyLabel(alt.card.buy),
    heading: I.heading(alt.card), state: I.pageState({ deckId: String(ideas.deck.id), summary: ideas.summary, card: alt.card.card, target: alt }) } : null,
})));
