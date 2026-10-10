"""What a card does, stored and shown everywhere (#435, docs/functional-equivalents.md section 13).

The Vault's 22 roles (``vault.equivalents``) are read when the catalog loads and stored with each card (``oracle_cards.roles``, a
GIN-indexed object, with ``roles_version``; both are part of the row's content hash). From that one column come: the card's list of
points with the rule that found each ("Doubles tokens"), Browse's filter on the person's own collection (combined with the other
filters, bucket and tag), "what this deck does" (every role, gaps shown empty), and the read-only tool ``card_roles``. Every answer
carries the label "The Vault's reading of the card text, not an official classification" and a computed provenance; Scryfall's
Tagger tags stay a separate, labelled list. The sync reports a card whose text the rules cannot read."""

import time
from datetime import date

import pytest
from sqlalchemy import select, text, update
from tests.ids import sid

from test_agents import auth, bot, call_tool, make_token  # noqa: F401 - fixtures
from tests.test_collection_filters import CSV, card as printing
from tests.test_deck_api import card as oracle, oid
from tests.test_deck_independence import save
from vault import card_roles, catalog_sync as cs, equivalents as eq
from vault.api import mcp
from vault.models import OracleCard
from vault.sync import sync

V1 = "/api/v1"
CARDS = f"{V1}/collection/cards"
DAY = date(2026, 10, 10)

# n, name, type line, Oracle text. The four cards the fixture CSV owns come first, then cards the person does not own.
TEXT = {
    "kil": "When A Killer Among Us enters the battlefield, create a 1/1 white Human creature token, a 1/1 blue Merfolk creature token, and a 1/1 red Goblin creature token.",
    "sol": "{T}: Add {C}{C}.",
    "acc": "{T}: Add {C}.",
    "bel": "Flying\nWhenever Belfry Spirit deals combat damage to a player, you gain 3 life.",
}
CATALOG = [
    (1, "A Killer Among Us", "Legendary Artifact Creature — Golem", TEXT["kil"]),                       # token maker (once)
    (2, "Sol Ring", "Artifact", TEXT["sol"]),                                                              # mana rock
    (3, "Accursed Marauder", "Artifact Land", TEXT["acc"]),                                                # a land: no role
    (4, "Belfry Spirit", "Enchantment Creature — Spirit", TEXT["bel"]),                                    # lifegain
    (5, "Doubling Season", "Enchantment", "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.\n"
                                           "If an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead."),
    (6, "Rhystic Study", "Enchantment", "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}."),
    (7, "Counterspell", "Instant", "Counter target spell."),
    (8, "Dull Bear", "Creature — Bear", ""),
]
OWNED = {"kil": 1, "sol": 2, "acc": 3, "bel": 4}


def oracle_rows():
    return [oracle(n, name, tl, (), 2, rank=n, text=txt, legalities={"commander": "legal", "modern": "legal"},
                   image_uris={"normal": f"https://cards.scryfall.io/normal/front/{n}.jpg"}, artist=f"Artist {n}",
                   scryfall_uri=f"https://scryfall.com/card/{n}") for n, name, tl, txt in CATALOG]


def seed_catalog(app, rows=None):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, rows if rows is not None else oracle_rows(), today=DAY)
        for source in ("oracle_cards", "oracle_tags"):
            cs.record_source(db, source, version=source + "-1", rows=1)
        db.commit()


def seed_collection(app, client):
    """The fixture CSV's four cards, matched to printings that know their Oracle card (so the filter can find them)."""
    assert client.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}).status_code == 201
    printings = [printing("kil", "A Killer Among Us", "mkm", "167", "Legendary Artifact Creature — Golem", 4, usd=0.12),
                 printing("sol", "Sol Ring", "c21", "263", "Artifact", 1, ("nonfoil", "foil"), usd=1.0, usd_foil=3.0),
                 printing("acc", "Accursed Marauder", "mh3", "512", "Artifact Land", None, ("etched",), usd_etched=0.8),
                 printing("bel", "Belfry Spirit", "gk2", "29", "Enchantment Creature — Spirit", 3.5, usd=0.25)]
    for p, n in zip(printings, (1, 2, 3, 4)):
        p.oracle_id = oid(n)
    with app.state.db.sessions() as db:
        sync(db, printings, day=DAY)


@pytest.fixture
def owner(app, signed_in):
    seed_catalog(app)
    seed_collection(app, signed_in)
    return signed_in


