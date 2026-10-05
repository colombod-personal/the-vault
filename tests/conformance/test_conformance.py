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


@pytest.mark.parametrize("kind", ["default_cards", "oracle_cards", "rulings", "oracle_tags"])
def test_scryfall_bulk_data(real, twin, kind):
    r = next(b for b in real.get("https://api.scryfall.com/bulk-data").json()["data"] if b["type"] == kind)
    t = next(b for b in twin.get("https://api.scryfall.com/bulk-data").json()["data"] if b["type"] == kind)
    # mtg_toolkits downloads jsonl_download_uri (Scryfall no longer sends download_uri)
    assert missing(r, ["jsonl_download_uri", "compressed_size", "updated_at", "type"]) == [] and invented(t, r) == []
    assert r["jsonl_download_uri"].startswith(f"https://data.scryfall.io/{kind.replace('_', '-')}/")


def test_scryfall_ruling_and_tag_shapes(real, twin):
    """The first line of the real rulings and oracle-tags files, against the twin's."""
    import gzip, json

    def first(client, kind):
        entry = next(b for b in client.get("https://api.scryfall.com/bulk-data").json()["data"] if b["type"] == kind)
        raw = client.get(entry["jsonl_download_uri"]).content
        return json.loads(gzip.decompress(raw).splitlines()[0])

    universe = twin.universe
    oracle = next(iter(universe.scryfall.cards.values()))["oracle_id"]
    universe.scryfall.add_ruling(oracle, "A twin ruling.")
    universe.scryfall.add_tag("twin-tag", cards={oracle: "median"})
    real_rule = real.get(next(b for b in real.get("https://api.scryfall.com/bulk-data").json()["data"] if b["type"] == "rulings")["jsonl_download_uri"])
    r_rule = json.loads(gzip.decompress(real_rule.content).splitlines()[0])
    assert missing(r_rule, ["oracle_id", "source", "published_at", "comment"]) == []
    assert invented(first(twin, "rulings"), r_rule) == []
    r_tag = first(real, "oracle_tags")
    assert missing(r_tag, ["id", "slug", "label", "description", "parent_ids", "child_ids", "taggings"]) == []
    t_tag = first(twin, "oracle_tags")
    assert invented({k: v for k, v in t_tag.items() if k != "taggings"}, r_tag) == []
    assert missing(r_tag["taggings"][0], ["oracle_id", "weight"]) == []


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


# -- Vercel (jobs/vercel_setup.py) ------------------------------------------------------------

VERCEL = "https://api.vercel.com"
VERCEL_CALLS = [
    ("GET", "/v9/projects/the-vault"),
    ("PATCH", "/v9/projects/the-vault"),
    ("GET", "/v10/projects/the-vault/env"),
    ("POST", "/v10/projects/the-vault/env"),
    ("PATCH", "/v9/projects/the-vault/env/env_x"),
    ("GET", "/v9/projects/the-vault/domains"),
    ("GET", "/v13/deployments/the-vault.vercel.app"),
    ("POST", "/v13/deployments"),
]


@pytest.mark.parametrize("auth", ["missing", "invalid"])
@pytest.mark.parametrize("method,path", VERCEL_CALLS, ids=[f"{m} {p}" for m, p in VERCEL_CALLS])
def test_vercel_refuses_without_a_valid_token(real, twin, auth, method, path):
    headers = {"Authorization": "Bearer not-a-token"} if auth == "invalid" else {}
    body = {} if method != "GET" else None
    r = real.request(method, VERCEL + path, headers=headers, json=body)
    t = twin.request(method, VERCEL + path, headers=headers, json=body)
    assert r.status_code == t.status_code == 403
    assert r.json() == t.json()


@pytest.fixture(scope="module")
def vercel_project():
    """A scratch Vercel project with a production deployment and one variable, never the app's
    own: VERCEL_CONFORMANCE_TOKEN, VERCEL_CONFORMANCE_PROJECT (and VERCEL_CONFORMANCE_SCOPE)."""
    token, project = os.environ.get("VERCEL_CONFORMANCE_TOKEN"), os.environ.get("VERCEL_CONFORMANCE_PROJECT")
    if not (token and project):
        pytest.skip("set VERCEL_CONFORMANCE_TOKEN and VERCEL_CONFORMANCE_PROJECT (a scratch project)")
    assert project != "the-vault", "conformance never touches the production project"
    scope = os.environ.get("VERCEL_CONFORMANCE_SCOPE")
    return project, {"Authorization": f"Bearer {token}"}, {"slug": scope} if scope else {}


