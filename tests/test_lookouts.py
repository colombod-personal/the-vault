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
    assert hit["causes"] == ["Harmonic Sliver"] and hit["hurts"] == ["Rhystic Study", "Skullclamp", "Sol Ring", "Thrumming Hivepool"]
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


# -- at the point of the plan (bead vault-064.11): a proposed add that turns against the deck is flagged ------------------------
# Oracle text as Scryfall serves it (read through its API on 2026-10-11). Each of these is a real card.

OVERLORD = CardText("Sliver Overlord", "Legendary Creature — Sliver Mutant",
                    "{3}: Search your library for a Sliver card, reveal that card, put it into your hand, then shuffle.\n"
                    "{3}: Gain control of target Sliver. (This effect lasts indefinitely.)")
ARCANE_SIGNET = CardText("Arcane Signet", "Artifact", "{T}: Add one mana of any color in your commander's color identity.")
SMOTHERING = CardText("Smothering Tithe", "Enchantment",
                      "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token. "
                      "(It's an artifact with \"{T}, Sacrifice this token: Add one mana of any color.\")")
BONESPLITTER = CardText("Bonesplitter", "Artifact — Equipment", "Equipped creature gets +2/+0.\nEquip {1}")
GEMHIDE = CardText("Gemhide Sliver", "Creature — Sliver", "All Slivers have \"{T}: Add one mana of any color.\"")
MESMERIC = CardText("Mesmeric Sliver", "Creature — Sliver",
                    "All Slivers have \"When this permanent enters, you may fateseal 1.\" (To fateseal 1, its controller looks at the "
                    "top card of an opponent's library, then they may put that card on the bottom of that library.)")
NECROTIC = CardText("Necrotic Sliver", "Creature — Sliver", "All Slivers have \"{3}, Sacrifice this permanent: Destroy target permanent.\"")
SMASH = CardText("Smash to Smithereens", "Instant", "Destroy target artifact. Smash to Smithereens deals 3 damage to that artifact's controller.")
PLAINS = CardText("Plains", "Basic Land — Plains", "")

THE_REAL = [OVERLORD, HARMONIC, CRYSTALLINE, GALERIDER, SOL_RING, ARCANE_SIGNET, RHYSTIC, SMOTHERING, SKULLCLAMP, BONESPLITTER, GEMHIDE,
            MESMERIC, NECROTIC, SMASH, PLAINS]
COLOURS = {"Sliver Overlord": ["W", "U", "B", "R", "G"], "Harmonic Sliver": ["G", "W"], "Crystalline Sliver": ["W", "U"],
           "Galerider Sliver": ["U"], "Rhystic Study": ["U"], "Smothering Tithe": ["W"], "Gemhide Sliver": ["G"], "Mesmeric Sliver": ["U"],
           "Necrotic Sliver": ["W", "B"], "Smash to Smithereens": ["R"]}
RAMP = ("Harmonic Sliver", "Crystalline Sliver", "Gemhide Sliver")  # the test's own role tags: they only decide who is a candidate


def test_a_lookout_names_the_cards_that_do_it_and_the_cards_it_hurts():
    found = lookouts.read([HARMONIC, SOL_RING, SKULLCLAMP, QUEEN])
    hit = next(x for x in found["lookouts"] if x["kind"] == "forced_self_destruction")
    assert hit["causes"] == ["Harmonic Sliver"] and hit["hurts"] == ["Skullclamp", "Sol Ring"]  # the token maker is in cards, not in hurts
    blocked = next(x for x in lookouts.read([CRYSTALLINE, SKULLCLAMP])["lookouts"] if x["kind"] == "own_targets_blocked")
    assert blocked["causes"] == ["Crystalline Sliver"] and blocked["hurts"] == ["Skullclamp"]