def names(client, **params):
    items, res = [], client.get(CARDS, params=params)
    assert res.status_code == 200, res.text
    while True:
        body = res.json()
        items += [i["name"] for i in body["items"]]
        if "next" not in body["_links"]:
            return items
        res = client.get(body["_links"]["next"]["href"])


# -- stored when the catalog loads ------------------------------------------------------------------------------------------------

def test_the_sync_stores_each_cards_roles_with_the_rule_and_the_version(app):
    seed_catalog(app)
    with app.state.db.sessions() as db:
        sol = db.get(OracleCard, oid(2))
        assert sol.roles == {"mana-rock": {"strength": "core", "rule": "mana-rock-taps", "repeatable": None}}
        assert sol.roles_version == eq.RULES_VERSION == card_roles.VERSION
        bear = db.get(OracleCard, oid(8))
        assert bear.roles == {} and bear.roles_version == card_roles.VERSION  # read, and nothing found: not "not read yet"
        doubling = db.get(OracleCard, oid(5))
        assert list(doubling.roles) == ["token-doubler", "counter-doubler"]  # the Vault's order
        # the stored reading is the one the matcher computes, for every card of the catalog
        for row in db.scalars(select(OracleCard)):
            assert card_roles.hits_of(row) == eq.roles_of(row)


def test_the_roles_are_in_the_content_hash_so_a_change_of_rules_rewrites_every_row_once(app, monkeypatch):
    seed_catalog(app)
    rows = oracle_rows()
    with app.state.db.sessions() as db:
        assert cs.sync_oracle_cards(db, rows, today=DAY)["written"] == 0  # nothing changed, nothing touched
        # a row loaded before this change: no roles, no version, the hash it had then
        db.execute(update(OracleCard).values(roles={}, roles_version=None, content_hash="before-roles"))
        db.commit()
        again = cs.sync_oracle_cards(db, rows, today=DAY)
        assert again["written"] == len(CATALOG)  # the backfill: every row is rewritten, once
        db.commit()
        assert db.get(OracleCard, oid(2)).roles_version == card_roles.VERSION and db.get(OracleCard, oid(2)).roles
        assert cs.sync_oracle_cards(db, rows, today=DAY)["written"] == 0
        # and a rule that changes (a new version of the rules) does it again
        monkeypatch.setattr(card_roles, "VERSION", "2099-01-01.1")
        assert cs.sync_oracle_cards(db, rows, today=DAY)["written"] == len(CATALOG)
        db.commit()
        assert db.get(OracleCard, oid(2)).roles_version == "2099-01-01.1"


def test_the_sync_reports_the_cards_whose_text_the_rules_cannot_read(app, monkeypatch):
    real = eq.derive

    def reading(r):
        if "unreadable" in r.text:
            raise ValueError("a wording no rule was written for")
        return real(r)
    monkeypatch.setattr(eq, "derive", reading)
    rows = oracle_rows() + [oracle(20, "Odd Card", "Sorcery", (), 1, text="This is unreadable.")]
    with app.state.db.sessions() as db:
        result = cs.sync_oracle_cards(db, rows, today=DAY)
        db.commit()
        assert result["unread_roles"] == ["Odd Card"] and "ValueError" in result["unread_roles_why"]["Odd Card"]
        assert result["cards"] == len(CATALOG) + 1  # the load went on
        odd = db.get(OracleCard, oid(20))
        assert odd.roles == {} and odd.roles_version == card_roles.VERSION  # stored as read-with-no-roles: not recomputed on every request
        assert "unread_roles" not in cs.sync_oracle_cards(db, oracle_rows(), today=DAY)  # the report is only there when something was unread


# -- one card -------------------------------------------------------------------------------------------------------------------------

def test_a_card_lists_what_it_does_with_the_rule_the_label_and_the_provenance(owner):
    body = owner.get(f"{V1}/catalog/cards/roles", params={"name": "Doubling Season"}).json()
    assert body["label"] == "The Vault's reading of the card text, not an official classification"
    assert body["card"]["name"] == "Doubling Season" and body["roles_version"] == eq.RULES_VERSION
    assert [(r["point"], r["strength"], r["rule"], r["basis"]) for r in body["roles"]] == [
        ("Doubles tokens", "core", "token-doubling", "computed"), ("Doubles counters", "core", "counter-doubling", "computed")]
    first = body["roles"][0]
    assert "token-doubling" in first["why"] and "Known to get wrong" in first["why"] and first["name"] == "Token doubler"
    computed = body["provenance"][0]
    assert computed["kind"] == "computed" and "not an official classification" in computed["origin"]
    assert computed["inputs"][0]["source"] == "Scryfall" and computed["notice"]  # the Oracle text is Wizards' via Scryfall
    assert body["community_tags"] == [] and "community's opinion" in body["community_tags_label"]


