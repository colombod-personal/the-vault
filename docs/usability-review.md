# Usability review (2026-10-04)

Done by running the app locally against a collection of 240 real cards (447 copies, priced from Scryfall's real
data) and using it in a real Chromium browser at desktop size (1280x720) and phone size (375x812 emulated), with
measurements taken from the page rather than judged by eye. Triggered by a report that the card panel is hard to
close: the close button is a circle with an X, and hovering it shows the click surface is hard to find.

## The reported problem, measured

| | Before | After |
|---|---|---|
| Size of the close circle | 32 x 32 px | 44 x 44 px |
| Share of the circle that actually receives a click (desktop) | **36%** (52 of 81 sample points were covered by the card header drawn on top of it) | **100%** |
| While the panel scrolls | the button scrolled away with the content | stays at the top (sticky row) |
| On a phone with a wide table behind the panel | **off-screen** (the page had grown to 671 px wide, so the panel's right edge, and the button, sat beyond the 375 px screen) | on-screen, 100% clickable; page stays 375 px |
| Accessible name | none | "Close card details" |
| Keyboard | no focus ring; focus stayed behind the panel | focus ring; focus moves to Close on open, Tab stays inside, Esc closes, focus returns to the row you opened |
| Panel announced as a dialog | no | `role="dialog"`, `aria-modal`, labelled with the card name |

Root causes: the button was `position: absolute` in the panel's padding area, and the card header (a later sibling with
`position: relative; z-index: 1`) painted over most of it; on phones the Browse table (671 px wide, in a panel with no
horizontal scroll) widened the page, and fixed overlays are placed against the widened page.

## Fixed in this change

- Card panel: its own sticky toolbar row with a 44 px close button, accessible name, focus ring and `title="Close (Esc)"`.
- Account panel and message bar: their small chip-sized ✕ buttons are now 40 px, named, and auto-focused (panel).
- Dialogs: focus moves in, Tab is kept inside, focus returns to the opener on close (card panel and account panel).
- Phones: wide tables scroll inside their panel (the page no longer widens); the Browse filters become a readable two-column
  grid with the search box on its own row (they were four cramped columns with truncated labels such as "Sor"); the section
  tabs fade at the right edge to show they scroll; the card panel is single-column.
- Navigation: `aria-current="page"` on the current section; a visible focus ring on the tabs.
- Data shown to the person (found while using it): cards imported by name only (a Moxfield file without an Edition) were
  all filed under one arbitrary set ("1 sets", "Secrets of Strixhaven Commander: 447 cards"). They now show as "Printing not
  specified" and do not count as a set (PR #70).

## Checked and fine

- Text contrast on Overview, Browse and Decks: 132 text elements, none below WCAG AA (4.5:1, or 3:1 for large text).
- No image without `alt`, no control without an accessible name, no control covered by another (outside the panel bug above).
- Esc, a click on the backdrop and the browser Back button all close the card panel.
- Escape closes dialogs; the card image shows whole with the artist credited.

## Not fixed (open; ordered by how much they matter)

1. **A name-only import prices an arbitrary printing.** Sacred Foundry (foil) showed $1,333.65 each: the sync priced the first
   printing it found. Totals for such files are overstated; the Vault does not say so. Needs a rule (cheapest paper printing of
   the matching finish, labelled "printing not specified") in the sync and a note in the UI. Files with set and number are
   unaffected.
2. **Unpriced cards show $0.00** (dashboard "Latest pulls", Browse) instead of "no price". Needs a flag from the server.
3. **On phones the price columns of the Browse table are off to the right** inside the scrolling panel, with no cue except the
   clipped column. A card layout for phones (name, printing, quantity, value) would serve better than a scrolling table.
4. Small targets: the Browse row's name button is 21 px tall (the whole row is clickable with a mouse, but a keyboard or screen
   reader user lands on the small button), footer links are 15 px tall, the Graph colour pips are 18 px.
5. Pluralisation and spacing: "1 sets in the vault", "1sets" on the Overview tile.
6. Images load lazily and several top-card tiles on the Overview stayed black for a few seconds on first load.
7. No skip-to-content link; no body scroll lock behind the card panel (the page can scroll behind it with the wheel).
8. Not tested: Safari and Firefox (only Chromium), a screen reader, high-contrast or forced-colours mode, zoom to 200%,
   slow networks, the Decks and Lab views beyond contrast and structure, and the Graph view's interaction.

## Phone-first pass (#95, 2026-10-08)

Measured with `scripts/measure_phone.js` at 390 x 844, signed in, on a local Vault holding the owner's real export (21,950 copies, 10,645 printings, 269 sets). Before: every button, chip and row link was 36 px high (the floor set in #90), body text went down to 11 px, labels to 9.5 px (the bottom tab bar), and the dashboard alone had 118 texts under their floor. After the rules at the end of `public/layout.css` ("Phone-first pass"):