def test_a_plan_is_read_on_the_deck_after_it_not_on_the_deck_before_plus_the_adds():
    deck = [OVERLORD, SOL_RING]
    # Harmonic Sliver comes in while the only artifact or enchantment goes out: nothing is left for it to destroy
    assert lookouts.plan(deck, [OVERLORD, HARMONIC])["lookouts"] == []
    # the same add with the artifact kept: the lookout is new and names the card the plan brings in
    plan = lookouts.plan(deck, [OVERLORD, SOL_RING, HARMONIC])
    hit = plan["lookouts"][0]
    assert hit["kind"] == "forced_self_destruction" and hit["new"] is True and hit["new_cards"] == ["Harmonic Sliver"]
    assert plan["by_card"]["Harmonic Sliver"][0]["role"] == "cause" and plan["by_card"]["Harmonic Sliver"][0]["hurts"] == ["Sol Ring"]
    assert "Harmonic Sliver would turn against this deck" in plan["warnings"]["Harmonic Sliver"] and "603.3d" in plan["warnings"]["Harmonic Sliver"]


def test_a_cut_clears_a_lookout_and_a_cut_that_leaves_a_victim_narrows_it_without_making_it_new():
    deck = [OVERLORD, HARMONIC, SOL_RING, ARCANE_SIGNET]
    cleared = lookouts.plan([OVERLORD, HARMONIC, SOL_RING], [OVERLORD, HARMONIC])
    assert cleared["lookouts"] == [] and [x["kind"] for x in cleared["cleared"]] == ["forced_self_destruction"]
    narrowed = lookouts.plan(deck, [OVERLORD, HARMONIC, ARCANE_SIGNET])
    assert [x["new"] for x in narrowed["lookouts"]] == [False] and narrowed["lookouts"][0]["hurts"] == ["Arcane Signet"]
    assert narrowed["cleared"] == [] and narrowed["by_card"] == {}


def test_a_victim_that_joins_the_grantors_deck_and_a_token_maker_are_told_apart():
    victim = lookouts.plan([OVERLORD, HARMONIC], [OVERLORD, HARMONIC, SKULLCLAMP])
    assert victim["by_card"]["Skullclamp"][0]["role"] == "hurt" and victim["by_card"]["Skullclamp"][0]["causes"] == ["Harmonic Sliver"]
    assert "Skullclamp would be hurt by Harmonic Sliver" in victim["warnings"]["Skullclamp"]
    maker = lookouts.plan([OVERLORD, HARMONIC, SOL_RING], [OVERLORD, HARMONIC, SOL_RING, QUEEN])
    assert maker["by_card"]["Sliver Queen"][0]["role"] == "worsens" and maker["lookouts"][0]["new_cards"] == ["Sliver Queen"]


@pytest.mark.parametrize("near_miss", [GEMHIDE, MESMERIC, NECROTIC, SMASH, GALERIDER], ids=lambda c: c.name)
def test_real_cards_that_look_close_do_not_fire_at_the_point_of_the_plan(near_miss):
    # a lone lord, an optional 'may' trigger, an activated (chosen) destroy, a spell that destroys one artifact: none is a lookout
    deck = [OVERLORD, SOL_RING, ARCANE_SIGNET, RHYSTIC, SMOTHERING, SKULLCLAMP, BONESPLITTER]
    plan = lookouts.plan(deck, deck + [near_miss])
    assert plan["lookouts"] == [] and plan["by_card"] == {} and plan["warnings"] == {}


# -- end to end: validate_deck_changes and find_upgrades (the tool and its REST endpoint) ------------------------------------------

def _catalog(app, cards, ramp=()):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, cards, today=TODAY)
        cs.sync_oracle_tags(db, [{"id": "t-ramp", "slug": "ramp", "label": "ramp", "parent_ids": [], "child_ids": [],
                                  "taggings": [{"oracle_id": c["oracle_id"], "weight": "strong"} for c in cards if c["name"] in ramp]}])
        cs.sync_oracle_prices(db, [{"oracle_id": c["oracle_id"], "scryfall_id": c["id"], "usd": 1.0, "usd_foil": None, "eur": None,
                                    "day": TODAY, "source": "scryfall"} for c in cards])
        for name in ("oracle_cards", "oracle_tags", "oracle_prices"):
            cs.record_source(db, name, version=name + "-1", rows=1)
        db.commit()


@pytest.fixture
def planned(signed_in, app):
    cards = [card(300 + i, c.name, c.type_line, COLOURS.get(c.name, []), 2, text=c.text, rank=40 + i) for i, c in enumerate(THE_REAL)]
    _catalog(app, cards, ramp=RAMP)
    return signed_in