def test_a_repeating_token_maker_says_so_in_its_point_and_a_card_with_no_role_says_that_is_not_nothing(owner):
    sol = owner.get(f"{V1}/catalog/cards/roles", params={"name": "Sol Ring"}).json()
    assert [r["point"] for r in sol["roles"]] == ["Taps for mana (a mana rock)"]
    bear = owner.get(f"{V1}/catalog/cards/roles", params={"name": "Dull Bear"}).json()
    assert bear["roles"] == [] and "not the same as the card doing nothing" in bear["message"]
    assert card_roles.point_of("token-maker", True) == "Makes tokens again and again" and card_roles.point_of("token-maker", False) == "Makes tokens once"
    assert card_roles.point_of("treasure", None) == "Makes Treasure"


def test_a_card_name_the_catalog_does_not_know_is_a_404_or_suggestions(owner):
    assert owner.get(f"{V1}/catalog/cards/roles", params={"name": "Zzzz Nothing Like It"}).status_code == 404
    close = owner.get(f"{V1}/catalog/cards/roles", params={"name": "Sol Rin"}).json()
    assert close["card"] is None and close["suggestions"] == ["Sol Ring"] and close["roles"] == []
    assert owner.get(f"{V1}/catalog/cards/roles").status_code == 422


def test_scryfall_tagger_tags_come_apart_and_are_labelled_a_community_opinion(app, owner):
    with app.state.db.sessions() as db:
        cs.sync_oracle_tags(db, [{"id": "t1", "slug": "ramp", "label": "Ramp", "parent_ids": [], "child_ids": [],
                                  "taggings": [{"oracle_id": oid(2), "weight": "strong"}]}])
        db.commit()
    body = owner.get(f"{V1}/catalog/cards/roles", params={"name": "Sol Ring"}).json()
    assert [t["tag"] for t in body["community_tags"]] == ["ramp"] and "community's opinion" in body["community_tags_label"]
    assert all(r["basis"] == "computed" for r in body["roles"])  # a tag never becomes a role
    assert any(p["source"] == "Scryfall Tagger" for p in body["provenance"])


def test_a_card_that_has_not_been_read_yet_is_read_on_the_fly_not_shown_as_having_no_roles(app, owner):
    with app.state.db.sessions() as db:  # the window between a deploy and the next catalog load
        db.execute(update(OracleCard).values(roles={}, roles_version=None))
        db.commit()
    body = owner.get(f"{V1}/catalog/cards/roles", params={"name": "Sol Ring"}).json()
    assert [r["role"] for r in body["roles"]] == ["mana-rock"]
    roles = owner.get(f"{V1}/collection/roles").json()
    assert roles["read_cards"] == 0 and "not been read with these rules yet" in roles["note"]  # the filter says so, it does not pretend
    assert names(owner, role="mana-rock") == []


def test_the_vocabulary_lists_all_22_roles_with_their_rules(owner):
    body = owner.get(f"{V1}/catalog/roles").json()
    assert [r["role"] for r in body["roles"]] == [r.slug for r in eq.VOCABULARY] and len(body["roles"]) == 22
    assert sum(len(r["rules"]) for r in body["roles"]) == len(eq.RULES)
    assert all(r["point"] and r["rules"][0]["matches"] and r["rules"][0]["known_to_get_wrong"] for r in body["roles"])
    assert body["label"] == card_roles.LABEL and body["provenance"][0]["kind"] == "computed"


def test_every_role_has_a_point_and_the_points_are_distinct():
    assert set(card_roles.POINTS) == set(eq.ROLES) and len(set(card_roles.POINTS.values())) == 22


# -- Browse: the filter over the person's own collection --------------------------------------------------------------------------

def test_browse_filters_by_role_and_the_count_says_how_many_entries_match(owner):
    body = owner.get(CARDS, params={"role": "mana-rock"}).json()
    assert [i["name"] for i in body["items"]] == ["Sol Ring"] and body["total"] == 1  # the count Browse shows
    assert names(owner, role="token-maker") == ["A Killer Among Us"]
    assert names(owner, role="lifegain") == ["Belfry Spirit"]
    assert names(owner, role="draw-engine") == []  # a role none of the cards has: an empty answer, not an error
    assert body["_links"]["self"]["href"].endswith("role=mana-rock")  # the links keep the filter


