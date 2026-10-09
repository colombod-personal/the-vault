# What a person can do in the web app, and through their AI assistant

Owner's rule (2026-10-04): everything a user can do in the web app must also be possible through the AI integrations,
except account-level actions that are kept out on purpose. This table maps each action to its REST route and MCP
tool. Built from the routes in `vault/api/v1.py`, `vault/auth.py`, `vault/oauth_routes.py` and the tools in
`vault/api/mcp.py` and `vault/api/mcp_catalog.py` (issue #100). Gaps are tracked in #101.

**UI place** says where in the web app the action lives, read from the front end (`public/app.jsx`, `public/views/*.jsx`):
the top-bar tabs (Vault, Browse, Sets, Decks, Lab, Graph), the top-bar buttons (Import CSV, Help, your name = the Account panel), and
the label of the control, written in “quotes” exactly as the app shows it. `tests/test_ai_parity_doc.py` checks that every
quoted label exists in the front end, that every MCP tool is named here, and that the table has this column. Where the web app has
no place for an action it says **none**.

Scopes: **read**, **write** (changes the collection or decks; off by default on the consent screen), **account**
(never given to AI apps).

## Collection

| Action (web app) | UI place | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|---|
| See summary, value, gains | “Vault” tab (“Your collection, at a glance.”); the value figure opens “Collection value over time” | `GET /collection`, `/collection/stats`, `/collection/valuation` | `get_collection_summary`, `get_collection_stats`, `get_valuation` | read | no |
| Breakdowns by colour, type, mana value (Insights) | “Lab” tab (“What your numbers reveal.”: “Color, type and curve”, “Mana value distribution”) | `GET /collection/breakdowns`, `/collection/names` | `get_collection_breakdowns` | read | no |
| Value history, acquisition timeline | “Vault” tab (“Acquisition timeline”); “Collection value over time” page (“Market value, day by day”); “Lab” tab (“Acquisition spend by month”) | `GET /collection/history`, `/collection/timeline` | `get_value_history`, `get_acquisition_timeline` | read | no |
| See which copies are spare (beyond your saved decks) and what they are worth | none yet: the Lab page is still to be built (#164) | `GET /collection/spare`, `/collection/spare/printings?name=` | `list_spare_copies` (`name` for one card's spare printings) | read | yes |
| See profit and loss by holding: the winners and the losers | none yet: the Lab page is still to be built (#164); the “Lab” tab shows an older list (“Profit and loss”) | `GET /collection/pnl?side=winners\|losers` | `get_collection_pnl` | read | yes |
| Browse and filter cards | “Browse” tab (“Every printing you own.”: search, set, “All printings”, card type and mana value filters, “Table” / “Grid”); clicking a card opens the card drawer | `GET /collection/cards`, `/collection/cards/{id}` | `search_cards`, `list_card_names`, `get_card` | read | no |
| See the places your copies live in (buckets) and what is in one | none yet: the web app has no buckets view (#125) | `GET /collection/buckets`, `/collection/cards?bucket=`, `/collection/export.csv?bucket=` | `list_buckets`, `search_cards` (`bucket`) | read | yes |
| Make, rename or delete a bucket | none yet: the web app has no buckets view (#125) | `POST /collection/buckets`, `PATCH` and `DELETE /collection/buckets/{id}` | `create_bucket`, `rename_bucket`, `delete_bucket` (an empty bucket; asks first) | write | yes |
| Move copies between buckets | none yet: the web app has no buckets view (#125) | `POST /collection/buckets/{id}/move` | `move_cards` (more than 10 copies is shown first) | write | yes |
| See the tags and the cards that have one | none yet: the web app has no tags view (#128) | `GET /collection/tags`, `/collection/tags/{tag}`, `/collection/cards?tag=` (each card lists its `tags`) | `list_tags`, `search_cards` (`tag`) | read | yes |
| Keep notes on a card or bucket (metadata) | none yet: the web app has no tags or notes view (#128) | `GET`, `PUT` and `DELETE /collection/cards/{id}/metadata` and `/collection/buckets/{id}/metadata` | `get_card_metadata`, `set_card_metadata`, `get_bucket_metadata`, `set_bucket_metadata` (each writer owns its namespace) | read, write | yes |
| Tag or untag cards, rename or delete a tag | none yet: the web app has no tags view (#128) | `POST /collection/tags/{tag}/cards`, `POST /collection/tags/{tag}/cards/remove`, `PATCH` and `DELETE /collection/tags/{tag}` | `tag_cards`, `untag_cards` (more than 25 cards is shown first), `rename_tag`, `delete_tag` (asks first); an assistant's tags carry its app's name | write | yes |
| Sets view | “Sets” tab (“Holdings by expansion”); a set opens its own page | `GET /collection/sets`, `/catalog/sets` | `list_sets` | read | no |
| Refresh prices | “Vault” tab, “Update now” button (it also runs by itself after an import and on a new day) | `POST /collection/refresh` | `refresh_prices` | write | no |
| **Upload a collection file (CSV)** | Top bar “Import CSV” button (first run: “Choose CSV file”) | `POST /imports` (multipart) | `import_collection_csv` (shows what would change; replaces only with `confirm`); big files: `start_collection_upload`, `get_staged_upload`, `confirm_staged_upload` | write | no |
| Refresh a saved deck from its stored link | “Decks” tab, a saved deck: “Refresh” (asks Archidekt again), then “Update saved copy” | `POST /decks/{id}/refresh` (preview; `confirm` + `fingerprint` replaces) | `refresh_deck` (Archidekt links; Moxfield and others: paste a fresh export) | write | no (the web app does it with “Refresh” and “Update saved copy”, a `PUT`, not this preview route) |
| Expert council review (Commander expert, casual table, judge, devil's advocate, ...) | none: assistants only | `GET /council`, `/experts`, `/experts/{id}` | `council_brief`, `expert_brief` (the plugin's agents, for connectors) | read | partly: in one chat the views are not independent (plugin hosts run real agents) |
| See your decks at a glance: name, format, commander(s), card count, colour identity | “Decks” tab (“Your decks.”, one tile per deck); Account panel, “Saved decks” | `GET /decks?brief=true` (and every deck answer's `overview`) | `list_decks` (no card lines), `get_deck`; set a format with `save_deck` / `update_deck` | read / write | no |
| **Edit the cards owned** (bought, sold, traded) | none: the web app has no single-card edit (an import replaces the collection) | `POST /collection/changes/preview`, `/apply`, `/undo` | `update_owned_cards` (preview; asks for the printing), `confirm_owned_cards_update` (exactly the preview, after a yes), `undo_owned_cards_update`; docs/owned-cards-updates.md | write | no (the web UI has no single-card edit yet) |
| See the printings of a card you own, with pictures | “Browse” tab (“Printing” column, “Grid” layout); the card drawer | `GET /collection/printings?name=` | `show_owned_printings` (a picture view in hosts with MCP Apps) | read | no |
| See past imports and what changed | partly: the banner shown right after an import (“Imported”); the list of past imports is not shown | `GET /imports`, `GET /imports/{id}` | `list_imports`, `get_import` | read | no |
| Export the collection to another app | Account panel, “Move your collection” (one download per format) | `GET /collection/exports` | `list_export_formats` (download links) | read | no |
| Graph view (clusters, deck map) | “Graph” tab (“Your collection as a network.”: “Color galaxy”, “Set clusters”, “Deck map” ...) | computed in the browser from the collection | `get_deck_overlap` (cards shared by several decks, and copies short) | read | partly: the clusters themselves stay visual |

All collection tools also read a collection someone shared with you (`share_id`, routes under `/shared/{id}/collection`).

## Decks

| Action | UI place | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|---|
| List saved decks | “Decks” tab (“Your decks.”); Account panel, “Saved decks” (“Open”) | `GET /decks` | `list_decks` (a `query` finds a deck by name) | read | no |
| Open a deck with coverage | “Decks” tab, click a deck: “Cards” tab (“Missing”, “To finish”, what you own of each card) | `GET /decks/{id}` | `get_deck` (each card line has its own `price_date`; a deck from Archidekt has `credit.fetched_at`) | read | no |
| See what changed in a saved deck, and its earlier lists | A saved deck's page: the note “Changed since you last looked” (cards added and cut since you last opened it) and the “History” tab (each earlier list, kept when its cards change, the last 20, with “Show this list”) | `GET /decks/{id}/versions`, `GET /decks/{id}/versions/{version_id}`, `POST /decks/{id}/seen` (the open: records the list the page showed if its cards changed) | `get_deck` (its `last_change`: the latest list against the previous version); the full history and the since-last-looked note are the web app's | read | no (an assistant sees the latest change, not the whole list of versions) |
| Save a deck (text and its link) | A deck's page: “Save to your decks” | `POST /decks` | `save_deck` (with `source_url`) | write | no |
| Edit a deck | A saved deck's page: “Update saved copy” (re-saves the list as loaded; there is no list editor) | `PUT /decks/{id}` | `update_deck` | write | no |
| Delete a deck | A saved deck's page: “Remove”; Account panel, “Saved decks” (“Delete”) | `DELETE /decks/{id}` | `delete_deck` (shows the deck first; deletes only with `confirm`) | write | no |
| Load a deck from an Archidekt link | “Decks” tab: “From a link”, then “Open deck” | `GET /archidekt/decks/{id}` | `get_archidekt_deck`; `import_deck_from_link` saves it | read / write | no |
| Check a decklist against the collection | “Decks” tab: “Paste a list”, then “Open list” (“Cards” tab) | `POST /decks/coverage`, `/decks/parse` | `check_decklist`, `parse_decklist` | read | no |
| Stats, legality, upgrades, combos | A deck's page tabs: “Stats”, “Legality”, “Upgrades” (“Find upgrades”), “Combos” | `POST /decks/stats`, `/legality`, `/upgrades`, `/validate-changes`, `/combos` | `deck_stats`, `deck_legality`, `find_upgrades`, `validate_deck_changes`, `find_combos` (each takes a saved `deck_id` or pasted text) | read | no (`validate_deck_changes` has no web tab: it checks an assistant's proposed swaps) |
| Shopping list | A deck's page: “Buy list” tab (“Copy list”) | `POST /decks/shopping-list` | `shopping_list` (`format`: plain, cardkingdom, tcgplayer, cardmarket, csv; `finish`, `language`, `sets`, `condition`) | read | partly: the web tab copies the plain list; store formats and printing rules are assistant-side (#55) |
| Simulate the first turns of a deck (mana curve odds) | none yet: the web visual is #138 | `POST /decks/simulate` | `simulate_draws` (takes a saved `deck_id` or pasted text) | read | yes: no web view of it (#138) |
| Check the connection (who, scopes, data versions) | none: assistants only | `GET /agent/whoami`, `/catalog/status` | `whoami` | read | no |

## Sharing

| Action | UI place | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|---|
| See what others shared with me, open a shared deck | Account panel, “Shared with me” (“Open”); a banner says “Viewing” and “read-only” | `GET /shared`, `GET /shared/{id}/deck` | `list_shared_with_me`, `get_shared_deck` | read | no |
| List my shares | Account panel, “Share your collection” (the list of shares, “Revoke” / “Cancel”) | `GET /shares` | `list_my_shares` | read | no |
| Share my collection or a deck | Account panel: “Create invite link for my collection”; “Saved decks” (“Share”) | `POST /shares` | none, on purpose | account | kept with the person: an invite link hands your data to someone, so an injected instruction must not be able to create one |
| Accept an invitation | Open the invite link while signed in (a notice says what was shared; it is then under “Shared with me”) | `POST /shares/accept` | `accept_share` | write | no |
| Stop sharing | Account panel: “Revoke” (the owner) or “Leave” (the person it was shared with) | `DELETE /shares/{id}` | `stop_sharing` (shows shares first; ends one only with `confirm`) | write | no |

## Cards and rules (catalog)

| Action | UI place | REST route | MCP tool | Scope | Gap |
|---|---|---|---|---|---|
| Card details, rulings | The card drawer (click a card in Browse, the Vault tab or a deck): image, oracle text, artist credit; rulings are not shown in the web app | `/catalog/cards`, `/cards/{id}/rulings`, `/cards/lookup` | `get_card_oracle`, `get_rulings`, `lookup_cards` | read | partly: no rulings in the web app |
| Rules search, a rule | none: the web app has no rules view (“Help” explains the app, not the rules) | `/rules/search`, `/rules`, `/rules/term/{name}`, `/rules/{n}` | `search_rules`, `rules_outline`, `find_rules_term`, `get_rule`, `verify_citation`, `present_steps` | read | partly: assistants only |

## Account: kept out of AI apps on purpose

Shown on the consent screen as "It will never be able to". These stay web-only.

| Action | UI place | REST route | Why not through AI |
|---|---|---|---|
| Change my name | Account panel, “Profile” (“Save”) | `PATCH /me` | Account data (right to rectification is done by the person) |
| Export all my data | Account panel, “Your data” (“Download my data (.zip)”) | `GET /me/export` | Bulk personal data; the person downloads it themselves |
| Delete my account | Account panel, “Your data” (the delete button) | `DELETE /me` | Irreversible |
| Tokens, passkeys, sessions, connected apps, sign-in methods | Account panel: “Agents & API” (“Create token”), “Connected apps” (“Disconnect”), “Sign-in methods” (“Add a passkey”), “Sign out” | `/me/tokens`, `/me/passkeys`, `/me/sessions`, `/me/apps`, `/login/*` | Would let an app grant itself more access or lock the person out |

## Gaps (to close in #101)

- ~~G1 Large files~~: a one-time upload link (`start_collection_upload`), the file staged and previewed (`get_staged_upload`), imported only with `confirm` (`confirm_staged_upload`); `vault/uploads.py`.
- ~~G2 Import preview~~: `import_collection_csv` previews (`POST /imports/preview`, writes nothing) unless `confirm` is true.
- ~~G3 One import's changes~~: `get_import`.
- ~~G4 Deck overlap~~: `get_deck_overlap` ("which of my decks share cards, and am I short?"); the graph's clusters stay visual.
- ~~G5 Delete a deck~~: `delete_deck`, preview then `confirm`.
- ~~G6 Sharing~~: `list_my_shares`, `accept_share`, `stop_sharing` (confirm). Creating a share stays with the person (see the table).
- ~~Deck items (name search, import from link, `deck_id` analysis)~~: done in #96 (`list_decks` with `query`, `import_deck_from_link`, `deck_id` on every deck tool).
- G7 Web side of the simulation: `simulate_draws` has no place in the web app yet (#138). Store formats and printing rules of `shopping_list` have no control in the web app's “Buy list” tab (#55).
