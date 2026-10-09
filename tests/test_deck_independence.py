"""Deck independence (#165, docs/deck-independence.md): can the saved decks all be built at the same time from the copies owned?

`GET /decks/overlap` (and its paged `decks`, `contested` and `purchases` subresources) and the `get_deck_overlap` tool. Counted by card
name, basic lands left out. The tests follow the design's list: a card owned once and used by two decks, The World Tree (owned twice,
used by three), the worked examples, a mixed shortage, partial allocation, paging, the order and `priority`, an unreadable deck,
tenancy, provenance and the older fields unchanged."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from test_agents import auth, bot, call_tool, make_token  # noqa: F401 - fixtures
from tests.test_deck_api import card, oid
from vault import catalog_sync as cs
from vault.models import Deck, Entry, User

V1 = "/api/v1"
OVERLAP = V1 + "/decks/overlap"
PRICE_DAY = date(2026, 10, 4)


# -- helpers ----------------------------------------------------------------------------------------------------------

def sign_in(client, email="alice@example.com"):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200
    return client


@pytest.fixture
def alice(client):
    return sign_in(client)


@pytest.fixture
def bob(app):
    with TestClient(app) as c:
        yield sign_in(c, "bob@example.com")


def user_id(app, email):
    with app.state.db.sessions() as db:
        return db.scalar(select(User.id).where(User.email == email))


def own(app, copies: dict, email="alice@example.com", set_code=None):
    """Put copies in the person's collection: {card name: quantity}, one row per card (an optional printing)."""
    uid = user_id(app, email)
    with app.state.db.sessions() as db:
        for number, (name, quantity) in enumerate(copies.items(), 1):
            db.add(Entry(user_id=uid, name=name, quantity=quantity, set_code=set_code, collector_number=str(number) if set_code else None))
        db.commit()


def save(client, name, text) -> int:
    res = client.post(f"{V1}/decks", json={"name": name, "text": text})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def set_prices(app, prices: dict):
    """Scryfall's cheapest price per card name, dated PRICE_DAY (the catalog's oracle prices)."""
    with app.state.db.sessions() as db:
        numbered = list(enumerate(prices.items(), 900))
        cs.sync_oracle_cards(db, [card(n, name, "Artifact") for n, (name, _) in numbered], today=PRICE_DAY)
        cs.sync_oracle_prices(db, [{"oracle_id": oid(n), "scryfall_id": f"p{n}", "usd": usd, "usd_foil": None, "eur": None,
                                    "day": PRICE_DAY, "source": "scryfall"} for n, (_, usd) in numbered if usd is not None])
        db.commit()


