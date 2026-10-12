"""AGENTS.md section 10 names the Chief of Staff, the roles and how sessions talk (bead vault-92s, owner's ask of 2026-10-12).
The live roster stays in bead vault-br6; the section must point there and must not copy session ids."""

import re
from pathlib import Path

AGENTS = (Path(__file__).resolve().parent.parent / "AGENTS.md").read_text(encoding="utf-8")


def _section_10() -> str:
    start = AGENTS.index("## 10. The Chief of Staff, the roles, and how everyone talks")
    return AGENTS[start:]


def test_section_10_names_the_chief_of_staff_and_what_it_does_not_do():
    s = _section_10()
    for needed in ("Chief of staff for Vault project", "single interface to every session", "**It does:**", "**It does not:**",
                   "take a claim from a live holder", "one plain question"):
        assert needed in s, needed


def test_section_10_routes_by_role_and_reads_the_roster_live():
    s = _section_10()
    for role in ("Product Manager", "Progress Observer", "Delivery Manager", "AI Worker", "General Worker", "Spike Researcher", "Infra Manager",
                 "Documentation Manager"):
        assert role in s, role
    assert "bead **vault-br6**" in s and "do not recall it" in s


def test_section_10_says_how_sessions_talk():
    s = _section_10()
    for needed in ("The bead is the record; a message is only the nudge", "by its session id", "`SendMessage`",
                   "raised once, in two places", "**merged**, **deployed**, **verified**", "No personal data"):
        assert needed in s, needed


def test_section_10_carries_no_session_ids():
    assert not re.search(r"local_[0-9a-f]{8}-", _section_10())


def test_section_10_says_wintermute_is_headless_and_what_that_limits():
    s = _section_10()
    for needed in ("### Where a session can work: Wintermute is a headless server", "no display, no desktop apps",
                   "headless Playwright", "not configured", "`vault-csw`", "It cannot:", "A headless pass is evidence for the web pages only"):
        assert needed in s, needed
