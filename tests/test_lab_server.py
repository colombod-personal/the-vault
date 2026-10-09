"""The server side of the Lab (#164, docs/lab-design.md tasks 1 and 2): spare copies (`GET /collection/spare`,
`/collection/spare/printings`, `list_spare_copies`), profit and loss (`GET /collection/pnl`, `get_collection_pnl`) and the
market-only `summary` of `GET /collection/history`. Every test names the line of the design's "Tests (write first)" list it
covers."""

from datetime import date, timedelta

import pytest

from tests.ids import sid
from tests.test_agents import bot, call_tool, make_token  # noqa: F401 - fixtures
from vault.api import mcp
from vault.models import Card, CollectionValue, User

V1 = "/api/v1"
HEADER = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
          "Price Bought,Date Bought,LOW,MID,MARKET\n")


def row(qty, name, number="1", *, trade=0, code="XXX", printing="Normal", condition="Mint", paid="", market="1.00"):
    """One Dragon Shield row. Nothing is in the card table, so a row stays unmatched (no Scryfall id) and is priced by the
    file's market column: an empty `market` is a copy with no price."""
    return f"my cards,{qty},{trade},{name},{code},Set {code},{number},{condition},{printing},English,{paid},,{market},{market},{market}\n"


def csv(*rows):
    return (HEADER + "".join(rows)).encode()


def put(client, *rows):
    res = client.post(f"{V1}/imports", files={"file": ("c.csv", csv(*rows), "text/csv")})
    assert res.status_code == 201, res.text


