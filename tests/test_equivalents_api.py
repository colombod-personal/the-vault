"""Functional equivalents through `GET /decks/{id}/ideas/alternatives` and the tool `get_card_alternatives` (#166,
docs/functional-equivalents.md): the owner's examples as real answers (Doubling Season for token doubling, Rhystic Study for card
draw, Cyclonic Rift and Fierce Guardianship for interaction, Parallel Lives), the three states of the design (candidates found,
nothing found, found but borrowed by another deck) and the one the Vault must not confuse with them (no role known), the two
tiers, what the filters removed, and the promise that no tag and no outside data is read."""

import shutil
import time

import pytest

from test_agents import auth, bot, call_tool, make_token  # noqa: F401 - fixtures
from tests.test_deck_api import card
from tests.test_deck_independence import alice, bob, own, save, sign_in, stamp, user_id  # noqa: F401 - fixtures and helpers
from tests.test_deck_ideas import V1, alternatives, lab, seed  # noqa: F401 - fixtures and helpers
from vault import equivalents as eq
from vault.models import OracleTagLink

# n, name, type line, colours, mana value, Oracle text
EXTRA = [
    (300, "Test Rainbow", "Legendary Creature — Dragon", ["W", "U", "B", "R", "G"], 5, ""),
    (301, "Doubling Season", "Enchantment", ["G"], 5,
     "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.\n"
     "If an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead."),
    (302, "Parallel Lives", "Enchantment", ["G"], 4,
     "If an effect would create one or more creature tokens under your control, it creates twice that many of those tokens instead."),
    (303, "Anointed Procession", "Enchantment", ["W"], 4, "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead."),
    (304, "Vorinclex, Monstrous Raider", "Legendary Creature — Phyrexian Elk", ["G"], 6,
     "Trample, haste\nIf you would put one or more counters on a permanent or player, put twice that many of each of those kinds of counters on that permanent or player instead.\n"
     "If an opponent would put one or more counters on a permanent or player, they put half that many of each of those kinds of counters on that permanent or player instead, rounded down."),
    (305, "Rhystic Study", "Enchantment", ["U"], 3, "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}."),
    (306, "Phyrexian Arena", "Enchantment", ["B"], 3, "At the beginning of your upkeep, you draw a card and you lose 1 life."),
    (308, "Mystic Remora", "Enchantment", ["U"], 1,
     "Cumulative upkeep {1}\nWhenever an opponent casts a noncreature spell, you may draw a card unless that player pays {4}."),
    (309, "Cyclonic Rift", "Instant", ["U"], 2,
     "Return target nonland permanent you don't control to its owner's hand.\nOverload {6}{U} (You may cast this spell for its overload cost. If you do, change its text by replacing all instances of \"target\" with \"each.\")"),
    (310, "Evacuation", "Instant", ["U"], 5, "Return all creatures to their owners' hands."),
    (311, "Wrath of God", "Sorcery", ["W"], 4, "Destroy all creatures. They can't be regenerated."),
    (312, "Fierce Guardianship", "Instant", ["U"], 3,
     "Flash\nIf you control a commander, you may cast this spell without paying its mana cost.\nCounter target noncreature spell."),
    (313, "Counterspell", "Instant", ["U"], 2, "Counter target spell."),
    (314, "Force of Will", "Instant", ["U"], 5,
     "You may pay 1 life and exile a blue card from your hand rather than pay this spell's mana cost.\nCounter target spell."),
    (315, "Cloudstone Curio", "Artifact", [], 3,
     "Whenever a nonartifact permanent enters the battlefield under your control, you may return another target permanent you control that shares a card type with it to its owner's hand."),
    (316, "Smothering Tithe", "Enchantment", ["W"], 4, "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token."),
    (317, "Dockside Extortionist", "Creature — Goblin Pirate", ["R"], 2,
     "When Dockside Extortionist enters the battlefield, create X Treasure tokens, where X is the number of artifacts and enchantments your opponents control."),
    (318, "Unsummon", "Instant", ["U"], 1, "Return target creature to its owner's hand."),
]


def extra_cards(*more):
    return [card(n, name, tl, colors, cmc, rank=n, text=text, image_uris={"normal": f"https://cards.scryfall.io/normal/front/{n}.jpg"},
                 artist=f"Artist {n}", scryfall_uri=f"https://scryfall.com/card/{n}", legalities={"commander": "legal", "modern": "legal"})
            for n, name, tl, colors, cmc, text in EXTRA] + list(more)


@pytest.fixture
def owner(lab, app):
    seed(app, extra_cards=extra_cards())
    return lab


