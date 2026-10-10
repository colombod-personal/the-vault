"""The rules change brief (#107): two editions of the Comprehensive Rules, read live from the Wizards twin, compared when asked,
nothing of either stored; the rulings and legality changes the Vault already holds for the same period; and the check of the
rule numbers the skills and agents cite. The rules below are invented, in the file's format. No test reaches Wizards."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from tests.test_agents import call_tool, make_token, rpc
from tests.test_catalog_api import BOLT, card as make_card, load, serve_rules
from tests.test_deck_api import CARDS, oid
from tests.test_mcp_catalog import agent, bot  # noqa: F401  (fixtures)
from twins.universe import Universe
from vault import catalog_sync as cs
from vault import rules_changes as rc
from vault.rules_live import LiveRules, RulesUnavailable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_rule_citations as cli  # noqa: E402

V1 = "/api/v1/catalog"

SPLIT = "Splitting the combat damage between blockers is invented text for these tests, long enough to be its own rule."
END_STEP = "The combat damage step ends when all damage has been dealt and state-based actions are performed."
LAST = "Nothing happens in this rule except that it exists, which is all an invented test rule needs to do."


def edition(effective, *, trample_a, rules_100, rule_510_extra, glossary_extra="") -> str:
    """An invented Comprehensive Rules file. ``rules_100``: the numbered rules under 100 as one block."""
    return f"""Magic: The Gathering Comprehensive Rules

These rules are effective as of {effective}.

Contents

1. Game Concepts
100. General
5. Turn Structure
510. Combat Damage Step
7. Additional Rules
702. Keyword Abilities

1. Game Concepts

100. General

{rules_100}

5. Turn Structure

510. Combat Damage Step

510.1. First, the active player announces how each attacking creature assigns its combat damage. See rule 702.19.

{rule_510_extra}

7. Additional Rules

702. Keyword Abilities

702.19. Trample

702.19a {trample_a}

702.19b The controller of an attacking creature with trample first assigns damage to the creatures blocking it. See rule 510.1.

702.19c If all the creatures blocking it are removed from combat, the creature assigns all its damage to the player.

Glossary

Trample
A keyword ability that modifies how a creature assigns combat damage. See rule 702.19.
{glossary_extra}
Credits

