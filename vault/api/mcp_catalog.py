"""MCP tools, prompts and instructions for the catalog and deck analysis (built on ``mcp.py``'s ``Tool``).

Design rules, from docs/compliance.md and the roadmap:
- Every answer carries ``provenance``; the model is told to repeat sources, never to present them as the Vault's.
- Nothing is dumped: tools return the card, rule or deck asked about, capped.
- Code checks what code can check (quotes, legality, budgets); the model reasons over the results.
"""

from __future__ import annotations

from .. import provenance as prov

V1 = "/api/v1"

GROUNDING = """\
Rules, cards and prices - how to answer:
- Never answer a rules or card question from memory. Call get_card_oracle (and get_rulings), then
  find the rules: find_rules_term for a named game term, search_rules for a question, rules_outline to
  browse; open each with get_rule and read its children, siblings and references. Say which rule number
  and which edition (version) you used. The rules are read live from Wizards of the Coast.
- Quote only text a tool returned. Before you present a quote as an official rule, ruling or card
  text, call verify_citation; if it fails, use the source_text it returns instead of your wording.
- If the sources do not settle a question, say you are not sure and point to the official rules or a judge.
- Every result has `provenance`. Pass it on: say the material is Scryfall's or Wizards' (the Fan Content
  notice in it must be repeated when you show rules or card text), and that figures marked `computed`
  were worked out by the Vault from the sources it lists. Never present source material as the Vault's own.
- The Vault is an unofficial fan project, not approved or endorsed by Wizards of the Coast, Scryfall, Archidekt or
  Commander Spellbook. Say so if asked who made a card, rule or price, and never speak for any of them.
- Roles ("ramp", "removal") are Scryfall Tagger tags, a community's opinion. Popularity (EDHREC rank)
  is not power. Prices are dated and come from Scryfall; they are not a store's price today.
- Budgets and legality are enforced by the Vault: before you present a list of changes, call
  validate_deck_changes and only present it if valid is true. The Vault never fills a store cart.
- Shops: you have no shop's price, stock or shipping. Never say which shop is cheapest, never call a
  price "current", never say anything goes into a cart. Give the dated Scryfall price and let the person
  compare shops themselves (a shop's own search link is fine).
- Numbers and verdicts in a deck review: report each number exactly as a tool returned it, with its name ("average
  mana value of the non-land cards: 3.5", "five mana by turn 5: 58% of simulated games"); never reword a figure into a
  different claim or add one a tool did not return. The Vault has no power score: do not call a deck weak, strong, low
  or high power from its curve, roles or popularity; say what the tools show, and label any bracket placement above the
  computed floor as your opinion (deck_stats with include_combos returns `bracket`, the lowest Commander Bracket the deck's
  contents allow under Wizards' published rules, with its inputs). A deck of three or more colours: say that simulate_draws
  does not check colours (colour_warning), so its mana numbers are optimistic. Never quote a card's cost, type or text
  from memory: get_card_oracle.
- Deck reviews, rules disputes and synergy questions: call council_brief and follow it. It seats the experts for
  the format, with each one's brief: answer as each of them in turn, then challenge, then the plan.
- Decks: a person names a deck ("my sliver deck"); find it with list_decks before asking for a link.
  A deck read from Archidekt is Archidekt's: credit Archidekt and give the deck's link back.
- Changes to the person's data (their collection, decks and shares) are theirs to decide. Tools that change
  something first return a preview: show it, and run the change only once the person has said yes to that
  preview. Ask before saving a deck from a link. When a printing is ambiguous (choose_printing), ask which one
  they have, or offer printing_unknown for an add; never pick one for them.
"""