def test_vercel_contract_used_by_the_setup_job(real, twin, vercel_project):
    """Read-only except one project setting, switched on and restored, plus one redeploy of a
    deployment that doesn't exist (refused, builds nothing)."""
    project, headers, params = vercel_project
    v = twin.universe.vercel
    v.add_project("scratch", ["scratch.vercel.app"], [{"key": "EXAMPLE", "target": ["production"]}])
    v.deploy("scratch")  # unstamped, like a deployment vercel_setup never redeployed
    tw = {"Authorization": "Bearer twin-vercel-token"}

    r = real.get(f"{VERCEL}/v10/projects/{project}/env", headers=headers, params=params).json()
    t = twin.get(f"{VERCEL}/v10/projects/scratch/env", headers=tw).json()
    assert r["envs"], "give the scratch project one production variable"
    assert missing(r["envs"][0], ["id", "key", "target", "createdAt", "updatedAt"]) == []
    assert invented(t, r) == []

    r = real.get(f"{VERCEL}/v9/projects/{project}/domains", headers=headers, params=params).json()
    t = twin.get(f"{VERCEL}/v9/projects/scratch/domains", headers=tw).json()
    assert missing(r["domains"][0], ["name", "verified", "redirect", "gitBranch"]) == []
    assert invented(t, r) == []

    domain = next(d["name"] for d in r["domains"] if not d.get("redirect"))
    r = real.get(f"{VERCEL}/v13/deployments/{domain}", headers=headers, params=params).json()
    t = twin.get(f"{VERCEL}/v13/deployments/scratch.vercel.app", headers=tw).json()
    assert missing(r, ["id", "url", "createdAt", "meta"]) == [] and r["target"] == "production"
    assert invented(t, r) == []

    # The project setting that gives functions VERCEL_GIT_COMMIT_SHA: turned on, read back, then the
    # scratch project's own value restored.
    before = real.get(f"{VERCEL}/v9/projects/{project}", headers=headers, params=params)
    assert before.status_code == 200
    original = bool(before.json().get("autoExposeSystemEnvs"))
    try:
        r = real.patch(f"{VERCEL}/v9/projects/{project}", headers=headers, params=params, json={"autoExposeSystemEnvs": True})
        t = twin.patch(f"{VERCEL}/v9/projects/scratch", headers=tw, json={"autoExposeSystemEnvs": True})
        assert r.status_code == t.status_code == 200
        assert r.json()["autoExposeSystemEnvs"] is t.json()["autoExposeSystemEnvs"] is True
        r = real.get(f"{VERCEL}/v9/projects/{project}", headers=headers, params=params).json()
        t = twin.get(f"{VERCEL}/v9/projects/scratch", headers=tw).json()
        assert r["autoExposeSystemEnvs"] is True and missing(r, ["id", "name"]) == []
        assert invented(t, r) == []
    finally:
        real.patch(f"{VERCEL}/v9/projects/{project}", headers=headers, params=params,
                   json={"autoExposeSystemEnvs": original}).raise_for_status()

    body = {"name": project, "deploymentId": "dpl_doesnotexist", "target": "production"}
    r = real.post(f"{VERCEL}/v13/deployments", headers=headers, params=params, json=body)
    t = twin.post(f"{VERCEL}/v13/deployments", headers=tw, json={**body, "name": "scratch"})
    assert r.status_code == t.status_code and r.json()["error"]["code"] == t.json()["error"]["code"]


def test_commander_spellbook_find_my_combos(real, twin):
    """The same two-card deck, asked of the real service and of its twin: the twin may leave fields
    out but must not invent any, and what vault.combos reads must be in the real answer."""
    deck = {"main": [{"card": "Thassa's Oracle", "quantity": 1}, {"card": "Demonic Consultation", "quantity": 1}], "commanders": []}
    r = real.post("https://backend.commanderspellbook.com/find-my-combos", params={"limit": 1}, json=deck)
    t = twin.post("https://backend.commanderspellbook.com/find-my-combos", params={"limit": 1}, json=deck)
    assert r.status_code == t.status_code == 200
    real_results, twin_results = r.json()["results"], t.json()["results"]
    for key in ("included", "almostIncluded"):
        assert key in real_results
    variant = (real_results["included"] or real_results["almostIncluded"])[0]
    for key in ("id", "uses", "produces", "description", "manaNeeded", "easyPrerequisites", "notablePrerequisites", "identity", "popularity", "bracketTag"):
        assert key in variant, key
    assert "card" in variant["uses"][0] and "name" in variant["uses"][0]["card"] and "feature" in variant["produces"][0]
    assert invented(t.json(), r.json()) == []
    assert invented(twin_results["included"][0], variant) == []
