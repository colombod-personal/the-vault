# The digital twin universe

The Vault depends on seven outside services: Google, Microsoft, Apple and Facebook for sign-in,
Scryfall for card data and prices, Archidekt for decks, and Vercel's API for the setup job
(`jobs/vercel_setup.py`). `twins/` contains a **behavioural
clone** ("digital twin") of each one. The idea comes from StrongDM's Digital Twin Universe and
Microsoft's `amplifier-bundle-digital-twin-universe`.

- **Real addresses.** Twins answer on the real host names (`accounts.google.com`,
  `api.scryfall.com`, …). The Vault's code runs unchanged: only the network underneath is
  swapped, the way DNS would be.
- **Stateful.** Twins keep accounts, registered apps, one-time codes, tokens, signing keys,
  cards, prices and decks. Apple remembers who already consented, so the user's name arrives
  only once, as in reality.
- **Strict like the real thing.** A twin refuses what the real service refuses: unknown clients,
  redirect-URI mismatches, reused or expired codes, wrong PKCE verifiers, Apple client secrets
  signed with the wrong key, Scryfall calls without `User-Agent`/`Accept`, more than 75 identifiers
  per collection call, and rate-limit violations (429 plus a 30-second lockout).
- **Scenario knobs** on every twin:
  - `outage`: everything answers 503
  - `latency`
  - `fail_next(path, status, times)`: scripted errors
  - `rotate_keys()` on identity twins
  - `set_price()` on Scryfall
  - `private` decks on Archidekt
  - `deploy()`, `promote()` (an Instant Rollback) and `add_domain()` on Vercel
