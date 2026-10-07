# Data sources: what we use, what we do not, and why

Evaluated 2026-10-04 for issues #19 (extra price sources) and #20 (combos); shops re-read 2026-10-07 (#80). The rule from
`docs/compliance.md` applies: a source is used only when its terms are confirmed, always with
provenance, never presented as the Vault's own. Pages that could not be read are marked unverified.
**The gate is enforced:** every catalog source the code can load needs a row marked read in the "Source gate" table of
`docs/compliance.md`, and `tests/test_compliance_gate.py` fails otherwise.

| Source | Use | Status | Reason |
|---|---|---|---|
| Scryfall `oracle_cards`, `rulings`, `oracle_tags`, `default_cards` | Catalog, rulings, tags, cheapest prices | **Built; loads only the sources named in `CATALOG_SOURCES`**, each only with a row marked read in the Source gate of `docs/compliance.md` | Scryfall's API terms read first-hand 2026-10-05; asking Scryfall about added value is still an open owner action (#62) |
| Wizards Comprehensive Rules | Rules text and search | **Not stored: read live from Wizards** (`vault/rules_live.py`, owner decision #142) | Nothing to load, so no gate row beyond the live-read rule |
| Commander Spellbook | Combos for a deck | **On demand, no ingestion** (built in M2, `find_combos`) | Code is MIT, but no data licence was found; so no copy of their data is stored |
| Cardmarket price guide | Prices | **Not used** | Their API is closed to new applications (help centre, 2026-10-07); the terms are behind a bot check and **have not been read** (owner step in "Shops" below) |
| Card Kingdom price list | Prices | **Not used** | `api.cardkingdom.com/api/pricelist` answers publicly, but the terms (read 2026-10-07) forbid robots and data extraction except search engines following robots.txt, and robots.txt disallows `/api/`: needs Card Kingdom's permission |
| Magic Madhouse product feed | Prices, stock | **Not used** | Offered to affiliate partners: a full product feed, Google Shopping format, four times a day (affiliate page, 2026-10-07). Its terms forbid copying or exploiting the site without written permission, "granted either directly or through a legitimate reselling programme". Joining is the owner's decision; no commission rate is published on the page |
| Wizards Commander Brackets and Game Changers list | The bracket hint in `deck_stats` (`vault/brackets.py`) | **Read once, thresholds kept in code** (2026-10-07, #171); the pages are re-checked every night (`tests/conformance`) | The brackets are Wizards' published rules, cited with links as sources of the computed hint; the Game Changers flag comes from Scryfall's card data |
| Archidekt | Public decks | **Used, read only**: one public deck when a person asks, credited with a link back | The Vault only reads, never writes, and does not crawl or search (see `compliance.md`) |
| Moxfield | Decks | **Not used for fetching** | Terms of Service read first-hand 2026-10-07: no "robot, spider or other automatic device" and no manual copying without written approval; no API policy is published (see `compliance.md`) |
| EDHREC | Aggregates | **Not used for fetching** | Terms do not permit automated access (see `compliance.md`) |

## Prices (#19)

- Built: the cheapest priced paper printing of every card, from the default-cards file the daily
  price job already downloads (`oracle_prices`, today only, source "scryfall"). Scryfall's prices
  come from TCGplayer and Cardmarket; they are shown as Scryfall's, with the date.
- Not built: Card Kingdom and Cardmarket prices. Re-open if a licence or permission is obtained.
  Each stored price row already carries its `source`, so adding a source later does not mix numbers.
- Built (#29): the price of every printing, for shopping lists under a person's rules (`oracle_printings`: set, language,
  and the nonfoil, foil and etched price of each priced paper printing; today only; the day is the `catalog_sources` row).
  It comes from the same default-cards file, in the same daily price job, and loads only when `oracle_printings` is named in
  `CATALOG_SOURCES`. It leaves out digital cards, tokens, emblems, art cards, oversized cards and gold-bordered
  memorabilia, so a list never picks one for being cheap. Scryfall has **no price per condition**, so a condition rule is kept
  and shown but changes no price; Scryfall prices few non-English printings, so a language rule often leaves a card with no
  qualifying printing, and the answer says so per line.
- Shopping lists are text the user pastes into the store's own list tool, in the syntax that tool reads (below). The Vault
  never fetches store pages, fills carts or scrapes, and never says which store is cheapest.

### Store paste formats (#29, #55; each read on the store's own help page, 2026-10-07)

| Format | The line the store's tool reads | Page read | What the page says it cannot carry |
|---|---|---|---|
| `cardkingdom` | `4 Ancient Den`, `4x Ancient Den` or `Ancient Den` (the three formats the Deck Builder shows) | <https://blog.cardkingdom.com/deck-builder-craft-your-next-deck/> (the three formats are in the image on that page) | No set, finish or condition: the printing chosen is reported beside the list |
| `tcgplayer` | `1 Lightning Bolt [SLD] 84`: quantity, name, set code in brackets, collector number; set and number optional (`1 Lightning Bolt [SLD]` = any art from that set) | <https://help.tcgplayer.com/hc/en-us/articles/360055768913-Getting-Started-With-Mass-Entry> | Finish and condition are Mass Entry preferences (printings, conditions: default Moderately Played and better), not part of a line. TCGplayer's own set-code list is the authority if a Scryfall code is not matched |
| `cardmarket` | `4 Dark Ritual`, `Tarmogoyf`, `2 Dark Confidant (Modern Masters)`, `4x High Tide (V.1) (Fallen Empires)`: the expansion's name in parentheses, an optional version number (not written by the Vault) | <https://help.cardmarket.com/en/how-to-add-a-mtg-decklist-to-wants> | The page names no place for finish, language or condition, and does not say whether set codes are accepted (names are written); Cardmarket reports lines it could not match |
| `plain`, `csv` | Quantity and name; a spreadsheet with the printing and the dated price | none (not a store) | |

The syntaxes were read from the pages, not by pasting a list into each store (that needs each store's own site, and the
Vault never contacts a store): the tool's text is written to the line shapes above and tested against them
(`tests/test_shopping.py`). A real paste into each store's tool is still worth doing once by a person; if a store
rejects a line shape, the fix is in `vault/shopping.py` (`STORES`, `render`) and this table.

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

## Shops: links, terms and price feeds (issue #80)

Re-read first-hand on **2026-10-07** (the earlier pass of 2026-10-05 is superseded). Each row says where each fact came
from and when. "Read" means the page text itself was read in a browser or by a tool that returned the page; second-hand
listings are marked as such. The Vault fetches no shop page and no shop feed (see "Prices" above).

| Shop | Link format (checked 2026-10-07) | Terms on automation and use of prices or stock | Feed available? | Saved search, alert, wishlist features for users |
|---|---|---|---|---|
| **Card Kingdom** | `https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=NAME`: opened with "Sol Ring", 141 results. The terms only forbid linking "in a manner that damages or exploits ... our reputation or suggests any form of association, approval, or endorsement" | Terms of Service, [cardkingdom.com/static/tos](https://www.cardkingdom.com/static/tos), "Last Updated" 8/7/2025 on the page, read 2026-10-07: forbidden is "any data mining, robots, or similar data gathering or extraction methods designed to scrape or extract data ... except in accordance with instructions contained in our robot.txt file and only to compile for search results" (public search engines may copy for "publicly available, searchable indices", "but not caches or archives"). [robots.txt](https://www.cardkingdom.com/robots.txt) (read 2026-10-07) disallows `/api/`, `/catalog/item/`, `/catalog/restock_notice`, `/cart/affiliate/`, `/myaccount/` | **No licensed feed.** A public price list answers at `api.cardkingdom.com/api/pricelist` (its header carries only `created_at` and `base_url`, no licence), but it is under `/api/`, which robots.txt disallows and the terms above cover. The site has no affiliate or feed programme page that we could find (footer, 2026-10-07); Card Kingdom has partner arrangements (its blog names content creators; deck sites link with a `partner=` parameter, e.g. `partner=archidekt`). Terms and cost of a feed: none published; ask | **Restock notice** per product: "our site has a restock notification feature which will send you an email notice when your selected item is restocked", sent to everyone on the list at once ([support article](https://cardkingdom.freshdesk.com/support/solutions/articles/3000037518-what-can-i-do-if-you-are-out-of-stock-), updated 2026-09-22, read 2026-10-07). **Wishlist** at `/myaccount/wishlist` (signed in; the page itself was not opened) |
| **Magic Madhouse** | `https://magicmadhouse.co.uk/search.php?search_query=NAME`: "Sol Ring" lists the printings. **The old format `https://magicmadhouse.co.uk/?q=NAME` does not search: it shows the home page** (found 2026-10-07; the skill link is corrected in the same change) | Terms and Conditions, [magicmadhouse.co.uk/terms-conditions](https://magicmadhouse.co.uk/terms-conditions/) (generated by iubenda; no date on the page except mobile terms of Feb 13, 2024), read 2026-10-07: "Users may not reproduce, duplicate, copy, sell, resell or exploit any portion of this Application and of its Service without the Owner's express prior written permission, granted either directly or through a legitimate reselling programme", and users "may not copy, download, share ... or create derivative works from the content". It does not mention robots or scraping by name; the written-permission rule covers use of the catalogue | **Yes, through the affiliate programme**, [magicmadhouse.co.uk/affiliate-program](https://magicmadhouse.co.uk/affiliate-program/), read 2026-10-07: "full product feed", Google Shopping format (CSV and XML), "updated 4 times per day", 30-day cookie, partners "in any vertical". Terms stated on that page: no paid advertising on their brand name or misspellings of it; nothing is paid on rejected or cancelled sales; apply by contacting the programme manager named on the page (the account is managed by Visualsoft, whose contact is on the page). **Cost: no fee is stated; commission is "a percentage of the product value" with no rate on the page.** Rates of 7% (singles), 7.5% (accessories) and 1% (boxes) appear on third-party affiliate listings (affi.io, FlexOffers): **second-hand, unverified**. No rule for how a feed may be used is on the page, so those terms must be asked for in writing before joining | **Wishlist**: "Add To Wishlist" on each product page and a "My Wishlist" link (a Swym wishlist; whether it needs a login was not tested). A product page with stock showed no restock-alert button; an out-of-stock page was not checked, so **restock alerts are not confirmed either way** |
| **Cardmarket** | `https://www.cardmarket.com/en/Magic/Products/Search?searchString=NAME` is the format the skills use. **Not re-checked: Cardmarket's pages answer a bot check** ("Just a moment..." in the browser pane; HTTP 403 to fetches on 2026-10-07), which was not bypassed | **Not read.** The General Terms and Conditions ([cardmarket.com/en/Magic/Policies/GeneralTermsAndConditions](https://www.cardmarket.com/en/Magic/Policies/GeneralTermsAndConditions)) are behind that check, so they could not be read by a tool. What could be read on the help centre (read 2026-10-07): [Cardmarket API](https://help.cardmarket.com/en/cardmarket-api): "Currently, we are not accepting applications for access to the Cardmarket API"; credentials may not be shared with third-party software; [Partner Apps and Services](https://help.cardmarket.com/en/api-partnerships) lists TCG PowerTools and **Scryfall** as business partners, which is how Cardmarket prices reach the Vault (shown as Scryfall's). **Owner step:** open the terms in a browser and record the clauses on automated access and price data here | **No.** The API is closed to new applications (above). No affiliate or feed programme page exists on the help centre (the address `help.cardmarket.com/en/AffiliateProgram` returns 404; a search of the help centre finds event sponsorship, coupons and store-partner pages only) | **Wants lists**: [help](https://help.cardmarket.com/en/wants-list), up to 100 lists per game and 150 entries per list; **Email Alarm** per wanted card: "to receive an email when a card matching your wanted card (including condition, language, etc.,) is listed", sent once, the first time ([help](https://help.cardmarket.com/en/add-cards-to-wants)); a Shopping Wizard and "Sellers With the Most Cards" built on a wants list ([help](https://help.cardmarket.com/en/shopping-features-from-wants)) |

What this means:

- **Now:** per-card search links only (as the skills do), no fetching of shop pages, prices stay Scryfall's and dated.
- **Card Kingdom:** no automated use of its site or price list without written permission; the Vault does not use them.
  Asking is the owner's decision (draft not written yet: add to `docs/outreach-drafts.md` if wanted).
- **Magic Madhouse:** the only shop with a documented, legitimate feed. Joining is the owner's decision. Before joining,
  ask in writing (a) the commission rate, (b) whether a free, non-commercial tool may show feed prices and stock with a
  link back, (c) how often it may be read. Affiliate income would make the Vault earn money from referrals: an owner
  decision against "the app stays free" (free to users is unaffected) and against the Fan Content Policy's terms on
  monetisation, which still have to be read before joining (`docs/compliance.md`).
- **Cardmarket:** not usable as a source and not read; the owner reads the terms in a browser (step above).
- **Alerts:** each shop already notifies people about the cards they want (Cardmarket email alarms, Card Kingdom restock
  notices, Madhouse wishlists). The Vault does not build its own price or stock alerts from shop data; the shopping list
  it prints is pasted into the shop's own wants or wishlist tool.

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