def test_several_roles_need_all_of_them_unless_any_is_asked(owner):
    assert names(owner, role=["mana-rock", "token-maker"]) == []
    assert names(owner, role=["mana-rock", "token-maker"], role_match="any") == ["A Killer Among Us", "Sol Ring"]
    body = owner.get(CARDS, params={"role": ["mana-rock", "token-maker"], "role_match": "any"}).json()
    assert body["_links"]["self"]["href"].count("role=") == 2 and "role_match=any" in body["_links"]["self"]["href"]


def test_the_role_filter_combines_with_the_other_filters_and_the_bucket_and_tag(owner):
    assert names(owner, role="mana-rock", set="c21") == ["Sol Ring"]
    assert names(owner, role="mana-rock", set="mkm") == []
    assert names(owner, role="mana-rock", type="Artifact") == ["Sol Ring"]
    assert names(owner, role=["token-maker", "lifegain"], role_match="any", type="Creature") == ["A Killer Among Us", "Belfry Spirit"]
    assert names(owner, role=["token-maker", "lifegain"], role_match="any", q="belfry") == ["Belfry Spirit"]
    tag = owner.post(f"{V1}/collection/tags/trade/cards", json={"card_ids": [g["id"] for g in owner.get(CARDS, params={"name": "Sol Ring"}).json()["items"]]})
    assert tag.status_code in (200, 201), tag.text
    assert names(owner, role=["mana-rock", "token-maker"], role_match="any", tag="trade") == ["Sol Ring"]
    bucket = owner.get(f"{V1}/collection/buckets").json()["items"][0]["id"]
    assert names(owner, role="mana-rock", bucket=bucket) == ["Sol Ring"]  # the CSV's one folder holds all four cards


def test_a_role_that_does_not_exist_is_a_400_that_names_the_roles(owner):
    res = owner.get(CARDS, params={"role": "nonsense"})
    assert res.status_code == 400 and "mana-rock" in res.json()["detail"]
    assert owner.get(CARDS, params={"role": "mana-rock", "role_match": "most"}).status_code == 422


def test_the_role_filter_only_sees_the_callers_own_cards(app, owner):
    from fastapi.testclient import TestClient

    with TestClient(app) as other:
        assert other.post("/api/auth/dev-login", params={"email": "bob@example.com"}).status_code == 200
        assert other.get(CARDS, params={"role": "mana-rock"}).json()["total"] == 0
        assert other.get(f"{V1}/collection/roles/cards", params={"role": "mana-rock"}).json()["total"] == 0
        assert {r["role"]: r["cards"] for r in other.get(f"{V1}/collection/roles").json()["items"]}["mana-rock"] == 0


# -- the roles of the collection and their cards ---------------------------------------------------------------------------------------------

def test_the_collection_lists_every_role_with_how_many_of_its_cards_have_it(owner):
    body = owner.get(f"{V1}/collection/roles").json()
    got = {r["role"]: (r["cards"], r["core_cards"]) for r in body["items"]}
    assert len(got) == 22 and got["mana-rock"] == (1, 1) and got["token-maker"] == (1, 1) and got["lifegain"] == (1, 1) and got["counterspell"] == (0, 0)
    assert body["label"] == card_roles.LABEL and body["read_cards"] == len(CATALOG) and body["note"] is None
    assert body["provenance"][0]["kind"] == "computed"
    assert body["items"][0]["_links"]["cards"]["href"].endswith("/roles/cards?role=mana-rock")


def test_the_cards_for_a_role_are_one_row_per_card_with_copies_and_the_rule(owner):
    body = owner.get(f"{V1}/collection/roles/cards", params={"role": "mana-rock"}).json()
    [sol] = body["items"]
    assert sol["card"] == "Sol Ring" and sol["copies"] == 1 and [r["rule"] for r in sol["roles"]] == ["mana-rock-taps"]
    assert body["label"] == card_roles.LABEL and body["match"] == "all" and body["role"] == ["mana-rock"]
    both = owner.get(f"{V1}/collection/roles/cards", params={"role": ["token-maker", "lifegain"], "match": "any"}).json()
    assert [(c["card"], c["copies"]) for c in both["items"]] == [("A Killer Among Us", 4), ("Belfry Spirit", 1)]
    assert owner.get(f"{V1}/collection/roles/cards", params={"role": "nonsense"}).status_code == 400
    assert owner.get(f"{V1}/collection/roles/cards").status_code == 422  # a role is needed


