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
| **Upload a collection file (CSV)** | `POST /imports` (multipart) | `import_collection_csv` (shows what would change; replaces only with `confirm`); big files: `start_collection_upload`, `get_staged_upload`, `confirm_staged_upload` | write | no |
| Expert council review (Commander expert, casual table, judge, devil's advocate, ...) | `GET /council`, `/experts`, `/experts/{id}` | `council_brief`, `expert_brief` (the plugin's agents, for connectors) | read | partly: in one chat the views are not independent (plugin hosts run real agents) |
| See your decks at a glance: name, format, commander(s), card count, colour identity | `GET /decks?brief=true` (and every deck answer's `overview`) | `list_decks` (no card lines), `get_deck`; set a format with `save_deck` / `update_deck` | read / write | no |
| **Edit the cards owned** (bought, sold, traded) | `POST /collection/changes/preview`, `/apply`, `/undo` | `update_owned_cards` (preview; asks for the printing), `confirm_owned_cards_update` (exactly the preview, after a yes), `undo_owned_cards_update`; docs/owned-cards-updates.md | write | no (the web UI has no single-card edit yet) |
| See the printings of a card you own, with pictures | `GET /collection/printings?name=` | `show_owned_printings` (a picture view in hosts with MCP Apps) | read | no |
| See past imports and what changed | `GET /imports`, `GET /imports/{id}` | `list_imports`, `get_import` | read | no |
| Export the collection to another app | `GET /collection/exports` | `list_export_formats` (download links) | read | no |
| Graph view (clusters, deck map) | computed in the browser from the collection | `get_deck_overlap` (cards shared by several decks, and copies short) | read | partly: the clusters themselves stay visual |

All collection tools also read a collection someone shared with you (`share_id`, routes under `/shared/{id}/collection`).

## Decks

| Action | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|
| List saved decks | `GET /decks` | `list_decks` | read | **partly**: no name search (#96) |
| Open a deck with coverage | `GET /decks/{id}` | `get_deck` | read | no |
| Save a deck (text and its link) | `POST /decks` | `save_deck` (with `source_url`) | write | no |
| Edit a deck | `PUT /decks/{id}` | `update_deck` | write | no |
| Delete a deck | `DELETE /decks/{id}` | `delete_deck` (shows the deck first; deletes only with `confirm`) | write | no |
| Load a deck from an Archidekt link | `GET /archidekt/decks/{id}` | `get_archidekt_deck` | read | **partly**: saving it needs the model to convert JSON to text (#96 `import_deck_from_link`) |
| Check a decklist against the collection | `POST /decks/coverage`, `/decks/parse` | `check_decklist`, `parse_decklist` | read | no |
| Stats, legality, upgrades, validate, combos, shopping list | `POST /decks/stats` ... | `deck_stats`, `deck_legality`, `find_upgrades`, `validate_deck_changes`, `find_combos`, `shopping_list` | read | **partly**: text only, no `deck_id` (#96) |

## Sharing

| Action | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|
| See what others shared with me, open a shared deck | `GET /shared`, `GET /shared/{id}/deck` | `list_shared_with_me`, `get_shared_deck` | read | no |
| List my shares | `GET /shares` | `list_my_shares` | read | no |
| Share my collection or a deck | `POST /shares` | none, on purpose | account | kept with the person: an invite link hands your data to someone, so an injected instruction must not be able to create one |
| Accept an invitation | `POST /shares/accept` | `accept_share` | write | no |
| Stop sharing | `DELETE /shares/{id}` | `stop_sharing` (shows shares first; ends one only with `confirm`) | write | no |

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

- ~~G1 Large files~~: a one-time upload link (`start_collection_upload`), the file staged and previewed (`get_staged_upload`), imported only with `confirm` (`confirm_staged_upload`); `vault/uploads.py`.
- ~~G2 Import preview~~: `import_collection_csv` previews (`POST /imports/preview`, writes nothing) unless `confirm` is true.
- ~~G3 One import's changes~~: `get_import`.
- ~~G4 Deck overlap~~: `get_deck_overlap` ("which of my decks share cards, and am I short?"); the graph's clusters stay visual.
- ~~G5 Delete a deck~~: `delete_deck`, preview then `confirm`.
- ~~G6 Sharing~~: `list_my_shares`, `accept_share`, `stop_sharing` (confirm). Creating a share stays with the person (see the table).
- Deck items (name search, import from link, `deck_id` analysis) are in #96, owned by the deck work (#88).
