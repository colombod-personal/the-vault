# What a person can do in the web app, and through their AI assistant

Owner's rule (2026-10-04): everything a user can do in the web app must also be possible through the AI integrations,
except account-level actions that are kept out on purpose. This table maps each action to its REST route and MCP
tool. Built from the routes in `vault/api/v1.py`, `vault/auth.py`, `vault/oauth_routes.py` and the tools in
`vault/api/mcp.py` and `vault/api/mcp_catalog.py` (issue #100). Gaps are tracked in #101.

Scopes: **read**, **write** (changes the collection or decks; off by default on the consent screen), **account**
(never given to AI apps).

## Collection

| Action (web app) | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|
| See summary, value, gains | `GET /collection`, `/collection/stats`, `/collection/valuation` | `get_collection_summary`, `get_collection_stats`, `get_valuation` | read | no |
| Breakdowns by colour, type, mana value (Insights) | `GET /collection/breakdowns`, `/collection/names` | `get_collection_breakdowns` | read | no |
| Value history, acquisition timeline | `GET /collection/history`, `/collection/timeline` | `get_value_history`, `get_acquisition_timeline` | read | no |
| Browse and filter cards | `GET /collection/cards`, `/collection/cards/{id}` | `search_cards`, `list_card_names`, `get_card` | read | no |
| Sets view | `GET /collection/sets`, `/catalog/sets` | `list_sets` | read | no |
| Refresh prices | `POST /collection/refresh` | `refresh_prices` | write | no |
| **Upload a collection file (CSV)** | `POST /imports` (multipart) | `import_collection_csv` (CSV text in the argument) | write | **yes**: see G1, G2 |
| See past imports and what changed | `GET /imports`, `GET /imports/{id}` | `list_imports` | read | **yes**: no tool for one import's changes (G3) |
| Export the collection to another app | `GET /collection/exports` | `list_export_formats` (download links) | read | no |
| Graph view (clusters, deck map) | computed in the browser from the collection | none | read | **yes**: no AI equivalent (G4) |

All collection tools also read a collection someone shared with you (`share_id`, routes under `/shared/{id}/collection`).

## Decks

| Action | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|
| List saved decks | `GET /decks` | `list_decks` | read | **partly**: no name search (#96) |
| Open a deck with coverage | `GET /decks/{id}` | `get_deck` | read | no |
| Save a deck (text and its link) | `POST /decks` | `save_deck` (with `source_url`) | write | no |
| Edit a deck | `PUT /decks/{id}` | `update_deck` | write | no |
| **Delete a deck** | `DELETE /decks/{id}` | none | write | **yes** (G5) |
| Load a deck from an Archidekt link | `GET /archidekt/decks/{id}` | `get_archidekt_deck` | read | **partly**: saving it needs the model to convert JSON to text (#96 `import_deck_from_link`) |
| Check a decklist against the collection | `POST /decks/coverage`, `/decks/parse` | `check_decklist`, `parse_decklist` | read | no |
| Stats, legality, upgrades, validate, combos, shopping list | `POST /decks/stats` ... | `deck_stats`, `deck_legality`, `find_upgrades`, `validate_deck_changes`, `find_combos`, `shopping_list` | read | **partly**: text only, no `deck_id` (#96) |

## Sharing

| Action | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|
| See what others shared with me, open a shared deck | `GET /shared`, `GET /shared/{id}/deck` | `list_shared_with_me`, `get_shared_deck` | read | no |
| **Share my collection or a deck, list my shares** | `POST /shares`, `GET /shares` | none | write | **yes** (G6) |
| **Accept an invitation** | `POST /shares/accept` | none | write | **yes** (G6) |
| **Stop sharing** | `DELETE /shares/{id}` | none | write | **yes** (G6) |

## Cards and rules (catalog)

| Action | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|
| Card details, rulings | `/catalog/cards`, `/cards/{id}/rulings`, `/cards/lookup` | `get_card_oracle`, `get_rulings`, `lookup_cards` | read | no |
| Rules search, a rule | `/rules/search`, `/rules/{n}` | `search_rules`, `get_rule`, `verify_citation`, `present_steps` | read | no |

## Account: kept out of AI apps on purpose

Shown on the consent screen as "It will never be able to". These stay web-only.

| Action | REST route | Why not through AI |
|---|---|---|
| Change my name | `PATCH /me` | Account data (right to rectification is done by the person) |
| Export all my data | `GET /me/export` | Bulk personal data; the person downloads it themselves |
| Delete my account | `DELETE /me` | Irreversible |
| Tokens, passkeys, sessions, connected apps, sign-in methods | `/me/tokens`, `/me/passkeys`, `/me/sessions`, `/me/apps`, `/login/*` | Would let an app grant itself more access or lock the person out |

## Gaps (to close in #101)

- **G1 Large files.** `import_collection_csv` takes the file as text in a tool argument. A real collection
  (21,950 copies) is far too big to pass that way. Needed: a one-time upload link (the tool returns a short-lived URL the person
  opens to pick the file, or the host passes an attachment), then the import runs server-side.
- **G2 Import replaces the whole collection with no preview.** Needed: preview what would change (added, removed, changed),
  then confirm, as the web app's import summary shows.
- **G3 One import's changes.** Add `get_import` (`GET /imports/{id}`).
- **G4 Graph view.** No AI equivalent: decide whether a tool returning clusters/deck overlaps is useful (e.g. "which of my
  decks share cards"; the real claude.ai run worked this out by hand).
- **G5 Delete a deck.** Add `delete_deck` with preview and confirm.
- **G6 Sharing.** Add `share`, `list_my_shares`, `accept_share`, `stop_sharing`, with confirm on create and stop.
- Deck items (name search, import from link, `deck_id` analysis) are in #96, owned by the deck work (#88).
