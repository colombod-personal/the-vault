# Letting an assistant update which cards you own: design (issue #81)

Status: **draft for the owner's review.** Nothing here is built; #83 implements it once this is agreed
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
- **The next full import still replaces everything**, assistant edits included, because the file is the source of truth
  for people who keep their collection in another app. The import preview (already built) lists what would be lost, so
  nobody loses an edit silently. People who keep their collection only in the Vault never re-import, so their edits
  stay.

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
4. **No guessing printings.** A name that matches one card adds it as "printing not specified" (the Vault already
   shows these, and prices them by name), unless the person names the printing. A name that matches several cards (a
   token and a card, a typo) is refused with the candidates. Removing needs the printing when the person owns several.
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
| The edit is lost on the next import | The import preview lists it; documented in the tool description so the assistant says so |

## Tests to write first (#83)

- A read-only token cannot preview or confirm (403); a write token can.
- Preview changes nothing; confirm applies exactly the preview (copies and value equal to the preview's totals).
- Confirm with a token for a different change set, another person, an expired token, or after the collection changed
  is refused and changes nothing.
- 51 lines, or removing 26 copies or more than 10%, is refused.
- An ambiguous name is refused with candidates; a single match without a printing is added as "printing not specified".
- Undo restores the exact previous quantities; undo after a later import is refused.
- The change set appears in the import history with the app's name; a later full import's preview lists it as lost.
- Tenancy: no tool can read or change another person's collection.

## Questions for the owner

1. **The snapshot rule:** is it right that a later full import replaces assistant edits (with a warning in its
   preview), rather than keeping a separate layer of manual edits that is reapplied after imports? (The layer avoids
   losing edits but double-counts cards that the next export already includes.)
2. **Caps:** 50 lines per change set; at most 25 copies or 10% removed at once. Right numbers?
3. **Printing not specified:** allowed for adds by name (priced by name), or must the person always give the printing?
4. **Buckets (#118):** until buckets exist, edits go to the one collection; afterwards each line names a bucket, with a
   default. Fine to design it that way now?