| View | Controls | Under 44 px | Texts | Under 12 px (11 px for labels) | Scrolls sideways |
|---|---|---|---|---|---|
| Vault | 42 | 0 | 184 | 0 | no |
| Browse | 80 | 0 | 583 | 0 | no |
| Sets | 281 | 0 | 1,371 | 0 | no |
| Decks (library) | 15 | 0 | 39 | 0 | no |
| A saved deck, tab Cards | 33 | 0 | 118 | 0 | no |
| the deck's Stats, Legality, Upgrades, Combos, Buy list, History tabs | 20 to 24 each | 0 | 0 small | 0 | no |
| the deck's Opening turns tab (#138, 2026-10-09; a Commander deck, first game open) | 33 | 0 | 450 | 0 | no |
| Lab | 24 | 0 | 158 | 0 | no |
| Graph | 37 | 0 | 71 | 0 | no |
| Value | 18 | 0 | 256 | 0 | no |
| Help | 9 | 0 | 63 | 0 | no |
| A card's panel, the Account panel | 82, 103 | 0 | | 0 (after the small colour pip's letter went to 12 px) | no |

Screenshots at 390 px: see the next section (all of them were retaken on 2026-10-09; the Lab shown here on 2026-10-08 was the one #164 replaced).

What the pass changed: every control a finger taps is 44 px high (the colour filters of the Graph 44 x 44); chips, buttons and the bottom tab labels are 11 px (they were 9 to 10.5 px); text is 12 px or more (it was 10 to 11 px: table notes, quantities, deltas, the footer, card-art placeholders, the freshness line); small texts set in the markup are raised to 12 px; the stat cards let a label and its cue, and the freshness line, wrap as whole words.

**Not done, and not claimed:**
- The text drawn inside SVG charts (the Value chart's axis, the Graph's node labels) is not counted: it scales with its chart.
- The phone views were not checked on a real iPhone.

## Lab and Value as summaries and lists, every view photographed (#95, 2026-10-09)

**What the rows asked.** "Lab and Valuation lead with a summary, and their tables become lists on a phone" and "checked with a collection the size of the owner's (~22,000 cards), screenshots of every view at 390x844".

**The collection.** A synthetic one, so that nothing personal is photographed: invented card names, 269 invented sets, invented prices, built to the size and shape of the owner's export (14,597 rows, **21,950 copies**, 9,169 printings, 7,136 card names, 37 purchase months, one folder), imported through `POST /api/v1/imports` into a local Postgres, with eight saved decks of 93 to 108 cards and 400 days of value history. The generator (`seed95.py`, `seed95b.py`) is not in the repository: the numbers and the screenshots are. The owner's own export was used only for #314's first measurement.

**Found.**
- The **Lab** (rebuilt in #164) already leads with three counters (what to buy, what to sell, profit and loss) and has no table: its cards to buy, decks and spare copies are lists. No change needed; measured below.
- The **Value** view led with a summary, but the half screen above the figures was the price date and two buttons stacked in a column, and its **ledger of months was a table**: on a phone the last two of its five columns (market value added, cumulative value) were off the right edge of a scrolling box (screenshot before: the "Market" header cut at the screen's edge).

**Changed.** On a phone the price date and the buttons sit in a row under the title, so the four figures start on the first screen; the ledger is a list (month and value added on the first line, cards added, spend and cumulative value on the second), 12 months at a time with a "Show 12 more months" button (44 px), filter and sort full width. The table is still what wider screens get (the list is `display: none` there). `tests/test_tap_targets.py` keeps both.

**Measured** with `scripts/measure_phone.js` at 390 x 844 on that collection, after the change:

| View | Controls | Under 44 px | Texts | Under 12 px (11 px for labels) | Scrolls sideways |
|---|---|---|---|---|---|
| Vault | 42 | 0 | 214 | 0 | no |
| Browse | 143 | 0 | 586 | 0 | no |
| Sets | 281 | 0 | 1,371 | 0 | no |
| One set (269 sets, the busiest) | 71 | 0 | 366 | 0 | no |
| Decks (library, 8 decks) | 22 | 0 | 89 | 0 | no |
| A saved deck, tab Cards | 87 | 0 | 551 | 0 | no |
| Ideas (the Graph's route opens it) | 11 | 0 | 77 | 0 | no |
| **Lab** (8 decks, 6,990 spare cards) | 83 | 0 | 465 | 0 | no |
| **Value** | 23 (was 22) | 0 | 114 (was 267: the table's cells are no longer shown) | 0 | no |
| Help | 9 | 0 | 65 | 0 | no |
| The Account panel | 89 | 0 | 307 | 0 | no |

Screenshots (390 x 844, the first screen unless named): [Vault](screenshots/phone-dashboard-390.jpg), [Browse](screenshots/phone-browse-390.jpg), [Sets](screenshots/phone-sets-390.jpg), [a set](screenshots/phone-setdetail-390.jpg), [Decks](screenshots/phone-decks-390.jpg), [a saved deck](screenshots/phone-deck-390.jpg), [Ideas](screenshots/phone-ideas-390.jpg), [Lab](screenshots/phone-lab-390.jpg) (summary), [Lab: cards to buy](screenshots/phone-lab-buy-390.jpg), [Lab: spare copies](screenshots/phone-lab-sell-390.jpg), [Value](screenshots/phone-value-390.jpg), [Value: the ledger as a list](screenshots/phone-value-ledger-390.jpg), [Help](screenshots/phone-help-390.jpg), [Account](screenshots/phone-account-390.jpg).

**Not done.** The Browse table is still a table that scrolls sideways inside its panel with the card name pinned (it is not part of this row). Not checked on a real iPhone. The synthetic prices and names are not the owner's, so the Lab's lists show invented cards.
