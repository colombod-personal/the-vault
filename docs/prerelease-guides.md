# Prerelease guides: set to Wizards' guide and event page (vault-064.10.8)

Owner, 2026-10-12: Wizards "publishes pre release guides ahead of the pre release events, the vault product must know how to get to those
on the wizard of the coast website, this looks like a table where we keep a row for set -> pre release guide url".

## What exists

| Piece | Where |
|---|---|
| The table `set_prerelease_guides` (one row per set, lower-case Scryfall code) | `vault/models.py`, migration `0124` |
| The manual seed: the thirteen sets released in the two years before 2026-10-12 and the next announced set (Star Trek), each page opened on 2026-10-12 | `vault/prerelease_seed.py` (inserted by the migration; `apply_seed` adds missing rows only) |
| The read tool `get_prerelease_guide(set)`; REST `GET /api/v1/catalog/prerelease?set=` | `vault/prerelease_guides.py` (`answer`), `vault/api/catalog_api.py`, `vault/api/mcp_catalog.py` |
| The discovery job, a step of the daily `sync-catalog` workflow | `jobs/sync_prerelease_guides.py`, `.github/workflows/sync-catalog.yml` |
| The twin of the two Wizards sites' sitemaps and pages | `twins/wizards.py`; live comparison in `tests/conformance` |

The tool is not in the compact core list (`vault/api/mcp_profile.py`): hosts that use the compact list reach it through `vault_tools`
and `vault_call`.

## A row

Per set: name, release date, prerelease start and end with where they came from, the **player guide** link (magic.wizards.com) and the **WPN
event page** link (wpn.wizards.com), each with the page's own title as a label, the day it was read and `verified`/`unverified`, and the pack
composition per format (traditional, Two-Headed Giant): packs per player or team, boosters in each pack, a one-line summary in the Vault's words,
its source URL, day and status. A row is `verified` when the dates were read from a Wizards page and a page of the set is verified.
**Nothing of a Wizards page's text is stored** (`docs/compliance.md`, "Wizards' prerelease pages").

Two kinds of page are linked because they hold different things: the guide is for players (what is in a Prerelease Pack, how to build the
40-card Sealed deck), the event page is for stores (dates, the Traditional and Two-Headed Giant templates). Their addresses are not derivable
from the set's name (`spider-man-prerelease-guide`, `the-guide-to-your-first-prerelease-with-aetherdrift`, `prerelease-marvels-spider-man`).

## The job

1. Scryfall's list of sets (one request): every upcoming expansion or core set gets a row the day it is listed, with dates worked out as the
   week before its release date and marked `unverified`.
2. For a set whose prerelease is within 45 days and whose pages are not both verified: read the two sitemaps (`magic.wizards.com/en/sitemap.xml`;
   `wpn.wizards.com/sitemap_index.xml` and the English sitemap it names), match the set's name to a page by its identifying words
   (`match_pages`), and check each page found with one request (its title must be a prerelease page). From an event page: the dates and the
   Two-Headed Giant arrangement; from a guide: the Play Boosters in a pack.
3. A set with nothing found keeps a row that says so, with the day and where it looked (never silence); within two weeks of the prerelease
   it is listed in the job's `needs_a_person` and as a GitHub Actions warning.
4. A verified fact is never overwritten: when a source now says something else the row is left alone and `source_change_note` says what
   was seen. Two pages that fit one set are not guessed between.
5. A sitemap that cannot be read, or a page that does not fit, fails the run after its work and its report, so it is noticed.

It runs only when `CATALOG_SOURCES` names `prerelease_guides`; see the compliance section for why and for the switch. Tests use the Wizards
twin and never the real web.

## What it does not do

* It does not read pack contents out of the prose of a guide beyond the one counted list, and it does not know which colour pack a person
  holds. A guide whose list it cannot read is linked and its contents are `unverified`.
* It does not know about regional delays or a store's own rules: every answer says the store's listing decides.
* It cannot open a bead when a guide is missing (it runs in GitHub Actions): the signal is the row's note, the run's warning and the
  `needs_a_person` list.
