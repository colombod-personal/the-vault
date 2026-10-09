// Runs the Opening turns tab's wording (public/lib/deck_sim.js) on real answers and prints what the page would say.
// Not a test by itself (no .test.mjs name): tests/test_deck_sim_page.py runs it.
//
//   stdin  { simulate: <the answer of POST /decks/simulate>, stats: <the `result` of POST /decks/stats>, errors: [{ status, message, retryAfter }], turns }
//   stdout { deckLine, intro, asPlayed, tiles, panels, games, curve, provenance, problems }
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(readFileSync(0, 'utf8'));
const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/deck_sim.js', import.meta.url), 'utf8'), sandbox);
const S = sandbox.window.VaultDeckSim;
const J = (o) => JSON.parse(JSON.stringify(o));
const { simulate: a, stats, errors, turns } = input;
const r = a.result;

console.log(JSON.stringify(J({
  deckLine: S.deckLine(a.deck.name || 'Pasted decklist', a.deck.overview),
  intro: S.intro(r), asPlayed: S.asPlayed(r), tiles: S.tiles(r), panels: S.oddsPanels(r),
  samplesIntro: S.samplesIntro(r),
  games: r.samples.map((g, i) => ({ summary: S.gameSummary(g, i), turns: g.turns.map((t) => S.turnCells(t, r)) })),
  curve: S.curve(stats), provenance: S.provenance(a.provenance[0]),
  unmatched: S.unmatchedLine(r.unmatched),
  problems: errors.map((e) => S.problem(e, turns)),
})));
