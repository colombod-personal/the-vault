"""The catalog API (/api/v1/catalog): lookups by the card or rule asked about, with provenance on every
answer, verbatim-quote checks, caps, rate limits, and no way to dump the catalog."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests.test_rules_parser import SAMPLE
from vault import catalog_sync as cs
from vault import provenance as prov
from vault.app import create_app
from vault.config import Settings

V1 = "/api/v1/catalog"
BOLT = "11111111-1111-1111-1111-111111111111"
FIRE = "22222222-2222-2222-2222-222222222222"


def card(oracle_id, name, text, **extra):
    base = {"object": "card", "id": "p-" + oracle_id[:4], "oracle_id": oracle_id, "name": name, "layout": "normal",
            "mana_cost": "{R}", "cmc": 1.0, "type_line": "Instant", "oracle_text": text, "colors": ["R"],
            "color_identity": ["R"], "keywords": [], "legalities": {"modern": "legal"}, "digital": False}
    base.update(extra)
    return base


def load(app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [
            card(BOLT, "Lightning Bolt", "Lightning Bolt deals 3 damage to any target."),
            card(FIRE, "Fire // Ice", "Fire deals 2 damage divided as you choose among one or two targets.", layout="split"),
        ])
        cs.sync_rulings(db, [{"object": "ruling", "oracle_id": BOLT, "source": "wotc", "published_at": f"20{10 + i}-01-01",
                              "comment": f"Ruling number {i} about the bolt’s target."} for i in range(30)])
        cs.sync_oracle_tags(db, [{"id": "t-1", "slug": "removal-burn", "label": "removal-burn", "parent_ids": [], "child_ids": [],
                                  "taggings": [{"oracle_id": BOLT, "weight": "strong"}]},
                                 {"id": "t-0", "slug": "removal", "label": "removal", "parent_ids": [], "child_ids": ["t-1"], "taggings": []}])
        cs.sync_oracle_prices(db, [{"oracle_id": BOLT, "scryfall_id": "p-1111", "usd": 0.69, "usd_foil": 2.5, "eur": 0.5, "day": date(2026, 10, 4), "source": "scryfall"}])
        for name in ("oracle_cards", "rulings", "oracle_tags", "oracle_prices"):
            cs.record_source(db, name, version=name + "-1", rows=1)
        db.commit()
    serve_rules(app)


def serve_rules(app, text=SAMPLE):
    """The rules are read live from Wizards (nothing stored): point the app at the Wizards twin serving ``text``."""
    from twins.universe import Universe
    universe = Universe(seed=False)
    universe.wizards.publish(text)
    app.state.rules_live.reset(universe.transport)
    app.state.rules_live.edition()
    return universe


@pytest.fixture
def loaded(signed_in, app):
    load(app)
    return signed_in


def blocks(body):
    return body["provenance"]


def test_anonymous_callers_are_always_refused(client, app, monkeypatch):
    """No anonymous catalog, even if an old PUBLIC_CATALOG setting is still around: an anonymous card-data API would proxy
    Scryfall's data, which its terms forbid (owner decision 2026-10-06, #62)."""
    monkeypatch.setenv("PUBLIC_CATALOG", "1")
    load(app)
    for path, params in ((f"{V1}/cards", {"name": "Lightning Bolt"}), (f"{V1}/rules/search", {"q": "sample rule"}),
                         (f"{V1}/rules/100.1", {})):
        res = client.get(path, params=params)
        assert res.status_code == 401 and "Bearer" in res.headers["www-authenticate"], path


