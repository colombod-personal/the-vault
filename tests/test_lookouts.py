"""Lookouts read from real Oracle text (bead vault-85p.1): what in a deck can turn against its owner."""

from vault import lookouts
from vault.possible_loops import CardText

# Oracle text as Scryfall serves it (read through the Vault on 2026-10-11)
HARMONIC = CardText("Harmonic Sliver", "Creature — Sliver",
                    'All Slivers have "When this permanent enters, destroy target artifact or enchantment."')
CRYSTALLINE = CardText("Crystalline Sliver", "Creature — Sliver",
                       "All Slivers have shroud. (They can't be the targets of spells or abilities.)")
SHADOW = CardText("Shadow Sliver", "Creature — Sliver",
                  "All Sliver creatures have shadow. (They can block or be blocked by only creatures with shadow.)")
LEGION = CardText("Sliver Legion", "Legendary Creature — Sliver",
                  "All Sliver creatures get +1/+1 for each other Sliver on the battlefield.")
QUEEN = CardText("Sliver Queen", "Legendary Creature — Sliver", "{2}: Create a 1/1 colorless Sliver creature token.")
HIVEPOOL = CardText("Thrumming Hivepool", "Artifact",
                    "Affinity for Slivers (This spell costs {1} less to cast for each Sliver you control.)\nSlivers you control have "
                    "double strike and haste.\nAt the beginning of your upkeep, create two 1/1 colorless Sliver creature tokens.")
SKULLCLAMP = CardText("Skullclamp", "Artifact — Equipment",
                      "Equipped creature gets +1/-1.\nWhenever equipped creature dies, draw two cards.\nEquip {1}")
SOL_RING = CardText("Sol Ring", "Artifact", "{T}: Add {C}{C}.")
GALERIDER = CardText("Galerider Sliver", "Creature — Sliver", "Sliver creatures you control have flying.")
RHYSTIC = CardText("Rhystic Study", "Enchantment",
                   "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.")


def kinds(result):
    return [x["kind"] for x in result["lookouts"]]


def test_harmonic_sliver_will_destroy_your_own_artifacts_worse_with_token_makers():
    found = lookouts.read([HARMONIC, SOL_RING, SKULLCLAMP, RHYSTIC, QUEEN, HIVEPOOL])
    hit = next(x for x in found["lookouts"] if x["kind"] == "forced_self_destruction")
    assert hit["severity"] == "high" and hit["rules"] == ["603.3d", "601.2c"]
    assert {"Sol Ring", "Skullclamp", "Rhystic Study", "Thrumming Hivepool"} <= set(hit["cards"])
    assert "Sliver Queen" in hit["what"] and "Thrumming Hivepool" in hit["what"]  # the token makers that bring more triggers
    assert "must hit yours" in hit["what"]


def test_crystalline_sliver_stops_your_own_equipment():
    found = lookouts.read([CRYSTALLINE, SKULLCLAMP, SOL_RING])
    hit = next(x for x in found["lookouts"] if x["kind"] == "own_targets_blocked")
    assert hit["cards"] == ["Crystalline Sliver", "Skullclamp"] and hit["rules"] == ["702.18a", "702.6a"]


def test_sliver_bonuses_are_shared_with_opponents_and_shadow_hurts_your_blocks():
    found = lookouts.read([LEGION, SHADOW, HARMONIC, GALERIDER])
    hit = next(x for x in found["lookouts"] if x["kind"] == "shared_with_opponents")
    assert set(hit["cards"]) == {"Sliver Legion", "Shadow Sliver", "Harmonic Sliver"}  # 'you control' is not shared
    assert "Shadow Sliver" in hit["what"] and hit["rules"] == ["702.28b"] and hit["severity"] == "high"


def test_near_misses_do_not_fire():
    # 'you control' grants, a lone lord, hexproof instead of shroud, and an optional trigger are not lookouts
    hexproof = CardText("Shield Test", "Creature — Sliver", "All Slivers have hexproof.")
    optional = CardText("Optional Test", "Creature — Sliver",
                        'All Slivers have "When this permanent enters, you may destroy target artifact."')
    assert kinds(lookouts.read([GALERIDER, SKULLCLAMP, SOL_RING])) == []
    assert kinds(lookouts.read([LEGION, QUEEN])) == []  # one shared lord is not reported
    assert "own_targets_blocked" not in kinds(lookouts.read([hexproof, SKULLCLAMP]))
    assert "forced_self_destruction" not in kinds(lookouts.read([optional, SOL_RING]))
    assert kinds(lookouts.read([CRYSTALLINE, SOL_RING])) == []  # shroud with nothing of yours that targets: nothing to warn


def test_the_answer_says_how_it_was_found_and_orders_worst_first():
    found = lookouts.read([HARMONIC, CRYSTALLINE, SHADOW, LEGION, SKULLCLAMP, SOL_RING])
    assert found["source"] == "the Vault's reading of card text" and "get_rule" in found["note"]
    severities = [x["severity"] for x in found["lookouts"]]
    assert severities == sorted(severities, key={"high": 0, "medium": 1, "low": 2}.get)
    for x in found["lookouts"]:
        assert x["title"] and x["what"] and x["advice"] and x["cards"]


# -- end to end: the lookouts arrive inside the deck stats answer and the council brief (one call, any host) -------------------

import pytest  # noqa: E402

from tests.test_deck_api import card, cs, TODAY, V1  # noqa: E402

SLIVERS = [card(101, "Sliver Overlord", "Legendary Creature — Sliver Mutant", ["W", "U", "B", "R", "G"], 5),
           card(102, "Harmonic Sliver", "Creature — Sliver", ["G", "W"], 3, text=HARMONIC.text),
           card(103, "Sol Ring", "Artifact", [], 1, text=SOL_RING.text),
           card(104, "Plains", "Basic Land — Plains", [], 0)]
SLIVER_DECK = "Commander\n1 Sliver Overlord\n\nDeck\n1 Harmonic Sliver\n1 Sol Ring\n10 Plains\n"


@pytest.fixture
def slivers(signed_in, app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, SLIVERS, today=TODAY)
        cs.record_source(db, "oracle_cards", version="oracle_cards-1", rows=1)
        db.commit()
    return signed_in


def test_deck_stats_carry_the_lookouts(slivers):
    res = slivers.post(f"{V1}/stats", json={"text": SLIVER_DECK})
    assert res.status_code == 200, res.text
    found = res.json()["result"]["lookouts"]
    hit = next(x for x in found["lookouts"] if x["kind"] == "forced_self_destruction")
    assert "Sol Ring" in hit["cards"] and found["source"] == "the Vault's reading of card text"


def test_the_council_brief_for_a_saved_deck_carries_the_lookouts_and_tells_the_chair_to_use_them(slivers):
    saved = slivers.post(V1, json={"name": "Slivers", "text": SLIVER_DECK, "format": "commander"})
    assert saved.status_code in (200, 201), saved.text
    brief = slivers.get("/api/v1/council", params={"deck_id": saved.json()["id"]}).json()
    assert any(x["kind"] == "forced_self_destruction" for x in brief["lookouts"]["lookouts"])
    assert "lookouts" in brief["how_to_run"]
