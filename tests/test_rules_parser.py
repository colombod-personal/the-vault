"""The Comprehensive Rules parser and loader. The sample below is invented text in the file's format
(the real text is Wizards' material and is not kept in this repository)."""

from datetime import date

import pytest

from jobs import sync_catalog
from vault import rules_parser as rp

SAMPLE = """Magic: The Gathering Comprehensive Rules

These rules are effective as of March 3, 2027.

Contents

1. Game Concepts
100. General
2. Parts of a Card
200. General

1. Game Concepts

100. General

100.1. First sample rule.

100.1a A sample subrule.

Example: A sample example that belongs to the subrule.

100.2. A second sample rule
that wraps onto another line.

2. Parts of a Card

200. General

200.1. The parts of a card.

Glossary

Sample Term
1. First sense of a sample term. 2. Second sense. See rule 100.1.

Other Term
Another definition.

Credits

Sample credits.
"""


def numbers(parsed):
    return [r["number"] for r in parsed.rules]


def test_sections_rules_subrules_examples_and_glossary_are_split_as_published():
    parsed = rp.parse(SAMPLE)
    assert parsed.effective_date == date(2027, 3, 3) and parsed.version == "2027-03-03"
    assert numbers(parsed) == ["1", "100", "100.1", "100.1a", "100.2", "2", "200", "200.1",
                               "glossary:Sample Term", "glossary:Other Term"]
    by = {r["number"]: r for r in parsed.rules}
    assert by["100.1a"]["text"] == "A sample subrule.\nExample: A sample example that belongs to the subrule."
    assert by["100.2"]["text"] == "A second sample rule\nthat wraps onto another line."
    assert (by["100.1a"]["parent"], by["100.1"]["parent"], by["100"]["parent"], by["200"]["parent"]) == ("100.1", "100", "1", "2")
    assert by["100"]["kind"] == "heading" and by["100.1"]["kind"] == "rule" and by["glossary:Other Term"]["kind"] == "glossary"
    assert by["glossary:Sample Term"]["text"].splitlines()[:2] == ["1. First sense of a sample term.", "2. Second sense. See rule 100.1."]


def test_windows_line_endings_and_a_bom_are_fine():
    parsed = rp.parse("﻿" + SAMPLE.replace("\n", "\r\n"))
    assert parsed.version == "2027-03-03" and "100.1a" in numbers(parsed)


@pytest.mark.parametrize("bad", ["", "Not the rules at all.", "These rules are effective as of March 3, 2027.\n\nnothing else"])
def test_text_that_is_not_the_rules_is_refused(bad):
    with pytest.raises(rp.RulesFormatError):
        rp.parse(bad)


def test_the_catalog_job_no_longer_loads_the_rules(database_url, monkeypatch):
    """The rules are read live from Wizards (vault/rules_live.py): the job refuses a "rules" source."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(SystemExit):
        sync_catalog.main(["--sources", "rules"])
