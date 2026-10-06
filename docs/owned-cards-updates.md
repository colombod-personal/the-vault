# Letting an assistant update which cards you own: design (issue #81)

Status: **agreed with the owner on 2026-10-06** (decisions below). #83 builds the change sets; #194 the re-import. Nothing here is built; #83 implements it once this is agreed
(`status:ready`). Threat model: docs/mcp-oauth-threat-model.md.

## What it is for

"I just opened a Sliver Queen, add it." "I traded away my two Doubling Seasons." "I sold the Cavern of Souls." Today an
assistant can read the collection but can only change it by importing a whole file. This adds small, checked edits.

## The one fact that shapes the design

A person's collection in the Vault is a **snapshot of their last imported file** (Dragon Shield, Moxfield, CSV): every
import replaces the whole collection and records what changed (`imports`, with a summary and the
`collection_version` it replaced). So:

- An assistant's edit is recorded **the same way an import is**: a small change set, stored as an import entry of kind
  `assistant`, with its summary, the app that made it, and the version it applied to. History, the import list and the
  web app's "what changed" all show it without new machinery.
- **A re-import applies only what changed in the person's app (owner decision, 2026-10-06).** Today an import replaces
  the whole collection with the file. Once changes can also be made in the Vault (assistant edits now, web edits later),
  that would wipe them. Instead a re-import compares three things:
  - **the last imported file** (what the app said before),
  - **the new file** (what the app says now): the difference between the two is exactly "sold this, bought that", and
    only that difference is applied,
  - **the collection now**, with the edits made in the Vault since the last import: those stay.

  The last imported file does not need to be stored: it is today's collection minus the change sets recorded since that
  import (assistant edits are recorded as change sets for this reason). When the same card changed on both sides (the
  person told the assistant they sold it, and the new file also drops it), the import preview lists it once as a
  **conflict** and asks, instead of applying it twice. A "replace everything with this file" option stays for people
  who want the old behaviour. This is server behaviour, not AI: it applies to every import, from the web app, the API
  or an assistant. It is its own piece of work (see "Implementation order").

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
   import); the web app shows an Undo on that history entry too. Undo is itself recorded.
6. **Audit.** Each change set records the app (OAuth client or token name), the time and the summary; the person sees
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
| The edit is lost on the next import | A re-import applies only the app's own changes since the last import and keeps Vault edits; conflicts are asked about in the preview |

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
  sides as a conflict; "replace everything" still replaces.
- Tenancy: no tool can read or change another person's collection.

## Implementation order

1. **Change sets** (#83): the three tools, the change-set record, undo, caps, audit.
2. **Re-import as a three-way update** (new issue): the import preview shows what the new file changes since the last
   import, what Vault edits it keeps, and any conflicts to answer; "replace everything" stays as an option. Coordinates
   with the Collections work (#124 import into one bucket, #129 reset), which change the same import path.
3. **Buckets** (#118): each line names a bucket, with a default, once buckets exist (owner agreed).

## Decisions (owner, 2026-10-06)

1. A re-import applies only the changes made in the person's app since the last import and keeps edits made in the
   Vault; conflicts are asked about in the preview; "replace everything" remains available.
2. Caps: as proposed (50 lines; at most 25 copies or 10% removed at once) unless revisited.
3. The assistant must understand the card and confirm the printing with the person; "printing not specified" only when
   the person does not know.
4. Buckets: designed in now, a default bucket until #118 lands.
5. Conflicts on re-import: the preview always asks, with "keep the Vault's edit" preselected (the more recent intent).