def deck_of(client, name, *cards, commander="Test Rainbow"):
    return save(client, name, f"Commander\n1 {commander}\n\nDeck\n" + "".join(f"1 {c}\n" for c in cards))


def by_name(body):
    return {r["card"]: r for r in body["items"]}


# -- the owner's examples ---------------------------------------------------------------------------------------------------------

def test_doubling_season_is_matched_by_the_token_doublers_you_own_and_the_difference_is_said(owner, app):
    own(app, {"Anointed Procession": 2, "Vorinclex, Monstrous Raider": 1, "Rhystic Study": 1, "Test Rainbow": 1})
    deck = deck_of(owner, "Sliver Swarm", "Doubling Season")
    body = alternatives(owner, deck, "Doubling Season")
    assert body["card"]["core_roles"] == ["token-doubler", "counter-doubler"] and body["card"]["primary_role"] == "token-doubler"
    assert [(r["role"], r["strength"], r["basis"]) for r in body["card"]["roles"]] == [("token-doubler", "core", "computed"), ("counter-doubler", "core", "computed")]
    assert body["tiers"] == {"same_job": 1, "similar": 1}
    assert [r["card"] for r in body["items"]] == ["Anointed Procession", "Vorinclex, Monstrous Raider"]  # same job, then similar; Rhystic Study is another job
    procession, vorinclex = body["items"]
    assert procession["tier"] == "same_job" and procession["copies_free"] == 2 and procession["borrowed"] is False
    assert [r["role"] for r in procession["shared_roles"]] == ["token-doubler"] and procession["shared_roles"][0]["rule"] == "token-doubling"
    assert procession["lacks"] == [{"role": "counter-doubler", "name": "Counter doubler"}] and procession["extra"] == []  # "Does not: counter doubler"
    assert procession["why"] == "same job: token doubler (core, Vault rule token-doubling); costs 1 less than Doubling Season"
    assert procession["mana_value_change"] == -1.0 and procession["type_note"] is None
    assert procession["oracle_text"].startswith("If an effect would create one or more tokens") and body["card"]["oracle_text"].count("\n") == 1  # both texts
    assert vorinclex["tier"] == "similar" and [r["role"] for r in vorinclex["shared_roles"]] == ["counter-doubler"]
    assert vorinclex["lacks"] == [{"role": "token-doubler", "name": "Token doubler"}] and vorinclex["type_note"] == {"target": "Enchantment", "candidate": "Creature"}
    assert vorinclex["why"].startswith("similar, with a difference: shares counter doubler with Doubling Season")
    assert body["reason"] is None and body["message"] is None


def test_rhystic_study_nothing_found_for_the_same_job_says_so_and_offers_the_similar_ones_separately(owner, app):
    # a red-green deck: the owner's blue and black draw engines are outside its colours, the one-shot draw is inside
    own(app, {"Phyrexian Arena": 1, "Mystic Remora": 1, "Divination": 1, "Test Commander": 1})
    deck = save(owner, "Avatar Aang", "Commander\n1 Test Commander\n\nDeck\n1 Rhystic Study\n")
    body = alternatives(owner, deck, "Rhystic Study")
    assert body["card"]["core_roles"] == ["draw-engine"] and body["tiers"] == {"same_job": 0, "similar": 1}
    assert body["filtered_out"] == {"colour_identity": 2, "format": 0, "in_deck": 0}
    assert body["filtered_note"] == "Owned cards that do this job but were not offered: 2 outside this deck's colour identity (RG)."
    [one_shot] = body["items"]
    assert one_shot["card"] == "Divination" and one_shot["tier"] == "similar"
    assert one_shot["different"] == [{"kind": "neighbour", "target": {"role": "draw-engine", "name": "Card draw (every turn or every trigger)"},
                                      "candidate": {"role": "draw-once", "name": "Card draw (once)"}}]
    assert one_shot["why"].startswith("similar, with a difference: card draw (once), where Rhystic Study is card draw (every turn or every trigger)")
    assert "own nothing else that does exactly this job" in body["message"] and "similar, with a difference" in body["message"]
    assert body["reason"] is None  # something is listed; `message` and `tiers` say that none of it is the same job


def test_rhystic_study_with_nothing_at_all_found_is_the_empty_state(owner, app):
    own(app, {"Phyrexian Arena": 1, "Test Commander": 1})
    deck = save(owner, "Avatar Aang", "Commander\n1 Test Commander\n\nDeck\n1 Rhystic Study\n")
    body = alternatives(owner, deck, "Rhystic Study")
    assert body["items"] == [] and body["reason"] == "none_found" and body["total"] == 0 and body["tiers"] == {"same_job": 0, "similar": 0}
    assert body["message"] == "You own nothing else that does this job in this deck's colours and format."
    assert body["filtered_out"]["colour_identity"] == 1  # and one owned card was set aside for its colours, which the page says


