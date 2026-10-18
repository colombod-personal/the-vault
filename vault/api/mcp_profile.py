"""A smaller tool list for hosts that give a plugin's MCP tools a size budget (the Codex family: ChatGPT desktop, Codex CLI).

Measured on 2026-10-11 (vault-wxy.5): the Vault answers ``tools/list`` with all 81 tools in one reply, and Codex then
keeps only an alphabetical prefix that fits a budget shared by the plugin (30 tools in the owner's app, 62 in a clean
home), silently dropping the rest: ``list_decks``, ``whoami``, ``search_cards`` and the rules tools were past the cut, so
the assistant said "the deck-list tool isn't available" and fell back to the browser.

A client whose own ``initialize`` names it (``codex-mcp-client``) therefore gets this profile:

* the **core** tools: what a player's first questions need, with one-sentence descriptions (the full text is one
  ``vault_tools`` call away). A write tool is listed only to a token that may write (the exceptions are
  ``start_collection_upload`` and ``import_collection_file``, which stage a file and import nothing, and ``sign_out``, which ends
  only the caller's own connection: all are listed to every token), and keeps its own approval hints;
* ``vault_tools``: lists every other tool with a one-line summary, or returns one tool's full description and schema;
* ``vault_call``: runs any other **read** tool by name; ``vault_write``: runs any other non-destructive **write** tool by
  name. Two tools, so a host's "always allow" for reading never pre-approves changing anything. Both go through exactly the
  same path as a direct call (same argument checks, same API, same token scopes, same preview-then-confirm).

What the dispatchers refuse (a policy of presentation, not enforcement: the REST API is the enforcement, and a direct
``tools/call`` by name behaves as it always did): tools that delete or revoke (a host that sees one tool called
``vault_write`` cannot show that tool's own warning, so those stay in the web app), tools that are listed directly (call
them by name), and the dispatchers themselves.

Every other client keeps the full list, unchanged. The profile lives in the signed ``Mcp-Session-Id`` (``mcp_session``),
so nothing is stored; it changes which list is shown, never what a token may do.
"""

from __future__ import annotations

import re
from typing import Any

# Matched against clientInfo.name exactly: the CLI and the desktop app both send it.
COMPACT_CLIENTS = ("codex-mcp-client",)

# The first journeys: who am I, my decks, a deck's numbers and legality, the council, the collection, a card, a rule, what
# to buy, saving a deck from a link and refreshing it, and "I bought a Sol Ring" (the preview, its confirmation and its undo) with the one-time
# upload's confirmation. Listed directly so the host keeps showing each one's own approval hint. Order is the order listed.
CORE = (
    "whoami",
    "list_decks", "get_deck", "deck_stats", "deck_legality", "validate_deck_changes", "find_upgrades",
    "council_brief", "simulate_draws", "find_combos", "shopping_list",
    "get_collection_summary", "search_cards", "check_decklist",
    "get_card_oracle", "get_rule", "search_rules", "verify_citation",
    "import_deck_from_link", "refresh_deck", "import_collection_file", "start_collection_upload",
    "sign_out",
    "update_owned_cards", "confirm_owned_cards_update", "undo_owned_cards_update", "confirm_staged_upload",
)