Invented for tests.
"""


OLD = edition(
    "January 5, 2027",
    trample_a="Trample is a static ability that modifies the rules for assigning an attacking creature's damage. It is old.",
    rules_100=f"100.1. These rules apply to any game.\n\n100.2. {SPLIT}\n\n100.3. {LAST}\n\n100.9. This rule exists only in the old edition and nothing replaces it.",
    rule_510_extra=f"510.3. {END_STEP}",
    glossary_extra="\nBanding\nAn old keyword ability that was removed. See rule 702.22.\n")
NEW = edition(
    "March 3, 2027",
    trample_a="Trample is a static ability that modifies the rules for assigning an attacking creature's combat damage.",
    rules_100=f"100.1. These rules apply to any game.\n\n100.2. A rule inserted before the others, so everything after it takes the next number.\n\n100.3. {SPLIT}\n\n100.4. {LAST}",
    rule_510_extra=f"510.4. {END_STEP}\n\n510.5. A brand new rule only the new edition has.")


def two_editions(old=OLD, new=NEW, old_day="20270105", new_day="20270303"):
    universe = Universe(seed=False)
    universe.wizards.publish(old, old_day, listed=False)  # still on the CDN, no longer linked from the page
    universe.wizards.publish(new, new_day)
    return universe


def live(universe) -> LiveRules:
    return LiveRules(transport=universe.transport)


# -- the diff ---------------------------------------------------------------------------------------------------------------------

def test_the_brief_lists_what_was_added_removed_renumbered_shifted_and_changed_in_rule_order():
    comparison = live(two_editions()).compare()
    assert (comparison.previous.version, comparison.current.version) == ("2027-01-05", "2027-03-03")
    assert (comparison.previous.file_date, comparison.current.file_date) == ("2027-01-05", "2027-03-03")
    ch = comparison.changes
    assert ch.added == ["510.5"]
    assert ch.removed == ["100.9", "glossary:Banding"]
    # the same words under another number are "renumbered", not "removed and added"; 100.3 and 100.4 only moved down by one
    assert ch.renumbered == {"100.2": "100.3", "100.3": "100.4", "510.3": "510.4"}
    assert ch.shifted == {"100.3": "100.2"}  # a number both editions have, now holding another rule's words
    assert ch.gone == {"100.9", "510.3", "glossary:Banding"}  # numbers the new edition does not have at all
    assert sorted(ch.changed) == ["100.2", "702.19a"]
    assert ch.changed["702.19a"] == {"was": "Trample is a static ability that modifies the rules for assigning an attacking creature's damage.",
                                     "now": "Trample is a static ability that modifies the rules for assigning an attacking creature's combat damage."}
    assert ch.changed["100.2"]["now"].startswith("A rule inserted before the others")
    assert ch.changed["100.2"]["was"].startswith("Splitting the combat damage")
    brief = ch.brief()
    assert brief["counts"] == {"added": 1, "removed": 2, "renumbered": 3, "shifted": 1, "changed": 2}
    assert brief["renumbered"][-1] == {"from": "510.3", "to": "510.4"} and brief["shifted"] == [{"number": "100.3", "holds_the_words_of": "100.2"}]
    assert [c["number"] for c in brief["changed"]] == ["100.2", "702.19a"]  # rule order, not file or dict order
    assert [r["number"] for r in brief["removed"]] == ["100.9", "glossary:Banding"]
    assert brief["by_subsection"][0] == {"section": "100", "heading": "General", "changes": 4}
    assert not brief["capped"]


def test_a_brief_is_capped_and_always_the_same():
    ch = live(two_editions()).compare().changes
    capped = ch.brief(limit=1)
    assert capped["capped"] and len(capped["removed"]) == 1 and capped["removed"][0]["number"] == "100.9"
    assert capped["counts"] == ch.brief()["counts"]  # the counts are the whole difference, the lists are the first of it
    assert json.dumps(ch.brief()) == json.dumps(live(two_editions()).compare().changes.brief())
    assert all(len(str(v)) <= 250 for item in ch.brief()["changed"] for v in item.values())  # a pointer to a rule, not a copy of it


def test_rule_numbers_sort_the_way_the_rules_are_numbered():
    numbers = ["702.19b", "702.2", "glossary:Zed", "702.19", "7", "702.19a", "glossary:Alpha", "702"]
    assert sorted(numbers, key=rc.number_key) == ["7", "702", "702.2", "702.19", "702.19a", "702.19b", "glossary:Alpha", "glossary:Zed"]


def test_a_rule_that_only_grew_or_only_lost_a_sentence_says_which():
    assert rc.first_change("One. Two.", "One. Two. Three.") == {"was": None, "now": "Three."}
    assert rc.first_change("One. Two. Three.", "One. Three.") == {"was": "Two.", "now": None}
    assert rc.first_change("Same.", "Same.") == {"was": None, "now": None}


# -- finding the previous edition on Wizards' CDN, with nothing stored --------------------------------------------------------------

def test_the_previous_edition_is_found_by_its_dated_file_name_across_a_gap_of_weeks():
    universe = two_editions()
    rules = live(universe)
    first = rules.compare()
    heads = [c.path for c in universe.wizards.calls if c.method == "HEAD" and c.host == "media.wizards.com"]
    assert any(p.endswith("20270105.txt") for p in heads) and len(heads) >= 57  # one HEAD for each day back to the old file's name
    gets = [c.path for c in universe.wizards.calls if c.method == "GET" and c.path.endswith(".txt")]
    assert sorted(p[-12:] for p in gets) == ["20270105.txt", "20270303.txt"]  # each edition read once
    before = len(universe.wizards.calls)
    assert rules.compare() is first and len(universe.wizards.calls) == before  # asked again: nothing is fetched, the brief is in memory


def test_a_correction_published_under_a_new_name_is_not_taken_for_the_previous_edition():
    universe = two_editions()
    universe.wizards.publish(NEW.replace("invented", "corrected"), "20270215", listed=False)  # same effective date as the current one
    comparison = live(universe).compare()
    assert comparison.previous.version == "2027-01-05"
    assert [s.file_date for s in comparison.same_effective_date] == ["2027-02-15"]


def test_with_no_earlier_file_the_brief_says_so_instead_of_inventing_one():
    universe = Universe(seed=False)
    universe.wizards.publish(NEW, "20270303")
    comparison = live(universe).compare()
    assert comparison.previous is None and comparison.changes is None
    assert "No earlier edition was found" in comparison.note and "`previous`" in comparison.note


def test_a_named_previous_edition_skips_the_search_and_an_unknown_one_is_refused():
    universe = two_editions()
    rules = live(universe)
    assert rules.compare("2027-01-05").previous.version == "2027-01-05"
    assert not [c for c in universe.wizards.calls if c.method == "HEAD" and c.path.endswith("20270104.txt")]
    assert rules.compare("20261201").previous is None
    with pytest.raises(ValueError):
        rules.compare("last spring")


def test_when_wizards_cannot_be_asked_the_brief_is_unavailable_not_guessed():
    universe = two_editions()
    rules = live(universe)
    rules.edition()
    universe.wizards.outage = True  # the edition is cached; asking the CDN for an earlier one fails
    with pytest.raises(RulesUnavailable):
        rules.compare()


def test_nothing_of_either_edition_is_kept_beyond_the_brief(app):
    rules = live(two_editions())
    comparison = rules.compare()
    kept = json.dumps([comparison.changes.brief(rc.MAX_LIMIT), comparison.changes.old_text, comparison.changes.new_text])
    assert "It is old" not in kept  # only the first changed sentence of a rule, not the rule
    assert not hasattr(comparison.changes, "rows") and not hasattr(comparison, "rows")
    from sqlalchemy import inspect
    assert not {"rules", "rules_versions", "rules_changes"} & set(inspect(app.state.db.engine).get_table_names())


# -- the answer: REST and the MCP tool ------------------------------------------------------------------------------------------

def add_history(app):
    """Rulings and a ban the Vault holds, some inside the window (from the previous edition's day) and some before it."""
    with app.state.db.sessions() as db:
        cs.sync_rulings(db, [{"object": "ruling", "oracle_id": BOLT, "source": "wotc", "published_at": f"{2026 + i // 12}-{1 + i % 12:02d}-15",
                              "comment": f"Ruling {i} about the bolt, " + "with a long explanation " * 20} for i in range(36)])
        rider = {"object": "card", "id": "p-rider", "oracle_id": oid(40), "name": "Test Rider", "layout": "normal", "mana_cost": "{1}", "cmc": 1.0,
                 "type_line": "Creature — Human", "oracle_text": "", "colors": [], "color_identity": [], "keywords": [],
                 "legalities": {"commander": "legal"}, "edhrec_rank": 5, "digital": False}
        base = CARDS + [make_card(BOLT, "Lightning Bolt", "Lightning Bolt deals 3 damage to any target.")]
        cs.sync_oracle_cards(db, base + [rider], today=date(2026, 12, 1))
        cs.sync_oracle_cards(db, base + [{**rider, "legalities": {"commander": "banned"}}], today=date(2027, 2, 1))
        db.commit()


@pytest.fixture
def brief_app(signed_in, app):
    load(app)
    add_history(app)
    app.state.rules_live.reset(two_editions().transport)
    app.state.rules_live.edition()
    return signed_in


def test_the_endpoint_gives_the_brief_the_rulings_and_the_legality_changes_with_provenance(brief_app):
    body = brief_app.get(f"{V1}/rules/changes", params={"limit": 5}).json()
    assert body["current"]["version"] == "2027-03-03" and body["previous"]["version"] == "2027-01-05"
    assert body["previous"]["url"].endswith("MagicCompRules%2020270105.txt")
    assert body["rules"]["counts"]["changed"] == 2 and body["rules"]["changed"][0]["number"] == "100.2"
    assert body["since"] == "2027-01-05"
    rulings = body["rulings"]
    assert rulings["total"] == 24 and len(rulings["items"]) == 5 and rulings["capped"] and rulings["by_source"] == {"wotc": 24}
    assert [r["published_at"] for r in rulings["items"]] == sorted((r["published_at"] for r in rulings["items"]), reverse=True)
    assert all(r["published_at"] >= "2027-01-05" and len(r["comment"]) <= 240 for r in rulings["items"])  # a pointer, cut short
    assert body["legality"]["items"] == [{"card": "Test Rider", "oracle_id": oid(40), "format": "commander", "old": "legal", "new": "banned",
                                          "observed_on": "2027-02-01"}]
    assert "the day the Vault saw the change" in body["legality"]["note"]
    blocks = body["provenance"]
    wizards = [b for b in blocks if b["source"] == "Wizards of the Coast"]
    assert [b["version"] for b in wizards] == ["2027-03-03", "2027-01-05"] and all(b["notice"] and "stores no copy" in b["origin"] for b in wizards)
    assert {b["source"] for b in blocks if b["kind"] == "source"} == {"Wizards of the Coast", "Scryfall"}
    computed = [b["origin"] for b in blocks if b["kind"] == "computed"]
    assert any("change brief" in o for o in computed) and any("legality change log" in o for o in computed)
    assert not any(b["kind"] == "computed" and b["source"] != "The Vault" for b in blocks)


def test_the_window_can_be_chosen_and_a_bad_date_or_unknown_edition_is_refused(brief_app):
    narrow = brief_app.get(f"{V1}/rules/changes", params={"since": "2027-06-01"}).json()
    assert narrow["since"] == "2027-06-01" and narrow["rulings"]["total"] == 19 and narrow["legality"]["total"] == 0
    assert brief_app.get(f"{V1}/rules/changes", params={"since": "soon"}).status_code == 422
    assert brief_app.get(f"{V1}/rules/changes", params={"previous": "2027-01-06"}).status_code == 404
    assert brief_app.get(f"{V1}/rules/changes", params={"limit": 0}).status_code == 422
    assert brief_app.get(f"{V1}/rules/changes", params={"limit": 51}).status_code == 422


def test_without_an_earlier_edition_the_rules_part_is_empty_and_says_why_but_the_rest_still_answers(signed_in, app):
    load(app)
    add_history(app)
    serve_rules(app)  # one edition only
    body = signed_in.get(f"{V1}/rules/changes").json()
    assert body["previous"] is None and body["rules"] is None and "No earlier edition was found" in body["note"]
    assert "last 30 days" in body["note"] and body["provenance"][0]["source"] == "Wizards of the Coast"


def test_the_endpoint_says_when_wizards_cannot_be_read(signed_in, app):
    import httpx
    app.state.rules_live.reset(httpx.MockTransport(lambda r: httpx.Response(503)))
    res = signed_in.get(f"{V1}/rules/changes")
    assert res.status_code == 503 and "could not be read" in res.json()["detail"]


def test_the_endpoint_needs_a_signed_in_person(client, app):
    assert client.get(f"{V1}/rules/changes").status_code == 401


def test_an_agent_reads_the_brief_through_the_mcp_tool_and_it_is_read_only(agent, app, bot):  # noqa: F811
    add_history(app)
    app.state.rules_live.reset(two_editions().transport)
    read = make_token(agent)
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
    assert tools["rules_changes"]["annotations"]["readOnlyHint"] is True
    assert "The answer names the two editions compared" in " ".join(tools["rules_changes"]["description"].split())
    # #241: the order to say them is in the instructions every host reads, not in the description
    from vault.api import mcp
    assert "say which two editions you compared" in " ".join(mcp.INSTRUCTIONS.split())
    result = call_tool(bot, read, "rules_changes", limit=3)
    assert result["isError"] is False, result["content"][0]["text"]
    body = result["structuredContent"]
    assert body["previous"]["version"] == "2027-01-05" and body["rules"]["counts"]["removed"] == 2
    assert body["provenance"] and all(b["kind"] in ("source", "computed") for b in body["provenance"])
    bad = rpc(bot, "tools/call", {"name": "rules_changes", "arguments": {"since": "not a date"}}, read).json()
    assert bad["error"]["code"] == -32602  # the schema says date: refused before the Vault is asked


# -- which citations in the skills and agents are affected ------------------------------------------------------------------------

def test_citations_are_read_from_rule_words_and_dotted_numbers_but_not_from_other_numbers():
    text = ("See rule 603.3b and rules 506 to 511; CR 702.19, also 613.1a. A 100-card deck costs $100.50 and 99 cards.\n"
            "Plain 704.5g and nothing about rule 12.")
    assert rc.cited_numbers(text) == [(1, "603.3b"), (1, "506"), (1, "511"), (1, "702.19"), (1, "613.1a"), (2, "704.5g")]


def test_the_repositorys_own_skills_and_agents_cite_rule_numbers_the_check_can_see():
    seen = {c.number for c in rc.scan(ROOT)}
    assert {"603.3b", "101", "601", "603", "613", "614", "701", "702", "704", "810", "903", "506", "511", "613.1"} <= seen
    assert {c.path for c in rc.scan(ROOT)} >= {"skills/rules-judge/SKILL.md", "skills/vault-attribution/SKILL.md"}


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_citation_of_a_removed_or_renumbered_rule_fails_and_a_changed_rule_warns(tmp_path):
    write(tmp_path, "skills/judge/SKILL.md", "Rule 702.19b is fine.\nRule 100.9 is gone.\nSee rule 702.19a.\nAnd rule 510.3 moved.\n"
                                           "Rule 100.3 shifted.\nRule 999.9 never existed.\n")
    write(tmp_path, "agents/helper.md", "Cite rule 510.1.\n")
    rules = live(two_editions())
    result = rc.check(rc.scan(tmp_path), rules.edition(), rules.compare().changes)
    assert [(f["number"], f["line"]) for f in result["failures"]] == [("100.9", 2), ("510.3", 4), ("999.9", 6)]
    problems = {f["number"]: f["problem"] for f in result["failures"]}
    assert "removed from the rules after the 2027-01-05 edition" in problems["100.9"]
    assert "renumbered" in problems["510.3"] and "now 510.4" in problems["510.3"]
    assert "not a rule number in the current edition" in problems["999.9"]
    assert [(w["number"], w["line"]) for w in result["warnings"]] == [("702.19a", 3), ("100.3", 5)]
    assert "now holds the words that were rule 100.2" in result["warnings"][1]["problem"]
    assert result["cited"] == 7 and result["current_version"] == "2027-03-03" and result["previous_version"] == "2027-01-05"
    text = rc.report(result)
    assert "FAIL skills/judge/SKILL.md:2 rule 100.9" in text and "WARN skills/judge/SKILL.md:3 rule 702.19a" in text


def test_the_script_fails_for_a_removed_rule_only_and_warns_for_a_changed_one(tmp_path, capsys):
    rules = live(two_editions())
    write(tmp_path, "skills/a/SKILL.md", "Rule 702.19b and rule 702.19a.\n")
    assert cli.main(["--root", str(tmp_path)], rules=rules) == 0  # a changed rule is a warning
    out = capsys.readouterr().out
    assert "WARN skills/a/SKILL.md:1 rule 702.19a" in out and "FAIL" not in out
    write(tmp_path, "skills/b/SKILL.md", "Rule 100.9 was removed.\n")
    assert cli.main(["--root", str(tmp_path)], rules=rules) == 1
    assert "FAIL skills/b/SKILL.md:1 rule 100.9" in capsys.readouterr().out


def test_the_script_writes_the_job_summary_and_github_annotations(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    write(tmp_path, "skills/a/SKILL.md", "Rule 702.19a.\nRule 100.9.\n")
    assert cli.main(["--root", str(tmp_path)], rules=live(two_editions())) == 1
    out = capsys.readouterr().out
    assert "::warning file=skills/a/SKILL.md,line=1::" in out and "::error file=skills/a/SKILL.md,line=2::" in out
    text = summary.read_text(encoding="utf-8")
    assert "2027-01-05" in text and "2027-03-03" in text and "Added" in text and "100.9" in text


def test_the_script_cannot_check_without_wizards_and_says_so(tmp_path, capsys):
    import httpx
    write(tmp_path, "skills/a/SKILL.md", "Rule 702.19a.\n")
    assert cli.main(["--root", str(tmp_path)], rules=LiveRules(transport=httpx.MockTransport(lambda r: httpx.Response(503)))) == 2
    assert "could not be read" in capsys.readouterr().out


def test_the_script_with_only_one_edition_still_fails_for_a_number_that_is_not_a_rule(tmp_path, capsys):
    universe = Universe(seed=False)
    universe.wizards.publish(NEW, "20270303")
    write(tmp_path, "skills/a/SKILL.md", "Rule 702.19a and rule 999.9.\n")
    assert cli.main(["--root", str(tmp_path)], rules=live(universe)) == 1
    out = capsys.readouterr().out
    assert "no earlier edition was found" in out and "FAIL skills/a/SKILL.md:1 rule 999.9" in out and "WARN" not in out


def test_the_weekly_workflow_runs_the_check_read_only():
    import yaml
    path = ROOT / ".github" / "workflows" / "rules-reconciler.yml"
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = wf.get(True) or wf["on"]
    assert set(triggers) == {"schedule", "workflow_dispatch"} and wf["permissions"] == {"contents": "read"}
    assert any(s.get("run") == "python scripts/check_rule_citations.py" for s in wf["jobs"]["citations"]["steps"])
    assert "secrets." not in path.read_text(encoding="utf-8")


JUDGES = ("vault-judge", "vault-commander-expert", "vault-devils-advocate", "vault-limited-expert", "vault-pauper-expert", "vault-pioneer-expert",
          "vault-standard-expert", "vault-synergy-analyst", "vault-two-headed-giant-expert")  # the agents that can open a rule


def test_every_member_that_reads_rules_is_told_to_cite_the_edition_and_the_chair_and_judge_to_check_for_changes():
    """#107: an answer that names rule 702.19b without saying which edition it read is not checkable; the edition is the `version`
    every rules tool returns. The council brief a connector gets, each agent file, and the skills carry the instruction."""
    from vault import experts
    flat = lambda text: " ".join(text.lower().split())  # noqa: E731
    council = flat(json.dumps(experts.council("commander", "tune it")))
    assert "cite every rule number with the comprehensive rules edition" in council and "rules_changes first" in council
    for name in JUDGES:
        brief = flat(experts.brief(name)["brief"])
        assert "cite every rule number" in brief or "citing every rule number with the edition" in brief, name
        assert "<version>" in brief, name
    assert "rules_changes" in flat(experts.brief("vault-judge")["brief"])
    for skill in ("rules-judge", "expert-council"):
        for base in (ROOT / "skills", ROOT / "plugins" / "the-vault-openai" / "skills"):
            text = flat((base / skill / "SKILL.md").read_text(encoding="utf-8"))
            assert "<version>" in text and "rules_changes" in text, (skill, base)
    from vault.api import mcp
    assert "rules_changes" in mcp.INSTRUCTIONS


def test_the_agents_that_read_rules_are_the_ones_this_test_lists():
    import re
    named = {p.stem for p in (ROOT / "agents").glob("*.md") if re.search(r"^\s+- get_rule$", p.read_text(encoding="utf-8"), re.M)}
    assert named == set(JUDGES)


def rows(**numbers):
    from types import SimpleNamespace
    return SimpleNamespace(version="2027-01-01", rows={n.replace("_", "."): {"number": n.replace("_", "."), "text": t, "kind": "rule", "parent": None}
                                                       for n, t in numbers.items()})


def test_a_rule_that_only_cites_another_number_than_before_has_moved_and_is_not_removed_and_added():
    """Real editions insert a rule and the rules after it move down; they also cite each other by number (rule 506.7c cited 506.7,
    its successor cites 506.8). The words are the same apart from the number they cite."""
    cast = "Some spells state that they may be cast only during combat in addition to the criteria described in rule {}."
    old = rows(**{"506_7c": cast.format("506.7"), "506_9": "Nothing else here, but long enough to be a rule of its own."})
    new = rows(**{"506_8c": cast.format("506.8"), "506_9": "Nothing else here, but long enough to be a rule of its own."})
    ch = rc.diff(old, new)
    assert ch.renumbered == {"506.7c": "506.8c"} and not ch.added and not ch.removed and not ch.changed
    assert ch.gone == {"506.7c"}


def test_a_long_rule_with_one_word_changed_shows_the_words_around_the_change():
    names = ", ".join(f"Type{i}" for i in range(60))
    first = rc.first_change(f"The types are {names}.", f"The types are {names.replace('Type30', 'Type30, Newtype')}.")
    assert "Type30" in first["was"] and "Newtype" in first["now"] and first["was"].startswith("…") and len(first["now"]) <= rc.SENTENCE_CAP + 2
    short = rc.first_change("One short sentence.", "Another short sentence.")
    assert short == {"was": "One short sentence.", "now": "Another short sentence."}
