"""Do the twins still behave like the real services?

The same requests go to the real service and to its twin, and the answers are compared:
- Values that must match exactly: issuers, endpoint URLs, error codes.
- Shapes: every field the twin sends must exist in the real answer, with the same JSON type
  when both are non-null. A twin may leave fields out, but must not invent any.
- Fields the vault or mtg-toolkits read must be present in the real answer.

These tests call the real services, so they are skipped unless ``TWINS_LIVE=1``. They run
nightly (``.github/workflows/twins-conformance.yml``). A failure means a real service changed
(or a twin is wrong): update the twin, then the code that relies on it.
"""

from __future__ import annotations

import os

import httpx
import pytest

from twins import Universe

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("TWINS_LIVE") != "1", reason="set TWINS_LIVE=1 to compare with the real services"),
]

HEADERS = {"User-Agent": "the-vault-conformance/0.1 (+https://github.com/colombod-personal/the-vault)",
           "Accept": "application/json"}


@pytest.fixture(scope="module")
def real():
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        yield c


@pytest.fixture(scope="module")
def twin():
    u = Universe()
    with httpx.Client(transport=u.transport, headers=HEADERS, follow_redirects=True) as c:
        c.universe = u
        yield c


def jtype(v) -> str:
    return {dict: "object", list: "array", str: "string", bool: "boolean", type(None): "null"}.get(
        type(v), "number" if isinstance(v, (int, float)) else type(v).__name__)


def invented(twin_value, real_value, path="$") -> list[str]:
    """Where the twin's JSON has fields or types the real JSON doesn't."""
    problems = []
    if isinstance(twin_value, dict) and isinstance(real_value, dict):
        for key, value in twin_value.items():
            if key not in real_value:
                problems.append(f"{path}.{key}: not in the real answer")
            else:
                problems += invented(value, real_value[key], f"{path}.{key}")
    elif isinstance(twin_value, list) and isinstance(real_value, list):
        if twin_value and real_value:
            problems += invented(twin_value[0], real_value[0], f"{path}[0]")
    elif None not in (twin_value, real_value) and jtype(twin_value) != jtype(real_value):
        problems.append(f"{path}: twin {jtype(twin_value)}, real {jtype(real_value)}")
    return problems


def missing(real_value: dict, required: list[str]) -> list[str]:
    return [k for k in required if k not in real_value]


# -- identity providers -----------------------------------------------------------------------

DISCOVERY = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "microsoft": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
    "apple": "https://appleid.apple.com/.well-known/openid-configuration",
}
EXACT = ["issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"]


@pytest.mark.parametrize("provider", list(DISCOVERY))
def test_discovery_documents(real, twin, provider):
    r, t = real.get(DISCOVERY[provider]).json(), twin.get(DISCOVERY[provider]).json()
    assert {k: t[k] for k in EXACT} == {k: r[k] for k in EXACT}
    assert set(t["id_token_signing_alg_values_supported"]) <= set(r["id_token_signing_alg_values_supported"])
    assert set(t["response_modes_supported"]) <= set(r.get("response_modes_supported", ["query", "fragment"])) | {"form_post"}
    assert invented(t, r) == []


@pytest.mark.parametrize("provider", list(DISCOVERY))
def test_signing_keys(real, twin, provider):
    uri = real.get(DISCOVERY[provider]).json()["jwks_uri"]
    r, t = real.get(uri).json(), twin.get(uri).json()
    assert r["keys"] and all(k["kty"] == "RSA" and "kid" in k for k in r["keys"])
    assert invented(t, r) == []


TOKEN_ERRORS = {
    "google": ("https://oauth2.googleapis.com/token", {"error"}),
    "microsoft": ("https://login.microsoftonline.com/common/oauth2/v2.0/token", {"error", "error_codes"}),
    "apple": ("https://appleid.apple.com/auth/token", {"error"}),
}


@pytest.mark.parametrize("provider", list(TOKEN_ERRORS))
def test_token_endpoint_errors(real, twin, provider):
    url, required = TOKEN_ERRORS[provider]
    form = {"grant_type": "authorization_code", "code": "not-a-code", "client_id": "not-a-client",
            "client_secret": "x", "redirect_uri": "https://example.com/cb"}
    r, t = real.post(url, data=form), twin.post(url, data=form)
    assert r.status_code in (400, 401) and t.status_code in (400, 401)
    assert missing(r.json(), sorted(required)) == [] and invented(t.json(), r.json()) == []


