"""Deck analysis (vault.deck_tools, /api/v1/decks/...): computed from the stored catalog, with the
budget and legality enforced by code, every answer labelled as computed with its inputs, and
nothing guessed when data is missing."""

from datetime import date

import pytest

from vault import catalog_sync as cs
from vault import provenance as prov

V1 = "/api/v1/decks"
TODAY = date(2026, 10, 4)


def oid(n):
    return f"{n:08d}-0000-0000-0000-000000000000"


def card(n, name, type_line, colors=(), cmc=2.0, rank=None, legal="legal", text="", **extra):
    base = {"object": "card", "id": f"p{n}", "oracle_id": oid(n), "name": name, "layout": "normal", "mana_cost": "", "cmc": cmc,
            "type_line": type_line, "oracle_text": text, "colors": list(colors), "color_identity": list(colors), "keywords": [],
            "legalities": {"commander": legal, "modern": legal}, "edhrec_rank": rank, "digital": False}
    base.update(extra)
    return base


CARDS = [
    card(1, "Test Commander", "Legendary Creature — Elf", ["R", "G"], 4, rank=100),
    card(2, "Test Mountain", "Basic Land — Mountain", [], 0),
    card(3, "Test Rock", "Artifact", [], 2, rank=10),
    card(4, "Test Burn", "Instant", ["R"], 1, rank=20),
    card(5, "Blue Cantrip", "Instant", ["U"], 1, rank=30),
    card(6, "Banned Thing", "Sorcery", ["R"], 3, legal="banned"),
    card(7, "Cheap Ramp", "Creature — Elf", ["G"], 1, rank=50),
    card(8, "Pricey Ramp", "Artifact", [], 1, rank=5),
    card(9, "Dull Bear", "Creature — Bear", ["G"], 2, rank=9000),
    card(10, "Other Rock", "Artifact", [], 2, rank=60),
    card(11, "Fire // Ice", "Instant // Instant", ["R", "U"], 2, rank=70, layout="split"),
    card(12, "Test Changer", "Artifact", [], 1, rank=1, game_changer=True),
]
PRICES = {3: 1.0, 4: 0.5, 7: 0.25, 8: 25.0, 9: 0.1, 10: 3.0}
TAGS = {"ramp": [3, 7, 8, 10], "removal-burn": [4]}


@pytest.fixture
def loaded(signed_in, app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, CARDS, today=TODAY)
        tags = [{"id": "t-ramp", "slug": "ramp", "label": "ramp", "parent_ids": [], "child_ids": [],
                 "taggings": [{"oracle_id": oid(n), "weight": "strong"} for n in TAGS["ramp"]]},
                {"id": "t-removal", "slug": "removal", "label": "removal", "parent_ids": [], "child_ids": ["t-burn"], "taggings": []},
                {"id": "t-burn", "slug": "removal-burn", "label": "removal-burn", "parent_ids": ["t-removal"], "child_ids": [],
                 "taggings": [{"oracle_id": oid(4), "weight": "median"}]}]
        cs.sync_oracle_tags(db, tags)
        cs.sync_oracle_prices(db, [{"oracle_id": oid(n), "scryfall_id": f"p{n}", "usd": usd, "usd_foil": None, "eur": None,
                                    "day": TODAY, "source": "scryfall"} for n, usd in PRICES.items()])
        for name in ("oracle_cards", "oracle_tags", "oracle_prices"):
            cs.record_source(db, name, version=name + "-1", rows=1)
        db.commit()
    return signed_in


DECK = """Commander
1 Test Commander

Deck
1 Test Rock
1 Test Burn
1 Dull Bear
1 Fire // Ice
5 Test Mountain
"""


VALID = "Commander\n1 Test Commander\n\nDeck\n1 Test Rock\n1 Test Burn\n1 Dull Bear\n96 Test Mountain\n"  # 100 cards, in the commander's colours


def post(client, path, **body):
    res = client.post(f"{V1}/{path}", json=body)
    return res


