"""Shopping lists: pick a printing under a person's rules, and write the list the way each store's own tool reads it (#29, #55).

The Vault never contacts a store, fills a cart or names a cheapest shop: it prices from Scryfall's figures (sourced from
TCGplayer and Cardmarket), dated, and writes text a person pastes into the store's own list tool. What each store's tool
reads was checked against the store's own help page (``STORES``, docs/data-sources.md); anything a page does not say is
not guessed here, and the answer says what the paste cannot carry.

Choosing a printing: the rules a person can give are a finish, a language, a set list and a condition.
- finish, language and set are properties of a printing, and the catalog has Scryfall's price of each finish of each
  printing (``OraclePrinting``): the cheapest printing that satisfies all of them is chosen, and when none does the line says so.
- condition: Scryfall's prices are not per condition, so every printing qualifies on condition and no printing is
  cheaper for it. The rule is kept and shown, and the answer says so, rather than pretending the price changed.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

FINISHES = ("nonfoil", "foil", "etched")
CONDITIONS = ("NM", "LP", "MP", "HP", "DMG")
FORMATS = ("plain", "cardkingdom", "tcgplayer", "cardmarket", "csv")
LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
             "ja": "Japanese", "ko": "Korean", "ru": "Russian", "zhs": "Simplified Chinese", "zht": "Traditional Chinese",
             "he": "Hebrew", "la": "Latin", "grc": "Ancient Greek", "ar": "Arabic", "sa": "Sanskrit", "ph": "Phyrexian"}
LANGUAGE_NAMES = {name.lower(): code for code, name in LANGUAGES.items()} | {"chinese": "zhs"}

# What each store's own list tool reads: the page it was checked on, the day, and the line it accepts (docs/data-sources.md).
STORES = {
    "plain": {"store": None, "line": "<quantity> <name>", "checked": None, "page": None,
              "limits": "No store: the quantity and the card's name, one card per line."},
    "cardkingdom": {
        "store": "Card Kingdom Deck Builder", "line": "<quantity> <name>", "checked": "2026-10-07",
        "page": "https://blog.cardkingdom.com/deck-builder-craft-your-next-deck/",
        "limits": "The Deck Builder reads '4 Name', '4x Name' or just 'Name' (the three formats its own page shows); there is no "
                  "place for a set, a finish or a condition, so the printing chosen is shown beside the list, not in it."},
    "tcgplayer": {
        "store": "TCGplayer Mass Entry", "line": "<quantity> <name> [<SET>] <collector number>", "checked": "2026-10-07",
        "page": "https://help.tcgplayer.com/hc/en-us/articles/360055768913-Getting-Started-With-Mass-Entry",
        "limits": "Mass Entry reads quantity, name, a set code in brackets and a collector number; the set and number are optional "
                  "('1 Lightning Bolt [SLD]' means any art from that set). Finish and condition are not part of a line: they are "
                  "set in Mass Entry's own preferences (printings, and conditions, which default to Moderately Played and better). "
                  "Set codes written here are Scryfall's; TCGplayer's 'Show Set Codes' list is the authority if one is not matched."},
    "cardmarket": {
        "store": "Cardmarket want list (Add via list)", "line": "<quantity> <name> (<expansion name>)", "checked": "2026-10-07",
        "page": "https://help.cardmarket.com/en/how-to-add-a-mtg-decklist-to-wants",
        "limits": "The want list reads 'Name', '4 Name', and 'Name (Expansion)' with the expansion's name in parentheses (and an "
                  "optional '(V.1)' version number, which is not written here). The page names no place for a finish, a language "
                  "or a condition: set them on the want list afterwards. Expansion names are Scryfall's; Cardmarket reports lines "
                  "it cannot match, and the page says to check their spelling."},
    "csv": {"store": None, "line": "quantity,name,set,collector_number,finish,language,unit_price_usd,price_date",
            "checked": None, "page": None,
            "limits": "A spreadsheet file, not tied to a store. A cell that could be read as a formula starts with an apostrophe."},
}


@dataclass(frozen=True)
class Rules:
    """The printing rules a person gave. Empty means none: the cheapest priced paper printing, as before."""

    finish: str | None = None
    language: str | None = None
    sets: tuple[str, ...] = ()
    condition: str | None = None

    @property
    def given(self) -> bool:
        return bool(self.finish or self.language or self.sets or self.condition)

    def describe(self) -> dict:
        return {"finish": self.finish, "language": self.language, "sets": list(self.sets) or None, "condition": self.condition}


def language_code(value: str | None) -> str | None:
    """A Scryfall language code from a code or a name ('Japanese'); an unknown language is an error, never ignored."""
    if value is None or not value.strip():
        return None
    v = value.strip().lower()
    if v in LANGUAGES:
        return v
    if v in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[v]
    raise ValueError(f"Unknown language '{value}': use a Scryfall code ({', '.join(LANGUAGES)}) or its name")


def make_rules(finish: str | None, language: str | None, sets: list[str] | None, condition: str | None) -> Rules:
    if finish is not None and finish not in FINISHES:
        raise ValueError(f"finish must be one of {', '.join(FINISHES)}")
    if condition is not None and condition.upper() not in CONDITIONS:
        raise ValueError(f"condition must be one of {', '.join(CONDITIONS)} (near mint to damaged)")
    return Rules(finish=finish, language=language_code(language),
                 sets=tuple(dict.fromkeys(s.strip().lower() for s in (sets or []) if s.strip())),
                 condition=condition.upper() if condition else None)


def _price(printing, finish: str) -> float | None:
    return {"nonfoil": printing.usd, "foil": printing.usd_foil, "etched": printing.usd_etched}[finish]


def choose(printings, rules: Rules):
    """The cheapest printing and finish that satisfy the rules, as ``(printing, finish, price)``, or None when none does.
    Ties go to the nonfoil, then to the set code and collector number, so the same data always gives the same choice."""
    best = None
    for p in printings:
        if rules.language and p.lang != rules.language:
            continue
        if rules.sets and p.set_code.lower() not in rules.sets:
            continue
        for rank, finish in enumerate(FINISHES):
            if rules.finish and finish != rules.finish:
                continue
            price = _price(p, finish)
            if price is None:
                continue
            key = (price, rank, p.set_code, p.collector_number)
            if best is None or key < best[0]:
                best = (key, p, finish, price)
    return None if best is None else (best[1], best[2], best[3])


def why_none(rules: Rules) -> str:
    asked = [f"finish {rules.finish}" if rules.finish else None,
             f"language {LANGUAGES.get(rules.language, rules.language)}" if rules.language else None,
             f"set {', '.join(s.upper() for s in rules.sets)}" if rules.sets else None]
    return ("No printing of this card in the catalog has a Scryfall price with " + " and ".join(a for a in asked if a)
            + ". It may exist unpriced there (Scryfall prices few non-English printings); it is left out of the list.")


def printing_dict(p, finish: str) -> dict:
    return {"scryfall_id": p.scryfall_id, "set": p.set_code.upper(), "set_name": p.set_name, "collector_number": p.collector_number,
            "finish": finish, "language": p.lang}


# -- the store formats -------------------------------------------------------------------------------

def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def render(lines: list[dict], fmt: str) -> str:
    """The list in one store's paste syntax. A line that has no printing chosen (none qualified) is not written."""
    todo = [l for l in lines if not l.get("no_qualifying_printing")]
    out = []
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["quantity", "name", "set", "collector_number", "finish", "language", "unit_price_usd", "price_date"])
        for l in todo:
            p = l.get("printing") or {}
            writer.writerow([l["quantity"], _csv_cell(l["name"]), p.get("set", ""), _csv_cell(p.get("collector_number", "")),
                             p.get("finish", ""), p.get("language", ""), "" if l["unit_price_usd"] is None else l["unit_price_usd"],
                             l.get("price_date") or ""])
        return buf.getvalue().rstrip("\n")
    for l in todo:
        p = l.get("printing")
        if fmt == "tcgplayer" and p:
            out.append(f"{l['quantity']} {l['name']} [{p['set']}] {p['collector_number']}")
        elif fmt == "cardmarket" and p and p.get("set_name"):
            out.append(f"{l['quantity']} {l['name']} ({p['set_name']})")
        else:  # plain and Card Kingdom (name only), and any line without a printing
            out.append(f"{l['quantity']} {l['name']}")
    return "\n".join(out)


