# Compliance and provenance

The Vault depends on other people's data and says so, always. Two rules sit above everything else:

1. **Show provenance. Always.** Every card, rule, ruling, tag, price and deck we return or display
   says where it came from and as of when.
2. **Never make anything look like ours.** Source material is labelled as the source's. What the
   Vault computes (a legality check, a budget, a deck count) is labelled as computed by the Vault, from
   which inputs. The Vault is not produced or endorsed by Scryfall, Wizards, Moxfield or Archidekt.

Status of this document: written 2026-10-04 from public pages that were reachable then; Scryfall's terms read first-hand on 2026-10-05;
Moxfield's terms, Card Kingdom's, Magic Madhouse's and Archidekt's read first-hand on 2026-10-07 and Cardmarket's General
Terms (version of 20/02/2026) read in a browser on 2026-10-07, see `docs/data-sources.md`; 17Lands' public data sets page, terms of
service, usage guidelines, FAQ, metrics definitions, `robots.txt` and the CC BY 4.0 legal code read first-hand on 2026-10-09 (see "17Lands" below). Anything marked unverified may not be relied on
until someone has read the primary text. This is engineering diligence, not legal advice.

## Source gate (enforced)

A catalog source is loaded only when its terms have been read first-hand and the row below says so. The rule is a test,
not a promise: `tests/test_compliance_gate.py` fails when the code can load a source (`jobs/sync_catalog.py` `SOURCES`,
`OTHER_JOBS`, `vault/provenance.py` `CATALOG_SOURCES`) that has no row here with Gate `read`, a date and a terms URL, and
when a new `jobs/sync_*.py` job or `sync-*.yml` workflow appears without this table being looked at. What the test cannot
see is the GitHub repository variable `CATALOG_SOURCES` (production); the job itself refuses names outside its own
list, so production can only enable sources the test has checked.

| Key | Source | Gate | Read on | Terms | What the terms mean for us |
|---|---|---|---|---|---|
| `oracle_cards` | Scryfall bulk `oracle_cards` | read | 2026-10-05 | https://scryfall.com/docs/api | Free for "creating additional Magic software"; must add value, no proxying; accurate User-Agent; credit |
| `rulings` | Scryfall bulk `rulings` | read | 2026-10-05 | https://scryfall.com/docs/api | As above; rulings are Wizards' text and are shown as such |
| `oracle_tags` | Scryfall Tagger tags | read | 2026-10-05 | https://scryfall.com/docs/api/tags | Track by id, be able to hide tags (`HIDDEN_TAGS`), labelled community opinion |
| `oracle_prices` | Scryfall prices (TCGplayer, Cardmarket via Scryfall) | read | 2026-10-05 | https://scryfall.com/docs/api | Shown as Scryfall's, with date and marketplace |
| `oracle_printings` | Scryfall bulk `default_cards`: one priced paper printing per row | read | 2026-10-05 | https://scryfall.com/docs/api | Same terms as the prices it carries: shown as Scryfall's with date and marketplace; used to pick the cheapest acceptable printing, never a shop's price; off until `CATALOG_SOURCES` lists it |
| `limited_17lands` | 17Lands public data sets (game and draft files of recent sets): reduced to per-card counts, the files themselves never stored | read | 2026-10-09 | https://www.17lands.com/public_datasets | CC BY 4.0: credit 17Lands, name the licence and link it, say what the Vault changed, no endorsement; every figure shown with its sample size and as Arena data (see "17Lands" below). Off until `CATALOG_SOURCES` names it; read by `jobs/sync_limited.py`, run only by the `sync-limited` Action |
| `rules` | Wizards' Comprehensive Rules | read | 2026-10-04 | https://company.wizards.com/en/legal/fancontentpolicy | Read live, nothing stored (#142); Fan Content notice shown |

Sources the Vault does **not** load, recorded so the next person does not have to find out again:

| Key | Source | Gate | Read on | Terms | Verdict |
|---|---|---|---|---|---|
| `moxfield` | Moxfield decks | read: automation forbidden | 2026-10-07 | https://moxfield.com/help/terms | Not used for fetching; see "Moxfield" below |
| `archidekt` | Archidekt public decks | read: not clear, owner position recorded | 2026-10-07 | https://archidekt.com/terms | One public deck per person's request only; see "Archidekt" below |
| `cardkingdom` | Card Kingdom site and price list | read: automation forbidden | 2026-10-07 | https://www.cardkingdom.com/static/tos | Not used; ask first (`docs/data-sources.md`) |
| `magicmadhouse` | Magic Madhouse site and affiliate feed | read: written permission needed | 2026-10-07 | https://magicmadhouse.co.uk/terms-conditions/ | Not used; the affiliate feed is the legitimate route (`docs/data-sources.md`) |
| `cardmarket` | Cardmarket site | read | 2026-10-07 | https://www.cardmarket.com/en/Magic/Policies/GeneralTermsAndConditions | Not used: showing its cards and prices needs its prior written agreement (clause 9); its prices reach the Vault only as Scryfall's |
| `commander_spellbook` | Commander Spellbook API | not read: no data licence found | n/a | https://commanderspellbook.com | On demand per request, nothing stored |

The questions we planned to put to the sources' owners are **not part of this test** and are still open owner actions:
see "Open with the owner" below. Until the owner decides otherwise, an unanswered question means "not allowed yet" for
anything beyond what the read terms already permit.

## Requirements by source

### Wizards of the Coast (Fan Content Policy)