def computed(res):
    assert res.status_code == 200, res.text
    body = res.json()
    block = body["provenance"][0]
    assert block["kind"] == "computed" and block["source"] == "The Vault" and block["inputs"]
    assert all(i["kind"] == "source" for i in block["inputs"])
    return body["result"]


def test_stats_count_roles_curve_and_cost_with_provenance(loaded):
    r = computed(post(loaded, "stats", text=DECK))
    assert r["cards"] == 10 and r["lands"] == 5 and r["nonland"] == 5
    assert r["color_identity"] == ["U", "R", "G"] or sorted(r["color_identity"]) == ["G", "R", "U"]
    assert r["roles"]["ramp"]["count"] == 1 and r["roles"]["removal"]["count"] == 1  # the tag tree is rolled up
    assert r["roles"]["removal"]["cards"][0]["tag_weight"] == "median"
    assert r["curve"]["2"] == 3 and r["types"]["Creature"] == 2
    assert r["estimated_cost_usd"] == 1.6 and r["unmatched"] == []  # 1.0 + 0.5 + 0.1 (the split card has no price)
    assert "opinion" in r["role_note"]
    inputs = {i["source"] for i in post(loaded, "stats", text=DECK).json()["provenance"][0]["inputs"]}
    assert inputs == {"Scryfall", "Scryfall Tagger"}


def test_unknown_cards_are_reported_not_guessed(loaded):
    r = computed(post(loaded, "stats", text="1 Test Rock\n1 Totally Made Up Card"))
    assert r["unmatched"] == ["Totally Made Up Card"]


def test_legality_finds_banned_colour_identity_and_size_issues(loaded):
    deck = "Commander\n1 Test Commander\n\nDeck\n1 Banned Thing\n1 Blue Cantrip\n2 Test Rock\n1 Nonexistent"
    r = computed(post(loaded, "legality", text=deck, format="commander"))
    kinds = {(i["kind"], i["card"]) for i in r["issues"]}
    assert ("not_legal", "Banned Thing") in kinds and ("color_identity", "Blue Cantrip") in kinds
    assert ("too_many_copies", "Test Rock") in kinds and ("unknown_card", "Nonexistent") in kinds
    assert any(i["kind"] == "deck_size" for i in r["issues"]) and r["legal"] is False
    assert r["commander_color_identity"] == ["R", "G"] and r["not_checked"]


def test_basic_lands_may_repeat_and_a_clean_modern_deck_is_legal(loaded):
    r = computed(post(loaded, "legality", text="56 Test Mountain\n4 Test Rock", format="modern"))
    assert r["legal"] is True and r["issues"] == []


def test_a_bad_format_or_empty_deck_is_a_400(loaded):
    assert post(loaded, "legality", text=DECK, format="nope").status_code == 400
    assert post(loaded, "stats", text="not a decklist at all").status_code == 400


def test_upgrade_candidates_respect_legality_colours_budget_and_the_deck(loaded):
    r = computed(post(loaded, "upgrades", text=DECK, format="commander", budget_usd=5, roles=["ramp"]))
    names = [c["name"] for c in r["candidates"]["ramp"]]
    assert names == ["Cheap Ramp", "Other Rock"]  # ordered by popularity rank; Pricey Ramp is over budget; Test Rock is already in
    top = r["candidates"]["ramp"][0]
    assert top["price_usd"] == 0.25 and top["price_date"] == "2026-10-04" and top["price_source"] == "scryfall"
    assert "Scryfall Tagger" in top["why"] and any("Popularity is not power" in n for n in r["notes"])
    assert [c["name"] for c in r["cut_candidates"]][0] == "Dull Bear"  # the least-played card without a role comes first