def catalog_tools(Tool, ID, PAGING):  # noqa: N803 - the classes and constants of mcp.py
    from urllib.parse import quote

    DECK_ID = {**ID, "description": "A saved deck's id (from list_decks); use this instead of sending the list again"}

    def _deck_body(a: dict, *more: str) -> dict:
        return {k: a[k] for k in ("text", "deck_id", *more) if a.get(k) is not None}

    deck = {"type": "string", "minLength": 1, "maxLength": 50_000,
            "description": "The decklist, one card per line (e.g. '1 Sol Ring'); put commander cards under a 'Commander' header"}
    fmt = {"type": "string", "maxLength": 20, "enum": list(__import__("vault.deck_tools", fromlist=["FORMATS"]).FORMATS),
           "description": "The format to check against"}
    return [
        Tool("whoami", "Who you are connected as, which scopes you have, and which data versions the Vault holds "
             "(Comprehensive Rules edition, card data, rulings, tags, prices). Call this first to check the connection.",
             path=lambda a: f"{V1}/agent/whoami", title="Check the connection", provenance=("catalog",)),
        Tool("get_card_oracle", "A card's official Oracle text, types, legalities and Scryfall Tagger tags, by exact name "
             "(either face of a double-faced card) or Oracle id. For a card with two faces, each face's mana cost, text and "
             "stats are under `faces`; the top-level fields can be empty. Also lists the recorded changes of its legality "
             "(`legality_changes`: a ban, an unban or a restriction, with the day the Vault saw it), and `computed_roles`: roles the Vault "
             "worked out from the Oracle text where Scryfall's tags have none (computed, with the rule used). A misspelled name returns "
             "suggestions, never a guess. Works for any card, owned or not.",
             {"name": {"type": "string", "minLength": 1, "maxLength": 300, "description": "Exact card name"},
              "oracle_id": {"type": "string", "minLength": 36, "maxLength": 36}},
             path=lambda a: f"{V1}/catalog/cards", query=("name", "oracle_id"), provenance=("catalog",), ui="card"),
        Tool("get_rulings", "A card's rulings (Wizards' text via Scryfall), newest first, at most 25 a page. More "
             "remain when `next_offset` is not null: pass it back as `offset` to read the next page.",
             {"oracle_id": {"type": "string", "minLength": 36, "maxLength": 36, "description": "From get_card_oracle"},
              "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 25},
              "offset": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 0, "description": "Skip this many of the newest rulings"}}, ["oracle_id"],
             path=lambda a: f"{V1}/catalog/cards/{quote(a['oracle_id'], safe='')}/rulings", query=("limit", "offset"), provenance=("catalog",)),
        Tool("search_rules", "Search the Comprehensive Rules for a topic (e.g. 'replacement effect damage'); best matches "
             "first, at most 10, each with its number and the edition. A keyword ability or glossary term in the query puts "
             "its defining rules first. The rules are read live from Wizards of the Coast's current edition.",
             {"query": {"type": "string", "minLength": 2, "maxLength": 200},
              "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}, ["query"],
             path=lambda a: f"{V1}/catalog/rules/search", query=("q", "limit"), provenance=("catalog",)),
        Tool("rules_outline", "The Comprehensive Rules' table of contents, to find your way: with no argument the nine sections; "
             "with a section (7), subsection (702) or rule (702.19), what is directly under it, with headings. Drill down, then "
             "open a rule with get_rule.",
             {"under": {"type": "string", "maxLength": 20, "description": "A section, subsection or rule number"}},
             path=lambda a: f"{V1}/catalog/rules", query=("under",), provenance=("catalog",)),
        Tool("find_rules_term", "A glossary term or keyword ability ('trample', 'state-based actions', 'commander') and the rules "
             "that define it, with the glossary definition. Start here when a question names a game term.",
             {"name": {"type": "string", "minLength": 2, "maxLength": 120}}, ["name"],
             path=lambda a: f"{V1}/catalog/rules/term/{quote(a['name'], safe='')}", provenance=("catalog",)),
        Tool("get_rule", "One rule by number (e.g. '613.1a') or a glossary term ('glossary:Trample'), with where it sits: its "
             "parent, children, previous and next rule, the rules it cites and the rules that cite it. Follow those to read "
             "around a rule (exceptions often sit in a sibling or a later subrule).",
             {"number": {"type": "string", "minLength": 1, "maxLength": 120}}, ["number"],
             path=lambda a: f"{V1}/catalog/rules/{quote(a['number'], safe='')}", provenance=("catalog",)),
        Tool("verify_citation", "Check that a quote is verbatim in the rule, Oracle text or ruling you attribute it to. Whitespace "
             "and typographic quotes are forgiven; nothing else. If it fails you get the true text back.",
             {"kind": {"type": "string", "enum": ["rule", "oracle_text", "ruling"]},
              "ref": {"type": "string", "minLength": 1, "maxLength": 300, "description": "A rule number (or glossary:Term), or a card name or Oracle id"},
              "quote": {"type": "string", "minLength": 1, "maxLength": 4000},
              "version": {"type": "string", "maxLength": 10}}, ["kind", "ref", "quote"],
             method="POST", path=lambda a: f"{V1}/catalog/verify-citation",
             body=lambda a: {k: a[k] for k in ("kind", "ref", "quote", "version") if a.get(k) is not None}, provenance=("catalog",)),
        Tool("deck_stats", "Counts, mana curve, color identity, roles (ramp, draw, removal, sweepers...), the Commander Game Changers "
             "in the deck, estimated cost, and a Commander Bracket hint (`bracket`): the lowest bracket the deck's contents allow under "
             "Wizards' published rules, from Game Changers, mass land denial, extra turns and, with `include_combos`, two-card combos "
             "from Commander Spellbook. Each input is listed; the hint is a floor, not a placement. Computed by the Vault from the "
             "catalog. Roles say whether each came from Scryfall's tags or from the Vault's rules over Oracle text." + " Give `deck_id` (a saved deck, from list_decks) or `text`.",
             {"text": deck, "deck_id": DECK_ID,
              "include_combos": {"type": "boolean", "default": False,
                                 "description": "Also ask Commander Spellbook for two-card combos (sends the deck's card names to it)"}}, [], method="POST",
             path=lambda a: f"{V1}/decks/stats", body=lambda a: _deck_body(a, "include_combos"), provenance=("computed",), ui="deck"),
        Tool("simulate_draws", "How a deck's mana curve plays: a few sample games of the first turns (opening hand, draws, land "
             "drops, what gets cast) and the odds over many games: land drops made, mana by turn, cards in hand, the chance of "
             "discarding to hand size, 'five mana by turn 5'. Says when discarding or a big hand is the deck's plan, and lists "
             "what the simulation does not model. Explain the numbers in plain words; they are a hint, not a promise." + " Give `deck_id` (a saved deck, from list_decks) or `text`.",
             {"text": deck, "deck_id": DECK_ID, "format": fmt,
              "on_the_play": {"type": "boolean", "default": True, "description": "Going first"},
              "turns": {"type": "integer", "minimum": 1, "maximum": 10, "default": 6},
              "samples": {"type": "integer", "minimum": 0, "maximum": 10, "default": 5, "description": "Games shown turn by turn"},
              "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647, "description": "Repeat a run exactly"}},
             ["format"], method="POST", path=lambda a: f"{V1}/decks/simulate",
             body=lambda a: _deck_body(a, "format", "on_the_play", "turns", "samples", "seed"),
             provenance=("computed",)),
        Tool("deck_legality", "Whether a decklist is legal in a format: banned or illegal cards, copy limits, deck size, commander color "
             "identity. Lists every issue, says what it did not check, and lists recorded changes of legality for the cards in the "
             "deck in that format (`changes`)." + " Give `deck_id` (a saved deck, from list_decks) or `text`.", {"text": deck, "deck_id": DECK_ID, "format": fmt}, ["format"],
             method="POST", path=lambda a: f"{V1}/decks/legality", body=lambda a: _deck_body(a, "format"),
             provenance=("computed",)),
        Tool("find_upgrades", "Upgrade candidates for a deck within a budget: legal, inside the deck's colors, not already in it, each priced "
             "at or under budget_usd, for the roles the deck is short of (or the roles you name). Ordered by popularity, which is "
             "not power. Also lists the deck's least-played untagged cards as cut candidates. Then call validate_deck_changes." + " Give `deck_id` (a saved deck, from list_decks) or `text`.",
             {"text": deck, "deck_id": DECK_ID, "format": fmt,
              "budget_usd": {"type": "number", "minimum": 0, "maximum": 100000, "description": "The most any single added card may cost"},
              "roles": {"type": "array", "maxItems": 8, "items": {"type": "string", "enum": ["ramp", "draw", "removal", "sweeper", "counterspell", "tutor", "recursion", "sacrifice_outlet"]}},
              "limit": {"type": "integer", "minimum": 1, "maximum": 15, "default": 10},
              "use_collection": {"type": "boolean", "default": False, "description": "Also suggest cards the person already owns, "
                                 "whatever their price, first, each with owned_copies (free to add)"}}, ["format", "budget_usd"],
             method="POST", path=lambda a: f"{V1}/decks/upgrades",
             body=lambda a: _deck_body(a, "format", "budget_usd", "roles", "limit", "use_collection"),
             provenance=("computed",), ui="upgrades"),
        Tool("validate_deck_changes", "Check a proposed list of cuts and adds before presenting it: every card exists and is legal, adds are in "
             "the deck's colors, the resulting deck is still legal, and the adds' total price is within budget_usd. Present the plan only "
             "if valid is true." + " Give `deck_id` (a saved deck, from list_decks) or `text`.",
             {"text": deck, "deck_id": DECK_ID, "format": fmt, "adds": {"type": "array", "maxItems": 60, "items": {"type": "string", "maxLength": 300}},
              "cuts": {"type": "array", "maxItems": 60, "items": {"type": "string", "maxLength": 300}},
              "budget_usd": {"type": "number", "minimum": 0, "maximum": 100000, "description": "The most the adds may cost in total"}},
             ["format"], method="POST", path=lambda a: f"{V1}/decks/validate-changes",
             body=lambda a: _deck_body(a, "format", "adds", "cuts", "budget_usd"),
             provenance=("computed",)),
        Tool("present_steps", "Show the person a step-by-step explanation (an interaction, a stack, a ruling) with each cited rule attached. "
             "Write the steps yourself, citing rule numbers you looked up with get_rule; the Vault attaches each rule's verbatim text and "
             "edition, and flags any number that does not exist. Use it after you have verified your quotes.",
             {"title": {"type": "string", "maxLength": 200},
              "cards": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 300}},
              "steps": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "object", "properties": {
                  "text": {"type": "string", "minLength": 1, "maxLength": 700},
                  "rules": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 20}}},
                  "required": ["text"], "additionalProperties": False}},
              "version": {"type": "string", "maxLength": 10, "description": "Rules edition YYYY-MM-DD; default latest"}}, ["steps"],
             method="POST", path=lambda a: f"{V1}/catalog/walkthrough",
             body=lambda a: {k: a[k] for k in ("title", "cards", "steps", "version") if a.get(k) is not None},
             provenance=("catalog",), ui="steps"),
        Tool("find_combos", "Combos present in a decklist, and combos one card short (with the missing cards), asked of Commander Spellbook "
             "on demand. Descriptions are theirs and are attributed; the Vault keeps no copy of their data. It lists only combos Commander Spellbook knows: finding none does not mean the deck has " "no infinite combos." + " Give `deck_id` (a saved deck, from list_decks) or `text`.",
             {"text": deck, "deck_id": DECK_ID}, [], method="POST", path=lambda a: f"{V1}/decks/combos", body=lambda a: _deck_body(a),
             provenance=("computed",)),
        Tool("shopping_list", "The cards of a decklist the person does not own, with the cheapest known price of each (dated, from Scryfall) "
             "and a paste-ready list to put into a store's own list or deck tool. The Vault never contacts stores or fills carts, "
             "and knows no store's price: never say which store is cheapest." + " Give `deck_id` (a saved deck, from list_decks) or `text`.",
             {"text": deck, "deck_id": DECK_ID}, [], method="POST", path=lambda a: f"{V1}/decks/shopping-list",
             body=lambda a: _deck_body(a), provenance=("computed",), ui="shopping"),
    ]