def test_facebook_graph_errors(real, twin):
    url = "https://graph.facebook.com/v23.0/me?fields=id,name,email&access_token=not-a-token"
    r, t = real.get(url), twin.get(url)
    assert r.status_code == t.status_code == 400
    assert r.json()["error"]["type"] == t.json()["error"]["type"] == "OAuthException"
    assert invented(t.json(), r.json()) == []


# -- Scryfall ---------------------------------------------------------------------------------

CARD_FIELDS = ["id", "oracle_id", "name", "set", "set_name", "collector_number", "lang", "finishes", "prices",
               "image_uris", "artist", "scryfall_uri", "type_line", "rarity", "layout", "legalities"]


def test_scryfall_card(real, twin):
    url = "https://api.scryfall.com/cards/c21/263"
    r, t = real.get(url).json(), twin.get(url).json()
    assert (t["name"], t["set"], t["collector_number"]) == (r["name"], r["set"], r["collector_number"])
    assert missing(r, CARD_FIELDS) == [] and invented(t, r) == []
    assert set(r["prices"]) >= {"usd", "usd_foil", "usd_etched", "eur"}


def test_scryfall_double_faced_card(real, twin):
    url = "https://api.scryfall.com/cards/named?exact=Delver of Secrets&set=isd"
    r, t = real.get(url).json(), twin.get(url).json()
    assert t["name"] == r["name"] and "image_uris" not in r and r["card_faces"][0]["image_uris"]
    assert invented(t, r) == []


def test_scryfall_collection_and_errors(real, twin):
    body = {"identifiers": [{"name": "Sol Ring"}, {"name": "Not A Real Card Name"}]}
    r = real.post("https://api.scryfall.com/cards/collection", json=body).json()
    t = twin.post("https://api.scryfall.com/cards/collection", json=body).json()
    assert r["not_found"] == t["not_found"] == [{"name": "Not A Real Card Name"}]
    assert invented(t, r) == []
    r404 = real.get("https://api.scryfall.com/cards/named?exact=Not A Real Card Name")
    t404 = twin.get("https://api.scryfall.com/cards/named?exact=Not A Real Card Name")
    assert r404.status_code == t404.status_code == 404
    assert r404.json()["object"] == t404.json()["object"] == "error" and invented(t404.json(), r404.json()) == []


def test_scryfall_bulk_data(real, twin):
    r = next(b for b in real.get("https://api.scryfall.com/bulk-data").json()["data"] if b["type"] == "default_cards")
    t = next(b for b in twin.get("https://api.scryfall.com/bulk-data").json()["data"] if b["type"] == "default_cards")
    # mtg_toolkits downloads jsonl_download_uri when present, else download_uri
    assert missing(r, ["download_uri", "updated_at", "type"]) == [] and invented(t, r) == []
    assert r["download_uri"].startswith("https://data.scryfall.io/default-cards/")


def test_scryfall_requires_headers(real, twin):
    url = "https://api.scryfall.com/sets/c21"
    bare = {"User-Agent": "", "Accept": ""}
    assert real.get(url, headers=bare).status_code == twin.get(url, headers=bare).status_code


# -- Archidekt --------------------------------------------------------------------------------

def test_archidekt_search_and_deck(real, twin):
    r_page = real.get("https://archidekt.com/api/decks/v3/", params={"name": "elves", "orderBy": "-viewCount"}).json()
    assert missing(r_page, ["count", "next", "results"]) == [] and r_page["results"]
    twin.universe.archidekt.add_deck("Elves", "twin", [(1, "Llanowar Elves"), (1, "Sol Ring")])
    t_page = twin.get("https://archidekt.com/api/decks/v3/", params={"name": "elves"}).json()
    assert invented(t_page, r_page) == []

    deck_id = r_page["results"][0]["id"]
    r_deck = real.get(f"https://archidekt.com/api/decks/{deck_id}/").json()
    t_deck = twin.get(f"https://archidekt.com/api/decks/{t_page['results'][0]['id']}/").json()
    assert missing(r_deck, ["id", "name", "owner", "cards", "categories", "deckFormat"]) == []
    card = r_deck["cards"][0]
    assert missing(card, ["quantity", "categories", "modifier", "card"]) == []
    assert missing(card["card"], ["uid", "edition", "oracleCard", "collectorNumber"]) == []
    assert invented(t_deck, r_deck) == []

    gone = real.get("https://archidekt.com/api/decks/1/")
    assert gone.status_code == twin.get("https://archidekt.com/api/decks/1/").status_code
