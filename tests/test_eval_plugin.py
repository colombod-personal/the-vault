"""The plugin evaluation set stays honest (bead vault-85p.6): every tool a case names exists, ids are unique, and the scorer
passes the recorded good run and fails a run that answers from memory."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import eval_plugin as ev  # noqa: E402

from vault.api import mcp  # noqa: E402

CASES = ev.load_cases()


def test_case_ids_are_unique_and_every_case_has_a_prompt_and_a_check():
    raw = json.loads(ev.CASES.read_text(encoding="utf-8"))["cases"]
    assert len({c["id"] for c in raw}) == len(raw)
    for c in raw:
        assert c["prompt"].strip() and c["why"].strip()
        assert any(c.get(k) for k in ("must_call", "must_call_any", "must_name", "must_cite", "must_match", "must_not")), c["id"]


def test_every_tool_a_case_requires_exists():
    for c in CASES.values():
        for tool in c.get("must_call", []) + [t for g in c.get("must_call_any", []) for t in g]:
            assert tool in mcp.BY_NAME, f"{c['id']}: no tool named {tool}"


def test_patterns_compile_and_rule_numbers_look_like_rule_numbers():
    for c in CASES.values():
        for m in c.get("must_match", []) + c.get("must_not", []):
            re.compile(m["pattern"])
        for rule in c.get("must_cite", []):
            assert re.fullmatch(r"\d{3}\.\d+[a-z]?", rule), (c["id"], rule)


def _run(tools, answer):
    lines = [json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": t}}) for t in tools]
    lines.append(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}}))
    return "\n".join(lines)


def test_the_scorer_passes_a_good_run_and_fails_one_answered_from_memory():
    case = CASES["sliver_council_review"]
    good_answer = ("Shadow Sliver gives every Sliver shadow, rule 702.28b. Sliver Overlord is the commander. simulate_draws does not "
                   "check colours, so its mana numbers are optimistic. Devil's advocate objection: cutting 7 basics leaves 3 red "
                   "sources in a five-colour deck; the plan answers it by keeping 4 Mountains.")
    tools, answer = ev.read_codex(_run(["council_brief", "deck_stats", "get_rule", "validate_deck_changes"], good_answer))
    assert all(r["ok"] for r in ev.score(case, tools, answer))
    tools, answer = ev.read_codex(_run(["deck_stats"], "Your deck has 45 basic lands and looks fine."))
    failed = {r["check"] for r in ev.score(case, tools, answer) if not r["ok"]}
    assert {"calls council_brief", "calls get_rule", "cites rule 702.28b",
            "does not: invented basic-land count (deck_stats says 54 lands)"} <= failed


def test_a_rule_number_is_not_matched_inside_a_longer_number():
    case = {"must_cite": ["702.18a"]}
    assert not ev.score(case, [], "see rule 1702.18a")[0]["ok"]
    assert ev.score(case, [], "see rule 702.18a (Comprehensive Rules)")[0]["ok"]


def test_markdown_emphasis_and_other_valid_tools_do_not_cause_false_failures():
    # found scoring nine real Codex runs (bead vault-85p.5): **not** broke "would not", and search_rules answers a rules question
    harmonic = CASES["harmonic_sliver_add"]
    rows = ev.score(harmonic, ["validate_deck_changes"], "I would **not** add `Harmonic Sliver`; it destroys your Sol Ring.")
    assert next(r for r in rows if r["check"] == "advises against it plainly")["ok"]
    rules = CASES["rules_unnamed_vault"]
    assert all(r["ok"] for r in ev.score(rules, ["search_rules"], "Rule 702.19b: assign lethal damage first."))
    assert not all(r["ok"] for r in ev.score(rules, [], "Rule 702.19b: assign lethal damage first."))
    colours = CASES["sliver_council_review"]
    rows = ev.score(colours, [], "The deck's colours are not checked, so real games will be slower.")
    assert next(r for r in rows if r["check"].startswith("says simulate_draws"))["ok"]


def test_the_advocate_is_told_to_always_raise_an_objection_and_the_chair_to_quote_the_rule():
    """Bead vault-wxy.7.2: real-app rows V6 and V7 (12 Oct): the advocate found no objection, and a lookout was named without
    a rule quoted in the answer."""
    advocate = (ROOT / "agents" / "vault-devils-advocate.md").read_text(encoding="utf-8")
    assert "always at least one" in advocate and "No objection" in advocate
    chair = (ROOT / "skills" / "expert-council" / "SKILL.md").read_text(encoding="utf-8")
    assert "quoted with `get_rule` in this answer" in chair and "not your own reading" in chair
    assert "`council_brief` or `validate_deck_changes`" in chair  # the brief carries lookouts too
    case = CASES["sliver_council_review"]
    rows = ev.score(case, [], "Devil's advocate: found no material legality objection.")
    assert not next(r for r in rows if r["check"].endswith("devil's advocate objects to nothing"))["ok"]
    rows = ev.score(case, [], "Devil's advocate: cutting 7 basics leaves 3 red sources, rule 903.4 puts red in the commander's identity.")
    assert next(r for r in rows if r["check"].endswith("devil's advocate objects to nothing"))["ok"]


def test_an_answer_with_no_advocate_objection_at_all_fails_the_positive_check():
    case = CASES["sliver_council_review"]
    rows = ev.score(case, [], "Shadow Sliver, rule 702.28b. Sliver Overlord. Colours are not checked. Looks fine.")
    assert not next(r for r in rows if r["check"].startswith("devil's advocate raises"))["ok"]
