# An optional "possible loops" note, drawn from card text (design for the third criterion of #172)

Status: **proposed, waiting for the owner's agreement** (criterion 3 of #172: "A design for an optional 'possible loops' note drawn
from card text, written and agreed"). This page is the design only; no product code changes in the pull request that adds it.
Criteria 1 and 2 of #172 (the answer says it lists only Spellbook's combos; the council skill and agents never say "no infinite
combos" from `find_combos` alone) are merged and verified (#310).

## 1. Why, and the real example

On a real council run `find_combos` returned 0 combos for a deck whose synergy analyst and devil's advocate agreed that its token engine
(Sliver Queen, mana Slivers, haste) can loop; the casual table relied on the 0 to say "no infinite combos". #310 stopped the false "no".
This design is about the other half of the issue: give the assistants, when asked, a **reading of the card text** that points at
possible loops Commander Spellbook does not list, **without ever claiming a loop that does not exist**.

The example, with the Oracle text read from the Vault's own `get_card_oracle` on 2026-10-09 (Scryfall's Oracle data, Wizards' text):

| Card | Text (as the catalog has it) |
|---|---|
| Sliver Queen | "{2}: Create a 1/1 colorless Sliver creature token." |
| Manaweft Sliver | Sliver creatures you control have "{T}: Add one mana of any color." |
| Gemhide Sliver | All Slivers have "{T}: Add one mana of any color." |
| Heart Sliver | All Sliver creatures have haste. |

Worked by the text alone: pay {2}, get a Sliver token. With a mana Sliver in play every Sliver, tokens included, can tap for one mana, and
with Heart Sliver the new token can tap at once (without haste a token cannot use a {T} ability the turn it arrives). That is **{2} in,
{1} back: a net cost of {1} per token.** (A Sliver taps once, so two mana-granting cards do not make it return two.) Starting with M
mana, the three cards can make at most M more Slivers and then stop. That is a strong **engine** (it doubles a board of mana Slivers) and **not a loop**: it does not
make unbounded tokens or mana by itself.

It becomes a loop only if something else in the deck closes the one-mana gap: an effect that makes a tapped creature produce one
more mana, or that lowers the {2} to {1} (then each token pays 1 and returns 1: net zero, repeatable). **What the issue does not say is
which of those the real deck held.** So the example is the best test of this design in both directions: an AI that says "this loops"
from the three cards is wrong by one mana; a note that says "this cannot loop" without looking for a closer is wrong whenever the
deck holds one. The note has to do the arithmetic and name what is missing or what closes it.

## 2. What counts as a possible loop

Three words, kept apart everywhere (the answer, the skills, the web):

- **Spellbook combo**: a combo in Commander Spellbook's database (`included` / `almost_included` in `find_combos`). Theirs, written by
  their community, linked to their page. Unchanged by this design.