def test_upgrades_can_offer_cards_you_own_first_whatever_their_price(loaded, app):
    """The web Decks page asks with use_collection: a card you own is free to add, so the budget
    doesn't apply to it; it comes first, with the copies you have."""
    from vault.models import Card, Entry, User
    with app.state.db.sessions() as db:
        uid = db.query(User.id).order_by(User.id.desc()).first()[0]
        db.add(Card(scryfall_id="p8", oracle_id=oid(8), name="Pricey Ramp", set_code="tst", collector_number="8"))
        db.add(Entry(user_id=uid, name="Pricey Ramp", scryfall_id="p8", quantity=2))
        db.commit()
    plain = computed(post(loaded, "upgrades", text=DECK, format="commander", budget_usd=5, roles=["ramp"]))
    assert "Pricey Ramp" not in [c["name"] for c in plain["candidates"]["ramp"]]
    assert "owned_copies" not in plain["candidates"]["ramp"][0]  # unchanged unless asked
    mine = computed(post(loaded, "upgrades", text=DECK, format="commander", budget_usd=5, roles=["ramp"], use_collection=True))
    ramp = mine["candidates"]["ramp"]
    assert ramp[0]["name"] == "Pricey Ramp" and ramp[0]["owned_copies"] == 2  # over budget, but yours
    assert "already in your collection" in ramp[0]["why"] and "priced within the budget" in ramp[1]["why"]
    from vault.models import OraclePrice
    with app.state.db.sessions() as db:  # a stored price that isn't a real one is shown as unknown, not sent as-is
        db.query(OraclePrice).filter(OraclePrice.oracle_id == oid(8)).update({"usd": 1e12})  # beyond any real price
        db.commit()
    odd = computed(post(loaded, "upgrades", text=DECK, format="commander", budget_usd=5, roles=["ramp"], use_collection=True))
    assert odd["candidates"]["ramp"][0]["name"] == "Pricey Ramp" and odd["candidates"]["ramp"][0]["price_usd"] is None
    with app.state.db.sessions() as db:  # a row of 0 copies isn't owning the card
        db.query(Entry).filter(Entry.scryfall_id == "p8").update({"quantity": 0})
        db.commit()
    none = computed(post(loaded, "upgrades", text=DECK, format="commander", budget_usd=5, roles=["ramp"], use_collection=True))
    assert "Pricey Ramp" not in [c["name"] for c in none["candidates"]["ramp"]]
    assert [c["name"] for c in ramp[1:]] == ["Cheap Ramp", "Other Rock"] and ramp[1]["owned_copies"] == 0


def test_role_guidelines_are_only_for_100_card_formats(loaded):
    """Modern has no usual role counts: nothing is assumed missing, and asked-for roles carry no
    Commander guideline."""
    assert computed(post(loaded, "upgrades", text=DECK, format="modern", budget_usd=5))["gaps"] == {}
    r = computed(post(loaded, "upgrades", text=DECK, format="modern", budget_usd=5, roles=["ramp"]))
    assert r["gaps"]["ramp"]["guideline"] is None and "ramp" in r["candidates"]
    assert computed(post(loaded, "upgrades", text=DECK, format="commander", budget_usd=5, roles=["ramp"]))["gaps"]["ramp"]["guideline"] == 10


def test_upgrades_need_the_prices_and_tags_to_be_loaded(signed_in, app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, CARDS)
        cs.record_source(db, "oracle_cards", version="v", rows=1)
        db.commit()
    assert post(signed_in, "upgrades", text=DECK, format="commander", budget_usd=5).status_code == 503
    assert post(signed_in, "stats", text=DECK).status_code == 200  # stats still work, just without prices


def test_nothing_is_computed_before_the_catalog_is_loaded(signed_in):
    assert post(signed_in, "stats", text=DECK).status_code == 503


def test_the_validator_enforces_the_budget_legality_and_colours(loaded):
    ok = computed(post(loaded, "validate-changes", text=VALID, format="commander", cuts=["Dull Bear"], adds=["Cheap Ramp"], budget_usd=1))
    assert ok["issues"] == [] and ok["valid"] is True and ok["added_cost_usd"] == 0.25 and ok["cards_after"] == 100
    over = computed(post(loaded, "validate-changes", text=VALID, format="commander", cuts=["Dull Bear"], adds=["Pricey Ramp"], budget_usd=10))
    assert over["valid"] is False and {i["kind"] for i in over["issues"]} >= {"over_budget"}
    wrong = computed(post(loaded, "validate-changes", text=VALID, format="commander", cuts=["Not In Deck"],
                          adds=["Blue Cantrip", "Banned Thing", "Ghost Card"], budget_usd=100))
    kinds = {i["kind"] for i in wrong["issues"]}
    assert wrong["valid"] is False and kinds >= {"cut_not_in_deck", "color_identity", "not_legal", "unknown_card"}


