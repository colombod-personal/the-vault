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

### Measured on the real edition (#144, 2026-10-07)

Run `python scripts/measure_rules_index.py` (add `DATABASE_URL` to include approach A; it uses a temporary table and keeps nothing). Edition
2026-09-25: 3,165 rules, 741 glossary entries. Network times are from the machine that ran it (a UK connection to Wizards' CDN, warm); the
earlier spike measured 1.0 s for the page and 0.6 s for the file from another network, so treat network figures as "tens of milliseconds
to about a second".

| Step | Measured |
|---|---|
| Rules page GET (150 KB) | median 21 ms (slowest of 5: 172 ms) |
| TXT `HEAD` (returns `ETag`, `Last-Modified`) | median 8 ms |
| TXT GET (978 KB) | median 21 ms |
| Parse and build the index and navigation map (approach B) | 253 ms |
| Memory held per instance by the built edition | 9 MB |
| One search question (BM25 plus term boost) | 1.6 ms |
| Stemmed full-text index of every rule, text not kept (approach A) | 765 KB; 0.45 ms a check |

**Citation checking, per approach.** 300 rules; for each a true quote (8 to 16 consecutive words) and the same quote altered, and the true quote
attributed to the wrong rule. "Wrongly accepted" is the share of altered quotes the check passed; a good check passes every true quote and no altered one.

| Alteration | B: the Vault's check on the fetched text | A: stemmed index in Postgres |
|---|---|---|
| true quotes accepted | 300 of 300 | 300 of 300 |
| a negation added ("can" to "can not") | 0 of 153 wrongly accepted | **40 of 153 (26%)** |
| one word swapped for another | 0 of 295 | 29 of 295 (10%) |
| a plural toggled ("creature" for "creatures") | 0 of 295 | **267 of 295 (91%)** |
| a small word dropped ("the", "a", "of") | 0 of 266 | 38 of 266 (14%) |
| two words swapped | 0 of 300 | 54 of 300 (18%) |
| right words, wrong rule number | 0 of 300 | 0 of 300 |
| the last word cut short by a letter | **92 of 92 (100%)** | 36 of 92 (39%) |

What it shows. Approach A cannot tell a quote from a paraphrase: stemming and dropped small words make "can not" and "creatures" pass, which is
the very error a citation check exists to catch, so it cannot back `verify_citation`. Approach B is exact on every alteration except one:
**a quote that stops in the middle of a word still passes**, because the check is a substring test (`catalog_queries._squash`); it should require
word boundaries. That is a defect in `verify_citation` found by this measurement and recorded here, not fixed in this change (it also covers the
Oracle-text and ruling checks). Approach C (embeddings) was **not measured**: it needs a model, and similarity cannot answer "is this exact
sentence in the rule" at all. Approach D (link only) cannot check a quote at all.

**Edition detection, per approach** (simulated with the real `LiveRules` and the Wizards twin, a clock stepped by the minute for 8 hours after the change;
approach A is analytic):

| Approach | A new edition (a new file name) | A file corrected in place | Cost of looking |
|---|---|---|---|
| A: daily job keeps a derived index | at the next daily run: up to 24 hours (analytic) | only if the job also compares validators (not built) | a database write and a reindex each time |
| B before #143 (page every 6 h, refetch on a new name) | seen after 360 minutes | **never** until the instance restarts (the cached edition was kept while the name was unchanged) | page read per 6 h |
| **B now** (also one `HEAD` on the unchanged name) | seen after 360 minutes | seen after 360 minutes | page (21 ms) + `HEAD` (8 ms) per 6 h; file and 253 ms rebuild only when it changed |
| C: embeddings | like A, plus re-embedding | like A | not measured |
| D: link only | nothing to detect (the page is the link) | nothing to detect | none; but nothing can be checked |

The 360 minutes are the page TTL (6 hours), the longest an instance can be behind. A real Wizards edition has been seen only once in these
measurements, so the cadence of real editions (one per set, roughly every two to three months, announced by an Update Bulletin before release) comes from
the bulletins, not from a series of measurements.

## Recommendation: B, "fetch, cache, search in memory"

- **Nothing of the rules text is stored in the database.** Each server instance fetches the current edition's TXT
  when a rules tool is first called, builds an in-memory index (41 ms), and keeps it while the instance is warm.
- **The edition is detected automatically, by each instance:** it reads the rules page (at most every 6 hours) and refetches when
  the link names another file, or when the file under the same name changed (one `HEAD` compares its `ETag`). Nothing about the
  edition is stored: not the text, not its URL, not its date (see "Design" below, which supersedes the first idea of a daily job that
  kept the URL). No manual tracking.
- **Every quote comes from Wizards' own file**, with the rule number, the edition date, the notice and the link;
  `verify_citation` checks against that same fetched text.
- **If Wizards' site is unreachable**, the rules tools say so; they never answer from memory.
- **Changes that followed (#145, #146, done):** the stored `rules` and `rules_versions` tables and the `rules` catalog source are gone
  (migration 0104, and the catalog job refuses a `rules` source: `tests/test_rules_parser.py`); `vault/rules_live.py` (fetch, parse, BM25,
  navigation map, cache keyed by edition) feeds the rules tools; a keyword or glossary term in a question puts its defining rule first.

Measured on production, 2026-10-09 (Vercel runtime logs, `mcp_tool` events, `duration_ms`; `vercel logs --environment production --query mcp_tool`):
the first `get_rule` call on a cold instance took 382 ms (it fetched and parsed Wizards' rules file), the repeat call a moment later 20 ms.
A tool that touches no outside source shows the same cold-start cost: `whoami` 835 ms and 390 ms on two cold instances against 19 to 20 ms warm.
So the cold start of a rules call is about 0.4 s over a warm one, first call only, which agrees with the estimate below (#145).

Open point for the design: a short retry with backoff when Wizards' server is slow.

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

**Editions (#25).** Only the current edition can be cited. Wizards' rules page links the current TXT and nothing else (checked 2026-10-07 and again 2026-10-09: no archive of past editions on that page or linked from it), and the Vault stores no copy. Earlier files do stay on Wizards' CDN under their dated names (found 2026-10-09; see `docs/reconciler-design.md`), and the Vault reads the previous one only to compute the change brief (`rules_changes`, #107): nothing is cited from it and `version` still accepts only the current edition. The `version` argument of `verify_citation`, `present_steps`, `get_rule` and `search_rules` therefore accepts the current edition's date or `latest`; any other value is refused with an error that names the current edition (`tests/test_catalog_api.py::test_the_version_argument_names_the_current_edition_or_is_refused_by_name`), and for card text and rulings, which have no editions, any date is refused. Reading an old edition would need an archive Wizards publishes, or a copy the owner decides to keep (against the design above).

Every answer carries provenance: Wizards of the Coast, Comprehensive Rules, the edition date, the TXT link, the Fan
Content notice.

**Reading guide for agents** (the `rules-judge` skill, the judge agent and the MCP instructions): how the rules are
organised (1 Game concepts, 2 Parts of a card, 3 Card types, 4 Zones, 5 Turn structure, 6 Spells, abilities and
effects, 7 Additional rules incl. 702 keyword abilities and 704 state-based actions, 8 Multiplayer, 9 Casual variants
incl. 903 Commander), and a strategy: find the term (`find_rules_term`) or search; open the defining rule; read its
children; follow the references both ways; check exceptions in the parent and siblings; verify every quote.

**Removed:** the `rules` and `rules_versions` tables (empty in production; dropped by a migration), the `rules`
catalog source and `RULES_URL`. Other pages of these docs that once described loading the rules into the catalog
(`docs/catalog-design.md` tables and "Rulings and rules", `docs/ai-integration-testing.md`'s local-data command,
`docs/data-sources.md`) were corrected to match (#142); `tests/test_rules_parser.py::test_the_catalog_job_no_longer_loads_the_rules` is
the test that a command naming `rules` as a source is refused.

## How a new edition is announced, and how the Vault notices (#143)

**What Wizards does** (read 2026-10-07):

- With each set release Wizards publishes an **Update Bulletin** on its announcements page (for example "Secrets of Strixhaven Update
  Bulletin", 15 April 2026, and "Lorwyn Eclipsed Update Bulletin", 9 January 2026, both by Eric Levine): "a summary of the rules changes
  planned to come to Magic with the release", ending "the official rules can be found on our rules page ... the official rules take
  precedence". The bulletin is a summary, not a machine-readable feed; there is no RSS or API for the Comprehensive Rules.
- The authoritative announcement is the **rules page itself** (`magic.wizards.com/en/rules`): its three links (DOCX, PDF, TXT) change
  to the new file. The page carries **no date or version text**; the edition is in the file name (`MagicCompRules 20260925.txt`) and in
  the file's first lines ("These rules are effective as of September 25, 2026").
- **A file can be published before it takes effect.** The current TXT's `Last-Modified` is 17 August 2026, 39 days before its effective
  date. When the page began linking it is not known. So an edition read from the page may be one that is not yet in force.

**What the Vault does:**

1. Each server instance reads the page at most every 6 hours and takes the TXT link (`TXT_LINK` in `vault/rules_live.py`).
2. A link to a **different file name** is a new edition: the file is fetched, parsed and indexed (253 ms), and every later answer carries the
   new version. Nobody tracks it by hand.
3. The **same file name** is asked once with `HEAD` (8 ms): a changed `ETag` means Wizards corrected the file in place, and it is read again.
   An unchanged file costs no download.
4. The answer's `version` is the file's own "effective as of" date, and its provenance names the edition and links Wizards' document. If that
   date is still in the future, the provenance says so ("this edition takes effect on ..., until then the previous edition is in force"),
   because an assistant should not present a rule as in force before its date.
5. If Wizards cannot be reached and an edition is cached, the cached one is kept (retry in 5 minutes); with nothing cached the rules tools
   answer 503 and say so.
6. **Nightly, the real page is compared with the twin** (`tests/conformance`, `test_wizards_rules_*`): if Wizards changes how the page links the
   file, the check fails that night, not when a person asks a rules question.

**Owner-visible limits:** an instance can be up to 6 hours behind a new edition; the Vault does not read Update Bulletins (it needs
none, the file is the source); and it cannot know a file's publication date relative to the page beyond the file's `Last-Modified`.
