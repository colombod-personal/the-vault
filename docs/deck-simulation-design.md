# The Deck page shows how the deck plays: mana curve and simulated opening turns (design for #138)

Status: **agreed and built** (acceptance criterion 3 of #138: "The design (charts, phone layout) is agreed before implementation and linked
here"). The owner approved the recommended defaults of section 11 on 2026-10-09 ("take the recommendations"); the tab is built as written, both
steps of section 10 in one pull request, with `margin_points` and #392's seed range (closed). It is in `public/views/deck_opening.jsx`, its words in
`public/lib/deck_sim.js`, its tests in `tests/test_deck_sim_page.py` and `tests/js/deck_sim.test.mjs`. The rest of this page is the design as it was approved. The mockup is static HTML
with invented figures: [`docs/mockups/deck-simulation.html`](mockups/deck-simulation.html), photographed at 1400 px and 390 px:
[desktop](screenshots/deck-simulation-mockup-1400.jpg), [phone](screenshots/deck-simulation-mockup-390.jpg) (every sample game is
opened in those two images; in the app only the first is).

## 1. The ask, and what answers each part

#138 (part of #88): "Show the curve and the simulation from #137 on the Deck page: a few sample games turn by turn, and the odds (land
drops, mana by turn, cards in hand, discard risk), with the deck's own exceptions (discard as a plan). Needs a design (charts, phone
layout) before implementation. Uses POST /api/v1/decks/simulate."