def _arg(name: str, description: str, required: bool = True) -> dict:
    return {"name": name, "description": description, "required": required}


PROMPTS = [
    {"name": "rules_judge", "title": "Answer a rules question",
     "description": "Answer a Magic rules question from the Comprehensive Rules and rulings, with verified citations.",
     "arguments": [_arg("question", "The rules question")],
     "text": "Answer this Magic: The Gathering rules question as a careful judge would: {question}\n\n" + GROUNDING +
             "\nSteps: identify the cards and look each one up with get_card_oracle and get_rulings; find the relevant rules with "
             "search_rules and get_rule; verify every quote with verify_citation; then answer step by step, citing rule numbers and the "
             "rules edition. If the sources do not settle it, say so."},
    {"name": "explain_interaction", "title": "Explain a card interaction",
     "description": "Walk through how two or more cards interact, step by step, with the rules that apply.",
     "arguments": [_arg("cards", "The cards, comma separated"), _arg("scenario", "What is on the battlefield or stack", False)],
     "text": "Explain how these cards interact: {cards}. Scenario: {scenario}\n\n" + GROUNDING +
             "\nWalk through the stack and timing in order (priority, triggers, replacement effects, layers where relevant). For each step "
             "cite the rule number. Look up every card and its rulings first."},
    {"name": "upgrade_deck", "title": "Upgrade a deck on a budget",
     "description": "Suggest swaps for a deck within a budget, checked by the Vault.",
     "arguments": [_arg("deck", "The decklist"), _arg("format", "The format, e.g. commander"), _arg("budget_usd", "The budget in USD")],
     "text": "Help upgrade this {format} deck within a budget of ${budget_usd}:\n\n{deck}\n\n" + GROUNDING +
             "\nSteps: run deck_stats and deck_legality; call find_upgrades with the budget; choose swaps that fit the deck's plan and explain "
             "each one; then call validate_deck_changes with your final cuts and adds and the budget. Present the plan only if valid is true, "
             "and show the dated prices. Say that popularity is not power."},
    {"name": "shopping_help", "title": "Plan what to buy for a deck",
     "description": "Work out which cards are missing from the collection and how to buy them.",
     "arguments": [_arg("deck", "The decklist")],
     "text": "Work out what this deck still needs from the person's collection and give them a list to buy:\n\n{deck}\n\n" + GROUNDING +
             "\nCall shopping_list. Show the paste-ready list and the dated total. Tell them to paste it into the store's own list or deck tool "
             "and compare prices there; the Vault does not contact stores."},
    {"name": "council_review", "title": "Review a deck with an expert council",
     "description": "On-topic experts review a deck independently, a devil's advocate challenges them, and you get a checked plan.",
     "arguments": [_arg("deck", "The deck: a saved deck's name, a link, or the decklist"), _arg("format", "The format, e.g. commander"),
                   _arg("goal", "What the person wants: tune it, check it, explain it, find synergies", required=False)],
     "text": "Act as the chair of an expert council reviewing this {format} deck. Goal: {goal}\n\nDeck: {deck}\n\n" + GROUNDING +
             "\nFirst call council_brief with the format and goal (or the saved deck's deck_id): it seats the panel and gives "
             "each member's brief; follow it. Steps: find the deck (list_decks and get_deck for a saved deck); gather shared facts once with deck_stats, deck_legality, "
             "find_combos, check_decklist and get_deck_overlap. Seat only experts for this format (for commander: the Commander expert and a "
             "casual table voice), plus a rules judge and a devil's advocate, and the synergy or collection analyst if useful. Give each at most "
             "three points with evidence, then let the devil's advocate challenge them with evidence. Answer with the plan first (checked with "
             "validate_deck_changes, shown only if valid is true), then Agreed, Disputed and Not checked in one line each, and offer the full "
             "discussion on request. Label metagame knowledge as opinion."},
]


