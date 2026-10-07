# Letting an assistant update which cards you own: design (issue #81)

Status: **agreed with the owner on 2026-10-06** (decisions below). Built: the change sets, undo, caps and audit (#83,
`vault/owned_changes.py`) and the re-import as a three-way update (#194, `vault/merge.py`, `vault/importer.py`; the
rules and their tests are under "The rules of a re-import"). Threat model: docs/mcp-oauth-threat-model.md.

## What it is for

"I just opened a Sliver Queen, add it." "I traded away my two Doubling Seasons." "I sold the Cavern of Souls." Today an
assistant can read the collection but can only change it by importing a whole file. This adds small, checked edits.

## The one fact that shapes the design

A person's collection in the Vault started as a **snapshot of their last imported file** (Dragon Shield, Moxfield, CSV):
every import records what changed (`imports`, with a summary and the `collection_version` it replaced). Until #194 an
import also replaced the whole collection; now it applies only what changed in the app (below). So:

- An assistant's edit is recorded **the same way an import is**: a small change set, stored as an import entry of kind
  `assistant`, with its summary, the app that made it, and the version it applied to. History, the import list and the
  web app's "what changed" all show it without new machinery.
- **A re-import applies only what changed in the person's app (owner decision, 2026-10-06).** An import used to replace
  the whole collection with the file; now that changes can also be made in the Vault (assistant edits now, web edits later),
  that would wipe them. Instead a re-import compares three things:
  - **the last imported file** (what the app said before),
  - **the new file** (what the app says now): the difference between the two is exactly "sold this, bought that", and
    only that difference is applied,
  - **the collection now**, with the edits made in the Vault since the last import: those stay.

  The baseline is **the last imported file as imported, stored per scope** (the folders, later buckets, it covered),
  not derived by subtracting change sets from today's collection: a card added only in the Vault and kept by a merge is
  in no file, so subtracting would count it as part of the baseline and a second import of the same file would delete
  it (docs/collections.md, decision 6). Each merged import stores its own snapshot as the new baseline; change sets stay
  the history. When the same card changed on both sides (the
  person told the assistant they sold it, and the new file also drops it), the import preview lists it once as a
  **conflict** and asks, instead of applying it twice. A "replace everything with this file" option stays for people
  who want the old behaviour. This is server behaviour, not AI: it applies to every import, from the web app, the API
  or an assistant. The exact rules follow.

## The rules of a re-import (#194)

A **card** here is a printing in a finish (name, set, collector number, finish: `delta.BY_PRINTING`). Each card has a
**state**: its copies grouped by condition, language, folder, price paid and date paid, with their quantities and trade
quantities. Dragon Shield's own price columns are not part of it (every export has new ones). "Changed" means the state
differs. Three states are compared per card:

- **base**: the last imported file as imported (`collection_baselines`, replaced by every import; Vault edits never touch
  it);
- **theirs**: the new file;
- **ours**: the collection now, with the Vault's edits.

| In the app (theirs vs base) | In the Vault (ours vs base) | Result |
|---|---|---|
| unchanged | untouched | the file's rows (nothing changes; the file's prices and details are refreshed) |
| unchanged | edited | **keep the Vault's edit** (listed as a kept edit) |
| changed | untouched | **take the app's change** (what the app did: bought, sold, moved) |
| changed | edited, to the same thing | take the app's rows; no conflict (both agree) |
| changed | edited, to something else | **conflict**: listed in the preview, default **keep the Vault's edit** |
| removed (not in the file now) | edited | conflict of kind `removed_in_app`: the preview says the app dropped the card and it was edited here; default keep the Vault's edit |
| removed (not in the file now) | untouched | removed |
| added (not in the last file) | added or changed differently | conflict of kind `added_in_both` |
| not in the file, not in the last file | added in the Vault | kept edit (the card is in no file) |

Other kinds of conflict: `removed_in_vault` (the assistant removed every copy, the app changed it) and `changed_in_both`.

