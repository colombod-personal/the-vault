"""The deck ideas lab, server side (#163, docs/deck-ideas-lab-design.md): `GET /decks/{id}/ideas` (a saved deck in role lanes, with
what the collection covers, what is missing and what another deck holds) and `GET /decks/{id}/ideas/alternatives` (owned cards that
can stand in for a card), and the tools `get_deck_ideas` and `get_card_alternatives`.

The tests follow the design's list: the lanes (every copy in exactly one, Other and Lands so nothing is dropped, header counts equal
the sum whatever the page size, lanes page on their own), a missing card with a shared core role lists the owned card, nothing
outside the colour identity or the format appears, a card another deck holds is borrowed with that deck's name and `move` needs a
donor copy, the mixed shortage, copy limits (an owned Sol Ring is never offered for ramp in a Commander deck that runs one), own
account only, provenance with dated prices, and the performance of the first page on a 100-card deck (a query counter)."""

import time
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, update

from test_agents import auth, bot, call_tool, make_token  # noqa: F401 - fixtures
from tests.test_deck_api import card, oid
from tests.test_deck_independence import alice, bob, own, save, sign_in, stamp, user_id  # noqa: F401 - fixtures and helpers
from vault import catalog_sync as cs
from vault.models import Deck

V1 = "/api/v1"
DAY = date(2026, 10, 4)

# n, name, type line, colours, mana value, legalities (default: legal in commander and modern)
CATALOG = [
    (1, "Test Commander", "Legendary Creature — Elf", ["R", "G"], 4),
    (2, "Test Mountain", "Basic Land — Mountain", [], 0),
    (3, "Sol Ring", "Artifact", [], 1),
    (4, "Mind Stone", "Artifact", [], 2),
    (5, "Arcane Signet", "Artifact", [], 2),
    (6, "Cultivate", "Sorcery", ["G"], 3),
    (7, "Sapphire Medallion", "Artifact", ["U"], 2),  # outside a red-green deck's colour identity
    (8, "Banned Rock", "Artifact", [], 1),  # banned in commander
    (9, "Weak Ramp", "Creature — Elf", ["G"], 1),  # a weak tag: incidental, not core
    (10, "Multi Tool", "Artifact Creature — Construct", [], 3),  # ramp, draw and tutor
    (11, "Divination", "Sorcery", ["G"], 3),
    (12, "Dull Bear", "Creature — Bear", ["G"], 2),  # no role
    (13, "Command Tower", "Land", [], 0),
    (14, "Chaos Warp", "Instant", ["R"], 3),
    (15, "Blasphemous Act", "Sorcery", ["R"], 9),
    (16, "Commander Only Rock", "Artifact", [], 2),  # legal in commander, not in modern
]
LEGALITIES = {8: {"commander": "banned", "modern": "banned"}, 16: {"commander": "legal", "modern": "not_legal"}}
TAGGED = {  # role tag -> [(n, weight)]
    "ramp": [(3, "strong"), (4, "strong"), (5, "median"), (6, "strong"), (7, "strong"), (8, "strong"), (9, "weak"), (10, "strong"),
             (16, "strong")],
    "draw-engine": [(10, "strong"), (11, "strong")],
    "tutor": [(10, "median")],
    "removal": [(14, "strong")],
    "sweeper": [(15, "strong")],
}
PRICES = {6: 0.40, 4: 1.10, 3: 2.00, 14: 0.75}
IMAGE = "https://cards.scryfall.io/normal/front/{}.jpg"


def seed(app, extra_cards=(), extra_tags=None, prices=None):
    """The catalog the tests read: cards, role tags, prices (dated DAY) and the sources that say they are loaded."""
    cards = [card(n, name, tl, colors, cmc, rank=n, legalities=LEGALITIES.get(n, {"commander": "legal", "modern": "legal"}),
                  image_uris={"normal": IMAGE.format(n)}, artist=f"Artist {n}", scryfall_uri=f"https://scryfall.com/card/{n}")
             for n, name, tl, colors, cmc in CATALOG] + list(extra_cards)
    tagged = {**TAGGED, **(extra_tags or {})}
    tags = [{"id": f"t-{slug}", "slug": slug, "label": slug, "parent_ids": [], "child_ids": [],
             "taggings": [{"oracle_id": oid(n), "weight": w} for n, w in found]} for slug, found in tagged.items()]
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, cards, today=DAY)
        cs.sync_oracle_tags(db, tags)
        cs.sync_oracle_prices(db, [{"oracle_id": oid(n), "scryfall_id": f"p{n}", "usd": usd, "usd_foil": None, "eur": None,
                                    "day": DAY, "source": "scryfall"} for n, usd in {**PRICES, **(prices or {})}.items()])
        for name in ("oracle_cards", "oracle_tags", "oracle_prices"):
            cs.record_source(db, name, version=name + "-1", rows=1)
        db.commit()


