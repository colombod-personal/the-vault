# Data sources: what we use, what we do not, and why

Evaluated 2026-10-04 for issues #19 (extra price sources) and #20 (combos). The rule from
`docs/compliance.md` applies: a source is used only when its terms are confirmed, always with
provenance, never presented as the Vault's own. Pages that could not be read are marked unverified.

| Source | Use | Status | Reason |
|---|---|---|---|
| Scryfall `oracle_cards`, `rulings`, `oracle_tags`, `default_cards` | Catalog, rulings, tags, cheapest prices | **Built, off by default** (`CATALOG_SOURCES`) | Terms to be read directly before enabling (compliance gate, #62) |
| Wizards Comprehensive Rules | Rules text and search | **Parser and loader built, off by default** | Needs Wizards' permission or a decision to link and excerpt only (#62) |
| Commander Spellbook | Combos for a deck | **On demand, no ingestion** (built in M2, `find_combos`) | Code is MIT, but no data licence was found; so no copy of their data is stored |
| Cardmarket price guide | Prices | **Not used** | Their API is closed to new applications; the site is behind a bot check (2026-10-05), so its terms could not be read by a tool: the owner reads them |
| Card Kingdom price list | Prices | **Not used** | `api.cardkingdom.com/api/pricelist` answers publicly, but the terms (read 2026-10-05) forbid robots and data extraction except search engines following robots.txt, and robots.txt disallows `/api/`: needs Card Kingdom's permission |
| Magic Madhouse product feed | Prices, stock | **Not used** | Offered to affiliate partners: a full product feed, Google Shopping format, four times a day (affiliate page, 2026-10-05). The terms forbid copying site content otherwise. Joining is the owner's decision |
| Archidekt | Public decks | **Used, read only**: one public deck when a person asks, credited with a link back | The Vault only reads, never writes, and does not crawl or search (see `compliance.md`) |
| Moxfield, EDHREC | Decks, aggregates | **Not used for fetching** | Terms forbid or do not permit automated access (see `compliance.md`) |

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
- Their client (`vault/combos.py`) sends a descriptive `User-Agent`, has a 15 s timeout, and **since #20** a rate limit and
  a circuit breaker, so a slow upstream never slows the Vault and a busy day cannot flood a volunteer-run service:
  - **Rate limit:** at most 20 calls a minute from one server process (sliding window). The 21st gets `503` with
    `Retry-After`, and no request goes to Commander Spellbook.
  - **Circuit breaker:** after 3 failures in a row (timeouts, 5xx, 429, an answer in an unexpected shape) the client stops
    calling for 60 s (`503` with `Retry-After`); then one probe call decides: success closes the breaker, failure reopens it for
    twice as long, up to 5 minutes. An upstream `Retry-After` on a 429 opens it at once for that long. A refusal of our own
    request (another 4xx) is not counted as the service failing. Everything else in the Vault keeps working meanwhile.
  - **Limits of the guard, honestly:** both are per process. Each warm serverless instance counts on its own, so the real ceiling
    is 20 a minute times the number of instances, and a breaker opened in one instance does not stop another. A shared counter
    in Postgres would fix that and was not built because one Vault instance is what runs today. The numbers are ours (Commander
    Spellbook publishes no limit that we found); tested against the twin's scripted failures (`tests/test_combos.py`), not
    against a real overload, which was never provoked.

## Shops: links, terms and price feeds (issue #80, read 2026-10-05)

| Shop | Plain search links | Automated price or stock lookups | A legitimate route to live prices | Saved searches / alerts for users |
|---|---|---|---|---|
| Card Kingdom | Allowed: the terms only forbid linking that damages them or implies endorsement. Format checked: `https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=NAME` | Forbidden ("data mining, robots ... except in accordance with ... robot.txt ... only to compile for search results"); `/api/` disallowed | Ask Card Kingdom (public price list exists; affiliate cart paths exist) | Restock notices on product pages (signed in) |
| Magic Madhouse | Not restricted by the terms; format checked: `https://magicmadhouse.co.uk/?q=NAME` | Copying or exploiting site content needs written permission | **Affiliate programme with a full product feed** (prices and stock, 4x a day) | Not checked |
| Cardmarket | Format used: `https://www.cardmarket.com/en/Magic/Products/Search?searchString=NAME` (works in a browser) | Unknown: terms not readable by a tool (bot check) | API closed to new applications | Wants lists (signed in) |

Recommendation:
- **Now:** per-card search links only (as the skills do), no fetching of shop pages, prices stay Scryfall's and dated.
- **For a real "best price"** (#84): ask Card Kingdom for permission to use its price list, and decide whether to join Magic Madhouse's affiliate programme for its feed. Affiliate income would make the Vault earn money from referrals: an owner decision against "the app stays free" (free to users is unaffected) and the Fan Content Policy's terms on monetisation (to read before joining).
- Owner reads Cardmarket's terms in a browser.

## Metagame and Limited data (issue #105, read 2026-10-05)

| Source | What it has | Terms (read first-hand) | Verdict |
|---|---|---|---|
| **17Lands public datasets** (`17lands.com/public_datasets`) | Draft picks, game results and replays for each set and format (PremierDraft, TradDraft, Sealed, a Powered Cube...), updated every few weeks | "Unless otherwise noted, these data sets are licensed under a **Creative Commons Attribution 4.0** International License." Attribution required; the site's own terms forbid reselling its content and exploiting protected content, which the dataset licence overrides for these files | **Usable** with attribution. Files are large (one set and format: draft 75 MB, game 22 MB gzipped), so we never store them: a scheduled job computes small per-card aggregates (games in hand, win rate in hand, pick counts) and keeps only those, labelled "17Lands data, CC BY 4.0, set, format, date" |
| MTGGoldfish | Metagame shares, decklists, prices | Terms of Use: "intended solely for personal, non-commercial use"; contents may not be reproduced or republished | **Not used as data.** Links only |
| MTGTop8 | Tournament decklists | No terms of use found on the site; the page says Wizards owns the card information; no API | **Not used.** Ask them first if we ever want it |
| EDHREC | Commander popularity | Already recorded: not fetched by the Vault; popularity comes from Scryfall's `edhrec_rank` field only | Unchanged |
| Two-Headed Giant | Nothing public that we found | The rules are in the Comprehensive Rules (section 810), read live | Computed from rules and card text; no data source |

Consequences for the council (docs/expert-council.md):
- **Competitive metagame:** no licensed source found; format experts keep labelling metagame statements as opinion.
- **Limited:** 17Lands aggregates can ground draft help (what a card's win rate in hand is, by set), with attribution; this is a data job plus tool, not yet built (new issue).
- **Do not scrape** MTGGoldfish, MTGTop8 or EDHREC; ask for permission or an API if a source is wanted.