# One or two whole sentences for each core tool, written by hand (the first sentence of the full text is often cut mid-clause).
COMPACT = {
    "whoami": "Who the person is connected as, the sign-in's scopes and the data versions the Vault holds. Use it to check the connection.",
    "list_decks": "The person's saved decks: name, format, commander(s), card count and source. Pass `query` with words from a name to find one.",
    "get_deck": "One saved deck by `deck_id`: overview, what the person owns and what is missing with dated prices, and the decklist.",
    "deck_stats": "A deck's counts, mana curve, colours, roles, Game Changers, cost, lookouts (cards that can turn against it) and Commander bracket floor.",
    "deck_legality": "Whether a deck (`deck_id` or pasted `text`) is legal in a format: banned cards, copy limits, size, colour identity; says what it did not check.",
    "validate_deck_changes": "Checks proposed cuts and adds against a deck: cards exist, are legal and in colours, the result is legal, and the cost is within budget.",
    "find_upgrades": "Legal upgrades within a budget for the roles a deck is short of, plus cut candidates. Check a chosen change with validate_deck_changes.",
    "council_brief": "The expert council's panel and procedure for a deck review, rules dispute or synergy question, with each member's brief and the deck's facts.",
    "simulate_draws": "How a deck's curve plays: sample games and odds over many (land drops, mana by turn, cards in hand); lists what it does not model.",
    "find_combos": "Combos in a decklist and combos one card short, from Commander Spellbook and attributed; finding none does not mean the deck has none.",
    "shopping_list": "The cards of a deck the person does not own, with dated Scryfall prices, as paste-ready text for a store's own list tool.",
    "get_collection_summary": "Totals for the collection: copies, printings, sets, market value, amount paid, price date and breakdowns; `bucket` or `tag` narrows it.",
    "search_cards": "Finds printings in the collection with name, set, finish, condition, quantity, price, value and card data. Paged.",
    "check_decklist": "Which cards of a pasted decklist the person owns, partly owns or is missing. Accepts Archidekt, Moxfield, Arena and MTGO text.",
    "get_card_oracle": "A card's official Oracle text, types, legalities and tags, by exact name or Oracle id, with its recorded legality changes.",
    "get_rule": "One rule by number (e.g. 613.1a) or glossary term, with its place in the rules; the only source for a rule's current wording.",
    "search_rules": "Searches the Comprehensive Rules for a topic; best matches first, at most 10, each with its number and the edition.",
    "verify_citation": "Checks that a quote is verbatim in the rule, Oracle text or ruling it is attributed to; a failed check returns the true text.",
    "import_deck_from_link": "Saves a public Archidekt deck from its link, with sections, format and the author's credit. A saved link is left unchanged unless `update` is true.",
    "import_collection_file": "Stages the attached collection file and previews what importing would change; imports nothing. Pass `file`, or call with {} for how to send it in parts. Any sign-in may call it.",
    "start_collection_upload": "A one-hour link to upload a collection file in the browser, only when the app cannot hand over the attached file. Any sign-in may make it.",
    "refresh_deck": "Compares a saved deck with its Archidekt link; with the preview's fingerprint and `confirm` it replaces the saved list with the source's.",
    "update_owned_cards": "Previews small edits to the cards the person owns (bought, sold, traded): add, remove or set copies. Changes nothing; returns a confirmation.",
    "confirm_owned_cards_update": "Applies the change previewed by update_owned_cards, with the same lines and that preview's confirmation. Recorded in history and undoable.",
    "undo_owned_cards_update": "Reverts the latest assistant change if nothing else changed the collection; shows what it would change first.",
    "confirm_staged_upload": "Imports the file uploaded through start_collection_upload: a preview first, then confirm with the preview's `content_hash`.",
    "sign_out": "Signs this app out of the Vault; its next call asks to sign in again, as another account if wanted. Needs `confirm` true; without it, only shows the account and app.",
}
assert set(COMPACT) == set(CORE), "every core tool has a hand-written description"

# Tools that take a file as an object or as text in parts: the compact list (the coding apps, which are given a path and no file object)
# does not require the object; the full list still does (ChatGPT fills `file` from the attachment only when the schema asks for it).
PARTS_TOOLS = {"import_collection_file": ("file",)}

# Parameters whose meaning the generic cut loses (a shortened text must keep what a wrong value would cost).
PARAM_TEXT = {
    ("confirm_staged_upload", "conflicts"): "For cards changed in both the app and the Vault: `vault` keeps the Vault's edit (the default), `app` takes the app's value.",
    ("confirm_staged_upload", "replace_everything"): "Replace the whole collection with the file, discarding edits made in the Vault (the preview says how many).",
    ("shopping_list", "condition"): "The worst condition accepted; it changes no price, because Scryfall's prices are not per condition.",
    ("verify_citation", "version"): "Rules edition (YYYY-MM-DD); only the current one can be read. Leave it out or give 'latest'.",
}

# Added to the server's instructions for a compact client only: the full list has the tools the instructions name.
INSTRUCTIONS_NOTE = (
    "\n\nThis connection lists a short set of tools. A tool these instructions name that is not in the list (for example "
    "get_rulings, list_buckets, tag_cards) is reached with vault_tools (look it up) and then vault_call (a tool that only reads) or "
    "vault_write (a tool that changes something), each by name with its arguments. Tools that delete or revoke are done by the "
    "person in the Vault's web app; sign_out, which ends only this app's own connection, is listed and called by name."
)