def test_rhystic_study_is_matched_by_the_other_draw_engines_when_the_colours_allow(owner, app):
    own(app, {"Phyrexian Arena": 1, "Mystic Remora": 1, "Divination": 1})
    deck = deck_of(owner, "Aang", "Rhystic Study")
    body = alternatives(owner, deck, "Rhystic Study")
    assert [(r["card"], r["tier"]) for r in body["items"]] == [("Phyrexian Arena", "same_job"), ("Mystic Remora", "same_job"), ("Divination", "similar")]  # the nearer cost first
    assert body["items"][0]["mana_value_change"] == 0.0 and "costs the same as Rhystic Study" in body["items"][0]["why"]
    assert body["items"][1]["mana_value_change"] == -2.0 and "costs 2 less than Rhystic Study" in body["items"][1]["why"]


def test_cyclonic_rift_and_fierce_guardianship_are_two_different_interaction_jobs(owner, app):
    own(app, {"Evacuation": 1, "Unsummon": 1, "Wrath of God": 1, "Counterspell": 1, "Force of Will": 1})
    deck = deck_of(owner, "Control", "Cyclonic Rift", "Fierce Guardianship")
    rift = alternatives(owner, deck, "Cyclonic Rift")
    assert rift["card"]["core_roles"] == ["bounce", "sweeper"] and rift["card"]["primary_role"] == "bounce"
    assert [(r["card"], r["tier"]) for r in rift["items"]] == [("Evacuation", "same_job"), ("Unsummon", "same_job"), ("Wrath of God", "similar")]
    evacuation, unsummon, wrath = rift["items"]
    assert [r["role"] for r in evacuation["shared_roles"]] == ["bounce", "sweeper"] and evacuation["lacks"] == []
    assert unsummon["lacks"] == [{"role": "sweeper", "name": "Board wipe"}]  # a single bounce does not wipe the board
    assert wrath["lacks"] == [{"role": "bounce", "name": "Bounce"}] and wrath["why"].startswith("similar, with a difference: shares board wipe with Cyclonic Rift")
    guardianship = alternatives(owner, deck, "Fierce Guardianship")
    assert guardianship["card"]["core_roles"] == ["counterspell", "free-counterspell"]
    assert [(r["card"], r["tier"]) for r in guardianship["items"]] == [("Force of Will", "same_job"), ("Counterspell", "similar")]
    plain = guardianship["items"][1]
    assert plain["lacks"] == [{"role": "free-counterspell", "name": "Free counterspell"}]
    assert plain["why"].startswith("similar, with a difference: counterspell but not free counterspell")
    assert guardianship["items"][0]["extra"] == [] and guardianship["items"][0]["shared_roles"][1]["rule"] == "counter-free"
    # and the reverse: a free counterspell does a plain counterspell's job, and says it does more
    counter = alternatives(owner, deck, "Counterspell")
    assert counter["items"][0]["card"] == "Force of Will" and counter["items"][0]["tier"] == "same_job"
    assert counter["items"][0]["extra"] == [{"role": "free-counterspell", "name": "Free counterspell"}]


def test_parallel_lives_found_but_borrowed_by_another_deck_says_whose_and_what_moving_leaves(owner, app):
    own(app, {"Doubling Season": 1})
    deck = deck_of(owner, "Sliver Swarm", "Parallel Lives")
    deck_of(owner, "Avatar Aang", "Doubling Season")  # the only copy owned is in this deck
    body = alternatives(owner, deck, "Parallel Lives")
    [season] = body["items"]
    assert season["card"] == "Doubling Season" and season["tier"] == "same_job" and season["borrowed"] is True
    assert season["borrowed_from"] == "Avatar Aang" and season["move"]["from_deck"]["name"] == "Avatar Aang" and season["copies_free"] == 0
    assert season["extra"] == [{"role": "counter-doubler", "name": "Counter doubler"}] and season["lacks"] == []
    assert season["buy"]["quantity"] == 1 and body["card"]["buy"]["quantity"] == 1  # a copy to buy, or the missing card itself, both priced as Scryfall's
    assert body["card"]["status"] == "missing" and body["tiers"] == {"same_job": 1, "similar": 0}