def store_notes(fmt: str, lines: list[dict], rules: Rules) -> list[str]:
    """What the pasted text cannot carry, said next to it (never silently dropped)."""
    notes = []
    info = STORES[fmt]
    if info["limits"] and fmt != "plain":
        notes.append(info["limits"])
    chosen = [l for l in lines if l.get("printing")]
    if chosen and fmt in ("plain", "cardkingdom"):
        notes.append("The printings chosen are listed in `lines`; this format has no place for them.")
    if rules.finish in ("foil", "etched") or any(l["printing"]["finish"] != "nonfoil" for l in chosen):
        if fmt in ("tcgplayer", "cardmarket", "cardkingdom", "plain"):
            notes.append("Some printings were chosen in a foil finish, which this paste cannot ask for: pick the finish in the store.")
    if rules.language and fmt in ("tcgplayer", "cardkingdom", "plain", "cardmarket"):
        notes.append(f"Language {LANGUAGES.get(rules.language, rules.language)} cannot be written in this paste: set it in the store.")
    if rules.condition:
        notes.append(f"Condition {rules.condition} is not in the paste, and Scryfall's prices are not per condition: every printing "
                     "qualifies on condition and no price changed for it. Set the condition in the store's own filter, where its "
                     "prices for that condition apply.")
    if any("//" in l["name"] for l in lines):
        notes.append("A double-faced card is written with both faces' names; a store may list it under one face: check those lines.")
    return notes
