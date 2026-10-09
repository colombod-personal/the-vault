"""The `bucket` and `tag` filters on the collection's analytics (#130, docs/collections.md section 5), on /api/v1 and the MCP tools.

The data: Sol Ring (printing C21) is a stack split across two buckets (3 copies in Binder, 2 in Trade box), a second Sol Ring
printing and an unmatched card sit in Trade box, Mountain in Binder. Nothing here needs a price snapshot: the file's prices are used,
so every figure is known in advance.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tests.ids import sid
from tests.test_agents import V1, bot, call_tool, make_token, rpc  # noqa: F401 - fixtures
from vault.api import mcp
from vault.models import Card, CollectionValue, PriceSnapshot, User

COL = "/api/v1/collection"
SOL, MOUNTAIN = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000003"
HEADER = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
          "Price Bought,Date Bought,LOW,MID,MARKET\n")
CSV = (HEADER
       + "Binder,3,0,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,1.00,2024-02-17,1.00,2.00,2.30\n"
       + "Binder,4,0,Mountain,C17,Commander 2017,304,Mint,Normal,English,0.05,2024-03-01,0.05,0.05,0.05\n"
       + "Trade box,2,0,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,2.00,2024-02-17,1.00,2.00,2.30\n"
       + "Trade box,1,0,Sol Ring,CMM,Commander Masters,400,Mint,Normal,English,1.00,2024-02-18,1.00,2.00,2.30\n"
       + "Trade box,1,0,Unknown Thing,XXX,Nowhere,1,Mint,Normal,English,0.05,2024-02-18,0.05,0.05,0.05\n").encode()
WHOLE = {"copies": 11, "market": 14.05, "paid": 8.25}
ANALYTICS = ["", "/stats", "/sets", "/timeline", "/history", "/breakdowns", "/valuation", "/names"]


def sign_in_as(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def put_cards(app):
    with app.state.db.sessions() as db:
        db.add_all([Card(scryfall_id=sid("sol-c21"), oracle_id=SOL, name="Sol Ring", set_code="c21", collector_number="263"),
                    Card(scryfall_id=sid("sol-cmm"), oracle_id=SOL, name="Sol Ring", set_code="cmm", collector_number="400"),
                    Card(scryfall_id=sid("mountain"), oracle_id=MOUNTAIN, name="Mountain", set_code="c17", collector_number="304")])
        db.commit()


@pytest.fixture
def stocked(app, signed_in):
    put_cards(app)
    assert signed_in.post(f"{V1}/imports", files={"file": ("c.csv", CSV, "text/csv")}).status_code == 201
    return signed_in


def bucket_ids(client):
    return {b["name"]: b["id"] for b in client.get(f"{COL}/buckets").json()["items"]}


def card_ids(client):
    out = {}
    for c in client.get(f"{COL}/cards", params={"limit": 100}).json()["items"]:
        out.setdefault(c["name"], []).append(c["id"])
    return out


def tag(client, name, *cards):
    assert client.post(f"{COL}/tags/{name}/cards", json={"card_ids": list(cards)}).status_code == 200


def figures(client, **scope):
    """Every additive and distinct figure the analytics endpoints give, for one selection."""
    def get(path, **extra):
        res = client.get(f"{COL}{path}", params={**scope, **extra})
        assert res.status_code == 200, (path, scope, res.text)
        return res.json()

    summary, breakdowns, valuation = get(""), get("/breakdowns"), get("/valuation")
    names, sets, timeline = get("/names", limit=100), get("/sets", limit=100), get("/timeline")
    return {
        "copies": summary["copies"], "market": summary["market_value"], "paid": summary["paid"],
        "printings": summary["printings"], "cards": summary["cards"], "sets": summary["sets"],
        "pnl_paid": summary["known_cost_paid"],
        "b_copies": breakdowns["totals"]["copies"], "b_market": breakdowns["totals"]["market_value"],
        "b_printings": breakdowns["totals"]["printings"],
        "v_copies": valuation["totals"]["copies"], "v_market": valuation["totals"]["market"], "v_cost": valuation["totals"]["cost"],
        "n_copies": sum(n["copies"] for n in names["items"]), "n_market": sum(n["market_value"] for n in names["items"]),
        "n_names": names["total"],
        "s_copies": sum(s["copies"] for s in sets["items"]), "s_market": sum(s["market_value"] for s in sets["items"]),
        "s_sets": sets["total"],
        "t_copies": sum(m["copies"] for m in timeline["months"]), "t_paid": sum(m["paid"] for m in timeline["months"]),
    }


ADDITIVE = ["copies", "market", "paid", "pnl_paid", "b_copies", "b_market", "v_copies", "v_market", "v_cost", "n_copies",
            "n_market", "s_copies", "s_market", "t_copies", "t_paid"]
DISTINCT = ["printings", "cards", "sets", "b_printings", "n_names", "s_sets"]


def test_the_figures_of_all_buckets_add_up_to_the_whole_inventory_on_every_endpoint(stocked):
    ids = bucket_ids(stocked)
    assert set(ids) == {"Binder", "Trade box"}
    whole = figures(stocked)
    assert whole["copies"] == WHOLE["copies"] and whole["market"] == pytest.approx(WHOLE["market"])
    assert whole["paid"] == pytest.approx(WHOLE["paid"])
    parts = [figures(stocked, bucket=i) for i in ids.values()]
    for key in ADDITIVE:
        assert sum(p[key] for p in parts) == pytest.approx(whole[key]), key
    # a bucket is a real restriction, not a no-op
    assert [p["copies"] for p in parts] == [7, 4] or [p["copies"] for p in parts] == [4, 7]
    assert figures(stocked, bucket=ids["Binder"])["copies"] == 7 and figures(stocked, bucket=ids["Trade box"])["copies"] == 4


def test_a_stack_split_across_two_buckets_counts_in_each_but_the_distinct_counts_do_not_add_up(stocked):
    ids = bucket_ids(stocked)
    whole = figures(stocked)
    binder, trade = figures(stocked, bucket=ids["Binder"]), figures(stocked, bucket=ids["Trade box"])
    # Sol Ring (C21) is in both buckets: 3 + 2 copies, one printing, one card
    assert (binder["printings"], trade["printings"], whole["printings"]) == (2, 3, 4)
    assert binder["printings"] + trade["printings"] > whole["printings"]  # the split stack counts once in each
    assert (binder["cards"], trade["cards"], whole["cards"]) == (2, 2, 3)
    assert (binder["sets"], trade["sets"], whole["sets"]) == (2, 3, 4)  # c21 is in both buckets
    assert (binder["n_names"], trade["n_names"], whole["n_names"]) == (2, 2, 3)
    for key in DISTINCT:  # computed over the combined inventory, never forced to add up
        assert whole[key] <= binder[key] + trade[key], key
    # the split stack's copies do add up: 3 in one bucket, 2 in the other, 5 inventory-wide
    sol = lambda b: next(n for n in stocked.get(f"{COL}/names", params={"bucket": b}).json()["items"] if n["name"] == "Sol Ring")  # noqa: E731
    assert sol(ids["Binder"])["copies"] == 3 and sol(ids["Trade box"])["copies"] == 3  # Trade box: 2 of C21 and 1 of CMM
    assert next(n for n in stocked.get(f"{COL}/names").json()["items"] if n["name"] == "Sol Ring")["copies"] == 6
    top = stocked.get(f"{COL}/stats", params={"bucket": ids["Binder"]}).json()["most_copies"][0]
    assert (top["name"], top["copies"]) == ("Mountain", 4)


def test_a_tag_filters_every_endpoint_to_the_tagged_cards_every_printing_counted_once(stocked):
    ids = card_ids(stocked)
    tag(stocked, "staple", ids["Sol Ring"][0])  # tagging one printing tags the card: both printings count
    tag(stocked, "trade", ids["Sol Ring"][0], ids["Mountain"][0])  # Sol Ring now has two tags
    staple, trade = figures(stocked, tag="staple"), figures(stocked, tag="trade")
    assert staple["copies"] == 6 and staple["printings"] == 2 and staple["cards"] == 1 and staple["market"] == pytest.approx(13.8)
    assert trade["copies"] == 10 and trade["printings"] == 3 and trade["cards"] == 2  # not counted again for its second tag
    for key in ("copies", "b_copies", "v_copies", "n_copies", "s_copies", "t_copies"):
        assert staple[key] == 6 and trade[key] == 10, key
    # the same copies /collection/cards lists for the tag
    listed = stocked.get(f"{COL}/cards", params={"tag": "trade", "limit": 100}).json()
    assert sum(c["quantity"] for c in listed["items"]) == trade["copies"] and listed["value_total"] == pytest.approx(trade["market"])
    # tag and bucket together: the tagged cards in one bucket
    binder = bucket_ids(stocked)["Binder"]
    both = figures(stocked, tag="trade", bucket=binder)
    assert both["copies"] == 7 and both["printings"] == 2  # Sol Ring (3) and Mountain (4), but not Trade box's Sol Ring copies
    assert figures(stocked, tag="staple", bucket=binder)["copies"] == 3
    # breakdowns of a tag: the cards Mountain/Sol Ring only
    assert stocked.get(f"{COL}/stats", params={"tag": "staple"}).json()["most_copies"][0]["name"] == "Sol Ring"


def test_an_unknown_tag_is_an_empty_answer_a_malformed_one_is_400_and_an_unknown_bucket_is_404(stocked):
    empty = figures(stocked, tag="nothing")
    assert all(empty[k] == 0 for k in ("copies", "market", "paid", "printings", "b_copies", "v_copies", "n_copies", "n_names", "s_sets"))
    for path in ANALYTICS:
        assert stocked.get(f"{COL}{path}", params={"tag": "Bad Tag!"}).status_code == 400, path
        assert stocked.get(f"{COL}{path}", params={"bucket": 999999}).status_code == 404, path
        assert stocked.get(f"{COL}{path}", params={"tag": "nothing"}).status_code == 200, path
    history = stocked.get(f"{COL}/history", params={"tag": "nothing"}).json()
    assert history["total"] >= 1 and all(d["copies"] == 0 and d["market"] == 0 for d in history["items"])


def test_the_links_of_a_filtered_answer_keep_the_selection(stocked):
    binder = bucket_ids(stocked)["Binder"]
    links = stocked.get(COL, params={"bucket": binder, "tag": "x"}).json()["_links"]
    assert links["breakdowns"]["href"].endswith(f"/breakdowns?bucket={binder}&tag=x")
    assert links["most_valuable"]["href"].endswith(f"/cards?sort=-value&bucket={binder}&tag=x")
    plain = stocked.get(COL).json()["_links"]
    assert plain["breakdowns"]["href"].endswith("/collection/breakdowns") and plain["self"]["href"].endswith("/collection")
    assert stocked.get(f"{COL}/valuation", params={"bucket": binder}).json()["_links"]["history"]["href"].endswith(f"/history?bucket={binder}")


def test_another_persons_bucket_is_404_on_every_analytics_endpoint(client):
    sign_in_as(client, "bob@example.com")
    bob = client.post(f"{COL}/buckets", json={"name": "Bob's box"}).json()["id"]
    sign_in_as(client, "alice@example.com")
    for path in ANALYTICS:
        assert client.get(f"{COL}{path}", params={"bucket": bob}).status_code == 404, path
    sign_in_as(client, "bob@example.com")
    assert all(client.get(f"{COL}{path}", params={"bucket": bob}).status_code == 200 for path in ANALYTICS)


def test_a_shared_collection_refuses_the_filters_on_every_analytics_endpoint(app, client):
    sign_in_as(client, "alice@example.com")
    put_cards(app)
    assert client.post(f"{V1}/imports", files={"file": ("c.csv", CSV, "text/csv")}).status_code == 201
    binder = bucket_ids(client)["Binder"]
    token = client.post(f"{V1}/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    sign_in_as(client, "bob@example.com")
    shared = f"{V1}/shared/{client.post(f'{V1}/shares/accept', json={'token': token}).json()['id']}/collection"
    for path in ANALYTICS:
        assert client.get(f"{shared}{path}").status_code == 200, path
        assert client.get(f"{shared}{path}", params={"bucket": binder}).status_code == 404, path  # the owner's own grouping
        assert client.get(f"{shared}{path}", params={"tag": "x"}).status_code == 404, path
    assert client.get(shared).json()["copies"] == WHOLE["copies"]


def test_changing_a_persons_tags_changes_the_etag_and_never_serves_a_stale_answer(stocked):
    ids = card_ids(stocked)
    tag(stocked, "trade", ids["Mountain"][0])
    for path in ("", "/breakdowns", "/valuation", "/names", "/stats", "/sets", "/timeline", "/history"):
        first = stocked.get(f"{COL}{path}", params={"tag": "trade"})
        etag = first.headers["etag"]
        assert stocked.get(f"{COL}{path}", params={"tag": "trade"}, headers={"If-None-Match": etag}).status_code == 304, path
        assert stocked.get(f"{COL}{path}", params={"tag": "staple"}).headers["etag"] != etag, path  # another filter, another ETag
    before = {path: stocked.get(f"{COL}{path}", params={"tag": "trade"}).headers["etag"] for path in ANALYTICS}
    assert stocked.get(COL, params={"tag": "trade"}).json()["copies"] == 4
    tag(stocked, "trade", ids["Sol Ring"][0])  # the person's tags change
    for path in ANALYTICS:
        res = stocked.get(f"{COL}{path}", params={"tag": "trade"}, headers={"If-None-Match": before[path]})
        assert res.status_code == 200 and res.headers["etag"] != before[path], path
    assert stocked.get(COL, params={"tag": "trade"}).json()["copies"] == 10  # the cached view of the old tag is not served
    assert stocked.post(f"{COL}/tags/trade/cards/remove", json={"card_ids": ids["Mountain"]}).status_code == 200
    assert stocked.get(COL, params={"tag": "trade"}).json()["copies"] == 6
    assert stocked.patch(f"{COL}/tags/trade", json={"name": "keep"}).status_code == 200  # a rename
    assert stocked.get(COL, params={"tag": "trade"}).json()["copies"] == 0
    assert stocked.get(COL, params={"tag": "keep"}).json()["copies"] == 6


def test_the_bucket_etag_changes_when_copies_move_between_buckets(stocked):
    ids = bucket_ids(stocked)
    first = stocked.get(f"{COL}/breakdowns", params={"bucket": ids["Trade box"]})
    assert stocked.get(f"{COL}/breakdowns", params={"bucket": ids["Binder"]}).headers["etag"] != first.headers["etag"]
    sol = next(c for c in stocked.get(f"{COL}/cards", params={"bucket": ids["Trade box"]}).json()["items"] if c["name"] == "Unknown Thing")
    moved = stocked.post(f"{COL}/buckets/{ids['Trade box']}/move", json={"to": ids["Binder"], "lines": [{"card_id": sol["id"], "quantity": 1}]})
    assert moved.status_code == 200, moved.text
    after = stocked.get(f"{COL}/breakdowns", params={"bucket": ids["Trade box"]}, headers={"If-None-Match": first.headers["etag"]})
    assert after.status_code == 200 and after.json()["totals"]["copies"] == 3
    assert stocked.get(COL, params={"bucket": ids["Binder"]}).json()["copies"] == 8


def test_a_filtered_value_history_prices_the_selections_copies_at_each_days_prices(app, stocked):
    today, earlier, before = date.today(), date.today() - timedelta(days=10), date.today() - timedelta(days=20)
    with app.state.db.sessions() as db:
        user_id = db.scalar(select(User.id))
        db.add_all([PriceSnapshot(scryfall_id=sid("sol-c21"), day=earlier, usd_cents=1000),
                    CollectionValue(user_id=user_id, day=earlier, market_usd=0, cost_usd=0, copies=1, priced_copies=0),
                    CollectionValue(user_id=user_id, day=before, market_usd=0, cost_usd=0, copies=1, priced_copies=0)])
        db.commit()
    ids, cards = bucket_ids(stocked), card_ids(stocked)
    tag(stocked, "staple", cards["Sol Ring"][0])
    rows = lambda **scope: {d["day"]: d for d in stocked.get(f"{COL}/history", params=scope).json()["items"]}  # noqa: E731
    staple = rows(tag="staple")
    # before the snapshot: the file's price (2.30) for the 6 Sol Rings; after: $10 for the 5 C21 copies, 2.30 for the CMM one
    assert staple[before.isoformat()]["market"] == pytest.approx(13.8) and staple[before.isoformat()]["copies"] == 6
    assert staple[earlier.isoformat()]["market"] == pytest.approx(52.3) and staple[earlier.isoformat()]["priced"] == 5
    assert staple[earlier.isoformat()]["cost"] == pytest.approx(8.0)  # what was paid for them: 3 + 4 + 1
    # today's day agrees with the summary of the same selection
    summary = stocked.get(COL, params={"tag": "staple"}).json()
    assert staple[today.isoformat()]["market"] == pytest.approx(summary["market_value"]) and staple[today.isoformat()]["copies"] == summary["copies"]
    # the buckets' days add up to the whole inventory's day, and the unfiltered answer is the recorded one
    whole_today = rows()[today.isoformat()]
    per_bucket = [rows(bucket=i)[today.isoformat()] for i in ids.values()]
    assert sum(p["copies"] for p in per_bucket) == whole_today["copies"] == WHOLE["copies"]
    assert sum(p["cost"] for p in per_bucket) == pytest.approx(WHOLE["paid"])
    assert sum(p["market"] for p in per_bucket) == pytest.approx(summary["market_value"] + 0.2 + 0.05)  # Sol Ring + Mountain + unknown
    page = stocked.get(f"{COL}/history", params={"tag": "staple", "limit": 1}).json()
    assert page["count"] == 1 and "tag=staple" in page["_links"]["self"]["href"] and "tag=staple" in page["_links"]["next"]["href"]
    # the summary's two ends are the selection's own (the Lab's market-only summary, docs/lab-design.md), not the whole inventory's
    selected = page["summary"]
    assert selected["from"] == before.isoformat() and selected["to"] == today.isoformat()
    assert selected["market_start"] == pytest.approx(13.8) and selected["market_end"] == pytest.approx(staple[today.isoformat()]["market"])
    assert selected["market_change"] == pytest.approx(round(selected["market_end"] - selected["market_start"], 2))
    whole = stocked.get(f"{COL}/history").json()["summary"]
    assert whole["market_start"] == 0.0 and whole["market_end"] == pytest.approx(whole_today["market"])  # the recorded totals


# -- the MCP tools -----------------------------------------------------------------------------

TOOLS = {  # tool -> the REST path it reads
    "get_collection_summary": "", "get_collection_stats": "/stats", "list_sets": "/sets", "get_collection_breakdowns": "/breakdowns",
    "get_valuation": "/valuation", "list_card_names": "/names", "get_value_history": "/history", "get_acquisition_timeline": "/timeline",
}


def test_every_analytics_tool_advertises_bucket_and_tag_like_search_cards():
    search = mcp.BY_NAME["search_cards"].properties
    for name in TOOLS:
        tool = mcp.BY_NAME[name]
        assert {"bucket", "tag"} <= set(tool.properties) and {"bucket", "tag"} <= set(tool.query), name
        assert tool.properties["bucket"]["type"] == search["bucket"]["type"] and tool.properties["tag"]["pattern"] == search["tag"]["pattern"]
    assert "bucket" not in mcp.BY_NAME["get_card"].properties


def test_the_analytics_tools_pass_bucket_and_tag_to_the_api(stocked, bot):
    token = make_token(stocked)
    ids, cards = bucket_ids(stocked), card_ids(stocked)
    tag(stocked, "trade", cards["Sol Ring"][0], cards["Mountain"][0])
    tag(stocked, "staple", cards["Sol Ring"][0])
    for name, path in TOOLS.items():
        for scope in ({"bucket": ids["Binder"]}, {"tag": "staple"}, {"bucket": ids["Trade box"], "tag": "trade"}):
            args = dict(scope, **({"limit": 100} if name in ("list_sets", "list_card_names") else {}))
            via_tool = call_tool(bot, token, name, **args)
            assert not via_tool.get("isError"), (name, scope, via_tool)
            direct = stocked.get(f"{COL}{path}", params=args).json()
            got = {k: v for k, v in via_tool["structuredContent"].items() if k not in ("_provenance", "provenance")}
            assert {k: got[k] for k in direct if k in got and k != "_links"} == {k: direct[k] for k in direct if k in got and k != "_links"}, (name, scope)
    summary = call_tool(bot, token, "get_collection_summary", tag="staple")["structuredContent"]
    assert summary["copies"] == 6 and summary["market_value"] == pytest.approx(13.8)
    assert call_tool(bot, token, "get_collection_summary", bucket=ids["Binder"])["structuredContent"]["copies"] == 7
    assert call_tool(bot, token, "get_collection_summary")["structuredContent"]["copies"] == WHOLE["copies"]
    assert call_tool(bot, token, "get_valuation", bucket=ids["Trade box"])["structuredContent"]["totals"]["copies"] == 4
    assert call_tool(bot, token, "get_collection_breakdowns", tag="nothing")["structuredContent"]["totals"]["copies"] == 0
    assert call_tool(bot, token, "get_acquisition_timeline", bucket=ids["Binder"])["structuredContent"]["months"][0]["copies"] == 3


def test_the_tools_refuse_a_malformed_tag_another_persons_bucket_and_the_filters_on_a_shared_collection(app, client, bot):
    sign_in_as(client, "bob@example.com")
    bob_bucket = client.post(f"{COL}/buckets", json={"name": "Bob's box"}).json()["id"]
    sign_in_as(client, "alice@example.com")
    put_cards(app)
    client.post(f"{V1}/imports", files={"file": ("c.csv", CSV, "text/csv")})
    token = make_token(client)
    refused = rpc(bot, "tools/call", {"name": "get_valuation", "arguments": {"tag": "Not A Tag"}}, token).json()
    assert "error" in refused and "tag" in refused["error"]["message"]  # the schema stops it before anything is sent
    assert call_tool(bot, token, "get_valuation", bucket=bob_bucket).get("isError") is True  # 404, as on the API
    invite = client.post(f"{V1}/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    sign_in_as(client, "bob@example.com")
    share_id = client.post(f"{V1}/shares/accept", json={"token": invite}).json()["id"]
    bob_token = make_token(client)
    assert not call_tool(bot, bob_token, "get_collection_summary", share_id=share_id).get("isError")
    for name in TOOLS:
        assert call_tool(bot, bob_token, name, share_id=share_id, tag="x").get("isError") is True, name
        assert call_tool(bot, bob_token, name, share_id=share_id, bucket=bob_bucket).get("isError") is True, name