def save(client, name, text):
    res = client.post(f"{V1}/decks", json={"name": name, "text": text})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def login(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def spare(client, **params):
    res = client.get(f"{V1}/collection/spare", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def by_name(body):
    return {i["name"]: i for i in body["items"]}


@pytest.fixture
def me(signed_in):
    return signed_in


# -- spare copies: the count ------------------------------------------------------------------------------

def test_spare_is_have_minus_need_by_name_and_only_positive_spares_are_listed(me):
    """Design: a card owned once and needed by two decks is not listed and no spare is ever negative; basic lands never
    appear; a card used by two decks needing 1 each with 3 owned has spare 1."""
    put(me, row(1, "Sol Ring"), row(3, "Cavern of Souls", paid="1", market="5.00"), row(20, "Forest"), row(5, "Mountain"),
        row(1, "Short Card", market="9.00"))
    for deck in ("A", "B"):
        save(me, deck, "1 Sol Ring\n1 Cavern of Souls\n10 Forest\n2 Short Card")
    body = spare(me)
    assert body["status"] == "ok"
    items = by_name(body)
    assert set(items) == {"Cavern of Souls"}  # Sol Ring (1 of 2 needed) and Short Card (1 of 4) are short; basics never listed
    cavern = items["Cavern of Souls"]
    assert (cavern["have"], cavern["needed"], cavern["spare"], cavern["deck_count"]) == (3, 2, 1, 2)
    assert cavern["market_value_of_spare"] == 5.0
    assert all(i["spare"] > 0 for i in body["items"])


def test_a_card_in_no_deck_is_fully_spare_once_a_deck_exists(me):
    put(me, row(2, "Rhystic Study", market="4.00"), row(1, "Sol Ring"))
    save(me, "Ramp", "1 Sol Ring")
    rhystic = by_name(spare(me))["Rhystic Study"]
    assert (rhystic["have"], rhystic["needed"], rhystic["spare"], rhystic["deck_count"]) == (2, 0, 2, 0)
    assert rhystic["market_value_of_spare"] == 8.0


def test_names_count_by_front_face_and_case_like_everywhere_else(me):
    put(me, row(2, "Fire // Ice", market="1.00"), row(1, "fire", number="2"))
    save(me, "Burn", "1 Fire // Ice")
    items = spare(me)["items"]
    assert [(i["have"], i["needed"], i["spare"]) for i in items] == [(3, 1, 2)]  # one name: "Fire // Ice" and "fire" are Fire


def test_with_no_saved_decks_it_says_so_and_never_reports_the_collection_as_spare(me):
    put(me, row(4, "Sol Ring", market="2.00"))
    body = spare(me)
    assert body["status"] == "no_decks" and body["items"] == [] and body["total"] == 0
    assert body["summary"]["names"] == 0 and body["summary"]["copies"] == 0 and body["summary"]["market_value"] == 0.0
    assert body["decks_analysed"] == 0 and "Save" in body["note"]
    assert me.get(f"{V1}/collection/spare/printings", params={"name": "Sol Ring"}).json()["items"] == []


def test_an_empty_collection_is_its_own_state(me):
    save(me, "Ramp", "1 Sol Ring")
    body = spare(me)
    assert body["status"] == "empty_collection" and body["items"] == [] and body["summary"]["copies"] == 0


def test_a_deck_that_cannot_be_read_does_not_count_and_is_reported(me, monkeypatch):
    from vault import deck_text
    real = deck_text.parse_text

    def parse(text, *args, **kwargs):
        if "unreadable" in text:
            raise ValueError("cannot be read")
        return real(text, *args, **kwargs)

    put(me, row(2, "Sol Ring"))
    save(me, "Ramp", "1 Sol Ring")
    save(me, "Broken", "1 Sol Ring\n1 unreadable")
    monkeypatch.setattr(deck_text, "parse_text", parse)  # a saved list the parser can no longer read
    body = spare(me)
    assert body["decks_analysed"] == 1 and body["decks_skipped"] == 1
    assert by_name(body)["Sol Ring"]["spare"] == 1


# -- spare copies: unmatched and unpriced copies, links ----------------------------------------------------

def test_unmatched_and_unpriced_copies_get_no_link_and_no_price_and_sort_last(me):
    put(me, row(2, "Mystery Card", market=""), row(1, "Priced Card", market="3.00"))
    save(me, "Ramp", "1 Sol Ring")
    body = spare(me)
    assert [i["name"] for i in body["items"]] == ["Priced Card", "Mystery Card"]
    mystery = by_name(body)["Mystery Card"]
    assert mystery["market_value_of_spare"] == 0.0 and (mystery["priced_copies"], mystery["unpriced_copies"]) == (0, 2)
    [printing] = mystery["printings"]
    assert printing["scryfall_id"] is None and printing["scryfall_link"] is None and printing["unit_price"] is None
    assert printing["price_status"] == "unpriced" and printing["spare_quantity"] == 2
    assert printing["scryfall_search"].startswith("https://scryfall.com/search?q=") and "Mystery" in printing["scryfall_search"]
    assert body["summary"]["unpriced_copies"] == 2 and body["summary"]["priced_copies"] == 1
    assert body["summary"]["market_value"] == 3.0


def test_a_matched_printing_links_to_its_scryfall_page(app, me):
    with app.state.db.sessions() as db:
        db.add(Card(scryfall_id=sid("sol"), oracle_id=sid("sol-oracle"), name="Sol Ring", set_code="c21", collector_number="263",
                    scryfall_uri="https://scryfall.com/card/c21/263/sol-ring"))
        db.commit()
    put(me, row(2, "Sol Ring", "263", code="C21", market="2.00"))
    save(me, "Ramp", "1 Sol Ring")
    [printing] = by_name(spare(me))["Sol Ring"]["printings"]
    assert printing["scryfall_id"] == sid("sol") and printing["scryfall_link"] == "https://scryfall.com/card/c21/263/sol-ring"
    assert printing["unit_price"] == 2.0 and printing["price_status"] == "priced" and printing["set"]["code"] == "C21"


# -- spare copies: the summary, paging, order -------------------------------------------------------------

def test_the_summary_covers_every_spare_card_whatever_the_page_size_and_paging_keeps_the_order(me):
    put(me, row(1, "Card A", market="50.00"), row(2, "Card B", market="20.00"), row(1, "Card C", market="30.00"),
        row(1, "Card D", market="20.00"), row(1, "Card E", market="10.00"), row(1, "Card F", market=""))
    save(me, "Ramp", "1 Sol Ring")
    everything = spare(me, limit=500)
    expected = ["Card A", "Card B", "Card C", "Card D", "Card E", "Card F"]  # value of the spare copies (B: 2 x 20), then name; unpriced last
    assert [i["name"] for i in everything["items"]] == expected
    assert everything["summary"] == {"names": 6, "copies": 7, "market_value": 150.0, "priced_copies": 6, "unpriced_copies": 1}
    seen, cursor, summaries = [], None, []
    while True:
        page = spare(me, limit=2, **({"cursor": cursor} if cursor else {}))
        assert page["count"] <= 2 and page["total"] == 6
        seen += [i["name"] for i in page["items"]]
        summaries.append(page["summary"])
        cursor = (page["_links"].get("next") or {}).get("href", "").partition("cursor=")[2] or None
        if not cursor:
            break
    assert seen == expected and all(s == everything["summary"] for s in summaries)


def test_spare_does_not_change_with_the_page_size(me):
    put(me, *(row(3, f"Card {n}", market=f"{n}.00") for n in range(1, 8)))
    save(me, "Ramp", "1 Card 1\n1 Card 2")
    full = {i["name"]: i["spare"] for i in spare(me, limit=500)["items"]}
    one = {}
    cursor = None
    while True:
        page = spare(me, limit=1, **({"cursor": cursor} if cursor else {}))
        one |= {i["name"]: i["spare"] for i in page["items"]}
        cursor = (page["_links"].get("next") or {}).get("href", "").partition("cursor=")[2] or None
        if not cursor:
            break
    assert one == full and full["Card 1"] == 2 and full["Card 7"] == 3


def test_a_bad_cursor_or_limit_is_refused(me):
    put(me, row(1, "Sol Ring"))
    save(me, "Ramp", "1 Mountain")
    assert me.get(f"{V1}/collection/spare", params={"cursor": "!!"}).status_code == 400
    assert me.get(f"{V1}/collection/spare", params={"limit": 0}).status_code == 400


# -- spare copies: which physical copies are spare --------------------------------------------------------

def test_trade_status_never_overrides_price_and_only_breaks_ties(me):
    """A $100 copy that is not for trade and a $1 copy marked for trade, one needed: the decks keep the $1 copy and the
    $100 copy is the spare. At equal prices the trade-marked copy is the one reported as spare."""
    put(me, row(1, "Gold", "1", market="100.00"), row(1, "Gold", "2", market="1.00", trade=1),
        row(1, "Tie", "1", market="5.00"), row(1, "Tie", "2", market="5.00", trade=1))
    save(me, "Deck", "1 Gold\n1 Tie")
    items = by_name(spare(me))
    [gold] = items["Gold"]["printings"]
    assert gold["unit_price"] == 100.0 and gold["trade_marked_quantity"] == 0 and items["Gold"]["market_value_of_spare"] == 100.0
    [tie] = items["Tie"]["printings"]
    assert tie["collector_number"] == "2" and tie["trade_marked_quantity"] == 1 and tie["spare_quantity"] == 1


def test_trade_quantity_is_reported_without_changing_spare(me):
    put(me, row(4, "Plain", "1", market="2.00"), row(4, "Marked", "1", market="2.00", trade=4),
        row(2, "Too Many", "1", market="2.00", trade=5))
    save(me, "Deck", "1 Plain\n1 Marked\n1 Too Many")
    items = by_name(spare(me))
    assert items["Plain"]["spare"] == items["Marked"]["spare"] == 3
    assert items["Plain"]["printings"][0]["trade_marked_quantity"] == 0
    assert items["Marked"]["printings"][0]["trade_marked_quantity"] == 3  # the deck's copy was taken from the same pool
    too_many = items["Too Many"]["printings"][0]  # an import can store more marked copies than there are copies
    assert items["Too Many"]["spare"] == 1 and too_many["spare_quantity"] == 1 and too_many["trade_marked_quantity"] <= 1


def test_mixed_price_printings_leave_the_expensive_foil_as_the_spare_and_the_rows_add_up(me):
    put(me, row(1, "Mixed", "1", market="1.00"), row(1, "Mixed", "2", printing="Foil", market="40.00"))
    save(me, "Deck", "1 Mixed")
    first = by_name(spare(me))["Mixed"]
    [foil] = first["printings"]
    assert foil["printing"] == "Foil" and foil["unit_price"] == 40.0 and first["spare"] == 1
    assert sum(r["spare_quantity"] * r["unit_price"] for r in first["printings"]) == first["market_value_of_spare"] == 40.0
    assert by_name(spare(me))["Mixed"] == first  # stable between calls


def test_printing_rows_are_a_bounded_preview_with_a_paged_continuation(me):
    put(me, *(row(1, "Many", str(n), market=f"{n}.00") for n in range(1, 13)))
    save(me, "Deck", "1 Sol Ring")
    many = by_name(spare(me))["Many"]
    assert many["spare"] == 12 and many["printing_rows_total"] == 12 and len(many["printings"]) == 10
    prices = [r["unit_price"] for r in many["printings"]]
    assert prices == sorted(prices) and prices[0] == 1.0  # allocation order: the cheapest copies come first
    assert many["market_value_of_spare"] == sum(range(1, 13))
    href = many["_links"]["printings"]["href"]
    assert href.startswith(f"{V1}/collection/spare/printings?name=Many")
    got, url = [], href + "&limit=5"
    while url:
        page = me.get(url).json()
        assert page["total"] == 12 and page["count"] <= 5
        got += page["items"]
        url = (page["_links"].get("next") or {}).get("href")
    assert [r["id"] for r in got[:10]] == [r["id"] for r in many["printings"]]
    assert len({r["id"] for r in got}) == 12 and sum(r["spare_quantity"] * r["unit_price"] for r in got) == many["market_value_of_spare"]


def test_the_printings_of_a_name_that_is_not_in_the_collection_answer_404(me):
    put(me, row(1, "Sol Ring"))
    save(me, "Deck", "1 Mountain")
    assert me.get(f"{V1}/collection/spare/printings", params={"name": "Black Lotus"}).status_code == 404
    assert me.get(f"{V1}/collection/spare/printings").status_code == 422  # the name is required


def test_the_answer_carries_the_price_date_and_etag(me, app):
    put(me, row(1, "Sol Ring", market="2.00"))
    save(me, "Deck", "1 Mountain")
    first = me.get(f"{V1}/collection/spare")
    assert "prices_as_of" in first.json() and first.headers["etag"]
    assert me.get(f"{V1}/collection/spare", headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    save(me, "Another", "1 Sol Ring")  # a deck changes who is spare, so the cached answer must not be served
    assert me.get(f"{V1}/collection/spare", headers={"If-None-Match": first.headers["etag"]}).status_code == 200


# -- profit and loss --------------------------------------------------------------------------------------

PNL_ROWS = (row(2, "Alpha", paid="1.00", market="3.00"),  # gain +4.00
            row(1, "Beta", paid="2.00", market="2.50"),  # +0.50
            row(1, "Gamma", paid="10.00", market="4.00"),  # -6.00
            row(2, "Delta", paid="5.00", market="4.00"),  # -2.00
            row(3, "Epsilon", paid="1.00", market=""),  # a price paid but no current price
            row(2, "Zeta", market="1.00"),  # no price paid
            row(1, "Eta", market=""))  # neither


def pnl(client, side="winners", **params):
    res = client.get(f"{V1}/collection/pnl", params={"side": side, **params})
    assert res.status_code == 200, res.text
    return res.json()


def test_winners_and_losers_are_the_signed_holdings_most_extreme_first(me):
    put(me, *PNL_ROWS)
    winners, losers = pnl(me, "winners"), pnl(me, "losers")
    assert [(i["name"], i["gain"]) for i in winners["items"]] == [("Alpha", 4.0), ("Beta", 0.5)]
    assert [(i["name"], i["gain"]) for i in losers["items"]] == [("Gamma", -6.0), ("Delta", -2.0)]
    alpha = winners["items"][0]
    assert (alpha["paid"], alpha["market_value"], alpha["unit_price"], alpha["copies"]) == (2.0, 6.0, 3.0, 2)
    assert alpha["_links"]["self"]["href"].startswith(f"{V1}/collection/cards/")
    assert winners["side"] == "winners" and winners["total"] == 2 and losers["total"] == 2


def test_the_summary_counts_every_copy_once_and_is_the_same_on_every_page(me):
    put(me, *PNL_ROWS)
    summary = pnl(me, "winners")["summary"]
    assert summary["total_copies"] == 12 and summary["covered_copies"] == 6
    assert summary["unknown_cost_copies"] == 3  # Zeta (2) and Eta (1, with neither a cost nor a price: counted once, here)
    assert summary["unpriced_market_copies"] == 3  # Epsilon: paid, but no current price
    assert summary["covered_copies"] + summary["unknown_cost_copies"] + summary["unpriced_market_copies"] == summary["total_copies"]
    assert summary["net_gain"] == -3.5
    assert summary["biggest_gain"]["name"] == "Alpha" and summary["biggest_loss"]["name"] == "Gamma"
    assert pnl(me, "losers", limit=1)["summary"] == summary == pnl(me, "winners", limit=1)["summary"]


def test_a_holding_with_a_price_paid_but_no_current_price_is_never_listed_as_a_loss(me):
    put(me, row(3, "Epsilon", paid="1.00", market=""), row(1, "Beta", paid="2.00", market="2.50"))
    losers = pnl(me, "losers")
    assert losers["items"] == [] and losers["summary"]["biggest_loss"] is None
    assert losers["summary"]["unpriced_market_copies"] == 3
    assert [i["name"] for i in pnl(me, "winners")["items"]] == ["Beta"]


def test_net_gain_is_the_sum_of_the_counted_gains(me):
    put(me, *PNL_ROWS)
    gains = [i["gain"] for side in ("winners", "losers") for i in pnl(me, side)["items"]]
    assert round(sum(gains), 2) == pnl(me)["summary"]["net_gain"]
    on_the_cards = {c["name"]: c["gain"] for c in me.get(f"{V1}/collection/cards", params={"limit": 100}).json()["items"]}
    assert on_the_cards["Gamma"] == -6.0 and on_the_cards["Alpha"] == 4.0  # the same holdings /collection/cards reports


def test_every_holding_profitable_leaves_the_losers_empty(me):
    put(me, row(1, "Alpha", paid="1.00", market="3.00"), row(2, "Beta", paid="2.00", market="2.50"))
    losers = pnl(me, "losers")
    assert losers["items"] == [] and losers["total"] == 0 and losers["summary"]["biggest_loss"] is None
    assert pnl(me, "winners")["summary"]["biggest_gain"]["name"] == "Alpha"
    put(me, row(1, "Gamma", paid="10.00", market="4.00"))
    assert pnl(me, "winners")["items"] == [] and pnl(me, "winners")["summary"]["biggest_gain"] is None
    assert pnl(me, "losers")["summary"]["net_gain"] == -6.0


def test_winners_and_losers_page_independently(me):
    put(me, *(row(1, f"Win {n}", paid="1.00", market=f"{n + 1}.00") for n in range(1, 4)),
        *(row(1, f"Lose {n}", paid="10.00", market=f"{10 - n}.00") for n in range(1, 4)))
    w1, l1 = pnl(me, "winners", limit=2), pnl(me, "losers", limit=2)
    assert [i["name"] for i in w1["items"]] == ["Win 3", "Win 2"] and [i["name"] for i in l1["items"]] == ["Lose 3", "Lose 2"]
    w2 = me.get(w1["_links"]["next"]["href"]).json()
    assert [i["name"] for i in w2["items"]] == ["Win 1"] and "next" not in w2["_links"]
    assert pnl(me, "losers", limit=2) == l1  # continuing one never moves the other
    l2 = me.get(l1["_links"]["next"]["href"]).json()
    assert [i["name"] for i in l2["items"]] == ["Lose 1"] and l2["side"] == "losers"


def test_the_reason_is_chosen_from_the_counts(me):
    put(me, row(2, "Zeta", market="1.00"))
    nothing = pnl(me)["summary"]
    assert nothing["covered_copies"] == 0 and nothing["net_gain"] is None
    assert nothing["reason"] == "no price paid is known"
    put(me, row(2, "Epsilon", paid="1.00", market=""))
    assert pnl(me)["summary"]["reason"] == "prices are not available yet"
    put(me, row(2, "Alpha", paid="1.00", market="3.00"))
    assert pnl(me)["summary"]["reason"] is None


def test_an_empty_collection_has_nothing_to_count(me):
    summary = pnl(me)["summary"]
    assert summary["total_copies"] == 0 and summary["covered_copies"] == 0 and summary["biggest_gain"] is None
    assert summary["reason"]


def test_the_side_must_be_winners_or_losers(me):
    assert me.get(f"{V1}/collection/pnl", params={"side": "both"}).status_code == 422
    assert me.get(f"{V1}/collection/pnl", params={"cursor": "!!"}).status_code == 400


# -- the market-only summary of the value history ---------------------------------------------------------

def test_the_history_summary_is_the_market_over_the_requested_range(me, app):
    put(me, row(1, "Sol Ring"))
    today = date.today()
    with app.state.db.sessions() as db:
        user_id = db.query(User.id).scalar()
        db.query(CollectionValue).delete()
        for days, market, cost in ((30, 100.0, 50.0), (20, 120.0, 50.0), (10, 90.0, 60.0), (0, 110.0, 60.0)):
            db.add(CollectionValue(user_id=user_id, day=today - timedelta(days=days), market_usd=market, cost_usd=cost,
                                   copies=1, priced_copies=1))
        db.commit()
    whole = me.get(f"{V1}/collection/history").json()
    assert whole["summary"] == {"from": (today - timedelta(days=30)).isoformat(), "to": today.isoformat(),
                                "market_start": 100.0, "market_end": 110.0, "market_change": 10.0}
    assert "cost" not in " ".join(whole["summary"])  # market only: history's market covers every holding, cost only some
    since = (today - timedelta(days=15)).isoformat()
    ranged = me.get(f"{V1}/collection/history", params={"since": since, "limit": 1}).json()
    assert ranged["count"] == 1 and ranged["summary"] == {"from": (today - timedelta(days=10)).isoformat(), "to": today.isoformat(),
                                                           "market_start": 90.0, "market_end": 110.0, "market_change": 20.0}


def test_the_history_summary_of_nothing_is_null(me):
    summary = me.get(f"{V1}/collection/history").json()["summary"]
    assert summary == {"from": None, "to": None, "market_start": None, "market_end": None, "market_change": None}


# -- only my own account: shared views, other people -------------------------------------------------------

def test_a_shared_collection_has_no_lab_data_and_no_deck_crosses_the_share(me):
    """The spare, overlap and pnl routes answer 404 under /shared/{id}/..., whatever show_costs is, and a share of the
    collection only never shows a deck name or a deck requirement."""
    put(me, row(3, "Sol Ring", paid="1.00", market="2.00"))
    save(me, "Secret Brew", "1 Sol Ring\n1 Hidden Tech")
    shares = {}
    for show_costs in (True, False):
        invite = me.post(f"{V1}/shares", json={"kind": "collection", "show_costs": show_costs}).json()
        shares[show_costs] = invite["url"].split("invite=")[1]
    login(me, "bob@example.com")
    for show_costs, token in shares.items():
        sid_ = me.post(f"{V1}/shares/accept", json={"token": token}).json()["id"]
        base = f"{V1}/shared/{sid_}"
        for path in ("collection/spare", "collection/spare/printings?name=Sol%20Ring", "collection/pnl?side=winners",
                     "collection/pnl?side=losers", "decks/overlap", "collection/lab"):
            assert me.get(f"{base}/{path}").status_code == 404, (show_costs, path)
        everything = "".join(me.get(f"{base}/collection{p}").text for p in ("", "/cards", "/stats", "/names", "/sets", "/history",
                                                                          "/valuation", "/timeline", "/breakdowns"))
        assert "Secret Brew" not in everything and "Hidden Tech" not in everything
        if not show_costs:
            history = me.get(f"{base}/collection/history").json()
            assert all(i["cost"] is None for i in history["items"]) and "cost" not in " ".join(history["summary"])
            assert "biggest_gains" not in me.get(f"{base}/collection/stats").json()
    own = spare(me)  # Bob's own Lab is Bob's: it never falls back to, or mixes in, the owner's decks or collection
    assert own["status"] == "empty_collection" and own["items"] == [] and "Secret Brew" not in str(own)
    assert pnl(me)["summary"]["total_copies"] == 0


def test_other_peoples_decks_and_collections_never_appear(me):
    put(me, row(2, "Alice Card", paid="1.00", market="3.00"), row(1, "Sol Ring"))
    save(me, "Alice Deck", "1 Sol Ring")
    login(me, "bob@example.com")
    put(me, row(5, "Bob Card", paid="1.00", market="0.50"), row(1, "Sol Ring"))
    assert spare(me)["status"] == "no_decks"  # Alice's deck is not Bob's
    save(me, "Bob Deck", "1 Bob Card")
    body = spare(me)
    assert by_name(body)["Bob Card"]["spare"] == 4 and set(by_name(body)) == {"Sol Ring", "Bob Card"} and "Alice" not in str(body)
    assert [i["name"] for i in pnl(me, "losers")["items"]] == ["Bob Card"] and "Alice" not in str(pnl(me, "winners"))
    assert me.get(f"{V1}/collection/spare/printings", params={"name": "Alice Card"}).status_code == 404


def test_the_lab_routes_need_a_signed_in_person(client):
    for path in ("spare", "spare/printings?name=x", "pnl"):
        assert client.get(f"{V1}/collection/{path}").status_code == 401


# -- the assistant tools -----------------------------------------------------------------------------------

@pytest.fixture
def stocked(me):
    put(me, *PNL_ROWS, row(3, "Sol Ring", market="2.00"))
    save(me, "Ramp", "1 Sol Ring")
    return me


def test_the_tools_are_listed_classified_read_only_and_own_account_only(stocked, bot):
    for name in ("list_spare_copies", "get_collection_pnl"):
        tool = mcp.BY_NAME[name]
        assert name in mcp.SCRYFALL_DATA and name not in mcp.OWN_DATA_ONLY and tool.provenance == ("scryfall",)
        assert tool.write is False and "share_id" not in tool.properties  # the Lab is the person's own
        assert tool.schema()["annotations"]["readOnlyHint"] is True


def test_list_spare_copies_gives_what_the_route_gives_with_provenance(stocked, bot):
    token = make_token(stocked)
    answer = call_tool(bot, token, "list_spare_copies", limit=1)
    assert answer["isError"] is False, answer
    body = answer["structuredContent"]
    assert body["provenance"] and body["status"] == "ok" and body["summary"]["names"] == body["total"]
    route = stocked.get(f"{V1}/collection/spare", params={"limit": 1}).json()
    assert [i["name"] for i in body["items"]] == [i["name"] for i in route["items"]] and body["summary"] == route["summary"]
    rest = call_tool(bot, token, "list_spare_copies", cursor=body["next_cursor"])["structuredContent"] if body.get("next_cursor") else {"items": []}
    assert len(body["items"]) + len(rest["items"]) == body["total"]
    rows = call_tool(bot, token, "list_spare_copies", name="Sol Ring")["structuredContent"]  # one name's printing rows, paged
    assert rows["items"] and rows["items"][0]["spare_quantity"] == 2 and "provenance" in rows
    assert call_tool(bot, token, "list_spare_copies", name="Black Lotus").get("isError")


def test_get_collection_pnl_gives_what_the_route_gives_with_provenance(stocked, bot):
    token = make_token(stocked)
    answer = call_tool(bot, token, "get_collection_pnl", side="losers", limit=1)
    assert answer["isError"] is False, answer
    body = answer["structuredContent"]
    assert body["provenance"] and [i["name"] for i in body["items"]] == ["Gamma"] and body["next_cursor"]
    more = call_tool(bot, token, "get_collection_pnl", side="losers", limit=1, cursor=body["next_cursor"])["structuredContent"]
    assert [i["name"] for i in more["items"]] == ["Delta"] and more["summary"] == body["summary"]
    from tests.test_agents import rpc
    refused = rpc(bot, "tools/call", {"name": "get_collection_pnl", "arguments": {"side": "neither"}}, token).json()
    assert "error" in refused and "side" in refused["error"]["message"]  # refused by the schema before anything is sent


def test_the_tools_refuse_a_share_id(stocked, bot):
    from tests.test_agents import rpc
    token = make_token(stocked)
    for tool in ("list_spare_copies", "get_collection_pnl"):
        refused = rpc(bot, "tools/call", {"name": tool, "arguments": {"share_id": 1}}, token).json()
        assert "error" in refused or refused["result"].get("isError")