def render_prompt(prompt: dict, arguments: dict) -> str:
    class Blank(dict):
        def __missing__(self, key):
            return "(not given)"

    return prompt["text"].format_map(Blank({k: str(v) for k, v in (arguments or {}).items()}))


# Third-party blocks for tools that return Scryfall (or Archidekt) data that is not already in the body.
def provenance_blocks(kinds: tuple[str, ...], body: dict) -> list[dict]:
    out = []
    if "scryfall" in kinds:
        as_of = body.get("prices_date") if isinstance(body.get("prices_date"), str) else None
        out.append(prov.source("Scryfall", origin="card data and images: Wizards of the Coast and artists; prices: TCGplayer and Cardmarket",
                               url="https://scryfall.com", as_of=as_of, wizards_material=True).model_dump(exclude_none=True))
    if "archidekt" in kinds:
        deck = body.get("deck") if isinstance(body.get("deck"), dict) else {}
        owner = (body.get("owner") or {}).get("username") if isinstance(body.get("owner"), dict) else deck.get("author")
        deck_id = body.get("id") or deck.get("id")
        out.append(prov.source("Archidekt", origin=f"deck by {owner}" if owner else "a deck by its Archidekt author",
                               url=f"https://archidekt.com/decks/{deck_id}" if deck_id else "https://archidekt.com").model_dump(exclude_none=True))
    return out
