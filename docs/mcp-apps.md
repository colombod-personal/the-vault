# MCP Apps views

The Vault can show small interactive pages inside the chat of hosts that support
[MCP Apps](https://github.com/modelcontextprotocol/ext-apps) (the `io.modelcontextprotocol/ui` extension,
stable 2026-01-26): the host fetches a `ui://` page, shows it in a sandboxed frame next to the tool's result,
and the page can call the Vault's tools back through the host. Hosts without support ignore all of this and
show the tool's normal text answer, which has the same information.

| View (`ui://vault/...`) | Tool | What it shows |
|---|---|---|
| `card` | `get_card_oracle` | The card image (whole, artist credited), Oracle text, price (dated), legalities, Tagger tags labelled as opinion, rulings on request (calls `get_rulings`) |
| `deck` | `deck_stats` | Mana curve, roles, color identity, estimated cost, unknown cards. **Legality is shown on opening** (calls `deck_legality` for the deck's own format, or asks for one; all 20 formats the server checks), and a "Find combos" button (calls `find_combos`, attributed to Commander Spellbook). Works for a saved deck (`deck_id`) and for pasted text |
| `combos` | `find_combos` | Combos in the deck and combos one card short, each with its cards, what it produces, Commander Spellbook's description and a link to its page; the tool's limits ("finding none does not mean there are no infinite combos") are shown |
| `upgrades` | `find_upgrades` | Candidates per role with prices; each add is **paired with a cut** (the least-played card is suggested, changeable) and shows the **price difference** of the swap; "Check this plan" calls `validate_deck_changes` and shows the net price change and the deck's cost before and after; the **budget slider re-calls the validator** for the plan and `find_upgrades` for new candidates (the plan is kept). Works for a saved deck (`deck_id`) and for pasted text |
| `steps` | `present_steps` | A numbered walkthrough; each step's cited rules expand to their verbatim text, unknown rule numbers are flagged |
| `shopping` | `shopping_list` | The list with dated prices, a copy button, **a download as a text file** (a Blob link made in the page; if the host blocks downloads the text stays on screen and the page says so), a chooser when the tool returns several store formats, and the notes about stores |
| `printings` | `update_owned_cards`, `show_owned_printings` | Printing pictures and the choice of printing (#205) |

## How it works here

- `tools/list` gives each such tool `_meta.ui = {resourceUri, visibility: ["model", "app"]}` **only to a client that
  advertised the extension** (#50, #51). The specification says clients advertise it in `initialize`
  (`capabilities.extensions["io.modelcontextprotocol/ui"] = {"mimeTypes": ["text/html;profile=mcp-app"]}`) and that
  servers "SHOULD check client capabilities" and keep a text fallback. The server is stateless, so what the client said
  comes back in a signed `Mcp-Session-Id` returned by `initialize` (`vault/api/mcp_session.py`: a version, one bit, a
  random part and an HMAC under the server secret; no person, no token, nothing stored). Streamable HTTP clients send
  it on every later request. **Fail open:** a request with no session id (a client that ignores ids, or a connection from
  before this existed) or an id that does not verify is treated as "unknown" and gets the view links exactly as before. Only
  an id this server signed, saying the client connected without the extension, hides them. A host without the
  extension gets the same tools and the same text answers, only without `_meta.ui`. `initialize` logs one line per
  connection (`MCP initialize from <client>/<version>: MCP Apps extension advertised|not advertised`: name and version only), so the
  log shows what each real host says.
- **Risk to watch after a deploy:** a host that renders the views but does not advertise the extension would lose them after
  reconnecting. The matrix below says which hosts were seen to render; re-check each after the release (reconnect the
  connector first: hosts keep the tool list from when they connected) and read the log line above.
- `resources/list` and `resources/read` serve the pages (`text/html;profile=mcp-app`) with `_meta.ui.csp`:
  no network at all, except `resourceDomains: ["https://cards.scryfall.io"]` for the card image. Nothing else
  is loaded: no scripts, fonts or stylesheets from elsewhere.
- Each page does the handshake (`ui/initialize`, then `ui/notifications/initialized`), receives
  `ui/notifications/tool-input` and `ui/notifications/tool-result`, reports its size
  (`ui/notifications/size-changed`), applies the host's theme, calls tools with `tools/call` and opens links
  with `ui/open-link`. It only listens to messages from its parent frame.
- The pages are in `vault/api/mcp_ui.py` (plain HTML and JS, no build step, about 4 KB of shared code plus
  each view).

## Rules the views keep

- **Provenance is always shown.** Every view ends with a footer listing the result's `provenance` blocks (source,
  origin, date, version, link), what the Vault computed and from which inputs, the Fan Content notice when a
  block carries it, and that the Vault is not produced or endorsed by Scryfall, Wizards or the sources.
- **Text only.** Data from tool results is inserted with `textContent`; no `innerHTML`, `eval`, `fetch`,
  storage or navigation (`tests/test_mcp_apps.py` fails on any of them). Links go through the host.
- **Card images** are Scryfall's own link, shown whole with the artist and "Image: Scryfall"; never cropped.
- **Nothing is invented in the UI.** Numbers come from tool output; prices carry their date; roles are
  labelled as a community's opinion; popularity is not presented as power.

## Checked, and not checked

Checked on 2026-10-04:

- All seven pages parse as JavaScript (`node --check`, in the tests) and pass the safety checks above.
- Each view is also **run** (`tests/test_mcp_apps_run.py`, `tests/mcp_view_harness.js`): its script executes under node
  against a small DOM stub and a fake host, on what the real tools answered (a saved deck by `deck_id` and a pasted list), and
  the tests read what a person would see and which tools the view called with which arguments (legality on opening, combos,
  pairs and deltas, the budget slider re-calling `validate_deck_changes` and `find_upgrades`, copy and download). A stub is not a
  browser: no layout, no CSS, no real sandbox; the browser run below is the check for those.
- The capability check is tested with the client twin (`twins/mcp_client.py`, `tests/test_mcp_apps_capability.py`):
  advertised, not advertised, no session id, forged ids, odd capability shapes, batches. The twin sends the payload the
  specification gives; **the payloads claude.ai and ChatGPT really send have not been captured yet** (the log line above
  will show them), so the twin is the specification's client, not yet a recording of a real one.
- Export (#55): the specification lists no download request and does not mention `allow-downloads` for the host's frame, so the
  page makes a Blob link itself; whether a given host's sandbox allows it is not known until tried in each host.
- A small test host (a page that speaks the host side of the protocol, in a sandboxed frame) drove every view in a
  real browser: the handshake, tool-input and tool-result, size reports, `tools/call` for the legality check,
  the plan validator and rulings, light and dark themes, and the real Scryfall image loading.
- The wire format follows the published specification (ext-apps `2026-01-26`).

Checked in a real host so far: Claude on the web (below). The specification and this implementation were read, not run against ChatGPT,
VS Code, Cursor or Goose. Their rendering, CSP handling and quirks may differ; the matrix below stays empty until
someone has opened a view in each.

| Host | View renders | Tool calls from the view | Notes |
|---|---|---|---|
| Claude (web, claude.ai) | **yes**: `shopping`, `deck` (2026-10-06); `deck`, `card`, `upgrades` again on production with the demo account on 2026-10-08 | **yes**: in `upgrades`, moving the budget slider ($15 to $50) and ticking a card gave a plan (add Mind Stone, cut Frenzy Sliver), and 'Check this plan' ran the Vault's validation from inside the view: 'Valid. The adds cost $0.21 of $50.00; the deck has 100 cards', '$674.57 before, $674.48 after', with its 'Computed by The Vault' line (2026-10-08) | Views appear inline under the tool call. 2026-10-08, https://claude.ai/chat/92c6b5c3-211f-412c-b9d9-078f43344b26: the deck panel (header 'Sliver Swarm (demo), Commander, Commander: Sliver Overlord, 100 cards, colour identity WUBRG', statistics, mana curve, roles marked as a community's opinion), the card panel (picture, 'Illustrated by Chris Rahn. Image: Scryfall.', a dated price 'not a store's price today', legal-in chips, Tagger tags 'community opinion, not rules', a rulings button, a Scryfall link, the source footer), and the upgrade view. Screenshots: `docs/screenshots/claude-deck-dashboard-2026-10-08.jpg`, `claude-card-panel-2026-10-08.jpg`, `claude-upgrades-2026-10-08.jpg`, `claude-upgrades-check-plan-2026-10-08.jpg`. `steps`, the phone layout and Claude desktop are not yet checked (#56) |
| Claude: the `printings` view (#205) | **yes** (2026-10-08) | **yes**: tapping a printing composes the message 'For Sol Ring: it's the Commander 2021 #263, non-foil.' in the chat box (claude.ai shows its own caution banner for prompts a view composes) and sending it ran the preview view | https://claude.ai/chat/7f9966b3-1927-42ed-9e9b-393a669627e5: twelve printings with pictures, artist credit and 'you own 1 non-foil' on the owned one, listed first; then 'Ready to apply: nothing has changed yet'. Screenshots: `claude-printing-picker-2026-10-08.jpg`, `claude-printing-picker-all-2026-10-08.jpg`, `claude-printing-preview-2026-10-08.jpg` |
| ChatGPT (web) | not confirmed | not tried | Connected 2026-10-06 (#77). Asked for pictures, ChatGPT showed card images in its own gallery (cropped, no artist credit) rather than, as far as seen, the Vault's view. Whether it renders `ui://vault/*` views is the open question (#85) |
| VS Code | not tried | not tried | |
| Cursor | not tried | not tried | |
| Goose | not tried | not tried | |