def test_a_collection_shared_with_you_answers_the_same_questions(app, owner):
    from fastapi.testclient import TestClient

    token = owner.post(f"{V1}/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    with TestClient(app) as bob:
        assert bob.post("/api/auth/dev-login", params={"email": "bob@example.com"}).status_code == 200
        accepted = bob.post(f"{V1}/shares/accept", json={"token": token})
        assert accepted.status_code == 200, accepted.text
        base = f"{V1}/shared/{accepted.json()['id']}/collection"
        assert [i["name"] for i in bob.get(base + "/cards", params={"role": "mana-rock"}).json()["items"]] == ["Sol Ring"]
        assert {r["role"]: r["cards"] for r in bob.get(base + "/roles").json()["items"]}["mana-rock"] == 1


# -- a deck: what it does -----------------------------------------------------------------------------------------------------------------------

DECK = "Commander\n1 Belfry Spirit\n\nDeck\n1 Sol Ring\n1 Doubling Season\n1 Rhystic Study\n1 Dull Bear\n3 Accursed Marauder\n1 Not In The Catalog\n"


def test_a_deck_shows_every_role_with_its_cards_and_the_gaps_empty(owner):
    res = owner.post(f"{V1}/decks/roles", json={"text": DECK})
    assert res.status_code == 200, res.text
    body = res.json()
    r = body["result"]
    assert [x["role"] for x in r["roles"]] == [v.slug for v in eq.VOCABULARY] and len(r["roles"]) == 22  # all of them, in the Vault's order
    by = {x["role"]: x for x in r["roles"]}
    assert [c["card"] for c in by["mana-rock"]["items"]] == ["Sol Ring"] and by["mana-rock"]["cards"] == 1 and by["mana-rock"]["empty"] is False
    assert [c["card"] for c in by["token-doubler"]["items"]] == ["Doubling Season"]
    assert by["draw-engine"]["items"][0]["rule"] == "draw-engine" and by["draw-engine"]["items"][0]["point"] == "Draws cards again and again"
    assert by["counterspell"] == {**by["counterspell"], "cards": 0, "copies": 0, "empty": True, "items": []}  # the gap is there, empty
    assert r["distinct_cards"] == 6 and r["unmatched"] == ["Not In The Catalog"]
    assert [c["card"] for c in r["without_role"]] == ["Dull Bear"] and r["lands_without_role"] == 3  # lands are counted apart, the bear is named
    assert r["label"] == card_roles.LABEL and "not an official classification" in body["provenance"][0]["origin"]
    assert body["deck"]["overview"]["format"] or body["deck"]["overview"] is not None  # the answer leads with the deck


def test_a_saved_deck_by_id_and_only_ones_own(owner):
    deck = save(owner, "Roles deck", DECK)
    assert owner.post(f"{V1}/decks/roles", json={"deck_id": deck}).json()["deck"]["name"] == "Roles deck"
    assert owner.post(f"{V1}/decks/roles", json={"deck_id": deck + 100}).status_code == 404
    assert owner.post(f"{V1}/decks/roles", json={}).status_code == 422


# -- timing on a large catalog and collection -----------------------------------------------------------------------------------------------

def test_filtering_a_large_collection_is_one_query_on_the_index(app, signed_in):
    """22,000 catalog cards and a collection of 6,000 printings of them: the role filter is a single query and fast."""
    from sqlalchemy import insert
    from vault.models import Bucket, Card, Entry, User

    texts = ["{T}: Add {C}.", "Draw a card.", "Counter target spell.", "Destroy target creature.", "Gain 3 life.", ""]
    with app.state.db.sessions() as db:
        uid = db.scalar(select(User.id))
        bucket = db.scalar(select(Bucket.id).where(Bucket.user_id == uid).limit(1)) or db.scalar(insert(Bucket).values(user_id=uid, name="Unsorted", kind="default").returning(Bucket.id))
        slugs = [{k: v for k, v in card_roles.stored(type("R", (), {"name": "X", "type_line": "Artifact", "oracle_text": t, "faces": None})()).items()} for t in texts]
        db.execute(insert(OracleCard), [{"oracle_id": f"{i:08d}-0000-0000-0000-000000000000", "name": f"Card {i}", "type_line": "Artifact", "oracle_text": texts[i % 6],
                                         "roles": slugs[i % 6], "roles_version": card_roles.VERSION, "content_hash": "x", "colors": [], "color_identity": [],
                                         "keywords": [], "produced_mana": [], "legalities": {}, "digital": False} for i in range(22000)])
        db.execute(insert(Card), [{"scryfall_id": sid(f"p{i}"), "oracle_id": f"{i:08d}-0000-0000-0000-000000000000", "name": f"Card {i}", "set_code": "tst",
                                   "collector_number": str(i), "colors": [], "color_identity": [], "finishes": []} for i in range(0, 22000, 4)])
        db.execute(insert(Entry), [{"user_id": uid, "name": f"Card {i}", "quantity": 1, "scryfall_id": sid(f"p{i}"), "set_code": "tst", "collector_number": str(i),
                                    "bucket_id": bucket, "position": 0}
                                   for i in range(0, 22000, 4)])
        db.commit()
        db.execute(text("ANALYZE oracle_cards"))
        started = time.perf_counter()
        found = card_roles.owned_printings(db, uid, ["mana-rock"])
        one_role = time.perf_counter() - started
        started = time.perf_counter()
        counts = card_roles.owned_counts(db, uid)
        counted = time.perf_counter() - started
        both = card_roles.owned_printings(db, uid, ["counterspell", "spot-removal"], "any")
        assert len(found) == len([i for i in range(0, 22000, 4) if i % 6 == 0]) and counts["mana-rock"]["cards"] == len(found)
        assert len(both) == len([i for i in range(0, 22000, 4) if i % 6 in (2, 3)])
        plan = "\n".join(db.execute(text("EXPLAIN SELECT oracle_id FROM oracle_cards WHERE roles ?& array['counterspell']")).scalars())
    assert one_role < 1.0 and counted < 1.5, (one_role, counted)
    print(f"role filter over 22,000 catalog cards and {len(found) + 0} owned of 5,500 printings: {one_role * 1000:.0f} ms; counts of all roles: {counted * 1000:.0f} ms\n{plan}")


# -- the tool ---------------------------------------------------------------------------------------------------------------------------------

def test_the_card_roles_tool_is_read_only_classified_and_described_with_its_limits():
    tool = mcp.BY_NAME["card_roles"]
    assert tool.write is False and tool.schema()["annotations"]["readOnlyHint"] is True and tool.provenance == ("computed",)
    for phrase in ("not an official classification", "Scryfall Tagger tags", "community_tags", "rule", "deck_id", "no role known"):
        assert phrase in tool.description or phrase.replace("no role known", "no role found") in tool.description, phrase
    assert "role" in mcp.BY_NAME["search_cards"].schema()["inputSchema"]["properties"]
    assert mcp._invalid(tool.schema()["inputSchema"], {"role": ["nonsense"]}, "arguments")
    assert mcp._invalid(tool.schema()["inputSchema"], {"role": ["mana-rock"], "match": "any"}, "arguments") is None


def test_the_tool_gives_a_cards_roles_the_persons_cards_for_a_role_and_a_decks_roles(owner, bot):
    token = make_token(owner)
    card = call_tool(bot, token, "card_roles", card="Doubling Season")
    assert not card["isError"], card
    body = card["structuredContent"]
    assert [r["point"] for r in body["roles"]] == ["Doubles tokens", "Doubles counters"] and body["label"] == card_roles.LABEL
    assert body["provenance"][0]["kind"] == "computed"

    cards = call_tool(bot, token, "card_roles", role=["token-maker", "lifegain"], match="any")["structuredContent"]
    assert [c["card"] for c in cards["items"]] == ["A Killer Among Us", "Belfry Spirit"] and cards["label"] == card_roles.LABEL
    assert cards["items"][0]["roles"][0]["why"].startswith("Found by the Vault's rule")

    summary = call_tool(bot, token, "card_roles")["structuredContent"]
    assert len(summary["items"]) == 22 and {r["role"]: r["cards"] for r in summary["items"]}["mana-rock"] == 1

    deck = save(owner, "Roles deck", DECK)
    mine = call_tool(bot, token, "card_roles", deck_id=deck)["structuredContent"]
    assert len(mine["result"]["roles"]) == 22 and mine["result"]["roles"][0]["role"] == "mana-rock" and mine["deck"]["name"] == "Roles deck"

    found = call_tool(bot, token, "search_cards", role=["mana-rock"])["structuredContent"]
    assert [i["name"] for i in found["items"]] == ["Sol Ring"]  # the same filter as Browse
    shared_without_share = call_tool(bot, token, "card_roles", role=["mana-rock"], share_id=999)
    assert shared_without_share["isError"]  # a collection that is not shared with the person is not theirs to read
