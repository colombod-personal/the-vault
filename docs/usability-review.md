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
