# Compliance and provenance

The Vault depends on other people's data and says so, always. Two rules sit above everything else:

1. **Show provenance. Always.** Every card, rule, ruling, tag, price and deck we return or display
   says where it came from and as of when.
2. **Never make anything look like ours.** Source material is labelled as the source's. What the
   Vault computes (a legality check, a budget, a deck count) is labelled as computed by the Vault, from
   which inputs. The Vault is not produced or endorsed by Scryfall, Wizards, Moxfield or Archidekt.

Status of this document: written 2026-10-04 from public pages that were reachable then; Scryfall's terms read first-hand on 2026-10-05. Several
primary pages returned errors (marked **unverified**). Nothing marked unverified may be relied on
until someone has read the primary text. This is engineering diligence, not legal advice.

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
| No paywall: no payments, surveys, subscriptions, ratings, chat servers or follows in exchange for the data. "If you have an account system, end-users should be able to access card data anonymously or with free accounts." | The Vault is free; free accounts are explicitly fine. Optional anonymous access exists (`PUBLIC_CATALOG`) | done |
| Do not use the data to create new games or imply it is from another game | Magic only | done |
| "You may not simply repackage, republish, or proxy Scryfall data. Your software must create additional value for end-users." | Tools answer for the person's collection and decks, check legality and budgets, verify citations, simulate curves; card lookups serve that grounding. No bulk dumps or raw search proxy | done; keep in review |
| `User-Agent` (accurate, the app's name) and `Accept` on every API request | `jobs/sync_prices.py`, `jobs/sync_catalog.py` | done |
| Rate limits per endpoint; bulk files for large data | Daily bulk downloads, one rate-limited client | done |
| Images: do not crop the copyright or artist, distort, recolour, watermark or misattribute; with `art_crop`, show artist and copyright in the same view | Applies to the web app and MCP Apps views | done; keep in review |
| Tags (Tagger, community-maintained, moderated): track tags by `id`, not slug; be able to **temporarily hide individual tags** | `HIDDEN_TAGS` setting (ids or slugs) removes a tag and its children from roles; weights read as `very_strong` > `strong` > `median` > `weak` | done (2026-10-05) |
| Rulings and Oracle tags | Offered as bulk files for this purpose; shown with provenance | done |

No email to Scryfall is needed to load the card data on these terms.

### Moxfield

[moxfield.com/terms](https://moxfield.com/terms) returned **403**. From public statements and
developer discussion (unverified): no official API; scraping is against the terms; Moxfield has said
legitimate developers should contact them on Discord or at support@moxfield.com to establish a
relationship.

Rules for us: no automated fetching of Moxfield decks. The user's own CSV or pasted list is fine.
Any Moxfield-derived deck is labelled "Deck from Moxfield", with its author and a link. Ask Moxfield
before building anything automated.

### Archidekt

**The Vault only reads from Archidekt** (owner, 2026-10-05, issue #79). It never writes to Archidekt, never signs
in to anyone's Archidekt account, and never "syncs" a deck. When the Vault suggests changes, the person applies
them on Archidekt themselves; the Vault gives the change list and the buying list.

Sources (re-read 2026-10-05):

- **Terms:** [archidekt.com/terms](https://archidekt.com/terms), last updated 2018-09-07. The Vault is a personal,
  non-commercial tool that reads public decks a person points it to, credits Archidekt and the author, and does not
  harvest other users' information, build a competing site or reverse engineer anything.
- **Reading:** an Archidekt developer said on Archidekt's forum that the API is "open and public (as far as reading
  is concerned)" ([thread 40353](https://archidekt.com/forum/thread/40353)) and "You're more than welcome to use our
  API for whatever you want", asking only that it not be hammered
  ([thread 2832338](https://archidekt.com/forum/thread/2832338)). In early 2026 the same developer wrote "I believe we
  start rate limiting people at 40 requests per minute" ([thread 19112643](https://archidekt.com/forum/thread/19112643)):
  anything the Vault adds (#133) keeps the whole Vault well under that, paced rather than in bursts.
- **Writing:** there is no API for other apps to change a person's deck, so the Vault does not write.

What the Vault does:

- Reads **one public deck per request a person makes** (opening a link, the deck page, the graph overlay, or the
  `get_archidekt_deck` tool), server-side, with the toolkit's User-Agent naming the project. Private decks are not
  read (Archidekt answers 404 without sign-in).
- **No cache and no rate cap yet.** Today every read reaches Archidekt, one request per deck a person asks for.
  The plan (#133) is a cache for repeat reads, and a cap only if it is needed, and then only behind the cache so an
  over-limit read gets the cached copy instead of an error.
- No background jobs, crawling or deck search; nothing is fetched without a person asking. Decks are stored only
  when a person presses Save: their copy of that one deck's list, link and author's public username (for the
  credit), in their account, removable any time.
- Every Archidekt deck is labelled "Deck list from Archidekt", with its author and a link back.

Code review that nothing writes to Archidekt, with a guard test: #132.

Planned (#94): listing a person's **own** public decks by their Archidekt username, as one read on that person's
request, paced and cached. It is not built. Today's support is the single deck a person gives, and the
`archidekt-deck-helper` skill (and its generated plugin copy) tells agents to fetch only that one deck; they will need
updating together with #94.

### Other sources (to read before ingestion)

Commander Spellbook (data licence), Cardmarket public price guide (licence), Card Kingdom price
list (terms), Neon and Vercel (limits, in `catalog-design.md`). Each gets a row here once read.

## The two real risks

### 1. Registration

Wizards' policy says we cannot require email registration to access Fan Content. The Vault requires
sign-in (OAuth providers, which give us an email, or a passkey, which does not). Scryfall explicitly
allows free accounts. Today the README treats sign-in as protecting private collections, not
content, and the public pages stay open. The new design puts rules, card text and rulings behind
tokens, which is closer to the line.

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

## What I need from the owner

1. Someone to read the Scryfall API terms and Moxfield terms directly (both blocked my fetches), and
   paste or confirm the relevant clauses.
2. Approval to email Scryfall and Moxfield (drafts to follow) and to contact Wizards about rules text.
3. A decision on the registration question: public minimal tools without an account (recommended)
   or free accounts only.