- **ChatGPT, as it really behaves** (`ChatGptClient` in `twins/mcp_client.py`, from the production logs of 2026-10-06):
  a `private_key_jwt` client whose document and keys sit at `chatgpt.com/oauth/client.json` and `/oauth/jwks.json`;
  every token request goes out unsigned first (refused) and then signed with an RS256 assertion; it asks for `read write`;
  it scans `tools/list` once when the app is added and keeps that list (`add_app`, `cached_tools`); and its safety check
  flags tool descriptions that steer the approver (`flagged`). `tests/test_chatgpt_twin.py` runs it, and the nightly
  conformance run compares its document and keys with the real ones. A twin simpler than the real client is how
  ChatGPT could not connect while every OAuth test passed (#210).
- **Archidekt decks are as heavy as real ones**: the twin sends every field of the real API (captured 2026-10-06 in
  `tests/fixtures/archidekt_real_keys.json`: 30 deck fields, 36 card-analysis fields, 27 price fields from Card Kingdom,
  Cardmarket and other shops), about 2 KB a card, so tests meet the size and the shop prices real answers carry.
- **Clients too.** `twins/mcp_client.py` is the twin of an MCP client such as ChatGPT or Claude: it hosts
  Client ID Metadata Documents (and hostile ones: documents about another client, redirects to the cloud metadata
  address, oversized or slow answers, names that resolve to private addresses) and drives the whole OAuth flow
  (discovery, PKCE, consent, tokens, refresh, MCP calls) so abuse cases are one-line overrides. See
  `tests/test_mcp_oauth.py`, `tests/test_oauth_clients.py` and `docs/mcp-oauth-threat-model.md`. Its hosts end in
  `.example` and resolve through `Universe.resolve`; there is no live conformance check because there is no single
  real service to compare with (the real hosts are checked by hand: `docs/mcp-oauth-host-checklist.md`).
- **Sealed.** A request to a host outside the universe fails as a network error and is recorded
  in `universe.escapes`. The tests assert that it stays empty.
- **Checked against reality every night.** `tests/conformance` sends the same requests to each real
  service and its twin, then compares:
  - exact values: issuers, endpoints, error codes
  - shapes: a twin may leave fields out but never invent them or change their types
  - required fields: the ones the Vault reads must exist in the real answers

## In tests

```python
from twins import Universe

universe = Universe()
universe.register_vault(settings)          # the OAuth apps, as registered in each provider's console
app = create_app(settings, transport=universe.transport)

location = client.get("/api/auth/login/google", follow_redirects=False).headers["location"]
ann = universe.google.add_account("g-1", "ann@gmail.com", "Ann")
callback = universe.google.approve(location, ann)   # or .deny(location)
client.get(callback.url)                            # Apple: client.post(callback.url, data=callback.data)
```

`universe.transport` works for both sync and async httpx clients: the Vault's sign-in code, the
native-token verifier, the Archidekt route, the daily price job (`sync_prices.main(transport=...)`),
and `mtg_toolkits` clients (`ScryfallClient(client=universe.client())`).

What the twins caught while the suite was being built:
- **The Vault never checked the ID token's audience.** Authlib only validates `aud` when asked.
  The old hand-written fake hid this, because it left out `azp`. Fixed: `aud` and `iss` are now
  checked explicitly.
- **A provider outage at the start of sign-in was a 500.** It now returns to the sign-in screen
  with an error.
- **Native sign-in during an Apple outage was a 500.** It is now a 503 with `Retry-After`.
- **Microsoft's JWKS keys have no `alg`** (found by the conformance run). The twin was fixed to match.

## Running the app against the twins

```bash
# terminal 1: the twins, with the vault's OAuth apps registered from the same .env
python -m twins --port 9000          # control panel: http://localhost:9000/_twins

# terminal 2: the vault, sending every outbound call to the twins
VAULT_TWINS_URL=http://localhost:9000 uvicorn --factory vault.app:create_app --reload
```

Every sign-in button then shows the twin's sign-in page. Pick a demo account, create one, or
cancel. The daily job runs against the Scryfall twin too:
`VAULT_TWINS_URL=http://localhost:9000 python -m jobs.sync_prices`. It has a few seeded
cards (`twins/data/scryfall_cards.json`); add more through the control API.

`VAULT_TWINS_URL` is refused when `BASE_URL` is https or the app runs on Vercel, and `twins/` is
not deployed (`.vercelignore`).

How the pieces connect:
- The Vault's outbound calls go to `http://localhost:9000/h/<real host>/<path>` (`vault/outbound.py`).
- Sign-in redirects to browsers are rewritten the same way.
- Card data comes from the Vault, which calls the Scryfall twin; image and icon URLs in its
  answers point at the twins.

### Control API (for agents, scripts, the iOS simulator)

| Call | |
|---|---|
| `GET /_twins/api/state` | every twin's state: accounts, apps, keys, faults, call counts |
| `POST /_twins/api/reset` | forget codes, tokens, faults and calls |
| `POST /_twins/api/{twin}/outage` `{"on": true}` | the service goes down |
| `POST /_twins/api/{twin}/latency` `{"seconds": 2}` | slow answers |
| `POST /_twins/api/{twin}/fail` `{"path": "/token", "status": 500, "times": 1}` | scripted failure |
| `POST /_twins/api/{google\|microsoft\|apple\|facebook}/accounts` `{"sub", "email", "name", "hide_email", "share_email"}` | add a user |
| `POST /_twins/api/{provider}/rotate-keys` `{"keep_old": false}` | key rotation |
| `POST /_twins/api/{apple\|google}/native-token` `{"aud", "sub", "nonce"}` | the ID token Sign in with Apple / Google Sign-In would give the iOS app |
| `POST /_twins/api/scryfall/cards` `{"name", "set", "collector_number", "prices": {"usd": 1}}` | add a card |
| `POST /_twins/api/scryfall/prices` `{"id", "usd": 2.5}` | move a price |
| `POST /_twins/api/scryfall/rate-limits` `{"on": true}` | enforce Scryfall's rate limits |
| `POST /_twins/api/archidekt/decks` `{"name", "owner", "cards": [[1, "Sol Ring"]], "private": false}` | add a deck |

### iOS simulator

The simulator can't show the real Sign in with Apple sheet against a twin. In debug builds,
the app can ask the twin for an ID token instead:
`POST /_twins/api/apple/native-token {"aud": "<bundle id>", "sub": "sim-user", "nonce": "<sha256 of nonce>"}`.
It then sends the token to `POST /api/v1/auth/native/apple` as usual.

Browser sign-in (`ASWebAuthenticationSession`) works unchanged. The Vault's login URL
redirects to the twin page, and the handoff to `vault://auth?code=…` is the real one.

## Keeping the twins honest

- **Nightly:** `.github/workflows/twins-conformance.yml` runs `TWINS_LIVE=1 pytest -m live tests/conformance`.
  A failure means a real service changed, or a twin drifted. Fix the twin first, then whatever
  relies on it.
- **When the Vault starts using a new endpoint or field** of an outside service, add it to the
  twin and add a conformance check.
- **Vercel:** the token checks run nightly with no secrets. The contract check
  (`test_vercel_contract_used_by_the_setup_job`) needs a scratch Vercel project with a
  production deployment and one variable: set the repository variable
  `VERCEL_CONFORMANCE_PROJECT` to its name. It never touches `the-vault`. It switches the scratch
  project's *Automatically expose System Environment Variables* on and then restores it.
- **A new outside service** gets a twin (hosts, routes, error format, scenario knobs), is added to
  `Universe`, and gets conformance checks. Nothing reaches a real service from the tests.