- **Preview** (`POST /api/v1/imports/preview`, MCP `import_collection_csv` without `confirm`, `get_staged_upload`,
  `confirm_staged_upload` without `confirm`): `changes` is what happens to the collection; `merge` has `mode`
  (`first`, `merge`, `no_baseline`, `replace`), `from_your_app` (cards added, removed, increased, decreased, changed
  and the copies in and out: only this is applied), `kept_vault_edits` (count and cards), `conflicts` (count and cards,
  each with its `id`, `kind`, the copies at the last import, in the app and in the Vault, a `question` and the answer it
  will get), and `replace_everything_discards_vault_edits` (how many edits replacing everything would lose). Lists are
  capped (100 in an answer, 200 stored); the counts are complete.
- **Answers**: every conflict keeps the Vault's edit unless the person says otherwise: `conflicts=app` takes the app's
  value for all of them, `use_app_value=<id>` (repeatable) for the named ones. The assistant asks one card at a time.
  **`replace_everything=true`** gives the old behaviour: the file replaces the collection and the edits made in the Vault
  are discarded (the preview says how many first). The same options on `POST /api/v1/imports`, `GET /api/v1/uploads/{id}`
  and `POST /api/v1/uploads/{id}/apply`; the MCP tools take them as arguments. The web app imports without a preview and
  uses the defaults; it says afterwards how many cards it kept.
- **After every import** (merged or replaced) the file becomes the new base, whatever was answered: a card kept by a
  merge stays kept when the same file is imported again (the required test of docs/collections.md), and importing the
  same file twice changes nothing.
- **History and undo.** An import is an `imports` entry (kind `import`) whose `merge` field keeps what was applied, kept
  and asked, so the history reads the same for every path. Undo is unchanged: it reverts the last assistant change set
  until the collection changes again, and an import changes it, so an edit cannot be undone after an import (it stays,
  and the history says so). There is no undo of an import itself (there never was); importing the earlier file again does
  it, and keeps what was edited in the Vault since.
- **No base** (a collection imported before this existed and never imported since): migration `0112` rebuilds one from
  what the Vault recorded: the rows of the collection are the last file's, except the cards an assistant change set
  touched since, whose copies before the first change are in the change set's record; those are compared by copies only.
  A person with no import file on record (and any collection whose base was lost) gets `mode: no_baseline`: nothing can be
  told apart, the file replaces the collection as imports always did, and the preview says so. The next import then has a
  base.
