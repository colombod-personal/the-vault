"""AGENTS.md section 10 names the Chief of Staff, the roles and how sessions talk (bead vault-92s, owner's ask of 2026-10-12).
The live roster stays in bead vault-br6; the section must point there, must not copy session ids, and must mark what is the
owner's and what is proposed."""

import re
from pathlib import Path

AGENTS = (Path(__file__).resolve().parent.parent / "AGENTS.md").read_text(encoding="utf-8")


def _section_10() -> str:
    """Section 10 only: from its heading to the next numbered top-level heading (or the end of the file)."""
    start = AGENTS.index("## 10. The Chief of Staff, the roles, and how everyone talks")
    following = re.search(r"(?m)^## \d+\. ", AGENTS[start + 3:])
    return AGENTS[start:start + 3 + following.start()] if following else AGENTS[start:]


def _flat() -> str:
    """Section 10 with every run of whitespace collapsed to one space, so a phrase wrapped over two lines still matches."""
    return re.sub(r"\s+", " ", _section_10())


def test_section_10_names_the_chief_of_staff_what_it_decides_and_does_not_do():
    s = _flat()
    for needed in ("Chief of staff for Vault project", "main interface to every session", "**It does:**", "**It decides, on the owner's delegation**",
                   "**It does not:**", "take a claim from a live holder", "one plain question",
                   "only after a search of beads finds none"):
        assert needed in s, needed


def test_section_10_marks_what_is_the_owners_and_what_is_proposed():
    s = _flat()
    assert "**What is the owner's and what is proposed.**" in s and "proposed operating rules" in s
    assert "spelling corrected" in s


def test_section_10_routes_by_role_reads_the_roster_live_and_gives_no_unowned_power():
    s = _flat()
    for role in ("Product Manager", "Progress Observer", "Delivery Manager", "AI Worker", "General Worker", "Spike Researcher",
                 "Infra Manager"):
        assert role in s, role
    assert "| Documentation Manager" not in s  # the cloud route was withdrawn (vault-br6)
    assert "bead **vault-br6**" in s and "do not recall it" in s
    assert "repository settings" not in _section_10().split("| Infra Manager")[1].split("\n")[0]  # the row hands out no setting
    for needed in ("No role merges, closes a bead, changes a setting or publishes except as sections 5, 6 and 8 say",
                   "only after the owner's yes in chat, recorded in the bead"):
        assert needed in s, needed


def test_section_10_says_how_sessions_talk():
    s = _flat()
    for needed in ("The bead is the record; a message is only the nudge", "session id", "`SendMessage`",
                   "raised once, in two places", "**merged**, **deployed**, **verified**", "No personal data",
                   "never the owner's approval", "never a way around your own permissions",
                   "`NEEDS OWNER:`", "claimed by the worker who takes it"):
        assert needed in s, needed


def test_section_10_carries_no_session_ids_and_stays_inside_its_section():
    s = _section_10()
    assert not re.search(r"local_[0-9a-f]{8}-", s)
    assert not re.search(r"(?m)^## 1[1-9]\. ", s)


def test_section_10_says_wintermute_is_headless_and_what_that_limits():
    s = _flat()
    for needed in ("### Where a session can work: Wintermute is a headless server", "no display, no desktop apps",
                   "headless Playwright", "not configured", "`vault-csw`", "**It cannot:**",
                   "A headless pass is a test, not evidence about production or about the assistants",
                   "the host-CLI runs of `vault-wxy.9`, run on the owner's PC"):
        assert needed in s, needed
