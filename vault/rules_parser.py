"""Parse the Comprehensive Rules plain-text file into numbered rows.

The file (published by Wizards of the Coast) has a contents list, then numbered sections
("1. Game Concepts"), subsections ("100. General"), rules ("100.1. text"), subrules ("100.1a text"),
``Example:`` lines that belong to the rule above them, a Glossary (a term line, then its definition
line) and Credits. Rule numbers and text are returned exactly as published: this module only splits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

EFFECTIVE = re.compile(r"effective as of ([A-Z][a-z]+ \d{1,2}, \d{4})")
SECTION = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
SUBSECTION = re.compile(r"^(\d{3})\.\s+(\S.*)$")
RULE = re.compile(r"^(\d{3}\.\d+[a-z]?)\.?\s+(\S.*)$")


@dataclass
class ParsedRules:
    effective_date: date
    rules: list[dict] = field(default_factory=list)

    @property
    def version(self) -> str:
        return self.effective_date.isoformat()


class RulesFormatError(ValueError):
    """The file does not look like the Comprehensive Rules (so nothing is stored)."""


def parent_of(number: str) -> str | None:
    if re.fullmatch(r"\d{3}\.\d+[a-z]", number):
        return number[:-1]
    if re.fullmatch(r"\d{3}\.\d+", number):
        return number.split(".")[0]
    if re.fullmatch(r"\d{3}", number):
        return number[0]  # "100" belongs to section "1"
    return None


def parse(text: str) -> ParsedRules:
    # U+2028 (line separator) joins the numbered senses inside glossary definitions
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace(" ", "\n").lstrip("﻿").split("\n")]
    match = next((EFFECTIVE.search(line) for line in lines[:15] if EFFECTIVE.search(line)), None)
    if match is None:
        raise RulesFormatError("no 'effective as of <date>' line near the top")
    effective = datetime.strptime(match.group(1), "%B %d, %Y").date()

    # The contents list repeats the section titles; the body starts at the last "1. ..." before rule 100.
    first_rule = next((i for i, line in enumerate(lines) if RULE.match(line)), None)
    if first_rule is None:
        raise RulesFormatError("no numbered rules found")
    start = max(i for i in range(first_rule) if SECTION.match(lines[i]) and lines[i].startswith("1."))
    glossary = max((i for i, line in enumerate(lines) if line.strip() == "Glossary"), default=len(lines))
    credits = next((i for i in range(glossary, len(lines)) if lines[i].strip() == "Credits"), len(lines))

    out = ParsedRules(effective)
    last: dict | None = None
    for line in lines[start:glossary]:
        stripped = line.strip()
        if not stripped:
            continue
        if (m := RULE.match(stripped)):
            number, body = m.groups()
            last = {"number": number, "text": body, "parent": parent_of(number), "kind": "rule"}
        elif (m := SUBSECTION.match(stripped)):
            number, title = m.groups()
            last = {"number": number, "text": title, "parent": parent_of(number), "kind": "heading"}
        elif (m := SECTION.match(stripped)):
            number, title = m.groups()
            last = {"number": number, "text": title, "parent": None, "kind": "heading"}
        elif last is not None and last["kind"] == "rule":
            last["text"] += "\n" + stripped  # an Example: line or a wrapped line belongs to the rule above
            continue
        else:
            continue
        out.rules.append(last)

    terms = [block for block in _blocks(lines[glossary + 1:credits]) if len(block) >= 2]
    for block in terms:
        term = block[0].strip()
        out.rules.append({"number": f"glossary:{term}", "text": "\n".join(b.strip() for b in block[1:]),
                          "parent": None, "kind": "glossary"})
    seen: set[str] = set()
    for row in out.rules:
        if row["number"] in seen:
            raise RulesFormatError(f"rule {row['number']} appears twice")
        seen.add(row["number"])
    if sum(r["kind"] == "rule" for r in out.rules) < 3:
        raise RulesFormatError("too few rules found")
    return out


def _blocks(lines: list[str]):
    block: list[str] = []
    for line in lines:
        if line.strip():
            block.append(line)
        elif block:
            yield block
            block = []
    if block:
        yield block