def test_a_token_with_the_same_name_never_shadows_the_card(loaded, app):
    """Found with real data: 'Llanowar Elves' is also a token (not legal anywhere), and it replaced the card."""
    from vault.models import OracleCard
    with app.state.db.sessions() as db:
        cs._upsert(db, OracleCard, [cs.oracle_card_row(card(50, "Dull Bear", "Token Creature — Bear", [], 0, legal="not_legal", layout="token"))],
                   ("oracle_id",))
        db.commit()
    r = computed(post(loaded, "legality", text="Commander\n1 Test Commander\n\nDeck\n1 Dull Bear\n98 Test Mountain", format="commander"))
    assert ("not_legal", "Dull Bear") not in {(i["kind"], i["card"]) for i in r["issues"]}
    found = loaded.get("/api/v1/catalog/cards", params={"name": "Dull Bear"}).json()
    assert found["card"]["type_line"] == "Creature — Bear" and found["card"]["layout"] == "normal"


def test_problems_the_deck_already_had_do_not_invalidate_a_plan_but_are_reported(loaded):
    """Found with a real deck that held one off-color card: every plan was rejected because of it."""
    flawed = VALID.replace("96 Test Mountain", "95 Test Mountain\n1 Blue Cantrip")
    ok = computed(post(loaded, "validate-changes", text=flawed, format="commander", cuts=["Dull Bear"], adds=["Cheap Ramp"], budget_usd=1))
    assert ok["valid"] is True and ok["issues"] == []
    assert [(i["kind"], i["card"]) for i in ok["existing_issues"]] == [("color_identity", "Blue Cantrip")]
    worse = computed(post(loaded, "validate-changes", text=flawed, format="commander", cuts=["Dull Bear"], adds=["Banned Thing"], budget_usd=5))
    assert worse["valid"] is False and ("not_legal", "Banned Thing") in {(i["kind"], i["card"]) for i in worse["issues"]}
    fixed = computed(post(loaded, "validate-changes", text=flawed, format="commander", cuts=["Blue Cantrip"], adds=["Cheap Ramp"], budget_usd=1))
    assert fixed["valid"] is True and fixed["existing_issues"] == []  # cutting the problem card solves it


def test_an_unpriced_add_cannot_be_verified_against_a_budget(loaded):
    r = computed(post(loaded, "validate-changes", text=VALID, format="commander", adds=["Blue Cantrip"], cuts=["Dull Bear"], budget_usd=50))
    assert r["valid"] is False and any(i["kind"] == "no_price" for i in r["issues"])


def test_the_shopping_list_is_what_you_do_not_own_with_dated_prices(loaded):
    csv = (b"Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET\r\n"
           b"c,1,0,Test Rock,TST,Test,3,NearMint,Normal,English,,,1.00,1.00,1.00\r\n")
    assert loaded.post("/api/v1/imports", files={"file": ("export.csv", csv, "text/csv")}).status_code == 201
    r = computed(post(loaded, "shopping-list", text="1 Test Rock\n1 Test Burn\n2 Cheap Ramp"))
    assert [(l["name"], l["quantity"]) for l in r["lines"]] == [("Test Burn", 1), ("Cheap Ramp", 2)] or \
        sorted((l["name"], l["quantity"]) for l in r["lines"]) == [("Cheap Ramp", 2), ("Test Burn", 1)]
    assert r["total_usd"] == 1.0 and "Scryfall" in r["notes"][0] and "does not contact stores" in r["notes"][1]
    assert set(r["text"].splitlines()) == {"1 Test Burn", "2 Cheap Ramp"}


def test_analyses_are_rate_limited_per_person(loaded, app):
    codes = [post(loaded, "legality", text=DECK, format="commander").status_code for _ in range(32)]
    assert codes.count(200) == 30 and codes[-1] == 429