| Criterion of #138 | Where this design answers it |
|---|---|
| The Deck page shows the mana curve and a few sample games turn by turn from POST /api/v1/decks/simulate (#137) | 2 (the tab), 3 (curve and sample games), 4 (fields) |
| It shows the odds: land drops, mana by turn, cards in hand, discard risk, with the deck's own exceptions (discard as a plan) | 3 (four odds panels, the plan note), 4 (fields) |
| The design (charts, phone layout) is agreed before implementation and linked here | this page; the owner's agreement is recorded in the issue (section 11) |
| Works on a phone-width screen, checked in a browser with a screenshot | 7 (phone rules), 10 (what the implementation PR must show) |

## 2. Where it sits: a new tab, "Opening turns", after "Stats"

The deck page's tabs today (`TABS` in `public/views/deck.jsx`): Cards, Stats, Legality, Upgrades, Combos, Buy list, and History for a
saved deck. Proposed order:

**Cards, Stats, Opening turns, Legality, Upgrades, Combos, Buy list, History**

Why a tab of its own and not a panel in Stats:

- Stats makes one quick call and shows facts about the list. The simulation is a different kind of answer (a run of 1,000 games, a
  seed, controls that change it) and has its own loading, error and "too few cards" states. Mixed into Stats, its failure or its
  wait would hold up the facts that are already known.
- Every other analysis on this page is a tab that calls its endpoint when opened (`useDeckAnswer`), so nothing is simulated until
  the person asks.
- The tab row already scrolls sideways inside its own box on a phone (`.deck-tabs`, measured in docs/usability-review.md as no
  page-level sideways scroll); one more chip of 44 px height does not change that.

The Stats tab keeps its curve and gains one line under it, "See how this curve plays: Opening turns" (a button that sets the tab).
The mana curve also leads the new tab, because the odds below it are explained by it. Both draw the same component from the same
`POST /decks/stats` field (`curve`), so they cannot disagree.

AGENTS.md section 4 says deck panels lead with the deck (name, format, commander(s), card count, colour identity). The page header today
shows only the title, author and source link, so the tab leads with its own deck line, read from the answer's `deck.overview`
(the first line of the intro panel in the mockup), then the "As played" line (section 3, honesty). Putting that deck line in the page
header for every tab is a separate, small change for the other tabs; it is not part of this design.

## 3. What the tab shows, top to bottom

Phone order is the same as desktop order; desktop puts panels side by side where the mockup does.

1. **Intro and controls.** The deck line (format, card count, commander(s), colours), then one sentence that says what this is: "A simulation, not a prediction. The Vault played this list 1,000 times
   with a simple player (no opponent) and counted what happened in the first 6 turns." Then the **As played** line (format, who draws on
   turn 1, on the play or draw, turns, games, seed) and the controls: *On the play / On the draw* (a two-button group), *Turns*
   (4, 6, 8, 10), *Format* (the picker the Legality and Upgrades tabs already use, default the page's format), and the button
   *Play again with a new seed*.
2. **Colour warning**, when the answer has `colour_warning` (a deck of three or more colours): the server's own sentence, in a bordered
   notice above every figure, not in the assumptions below.
3. **Headline figures**, four tiles, named exactly as the answer names them so a person and an assistant read the same words:
   *Five mana by turn 5*, *Missed a land drop by turn 4*, *Discarded by turn 5*, *Mulligan rate* (each "of simulated games").
   The first three exist only when `turns` is 5 or more (the server returns them then); with fewer turns only *Mulligan rate* shows.
4. **Mana curve** (cards that are not lands, by mana value 0 to 7+), with the counts above the bars, one caption line
   ("37 lands, 62 other cards, average mana value of the non-land cards 2.9") and a "Show the curve as a table" disclosure.
5. **The odds, turn by turn: four panels**, each a list of one row per turn (label, a bar, the value in words):
   - *Land drops*: `land_drop` (did the player play a land this turn), and `all_land_drops_so_far` (no miss since turn 1).
   - *Mana by turn*: `mana_available` and `mana_spent` (averages), and `every_spell_in_hand_cost_too_much` ("Stuck: every spell in hand cost
     more than the mana"; on turn 1 with no one-drops that is normal, and the panel says so).
   - *Cards in hand*: `cards_in_hand`, the average at the end of the turn.
   - *Discard risk*: `discarded_by_now`, cumulative share of games that have had to discard to hand size by that turn, with the plan note.
6. **The deck's own exceptions** (`discard_may_be_the_plan`) sit inside the Discard risk panel: "Discarding may be part of this deck's
   plan", one line per card with its reason ("Reliquary Tower: no maximum hand size", "Wheel of Fortune: wheels hands"), and a sentence that
   the simulation lifts the hand limit once a "no maximum hand size" card is in play. When the list is not empty the discard bars use the
   neutral colour, not the warning colour: a deck that wants to discard is not told it has a fault. (The server sends each plan as one
   string, "Card: reason"; the reasons never contain a colon but some card names do ("Circle of Protection: Red"), so the page splits on
   the **last** ": ".)
7. **Sample games, turn by turn**: "The first 5 of the 1,000 games, as they came up. They are not picked to be typical, lucky or
   unlucky." (Honest, because `samples` is `results[:samples]`: the first games of the run.) Each game is a disclosure (the first one
   open): *Game 3 · mulligan 1, kept 6 · land drops made in turns 1 to 3: 3 of 3*, then the opening hand, then a table of the turns
   (turn, drew, land played, mana with what was spent, cast, hand size, notes). Notes are two flags from the data: "Nothing castable:
   every spell in hand costs more than the mana" (`nothing_affordable`) and "Discarded X" (`discarded`).
8. **What this simulation does not do**: the answer's `assumptions`, all of them, verbatim, as a list that is always open (not behind a
   disclosure), and under it the provenance line: "Computed by the Vault from Scryfall's Oracle card data (as of <date>). The mana a card
   makes is the Vault's reading of its text, not Scryfall's or Wizards' figure." plus the Fan Content notice the other panels carry.

### Charts: why bars in a list, not a chart library

The four odds panels are lists of rows (turn label, bar, value written out), not a drawn chart, for three reasons:
a phone shows them at 390 px without a sideways scroll or shrinking text (the rule in docs/usability-review.md: text 12 px or more, 11 px for
labels, controls 44 px); the value is real text next to each bar, which is the text alternative (section 8); and no charting library is added.
The curve reuses the existing `.deck-curve` bars. Bars never carry the meaning alone: the number is always printed, and the colour
(gold, copper, neutral) only separates the series in a panel that has two.