def test_signed_in_people_are_rate_limited(database_url):
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver",
                        catalog_rate_limit=3)
    app = create_app(settings, serve_static=False)
    load(app)
    with TestClient(app) as person:
        person.post("/api/auth/dev-login")
        codes = [person.get(f"{V1}/cards", params={"name": "Lightning Bolt"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    app.state.db.engine.dispose()


def test_a_card_by_name_carries_its_text_tags_and_provenance(loaded):
    body = loaded.get(f"{V1}/cards", params={"name": "lightning bolt"}).json()
    assert body["card"]["oracle_text"].startswith("Lightning Bolt deals 3") and body["card"]["legalities"]["modern"] == "legal"
    assert body["rulings_total"] == 30 and body["tags"] == [{"tag": "removal-burn", "label": "removal-burn", "weight": "strong"}]
    sources = {b["origin"]: b for b in blocks(body)}  # Scryfall appears twice: card text and prices
    text = sources["Wizards of the Coast (card text)"]
    assert text["source"] == "Scryfall" and text["kind"] == "source"
    assert text["notice"] == prov.FAN_CONTENT_NOTICE and text["version"] == "oracle_cards-1"
    assert "opinions" in sources["community tags, opinions rather than rules"]["origin"]  # tags never pass as rules


def test_the_price_is_in_the_answer_dated_and_credited(loaded):
    """Found by driving the tools against real data: the response model dropped `price`, so the card view never had one."""
    body = loaded.get(f"{V1}/cards", params={"name": "Lightning Bolt"}).json()
    assert body["price"] == {"usd": 0.69, "usd_foil": 2.5, "eur": 0.5, "as_of": "2026-10-04", "source": "scryfall",
                             "printing": "the cheapest priced paper printing"}
    assert "Scryfall" in {b["source"] for b in body["provenance"]} and any("TCGplayer" in (b["origin"] or "") for b in body["provenance"])
    assert loaded.get(f"{V1}/cards", params={"name": "Fire"}).json()["price"] is None  # no price known: none claimed


def test_a_plain_language_search_falls_back_to_any_word_and_says_so(loaded):
    """Found with real data: 'protection from red damage prevented' matched no rule because every word was required."""
    all_words = loaded.get(f"{V1}/rules/search", params={"q": "sample rule"}).json()
    assert all_words["matched"] == "all words" and {r["number"] for r in all_words["results"]} >= {"100.1", "100.2"}
    some = loaded.get(f"{V1}/rules/search", params={"q": "sample subrule wraps unrelated words"}).json()
    assert some["matched"] == "any word" and "100.1a" in {r["number"] for r in some["results"]}
    nothing = loaded.get(f"{V1}/rules/search", params={"q": "zzzzqq xxxxyy"}).json()
    assert nothing["results"] == [] and nothing["matched"] in ("all words", "any word")


def test_either_face_of_a_double_faced_card_finds_it(loaded):
    assert loaded.get(f"{V1}/cards", params={"name": "Fire"}).json()["card"]["name"] == "Fire // Ice"
    assert loaded.get(f"{V1}/cards", params={"name": "Ice"}).json()["card"]["name"] == "Fire // Ice"


def test_a_misspelling_gets_suggestions_and_no_guess(loaded):
    body = loaded.get(f"{V1}/cards", params={"name": "Lightnin Bolt"}).json()
    assert body["card"] is None and body["suggestions"] == ["Lightning Bolt"]
    assert loaded.get(f"{V1}/cards", params={"name": "Nothing Like It"}).status_code == 404
    assert loaded.get(f"{V1}/cards").status_code == 422


def test_rulings_are_capped_newest_first_and_attributed(loaded):
    body = loaded.get(f"{V1}/cards/{BOLT}/rulings", params={"limit": 25}).json()
    assert len(body["rulings"]) == 25 and body["total"] == 30
    dates = [r["published_at"] for r in body["rulings"]]
    assert dates == sorted(dates, reverse=True) and body["rulings"][0]["source"] == "wotc"
    assert blocks(body)[0]["origin"] == "Wizards of the Coast (rulings)"
    assert loaded.get(f"{V1}/cards/{BOLT}/rulings", params={"limit": 26}).status_code == 422
    assert loaded.get(f"{V1}/cards/{'0' * 36}/rulings").status_code == 404


def test_rules_by_number_with_subrules_and_glossary(loaded):
    body = loaded.get(f"{V1}/rules/100.1").json()
    assert body["version"] == "2027-03-03" and body["rule"]["text"] == "First sample rule."
    assert [r["number"] for r in body["subrules"]] == ["100.1a"]
    assert blocks(body)[0]["source"] == "Wizards of the Coast" and blocks(body)[0]["version"] == "2027-03-03"
    assert blocks(body)[0]["notice"] == prov.FAN_CONTENT_NOTICE
    glossary = loaded.get(f"{V1}/rules/glossary:sample term").json()
    assert glossary["rule"]["kind"] == "glossary"
    assert loaded.get(f"{V1}/rules/999.9").status_code == 404


def test_rule_search_is_capped_and_needs_a_query(loaded):
    body = loaded.get(f"{V1}/rules/search", params={"q": "sample rule", "limit": 10}).json()
    assert {r["number"] for r in body["results"]} >= {"100.1", "100.2"} and len(body["results"]) <= 10
    assert all(r["kind"] != "heading" for r in body["results"])
    assert loaded.get(f"{V1}/rules/search").status_code == 422 and loaded.get(f"{V1}/rules/search", params={"q": "a"}).status_code == 422


def test_a_quote_is_verified_only_when_it_is_verbatim(loaded):
    ok = loaded.post(f"{V1}/verify-citation", json={"kind": "rule", "ref": "100.1", "quote": "first   sample rule."}).json()
    assert ok["verified"] is False  # case matters: only whitespace and quote style are forgiven
    ok = loaded.post(f"{V1}/verify-citation", json={"kind": "rule", "ref": "100.1", "quote": "First  sample\nrule."}).json()
    assert ok["verified"] is True and ok["provenance"][0]["source"] == "Wizards of the Coast"
    bad = loaded.post(f"{V1}/verify-citation", json={"kind": "rule", "ref": "100.1", "quote": "First invented rule."}).json()
    assert bad["verified"] is False and bad["detail"]["source_text"] == "First sample rule."  # the true text, to correct it
    text = loaded.post(f"{V1}/verify-citation", json={"kind": "oracle_text", "ref": "Lightning Bolt", "quote": "deals 3 damage to any target"}).json()
    assert text["verified"] is True
    ruling = loaded.post(f"{V1}/verify-citation", json={"kind": "ruling", "ref": "Lightning Bolt", "quote": "bolt's target."}).json()
    assert ruling["verified"] is True  # a typographic apostrophe in the source matches a plain one
    assert loaded.post(f"{V1}/verify-citation", json={"kind": "nonsense", "ref": "x", "quote": "y"}).status_code == 422


def test_status_names_the_versions_held_and_repeats_the_notice(loaded):
    body = loaded.get(f"{V1}/status").json()
    assert body["rules_version"] == "2027-03-03" and body["sources"]["rulings"]["version"] == "rulings-1"
    assert body["notice"] == prov.FAN_CONTENT_NOTICE and {b["source"] for b in blocks(body)} >= {"Scryfall", "Scryfall Tagger"}


def test_every_catalog_endpoint_answers_with_provenance(loaded):
    answers = [loaded.get(f"{V1}/status"), loaded.get(f"{V1}/cards", params={"name": "Lightning Bolt"}),
               loaded.get(f"{V1}/cards/{BOLT}/rulings"), loaded.get(f"{V1}/rules/100.1"),
               loaded.get(f"{V1}/rules/search", params={"q": "sample"}),
               loaded.post(f"{V1}/verify-citation", json={"kind": "rule", "ref": "100.1", "quote": "First sample rule."})]
    for res in answers:
        assert res.status_code == 200, res.text
        assert res.json()["provenance"] and all(b["kind"] in ("source", "computed") and b["source"] for b in res.json()["provenance"])


FACE_CARDS = [
    ("33333333-3333-3333-3333-333333333331", "Delver of Secrets // Insectile Aberration", "transform",
     [("Delver of Secrets", "At the beginning of your upkeep, look at the top card of your library. You may reveal that card. If an instant or sorcery card is revealed this way, transform Delver of Secrets."),
      ("Insectile Aberration", "Flying")]),
    ("33333333-3333-3333-3333-333333333332", "Valki, God of Lies // Tibalt, Cosmic Impostor", "modal_dfc",
     [("Valki, God of Lies", "When Valki enters the battlefield, each opponent reveals their hand."),
      ("Tibalt, Cosmic Impostor", "As Tibalt enters the battlefield, you get an emblem with the abilities of exiled cards.")]),
    ("33333333-3333-3333-3333-333333333333", "Fire // Ice (faces)", "split",
     [("Fire", "Fire deals 2 damage divided as you choose among one or two targets."), ("Ice", "Tap target permanent. Draw a card.")]),
    ("33333333-3333-3333-3333-333333333334", "Bonecrusher Giant // Stomp", "adventure",
     [("Bonecrusher Giant", "Whenever Bonecrusher Giant becomes the target of a spell, it deals 2 damage to that spell's controller."),
      ("Stomp", "Damage can't be prevented this turn. Stomp deals 2 damage to any target.")]),
    ("33333333-3333-3333-3333-333333333335", "Akki Lavarunner // Tok-Tok, Volcano Born", "flip",
     [("Akki Lavarunner", "Haste"), ("Tok-Tok, Volcano Born", "If a source would deal damage to you, prevent 1 of that damage.")]),
]


def load_face_cards(app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [card(oid, name, None, layout=layout, oracle_text=None,
                                       card_faces=[{"name": n, "oracle_text": t, "type_line": "Creature"} for n, t in faces])
                                  for oid, name, layout, faces in FACE_CARDS])
        db.commit()


@pytest.mark.parametrize("index", range(len(FACE_CARDS)))
def test_a_quote_from_either_face_of_a_multi_face_card_verifies(loaded, app, index):
    """#249: the front face of Delver, a modal DFC, a split card, an adventure or a flip card verifies verbatim, and a
    quote spanning two faces is refused with every face's text so the caller can correct it."""
    load_face_cards(app)
    oid, name, layout, faces = FACE_CARDS[index]
    for face_name, text in faces:
        quote = text[: max(20, len(text) // 2)]
        said = loaded.post(f"{V1}/verify-citation", json={"kind": "oracle_text", "ref": oid, "quote": quote}).json()
        assert said["verified"] is True and said["detail"]["face"] == face_name, (layout, face_name, said)
    both = faces[0][1][-12:] + " " + faces[1][1][:12]
    refused = loaded.post(f"{V1}/verify-citation", json={"kind": "oracle_text", "ref": oid, "quote": both}).json()
    assert refused["verified"] is False
    assert [f["face"] for f in refused["detail"]["source_text"]] == [n for n, _ in faces]  # all faces' text, to correct it
    invented = loaded.post(f"{V1}/verify-citation", json={"kind": "oracle_text", "ref": oid, "quote": "Draw seven cards."}).json()
    assert invented["verified"] is False


def test_the_card_panel_shows_every_face_not_only_the_first():
    from vault.api.mcp_ui import CARD_JS
    assert "c.faces" in CARD_JS and "faces.forEach" in CARD_JS and "f.oracle_text" in CARD_JS


def test_status_and_whoami_report_the_live_rules_edition_even_on_a_cold_server(signed_in, app):
    """#244: whoami answered rules_version null while the rules tools answered from edition 2026-09-25."""
    from twins.universe import Universe
    load(app)
    universe = Universe(seed=False)
    universe.wizards.publish(SAMPLE)
    app.state.rules_live.reset(universe.transport)  # cold: nothing read yet on this instance
    assert app.state.rules_live.cached_version is None
    status = signed_in.get(f"{V1}/status").json()
    assert status["rules_version"] == app.state.rules_live.cached_version and status["rules_version"]
    universe.wizards.outage = True
    app.state.rules_live.reset(universe.transport)  # cold again, and Wizards is down: no edition, and no failure
    down = signed_in.get(f"{V1}/status")
    assert down.status_code == 200 and down.json()["rules_version"] is None


def test_rulings_page_on_with_offset_so_the_oldest_ones_can_be_read(loaded):
    """#23: a card with 30 rulings used to show 25 and hide the other five for good."""
    first = loaded.get(f"{V1}/cards/{BOLT}/rulings", params={"limit": 25}).json()
    assert first["next_offset"] == 25 and first["offset"] == 0
    rest = loaded.get(f"{V1}/cards/{BOLT}/rulings", params={"limit": 25, "offset": first["next_offset"]}).json()
    assert len(rest["rulings"]) == 5 and rest["next_offset"] is None
    seen = {r["comment"] for r in first["rulings"]} | {r["comment"] for r in rest["rulings"]}
    assert len(seen) == 30 and "Ruling number 0 about the bolt’s target." in seen


def test_a_real_quote_from_the_oldest_ruling_verifies(loaded):
    """#25: verify_citation only read the newest 25 rulings, so a genuine quote from an older one was called unverified."""
    old = loaded.post(f"{V1}/verify-citation", json={"kind": "ruling", "ref": "Lightning Bolt", "quote": "Ruling number 0 about the bolt's target."}).json()
    assert old["verified"] is True and old["detail"]["ruling"]["published_at"] == "2010-01-01"
    bad = loaded.post(f"{V1}/verify-citation", json={"kind": "ruling", "ref": "Lightning Bolt", "quote": "Ruling number 99 about nothing."}).json()
    assert bad["verified"] is False and bad["detail"]["rulings_checked"] == 30