- **Possible loop (the Vault's reading)**: a small set of cards in the deck whose texts, **read literally**, let one repeatable action pay for
  itself: in one pass the cards give back at least as much of every resource the pass uses (net zero or better), and at least one
  of those resources can then grow without bound (mana, creature tokens, untaps, triggers). It is a reading of the text, not a proof:
  it assumes every piece is on the battlefield and can be used, and ignores opponents.
- **Engine**: the same shape with a net cost (the Sliver trio above). Reported only as the "one ingredient short" form (section 4); it is not
  called a loop.

The Vault never writes "infinite combo", "combo", "wins the game" or "guaranteed" about a possible loop.

## 3. The patterns (small, explicit, and each with its arithmetic)

A first list of five, each a closed shape the detector can check by arithmetic. Anything that does not fit one of them is **not** reported:
no pattern matched means nothing is said about the deck beyond "these patterns found nothing" (section 5).

| # | Pattern | Needs (read from text) | Net per pass | Example shape |
|---|---|---|---|---|
| P1 | **Token for mana** | a repeatable ability that creates a creature token for a mana cost `C`; something that lets that token tap for mana (a lord that grants "{T}: Add ..." to a type the token has, or the token's own ability); haste for the token (or untap, or vigilance) | `m - C`, where `m` is the most mana one tap of the token makes | Sliver Queen + a mana Sliver + Heart Sliver |
| P2 | **Untap for less than it makes** | a permanent with "{T}: Add" of `m` mana; an effect that untaps it, for cost `u` (or free) | `m - u` | a creature that taps for mana + an untap-for-a-cost effect |
| P3 | **Copy or return on enter** | an "enters the battlefield" trigger that makes a token or copy (or returns a card) + a way to make the permanent enter again (blink, flicker, sacrifice and return) for cost `b` | creatures made per pass vs `b`; the loop is closed only if the cost is paid from what the pass makes | token-on-enter creature + a blink effect that costs mana the creature's trigger produces |
| P4 | **Mana-for-mana doubling** | an effect that adds or doubles mana when a permanent taps, + a permanent that taps for mana | extra mana per tap vs the cost to untap or replay | a "whenever you tap ... for mana, add one more" effect |
| P5 | **Sacrifice and recur** | a sacrifice outlet (free or paid) + a way to return the sacrificed permanent that costs no more than the outlet gives | gain per pass vs cost (life, mana, cards, tokens) | outlet + "return target creature card" with a cost the outlet pays |

Only P1 is specified to the last detail below (it is the real example); P2 to P5 follow the same method: **extract typed facts from
each card's text, then do the arithmetic on facts that match**.

### How a card's text becomes a fact

1. **Take the Oracle text of the front face** (`vault.card_faces.front_text`, as the simulation does), split into sentences and abilities, lower-cased
   for matching, with reminder text removed.
2. **Match a short list of anchored patterns**, each producing one typed fact, or nothing:
   - `makes_token(cost=C, repeatable=True, subtype=S, count=1)` from `^{cost}: Create (a|an|N) ... S creature token` (`C` in generic and coloured
     symbols; a cost with `{T}`, `Sacrifice`, `Discard`, `Pay N life` or `X` is recorded as such and makes the pass "needs another resource", never silently free).
   - `grants_tap_mana(to=S|"creatures you control"|"all S", mana=m)` from `(Slivers?|[A-Z][a-z]+ creatures) you control have "{T}: Add ..."`, and `all S have ...`
     (mana `m` from the existing `simulate._mana_added`, read from the symbols, never guessed).
   - `grants_haste(to=...)`, `has_tap_mana(m)` on the card itself, `untaps(target=..., cost=u)`, `reduces_activated_cost(by=1, floor=1)`,
     `adds_mana_when_tapped(extra=1)` for P1 closers.
3. **A fact needs an exact match.** A text with a clause the pattern does not account for (a condition, a target, "you may", "unless",
   "only once each turn", "at the beginning of", "sacrifice", "X", "for each") is recorded as a **blocker** on that fact, not
   ignored. Blockers lower the confidence (section 4) or drop the loop; they never raise it.
4. **Scope by type, not by name**: "Sliver" is read from the token's text ("1/1 colorless Sliver creature token") and from each card's type
   line (`Creature - Sliver`). A card matches a grant if its types satisfy the grant's scope, so the Queen herself counts as a Sliver.

### P1 in detail (the first and, to start with, only pattern)

For each card `Q` with a `makes_token(C, S)` fact in the deck:
1. `tap_mana` = the largest `m` among the `grants_tap_mana` facts (in the deck) whose scope covers a token of type `S`, or `Q`'s own
   (a creature taps once per untap, so grants do not add up: the largest wins).
2. `haste` = whether some `grants_haste` fact (or an untap or vigilance fact) covers a token of type `S`. If not, the token cannot pay back
   this turn: `net = -C`, an engine at a loss, and nothing is reported (this is the P1 non-loop with no haste).
3. `net = tap_mana - C`. If `net >= 0` and no blocker: **the text closes the loop** (section 4). If `net < 0`: look for closers in the deck: cards
   whose facts raise `tap_mana` (`adds_mana_when_tapped`) or lower `C` (`reduces_activated_cost`) so that `net >= 0`. Found: **the text closes the loop with these**; none found
   and `net == -1`: **one ingredient short**; `net < -1`: not reported.
4. The answer carries the arithmetic as steps, so a person can check it: "Pay {2} (Sliver Queen). Get a 1/1 Sliver. It has haste (Heart Sliver)
   and taps for {1} (Manaweft Sliver). Net: -{1} per Sliver."

The Sliver example therefore reports, from the three cards alone: **one ingredient short (-1 mana per token), nothing in the deck closes it**;
and if the deck also holds a card that fits a closer pattern, **the text closes the loop with that card**. Both are honest; "this loops" from the three cards
alone is not a result the detector can produce.

## 4. How it is labelled: the Vault's reading, never Spellbook's

Where it lives: an optional flag on the existing tool, `find_combos` (`include_possible_loops`, default **false**, `POST /decks/combos`), not
a new tool. Reasons: one place for "combos and loops"; a new tool means changes to the tool catalog, plugin, skills' tool lists, ai-parity,
cost table and generated files (AGENTS.md section 7); and the existing `limits` sentence can point at it ("ask with
`include_possible_loops` for the Vault's reading of the card text"). The flag is off by default so the answer's size and the
Spellbook answer do not change for anyone who does not ask (the council's cost table in docs/expert-council.md measures `find_combos` at
514 tokens: the note is measured before it is on by default anywhere).

Independence: the reading uses only the deck's Oracle cards, so it must not fail when Commander Spellbook does. With the flag set, a
Spellbook failure (`502`, `503`) does **not** take the note down: the answer is `200` with `spellbook: {"unavailable": "<message>"}` and the
loops. Without the flag the error behaviour is unchanged.

Shape, in `result`, kept apart from `included` and `almost_included` (which are never touched, merged or reordered):

```
possible_loops: {
  source: "the Vault's reading of card text",
  not_from: "Commander Spellbook",
  covers: ["token for mana"],            // the patterns checked: the answer says what it looked for
  loops: [ {
    confidence: "closes" | "one_short",
    pattern: "token for mana",
    cards: ["Sliver Queen", "Manaweft Sliver", "Heart Sliver"],
    steps: ["Pay {2} (Sliver Queen): a 1/1 Sliver token.", "It has haste (Heart Sliver) and taps for {1} (Manaweft Sliver).", "Net: -{1} per token."],
    net: "-1 mana per pass",
    needs: "something that adds one mana when a Sliver taps, or lowers the {2} to {1}",
    closed_by: [],                       // cards in the deck that fit, when there are some
    assumes: ["every card is on the battlefield", "no opponent acts", "colours of mana are not checked"],
    verify: "Read each card with get_card_oracle; ask a judge before relying on it."
  } ],
  note: "A reading of the text, not a proof, and not Commander Spellbook's. Finding none says only that these patterns found none."
}
```

`provenance` gets a second computed entry beside Spellbook's: kind `computed`, "the Vault's reading of card text", input Scryfall's `oracle_cards`
(Wizards' text, so the Fan Content notice rides with it). It is never listed as a Spellbook source. **The Commander Bracket hint does not use it**: the hint
counts only Spellbook two-card combos (`combos.two_card_combos`); a possible loop is a reading, and a bracket floor must not rest on one. If the owner wants it to,
that is its own decision and its own issue.

### Confidence: two levels, in words

| Level | Means | The note says |
|---|---|---|
| `closes` | every piece matched an exact pattern, no blocker, net >= 0 by the text | "The card text suggests a possible loop: ..." |
| `one_short` | every piece matched, net = -1, nothing in the deck is a closer | "These cards make an engine that is one mana short of a loop: ..." |

There is no "probably" level: a reading with an unpriced piece (an X, a "for each", a "may", a target) is **dropped**, not softened. A third,
weaker level could be added later only with measured false positives (section 6).

## 5. What the AI must say (and not say)

Rules for the tool's strings and for the skills and agents that use it (tested, section 7):

1. **Name it the Vault's reading, and name what it is not.** "The Vault's reading of the card text (not Commander Spellbook's) suggests a possible loop in ...".
2. **Never** say "infinite", "infinite combo", "combo", "guaranteed", "wins the game" or "proven" about a possible loop. Say "possible loop", "engine", "one mana short".
3. **Show the arithmetic.** The steps and the net, from `steps` and `net`, as returned, not reworded into a different claim (the same rule the skills already have for numbers).
4. **Say what it assumes** (`assumes`): the pieces are on the battlefield, no opponent, colours not checked. And that having the cards in a 99-card deck is not having them together:
   how often they meet is a different question (the simulation does not model it).
5. **`one_short` is not a loop.** Say "an engine that needs one more mana per token" and, if `needs` names something, say what to look for. Never round it up.
6. **When it found nothing, say what it covers**: "the Vault's patterns (`covers`) found none; Spellbook lists none either". Never "the deck has no loops" or "combo-free".
7. **Spellbook and the Vault disagree in the open.** Spellbook lists nothing and the Vault finds a possible loop: say both, in that order. Spellbook lists a combo and the Vault
   shows the same cards as a possible loop: report Spellbook's and drop the Vault's duplicate (the server drops it: any loop whose cards all appear in one `included` combo is suppressed).
8. **Verify before relying on it:** `get_card_oracle` for each card named, and a judge for the rules (the judge member and `verify_citation`), because the reading cannot see rulings.
9. The casual table may tell the pod "possible token engine, ask me before turn 5"; it may not tell the pod "there are no infinite combos".

A model answer, for the example deck: "Commander Spellbook lists no combos for this deck, and it lists only the combos it knows. The Vault's reading of the card
text (not Spellbook's) found one engine that is one mana short of a loop: Sliver Queen turns {2} into a Sliver, Heart Sliver lets the new Sliver tap at once and
Manaweft Sliver makes it tap for {1}: net -{1} per Sliver. Nothing else in the deck, by the patterns the Vault checks, closes that gap, so I would not call it a
loop; check the cards (get_card_oracle) and ask a judge before relying on that either way."

## 6. False positives and false negatives

Both are expected. The design's rule: **a false positive costs trust and is worse than a false negative**, so the detector is built to under-report, and says what it covers.

False positives it must avoid, and the guard for each:

| Risk | Guard |
|---|---|
| Summoning sickness (the token cannot tap) | P1 requires haste (or untap or vigilance) for the token; no haste means no report |
| A creature taps once; two grants are not double | `tap_mana` is the maximum of the grants, not the sum |
| Colours: a loop that needs coloured mana the deck cannot make | not checked: listed in `assumes`; `closes` is reported only when every cost in the pass is generic or the grant makes mana of any colour |
| "once each turn", "only", "unless", "may", "target", X, "for each", "sacrifice", a cost the text does not price | blocker: the loop is dropped |
| The piece is a commander (command zone) or must be cast first | cost to deploy the pieces is not counted; the note says "once all are on the battlefield" |
| Legend rule, replacement effects, state-based actions, "doesn't untap" | not modelled; named in `assumes`; the verification step (judge) is where they are caught |
| Duplicates of a Spellbook combo | suppressed (rule 7) |
| Mutual dependence on an opponent's permanents or choices | any pattern needing a target an opponent controls is not in the list |

False negatives, accepted: engines of a shape not in the pattern list, texts the anchored patterns do not match (modal, double-faced beyond the front
face, granted abilities written unusually, "as long as" statics), loops of more than the cards a pattern names (three-piece shapes beyond P1's token, grant and
haste), and anything Spellbook has but the reading does not. The answer says so every time (`covers`, `note`); the skills say "finding none is not proof".

Measuring it before it ships (the owner's rule: evidence, not green tests): run the detector on every deck saved in the owner's account (a read-only script, no
saved data changed), read **every** reported loop by hand against `get_card_oracle`, record true, false and "one short" counts in the issue, and ship only if
there is not a single `closes` that the arithmetic does not support. A false positive found later is a bug with a failing test written first.

## 7. Tests it needs

1. **Fact extraction**, table-driven: each pattern's accepted texts (the four Sliver texts above as fixtures, and invented cards with the same shapes, as the repo's tests use) and its rejected
   near-misses ("create a token" with no cost, a cost with `{T}`, an "X" token, "target creature you control", "each opponent", "once each turn").
2. **Arithmetic**: the Sliver trio gives `one_short`, net -1, with the exact steps; the trio plus an invented closer gives `closes` and names it; haste missing gives no report;
   two mana grants do not add; a grant to another type gives no report; cost 1 with a one-mana tap gives `closes` (net 0).
3. **Never a false claim**: a property test over random small decks of invented cards: `closes` appears only when `net >= 0` and no blocker, and never when any blocker is present.
4. **Wording**: no string the tool returns contains "infinite", "combo" (outside "Commander Spellbook combos"), "guaranteed", "wins the game" or "proven"; every loop has `source`, `not_from`,
   `assumes` and `verify`; the note says "not Commander Spellbook's".
5. **The answer**: with the flag off the answer is byte-for-byte what it is today; with it on `included` and `almost_included` are untouched; a Spellbook failure with the flag gives `200`
   with `spellbook.unavailable`; the extra provenance entry is `computed` with Scryfall's `oracle_cards` and the notice; another person's `deck_id` is `404` (tenancy test, as every deck route).
6. **Skills and agents**: `tests/test_agent_definitions.py` and `tests/test_skills.py` extended: every member that has `find_combos` and the council skill say "the Vault's reading" and never "no
   infinite combos", and say the same for `possible_loops`; the generated plugin and agent files are rebuilt (`scripts/build_plugin.py`, `tests/test_plugin.py`).
7. **Parity and catalog**: the tool description says what the flag does (not the workflow: `tests` already enforce that); `docs/ai-parity.md` gets the row ("web: none yet, a gap"), `public/llms.txt`
   and the tool catalog list the argument; the cost table in `docs/expert-council.md` is re-measured with the flag on.
8. **Twin and compliance**: the detector reads the catalog only (no new outside call), so no new twin; the compliance gate is run (the answer carries Wizards' text-derived material with its notice).

## 8. The smallest first slice

One pull request, behind a flag that defaults off, no web change:

- `vault/possible_loops.py`: the fact extractor for **P1 only**, the arithmetic, the two confidence levels, the blocker list, the dedupe against Spellbook's combos.
- `include_possible_loops` on `POST /decks/combos` and `find_combos`, with the answer shape above, the extra provenance entry, the independence from Spellbook's failure.
- The council skill and the synergy analyst, devil's advocate and casual table agents: ask for the flag in the facts step and follow section 5; the generated files rebuilt.
- The tests of section 7.
- Evidence for #172's third criterion: the hand-read run over the owner's saved decks (section 6), and a real council run on production on a deck holding the Sliver trio (a `zz-check` deck, deleted afterwards)
  that says "one mana short", not "loops" and not "no loops", with the answer captured in the issue.

Later, each its own pull request after the first is verified: P2 (untap), P3 to P5, a "Possible loops" section on the Combos tab of the Deck page (it would also close the
parity gap), and a third, weaker confidence level only if the measured false-positive rate allows it.

## 9. Agreement

The owner agrees by commenting on #172. Until then #172 stays `status:needs-refinement` and nothing is implemented. Recommended answers, one line each:

| Choice | Recommended | If the owner prefers otherwise |
|---|---|---|
| Home | a flag on `find_combos` | a new tool `find_possible_loops`: cleaner name, but every generated file and the tool count change |
| Default | off | on: bigger `find_combos` answer for everyone and a first reading of an unmeasured detector shown by default |
| Levels | `closes` and `one_short` only | add a weaker "worth a look" level once measured |
| First pattern | P1 (token for mana) only | P2 first (untap): a larger body of known cards, a less direct link to the real example |
| Bracket hint | does not use the reading | counts a `closes` loop as a combo input: a separate decision |
| Web | none in the first slice | add the Combos tab section in slice 2 |