## 4. The API fields used (nothing new needed for the first version)

One call, `POST /api/v1/decks/simulate`, made when the tab opens and again when a control changes. Body: `text` (the page's list: the
page already holds it) or `deck_id` for a saved deck, `format`, `on_the_play`, `turns`, and optionally `games`, `samples`, `seed`. The
page sends `games: 1000` and `samples: 5` explicitly so the copy ("1,000 games") is what was asked, not what a default happens to be.

Read from `result`:

| Field | Used for |
|---|---|
| `games`, `turns`, `on_the_play`, `multiplayer`, `seed`, `format` | the intro sentence and the *As played* line ("multiplayer: everyone draws on turn 1" when `multiplayer`, "two players: no draw on turn 1 on the play" when not and `on_the_play`) |
| `headline.mulligan_rate`, `five_mana_by_turn_5`, `discarded_by_turn_5`, `missed_a_land_drop_by_turn_4` | the four tiles (the last three only when present) |
| `per_turn[]`: `turn`, `land_drop`, `all_land_drops_so_far`, `mana_available`, `mana_spent`, `cards_in_hand`, `discarded_by_now`, `every_spell_in_hand_cost_too_much` | the four odds panels |
| `samples[]`: `mulligans`, `opening_hand[]`, `turns[]` (`turn`, `drew`, `land`, `mana`, `spent`, `cast[]`, `hand`, `discarded[]`, `nothing_affordable`) | the sample games |
| `discard_may_be_the_plan[]` | the plan note |
| `colour_warning` (present for three or more colours) | the notice above the figures |
| `assumptions[]` | "What this simulation does not do" |
| `unmatched[]` | the line "Not in the card catalog: ...", same words as the Stats tab, plus "these cards are left out of the games" (the server skips them, so the deck plays with fewer cards than the list has) |
| answer `deck`, `provenance[0]` | the provenance line (kind `computed`, input Scryfall's `oracle_cards` with its date) |

The curve and the caption come from the second call the tab makes in parallel, `POST /api/v1/decks/stats` (`curve`, `lands`, `cards`,
`nonland`, `average_mana_value_nonland`), which the Stats tab already makes. The curve shows as soon as that call returns and does not
wait for the simulation; if it fails, the curve panel says so and the rest still works. Nothing is computed in the browser: the page
formats numbers the answers give (CLAUDE.md: the front end only renders API answers).

Both are analyses: **30 a minute per person** is the rate limit (`tests/test_deck_api.py::test_analyses_are_rate_limited_per_person`),
so "Play again" can be refused with HTTP 429 and a `Retry-After` (state in section 5).

### Small server changes worth making in the implementation PRs (each additive, each with a test)

1. **`margin_points`** in `result`: the most a percentage can be off at this number of games (95%, `1.96 * sqrt(0.25 / games)` in points; about
   3.1 at 1,000 games). The page prints "good to about 3 points either way" from it instead of computing it. Without it the sentence is
   left out, or the figure is fixed copy that is wrong the day `games` changes. Recommended: add it.
2. **#392** (filed with this design): the default seed can exceed the maximum the endpoint accepts, so "Play again with the same seed"
   would fail for about half of all decks. Must be fixed before the seed is shown as something to repeat.
3. Optional, later: a `land` flag on the opening hand's cards (today `opening_hand` is names only, so the page cannot tell lands
   from spells without a lookup), and a line in `ASSUMPTIONS` that the commander is cast at most once and no commander tax is
   counted (true of `simulate._play_turn`; the assumptions list does not say it).

## 5. States