def commander_list(*names):
    """A 100-card Commander list: Sliver Overlord, the named cards, and Plains for the rest (so a plan that swaps one card is legal)."""
    return "Commander\n1 Sliver Overlord\n\nDeck\n" + "".join(f"1 {n}\n" for n in names) + f"{99 - len(names)} Plains\n"


def validate(client, deck, adds=(), cuts=()):
    res = client.post(f"{V1}/validate-changes", json={"text": deck, "format": "commander", "adds": list(adds), "cuts": list(cuts)})
    assert res.status_code == 200, res.text
    return res.json()["result"]


SWAP_PLAINS = ["Plains"]  # a cut that keeps the list at exactly 100 cards


def test_harmonic_sliver_added_to_a_deck_of_artifacts_and_enchantments_is_flagged_with_the_cards_it_would_destroy(planned):
    deck = commander_list("Sol Ring", "Arcane Signet", "Rhystic Study", "Smothering Tithe")
    out = validate(planned, deck, adds=["Harmonic Sliver"], cuts=SWAP_PLAINS)
    assert out["valid"] is True and out["issues"] == []  # a lookout is a warning, not an illegal list
    hit = next(x for x in out["lookouts"] if x["kind"] == "forced_self_destruction")
    assert hit["new"] is True and hit["new_cards"] == ["Harmonic Sliver"] and hit["rules"] == ["603.3d", "601.2c"]
    assert hit["hurts"] == ["Arcane Signet", "Rhystic Study", "Smothering Tithe", "Sol Ring"] and hit["advice"]
    assert len(out["warnings"]) == 1  # one plain sentence for the one add
    said = out["warnings"][0]
    assert said.startswith("Adding Harmonic Sliver would turn against this deck.") and "603.3d" in said and "601.2c" in said
    assert all(name in said for name in ("Sol Ring", "Arcane Signet", "Rhystic Study", "Smothering Tithe"))
    mine = out["added_cards"][0]["lookouts"][0]  # and the add itself carries it, with the cards it would hurt
    assert mine["role"] == "cause" and mine["causes"] == ["Harmonic Sliver"] and "Sol Ring" in mine["hurts"] and mine["rules"] == ["603.3d", "601.2c"]
    assert out["lookouts_read"]["source"] == "the Vault's reading of card text" and out["lookouts_cleared"] == []


def test_the_same_add_to_a_deck_without_artifacts_or_enchantments_is_not_flagged(planned):
    deck = commander_list("Galerider Sliver")  # (a second "All Slivers have" card would make a different lookout: shared with opponents)
    out = validate(planned, deck, adds=["Harmonic Sliver"], cuts=SWAP_PLAINS)
    assert out["valid"] is True
    assert out["lookouts"] == [] and out["warnings"] == [] and "lookouts" not in out["added_cards"][0]


def test_crystalline_sliver_added_to_a_deck_with_equipment_is_flagged_as_blocking_the_decks_own_targets(planned):
    deck = commander_list("Skullclamp", "Bonesplitter", "Sol Ring")
    out = validate(planned, deck, adds=["Crystalline Sliver"], cuts=SWAP_PLAINS)
    assert out["valid"] is True
    hit = next(x for x in out["lookouts"] if x["kind"] == "own_targets_blocked")
    assert hit["new"] is True and hit["new_cards"] == ["Crystalline Sliver"]
    assert hit["hurts"] == ["Bonesplitter", "Skullclamp"] and hit["rules"] == ["702.18a", "702.6a"]
    assert "Adding Crystalline Sliver would turn against this deck." in out["warnings"][0] and "702.18a" in out["warnings"][0]
    assert out["added_cards"][0]["lookouts"][0]["kind"] == "own_targets_blocked"