def test_simulate_plays_the_curve_with_the_commander_in_the_command_zone(loaded):
    text = "Commander\n1 Test Commander\nDeck\n37 Test Mountain\n30 Dull Bear\n31 Cheap Ramp\n1 Unknown Card"
    res = loaded.post(f"{V1}/simulate", json={"text": text, "format": "commander", "games": 200, "samples": 2})
    assert res.status_code == 200, res.text
    out = res.json()["result"]
    assert out["multiplayer"] is True and out["games"] == 200 and len(out["samples"]) == 2
    assert out["unmatched"] == ["Unknown Card"] and out["assumptions"]
    assert out["per_turn"][0]["land_drop"] > 80 and "five_mana_by_turn_5" in out["headline"]
    assert any("Test Commander" in t["cast"] for s in out["samples"] for t in s["turns"])
    assert res.json()["provenance"][0]["kind"] == "computed"
    again = loaded.post(f"{V1}/simulate", json={"text": text, "format": "commander", "games": 200, "samples": 2}).json()
    assert again["result"] == out  # the default seed comes from the deck: same deck, same answer


def test_simulate_needs_enough_cards(loaded):
    assert loaded.post(f"{V1}/simulate", json={"text": "3 Test Mountain", "format": "modern"}).status_code == 400


def test_a_hidden_tag_gives_no_role_and_very_strong_outranks_strong(loaded, monkeypatch):
    """Scryfall: tags are community data; apps should be able to hide one (HIDDEN_TAGS). Weights are written
    very_strong / strong / median / weak (scryfall.com/docs/api/tags)."""
    from vault import deck_tools as dt
    text = "1 Test Rock\n1 Cheap Ramp"
    assert loaded.post(f"{V1}/stats", json={"text": text}).json()["result"]["roles"]["ramp"]["count"] == 2
    monkeypatch.setenv("HIDDEN_TAGS", "ramp")
    assert loaded.post(f"{V1}/stats", json={"text": text}).json()["result"]["roles"]["ramp"]["count"] == 0
    assert dt.hidden_tags() == {"ramp"}


def test_stats_count_game_changers_and_give_a_bracket_floor_from_them_alone(loaded):
    none = loaded.post(f"{V1}/stats", json={"text": "1 Test Rock\n1 Cheap Ramp"}).json()["result"]["game_changers"]
    assert none["count"] == 0 and none["bracket_floor"] is None and none["cards"] == []
    one = loaded.post(f"{V1}/stats", json={"text": "1 Test Changer\n1 Test Rock"}).json()["result"]["game_changers"]
    assert one["count"] == 1 and one["bracket_floor"] == 3 and one["cards"] == [{"name": "Test Changer", "quantity": 1}]
    assert "Wizards' own page was not readable" in one["note"]  # says what it did not check


def test_every_analysis_takes_a_saved_deck_by_id_instead_of_the_list(loaded):
    """A person names a deck; the AI finds it (list_decks ?q=) and analyses it by id, not by sending 100 lines each time (#96)."""
    text = "Commander\n1 Test Commander\nDeck\n37 Test Mountain\n30 Dull Bear\n31 Cheap Ramp"
    saved = loaded.post("/api/v1/decks", json={"name": "Elves", "text": text}).json()
    by_text = loaded.post(f"{V1}/stats", json={"text": text}).json()["result"]
    assert loaded.post(f"{V1}/stats", json={"deck_id": saved["id"]}).json()["result"] == by_text
    assert loaded.post(f"{V1}/legality", json={"deck_id": saved["id"], "format": "commander"}).status_code == 200
    assert loaded.post(f"{V1}/shopping-list", json={"deck_id": saved["id"]}).status_code == 200
    assert loaded.post(f"{V1}/combos", json={"deck_id": saved["id"]}).status_code in (200, 502, 503)
    assert loaded.post(f"{V1}/simulate", json={"deck_id": saved["id"], "format": "commander", "games": 50, "samples": 1}).status_code == 200
    assert loaded.post(f"{V1}/validate-changes", json={"deck_id": saved["id"], "format": "commander", "adds": [], "cuts": []}).status_code == 200
    assert loaded.post(f"{V1}/upgrades", json={"deck_id": saved["id"], "format": "commander", "budget_usd": 5}).status_code == 200