- **Limits.** Cards are compared by their state, so an undone edit that the undo put back as a plain row (the original
  was in a folder, or Mint) counts as an edit on the next import and is kept, with the same number of copies. Until
  buckets exist (#118) there is one base per person; with buckets it is one per scope (docs/collections.md).

## The tools

| Tool | Scope | What it does |
|---|---|---|
| `update_owned_cards` | write | Propose changes: lines of `add`, `remove` or `set` with a card name, a quantity, and optionally the printing (set and collector number) and finish. **Changes nothing.** Returns the preview and a `confirmation` token |
| `confirm_owned_cards_update` | write, destructive | Applies exactly the previewed change set, given its `confirmation` token, after the person said yes |
| `undo_owned_cards_update` | write, destructive | Reverts the last assistant change set (preview first, then confirm, like the others) |

Preview answer: for each line, the card as the Vault understands it (name, printing, finish), copies before and after,
the price change (Scryfall's, dated), and any line it refuses with the reason. Totals: copies added and removed, value
change.

## Rules

1. **Preview, then confirm.** The confirmation token is signed by the server over (person, the exact change set, the
   collection version it was computed against) and expires after 15 minutes. The confirm call applies only that change
   set and only if the collection has not changed since; otherwise it refuses and asks for a new preview. A changed or
   injected instruction cannot slip different changes into the confirm step.
2. **Write scope only.** A read-only connection is refused by the API (as every write is today). The consent screen
   already says Write means "make changes: import collections, save and edit decks..."; it gains "update which cards
   you own".
3. **Small edits only.** At most 50 lines per change set. Removing more than 25 copies, or more than 10% of the
   collection's copies, in one change set is refused with a pointer to the import or reset flows, which have their own
   previews (#129). Setting a card to 0 counts as removing.
4. **The assistant understands the card and asks (owner decision).** The preview returns, for each line, the matching
   cards and their printings (set, collector number, finish, art, price) instead of picking one. The assistant shows
   them and asks which one the person means; only then does it build the change set with that printing. "Printing not
   specified" is used only when the person says they do not know (the Vault labels and prices these by name). A typo or
   a name matching a token and a card is never resolved silently. Removing needs the printing when the person owns
   several.
5. **Undo.** The last assistant change set can be undone until the collection changes again (another edit or an
   import); the web app shows an Undo on that history entry too (Account, "Collection history": `Undo` shows what it will put back, and "Undo this change" applies exactly that preview; older entries say they can't be undone any more). The import history marks the one entry that can be undone (`undoable`, and `undone` once it was). Undo is itself recorded.
6. **Audit and scope.** Each change set records, per changed printing, the folder (the future bucket) of the copies it
   added or removed, from day one (docs/collections.md). Each change set also records the app (OAuth client or token name), the time and the summary; the person sees
   it in their import history and can revoke the app under Connected apps.
7. **Rate limit.** 10 change sets a minute per person.
8. **Nothing else changes.** Decks, shares and the account are untouched; saved decks' coverage reflects the new counts
   on their next read.

## Risks and how the rules answer them (OAuth threat model)

| Risk | Answer |
|---|---|
| A prompt injection (a web page, a shared deck description) tells the assistant to delete cards | Preview shown to the person, host's destructive-tool confirmation, removal caps, undo, audit entry naming the app |
| The confirm step is replayed or altered | Token bound to the exact change set and collection version, single use, 15 minutes |
| A stolen token edits the collection | Write scope needed; revocable under Connected apps; every change set listed with the app's name; undo |
| Wrong printing priced wrongly | No guessing: one match or a refusal with candidates; "printing not specified" is labelled as such |
| The edit is lost on the next import | A re-import applies only the app's own changes since the last import and keeps Vault edits; conflicts are asked about in the preview (tests/test_reimport_merge.py, tests/test_reimport_mcp.py) |

## Tests to write first (#83)

- A read-only token cannot preview or confirm (403); a write token can.
- Preview changes nothing; confirm applies exactly the preview (copies and value equal to the preview's totals).
- Confirm with a token for a different change set, another person, an expired token, or after the collection changed
  is refused and changes nothing.
- 51 lines, or removing 26 copies or more than 10%, is refused.
- An ambiguous name is refused with candidates; a single match without a printing is added as "printing not specified".
- Undo restores the exact previous quantities; undo after a later import is refused.
- The change set appears in the import history with the app's name.
- (Re-import issue) A later re-import keeps the edit, applies only the file's own changes, and lists a card changed on both
  sides as a conflict; "replace everything" still replaces. Done in #194: one test per rule of the table, idempotence, the
  collections.md sequence, undo, tenancy, 10,000 rows, the migration and the whole flow through the MCP tools
  (`tests/test_reimport_merge.py`, `tests/test_reimport_mcp.py`).
- Tenancy: no tool can read or change another person's collection.

## Implementation order

1. **Change sets** (#83, built): the three tools, the change-set record, undo, caps, audit.
2. **Re-import as a three-way update** (#194, built): the import preview shows what the new file changes since the last
   import, what Vault edits it keeps, and any conflicts to answer; "replace everything" stays as an option. Coordinates
   with the Collections work (#124 import into one bucket, #129 reset), which change the same import path.
3. **Buckets** (#118): each line names a bucket, with a default, once buckets exist (owner agreed).

## Decisions (owner, 2026-10-06)

1. A re-import applies only the changes made in the person's app since the last import and keeps edits made in the
   Vault; conflicts are asked about in the preview; "replace everything" remains available.
2. Caps: as proposed (50 lines; at most 25 copies or 10% removed at once) unless revisited. Implementation note
   (#83): removing up to 5 copies is never refused by the 10% rule, or a new person with a handful of cards could not
   record selling one.
3. The assistant must understand the card and confirm the printing with the person; "printing not specified" only when
   the person does not know.
4. Buckets: designed in now, a default bucket until #118 lands.
5. Conflicts on re-import: the preview always asks, with "keep the Vault's edit" preselected (the more recent intent).
   Built as: a conflict applies "keep the Vault's edit" unless the person answers `app` for it (or for all); the
   assistant puts the question to the person, one card at a time.
