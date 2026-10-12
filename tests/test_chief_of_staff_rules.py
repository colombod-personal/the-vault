"""AGENTS.md section 10 names the Chief of Staff, the roles and how sessions talk (bead vault-92s, owner's ask of 2026-10-12).
The live roster stays in bead vault-br6; the section must point there, must not copy session ids, and must mark what is the
owner's and what is proposed."""

import re
from pathlib import Path

AGENTS = (Path(__file__).resolve().parent.parent / "AGENTS.md").read_text(encoding="utf-8")
HEADING = "## 10. The Chief of Staff, the roles, and how everyone talks"


def _section_of(text: str) -> str:
    """Section 10 only: from its heading to the next numbered top-level heading (or the end of the text)."""
    start = text.index(HEADING)
    following = re.search(r"(?m)^## \d+\. ", text[start + len(HEADING):])
    return text[start:start + len(HEADING) + following.start()] if following else text[start:]


def _section_10() -> str:
    return _section_of(AGENTS)


def _flat() -> str:
    """Section 10 with every run of whitespace collapsed to one space, so a phrase wrapped over two lines still matches."""
    return re.sub(r"\s+", " ", _section_10())


def test_the_section_helper_stops_at_the_next_numbered_heading():
    # A synthetic file with a section 11: its text and ids must be outside section 10 (the real file has no section 11 yet).
    text = f"## 9. Before\n\nnine\n\n{HEADING}\n\nten body\n\n### sub\n\nstill ten\n\n## 11. After\n\nlocal_12345678-aaaa eleven\n"
    section = _section_of(text)
    assert "ten body" in section and "still ten" in section
    assert "## 11." not in section and "eleven" not in section and "nine" not in section


def test_section_10_names_the_chief_of_staff_what_it_decides_and_does_not_do():
    s = _flat()
    for needed in ("Chief of staff for Vault project", "main interface to every session", "**It does:**",
                   "**It decides, on the owner's delegation**", "**It does not:**", "take a claim from a live holder",
                   "one plain question", "only after a search of beads finds none",
                   "Only the quoted words are the owner's", "Leaving a setting open and unchanged is within the delegation",
                   "answer for the owner except as delegated above"):
        assert needed in s, needed


def test_section_10_marks_what_is_the_owners_and_what_is_proposed():
    s = _flat()
    assert "**What is the owner's and what is proposed.**" in s and "proposed operating rules" in s
    assert "tidied" in s and "Proposed, not the owner's words" in s


def test_section_10_routes_by_role_reads_the_roster_live_and_gives_no_unowned_power():
    s = _flat()
    for role in ("Product Manager", "Progress Observer", "Delivery Manager", "AI Worker", "General Worker", "Spike Researcher",
                 "Infra Manager"):
        assert role in s, role
    assert "| Documentation Manager" not in s  # the cloud route was withdrawn (vault-br6)
    assert "bead **vault-br6**" in s and "do not recall it" in s
    assert "repository settings" not in _section_10().split("| Infra Manager")[1].split("\n")[0]  # the row hands out no setting
    assert "independent reads" not in _section_10().split("| Product Manager")[1].split("\n")[0]  # it directs the change
    for needed in ("No role merges, closes a bead, changes a setting or publishes except as sections 5, 6 and 8 say",
                   "only after the owner's yes in chat, recorded in the bead",
                   "beyond the reads and demo-account checks", "neither wrote nor directed the change", "claims no bead"):
        assert needed in s, needed


def test_section_10_says_how_sessions_talk():
    s = _flat()
    for needed in ("The bead is the record; a message is only the nudge", "session id", "`SendMessage`",
                   "raised once, in two places", "**merged**, **deployed**, **verified**", "No personal data",
                   "never the owner's approval", "never a way around your own permissions",
                   "`NEEDS OWNER:`", "claimed by the worker who takes it"):
        assert needed in s, needed


def test_section_10_carries_no_session_ids():
    assert not re.search(r"local_[0-9a-f]{8}-", _section_10())


def test_section_10_says_wintermute_is_headless_and_what_that_limits():
    s = _flat()
    for needed in ("### Where a session can work: Wintermute is a headless server", "empty display setting",
                   "no browser binary", "headless Playwright", "not configured", "`vault-csw`", "**It cannot:**",
                   "A headless pass is a test, not evidence about production or about the assistants",
                   "the host-CLI runs of `vault-wxy.9`, run on the owner's PC"):
        assert needed in s, needed


def _all_flat() -> str:
    return re.sub(r"\s+", " ", AGENTS)


def test_section_1_carries_the_experience_rule_and_the_template_asks_for_it():
    s = _all_flat()
    for needed in ("**The experience rule (owner, 2026-10-12).**", "(typos fixed)",
                   "the user experience must work, the implementation detail is not what we have customers using",
                   "never the mechanism", "never handed homework", "the final yes before something changes",
                   "a recommendation on every choice left", "a number measured on a real run in the real app"):
        assert needed in s, needed
    assert s.index("The experience rule") < s.index("## 2. Done means evidence")
    template = (Path(__file__).resolve().parent.parent / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert "- [ ] Criteria: what the person does and sees in their app, not the mechanism (AGENTS.md section 1)." in template


def test_section_8_carries_the_polling_rule_as_the_chief_of_staffs_not_the_owners():
    s = _all_flat()
    for needed in ("**Polling GitHub.** The Chief of Staff's operating rule", "not the owner's words",
                   "at most one open-PR query plus one merged query every 4 minutes per session",
                   "never a loop of API calls to test a limit", "`gh pr checks --watch --interval 120`"):
        assert needed in s, needed
    assert s.index("## 8. Beads is where") < s.index("**Polling GitHub.**") < s.index("## 9. Read the record")