| State | What the person sees | Source |
|---|---|---|
| First load | A status line "Playing 1,000 games..." with the spinner (the existing `Waiting` pattern), the controls disabled; the curve appears on its own when the stats call returns | pending request |
| Play again | The old figures stay, dimmed; the button reads "Playing again..." and is disabled; the new figures replace all of them together (no layout jump) | pending request with a result already held |
| Error (network, 5xx) | "Couldn't work out the opening turns: <message>" as an alert, and a *Try again* button (44 px) | `ApiError` with `status` 0 or 5xx |
| Asked too often | "Too many analyses this minute. The Vault allows 30 a minute for each person. You can play again in 41 seconds.", the button disabled with a countdown from `Retry-After` | HTTP 429 (`ApiError.retryAfter`) |
| Too few cards | "Not enough cards to play 6 turns." plus the server's sentence in quotes ("The deck has 9 cards: too few to play 6 turns.") and "Add cards, or choose fewer turns"; the curve still shows; odds and games are replaced by the message | HTTP 400 (the server's `detail`); a deck needs 7 cards plus one per turn, and the count excludes the commander, a companion and cards the catalog does not know |
| Cards unknown to the catalog | The Stats tab's line "Not in the card catalog: ..." and a sentence that they are left out | `unmatched` |
| Three or more colours | The notice above every figure | `colour_warning` |
| Fewer than 5 turns | Only the *Mulligan rate* tile; the other three tiles are not drawn (they would be empty) | `headline` lacks the keys |
| 60-card formats (standard, pioneer, modern, legacy, vintage, pauper, brawl, ...) | Same layout. No commander is cast; the *As played* line says "two players: no draw on turn 1 on the play"; the curve caption has no command-zone sentence | `multiplayer` false, no commander line in the list |
| The list changes (after "Change this deck" is applied, or a refresh) | The figures re-run for the new list, like the other tabs (`useDeckAnswer` is keyed on `text`) | `text` |

**Question for the owner, not blocking:** the server treats Commander, Oathbreaker, Pauper Commander and PreDH as multiplayer (everyone
draws on turn 1) and every other format, including `brawl`, as two-player. That is right for Standard Brawl; a 100-card Brawl table is
usually multiplayer. Recommended default: leave it and say so under the format picker ("two players"), until the owner says how Brawl is played.

## 6. Layout at 1400 px and at 390 px

**1400 px** (mockup: `deck-simulation-mockup-1400.jpg`). Content width as the other deck tabs (about 1240 px). The intro panel spans
the row; the four tiles are one row of four; the curve and a "How to read this" panel are two columns; the four odds panels are a
2 by 2 grid; sample games are full width, each turn table with seven columns; assumptions and provenance are one full-width panel.
The page is about 4,900 px high with all five games open (five games, six turns each).

**390 px** (mockup: `deck-simulation-mockup-390.jpg`). One column, 16 px gutters. The tabs scroll inside their own row. Controls wrap:
the play/draw group on one line, *Turns* and *Format* below, the button on its own line, every control 44 px high. The tiles are two by
two. The odds rows are `Turn 3 | bar | 82.7% of games` on one line (the bar takes what the row leaves). The sample game's turn table
turns into **one block per turn**: the turn as a heading, then *Drew / Land played*, *Mana / Hand* side by side, *Cast* and *Notes*
below, each with its small label above the value, and *Notes* is dropped when empty. There is no horizontal scroll anywhere.

Measured on the mockup in headless Chromium (CDP, the same way the other phone checks were made):

| Width | Controls | Under 44 px high | Text under 11 px | Page wider than the screen |
|---|---|---|---|---|
| 390 | 23 | 0 | 0 | no (scrollWidth 390) |
| 1400 | 23 | 0 | 0 | no (scrollWidth 1400) |