def root(client, **params):
    res = client.get(OVERLAP, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def by_name(items, key="name"):
    return {i[key]: i for i in items}


def whole(client, sub, **params):
    """Every item of a paged subresource, following `next` to the end, and how many pages that took."""
    items, pages, res = [], 0, client.get(f"{OVERLAP}/{sub}", params=params)
    while True:
        assert res.status_code == 200, res.text
        body = res.json()
        items += body["items"]
        pages += 1
        if "next" not in body["_links"]:
            return items, pages
        res = client.get(body["_links"]["next"]["href"])


def lacking(deck):
    return {l["card"]: l for l in deck["lacking"]}


def holds(deck):
    return {h["card"]: h for h in deck["holds"]}


# -- the allocation -----------------------------------------------------------------------------------------------------

def test_a_card_owned_once_and_used_by_two_decks_is_contested_and_the_first_deck_gets_it(alice, app):
    own(app, {"Rare Card": 1})
    a, b = save(alice, "A", "1 Rare Card"), save(alice, "B", "1 Rare Card")
    body = root(alice, priority=f"{a},{b}")
    decks = by_name(body["decks"])
    assert decks["A"]["stands_alone"] is True and decks["A"]["independent"] is False  # complete, but B depends on its copy
    assert holds(decks["A"])["Rare Card"] == {"card": "Rare Card", "quantity": 1, "also_wanted_by": ["B"], "also_wanted_by_total": 1}
    assert decks["B"]["stands_alone"] is False and decks["B"]["independent"] is False
    assert lacking(decks["B"])["Rare Card"]["quantity"] == 1
    assert lacking(decks["B"])["Rare Card"]["not_owned"] == 0 and lacking(decks["B"])["Rare Card"]["held_by_other_deck"] == 1
    contested = whole(alice, "contested", priority=f"{a},{b}")[0]
    assert [c["card"] for c in contested] == ["Rare Card"]
    c = contested[0]
    assert (c["have"], c["need_for_all"], c["global_deficit"]) == (1, 2, 1)
    assert [(d["deck"], d["gets"], d["lacking"]) for d in c["decks"]] == [("A", 1, 0), ("B", 0, 1)]
    options = by_name(c["options"], "kind")
    assert set(options) == {"move", "buy"}
    assert options["move"]["from_deck"]["name"] == "A" and options["move"]["to_deck"]["name"] == "B" and options["move"]["quantity"] == 1
    assert options["buy"]["quantity"] == 1


def test_the_world_tree_owned_twice_and_used_by_three_decks(alice, app):
    own(app, {"The World Tree": 2})
    set_prices(app, {"The World Tree": 6.60})
    ids = [save(alice, name, "1 The World Tree") for name in ("Avatar Aang", "Sliver Swarm", "The dragon in the night")]
    body = root(alice)
    decks = body["decks"]
    assert sum(1 for d in decks if d["stands_alone"]) == 2 and sum(1 for d in decks if not d["stands_alone"]) == 1
    short = next(d for d in decks if not d["stands_alone"])
    assert lacking(short)["The World Tree"]["held_by_other_deck"] == 1 and lacking(short)["The World Tree"]["not_owned"] == 0
    assert short["cost_to_complete"] == 6.6
    [tree] = whole(alice, "contested")[0]
    assert (tree["have"], tree["need_for_all"], tree["global_deficit"]) == (2, 3, 1)
    assert {o["kind"] for o in tree["options"]} == {"move", "buy"}
    buy = by_name(tree["options"], "kind")["buy"]
    assert buy["quantity"] == 1 and buy["unit_price"] == 6.6 and buy["cost"] == 6.6 and buy["price_date"] == "2026-10-04"
    assert body["summary"]["decks_needing_purchase"] == 1 and body["summary"]["finish_all_cost"] == 6.6
    assert body["prices_date"] == "2026-10-04"
    own(app, {"The World Tree": 1}, set_code="xyz")  # buying one copy (another printing) completes all three
    after = root(alice)
    assert all(d["stands_alone"] for d in after["decks"]) and all(d["independent"] for d in after["decks"])
    assert whole(alice, "contested")[0] == [] and whole(alice, "purchases")[0] == []
    assert after["summary"]["decks_needing_purchase"] == 0 and after["summary"]["finish_all_cost"] == 0
    assert len(ids) == 3


def test_three_decks_needing_one_card_with_one_owned(alice, app):
    own(app, {"Scarce Card": 1})
    set_prices(app, {"Scarce Card": 5.0})
    a, b, c = (save(alice, n, "1 Scarce Card") for n in ("A", "B", "C"))
    for order in (f"{a},{b},{c}", f"{c},{b},{a}"):  # whatever the order, the shares add up to the deficit times the price
        body = root(alice, priority=order)
        decks = body["decks"]
        assert [d["stands_alone"] for d in decks] == [True, False, False]
        assert [lacking(d).get("Scarce Card", {}).get("held_by_other_deck") for d in decks] == [None, 1, 1]
        assert sum(d["cost_to_complete"] for d in decks) == 10.0
        [purchase] = whole(alice, "purchases", priority=order)[0]
        assert purchase["global_deficit"] == 2 and purchase["cost"] == 10.0 and purchase["unit_price"] == 5.0
        assert body["summary"]["finish_all_cost"] == 10.0 and body["summary"]["decks_needing_purchase"] == 2
    own(app, {"Scarce Card": 1}, set_code="xyz")  # one purchase completes only the next deck; the card stays contested
    after = root(alice, priority=f"{a},{b},{c}")["decks"]
    assert [d["stands_alone"] for d in after] == [True, True, False]
    [still] = whole(alice, "contested", priority=f"{a},{b},{c}")[0]
    assert still["global_deficit"] == 1


def test_a_deck_needing_two_and_a_deck_needing_one_with_two_owned(alice, app):
    own(app, {"Pair Card": 2})
    set_prices(app, {"Pair Card": 3.0})
    a = save(alice, "A", "2 Pair Card")
    b = save(alice, "B", "1 Pair Card")
    first = root(alice, priority=f"{a},{b}")
    da, db_ = first["decks"]
    assert holds(da)["Pair Card"]["quantity"] == 2 and da["stands_alone"] is True and da["independent"] is False  # holds two contested copies
    assert lacking(db_)["Pair Card"]["quantity"] == 1 and db_["cost_to_complete"] == 3.0
    [c] = whole(alice, "contested", priority=f"{a},{b}")[0]
    assert c["global_deficit"] == 1 and by_name(c["options"], "kind")["buy"]["cost"] == 3.0
    second = root(alice, priority=f"{b},{a}")["decks"]  # the other order: B first, A short by one copy it can take by moving
    assert [d["name"] for d in second] == ["B", "A"]
    assert lacking(second[1])["Pair Card"]["held_by_other_deck"] == 1 and sum(d["cost_to_complete"] for d in second) == 3.0
    own(app, {"Pair Card": 1}, set_code="xyz")  # one purchase finishes everything
    assert all(d["stands_alone"] for d in root(alice, priority=f"{a},{b}")["decks"])


def test_a_card_owned_in_enough_copies_for_every_deck_is_free(alice, app):
    own(app, {"Sol Ring": 31})
    for n in range(4):
        save(alice, f"Deck {n}", "1 Sol Ring")
    body = root(alice)
    assert all(d["holds"] == [] and d["lacking"] == [] and d["independent"] for d in body["decks"])
    assert whole(alice, "contested")[0] == []
    ring = by_name(body["cards"])["Sol Ring"]
    assert (ring["need_for_all"], ring["have"], ring["short"], ring["decks_total"]) == (4, 31, 0, 4)


def test_a_card_owned_zero_times_is_not_contested_and_every_copy_lacking_is_not_owned(alice, app):
    own(app, {"Elsewhere": 0})
    a, b = save(alice, "A", "1 Ghost Card"), save(alice, "B", "1 Ghost Card")
    decks = root(alice)["decks"]
    for d in decks:
        assert d["holds"] == [] and d["stands_alone"] is False
        assert (lacking(d)["Ghost Card"]["quantity"], lacking(d)["Ghost Card"]["not_owned"], lacking(d)["Ghost Card"]["held_by_other_deck"]) == (1, 1, 0)
    assert whole(alice, "contested")[0] == []
    [purchase] = whole(alice, "purchases")[0]
    assert purchase["card"] == "Ghost Card" and purchase["global_deficit"] == 2 and purchase["contested"] is False
    assert purchase["move"] is None and purchase["price_status"] == "unpriced" and purchase["cost"] is None
    assert (a, b) and root(alice)["summary"]["unpriced"] == 1


def test_a_mixed_shortage_is_split_into_not_owned_and_held_by_another_deck(alice, app):
    own(app, {"Three Of Them": 3, "One Of Them": 1})
    a = save(alice, "A", "2 Three Of Them\n2 One Of Them")
    b = save(alice, "B", "2 Three Of Them\n2 One Of Them")
    da, db_ = root(alice, priority=f"{a},{b}")["decks"]
    # three owned, two each: A takes two; B holds one and lacks one, which exists but is held by A (available by moving)
    assert holds(da)["Three Of Them"]["quantity"] == 2 and "Three Of Them" not in lacking(da)
    assert holds(db_)["Three Of Them"]["quantity"] == 1
    assert {k: lacking(db_)["Three Of Them"][k] for k in ("quantity", "not_owned", "held_by_other_deck")} == {
        "quantity": 1, "not_owned": 0, "held_by_other_deck": 1}
    # one owned, two each: A gets it and still lacks one (not owned); B gets none and lacks two (one not owned, one held by A)
    assert holds(da)["One Of Them"]["quantity"] == 1
    assert {k: lacking(da)["One Of Them"][k] for k in ("quantity", "not_owned", "held_by_other_deck")} == {
        "quantity": 1, "not_owned": 1, "held_by_other_deck": 0}
    assert "One Of Them" not in holds(db_)
    assert {k: lacking(db_)["One Of Them"][k] for k in ("quantity", "not_owned", "held_by_other_deck")} == {
        "quantity": 2, "not_owned": 1, "held_by_other_deck": 1}
    deficits = {c["card"]: c["global_deficit"] for c in whole(alice, "contested", priority=f"{a},{b}")[0]}
    assert deficits == {"Three Of Them": 1, "One Of Them": 3}  # the decks' lacking copies add up to the deficit
    for card_name, deficit in deficits.items():
        assert sum(lacking(d).get(card_name, {}).get("quantity", 0) for d in (da, db_)) == deficit


def test_basic_lands_never_appear_and_a_deck_of_only_basics_is_independent(alice, app):
    own(app, {"Forest": 1})
    save(alice, "Lands", "20 Forest\n10 Island")
    save(alice, "Also lands", "5 Forest")
    body = root(alice)
    assert body["shared_cards"] == 0 and body["cards"] == []
    for d in body["decks"]:
        assert (d["need"], d["free"], d["independence"]) == (0, 0, 1.0)
        assert d["stands_alone"] is True and d["independent"] is True and d["holds"] == [] and d["lacking"] == []
    assert whole(alice, "contested")[0] == [] and whole(alice, "purchases")[0] == []


def test_printing_specific_decks_are_counted_by_name(alice, app):
    own(app, {"Printed Card": 1}, set_code="aaa")
    a, b = save(alice, "A", "1 Printed Card (bbb) 5"), save(alice, "B", "1 Printed Card (ccc) 7")  # neither names the printing owned
    assert [c["card"] for c in whole(alice, "contested", priority=f"{a},{b}")[0]] == ["Printed Card"]
    own(app, {"Printed Card": 1}, set_code="ddd")  # a second printing: any printing owned counts
    assert whole(alice, "contested")[0] == [] and all(d["stands_alone"] for d in root(alice)["decks"])


def test_a_fully_independent_set_of_decks(alice, app):
    own(app, {"Shared Rock": 2, "Own Card One": 1, "Own Card Two": 1})
    save(alice, "A", "1 Shared Rock\n1 Own Card One")
    save(alice, "B", "1 Shared Rock\n1 Own Card Two")
    body = root(alice)
    assert all(d["stands_alone"] and d["independent"] and d["independence"] == 1.0 for d in body["decks"])
    assert whole(alice, "contested")[0] == []
    assert body["summary"] == {"decks_analysed": 2, "decks_needing_purchase": 0, "finish_all_cost": 0, "unpriced": 0,
                               "contested_cards": 0, "cards_to_buy": 0}


# -- the order -----------------------------------------------------------------------------------------------------------

def stamp(app, deck_id, when):
    with app.state.db.sessions() as db:
        db.execute(update(Deck).where(Deck.id == deck_id).values(updated_at=when))
        db.commit()


def test_the_default_order_finishes_the_deck_closest_to_complete_first(alice, app):
    own(app, {"Z": 1})
    many = save(alice, "Aaa many missing", "1 Z\n1 Nope1\n1 Nope2")
    mid = save(alice, "Bbb one missing", "1 Z\n1 Nope3")
    few = save(alice, "Ccc complete", "1 Z")
    body = root(alice)
    assert [d["name"] for d in body["decks"]] == ["Ccc complete", "Bbb one missing", "Aaa many missing"]
    assert [d["order"] for d in body["decks"]] == [1, 2, 3]
    assert body["allocation"]["rule"] == "closest_to_complete" and body["allocation"]["priority_applied"] is False
    assert body["decks"][0]["stands_alone"] is True and holds(body["decks"][0])["Z"]["quantity"] == 1
    # priority overrides it; the decks it does not name follow in the default order
    over = root(alice, priority=f"{many}")
    assert [d["name"] for d in over["decks"]] == ["Aaa many missing", "Ccc complete", "Bbb one missing"]
    assert over["allocation"]["rule"] == "priority" and over["allocation"]["priority_applied"] is True
    assert holds(over["decks"][0])["Z"]["quantity"] == 1 and "Z" in lacking(over["decks"][1])
    repeated = root(alice, priority=[str(mid), str(few)])
    assert [d["name"] for d in repeated["decks"]][:2] == ["Bbb one missing", "Ccc complete"]


def test_equal_decks_are_ordered_by_most_recently_edited_then_name(alice, app):
    own(app, {"Y": 1})
    old = save(alice, "Old", "1 Y")
    new = save(alice, "New", "1 Y")
    same_b = save(alice, "Bravo", "1 Y")
    same_a = save(alice, "Alpha", "1 Y")
    now = datetime.now(timezone.utc)
    stamp(app, old, now - timedelta(days=3))
    stamp(app, new, now)
    stamp(app, same_b, now - timedelta(days=1))
    stamp(app, same_a, now - timedelta(days=1))
    assert [d["name"] for d in root(alice)["decks"]] == ["New", "Alpha", "Bravo", "Old"]


def test_priority_naming_a_deck_that_is_not_yours_is_a_404_that_reveals_nothing(alice, bob, app):
    own(app, {"Rare Card": 1})
    mine = save(alice, "Mine", "1 Rare Card")
    theirs = save(bob, "Bob's secret deck", "1 Rare Card")
    missing = 987654321
    other = alice.get(OVERLAP, params={"priority": f"{mine},{theirs}"})
    gone = alice.get(OVERLAP, params={"priority": f"{mine},{missing}"})
    assert other.status_code == gone.status_code == 404
    assert other.json() == gone.json() and "secret" not in other.text
    for sub in ("decks", "contested", "purchases"):
        assert alice.get(f"{OVERLAP}/{sub}", params={"priority": str(theirs)}).status_code == 404
    for bad in ("abc", "1,,x", "-4", "9" * 20):
        assert alice.get(OVERLAP, params={"priority": bad}).status_code == 400, bad
    assert alice.get(OVERLAP, params={"priority": str(mine)}).status_code == 200


# -- paging --------------------------------------------------------------------------------------------------------------

def test_decks_page_with_the_same_answers_as_one_page(alice, app):
    own(app, {"Shared": 2})
    for n in range(5):
        save(alice, f"Deck {n}", f"1 Shared\n1 Unowned {n}")
    one = whole(alice, "decks", limit=500)[0]
    paged, pages = whole(alice, "decks", limit=2)
    assert pages == 3 and paged == one and [d["order"] for d in paged] == [1, 2, 3, 4, 5]
    assert sum(1 for d in paged if holds(d).get("Shared")) == 2  # the page size never changes who gets the copies
    first = root(alice, limit=2)
    assert len(first["decks"]) == 2 and first["decks_analysed"] == 5 and "next" in first["_links"]
    assert {"decks", "contested", "purchases", "self"} <= set(first["_links"])
    assert first["decks"] == one[:2]
    assert alice.get(first["_links"]["next"]["href"]).json()["items"] == one[2:4]
    assert "next" not in root(alice)["_links"] and len(root(alice)["decks"]) == 5


def test_contested_and_purchases_page_on_their_own_cursors(alice, app):
    names = [f"Card {n}" for n in range(1, 6)]
    own(app, {n: 1 for n in names})
    set_prices(app, {"Card 1": 1.0, "Card 2": 4.0, "Card 3": 2.0, "Card 4": 4.0, "Card 5": None, "Ghost A": 0.5, "Ghost B": None})
    text = "\n".join(f"1 {n}" for n in names) + "\n1 Ghost A\n1 Ghost B"
    a, b = save(alice, "A", text), save(alice, "B", text)
    contested, pages = whole(alice, "contested", limit=2)
    assert pages == 3 and contested == whole(alice, "contested", limit=500)[0] and len(contested) == 5
    purchases, pages = whole(alice, "purchases", limit=2)
    assert pages == 4 and purchases == whole(alice, "purchases", limit=500)[0]
    # cheapest first (by the cost of the whole deficit), ties by name, unpriced last
    assert [p["card"] for p in purchases] == ["Card 1", "Ghost A", "Card 3", "Card 2", "Card 4", "Card 5", "Ghost B"]  # $1, $1, $2, $4, $4
    assert [p["contested"] for p in purchases] == [True, False, True, True, True, True, False]
    assert all((p["move"] is not None) == p["contested"] for p in purchases)
    one = alice.get(f"{OVERLAP}/purchases", params={"limit": 2, "priority": f"{b},{a}"})
    assert "priority=" in one.json()["_links"]["next"]["href"]  # the next page keeps the order the caller asked for
    assert alice.get(f"{OVERLAP}/decks", params={"cursor": "not-a-cursor"}).status_code == 400
    assert alice.get(f"{OVERLAP}/contested", params={"limit": 0}).status_code == 400


def test_purchases_as_paste_ready_text_one_page_at_a_time(alice, app):
    own(app, {"Card 1": 1, "Card 2": 1})
    set_prices(app, {"Card 1": 1.0, "Card 2": 2.0, "Card 3": 3.0})
    text = "1 Card 1\n1 Card 2\n3 Card 3"
    save(alice, "A", text)
    save(alice, "B", text)
    lines, res, pages = [], alice.get(f"{OVERLAP}/purchases", params={"format": "text", "limit": 2}), 0
    while True:
        body = res.json()
        assert body["format"] == "text" and "items" not in body
        lines += body["text"].splitlines()
        pages += 1
        if "next" not in body["_links"]:
            break
        res = alice.get(body["_links"]["next"]["href"])
    assert pages == 2 and lines == ["1 Card 1", "1 Card 2", "6 Card 3"]
    assert alice.get(f"{OVERLAP}/purchases", params={"format": "pdf"}).status_code == 422


# -- unreadable decks, tenancy, the older fields --------------------------------------------------------------------------

def test_an_unreadable_deck_is_skipped_reported_and_counted(alice, app, monkeypatch):
    from vault import deck_independence

    real = deck_independence.deck_text.parse_text

    def parse(text):
        if text.startswith("UNREADABLE"):
            raise ValueError("Exceeds the limit (4300 digits) for integer string conversion")
        return real(text)

    monkeypatch.setattr(deck_independence.deck_text, "parse_text", parse)
    own(app, {"Fine Card": 1})
    good = save(alice, "Good", "1 Fine Card")
    bad = save(alice, "Broken", "1 Fine Card")
    with app.state.db.sessions() as db:
        db.execute(update(Deck).where(Deck.id == bad).values(text="UNREADABLE 1 Fine Card"))
        db.commit()
    body = root(alice)
    assert body["decks_checked"] == 2 and body["decks_analysed"] == 1 and body["decks_skipped_count"] == 1
    assert body["summary"]["decks_analysed"] == 1
    skipped = next(d for d in body["decks"] if d["id"] == bad)
    assert skipped["status"] == "skipped" and skipped["reason"] and skipped["stands_alone"] is None
    assert skipped["_links"]["self"]["href"] == f"{V1}/decks/{bad}"
    assert next(d for d in body["decks"] if d["id"] == good)["status"] == "analysed"
    assert [d["id"] for d in whole(alice, "decks")[0]] == [good, bad]  # skipped decks come last
    assert whole(alice, "contested")[0] == []  # a deck that cannot be read does not make a card contested


def test_another_persons_decks_and_collection_never_appear(alice, bob, app):
    own(app, {"Rare Card": 1})
    own(app, {"Rare Card": 10, "Bob only": 4}, email="bob@example.com")
    a, b = save(alice, "A", "1 Rare Card"), save(alice, "B", "1 Rare Card")
    save(bob, "Bobs deck", "1 Rare Card\n1 Bob only")
    save(bob, "Bobs other deck", "1 Rare Card")
    responses = [alice.get(OVERLAP).text, alice.get(f"{OVERLAP}/decks").text, alice.get(f"{OVERLAP}/contested").text,
                 alice.get(f"{OVERLAP}/purchases").text, alice.get(f"{OVERLAP}/purchases", params={"format": "text"}).text]
    assert not [r for r in responses if "Bob" in r]  # not his decks, his cards or his names
    [c] = whole(alice, "contested")[0]
    assert (c["have"], c["need_for_all"]) == (1, 2)  # his ten copies are not hers
    mine = root(alice)
    assert mine["decks_checked"] == 2 and {d["id"] for d in mine["decks"]} == {a, b}
    assert root(bob)["decks_checked"] == 2 and all(d["stands_alone"] for d in root(bob)["decks"][:2])


def test_the_older_fields_are_unchanged_and_the_root_stays_bounded(alice, app):
    own(app, {"Shared Card": 3})
    for n in range(12):
        save(alice, f"Deck {n:02}", "1 Shared Card\n10 Forest")
    save(alice, "Solo", "1 Only Here")
    body = root(alice)
    assert {"decks_checked", "shared_cards", "short_cards", "cards", "note"} <= set(body)
    assert body["decks_checked"] == 13 and body["shared_cards"] == 1 and body["short_cards"] == 1
    assert body["note"].startswith("Basic lands are left out. 'short' is how many more copies you need")
    [shared] = body["cards"]
    assert (shared["name"], shared["need_for_all"], shared["have"], shared["short"]) == ("Shared Card", 12, 3, 9)
    assert len(shared["decks"]) == 10 and shared["decks_total"] == 12  # a preview, not every deck
    assert set(shared["decks"][0]) == {"deck_id", "deck", "quantity"}
    assert len(body["decks"]) == 13 <= 25  # the first page
    assert len(root(alice, limit=3)["decks"]) == 3


# -- access, rate limit, schema, tools --------------------------------------------------------------------------------------

def test_anonymous_callers_are_refused(client):
    for path in ("", "/decks", "/contested", "/purchases"):
        assert client.get(OVERLAP + path).status_code == 401, path


def test_overlap_is_limited_per_person(alice):
    codes = [alice.get(OVERLAP).status_code for _ in range(62)]
    assert codes[:60] == [200] * 60 and codes[60:] == [429, 429]


def test_the_endpoints_have_response_models_in_the_openapi_document(app):
    paths = app.openapi()["paths"]
    root_schema = paths[OVERLAP]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert root_schema["$ref"].endswith("/DeckOverlap")
    for sub in ("decks", "contested", "purchases"):
        assert "$ref" in paths[f"{OVERLAP}/{sub}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] or \
               "anyOf" in paths[f"{OVERLAP}/{sub}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    params = {p["name"] for p in paths[OVERLAP]["get"]["parameters"]}
    assert {"priority", "limit"} <= params


def test_a_read_only_token_reads_the_overlap_over_rest(alice, bot, app):
    own(app, {"Rare Card": 1})
    save(alice, "A", "1 Rare Card")
    save(alice, "B", "1 Rare Card")
    h = auth(make_token(alice))
    for path in ("", "/decks", "/contested", "/purchases"):
        assert bot.get(OVERLAP + path, headers=h).status_code == 200, path


def test_get_deck_overlap_carries_scryfall_provenance_and_the_price_date(alice, bot, app):
    from vault.api import mcp

    own(app, {"Rare Card": 1})
    set_prices(app, {"Rare Card": 2.5})
    save(alice, "A", "1 Rare Card")
    save(alice, "B", "1 Rare Card")
    read = make_token(alice)  # a read-only token is enough
    answer = call_tool(bot, read, "get_deck_overlap")
    assert not answer["isError"], answer
    body = answer["structuredContent"]
    [block] = body["provenance"]
    assert block["source"] == "Scryfall" and block["as_of"] == "2026-10-04" and body["prices_date"] == "2026-10-04"
    assert block["notice"]  # the Fan Content notice
    assert "get_deck_overlap" in mcp.SCRYFALL_DATA and "get_deck_overlap" not in mcp.OWN_DATA_ONLY
    tool = mcp.BY_NAME["get_deck_overlap"]
    assert "contested" in tool.description and "not_owned" in tool.description and tool.write is False
    assert tool.schema()["annotations"]["readOnlyHint"] is True


def test_no_tool_that_returns_prices_is_classified_as_own_data_only():
    from vault.api import mcp

    words = ("price", "cost", "worth", "value", "$")
    offenders = [t.name for t in mcp.TOOLS if t.name in mcp.OWN_DATA_ONLY and any(w in t.description.lower() for w in words)]
    assert offenders == [], f"these tools describe prices or costs but are classified own-data-only (they are Scryfall data): {offenders}"


def test_the_tool_pages_every_list_the_endpoint_does(alice, bot, app):
    own(app, {"Card 1": 1, "Card 2": 1, "Card 3": 1})
    set_prices(app, {"Card 1": 1.0, "Card 2": 2.0, "Card 3": 3.0, "Ghost": 0.5})
    text = "1 Card 1\n1 Card 2\n1 Card 3\n1 Ghost"
    a, b = save(alice, "A", text), save(alice, "B", text)
    read = make_token(alice)

    def paged(**args):
        items, cursor = [], None
        while True:
            body = call_tool(bot, read, "get_deck_overlap", **args, **({"cursor": cursor} if cursor else {}))["structuredContent"]
            items += body["items"]
            cursor = body.get("next_cursor")
            if not cursor:
                return items

    assert [d["name"] for d in paged(list="decks", limit=1)] == ["B", "A"]  # equal decks: the one edited last first
    assert [c["card"] for c in paged(list="contested", limit=2, priority=[b, a])] == ["Card 1", "Card 2", "Card 3"]
    assert [p["card"] for p in paged(list="purchases", limit=2)] == ["Card 1", "Ghost", "Card 2", "Card 3"]
    overview = call_tool(bot, read, "get_deck_overlap", priority=[b, a])["structuredContent"]
    assert [d["name"] for d in overview["decks"]] == ["B", "A"] and overview["allocation"]["rule"] == "priority"
    # format text is the paste-ready purchase list, one page per call
    first = call_tool(bot, read, "get_deck_overlap", format="text", limit=3)["structuredContent"]
    assert first["format"] == "text" and first["text"].splitlines() == ["1 Card 1", "2 Ghost", "1 Card 2"] and first["next_cursor"]
    rest = call_tool(bot, read, "get_deck_overlap", format="text", limit=3, cursor=first["next_cursor"])["structuredContent"]
    assert rest["text"] == "1 Card 3" and "next_cursor" not in rest
    from vault.api.mcp import _invalid, BY_NAME  # the input schema refuses what the endpoint would
    assert _invalid(BY_NAME["get_deck_overlap"].schema()["inputSchema"], {"list": "nonsense"}, "arguments")
    assert _invalid(BY_NAME["get_deck_overlap"].schema()["inputSchema"], {"priority": ["x"]}, "arguments")