def test_a_deck_id_must_be_the_callers_and_exactly_one_of_text_or_id_is_needed(loaded, app):
    from fastapi.testclient import TestClient

    mine = loaded.post("/api/v1/decks", json={"name": "Mine", "text": "1 Test Rock"}).json()
    with TestClient(app) as other:
        other.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        assert other.post(f"{V1}/stats", json={"deck_id": mine["id"]}).status_code == 404  # someone else's deck
    assert loaded.post(f"{V1}/stats", json={"deck_id": 999999}).status_code == 404
    assert loaded.post(f"{V1}/stats", json={}).status_code == 422
    assert loaded.post(f"{V1}/stats", json={"text": "1 Test Rock", "deck_id": mine["id"]}).status_code == 422


def test_an_analysis_of_a_deck_from_archidekt_carries_its_credit_and_fetched_at(loaded):
    """#96: deck answers carry provenance - the source link, the author's credit and when the list was taken."""
    saved = loaded.post("/api/v1/decks", json={"name": "Rocks", "text": "1 Test Rock", "source_url": "https://archidekt.com/decks/6803907/x",
                                               "source_author": "layer0"}).json()
    for path, extra in (("stats", {}), ("shopping-list", {}), ("legality", {"format": "commander"})):
        deck = loaded.post(f"{V1}/{path}", json={"deck_id": saved["id"], **extra}).json()["deck"]
        credit = deck["credit"]
        assert credit["source"] == "Archidekt" and credit["author"] == "layer0" and credit["url"].startswith("https://archidekt.com/decks/"), path
        assert credit["fetched_at"] and "not the Vault's" in credit["notice"], path
    pasted = loaded.post("/api/v1/decks", json={"name": "Mine", "text": "1 Test Rock"}).json()
    assert "credit" not in loaded.post(f"{V1}/stats", json={"deck_id": pasted["id"]}).json()["deck"]


def test_upgrades_price_the_cut_candidates_and_the_deck_so_a_swap_has_a_delta(loaded):
    """#53: a view shows what a swap does to the price, so the cut candidates carry their own price and the answer the
    deck's estimated cost (the same figure deck_stats gives)."""
    r = computed(post(loaded, "upgrades", text=DECK, format="commander", budget_usd=5, roles=["ramp"]))
    bear = next(c for c in r["cut_candidates"] if c["name"] == "Dull Bear")
    assert bear["price_usd"] == 0.1 and bear["price_date"] == "2026-10-04" and bear["price_source"] == "scryfall"
    assert r["deck_cost_usd"] == computed(post(loaded, "stats", text=DECK))["estimated_cost_usd"]


def test_the_validator_reports_what_a_plan_does_to_the_price_of_the_deck(loaded):
    """#53: adds and cuts each with their price, the net change and the deck's cost before and after. The budget check is
    unchanged (the adds only); these figures are for comparing."""
    r = computed(post(loaded, "validate-changes", text=VALID, format="commander", cuts=["Dull Bear"], adds=["Cheap Ramp"], budget_usd=1))
    assert r["added_cards"] == [{"name": "Cheap Ramp", "price_usd": 0.25}] and r["cut_cards"] == [{"name": "Dull Bear", "price_usd": 0.1}]
    assert r["cut_value_usd"] == 0.1 and r["net_change_usd"] == 0.15 and r["cut_unpriced"] == [] and r["cuts_not_refunded"] is True
    assert r["added_cost_usd"] == 0.25  # the budget still counts the adds alone
    assert round(r["deck_cost_after_usd"] - r["deck_cost_before_usd"], 2) == 0.15
    assert r["deck_cost_before_usd"] == computed(post(loaded, "stats", text=VALID))["estimated_cost_usd"]
    free = computed(post(loaded, "validate-changes", text=VALID, format="commander", cuts=["Test Mountain"], adds=[]))
    assert free["cut_cards"] == [{"name": "Test Mountain", "price_usd": None}] and free["cut_unpriced"] == ["Test Mountain"] and free["net_change_usd"] == 0.0