Read directly from [company.wizards.com/en/legal/fancontentpolicy](https://company.wizards.com/en/legal/fancontentpolicy)
on 2026-10-04:

| Requirement | Wording (short) | What it means for us |
|---|---|---|
| Notice | "[Title] is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC." | Keep it in the footer (exists); also in MCP server instructions, plugin and skill docs, and in every MCP Apps UI |
| Free access | "You can't require payments, surveys, downloads, subscriptions, or email registration to access your Fan Content" | **Risk.** The Vault needs sign-in. See "Registration" below |
| No verbatim reposting | Fan Content "does not include the verbatim copying and reposting of Wizards' IP" | **Risk** for the Comprehensive Rules, rulings and card text served verbatim by tools. See "Wizards' text" below |
| No logos or trademarks | Cannot use Wizards logos, trademarks or patents | Keep as is; no Wizards logos in the UI or plugin |
| Takedown | Wizards may remove content it deems inappropriate | Keep a way to switch tools off quickly |

The policy does not address software tools or rules text specifically.

### Scryfall (API terms)

Read directly from [scryfall.com/docs/api](https://scryfall.com/docs/api) and
[scryfall.com/docs/api/tags](https://scryfall.com/docs/api/tags) in a browser on **2026-10-05** (the scripted fetch
on 2026-10-04 was refused with 403). Scryfall provides its data and images "free of charge for the primary purpose of
creating additional Magic software", under the Fan Content Policy, with these guidelines:

| Requirement (Scryfall's wording, shortened) | What it means for us | Status |
|---|---|---|
| No Scryfall logos; do not imply Scryfall endorses you | "Not produced or endorsed by Scryfall" in the footer, MCP instructions and plugin | done |
| No paywall: no payments, surveys, subscriptions, ratings, chat servers or follows in exchange for the data. "If you have an account system, end-users should be able to access card data anonymously or with free accounts." | The Vault is free; free accounts are explicitly fine. No anonymous access (see Decisions) | done |
| Do not use the data to create new games or imply it is from another game | Magic only | done |
| "You may not simply repackage, republish, or proxy Scryfall data. Your software must create additional value for end-users." | Tools answer for the person's collection and decks, check legality and budgets, verify citations, simulate curves; card lookups serve that grounding. No bulk dumps or raw search proxy | done; keep in review |
| `User-Agent` (accurate, the app's name) and `Accept` on every API request | `jobs/sync_prices.py`, `jobs/sync_catalog.py` | done |
| Rate limits per endpoint; bulk files for large data | Daily bulk downloads, one rate-limited client | done |
| Images: do not crop the copyright or artist, distort, recolour, watermark or misattribute; with `art_crop`, show artist and copyright in the same view | Applies to the web app and MCP Apps views | done; keep in review |
| Tags (Tagger, community-maintained, moderated): track tags by `id`, not slug; be able to **temporarily hide individual tags** | `HIDDEN_TAGS` setting (ids or slugs) removes a tag and its children from roles; weights read as `very_strong` > `strong` > `median` > `weak` | done (2026-10-05) |
| Rulings and Oracle tags | Offered as bulk files for this purpose; shown with provenance | done |
| Live printing search (#208) | When a person adds a card without saying which printing, the server asks Scryfall once (`/cards/search?q=oracleid:<id>&unique=prints&order=released`, one page, through the same single rate-limited client with its `User-Agent` and `Accept`) and keeps the answer in memory for five minutes. **Nothing from that search is stored**: a printing becomes a stored card only when the person picks it and the change is applied (the existing lookup by set and number). **Image bytes never pass through the Vault**: the answer carries Scryfall's own `cards.scryfall.io` URLs (anything else is dropped), the artist and a Scryfall provenance block, and the view loads the pictures straight from Scryfall. If Scryfall does not answer, the answer says so and offers what the Vault already holds | done (`tests/test_live_printings.py`) |

No email to Scryfall is needed to load the card data on these terms.

### Moxfield

Read first-hand on **2026-10-07**: [moxfield.com/help/terms](https://moxfield.com/help/terms) ("Effective Date: July 9, 2026";
the old address `moxfield.com/terms` is a 404, and the page is rendered by the app, so it was read from the app's own
script bundle). The clauses that matter:

- Licence: "you may access and use the Site for your non-commercial personal use, and only as expressly permitted in these Terms."
- Automation: you may not "(5) use any robot, spider or other automatic device, process or means to access the Site for any
  purpose, including monitoring or copying any information or material on the Site, except as expressly approved by Moxfield;
  or (6) use any manual process to monitor or copy any of the material on the Site or for any other unauthorized purpose
  without Moxfield's prior, written consent."
- Competitors need written consent to use the Services at all.
- Contact for questions: support@moxfield.com (section 17).

**API policy:** none is published. The terms do not mention an API; no developer or API page was found on the
site or by web search on 2026-10-07 (community libraries exist, unofficial). The statement we had before, that
Moxfield asks legitimate developers to get in touch, is still second-hand (developer discussion) and unverified.

Rules for us: no automated fetching of Moxfield decks (the terms forbid it without Moxfield's approval).
The user's own CSV or pasted list is fine. Any Moxfield-derived deck is labelled "Deck from Moxfield", with its author and a
link. Moxfield must be asked before anything automated is built (draft in `docs/outreach-drafts.md`, **not sent**: owner).
`tests/test_deck_import.py` rejects Moxfield links, so nothing is fetched in the meantime.

### Archidekt

**No scheduled or automated job contacts Archidekt, and nothing searches it** (owner rule; found broken by the audit of
#132 on 2026-10-07: the nightly conformance run searched Archidekt for decks and fetched the top one). Live checks of
the twin against Archidekt run **by hand only** (`TWINS_LIVE_ARCHIDEKT=1`), fetch **one named public deck** (the
owner's) and never search; `tests/test_workflows.py::test_no_scheduled_job_contacts_archidekt` enforces it.

**The Vault only reads from Archidekt** (owner, 2026-10-05, issue #79). It never writes to Archidekt, never signs
in to anyone's Archidekt account, and never "syncs" a deck. When the Vault suggests changes, the person applies
them on Archidekt themselves; the Vault gives the change list and the buying list.

Sources (re-read 2026-10-05):

- **Terms:** [archidekt.com/terms](https://archidekt.com/terms), last updated 2018-09-07. The licence is personal and
  non-commercial and excludes software that "generates automated searches, requests, or queries", building a similar
  or competitive site, harvesting other users' information, and reverse engineering. The terms do not mention an API.
  The owner's position is that the Vault is within them, because it is a personal, non-commercial tool that makes
  one request when a person points it at a public deck (no searching, crawling or background requests), credits
  Archidekt and the author, harvests nothing, builds no competing site and reverse engineers nothing.
- **Reading:** an Archidekt developer said on Archidekt's forum that the API is "open and public (as far as reading
  is concerned)" ([thread 40353](https://archidekt.com/forum/thread/40353)) and "You're more than welcome to use our
  API for whatever you want", asking only that it not be hammered
  ([thread 2832338](https://archidekt.com/forum/thread/2832338)). In early 2026 the same developer wrote "I believe we
  start rate limiting people at 40 requests per minute" ([thread 19112643](https://archidekt.com/forum/thread/19112643)):
  the cache (#133) cuts repeat reads of the same deck, but a cache miss is fetched through one throttle for the whole process (one read a second, #353) and each person is limited to 30 Archidekt calls a minute; the logged counts show whether a tighter cap is needed.
- **Writing:** there is no API for other apps to change a person's deck, so the Vault does not write.

What the Vault does:

- Reads **one public deck per request a person makes** (opening a link, the deck page, the graph overlay,
  or the `get_archidekt_deck` tool), server-side, with the toolkit's User-Agent naming the project.
  Private decks are not read (Archidekt answers 404 without sign-in).
- **A cache, and no rate cap yet, by design (#133, 2026-10-06).** A read of a public deck is kept in `archidekt_deck_cache`
  (the deck's public JSON by id, no person's id) and served for 10 minutes; Refresh asks Archidekt again unless the copy
  is under a minute old; entries older than 7 days are deleted. Every answer carries `vault_cache` (from cache or not,
  fetched_at, age in seconds). Each read logs `archidekt deck cache=miss|hit calls_last_minute=N hits_last_minute=M hit_rate=R` (this process's last minute; counts only, no deck id or person; each Vercel instance counts its own, so the real rate is read by adding the instances' lines in the logs; `vault/archidekt_cache.py::Rate`, `tests/test_archidekt_rate.py`). A cap is added only if the counts
  show it is needed, and then only behind this cache: an over-limit read gets the cached copy instead of an error.
- No background jobs, crawling or deck search; nothing is fetched without a person asking. Decks are stored only
  when a person presses Save: their copy of that one deck's list, link and author's public username (for the
  credit), in their account, removable any time.
- Every Archidekt deck is labelled "Deck list from Archidekt", with its author and a link back.

Code review that nothing writes to Archidekt, with a guard test: #132.

**`search_decks` (the toolkit library's search) is one page on a person's request, and the Vault does not use it at all
(#132, 2026-10-07).** `mtg_toolkits.archidekt.ArchidektClient.search_decks(...)` is a generator that **follows `next`
across every page of results**, so called without `limit` it crawls Archidekt page after page. The rule: it is never
called from a job, a loop, a scheduled run or an agent tool; the Vault's only Archidekt call is `get_deck` for one named
public deck. If #94 (a person's own public decks) ever needs a list, it may call a search **once, on that person's
request, with `limit` no larger than one page, never inside a loop**, and the test below must be changed together with
this paragraph and shown to the owner. `tests/test_archidekt_readonly.py::test_the_vault_never_calls_search_decks_from_a_job_or_a_loop`
fails if `search_decks` appears in `vault/`, `jobs/`, `api/` or `scripts/`, or if any job imports the Archidekt client.

Planned (#94): listing a person's **own** public decks by their Archidekt username, as one read on that person's
request, paced and cached. It is not built. Today's support is the single deck a person gives or has saved, and the
`archidekt-deck-helper` skill (and its generated plugin copy) tells agents to fetch only that one deck; they will need
updating together with #94.

### 17Lands (public data sets, CC BY 4.0; read 2026-10-09)

Design and evidence: `docs/limited-data-design.md` (what was read, the licence clauses, what is not verified). Read in a browser on 2026-10-09:
[the public datasets page](https://www.17lands.com/public_datasets) ("Unless otherwise noted, these data sets are licensed under a Creative
Commons Attribution 4.0 International License"), the terms of service, the usage guidelines, the FAQ, the metrics definitions, `robots.txt`
(disallows only `/card_data/details` and `/data/card_based_performance`) and the [CC BY 4.0 legal code](https://creativecommons.org/licenses/by/4.0/legalcode.en).
The files live on `17lands-public.s3.amazonaws.com`. The job asks that host only for the game and draft files of the sets it is given.

| Requirement | Where it comes from | What it means for us |
|---|---|---|
| Credit the creator, name the licence and link it, link the material | CC BY 4.0 section 3(a)(1) | The `attribution` string and the `provenance` source block of every answer with these figures, `public/credits.html`, `whoami` |
| Say that the material was changed | section 3(a)(1)(B) | "Changed by the Vault: the per-game and per-pick rows were reduced to per-card counts, and the percentages, intervals and sample-size warnings were computed by the Vault, so they can differ from the figures on 17lands.com" (`changes` in the source block) |
| The warranty notice | section 3(a)(1)(A)(iv) | "which also states that it is provided without warranty (section 5)" |
| No implied endorsement | section 2(a)(6); usage guidelines | "Not produced or endorsed by 17Lands." Never "17Lands says"; "According to data from 17Lands (set, format, date)" is their own suggested wording |
| Removal on request | section 3(a)(3) | If 17Lands asks, the data is switched off at once (remove `limited_17lands` from `CATALOG_SOURCES`), as for a Wizards takedown |
| Be kind to a volunteer-run host (their guidelines discourage scraping the API and welcome the data dumps) | usage guidelines, FAQ | One download at a time, descriptive User-Agent, no file over 400 MB, 800 MB a run, three retries only for connection errors, 429 and 5xx; the replay files are never touched; a version already loaded is not read again (ETag) |

The credit text is in `vault/provenance.py` (`GENERIC_17LANDS_NOTICE`) and `vault/limited_stats.py` (`ATTRIBUTION`, with the set, format and
dates filled in); `tests/test_limited_tool.py` and `tests/test_limited_credits.py` pin it, and that the credits page, the `vault-attribution` skill,
the Limited expert and the server instructions carry it. What the Vault must not claim: that 17Lands endorses or produced it; that a figure *is*
17Lands' number (the percentages are the Vault's, labelled `computed`); a "Vault rating", grade or tier; that a card is a best pick or that a gap is real
below the sample floor (200 games in hand to show a number flagged, 1,000 to be called `ok`); that the data describes paper Magic or all players.
The terms were read on 2026-10-09: re-read them at least every 90 days and whenever 17Lands changes them (the reminder issue and the job's
refusal after 120 days are slice 2 of #178).

### Other sources (read 2026-10-07)

Shops are in `docs/data-sources.md` with the source URL and date per row: Card Kingdom (terms forbid robots and data
extraction; robots.txt disallows `/api/`), Magic Madhouse (copying or exploiting needs written permission "granted either
directly or through a legitimate reselling programme"; the affiliate programme offers a full product feed, no rate
published), Cardmarket (General Terms read 2026-10-07: showing its cards and prices needs its prior written agreement, clause 9; API closed to new applications).

Still unread: Commander Spellbook's data licence (none found; on demand only, nothing stored) and the **Fan Content Policy's
rules on monetisation**, which must be read before the owner joins any affiliate programme. Neon and Vercel limits are in
`catalog-design.md`.

## The two real risks

### 1. Registration

Wizards' policy says we cannot require email registration to access Fan Content. The Vault requires
sign-in (OAuth providers, which give us an email, or a passkey, which does not). Scryfall explicitly
allows free accounts. Today the README treats sign-in as protecting private collections, not
content, and the public pages stay open. The new design puts rules, card text and rulings behind
tokens, which is closer to the line.

**Superseded (owner, 2026-10-06, #62): see Decisions. Every feature needs a free account and there is no anonymous catalog;
the options below are the analysis that led to that decision, kept for the record.**

Options, in order of safety:

1. Keep tools that return Wizards' text available **without an account**, tightly rate-limited,
   read-only, with provenance, and keep the account only for the person's own data (collection,
   decks, sharing).
2. Keep them behind free accounts and ask Wizards to confirm in writing.
3. Do nothing and accept the risk (not recommended).

Recommendation: option 1 for a small public set (card by name, a single rule, a card's rulings),
and an open question to Scryfall about proxying (below). It also makes onboarding easier: the first
useful answer needs no sign-in.

### 2. Wizards' text and "no proxying"

Rules, rulings and Oracle text are Wizards' material. Serving them verbatim in bulk looks like
"verbatim copying and reposting" (Wizards) and "repackaging" (Scryfall). Design constraints:

- No endpoint or tool returns the whole rules document, all rulings, or the whole catalog; results
  are the specific rule(s) or card(s) asked about, capped, and paged.
- Every result links to the official source (rules document, Scryfall card page) and carries the
  notice.
- Tools add value on top: citations checked against the source, legality and budget validation,
  comparison with the user's collection. A pure "dump" tool is not built.
- Ask both parties. Ask Wizards for permission to use the Comprehensive Rules text in a free tool, and
  ask Scryfall whether the planned tools (rulings, tags, search) count as acceptable added value.
  Gate #16 (rules ingestion) on the answer or on a decision to link and excerpt only.

## Provenance rules (implementation)

Every MCP tool result, API response and UI element that carries third-party data includes `provenance`,
with no exceptions. It is a list of blocks (one per source, or one `computed` block listing its inputs); a
block looks like this:

```json
{
  "provenance": {
    "kind": "source",
    "source": "Scryfall",
    "origin": "Wizards of the Coast (rulings)",
    "url": "https://scryfall.com/card/...",
    "as_of": "2026-10-04",
    "version": "oracle-cards-20261003210155",
    "notice": "Unofficial Fan Content. Not approved/endorsed by Wizards. ..."
  }
}
```

- `kind` is `source` (third-party material, shown as theirs) or `computed` (the Vault derived it, with
  `inputs` listing which sources and versions). The two are never mixed in one field.
- Tags always say who tagged and how ("tagged `ramp` by Scryfall's community Tagger, weight median").
- Prices always carry marketplace, currency and date.
- Rules answers carry the CR version and rule number.
- Decks carry the site, author and link.
- The footer, credits page, MCP `instructions`, skills, plugin README and every MCP Apps UI show the
  Fan Content notice and the "not endorsed" lines. Skills tell the agent to repeat provenance when it
  answers.
- Tests: a CI test fails if any MCP tool's output schema lacks `provenance`, and a UI check fails if a
  third-party value is rendered without a source line.

## Open with the owner

These are **open owner actions**, not optional extras: the audit of #62 and #79 found them not done, and no decision to drop
them is recorded. The drafts are final and ready (`docs/outreach-drafts.md`); nothing has been sent. Nobody but the owner
sends them.

| Ask | Why | State | Owner step |
|---|---|---|---|
| Scryfall: do the planned tools count as added value? (#62) | Their terms require "additional value"; the answer shapes the lookup tools | **Not sent** | Send the Scryfall draft from the contact route on https://scryfall.com/docs/api, record the answer here with date and who answered |
| Moxfield: legitimate access before any automated deck fetching (#62) | Their terms forbid robots without written approval (read 2026-10-07) | **Not sent** | Send the Moxfield draft to support@moxfield.com or their Discord; until an answer, nothing is fetched |
| Archidekt: is one public deck per person's request acceptable? (#79) | Their terms exclude "automated searches, requests, or queries"; the owner's position is that one request on a person's action is within them, but Archidekt has never been asked | **Not sent** | Send the Archidekt draft via https://archidekt.com/contact or their Discord (link on the terms page) |
| Wizards: Fan Content Policy (optional since #142) | The rules are read live and nothing is stored, so nothing needs their permission today | Not sent, optional | Only if the owner wants it in writing |
| Cardmarket's terms | Read in a browser on 2026-10-07 (the earlier "bot check" was a wrong address) | **Read** | Recorded in `docs/data-sources.md`: showing Cardmarket's own prices needs its prior written agreement (clause 9); nothing from Cardmarket is stored or shown today |
| Fan Content monetisation terms | Needed before joining an affiliate programme | Not read | Read https://company.wizards.com/en/legal/fancontentpolicy before joining |

The gate (the table at the top) is met for what the Vault loads today, because the terms of those sources have been read.
The asks above are **not** a technical precondition that the code checks; they are the owner's commitments from #62, and
the status line here stays honest about them: **not sent**.

Done and no longer open: Scryfall's API terms were read first-hand (2026-10-05, #141, see "Scryfall (API terms)" above); Moxfield's
terms were read first-hand (2026-10-07, see "Moxfield"); the registration question is decided (see Decisions); the rules
are cited live from Wizards with no copy kept (see Decisions).

## Decisions

- **Free accounts, no anonymous catalog (owner, 2026-10-06, #62).** The Vault stays free; every feature needs a free
  account because it serves the person's own collection, decks and questions. There is no anonymous card or rules API:
  it would add nothing beyond Scryfall's own data and would amount to the "repackage, republish, or proxy" that
  Scryfall's terms forbid, and it would spend the free database on traffic that is not a player's. The `PUBLIC_CATALOG`
  switch was removed from the code so it cannot be turned on by mistake. If people should ever look up cards without an
  account, that is a product decision about a page that adds value, not a switch on a raw API.
- **Comprehensive Rules (owner, 2026-10-05, #142):** cited live from Wizards' current edition; the Vault stores no copy
  (docs/rules-index.md).