def test_a_cut_of_the_only_victim_clears_the_warning_and_the_deck_is_read_after_the_cut(planned):
    both = commander_list("Harmonic Sliver", "Sol Ring")
    asis = validate(planned, both)  # no change: the lookout is the deck's own, so it is listed but it is not this plan's warning
    assert [x["new"] for x in asis["lookouts"]] == [False] and asis["warnings"] == []
    cut = validate(planned, both, adds=["Galerider Sliver"], cuts=["Sol Ring"])
    assert cut["lookouts"] == [] and cut["warnings"] == []
    assert [x["kind"] for x in cut["lookouts_cleared"]] == ["forced_self_destruction"]  # the plan removes what the deck had
    # Harmonic Sliver comes in while the only artifact goes out: the deck after the plan has nothing for it to destroy
    swap = validate(planned, commander_list("Sol Ring"), adds=["Harmonic Sliver"], cuts=["Sol Ring"])
    assert swap["lookouts"] == [] and swap["warnings"] == [] and "lookouts" not in swap["added_cards"][0]
    # one victim cut, another kept: the lookout stays (narrowed) and is still not new
    narrowed = validate(planned, commander_list("Harmonic Sliver", "Sol Ring", "Arcane Signet"), adds=["Galerider Sliver"], cuts=["Sol Ring"])
    assert [(x["new"], x["hurts"]) for x in narrowed["lookouts"]] == [(False, ["Arcane Signet"])] and narrowed["warnings"] == []


def test_an_add_that_a_card_already_in_the_deck_would_hurt_is_flagged_too(planned):
    out = validate(planned, commander_list("Harmonic Sliver"), adds=["Skullclamp"], cuts=SWAP_PLAINS)
    assert out["valid"] is True
    mine = out["added_cards"][0]["lookouts"][0]
    assert mine["role"] == "hurt" and mine["causes"] == ["Harmonic Sliver"]
    assert "Skullclamp would be hurt by Harmonic Sliver" in out["warnings"][0]


@pytest.mark.parametrize("name", ["Gemhide Sliver", "Mesmeric Sliver", "Necrotic Sliver", "Smash to Smithereens"])
def test_a_near_miss_added_to_the_same_deck_is_not_flagged(planned, name):
    deck = commander_list("Sol Ring", "Arcane Signet", "Rhystic Study", "Smothering Tithe", "Skullclamp")
    out = validate(planned, deck, adds=[name], cuts=SWAP_PLAINS)
    assert out["lookouts"] == [] and out["warnings"] == [] and "lookouts" not in out["added_cards"][0], name


def test_find_upgrades_carries_the_lookouts_a_candidate_would_create(planned):
    deck = commander_list("Sol Ring", "Skullclamp", "Rhystic Study")
    res = planned.post(f"{V1}/upgrades", json={"text": deck, "format": "commander", "budget_usd": 5, "roles": ["ramp"]})
    assert res.status_code == 200, res.text
    out = res.json()["result"]
    by_name = {c["name"]: c for c in out["candidates"]["ramp"]}
    assert set(by_name) == set(RAMP)
    harmonic = by_name["Harmonic Sliver"]["lookouts"][0]
    assert harmonic["kind"] == "forced_self_destruction" and harmonic["role"] == "cause" and harmonic["rules"] == ["603.3d", "601.2c"]
    assert {"Sol Ring", "Skullclamp", "Rhystic Study"} <= set(harmonic["hurts"]) and harmonic["advice"]
    crystalline = by_name["Crystalline Sliver"]["lookouts"][0]
    assert crystalline["kind"] == "own_targets_blocked" and crystalline["hurts"] == ["Skullclamp"] and crystalline["rules"] == ["702.18a", "702.6a"]
    assert "lookouts" not in by_name["Gemhide Sliver"] and "WARNING" not in by_name["Gemhide Sliver"]["why"]
    assert "WARNING" in by_name["Harmonic Sliver"]["why"]  # the line the panel shows under the card
    assert len(out["warnings"]) == 2 and any("Harmonic Sliver" in w for w in out["warnings"]) and any("Crystalline Sliver" in w for w in out["warnings"])


def test_find_upgrades_on_a_deck_the_candidates_cannot_hurt_has_no_warnings(planned):
    res = planned.post(f"{V1}/upgrades", json={"text": commander_list("Galerider Sliver"), "format": "commander", "budget_usd": 5, "roles": ["ramp"]})
    assert res.status_code == 200, res.text
    out = res.json()["result"]
    assert out["warnings"] == [] and all("lookouts" not in c for c in out["candidates"]["ramp"])