DESC_CORE = 150   # characters of a core tool's description (for a tool with no hand-written one)
DESC_PROP = 70    # characters of a parameter's description
DESC_LISTED = 110  # characters of a tool's one-line summary in vault_tools

LOOKUP, CALL, WRITE = "vault_tools", "vault_call", "vault_write"
DISPATCHERS = (LOOKUP, CALL, WRITE)

# A full stop after these does not end a sentence.
_ABBREVIATIONS = ("e.g", "i.e", "etc", "vs", "cf", "approx", "incl")


def is_compact_client(name: object) -> bool:
    return isinstance(name, str) and name in COMPACT_CLIENTS


def first_sentence(text: str, limit: int) -> str:
    """The first sentence of ``text`` (not ended by an abbreviation such as "e.g." or inside an open parenthesis), cut at
    a word boundary with an ellipsis when it is longer than ``limit``."""
    flat = " ".join((text or "").split())
    end = len(flat)
    for found in re.finditer(r"[.!?](?:\s|$)", flat):
        before = flat[: found.start() + 1]
        word = re.split(r"[\s(]", before)[-1].lower().rstrip(".")
        if word in _ABBREVIATIONS or before.count("(") > before.count(")"):
            continue
        end = found.start() + 1
        break
    sentence = flat[:end]
    if len(sentence) <= limit:
        return sentence
    return _cut(sentence, limit) + "…"


def _cut(sentence: str, limit: int) -> str:
    """The longest whole-clause prefix of ``sentence`` within ``limit``: ends before a comma or semicolon outside
    parentheses, else at a word boundary, and never inside an open parenthesis."""
    window = sentence[: limit - 1]
    depth, clause, word = 0, 0, 0
    for i, ch in enumerate(window):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in ",;:":
            clause = i
        elif depth == 0 and ch == " ":
            word = i
    cut = window[: clause or word or len(window)]
    if cut.count("(") > cut.count(")"):  # still inside a parenthesis: stop before it
        cut = cut[: cut.rfind("(")]
    return cut.rstrip(" ,;:(")


