// Runs the Lab page's own wording (public/lib/lab.js) on the server's answers and prints what the page would say.
// Not a test by itself (it has no .test.mjs name): tests/test_lab_page.py feeds it real answers of the Lab's routes and compares
// every counter with the field it reads (docs/lab-design.md, "Every counter equals the matching field in its section's response").
//
//   stdin  { overlap, spare, pnl, history, stale? }   the answers of GET /decks/overlap, /collection/spare, /collection/pnl, /collection/history
//   stdout { buy, sell, pnl, headline, spareValue, coverage, hidden, action, history }
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(readFileSync(0, 'utf8'));
const sandbox = {};
sandbox.window = sandbox;
vm.runInNewContext(readFileSync(new URL('../../public/lib/lab.js', import.meta.url), 'utf8'), sandbox);
const L = sandbox.window.VaultLab;
const dated = input.stale ? '3 Oct' : null;

console.log(JSON.stringify({
  buy: L.buyCounter(input.overlap, dated),
  sell: L.sellCounter(input.spare, dated),
  pnl: L.pnlCounter(input.pnl, dated),
  headline: L.buyHeadline(input.overlap),
  spareValue: L.spareValueLine(input.spare.summary, dated),
  coverage: L.coverageLine(input.pnl.summary),
  hidden: L.pnlHidden(input.pnl.summary),
  action: L.chartAction(input.pnl, input.spare.status),
  history: L.historyLine(input.history.summary),
}));