def test_a_card_the_rules_cannot_read_says_no_role_known_and_that_is_not_the_same_as_nothing_like_it(owner, app):
    own(app, {"Doubling Season": 1})
    deck = deck_of(owner, "Sliver Swarm", "Cloudstone Curio")
    body = alternatives(owner, deck, "Cloudstone Curio")  # it returns your own permanents: no role of the Vault's covers that
    assert body["reason"] == "no_role" and body["items"] == [] and body["card"]["roles"] == [] and body["card"]["core_roles"] == []
    assert body["message"].startswith("The Vault knows no role for this card yet") and "not the same as 'you own nothing like it'" in body["message"]
    assert body["card"]["oracle_text"].startswith("Whenever a nonartifact permanent enters") and body["filtered_note"] is None
    none = alternatives(owner, deck_of(owner, "Other", "Smothering Tithe"), "Dockside Extortionist")  # a known role, no owned card
    assert none["reason"] == "none_found" and none["message"].startswith("You own nothing else")


def test_a_treasure_that_happens_once_is_offered_as_similar_to_one_that_repeats(owner, app):
    own(app, {"Dockside Extortionist": 1})
    deck = deck_of(owner, "Treasure Deck", "Smothering Tithe")
    [row] = alternatives(owner, deck, "Smothering Tithe")["items"]
    assert row["tier"] == "similar" and row["different"][0]["kind"] == "repeats"
    assert row["different"][0]["target_repeats"] is True and row["different"][0]["candidate_repeats"] is False
    assert "happens once" in row["why"] and "repeats" in row["why"]
    assert {r["role"]: r["repeatable"] for r in row["roles"]} == {"treasure": False}


# -- no tag, no outside data ----------------------------------------------------------------------------------------------------

def test_no_scryfall_tag_is_read_the_same_answer_comes_back_with_every_tag_removed(owner, app):
    own(app, {"Mind Stone": 1, "Arcane Signet": 1, "Rampant Growth": 1, "Divination": 1})
    deck = deck_of(owner, "Ramp Deck", "Cultivate")
    before = alternatives(owner, deck, "Cultivate")
    assert [r["card"] for r in before["items"]] == ["Rampant Growth", "Arcane Signet", "Mind Stone"]
    with app.state.db.sessions() as db:
        db.query(OracleTagLink).delete()
        db.commit()
    after = alternatives(owner, deck, "Cultivate")
    assert [r["card"] for r in after["items"]] == [r["card"] for r in before["items"]] and after["tiers"] == before["tiers"]


def test_a_card_tagged_by_the_community_but_without_a_role_in_its_text_is_not_offered(lab, app):
    seed(app, extra_cards=[card(330, "Tagged Only", "Artifact", [], 1, rank=330, text="")], extra_tags={"ramp": [(330, "very_strong")]})
    own(app, {"Tagged Only": 1, "Sol Ring": 1})
    deck = save(lab, "Ramp Deck", "Commander\n1 Test Commander\n\nDeck\n1 Mind Stone\n")
    names = {r["card"] for r in alternatives(lab, deck, "Mind Stone")["items"]}
    assert names == {"Sol Ring"}  # Scryfall's Tagger calls the other card ramp; the Vault's rules read no ramp in its (empty) text


def test_the_alternatives_say_where_the_roles_come_from_and_credit_the_oracle_text(owner, app):
    own(app, {"Anointed Procession": 1})
    deck = deck_of(owner, "Sliver Swarm", "Doubling Season")
    body = alternatives(owner, deck, "Doubling Season")
    assert body["roles_version"] == eq.RULES_VERSION and "Vault's own" in body["roles_note"] and "not Scryfall's community tags" in body["roles_note"]
    [block] = body["provenance"]
    assert block["kind"] == "computed" and "version " + eq.RULES_VERSION in block["origin"] and block["notice"]
    assert {i["source"] for i in block["inputs"]} == {"Scryfall"} and all(r["basis"] == "computed" for r in body["card"]["roles"] + body["items"][0]["roles"])


# -- paging and the tiers ---------------------------------------------------------------------------------------------------------

def test_the_tiers_page_in_order_and_the_counts_cover_the_whole_list(owner, app):
    own(app, {"Anointed Procession": 1, "Parallel Lives": 1, "Vorinclex, Monstrous Raider": 1})
    deck = deck_of(owner, "Sliver Swarm", "Doubling Season")
    seen, url = [], f"{V1}/decks/{deck}/ideas/alternatives"
    page = owner.get(url, params={"card": "Doubling Season", "limit": 1}).json()
    while True:
        assert page["tiers"] == {"same_job": 2, "similar": 1} and page["total"] == 3  # the counts are for the list, not the page
        seen += [(r["card"], r["tier"]) for r in page["items"]]
        nxt = page["_links"].get("next")
        if not nxt:
            break
        page = owner.get(nxt["href"]).json()
    assert seen == [("Anointed Procession", "same_job"), ("Parallel Lives", "same_job"), ("Vorinclex, Monstrous Raider", "similar")]


