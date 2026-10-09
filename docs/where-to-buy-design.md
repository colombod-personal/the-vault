# Where to buy: a menu for a missing card, regional shops and your local store (design for #212, epic #158)

Status: **draft for owner review.** Written 2026-10-09. Design only: no code, no shop was contacted by the Vault, no account setting exists yet. #212 is
`status:needs-refinement`, so this is the refinement the issue asks for (research and a design page), not an implementation.

Owner request (2026-10-06, in the issue): when a card is missing, besides owned alternatives, offer a compact **"Where to buy" menu** that sends the person to a
shop; make it **location aware** (a UK person gets Magic Madhouse); if possible point to **local official retailers**.

Marks used in this page, so a reader can tell what was seen from what is advice:
**Read** (date) = the page text was read on that date; **Measured** (2026-10-09) = a request or a click was made today and the result is quoted;
**Repo** = from this repository at `origin/main` 62d4c63; **Recommendation** = my advice, not a fact and not a decision; **Not verified** = could not be read or tried.

## 1. What exists and what limits it

- **Repo** (`docs/data-sources.md`, "Shops: links, terms and price feeds", #80, read 2026-10-07): the Vault only builds plain links. It never fetches shop pages, fills carts
  or scrapes, and says no shop is cheapest. Shopping lists are text the person pastes into a shop's own tool.
- **Repo**: `skills/archidekt-deck-helper/SKILL.md` already prints per-card search links for Card Kingdom, Cardmarket and Magic Madhouse, and `tests/test_skills.py` checks
  those three templates. There is no code that builds a link, no per-account setting, and no `country` or `locale` anywhere in `vault/` (searched on 2026-10-09).
- **Repo**: the Ideas view's missing-card **Buy** button opens the card's Scryfall page (`docs/deck-ideas-lab-design.md`, "As built (web)": "the Vault contacts no shop").
  This menu is what that button grows into.
- **Repo** (`docs/gdpr.md`): every per-person table must be in `vault.privacy.personal_data` (erasure), the export archive, the data map in `docs/gdpr.md` and
  `public/privacy.html`; `tests/test_privacy.py` fails if one is missing.

## 2. Terms of each target, read today

All pages below were read by me on **2026-10-09** unless a different date is shown. This is engineering diligence, not legal advice.

| Target | Link format (checked) | What the terms say about linking | What they say about using their content or data | What the Vault may do |
|---|---|---|---|---|
| **Magic Madhouse** (UK) | `https://magicmadhouse.co.uk/search.php?search_query=NAME`. **Measured:** HTTP 200, the result page contains "Sol Ring" (searched with `Sol+Ring`) | Terms and Conditions ([magicmadhouse.co.uk/terms-conditions/](https://magicmadhouse.co.uk/terms-conditions/), no date on the page): the clauses I found by searching for "link to", "linking", "robot", "scrap" and "derivative" contain nothing about links | Users may not "reproduce, duplicate, copy, sell, resell or exploit any portion" of the application without the owner's "express prior written permission, granted either directly or through a legitimate reselling programme"; and may not copy, download, publish or "create derivative works from the content" | A plain link that carries only the card name, opened by the person. Show none of its content: no price, stock, image or text. The affiliate feed is the licensed route (`docs/data-sources.md`, read 2026-10-07), not used |
| **Card Kingdom** (US) | `https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=NAME`. **Measured:** HTTP 200, the page lists "Sol Ring" | Terms of Service ([cardkingdom.com/static/tos](https://www.cardkingdom.com/static/tos)): forbidden is linking to the Products or Services "in a manner that damages or exploits, in our sole discretion, our reputation or suggests any form or association, approval, or endorsement" (sic) | Forbids "data mining, robots, or similar data gathering or extraction methods" except for public search engines following its `robot.txt`. (The "Last Updated" date, 8/7/2025, was recorded on 2026-10-07; today I re-read the clauses, not the date) | A plain link, named as the shop's, with a "not affiliated" line so nothing suggests endorsement. No fetching of its pages or `api.cardkingdom.com` |
| **Cardmarket** (EU) | `https://www.cardmarket.com/en/Magic/Products/Search?searchString=NAME`. **Checked in a browser on 2026-10-07** (`docs/data-sources.md`). **Not re-checked today: Cardmarket's pages answered a "Performing security verification" bot check to both a script and the browser pane, and I did not try to get past it** | General Terms and Conditions ([cardmarket.com/en/Magic/Policies/GeneralTermsAndConditions](https://www.cardmarket.com/en/Magic/Policies/GeneralTermsAndConditions)), version of 20/02/2026, **read 2026-10-07** (not today, see left): no sentence about links or crawling was found | Clause 9 (API): showing cards and prices needs Cardmarket's prior written agreement; clause 10: users may not publicly reproduce the platform's content except as the platform provides | A plain link, no content shown. Its prices appear in the Vault only as Scryfall's, dated. The owner is asked to read the terms (see decisions) |
| **Wizards Store & Event Locator** (official retailers) | `https://locator.wizards.com/search?query=PLACE&searchType=stores&distance=10`. **Measured:** I searched "Aldershot" with the page's own form: the address became `.../search?query=Aldershot&searchType=stores&sortBy=date&sortDirection=Asc&distance=10&page=1&pageSize=10`. A plain GET (no `sortBy`, `page`) returned the same two stores, and a GET with a postcode and `distance=25` returned a list. The form itself is a POST with a CSRF field, so the GET address is the one the results page shows, not the form's | Terms ([company.wizards.com/tou](https://company.wizards.com/tou), "Last Updated: December 10, 2025"), clause 2.2: you may not "data scrape" with robots or scripts, and may not "create derivative works ... including links or frames to content, images or artwork (except as expressly authorized by Wizards)". Clause 4 separately says Wizards' sites "may contain links to third party websites". **Whether clause 2.2(ii) reaches a plain hyperlink to the locator is not clear to me** | `robots.txt` (**Measured**) allows `/` and disallows `/api/` and `/auth/`. Copying its store list would be scraping and the issue forbids it | A hyperlink to the locator, opened by the person. Never fetch it, never copy or cache its stores. See decision 1 for the root-link fallback |
| **Scryfall** (card page) | `https://scryfall.com/card/...` (already used) | Guidelines at [scryfall.com/docs/api](https://scryfall.com/docs/api) concern data and images, not links | A card page carries Scryfall's own buy links; Scryfall's FAQ says it is supported by "buying cards through links on our site" (**Read**) | Already linked as the fallback row. Nothing new |

What Scryfall's own purchase links look like (**Measured**, the `purchase_uris` field for Sol Ring, 2026-10-09): `tcgplayer` is a `partner.tcgplayer.com` redirect with `subId1=api`,
`cardmarket` is `.../Products?idProduct=909814&referrer=scryfall&utm_...` and `cardhoarder` carries `affiliate_id=scryfall`. They are **Scryfall's affiliate links**.
The API documents the field as "an object providing URIs to this card's listing on major marketplaces" and `cardmarket_id` as the card's Cardmarket `idProduct`
(**Read** 2026-10-09, `scryfall.com/docs/api/cards`). Nothing in what I read says whether reusing those links is welcome, so using them would be a question to Scryfall,
and sending a person through Scryfall's referral would earn Scryfall money without saying so. Not proposed for v1.

Not read, so not offered: TCGplayer, Cardhoarder, Star City Games and any other shop. **Adding a shop is a three-step rule (Recommendation):** read its terms and date them
here; verify its search link format by hand and date it; add the template and its format test. A shop that fails any step is not in the menu.

## 3. Options

| Option | What it is | For | Against | Verdict |
|---|---|---|---|---|
| **A. Fixed list per region, the locator, and the person's own store** | A text menu ordered by the person's country; a "Find a store near me" link to Wizards' locator; one to three stores the person typed | Matches the issue; no new third party; no data fetched; all links verified in section 2; private by construction | Only as many shops as were read and verified (today three); a typed store has no search link unless the person pastes one | **Recommended** |
| B. Scryfall's `purchase_uris` | Use the marketplace links the card object carries | Product-precise (`idProduct`), no new shop terms | They are Scryfall's affiliate links; reuse is unread; the Vault would carry another's referral without saying so | Not for v1; the Scryfall page row already leads there |
| C. Affiliate links (Magic Madhouse programme, others) | The Vault earns on referrals | Could fund the Vault | The terms of that programme are unread beyond `docs/data-sources.md` (commission rate unpublished); the Fan Content Policy (**Read** 2026-10-09, last updated 2017-11-15) allows "sponsorships, ad revenue, and donations" if they do not interfere with access, and says nothing about affiliate links; the owner's rule is that the app stays free | Not in v1 (decision 3). If ever added: the menu says so on the row |
| D. Place from the IP address | Guess the country or town from the request | Zero setup | The issue forbids it; it is personal data; wrong behind a VPN or a mobile network | Rejected |
| E. Copy or cache the locator's store list, or build our own directory | Show stores without leaving the Vault | In-app | Scraping (Wizards clause 2.2(i) and `robots.txt`), stale data, and the issue says not to | Rejected |
| F. Link by Cardmarket product id instead of a name search | `.../Products?idProduct=<cardmarket_id>` from Scryfall's `cardmarket_id` | Lands on the card, not a result list | The Vault stores no `cardmarket_id` today (**Repo**: no match in `vault/` or `jobs/`); the link's behaviour could not be tested (bot check) | Possible later improvement, after the owner reads Cardmarket's terms and a person tests it |

## 4. The recommended design (option A)

### 4.1 The menu

A text-only dropdown on a missing card (Ideas view, Lab Buy rows, deck page missing rows). Each row is a link that opens in a new tab with
`rel="noopener noreferrer"`. No images, no prices, no stock, no "best" or "cheapest" (a real best price needs a licensed feed, #84). Order, for a person whose country is set:

1. **My store**, if the person typed one (section 4.3).
2. The country's shops (section 4.2), in the owner's order.
3. **Find a store near me**: the Wizards locator (section 4.4).
4. **More shops**: the shops of other regions, collapsed.
5. **Scryfall card page** (its own buy links; credit and provenance as today).

Footer line, always: "Plain links to each shop's own search. The Vault does not contact these shops, show their prices, or earn from your clicks. Not affiliated with any of them."
(The last clause is true only while option C is off; the sentence changes with the decision.)

### 4.2 Shops by country (Recommendation; the owner decides, decision 2)

| Account country | Shops in order | Basis |
|---|---|---|
| GB | Magic Madhouse, Cardmarket, then "More shops" | the issue's example; both links verified or read as in section 2 |
| US | Card Kingdom, Cardmarket | Card Kingdom link verified today; Cardmarket read 2026-10-07 |
| A country of the EU, EEA or Switzerland | Cardmarket, then "More shops" | Cardmarket is a European marketplace; its terms were read 2026-10-07 |
| Any other, or not set | Cardmarket, Card Kingdom (with no promise that either ships to you) | the Vault does not know where they ship; it says so in the empty state |

Nothing here claims a shop ships to a country. Today's list is three shops; the menu should say so, not pretend a market it has not verified.

### 4.3 My store (the person's own typing)

The issue wants a person's local shop on top of the menu ("for example the Aldershot Game Shop"). Design: up to **three** entries per account, each with

- **Name**, at most 80 characters, shown as typed.
- **Web address**, `https://` only, at most 300 characters, no embedded username or password; the menu shows its host name next to the name so the person sees where it leads.
- **Search address** (optional), at most 300 characters, `https://`, must contain the placeholder `{card}`; the person pastes it from their store's own search page. Without it, the row opens the store's web address.

Why the search address is optional and often absent: on 2026-10-09 the locator's own listing for a real Aldershot shop gave a Facebook group as its web address (**Measured**), which
has no card search. Most local stores will be a page, not a search. The row says which: "My store: <name> (opens its page)" or "(searches for the card)".

Validation: `https` scheme only (never `http:`, `javascript:`, `data:`), a host that contains a dot, no credentials, length limits, placeholder replaced with the percent-encoded card name; the
server stores the text and never fetches it (a test makes sure of it). Shown with a "typed by you" marker so it is never mistaken for a verified shop.

### 4.4 Find a store near me

Two parts, as the issue says, neither needs the Vault to know where the person is.

- **The link.** A menu row "Find a store near me (Wizards' official locator)". With the person's typed place it opens
  `https://locator.wizards.com/search?query=<place>&searchType=stores&distance=25`; without it, `https://locator.wizards.com/`. (**Measured** format; see section 2 for the terms question.)
- **The place** is typed into a small box in the menu (city, address or postcode) and used to build the link **in the browser**, so the server never receives it. It is not stored: the box is empty
  the next time. (**Recommendation**: keeping it, as a setting, is a later choice with its own privacy line; nothing in v1 needs it.) The menu says that the place you type is sent to Wizards' locator when you open it.

### 4.5 Region, never from the IP address

- **The setting** "Where I buy" is a country chosen by the person in their account (section 5).
- **Before it is set**, the menu orders itself by the region in the browser's language (for example `en-GB` gives GB), computed in the browser, not sent to the server, not stored, and says so: "Ordered for GB from your browser language. [Set where you buy]".
  A language without a region (`en`) gives the neutral order of the last row of section 4.2. Nothing ever reads the request's IP address for this (a test fails if it does).

### 4.6 Link templates and the format test

One module builds every link from a template and a name; nothing else in the code concatenates a shop URL. Templates (**Repo** for the first three; the locator's is **Measured** above):

| Id | Template | Example |
|---|---|---|
| `magicmadhouse` | `https://magicmadhouse.co.uk/search.php?search_query={q}` | `.../search.php?search_query=Sol+Ring` |
| `cardkingdom` | `https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D={q}` | `...filter%5Bname%5D=Sol+Ring` |
| `cardmarket` | `https://www.cardmarket.com/en/Magic/Products/Search?searchString={q}` | `...searchString=Sol+Ring` |
| `wizards-locator` | `https://locator.wizards.com/search?query={q}&searchType=stores&distance={miles}` | `...query=GU11+1DZ&searchType=stores&distance=25` |
| `scryfall` | the card's Scryfall page (as today) | |

`{q}` is `quote_plus` of the name (spaces become `+`, apostrophes and commas percent-encoded), as `tests/test_skills.py` already expects. A format test per template covers: a plain name, an
apostrophe ("Teferi's Protection"), a comma ("Mondrak, Glory Dominus"), a non-ASCII letter, and a two-faced name. For a card whose name contains ` // `, the link uses **the front face only**
(**Recommendation**; **Not verified** that each shop's search treats a full `A // B` name differently: a person should try one real double-faced card on each shop once, and the result be written in section 2). "Nothing contacts a shop" already has a guard (**Repo**, merged 2026-10-09 as #390): `tests/test_no_shop_calls.py` pins that the twin universe has no twin for a shop host, so any call to one is recorded as an
escape that fails every test asserting `universe.escapes`. The build adds `locator.wizards.com` and the typed-store case (a stored address is never requested) to that list, and a test that builds the menu
with a stored store and asserts no escape. No scheduled job may contact a shop (the same rule as Archidekt's in `docs/compliance.md`); re-checking a link format is done by a person, with the date written in section 2.

## 5. Where the settings live, and the privacy that goes with them

**What is stored (Recommendation):** a new table `buy_settings`, one row per person:

| Column | Type | Meaning |
|---|---|---|
| `user_id` | integer, primary key, references `users`, deleted with the account | |
| `country` | two letters (ISO 3166-1 alpha-2) or null | "Where I buy" |
| `stores` | list of at most three `{name, url, search_url}` | typed by the person (section 4.3) |
| `updated_at` | timestamp | |

Nothing else: no postcode, no address, no coordinates, no IP address, no device language. A country is one of about 250 values, and the stores are what the person typed; the Vault asks for nothing more.
It is **personal data** (it can say roughly where a person lives), so:

- `vault.privacy.personal_data`: a `buy_settings` delete before `users` (a test fails otherwise).
- Export: `buy_settings.json` in the archive, listed in its `README.txt`.
- `docs/gdpr.md` data map: one row (table, contents, export file, erasure).
- `public/privacy.html`: one plain-language paragraph (what is stored, why, how to remove it, that the place typed in the locator box is not stored). The wording uses no personal details of the
  project's owner and needs none from them (the privacy page's contact placeholders are a separate issue and are not a blocker here).
- `public/credits.html`: a line that the menu links to shops and the Wizards locator and that the Vault does not call them.
- Because it changes what the Vault stores, a migration (Alembic) and `tests/test_schema_migrations.py` apply.

**API and agents (Recommendation):** `GET` and `PUT /api/v1/me/buy-settings` (own account only, 404 for anything else, HAL links, a response model, idempotency key on `PUT`), cursor paging not needed (at most three
entries). Because an endpoint an agent could use also gets an MCP tool (`CLAUDE.md`), a read-only **`where_to_buy(card)`** returning the same ordered links from the person's settings, classified with provenance that says "plain links, no prices". Writing the
settings from an assistant is **not** proposed (a setting that reveals where a person lives should be changed by the person in the app).

**Rate and abuse:** the typed web address is only ever rendered as a link, never fetched, never embedded or framed, and never used as a redirect target on the Vault's own domain.

## 6. Mockups (text; the two layouts of the issue)

`[...]` is a control. Illustrative data. Shop rows are plain text.

### 6.1 The menu, country set to GB, one typed store (1400 px, then 390 px)

```
1400 px
Hunter Sliver  missing from Sliver Swarm                 [Buy v]
                                                          +-----------------------------------------------+
                                                          | My store: The Games Shop (opens its page)     |
                                                          | Magic Madhouse                                |
                                                          | Cardmarket                                    |
                                                          | Find a store near me (Wizards' locator)       |
                                                          |   Place: [ Aldershot            ] [Open]      |
                                                          | More shops >                                  |
                                                          | Scryfall card page                            |
                                                          |-----------------------------------------------|
                                                          | Plain links to each shop's own search. The    |
                                                          | Vault does not contact them or show prices.   |
                                                          | Not affiliated.            [Where I buy: GB]  |
                                                          +-----------------------------------------------+
390 px
Hunter Sliver   [Buy v]
+------------------------------+
| My store: The Games Shop     |
|   (opens its page)           |
| Magic Madhouse               |
| Cardmarket                   |
| Near me (Wizards' locator)   |
|   Place [ Aldershot   ][Open]|
| More shops >                 |
| Scryfall card page           |
| Plain links, no prices.      |
| Not affiliated. [Where: GB]  |
+------------------------------+
```

### 6.2 The empty state: no country set

```
1400 px
[Buy v]
+-----------------------------------------------------------+
| Ordered for GB from your browser language.                |
| [Set where you buy]  (nothing is stored until you save)   |
|-----------------------------------------------------------|
| Magic Madhouse                                            |
| Cardmarket                                                |
| Find a store near me (Wizards' locator)                   |
| More shops >      Scryfall card page                      |
+-----------------------------------------------------------+
(a browser language with no region, such as "en": "Pick where you buy to see the right shops first." and the neutral order)

390 px
[Buy v]
+------------------------------+
| Ordered for GB from your     |
| browser. [Set where you buy] |
| Magic Madhouse               |
| Cardmarket                   |
| Near me (Wizards' locator)   |
| More shops >  Scryfall page  |
+------------------------------+
```

### 6.3 The account setting

```
Account > Where I buy
Country        [ United Kingdom v ]                       (or "Not set")
My stores (up to 3, typed by you; stored with your account, exported and erased with it)
  Name         [ The Games Shop                    ]
  Web address  [ https://...                       ]  https only
  Search address (optional) [ https://.../search?q={card} ]  paste from the shop's own search page; {card} stands for the card name
  [Add another]   [Save]   [Remove all]
The place you type in the locator box is never stored.
```

At 390 px the same fields stack, one per line, targets 44 px, no horizontal scroll (checked like the Ideas view).

## 7. What the criteria of #212 look like after this page

| Criterion | State |
|---|---|
| Owner decisions recorded: shop list per country; whether Cardmarket's terms allow the link (owner reads them); affiliate or not | **Open, owner.** Questions and recommended answers in section 8 |
| Verified, with the date: the locator's link format and terms; each shop's search-link format | Locator format **Measured** 2026-10-09 and its terms read (with one unclear clause); Magic Madhouse and Card Kingdom formats **Measured** 2026-10-09; Cardmarket's format and terms from 2026-10-07 only (bot check today). Done as far as it can be without the owner or a browser that passes Cardmarket's check |
| Design in a docs page: menu mockup at 1400 and 390 px (text only), the account setting, the empty state | This page, sections 4 to 6 |
| Every link template is covered by a format test; nothing contacts a shop | **Specified** (section 4.6), not written (no code) |
| Privacy: no IP geolocation; the new account fields are in the export and the erasure; the privacy notice is updated | **Specified** (sections 4.5 and 5), not built |
| Light by default: no images in the menu | Specified (section 4.1: text only) |

## 8. What the owner must decide

Each with a recommended default, so "as recommended" is a complete answer.

1. **May the menu link to Wizards' locator with the person's place in the address?** The terms' clause 2.2(ii) (links or frames) is unclear to me. Recommendation: link with the place typed by the person (it is one hyperlink to a public search page the person asked for; the locator itself
   links out to each store's own page) **if you are comfortable with that reading**; the safe fallback is the root link `https://locator.wizards.com/` with no place, and it costs one sentence in the menu ("type your town there").
2. **The shop list per country** (section 4.2). Recommendation: as in that table; and say which further shops you want, each of which needs the three steps of section 2 before it is added.
3. **Affiliate or not.** Recommendation: **no** in v1 (plain links; the footer says so). If you later join the Magic Madhouse programme: read its terms, add a disclosure on the row, and re-read the Fan Content Policy's rule on sponsors (it asks that they not be Wizards' competitors).
4. **Cardmarket.** Read its General Terms (the link is in section 2) and say whether a plain search link is acceptable. Recommendation: acceptable, since nothing from Cardmarket is shown or fetched and the terms I read on 2026-10-07 do not mention links; but the terms are yours to read.
5. **One to three typed stores, https only, and the optional search address with `{card}`.** Recommendation: yes as designed.
6. **An assistant tool `where_to_buy` (read only), but no tool that writes the settings.** Recommendation: yes.
7. **Is "no price, no stock, no best price" still what you want, since a real price needs a licensed feed (#84)?** Recommendation: yes for this feature.

## 9. Tasks that follow (not started)

1. Owner decisions above. 2. The `buy_links` module with the templates and their format tests (and the "no network" test). 3. Migration, `buy_settings`, erasure, export, `docs/gdpr.md`,
`public/privacy.html`, `public/credits.html`. 4. REST, the account panel and the `where_to_buy` tool (`docs/ai-parity.md`, `public/llms.txt`). 5. The menu in the Ideas view, the Lab and the deck page,
with screenshots at 1400 and 390 px. 6. One real click through each shop link by a person, dated in section 2 (and Cardmarket's in a browser that passes its check).