Labels are 11 px monospace, as in the existing phone rules; all other text is 12 px or more. The mockup uses system fonts (it makes no
network request; the app's own fonts are the ones in `public/styles.css`) and its tokens are copied from `public/styles.css`, so
the implementation adds no colour: it reuses `--gold`, `--copper`, `--surface`, `--border`, `--danger` and the existing `.panel`,
`.eyebrow`, `.deck-tabs`, `.chip`, `.label-mono`. New layout rules go in `public/layout.css` (CLAUDE.md).

## 7. Phone rules, checked in the implementation

From docs/usability-review.md ("Phone-first pass"): every control 44 px high; text 12 px or more (11 px for labels); no sideways scroll
of the page; wide content scrolls inside its own panel or becomes a list. The sample games do the last (a block per turn). The
implementation PR runs `scripts/measure_phone.js` on the new tab at 390 px, with a saved deck, and pastes the table (controls, under 44
px, small texts, scrolls sideways) like the other views.

## 8. Accessibility: text alternatives for the charts

- **The odds panels**: each bar is `aria-hidden` and each row's text carries the value ("Turn 3", "82.7% of games"), so the list is
  its own text alternative; the panel has a heading and a one-line description of what the number means.
- **The mana curve**: `role="img"` with an `aria-label` that lists every value ("Mana curve, number of non-land cards by mana value. 0: 1,
  1: 11, ..."), plus a visible disclosure "Show the curve as a table" with a real table (mana value, cards), reachable by keyboard.
- **Colour is never the only signal**: the discard note has text, the "Nothing castable" and "Discarded" flags are words, the warning
  notice has a heading sentence; the danger colour is not used for discard when the deck's plan says discarding is fine.
- **Controls**: the play/draw choice is a group with `aria-pressed` buttons (the pattern of `.deck-tabs`); selects have labels; the
  sample games are real `<details>`, so the keyboard and screen readers get open/closed for free; the focus ring is the existing one.
- **Status**: the loading line is `role="status"`, errors are `role="alert"`; a re-run announces "Playing again" and, when done, the
  new *As played* line (it contains the seed) is the visible proof that new figures arrived.
- Not checked yet, and said so: a real screen reader, Safari and Firefox, forced-colours mode (the same list docs/usability-review.md
  carries). The turn tables are `<table>`s with column headers on desktop; on a phone they are restyled to blocks with CSS, which some
  screen readers then read as a list of cells: the implementation checks the turn blocks with a screen reader or, failing that, keeps
  the table semantics with `display: grid` on `tr` and `role` attributes.

## 9. Honesty copy (what the page says about itself)

Fixed copy, from the answer where there is one:

- Title sentence: "A simulation, not a prediction." (shown above all figures.)
- How many runs and which seed: the *As played* line always prints games and seed, and says "same deck, same seed, same figures": the
  default seed comes from the list, so reopening the tab gives the same numbers; *Play again with a new seed* is the only thing that
  changes them. Figures move "by a few points" between seeds ("good to about 3 points either way" from `margin_points`).
- The samples "are the first 5 of the 1,000 games, as they came up", not chosen.
- The player: keeps 7 cards with 2 to 5 lands, otherwise mulligans up to twice (London); each turn draws, plays one land, casts the
  biggest thing the mana allows, ramp first early. Written as fixed copy only about the player; everything else about what is not
  modelled is the server's `assumptions`, so the two cannot drift.
- Colours are not checked: stated in `assumptions` (always shown) and, for three or more colours, in the notice above the figures.
- The mana of rocks and creatures is the Vault's reading of card text: in the provenance line, so a person does not take it for
  Scryfall's or Wizards' data.
- The same figures an assistant reports ("five mana by turn 5: 58% of simulated games", `simulate_draws`) use the same names as the
  tiles, so the page and the chat agree.

Not claimed: that the numbers are the deck's real win rate, that they are "good" or "bad" (the Vault has no power score and the
page shows no verdict word), or that they are Scryfall's.

## 10. Smallest steps, and the evidence each needs

Each is its own pull request with `Refs #138` and a `Left open:` line; the issue closes only with the evidence below posted in it.

1. **Tab, controls, headline, curve, odds panels, plan note, assumptions, states** (everything except the sample games). Needs: the
   web-side API client call (`VaultApi.deckSimulate` in `public/lib/api.js`), a `DeckOpeningTurns` component, the shared curve
   component, the Stats tab's link, rules in `public/layout.css`, the rebuilt bundle (`npm --prefix web run build`), a row in
   `docs/ai-parity.md` ("Simulate the first turns of a deck" gets its UI place and the gap line G7 loses its first sentence;
   `tests/test_ai_parity_doc.py` reads the quoted labels, so the label "Opening turns" must exist in the front end), the `margin_points` field
   (test), and a test per state where the repo's front-end tests can reach it.
   Evidence: screenshots at 1400 and 390 px of a saved deck in each state it can reach locally (loaded, loading, error, too few cards, a three-colour
   deck's notice, a deck with Reliquary Tower so the plan note shows), the phone measurement table, and a production screenshot after deploy.
2. **Sample games** (the disclosures and the turn tables), with *Play again with a new seed*, which needs #392 fixed first.
   Evidence: the same, at 390 px, with the first game open and the turn blocks measured.
3. Later, only if wanted: a land marker in the opening hand (section 4, item 3); more games ("5,000") behind a button.

## 11. Agreement

The owner agreed on 2026-10-09 ("take the recommendations"): every default below was taken. The choices as recommended, so the owner can answer in one line each:

| Choice | Recommended | If the owner prefers otherwise |
|---|---|---|
| Where | a new tab "Opening turns" after Stats | a panel inside Stats: one fewer tab, a slower Stats page and one failure state for both |
| Charts | rows with a bar and the value in words, the existing curve bars | drawn line charts: harder at 390 px and need a text alternative of their own |
| Name of the tab | "Opening turns" (the issue's words) | "Playtest", "Goldfish" (a term many players know, few casual ones) |
| Games and turns | 1,000 games; turns 4, 6, 8 or 10 | let the person choose games (up to 5,000: slower, needs a progress state) |
| Brawl | two-player (as the server does today) | add `brawl` to the server's multiplayer formats |
| Figures' wording | "of simulated games", `margin_points` printed | no margin sentence |

## 12. As built: where the tab differs from this page

Everything in sections 2 to 10 is built. The differences, each small, so the page and the code can be compared:

- **Asked too often (429):** the alert holds the title and "The Vault allows 30 a minute for each person." (the number is `DECK_LIMIT`, a test keeps them equal);
  the countdown is on the button ("Play again in 41 s") and not in the alert, so a screen reader is not interrupted every second. The controls are disabled
  during the countdown. Figures already on screen stay, dimmed, with "The figures below are from the last run that worked."
- **Failed run (network, 5xx):** the same: the last figures that worked stay dimmed under the alert; only "too few cards" removes them (the answer for those
  settings does not exist).
- **Phone turn blocks** keep the table semantics with explicit ARIA roles (`role="table"`, `row`, `cell`, `rowheader`, `columnheader`), so a screen reader
  still reads a table when the CSS draws blocks. Not checked with a real screen reader, as section 8 said.
- **Discard bars** are neutral when the deck has a plan, and use the danger colour when it has none.
- **The commander sentence** under the curve comes from the stats answer (`by_section.commander`), so the curve does not wait for the games.
- **Not built (section 10, item 3, "later, only if wanted"):** a land marker in the opening hand, more than 1,000 games, a line in `ASSUMPTIONS` about the
  commander tax.

Screenshots of the real app (headless Chromium, `docs/screenshots/deck-opening-turns-*`, each at 1400 and 390 px): the tab loaded
(`deck-opening-turns-`, and `-games-open-` with every sample game open), playing again (`-busy-`), the first load (`-loading-`), a failed call
(`-error-`: the network call was refused by the test page), the server's own 429 (`-limit-`: 40 analyses sent first), too few cards
(`-toofew-`, the server's 400), three or more colours (`-colours-`), cards the catalog does not know (`-unknown-`), a 60-card list (`-sixty-`), the
Stats tab with its link (`-stats-link-`), and phone close-ups of a sample game and the discard panel (`-closeup-390`). They were taken on a local
Vault whose catalog holds **invented cards** ("Demo Forest", "Demo Tower", ...), not Scryfall's data, so the figures are those of invented decks.
