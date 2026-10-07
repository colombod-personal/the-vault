# Comprehensive Rules without keeping a copy (epic #142)

Owner's direction (2026-10-05): the assistant may cite the rule a question needs (its number, the Fan Content notice
and a link to Wizards' document), but the Vault should **not maintain a copy of the rules** or track rule changes by
hand. This page records the research (#143) and the spike (#144). The design (#145) follows from it.

## How Wizards publishes the rules (research, 2026-10-05)

- Page: <https://magic.wizards.com/en/rules>. It links the current edition in three formats, for example
  `https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt` (also `.pdf`, `.docx`). The file name
  carries the edition date (here 2026-09-25); the year folder changes with it.
- The links are in the page's server HTML, so a server can find the current edition by reading the page (1.0 s).
- The TXT is UTF-8, about 1 MB, fetched by a server in 0.6 s with an accurate `User-Agent`; it answers `HEAD` in
  0.05 s with an `ETag` and `Last-Modified`. It parses into 3,165 numbered rules (plus the glossary).
- Card rulings are not part of this: they come from Scryfall's bulk data with the cards (refreshed daily).
- Wizards' Fan Content Policy excludes "verbatim copying and reposting" of its IP; quoting the one rule a question
  needs, with the notice and a link, is citing it.

## Approaches compared (spike, 2026-10-05)

Ten real questions ("does trample damage go through when the blocker dies", "commander tax", "two-headed giant shared
life total", "protection from red", ...) against the live 2026-09-25 edition.

| | What the Vault stores | Search | Speed | Notes |
|---|---|---|---|---|
| **A. Derived index in Postgres** | 1.3 MB of `tsvector` (stemmed words with positions) per rule number, no text column | Postgres full-text rank | 22 ms a query | Weaker on the test questions (trample, deathtouch, protection found general rules, not 702.x). Stems with positions nearly reconstruct the text, so it is closer to a copy than it looks |
| **B. Nothing stored: fetch, then search in memory** | Nothing (optionally the current edition's URL) | BM25 over the fetched text | index built in 41 ms; 2 ms a query | Best results on the test questions (trample → 702.19, deathtouch → 702.2, protection → 702.16, commander tax → 903.8). A cold start adds ~0.6 s to fetch the file |
| C. Semantic (embeddings) | Vectors per rule | Similarity | needs a model | Needs a paid model or heavy local compute: against "free"; not pursued |
| D. Link only | Nothing | None | — | The judge could not find, quote or check rules |

## Recommendation: B, "fetch, cache, search in memory"

- **Nothing of the rules text is stored in the database.** Each server instance fetches the current edition's TXT
  when a rules tool is first called, builds an in-memory index (41 ms), and keeps it while the instance is warm.
- **The edition is detected automatically:** the daily job reads the rules page and stores only the edition's URL
  and date (a few bytes of metadata). Instances whose cached edition differs refetch. No manual tracking.
- **Every quote comes from Wizards' own file**, with the rule number, the edition date, the notice and the link;
  `verify_citation` checks against that same fetched text.
- **If Wizards' site is unreachable**, the rules tools say so; they never answer from memory.
- **Changes needed (#145, #146):** drop the stored `rules` table and the `rules` catalog source; a small
  `vault/rules_live.py` (fetch, parse, BM25, cache keyed by edition); the four rules tools read from it; improve
  search for keyword abilities (boost section 702 when the question names a keyword); glossary support.

Open points for the design: cold-start latency on Vercel (about 0.6 s extra, first call only), and a short retry
with backoff when Wizards' server is slow.

## Design (#145, agreed with the owner 2026-10-05)

Owner: "store nothing, but store how to navigate it, or let the agent know how to read through it, so we always get
fresh; the connector must have good tooling to navigate the rules, they are complex."

**Stored: nothing.** No rule text, no index, no edition table in the database. Each server instance:

1. reads Wizards' rules page (at most every 6 hours) to find the current edition's TXT link;
2. fetches the TXT when a rules tool is first used, or when the edition changed, and parses it with
   `vault/rules_parser.py` (the existing parser);
3. builds in memory: the search index (BM25), and the **navigation map**:
   - the hierarchy: sections ("7. Additional Rules"), subsections ("702. Keyword Abilities"), rules, subrules;
   - headings: the title of each section, subsection and titled rule ("702.19. Trample");
   - cross-references: every "rule 702.19" or "see rule 510" in the text, in both directions (cites / cited by);
   - glossary terms and keyword abilities, each mapped to the rules that define them.

If Wizards' site cannot be reached, the rules tools say so (503) and never answer from memory.

**Tools (REST under `/api/v1/catalog/rules`, MCP for agents):**

| Tool | What it gives |
|---|---|
| `rules_outline` | The table of contents: the nine sections, or one section's subsections, or a subsection's rules, with headings, to drill down |
| `get_rule` | One rule (or `glossary:Term`): its text, parent, children, previous/next sibling, the rules it cites and the rules that cite it |
| `search_rules` | Best matches for a question; a keyword ability or glossary term in the question puts its defining rule first |
| `find_rules_term` | A glossary term or keyword ability ("trample", "state-based actions") → the defining rule(s) and the glossary definition |
| `verify_citation`, `present_steps` | Unchanged for the agent; the text they check and attach comes from the live file |

**Editions (#25).** Only the current edition can be read. Wizards' rules page links the current TXT and nothing else (checked 2026-10-07: no archive of past editions on that page or linked from it), and the Vault stores no copy, so an older edition has no source to read from. The `version` argument of `verify_citation`, `present_steps`, `get_rule` and `search_rules` therefore accepts the current edition's date or `latest`; any other value is refused with an error that names the current edition (`tests/test_catalog_api.py::test_the_version_argument_names_the_current_edition_or_is_refused_by_name`), and for card text and rulings, which have no editions, any date is refused. Reading an old edition would need an archive Wizards publishes, or a copy the owner decides to keep (against the design above).

Every answer carries provenance: Wizards of the Coast, Comprehensive Rules, the edition date, the TXT link, the Fan
Content notice.

**Reading guide for agents** (the `rules-judge` skill, the judge agent and the MCP instructions): how the rules are
organised (1 Game concepts, 2 Parts of a card, 3 Card types, 4 Zones, 5 Turn structure, 6 Spells, abilities and
effects, 7 Additional rules incl. 702 keyword abilities and 704 state-based actions, 8 Multiplayer, 9 Casual variants
incl. 903 Commander), and a strategy: find the term (`find_rules_term`) or search; open the defining rule; read its
children; follow the references both ways; check exceptions in the parent and siblings; verify every quote.

**Removed:** the `rules` and `rules_versions` tables (empty in production; dropped by a migration), the `rules`
catalog source and `RULES_URL`.