def test_the_rules_run_on_a_large_collection_in_a_bounded_time_and_a_constant_number_of_queries(lab, app):
    """600 owned cards whose text mentions drawing: the words narrow them, the rules read what is left, and the cache makes the second ask cheap."""
    many = [card(1000 + i, f"Owned Drawer {i}", "Sorcery", ["G"], 1 + i % 6, rank=1000 + i, text="Draw two cards." if i % 3 else "Scry 2, then draw a card.",
                 legalities={"commander": "legal", "modern": "legal"}) for i in range(600)]
    seed(app, extra_cards=extra_cards(*many))
    own(app, {f"Owned Drawer {i}": 1 for i in range(600)} | {"Test Commander": 1})
    deck = save(lab, "Big", "Commander\n1 Test Commander\n\nDeck\n1 Rhystic Study\n")
    from tests.test_deck_ideas import count_queries
    eq._CACHE.clear()
    statements, stop = count_queries(app)
    started = time.perf_counter()
    body = alternatives(lab, deck, "Rhystic Study", limit=10)
    cold = time.perf_counter() - started
    stop()
    assert body["total"] == 600 and body["tiers"] == {"same_job": 0, "similar": 600} and len(body["items"]) == 10
    assert len(statements) <= 14, "\n".join(s.strip().splitlines()[0][:110] for s in statements)
    started = time.perf_counter()
    alternatives(lab, deck, "Rhystic Study", limit=10)
    warm = time.perf_counter() - started
    assert cold < 3.0 and warm < cold + 0.5, (cold, warm)


# -- what the page says, from the real answers ------------------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")
def test_the_ideas_page_words_the_tiers_the_difference_and_the_no_role_state_from_the_servers_answers(owner, app):
    from tests.test_ideas_page import page_says
    from tests.test_deck_ideas import ideas

    own(app, {"Anointed Procession": 2, "Vorinclex, Monstrous Raider": 1, "Test Rainbow": 1})
    deck = deck_of(owner, "Sliver Swarm", "Doubling Season", "Cloudstone Curio")
    said = page_says({"ideas": ideas(owner, deck), "alternatives": alternatives(owner, deck, "Doubling Season")})
    assert said["target"]["state"] == "alternatives" and said["target"]["role"] == "Does: token doubler (core), counter doubler (core)"
    assert said["tiers"] == {"same": "Same job (1)", "similar": "Similar, with a difference (1)", "toggle": "Show similar, with a difference (1)",
                             "sameCards": ["Anointed Procession"], "similarCards": ["Vorinclex, Monstrous Raider"]}
    procession, vorinclex = said["alternatives"]
    assert (procession["does"], procession["lacks"], procession["extra"], procession["type"]) == (
        "Does: token doubler", "Does not: counter doubler (Doubling Season does)", "", "")
    assert (vorinclex["does"], vorinclex["lacks"], vorinclex["type"]) == (
        "Does: counter doubler", "Does not: token doubler (Doubling Season does)", "a creature, where Doubling Season is an enchantment.")
    assert procession["why"].startswith("same job: token doubler") and vorinclex["why"].startswith("similar, with a difference")
    none = page_says({"ideas": ideas(owner, deck), "alternatives": alternatives(owner, deck, "Cloudstone Curio")})
    assert none["target"]["state"] == "no-role" and none["target"]["role"] == "The Vault knows no role for this card yet" and none["alternatives"] == []


# -- the tool ---------------------------------------------------------------------------------------------------------------------

def test_get_card_alternatives_describes_the_tiers_and_returns_them(owner, bot, app):
    from vault.api import mcp

    text = mcp.BY_NAME["get_card_alternatives"].description
    for phrase in ("same job", "similar", "Vault's own", "not Scryfall's", "lacks", "oracle_text", "filtered_note"):
        assert phrase in text, phrase
    own(app, {"Anointed Procession": 1, "Vorinclex, Monstrous Raider": 1})
    deck = deck_of(owner, "Sliver Swarm", "Doubling Season")
    answer = call_tool(bot, make_token(owner), "get_card_alternatives", deck_id=deck, card="Doubling Season")
    assert not answer["isError"], answer
    body = answer["structuredContent"]
    assert body["tiers"] == {"same_job": 1, "similar": 1} and [r["tier"] for r in body["items"]] == ["same_job", "similar"]
    assert body["items"][0]["lacks"][0]["name"] == "Counter doubler" and body["provenance"][0]["notice"]