def _shrink(node: Any) -> Any:
    """A copy of a JSON schema with every ``description`` shortened to its first sentence."""
    if isinstance(node, dict):
        return {k: (first_sentence(v, DESC_PROP) if k == "description" and isinstance(v, str) else _shrink(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_shrink(v) for v in node]
    return node


def compact_schema(tool, ui: bool = False) -> dict:
    """``tool.schema()`` with a one-sentence description and short parameter descriptions. Same names, types, bounds and
    annotations, so a call built from it is checked exactly as before."""
    full = tool.schema(ui=ui)
    schema = _shrink(full["inputSchema"])
    if tool.name in PARTS_TOOLS:  # a coding app has no file object to pass: the parts are as valid as `file` (vault.file_parts)
        schema["required"] = [name for name in schema.get("required", []) if name not in PARTS_TOOLS[tool.name]]
    for (name, prop), text in PARAM_TEXT.items():
        if name == tool.name and prop in schema.get("properties", {}):
            schema["properties"][prop]["description"] = text
    return {**full, "description": COMPACT.get(tool.name) or first_sentence(tool.description, DESC_CORE), "inputSchema": schema}


def _runner(name: str, title: str, description: str, reads: bool) -> dict:
    return {"name": name, "title": title, "description": description,
            "inputSchema": {"type": "object", "additionalProperties": False, "required": ["name"],
                            "properties": {"name": {"type": "string", "maxLength": 80, "description": f"The tool's name (see {LOOKUP})"},
                                           "arguments": {"type": "object", "description": "The tool's arguments, as vault_tools shows them"}}},
            # vault_write can run a tool that replaces (update_deck, set_*_metadata) or merges (rename_tag): the host asks first,
            # as it would for those tools listed directly.
            "annotations": {"title": title, "readOnlyHint": reads, "destructiveHint": not reads, "openWorldHint": False}}


def dispatcher_tools(scopes=("read", "write")) -> list[dict]:
    """The tools that reach everything outside the core list: the lookup, the reader and (for a token that may write)
    the writer."""
    out = [
        {"name": LOOKUP, "title": "Find a Vault tool",
         "description": "Lists the Vault's other tools (name and one line each), or with `name` returns that tool's full "
                        "description and arguments. Read a tool's full text here before calling it.",
         "inputSchema": {"type": "object", "additionalProperties": False, "required": [],
                         "properties": {"name": {"type": "string", "maxLength": 80, "description": "One tool's name: its full description and arguments"},
                                        "query": {"type": "string", "maxLength": 80, "description": "Only tools whose name or summary has these words"}}},
         "annotations": {"title": "Find a Vault tool", "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
        _runner(CALL, "Run a Vault tool that reads",
                "Runs one of the Vault's other tools that only reads, by name, with its arguments, exactly as if it were listed.",
                reads=True),
    ]
    if "write" in scopes:
        out.append(_runner(WRITE, "Run a Vault tool that changes something",
                           "Runs one of the Vault's other tools that changes the person's data, by name, with its arguments "
                           "(previews and confirmations work as usual). Not for tools that delete or revoke.", reads=False))
    return out


def tool_list(tools_by_name: dict, scopes, ui: bool = False) -> list[dict]:
    """What a compact client's ``tools/list`` returns: the core tools it is allowed to see, then the dispatcher tools."""
    listed = [compact_schema(tools_by_name[n], ui) for n in CORE
              if n in tools_by_name and tools_by_name[n].listed_for(set(scopes))]
    return listed + dispatcher_tools(scopes)


def changes_data(tool) -> bool:
    """Whether running the tool changes the person's data: a write tool, except one any connection may run (the upload link)."""
    return tool.write and not tool.any_scope


def refusal(tool, via: str, scopes=("read", "write")) -> str | None:
    """Why ``via`` (vault_call or vault_write) will not run ``tool``, or None when it will. ``tool`` may be None."""
    if tool is None or tool.name in DISPATCHERS:
        return f"Unknown tool ({LOOKUP} lists them)"
    if tool.name in CORE:
        if not tool.listed_for(set(scopes)):
            return f"{tool.name} needs a sign-in that may write"
        return f"{tool.name} is listed directly: call it by its own name"
    if tool.destructive:
        return f"{tool.name} deletes or revokes something: that is done in the Vault's web app, not through {via}"
    if via == CALL and changes_data(tool):
        if "write" not in scopes:
            return f"{tool.name} changes something and needs a sign-in that may write"
        return f"{tool.name} changes something: run it with {WRITE}"
    if via == WRITE and not changes_data(tool):
        return f"{tool.name} only reads: run it with {CALL}"
    return None


def lookup(tools: list, scopes, args: dict) -> dict:
    """The answer of ``vault_tools``: the tools outside the core list (those the token's scopes allow), or one tool in full."""
    core = set(CORE)
    name = args.get("name")
    if name:
        found = next((t for t in tools if t.name == name), None)
        if found is None or not found.listed_for(set(scopes)):
            return {"error": f"No tool called {name} for this sign-in", "hint": f"{LOOKUP} without a name lists them"}
        out = found.schema(ui=False)
        if found.name in core:
            out["run_with"] = "listed directly: call it by its own name"
        elif found.destructive:
            out["run_with"] = None
            out["note"] = f"Deletes or revokes something: done in the Vault's web app, not through {CALL} or {WRITE}."
        else:
            out["run_with"] = WRITE if changes_data(found) else CALL
        return out
    query = str(args.get("query") or "").lower().split()
    rows = []
    for t in tools:
        if t.name in core or not t.listed_for(set(scopes)):
            continue
        summary = first_sentence(t.description, DESC_LISTED)
        if query and not all(w in f"{t.name} {summary}".lower() for w in query):
            continue
        rows.append({"name": t.name, "summary": summary, "writes": changes_data(t),
                     "run_with": None if t.destructive else (WRITE if changes_data(t) else CALL)})
    return {"count": len(rows), "tools": rows,
            "how": f"{LOOKUP} with name for one tool's full text; {CALL} runs a tool that reads, {WRITE} one that changes "
                   f"something, each with name and arguments. A tool with run_with null is done in the web app. The core "
                   f"tools ({', '.join(CORE[:4])}, ...) are listed directly."}
