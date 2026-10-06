# MCP Apps views

The Vault can show small interactive pages inside the chat of hosts that support
[MCP Apps](https://github.com/modelcontextprotocol/ext-apps) (the `io.modelcontextprotocol/ui` extension,
stable 2026-01-26): the host fetches a `ui://` page, shows it in a sandboxed frame next to the tool's result,
and the page can call the Vault's tools back through the host. Hosts without support ignore all of this and
show the tool's normal text answer, which has the same information.

| View (`ui://vault/...`) | Tool | What it shows |
|---|---|---|
| `card` | `get_card_oracle` | The card image (whole, artist credited), Oracle text, price (dated), legalities, Tagger tags labelled as opinion, rulings on request (calls `get_rulings`) |
| `deck` | `deck_stats` | Mana curve, roles, color identity, estimated cost, unknown cards, and a legality check (calls `deck_legality`) |
| `upgrades` | `find_upgrades` | Candidates per role with prices, cut candidates, a budget slider that re-asks for candidates, and "Check this plan" (calls `validate_deck_changes`) |
| `steps` | `present_steps` | A numbered walkthrough; each step's cited rules expand to their verbatim text, unknown rule numbers are flagged |
| `shopping` | `shopping_list` | The list with dated prices, a copy button, and the notes about stores |

## How it works here

- `tools/list` gives each such tool `_meta.ui = {resourceUri, visibility: ["model", "app"]}`. The server is
  stateless, so it cannot see which hosts advertised the extension; hosts without it ignore `_meta`.
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

- All five pages parse as JavaScript (`node --check`, in the tests) and pass the safety checks above.
- A small test host (a page that speaks the host side of the protocol, in a sandboxed frame) drove every view in a
  real browser: the handshake, tool-input and tool-result, size reports, `tools/call` for the legality check,
  the plan validator and rulings, light and dark themes, and the real Scryfall image loading.
- The wire format follows the published specification (ext-apps `2026-01-26`).

Checked in a real host so far: Claude on the web (below). The specification and this implementation were read, not run against ChatGPT,
VS Code, Cursor or Goose. Their rendering, CSP handling and quirks may differ; the matrix below stays empty until
someone has opened a view in each.

| Host | View renders | Tool calls from the view | Notes |
|---|---|---|---|
| Claude (web, claude.ai) | **yes** (`shopping`, `deck`: 2026-10-06) | not tried | Views appear inline under the tool call: the shopping list ('You own everything in this list'), and the deck panel with the mana curve chart, roles table, provenance and Fan Content notice. `card`, `upgrades`, `steps`, phone layout and Claude desktop not yet checked (#56) |
| ChatGPT | not tried | not tried | may expect extra `_meta` keys; the OpenAI Apps SDK documentation is the reference |
| VS Code | not tried | not tried | |
| Cursor | not tried | not tried | |
| Goose | not tried | not tried | |
