"""The Comprehensive Rules read live from Wizards (vault/rules_live.py, docs/rules-index.md): nothing stored, a
navigation map to find your way, the edition picked up automatically. The text below is invented, in the file's format."""

import httpx
import pytest
from sqlalchemy import inspect

from tests.test_catalog_api import serve_rules
from twins.universe import Universe
from vault.rules_live import LiveRules, RulesUnavailable

V1 = "/api/v1/catalog"
TEXT = """Magic: The Gathering Comprehensive Rules

These rules are effective as of March 3, 2027.

Contents

1. Game Concepts
100. General
5. Turn Structure
510. Combat Damage Step
7. Additional Rules
702. Keyword Abilities

1. Game Concepts

100. General

100.1. These rules apply to any game.

5. Turn Structure

510. Combat Damage Step

510.1. First, the active player announces how each attacking creature assigns its combat damage. See rule 702.19.

7. Additional Rules

702. Keyword Abilities

702.19. Trample

702.19a Trample is a static ability that modifies the rules for assigning an attacking creature's combat damage.

702.19b The controller of an attacking creature with trample first assigns damage to the creatures blocking it. See rule 510.1.

702.19c If all the creatures blocking it are removed from combat, the creature assigns all its damage to the player.

Glossary

Trample
A keyword ability that modifies how a creature assigns combat damage. See rule 702.19.

Combat Damage Step
The fourth step of combat. See rule 510.

Credits

Invented for tests.
"""


def live(text=TEXT, edition="20270303"):
    universe = Universe(seed=False)
    universe.wizards.publish(text, edition)
    return LiveRules(transport=universe.transport), universe


def test_the_map_knows_parents_children_siblings_and_references_both_ways():
    ed = live()[0].edition()
    r = ed.rule("702.19b")
    assert r["parent"]["number"] == "702.19" and r["parent"]["text"] == "Trample"
    assert r["previous"]["number"] == "702.19a" and r["next"]["number"] == "702.19c"
    assert [c["number"] for c in r["cites"]] == ["510.1"]
    assert "702.19b" in [c["number"] for c in ed.rule("510.1")["cited_by"]]
    assert [c["number"] for c in ed.rule("702.19")["children"]] == ["702.19a", "702.19b", "702.19c"]


def test_the_outline_drills_down_from_sections_to_rules():
    ed = live()[0].edition()
    assert [i["number"] for i in ed.outline()["items"]] == ["1", "5", "7"]
    assert [(i["number"], i["text"]) for i in ed.outline("7")["items"]] == [("702", "Keyword Abilities")]
    assert [i["number"] for i in ed.outline("702")["items"]] == ["702.19"]


def test_a_term_leads_to_its_defining_rule_and_glossary_entry_and_search_puts_it_first():
    ed = live()[0].edition()
    trample = ed.term("trample")
    assert [r["number"] for r in trample["rules"]] == ["702.19"] and "keyword ability" in trample["glossary"]["text"]
    results, _ = ed.search("does trample damage go to the player when the blockers are gone", 3)
    assert results[0]["number"].startswith("702.19")


def test_a_new_edition_on_wizards_page_is_picked_up_without_anyone_tracking_it():
    clock = [0.0]
    universe = Universe(seed=False)
    universe.wizards.publish(TEXT, "20270303")
    rules = LiveRules(transport=universe.transport, page_ttl=100, clock=lambda: clock[0])
    assert rules.edition().version == "2027-03-03"
    universe.wizards.publish(TEXT.replace("March 3, 2027", "June 9, 2027"), "20270609")
    assert rules.edition().version == "2027-03-03"  # page checked again only after the ttl
    clock[0] = 101
    assert rules.edition().version == "2027-06-09"


def test_when_wizards_cannot_be_reached_the_rules_are_unavailable_not_invented():
    down = LiveRules(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(RulesUnavailable):
        down.edition()


def test_a_cached_edition_survives_a_short_outage():
    clock = [0.0]
    universe = Universe(seed=False)
    universe.wizards.publish(TEXT)
    rules = LiveRules(transport=universe.transport, page_ttl=10, clock=lambda: clock[0])
    rules.edition()
    universe.wizards.fail_next("/en/rules", 503, times=5)
    clock[0] = 11
    assert rules.edition().version == "2027-03-03"


def test_nothing_of_the_rules_is_stored(app):
    tables = set(inspect(app.state.db.engine).get_table_names())
    assert not {"rules", "rules_versions"} & tables


def test_the_api_navigates_the_live_rules(signed_in, app):
    serve_rules(app, TEXT)
    outline = signed_in.get(f"{V1}/rules", params={"under": "7"}).json()
    assert outline["version"] == "2027-03-03" and outline["items"][0]["number"] == "702"
    assert outline["provenance"][0]["source"] == "Wizards of the Coast" and "stores no copy" in outline["provenance"][0]["origin"]
    term = signed_in.get(f"{V1}/rules/term/Trample").json()
    assert term["rules"][0]["number"] == "702.19"
    rule = signed_in.get(f"{V1}/rules/702.19b").json()["rule"]
    assert rule["parent"]["number"] == "702.19" and rule["cites"][0]["number"] == "510.1"
    assert signed_in.get(f"{V1}/rules/term/Nonexistent").status_code == 404
    assert signed_in.get(f"{V1}/rules", params={"under": "999"}).status_code == 404


def test_the_api_says_when_the_rules_cannot_be_read(signed_in, app):
    app.state.rules_live.reset(httpx.MockTransport(lambda r: httpx.Response(503)))
    res = signed_in.get(f"{V1}/rules/search", params={"q": "trample"})
    assert res.status_code == 503 and "could not be read" in res.json()["detail"]


# -- how a new edition, or a corrected file, is noticed (#143, #144) ------------------------------------------------------------

def test_a_file_corrected_in_place_under_the_same_name_is_picked_up_after_the_ttl():
    """Wizards can fix a typo in the published file without changing its name: the page's link is the same, only the file's
    ETag moves. One HEAD per ttl notices it; an unchanged file is not downloaded again."""
    clock = [0.0]
    universe = Universe(seed=False)
    universe.wizards.publish(TEXT, "20270303")
    rules = LiveRules(transport=universe.transport, page_ttl=100, clock=lambda: clock[0])
    first = rules.edition().rows["100.1"]["text"]
    gets = lambda: [c for c in universe.wizards.calls if c.method == "GET" and c.path.endswith(".txt")]  # noqa: E731
    assert len(gets()) == 1
    clock[0] = 101
    assert rules.edition().rows["100.1"]["text"] == first and len(gets()) == 1  # same ETag: not downloaded again
    assert [c.method for c in universe.wizards.calls if c.path.endswith(".txt")] == ["GET", "HEAD"]
    universe.wizards.publish(TEXT.replace("These rules apply to any game.", "These rules apply to every game."), "20270303")  # a correction
    clock[0] = 150
    assert rules.edition().rows["100.1"]["text"] == first  # not before the ttl is up
    clock[0] = 202
    assert "every game" in rules.edition().rows["100.1"]["text"] and len(gets()) == 2


def test_an_edition_published_before_its_effective_date_says_so_in_its_provenance():
    rules, _ = live(TEXT, "20270303")  # effective as of March 3, 2027
    block = rules.edition().provenance()[0]
    assert block.version == "2027-03-03" and "takes effect on 2027-03-03" in block.origin and "previous edition is in force" in block.origin
    past, _ = live(TEXT.replace("March 3, 2027", "March 3, 2020"), "20200303")
    assert "takes effect" not in past.edition().provenance()[0].origin