@pytest.fixture
def lab(alice, app):
    seed(app)
    return alice


DECK = """Commander
1 Test Commander

Deck
1 Sol Ring
1 Cultivate
1 Multi Tool
1 Divination
1 Dull Bear
1 Not In The Catalog
10 Test Mountain
1 Command Tower
"""
COPIES = 1 + 1 + 1 + 1 + 1 + 1 + 1 + 10 + 1  # the deck's card count, the commander included
OWNED = {"Sol Ring": 2, "Multi Tool": 1, "Divination": 1, "Dull Bear": 1, "Command Tower": 1, "Test Commander": 1,
         "Mind Stone": 1, "Arcane Signet": 1, "Sapphire Medallion": 1, "Banned Rock": 1, "Weak Ramp": 1, "Not In The Catalog": 1}


def ideas(client, deck_id, **params):
    res = client.get(f"{V1}/decks/{deck_id}/ideas", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def alternatives(client, deck_id, card_name, **params):
    res = client.get(f"{V1}/decks/{deck_id}/ideas/alternatives", params={"card": card_name, **params})
    assert res.status_code == 200, res.text
    return res.json()


def lane_cards(body, lane):
    return {i["card"]: i for l in body["lanes"] if l["lane"] == lane for i in l["items"]}


def every_card(client, deck_id, **params):
    """Every card of every lane, following each lane's `next` to its end."""
    found = {}
    for entry in ideas(client, deck_id, **params)["lanes"]:
        page = entry
        while True:
            found.update({i["card"]: i for i in page["items"]})
            if "next" not in page["_links"]:
                break
            page = client.get(page["_links"]["next"]["href"]).json()["lanes"][0]
    return found


# -- the lanes ------------------------------------------------------------------------------------------------------------

def test_every_copy_is_in_exactly_one_lane_so_nothing_is_dropped(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    body = ideas(lab, deck, limit=100)
    assert [l["lane"] for l in body["lanes"]] == ["ramp", "draw", "removal", "sweeper", "counterspell", "tutor", "recursion",
                                                  "sacrifice_outlet", "other", "lands"]
    assert sum(l["copies"] for l in body["lanes"]) == COPIES == body["summary"]["copies"]
    seen = [i["card"] for l in body["lanes"] for i in l["items"]]
    assert len(seen) == len(set(seen)) == body["summary"]["cards"] == 9  # a card is in one lane only
    assert sum(i["need"] for l in body["lanes"] for i in l["items"]) == COPIES
    # a multi-role card (ramp, draw and tutor) is counted once, in the first lane of the fixed priority; its other roles are tags
    tool = lane_cards(body, "ramp")["Multi Tool"]
    assert tool["lane"] == "ramp" and tool["tags"] == ["draw", "tutor"]
    assert {r["role"] for r in tool["roles"]} == {"ramp", "draw", "tutor"} and all(r["basis"] == "scryfall_tagger" for r in tool["roles"])
    assert all("Multi Tool" not in lane_cards(body, other) for other in ("draw", "tutor"))
    assert set(lane_cards(body, "ramp")) == {"Sol Ring", "Cultivate", "Multi Tool"}
    assert set(lane_cards(body, "draw")) == {"Divination"}
    # a card with no role goes to Other, and so does one the catalog does not know; every land goes to Lands
    other = lane_cards(body, "other")
    assert set(other) == {"Dull Bear", "Not In The Catalog", "Test Commander"} and other["Not In The Catalog"]["known"] is False
    lands = lane_cards(body, "lands")
    assert set(lands) == {"Test Mountain", "Command Tower"} and lands["Test Mountain"]["need"] == 10 and lands["Test Mountain"]["basic"] is True
    assert [l["copies"] for l in body["lanes"] if l["lane"] == "lands"] == [11]


def test_the_header_counts_are_the_same_whatever_the_page_size_and_lanes_page_on_their_own(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    whole = ideas(lab, deck, limit=100)
    small = ideas(lab, deck, limit=1)
    assert small["summary"] == whole["summary"]
    assert [(l["lane"], l["copies"], l["cards"], l["total"]) for l in small["lanes"]] == [(l["lane"], l["copies"], l["cards"], l["total"]) for l in whole["lanes"]]
    assert all(l["count"] <= 1 for l in small["lanes"]) and sum(l["copies"] for l in small["lanes"]) == small["summary"]["copies"]
    ramp = next(l for l in small["lanes"] if l["lane"] == "ramp")
    assert ramp["count"] == 1 and ramp["total"] == 3 and "next" in ramp["_links"]
    assert "next" not in next(l for l in small["lanes"] if l["lane"] == "draw")["_links"]  # one card: nothing more to page
    # one lane pages on its own, with its cursor; the other lanes are not in the answer
    names = []
    page = lab.get(f"{V1}/decks/{deck}/ideas", params={"lane": "ramp", "limit": 1}).json()
    while True:
        assert [l["lane"] for l in page["lanes"]] == ["ramp"] and page["summary"] == whole["summary"]
        names += [i["card"] for i in page["lanes"][0]["items"]]
        if "next" not in page["lanes"][0]["_links"]:
            break
        page = lab.get(page["lanes"][0]["_links"]["next"]["href"]).json()
    assert sorted(names) == ["Cultivate", "Multi Tool", "Sol Ring"] and len(names) == 3
    assert set(every_card(lab, deck, limit=1)) == {i["card"] for l in whole["lanes"] for i in l["items"]}
    assert lab.get(f"{V1}/decks/{deck}/ideas", params={"cursor": "abc"}).status_code == 400  # a cursor pages one lane
    assert lab.get(f"{V1}/decks/{deck}/ideas", params={"lane": "nonsense"}).status_code == 422


def test_cards_that_need_a_decision_come_first_in_their_lane(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    ramp = next(l for l in ideas(lab, deck)["lanes"] if l["lane"] == "ramp")
    assert [(i["card"], i["status"]) for i in ramp["items"]] == [("Cultivate", "missing"), ("Multi Tool", "owned"), ("Sol Ring", "owned")]


def test_the_header_says_how_much_is_covered_missing_and_borrowed(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    body = ideas(lab, deck)
    assert body["deck"]["name"] == "Ramp Deck" and body["deck"]["overview"]["commanders"] == ["Test Commander"]
    assert body["deck"]["overview"]["color_identity"] == "RG"
    s = body["summary"]
    # Cultivate is the one card not owned; the 10 basic lands are never short, so the deck is covered but for that copy
    assert (s["copies"], s["covered"], s["lacking"], s["missing"], s["borrowed"], s["partial"], s["complete"]) == (COPIES, COPIES - 1, 1, 1, 0, 0, False)
    cultivate = lane_cards(body, "ramp")["Cultivate"]
    assert (cultivate["have"], cultivate["gets"], cultivate["not_owned"], cultivate["held_by_other_deck"]) == (0, 0, 1, 0)
    assert cultivate["buy"] == {"quantity": 1, "unit_price": 0.4, "price_date": "2026-10-04", "cost": 0.4, "price_status": "priced"}
    assert cultivate["move"] is None and cultivate["borrowed"] is False and cultivate["borrowed_from"] is None
    assert "alternatives" in cultivate["_links"] and "alternatives" not in lane_cards(body, "ramp")["Sol Ring"]["_links"]
    assert body["prices_date"] == "2026-10-04" and body["allocation"]["rule"] == "closest_to_complete"
    assert "coarse" in body["roles_note"] and body["lanes_note"] and body["borrow_note"]
    assert body["combos"] is None  # Commander Spellbook is asked only on request


def test_a_deck_that_is_fully_covered_says_so_and_keeps_its_lanes(lab, app):
    own(app, {**OWNED, "Cultivate": 1})
    deck = save(lab, "Ramp Deck", DECK)
    body = ideas(lab, deck)
    assert body["summary"]["complete"] is True and body["summary"]["lacking"] == 0 and body["summary"]["missing"] == 0
    assert sum(len(l["items"]) for l in body["lanes"]) == 9 and all(i["status"] == "owned" for l in body["lanes"] for i in l["items"])


# -- borrowed cards and moves -----------------------------------------------------------------------------------------------

def test_a_card_another_deck_holds_is_borrowed_with_that_decks_name_and_move_needs_a_donor_copy(lab, app):
    own(app, {**OWNED, "Chaos Warp": 1})
    small = save(lab, "Tiny Deck", "1 Chaos Warp")  # complete, so it takes the only copy first
    big = save(lab, "Big Deck", DECK + "1 Chaos Warp\n")
    body = ideas(lab, big)
    warp = lane_cards(body, "removal")["Chaos Warp"]
    assert (warp["have"], warp["gets"], warp["not_owned"], warp["held_by_other_deck"]) == (1, 0, 0, 1)
    assert warp["status"] == "missing" and warp["borrowed"] is True and warp["borrowed_from"] == "Tiny Deck"
    assert warp["borrowed_from_deck_id"] == small
    assert warp["move"] == {"kind": "move", "from_deck": {"id": small, "name": "Tiny Deck"}, "quantity": 1}
    assert warp["buy"] is None  # the copy exists: it can be moved, nothing has to be bought
    assert body["summary"]["borrowed"] == 1 and body["summary"]["missing"] == 1  # Cultivate is not owned
    # the deck holding the copy is not borrowing: it has the copy, and the other deck wants it
    holder = lane_cards(ideas(lab, small), "removal")["Chaos Warp"]
    assert holder["status"] == "owned" and holder["move"] is None and holder["borrowed_from"] is None
    assert holder["borrowed"] is True and holder["also_wanted_by"] == ["Big Deck"]
    # no donor copy, no move: a card owned nowhere is only bought
    nowhere = lane_cards(body, "ramp")["Cultivate"]
    assert nowhere["move"] is None and nowhere["buy"]["quantity"] == 1


def test_a_mixed_shortage_is_split_into_a_copy_to_buy_and_a_copy_to_move(lab, app):
    """Two decks each need two copies of a card, one is owned: the deck allocated none lacks one copy that is not owned (to buy) and
    one that another deck holds (to move), and is counted under both missing and borrowed."""
    own(app, {"Rare Thing": 1})
    first = save(lab, "First", "2 Rare Thing")
    second = save(lab, "Second", "2 Rare Thing")
    stamp(app, first, datetime(2026, 10, 2, tzinfo=timezone.utc))  # edited last, so it takes the copy first
    stamp(app, second, datetime(2026, 10, 1, tzinfo=timezone.utc))
    a, b = ideas(lab, first), ideas(lab, second)
    thing = lane_cards(b, "other")["Rare Thing"]
    assert (thing["need"], thing["have"], thing["gets"], thing["status"]) == (2, 1, 0, "missing")
    assert (thing["not_owned"], thing["held_by_other_deck"]) == (1, 1)
    assert thing["move"] == {"kind": "move", "from_deck": {"id": first, "name": "First"}, "quantity": 1}
    assert thing["buy"]["quantity"] == 1 and thing["borrowed_from"] == "First"
    assert (b["summary"]["missing"], b["summary"]["borrowed"], b["summary"]["lacking"]) == (1, 1, 2)  # a card can be counted under both
    mine = lane_cards(a, "other")["Rare Thing"]  # the first deck holds the copy: it still lacks one that is not owned
    assert (mine["gets"], mine["status"], mine["not_owned"], mine["held_by_other_deck"], mine["move"]) == (1, "partial", 1, 0, None)
    assert mine["buy"]["quantity"] == 1 and mine["borrowed"] is True and mine["also_wanted_by"] == ["Second"]
    assert (a["summary"]["missing"], a["summary"]["borrowed"], a["summary"]["partial"]) == (1, 0, 1)


def test_an_alternative_another_deck_holds_is_borrowed_with_its_name_and_a_price(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    save(lab, "Rock Deck", "1 Mind Stone")  # Mind Stone is the only copy owned and that deck holds it
    body = alternatives(lab, deck, "Cultivate")
    rows = {r["card"]: r for r in body["items"]}
    stone, signet = rows["Mind Stone"], rows["Arcane Signet"]
    assert stone["borrowed"] is True and stone["borrowed_from"] == "Rock Deck" and stone["copies_free"] == 0
    assert stone["move"]["from_deck"]["name"] == "Rock Deck" and stone["move"]["quantity"] == 1
    assert stone["buy"] == {"quantity": 1, "unit_price": 1.1, "price_date": "2026-10-04", "cost": 1.1, "price_status": "priced"}
    assert signet["borrowed"] is False and signet["borrowed_from"] is None and signet["move"] is None and signet["buy"] is None
    assert signet["copies_free"] == 1
    assert [r["card"] for r in body["items"]] == ["Arcane Signet", "Mind Stone"]  # a free copy first, then the one another deck holds


# -- alternatives ---------------------------------------------------------------------------------------------------------

def test_a_missing_card_with_a_shared_core_role_lists_the_owned_card(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    body = alternatives(lab, deck, "Cultivate")
    assert body["card"]["card"] == "Cultivate" and body["card"]["status"] == "missing" and body["card"]["core_roles"] == ["ramp"]
    assert body["card"]["buy"] == {"quantity": 1, "unit_price": 0.4, "price_date": "2026-10-04", "cost": 0.4, "price_status": "priced"}
    assert body["card"]["image"] == {"normal": IMAGE.format(6), "artist": "Artist 6"} and body["card"]["not_owned"] == 1
    rows = {r["card"]: r for r in body["items"]}
    assert set(rows) == {"Mind Stone", "Arcane Signet"}  # owned, share the ramp core role, in the colours, legal, not already at the limit
    stone = rows["Mind Stone"]
    assert stone["copies_owned"] == 1 and stone["mana_value"] == 2.0 and stone["mana_value_difference"] == 1.0
    assert stone["shared_roles"] == [{"role": "ramp", "target": "core", "candidate": "core"}]
    assert "ramp" in stone["why"] and "core" in stone["why"] and "mana value 2 vs 3" in stone["why"]
    assert stone["legal"] is True and stone["in_colours"] is True and stone["remaining_allowance"] == 1
    assert stone["image"]["artist"] == "Artist 4" and stone["_links"]["scryfall"]["href"] == "https://scryfall.com/card/4"
    assert body["format"] == "commander" and body["format_from"] == "default" and body["color_identity"] == ["R", "G"]
    assert body["reason"] is None and body["total"] == 2 and "coarse" in body["roles_note"]
    assert body["deck"]["name"] == "Ramp Deck"


def test_nothing_outside_the_colour_identity_or_the_format_or_without_a_core_role_is_offered(lab, app):
    own(app, {**OWNED, "Commander Only Rock": 1})
    deck = save(lab, "Ramp Deck", DECK)
    names = {r["card"] for r in alternatives(lab, deck, "Cultivate")["items"]}
    assert "Sapphire Medallion" not in names  # blue, in a red-green deck
    assert "Banned Rock" not in names  # banned in commander
    assert "Weak Ramp" not in names  # a weak tag is incidental, not core
    assert "Dull Bear" not in names and "Divination" not in names  # no shared role
    assert "Cultivate" not in names  # never itself
    assert "Commander Only Rock" in names
    modern = alternatives(lab, deck, "Cultivate", format="modern")
    # not legal in modern, banned, blue and weak-tagged cards stay out; Sol Ring is allowed more than one copy there
    assert {r["card"] for r in modern["items"]} == {"Mind Stone", "Arcane Signet", "Sol Ring"}
    assert modern["format"] == "modern" and modern["format_from"] == "request"


def test_an_owned_sol_ring_is_never_offered_when_the_commander_deck_already_runs_it(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    commander = {r["card"] for r in alternatives(lab, deck, "Cultivate")["items"]}
    assert "Sol Ring" not in commander  # the deck has its one copy: the format allows no more
    modern = {r["card"]: r for r in alternatives(lab, deck, "Cultivate", format="modern")["items"]}
    assert modern["Sol Ring"]["remaining_allowance"] == 3 and modern["Sol Ring"]["in_deck"] == 1  # four allowed, one in the deck
    # and when the deck does not run it, it is offered in Commander too
    other = save(lab, "No Rock", "Commander\n1 Test Commander\n\nDeck\n1 Cultivate\n")
    assert "Sol Ring" in {r["card"] for r in alternatives(lab, other, "Cultivate")["items"]}


def test_a_four_of_is_offered_until_the_copy_limit_is_reached(lab, app):
    own(app, {**OWNED, "Mind Stone": 10})  # enough for both decks, so only the format's copy limit decides
    full = save(lab, "Four Stones", "4 Mind Stone\n1 Cultivate")
    part = save(lab, "Two Stones", "2 Mind Stone\n1 Cultivate")
    assert "Mind Stone" not in {r["card"] for r in alternatives(lab, full, "Cultivate", format="modern")["items"]}
    [stone] = [r for r in alternatives(lab, part, "Cultivate", format="modern")["items"] if r["card"] == "Mind Stone"]
    assert stone["remaining_allowance"] == 2 and stone["in_deck"] == 2


def test_a_card_with_no_alternative_says_why(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK + "1 Blasphemous Act\n")
    none = alternatives(lab, deck, "Blasphemous Act")  # a sweeper, and no other sweeper is owned
    assert none["items"] == [] and none["total"] == 0 and none["reason"] == "none_found"
    assert "own nothing else" in none["message"] and none["card"]["core_roles"] == ["sweeper"]
    plain = alternatives(lab, deck, "Dull Bear")  # a card with no coarse role
    assert plain["items"] == [] and plain["reason"] == "no_role" and "no coarse role" in plain["message"]
    assert lab.get(f"{V1}/decks/{deck}/ideas/alternatives", params={"card": "No Such Cardd"}).status_code == 404


def test_alternatives_page_with_a_cursor_and_keep_their_order(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    first = lab.get(f"{V1}/decks/{deck}/ideas/alternatives", params={"card": "Cultivate", "limit": 1}).json()
    assert [r["card"] for r in first["items"]] == ["Arcane Signet"] and first["count"] == 1 and first["total"] == 2
    second = lab.get(first["_links"]["next"]["href"]).json()
    assert [r["card"] for r in second["items"]] == ["Mind Stone"] and "next" not in second["_links"]


def test_the_format_is_validated_and_the_decks_own_format_is_the_default(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    url = f"{V1}/decks/{deck}/ideas/alternatives"
    assert lab.get(url, params={"card": "Cultivate", "format": "nonsense"}).status_code == 422
    for fmt in ("commander", "modern", "pauper"):
        assert lab.get(url, params={"card": "Cultivate", "format": fmt}).status_code == 200
    assert alternatives(lab, deck, "Cultivate")["format"] == "commander"  # the default, as the deck tools assume
    set_format = lab.put(f"{V1}/decks/{deck}", json={"name": "Ramp Deck", "text": DECK, "format": "modern"})
    assert set_format.status_code == 200, set_format.text
    kept = alternatives(lab, deck, "Cultivate")
    assert kept["format"] == "modern" and kept["format_from"] == "deck"
    assert alternatives(lab, deck, "Cultivate", format="commander")["format_from"] == "request"


def test_a_card_not_in_the_deck_can_still_be_asked_about(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    body = alternatives(lab, deck, "Chaos Warp")
    assert body["card"]["in_deck"] == 0 and body["card"]["status"] is None and body["items"] == [] and body["reason"] == "none_found"


# -- own account only -----------------------------------------------------------------------------------------------------

def test_only_the_owners_deck_is_visible_and_nothing_crosses_a_share(lab, bob, app):
    own(app, OWNED)
    mine = save(lab, "Secret Brew", DECK)
    invite = lab.post(f"{V1}/shares", json={"kind": "collection", "show_costs": True}).json()
    deck_invite = lab.post(f"{V1}/shares", json={"kind": "deck", "deck_id": mine, "show_costs": True}).json()
    for path in ("ideas", "ideas/alternatives?card=Cultivate"):
        assert bob.get(f"{V1}/decks/{mine}/{path}").status_code == 404, path
        assert bob.get(f"{V1}/decks/999999/{path}").status_code == 404
    assert bob.get(f"{V1}/decks/{mine}/ideas").text == bob.get(f"{V1}/decks/999999/ideas").text  # another person's deck and a missing one look alike
    for token in (invite["url"].split("invite=")[1], deck_invite["url"].split("invite=")[1]):
        share = bob.post(f"{V1}/shares/accept", json={"token": token}).json()["id"]
        for path in (f"decks/{mine}/ideas", f"decks/{mine}/ideas/alternatives?card=Cultivate", "ideas", f"collection/decks/{mine}/ideas",
                     f"deck/ideas", f"deck/ideas/alternatives?card=Cultivate"):
            assert bob.get(f"{V1}/shared/{share}/{path}").status_code == 404, path
        assert bob.get(f"{V1}/decks/{mine}/ideas").status_code == 404  # accepting a share does not open the deck's ideas
    # Bob's own deck is allocated from Bob's own collection and decks: nothing of Alice's
    own(app, {"Cultivate": 1, "Sol Ring": 1}, email="bob@example.com")
    theirs = save(bob, "Bob Brew", "1 Cultivate\n1 Sol Ring")
    got = ideas(bob, theirs)
    assert got["summary"]["complete"] is True and "Secret Brew" not in str(got) and "Mind Stone" not in str(got)
    assert "Secret Brew" not in str(alternatives(bob, theirs, "Cultivate"))


def test_anonymous_callers_are_refused(client):
    assert client.get(f"{V1}/decks/1/ideas").status_code == 401
    assert client.get(f"{V1}/decks/1/ideas/alternatives", params={"card": "x"}).status_code == 401


def test_a_read_only_token_reads_both_routes(lab, bot, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    h = auth(make_token(lab))
    assert bot.get(f"{V1}/decks/{deck}/ideas", headers=h).status_code == 200
    assert bot.get(f"{V1}/decks/{deck}/ideas/alternatives", params={"card": "Cultivate"}, headers=h).status_code == 200


def test_the_routes_are_limited_per_person(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    codes = [lab.get(f"{V1}/decks/{deck}/ideas", params={"lane": "lands"}).status_code for _ in range(122)]
    assert codes[:120] == [200] * 120 and codes[120:] == [429, 429]


def test_the_catalog_not_being_loaded_is_a_503_not_a_wrong_answer(alice, app):
    deck = save(alice, "Early", "1 Sol Ring")
    assert alice.get(f"{V1}/decks/{deck}/ideas").status_code == 503


def test_the_endpoints_have_response_models_in_the_openapi_document(app):
    paths = app.openapi()["paths"]
    root = paths[f"{V1}/decks/{{deck_id}}/ideas"]["get"]
    assert root["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/DeckIdeas")
    assert {"lane", "limit", "cursor", "include_combos"} <= {p["name"] for p in root["parameters"]}
    alt = paths[f"{V1}/decks/{{deck_id}}/ideas/alternatives"]["get"]
    assert alt["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/DeckAlternatives")
    fmt = next(p for p in alt["parameters"] if p["name"] == "format")
    assert "commander" in str(fmt["schema"]) and "modern" in str(fmt["schema"])  # the formats the Vault supports


# -- provenance and the tools ---------------------------------------------------------------------------------------------

def test_both_answers_carry_provenance_and_prices_carry_their_date(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    for body in (ideas(lab, deck), alternatives(lab, deck, "Cultivate")):
        [block] = body["provenance"]
        assert block["kind"] == "computed" and block["source"] == "The Vault" and block["notice"]
        assert {"Scryfall", "Scryfall Tagger"} <= {i["source"] for i in block["inputs"]}
        assert body["prices_date"] == "2026-10-04"


def test_the_tools_are_listed_classified_as_scryfall_data_and_read_only(lab):
    from vault.api import mcp

    for name in ("get_deck_ideas", "get_card_alternatives"):
        tool = mcp.BY_NAME[name]
        assert name in mcp.SCRYFALL_DATA and name not in mcp.OWN_DATA_ONLY and tool.provenance == ("scryfall",)
        assert tool.write is False and "share_id" not in tool.properties and tool.schema()["annotations"]["readOnlyHint"] is True
        assert "price" in tool.description.lower() or "buy" in tool.description.lower()
    assert mcp._invalid(mcp.BY_NAME["get_deck_ideas"].schema()["inputSchema"], {"deck_id": 1, "lane": "nonsense"}, "arguments")
    assert mcp._invalid(mcp.BY_NAME["get_card_alternatives"].schema()["inputSchema"], {"deck_id": 1, "card": "x", "format": "nonsense"}, "arguments")
    assert mcp._invalid(mcp.BY_NAME["get_card_alternatives"].schema()["inputSchema"], {"deck_id": 1}, "arguments")  # card is required


def test_get_deck_ideas_gives_what_the_route_gives_with_provenance(lab, bot, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    read = make_token(lab)
    answer = call_tool(bot, read, "get_deck_ideas", deck_id=deck, lane="ramp", limit=2)
    assert not answer["isError"], answer
    body = answer["structuredContent"]
    assert [l["lane"] for l in body["lanes"]] == ["ramp"] and body["lanes"][0]["count"] == 2 and body["deck"]["name"] == "Ramp Deck"
    assert body["provenance"][0]["inputs"] and body["prices_date"] == "2026-10-04"
    assert body["lanes"][0]["_links"]["next"]["href"] and body["lanes"][0]["next_cursor"]
    # a lane's cursor is a plain value an assistant passes back as `cursor`
    rest = call_tool(bot, read, "get_deck_ideas", deck_id=deck, lane="ramp", limit=2, cursor=body["lanes"][0]["next_cursor"])["structuredContent"]
    assert [i["card"] for i in rest["lanes"][0]["items"]] == ["Sol Ring"]
    assert call_tool(bot, read, "get_deck_ideas", deck_id=999999).get("isError") is True  # not theirs: 404


def test_get_card_alternatives_carries_scryfall_provenance_and_dated_prices(lab, bot, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    save(lab, "Rock Deck", "1 Mind Stone")
    read = make_token(lab)
    answer = call_tool(bot, read, "get_card_alternatives", deck_id=deck, card="Cultivate", format="commander")
    assert not answer["isError"], answer
    body = answer["structuredContent"]
    assert body["prices_date"] == "2026-10-04" and {"Scryfall", "Scryfall Tagger"} <= {i["source"] for i in body["provenance"][0]["inputs"]}
    assert body["provenance"][0]["notice"]  # the Fan Content notice
    priced = [r for r in body["items"] if r["buy"]]
    assert priced and all(r["buy"]["price_date"] == "2026-10-04" for r in priced) and body["card"]["buy"]["price_date"] == "2026-10-04"
    from test_agents import rpc
    bad = rpc(bot, "tools/call", {"name": "get_card_alternatives", "arguments": {"deck_id": deck, "card": "Cultivate", "format": "nonsense"}}, read).json()
    assert "error" in bad and "format" in bad["error"]["message"]  # the input schema refuses what the endpoint would


# -- combos, only on request ----------------------------------------------------------------------------------------------

def test_combos_are_asked_of_commander_spellbook_only_when_requested(database_url):
    from twins import Universe
    from vault.app import create_app
    from vault.config import Settings

    universe = Universe()
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as client:
        sign_in(client)
        extra = [card(40, "Thassa's Oracle", "Creature — Merfolk", ["U"], 2), card(41, "Demonic Consultation", "Instant", ["B"], 1)]
        seed(app, extra_cards=extra)
        own(app, {"Thassa's Oracle": 1})
        deck = save(client, "Combo Deck", "Deck\n1 Thassa's Oracle\n1 Demonic Consultation\n1 Sol Ring")
        plain = ideas(client, deck)
        assert plain["combos"] is None and not [c for c in universe.spellbook.calls if c.path == "/find-my-combos"]
        asked = ideas(client, deck, include_combos="true")
        [combo] = asked["combos"]["combos"]
        assert sorted(combo["cards"]) == ["Demonic Consultation", "Thassa's Oracle"] and combo["owned"] is False  # one of the two is not owned
        assert combo["url"].startswith("https://commanderspellbook.com/combo/") and combo["source"] == "Commander Spellbook"
        assert asked["combos"]["checked"] is True
        assert "Commander Spellbook" in {i["source"] for i in asked["provenance"][0]["inputs"]}
        assert len([c for c in universe.spellbook.calls if c.path == "/find-my-combos"]) == 1
        universe.spellbook.fail_next("/find-my-combos", 500)
        failed = ideas(client, deck, include_combos="true")
        assert failed["combos"]["checked"] is False and "Commander Spellbook" in failed["combos"]["reason"]
        assert failed["summary"] == asked["summary"]  # the rest of the answer stands
    app.state.db.engine.dispose()
    assert not universe.escapes


# -- performance: the first page of a 100-card deck -----------------------------------------------------------------------

def hundred_card_deck():
    """A 100-card Commander deck: the commander, 63 different cards of every role (some with several) and 36 basic lands."""
    extra, tags, lines = [], {"ramp": [], "draw-engine": [], "removal": [], "sweeper": [], "tutor": []}, ["Commander", "1 Test Commander", "", "Deck"]
    roles = list(tags)
    for i in range(63):
        n = 200 + i
        extra.append(card(n, f"Perf Card {i}", "Artifact" if i % 2 else "Sorcery", ["G"], 1 + i % 5, rank=n))
        tags[roles[i % 5]].append((n, "strong"))
        if i % 7 == 0:
            tags[roles[(i + 2) % 5]].append((n, "median"))  # a second role
        lines.append(f"1 Perf Card {i}")
    lines.append("36 Test Mountain")
    return extra, tags, "\n".join(lines) + "\n"


def count_queries(app):
    statements = []

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = app.state.db.engine
    event.listen(engine, "before_cursor_execute", before)
    return statements, lambda: event.remove(engine, "before_cursor_execute", before)


def test_the_first_ideas_page_of_a_100_card_deck_makes_a_small_constant_number_of_queries(lab, app, capsys):
    extra, tags, text = hundred_card_deck()
    seed(app, extra_cards=extra, extra_tags=tags, prices={200 + i: 0.5 + i / 10 for i in range(0, 63, 3)})
    own(app, {f"Perf Card {i}": 1 for i in range(0, 63, 2)} | {"Test Commander": 1})
    for n in range(12):  # other decks, so the allocation has more to weigh
        save(lab, f"Side {n}", "\n".join(f"1 Perf Card {(n * 5 + i) % 63}" for i in range(20)))
    deck = save(lab, "Hundred", text)
    ideas(lab, deck)  # warm up
    statements, stop = count_queries(app)
    started = time.perf_counter()
    first = lab.get(f"{V1}/decks/{deck}/ideas")
    ideas_ms = (time.perf_counter() - started) * 1000
    stop()
    assert first.status_code == 200
    body = first.json()
    assert body["summary"]["copies"] == 100 and sum(l["copies"] for l in body["lanes"]) == 100
    import gzip
    assert len(gzip.compress(first.content)) < 60_000  # the design's budget for the first page
    asked = len(statements)
    assert asked <= 20, f"the first ideas page made {asked} queries:\n" + "\n".join(s.strip().splitlines()[0][:110] for s in statements)
    statements, stop = count_queries(app)
    started = time.perf_counter()
    alt = lab.get(f"{V1}/decks/{deck}/ideas/alternatives", params={"card": "Perf Card 1"})
    alt_ms = (time.perf_counter() - started) * 1000
    stop()
    assert alt.status_code == 200
    asked_alt = len(statements)
    assert asked_alt <= 25, f"alternatives made {asked_alt} queries:\n" + "\n".join(s.strip().splitlines()[0][:110] for s in statements)
    with capsys.disabled():
        print(f"\nPERF ideas: {asked} queries, {ideas_ms:.0f} ms, {len(first.content)} bytes; alternatives: {asked_alt} queries, {alt_ms:.0f} ms")
    assert ideas_ms < 1500 and alt_ms < 1500  # the production budget is 400 ms and 300 ms (p95); this guards against a slow path
    # the query count does not grow with the deck: the same for a 20-card deck
    small = save(lab, "Twenty", "\n".join(f"1 Perf Card {i}" for i in range(20)))
    statements, stop = count_queries(app)
    assert lab.get(f"{V1}/decks/{small}/ideas").status_code == 200
    stop()
    assert abs(len(statements) - asked) <= 2
