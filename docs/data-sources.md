# Data sources: what we use, what we do not, and why

Evaluated 2026-10-04 for issues #19 (extra price sources) and #20 (combos). The rule from
`docs/compliance.md` applies: a source is used only when its terms are confirmed, always with
provenance, never presented as the Vault's own. Pages that could not be read are marked unverified.

| Source | Use | Status | Reason |
|---|---|---|---|
| Scryfall `oracle_cards`, `rulings`, `oracle_tags`, `default_cards` | Catalog, rulings, tags, cheapest prices | **Built, off by default** (`CATALOG_SOURCES`) | Terms to be read directly before enabling (compliance gate, #62) |
| Wizards Comprehensive Rules | Rules text and search | **Parser and loader built, off by default** | Needs Wizards' permission or a decision to link and excerpt only (#62) |
| Commander Spellbook | Combos for a deck | **On demand, no ingestion** (built in M2, `find_combos`) | Code is MIT, but no data licence was found; so no copy of their data is stored |
| Cardmarket price guide | Prices | **Not used** | Their API is closed to new applications; the public price guide download exists but its terms and URL could not be read |
| Card Kingdom price list | Prices | **Not used** | A public JSON endpoint exists, but no terms of use or data licence could be found; third-party wrappers sell access to it |
| Moxfield, Archidekt, EDHREC | Decks, aggregates | **Not used for fetching** (Archidekt: one deck per user action, as today) | Terms forbid or do not permit automated access (see `compliance.md`) |

## Prices (#19)

- Built: the cheapest priced paper printing of every card, from the default-cards file the daily
  price job already downloads (`oracle_prices`, today only, source "scryfall"). Scryfall's prices
  come from TCGplayer and Cardmarket; they are shown as Scryfall's, with the date.
- Not built: Card Kingdom and Cardmarket prices. Re-open if a licence or permission is obtained.
  Each stored price row already carries its `source`, so adding a source later does not mix numbers.
- Shopping lists (M2) are plain text the user pastes into the store's own list tool (Card Kingdom's
  Deck Builder accepts pasted lists). The Vault never fetches store pages, fills carts or scrapes.

## Combos (#20)

- Decision: query Commander Spellbook's public API on demand when a user asks about a deck. No
  combo table, no bulk copy, no cache beyond a short in-process one. Every answer carries
  provenance (Commander Spellbook, link to each combo's page, as-of) and no claim of our own.
- To revisit: ask the Commander Spellbook maintainers (Discord) whether a nightly copy is welcome.
  Ingestion is worth it only with their consent; until then a call per request is the safe option.
- Their client needs a descriptive `User-Agent`, a timeout, a small rate limit and a circuit
  breaker so a slow upstream never slows the Vault.
