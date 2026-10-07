# Threat model: the Vault as an OAuth authorization server for MCP

Issue #41 (part of epic #40). It covers what #42 to #47 build: how ChatGPT, Claude and other MCP
clients connect to a person's Vault by URL, with no token pasting. It was written before the code
and updated with it; each mitigation names the test that proves it.

Targets: OAuth 2.1 (draft 13), RFC 9728 (protected resource metadata), RFC 8414 (server metadata),
RFC 7636 (PKCE), RFC 8707 (resource indicators), RFC 9207 (`iss` in the response), RFC 8252
(loopback redirects), RFC 7591 (dynamic registration), RFC 7009 (revocation), RFC 9700 (security
best practice), Client ID Metadata Documents (draft-ietf-oauth-client-id-metadata-document), and
the MCP authorization specification (2025-11-25). The 2026-07-28 revision was not read for this
work; re-check it before the real-host run (`docs/mcp-oauth-host-checklist.md`).

## What is protected

| Asset | Why it matters |
|---|---|
| A person's collection, decks, prices paid, shares | Private by default (`docs/gdpr.md`); prices paid are sensitive |
| The account itself: tokens, export, deletion, sign-in methods | Whoever controls these controls the person |
| Access and refresh tokens, authorization codes | Bearer credentials; theft means access until expiry or revocation |
| The consent decision | It is the only thing that lets an app in; it must be informed, and made by the person |
| The Vault's network position | Fetching a client's metadata URL is a request to an address a stranger chose |
| The Vault's storage and capacity | Anonymous registration and metadata fetches create rows and traffic |

## Actors

- **The person** (resource owner): signed in on the web (provider or passkey).
- **A legitimate MCP client**: ChatGPT, Claude, an IDE, a script. Public client (no secret), identified by
  an https metadata URL or a self-registered id.
- **A malicious client**: any website or program that can send a browser to `/oauth/authorize`.
- **A network or local attacker**: can read a redirect (a malicious app on the same phone or computer, a
  proxy), or sit on a loopback port.
- **A malicious tenant**: another Vault user with a valid account, trying to reach someone else's data.
- **The Vault operator**: trusted, but the Vault should not log or store secrets it does not need.

## Trust boundaries

1. Browser to Vault (consent and sign-in pages; session cookie, `SameSite=None` in production because Apple's callback is cross-site).
2. Client to Vault (`/oauth/token`, `/oauth/register`, `/api/mcp`): bearer credentials, no cookies.
3. Vault to the internet (metadata document fetch): the only place the Vault connects to an address chosen by a stranger.
4. MCP request to in-process `/api/v1` call: the tool calls re-use the caller's credentials; the REST API enforces tenancy and scopes.
5. Tenant to tenant: every query is scoped by the signed-in user (`docs/gdpr.md`).

## Design in one paragraph

Authorization code flow with PKCE S256 only. The authorization server and the MCP server are this one
app. Clients are public. Codes live 60 seconds and are single use. Access tokens (`vault_oat_...`) last
1 hour, refresh tokens (`vault_ort_...`) 30 days and rotate on every use. A grant (one person, one app)
carries the scopes the person allowed (`read`, optionally `write`) and the MCP resource it is for. Tokens
work on `/api/mcp` only and never carry the `account` power. Only SHA-256 hashes are stored. Personal
access tokens are unchanged.

## Attack paths and mitigations

Test names are in `tests/test_mcp_oauth.py` (**M**) and `tests/test_oauth_clients.py` (**C**).

### 1. Authorization code interception or injection
A malicious app on the device reads the redirect (custom scheme, loopback port), or an attacker plants
a stolen code in the victim's flow.
- PKCE S256 is mandatory; `plain`, a missing method or a missing challenge is refused with `invalid_request`
  (M `test_pkce_s256_is_required`). The verifier format is checked (M `test_a_malformed_verifier_is_refused`),
  and the comparison is constant time.
- The code is bound to client, redirect URI, challenge, resource and person (M `test_a_code_is_bound_to_*`).
- 60 seconds, single use, claimed by a conditional `UPDATE`; reuse revokes the grant the first redemption made
  (M `test_a_code_works_once_and_a_replay_revokes_what_it_made`, `test_a_code_expires_after_a_minute`).
- Claiming the code and creating the grant are **one transaction**: a replay waits on the claim's row lock and, once
  through, finds the grant and revokes it, so there is no window in which the replay has nothing to revoke. A code that
  fails a check (wrong verifier, client, redirect URI) is burnt on purpose, with the claim committed (M `test_two_parallel_redemptions_of_one_code`
  against Postgres, `test_a_code_that_fails_a_check_is_burnt`).
- Redirect URIs are https or http loopback: no custom schemes, which any app can claim.

### 2. Redirect URI abuse and open redirects
- Exact string match against the URIs in the client's metadata or registration; loopback URIs (`127.0.0.1`,
  `[::1]`, `localhost`) may differ in port only, as RFC 8252 allows (M `test_redirect_uri_must_match_exactly_*`,
  `test_loopback_redirects_may_use_any_port_*`, `test_a_non_loopback_port_difference_is_not_tolerated`).
- **The requested string is validated strictly before matching, and the target is rebuilt, never echoed**
  (`match_redirect`): no backslash, `%5c`, control characters, whitespace, non-ASCII, userinfo or fragment, and for a
  loopback URI the target is the registered scheme, host, path and query plus the requested port, accepted only if the
  request is exactly that string. This closes a parser-differential bypass (a backslash before an `@`: Python read host
  127.0.0.1 while a browser goes to the host before the backslash). Tests: M `test_loopback_redirects_are_checked_strictly_and_never_sent_as_given`,
  `test_other_loopback_spellings_are_refused` (IPv6 forms, uppercase host, trailing dot), `test_a_good_loopback_redirect_is_sent_to_exactly_the_registered_host`,
  `test_the_review_exploit_string_is_refused`, and at the token step `test_the_token_step_refuses_redirect_variants_too`.
- An unknown client or a redirect URI that does not match produces an error **page**, never a redirect, so the
  Vault cannot be used as an open redirector. Only after both are validated are other errors redirected, and
  then only to the validated URI, with `state` encoded (M `test_errors_to_the_redirect_encode_the_state`).
- Duplicate parameters are refused (M `test_a_repeated_parameter_is_refused`).
- The consent screen shows where the person is sent back to, and warns when it is `localhost` (an app on
  this computer), which a metadata document cannot prove (MCP spec, localhost risks).
- The response carries `iss` (RFC 9207) so clients can detect a mix-up.
- The sign-in resume redirects only to `/oauth/authorize?<query stored in the session>`, a same-site path, and the
  stored request is validated again from scratch (M `test_a_provider_sign_in_returns_to_the_pending_request`,
  `test_an_ordinary_sign_in_does_not_resume_an_old_request`).

### 3. Confused deputy and token passthrough
The Vault does not call any upstream API with a client's token, and never forwards a token to anyone. The
MCP tools call `/api/v1` in-process with the same credentials. Self-registered clients get consent for every
authorization (there is no "remembered" consent that a different client could ride on).

### 4. Token replay across resources (audience)
- Every authorization request and grant is bound to the one resource `<BASE_URL>/api/mcp` (RFC 8707). A missing
  or different `resource` is `invalid_target` (M `test_other_request_problems_go_back_to_the_app`).
- Access tokens are accepted only on `/api/mcp` and on the in-process calls the MCP tools make; the marker is
  written into the ASGI scope by the server and cannot be sent by a client. Anywhere else (`/api/v1/...`
  directly) an OAuth token is as good as unknown: 401 (M `test_oauth_tokens_are_for_the_mcp_server_only`,
  `test_a_token_for_another_resource_is_refused`).
- The in-process marker applies only to `/api/v1/...` paths (the ones the MCP tools build), not to any path
  (M `test_the_mcp_marker_only_applies_to_v1_paths`).
- Personal access tokens are a different credential (`vault_pat_`) and keep working.

### 5. Account-level powers
Creating tokens, deleting or exporting the account, managing sign-ins, sessions and connected apps need the
person (`account_user`). OAuth tokens never carry the `account` scope, so those endpoints answer 403 even on
the in-process path (M `test_even_on_the_mcp_servers_own_calls_a_token_never_gets_account_powers`,
`test_authenticate_never_returns_the_account_scope`). Connected-app management itself is an account endpoint,
so an app cannot revoke or list its siblings.

### 6. Refresh token theft
- Rotating refresh tokens; the old one is kept (hash) until its natural expiry. If a rotated token comes back,
  the whole grant is revoked, including the access token the thief might hold (M `test_a_reused_refresh_token_revokes_the_whole_grant`).
- A refresh token presented by a different `client_id` revokes the grant (M `test_a_stolen_refresh_token_cannot_be_used_by_another_client`).
- Rotation is a compare-and-swap, so two racing refreshes cannot both succeed. Of two parallel refreshes with one token,
  exactly one wins (M `test_two_parallel_refreshes_with_one_token`, Postgres). The loser is **not** treated as theft when it
  lost the swap itself (it may be a retry; the grant survives), and **is** when it arrives after the rotation (reuse: the
  grant is revoked). Either way the old token never works again, and presenting it later revokes the grant.
- **A grant has an absolute age of 90 days from first consent**: refreshing never extends it, the refresh token's expiry is
  capped at it, and a refresh after it revokes the grant (M `test_a_grant_has_an_absolute_maximum_age`).
- Found: "sign out everywhere" (`POST /api/auth/logout?everywhere=true`) rotates only the account's *browser* session key; the
  existing app sessions (`api_sessions`) and personal access tokens are not revoked by it either, by design (`docs/api.md`: apps
  are signed out under `/me/sessions`). OAuth grants follow the same rule and are revoked under Connected apps; the 90 day age
  bounds them (M `test_sign_out_everywhere_is_browser_only_like_tokens_and_app_sessions`).
- Expired tokens are refused (M `test_expired_access_and_refresh_tokens_are_refused`); 1 hour access tokens limit the window.
- Tokens are never accepted in the query string (M `test_tokens_are_not_accepted_in_the_query_string`).
- Residual: a thief who refreshes before the owner wins the race; the owner's next refresh then trips reuse
  detection and revokes the grant, so the exposure ends at the next legitimate use.

### 7. Consent phishing and clickjacking
- Names are cleaned of everything that draws nothing or reorders text: control characters, all Unicode format characters (soft hyphen,
  zero-width, bidi, tag characters U+E0000-E007F, byte-order mark, word joiner), surrogates, private use, and blank-looking letters
  (Hangul filler, braille blank), with whitespace collapsed; a name with nothing left is refused (C `test_names_lose_invisible_characters_and_keep_their_host_line`).
- A name is only what the app says. The title is **name followed by the host that identifies it** ("Twin Agent (app.example)"),
  an IDN host shows its Unicode form and its technical (punycode) form with a warning, and a self-registered app is titled
  "Unverified app: <name>" with a visible warning; write stays unticked (C `test_a_named_app_is_shown_with_its_address_next_to_the_name`,
  `test_a_look_alike_address_shows_its_technical_form`, `test_a_self_registered_app_is_titled_unverified_and_write_stays_unticked`).
- The consent page names the client (name from its metadata, escaped, control and bidi characters removed),
  the **web address that identifies it** (the host of the `client_id` URL, which a document cannot fake),
  says plainly when the client is **unverified** (self-registered), the scopes in plain language, what it can
  never do, and where it will be sent back (M `test_the_consent_screen_names_the_app_*`,
  `test_hostile_text_in_names_cannot_inject_html`, C `test_a_registered_client_can_connect_and_is_marked_unverified`).
- `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` on every page of the flow, `no-store`,
  no external resources, scripts only on the sign-in page and only with a per-response nonce (M `test_pages_cannot_be_framed`).
- "Use a different account" avoids consenting as the wrong person (M `test_use_a_different_account_*`).
- Residual: a convincing look-alike client name on a look-alike domain. The domain is shown prominently; the
  Vault does not rank reputations.

### 8. Consent CSRF and replay
- The request being answered is stored **in the database** (`oauth_consents`: hash of a random nonce, the user, the request, a
  10 minute expiry), not in the cookie; the browser's session holds only the nonce, and the form must present the same one.
  The answer consumes the row with one conditional `DELETE ... RETURNING`, so a copied cookie and form (a signed cookie can not be
  invalidated server-side) or two racing answers make at most one code. At most 5 unanswered screens per person (M
  `test_a_consent_cookie_and_form_cannot_be_replayed`, `test_racing_consent_answers_make_one_code`, `test_unanswered_consent_screens_are_capped_per_person`,
  `test_consent_needs_the_nonce_made_for_this_browser`, `test_a_consent_answer_works_once`, `test_consent_expires`,
  `test_the_consent_belongs_to_the_account_that_saw_it`).
- The session holds the nonces of the browser's open consent screens (a list, not one slot), so a second `GET /oauth/authorize`
  (another tab, or a cross-site link) does not break an open screen; past the cap the oldest are evicted first, in the cookie and the
  database alike (M `test_a_second_authorize_request_does_not_break_an_open_consent_screen`, `test_open_consent_screens_are_evicted_oldest_first`,
  `test_a_non_ascii_nonce_is_an_error_page_not_a_crash`).
- The app's global cross-site write guard refuses a foreign `Origin` on `POST /oauth/authorize`
  (M `test_a_consent_form_posted_from_another_site_is_refused`). Only the token, registration and revocation
  endpoints, which take no cookies, accept cross-origin calls (C `test_registration_can_be_called_from_a_browser_*`).

### 9. Scope escalation
- No `scope` means `read`. `write` is offered only when the client asks for it and is an **unticked** box on the
  consent page; whatever the client asks, the grant holds only what the person ticked (M `test_write_is_never_granted_without_the_person_choosing_it`).
- Unknown scopes are `invalid_scope`. A refresh may narrow scopes, never widen them (M `test_refresh_cannot_widen_the_scopes`).
- The REST layer enforces scopes: a read grant gets 403 `insufficient_scope` on writes, and write tools are not listed.

### 10. SSRF through Client ID Metadata Documents
A stranger sets `client_id=https://x/...` and the Vault fetches it. Mitigations in `vault.oauth_clients.ClientFetcher`:
- https, port 443 only, a path, no credentials or fragment, no IP literals, no dot segments, no `.local`/`.internal`/single-label names, 512 characters (C `test_client_id_urls_that_could_aim_the_fetch_are_refused`).
- The name is resolved first and **every** address must be public: private, loopback, link-local (cloud
  metadata), shared (100.64/10), multicast, reserved and unspecified are refused, as are IPv4-mapped, NAT64
  and 6to4 forms carrying such an address, and Teredo (C `test_only_public_addresses_are_public`,
  `test_names_that_resolve_to_private_addresses_are_never_contacted`).
- The connection goes to the address that was checked (host name kept for TLS and the Host header), so DNS
  rebinding between check and use does nothing (C `test_the_connection_goes_to_the_address_that_was_checked`).
- Redirects are never followed (C `test_redirects_are_not_followed`); environment proxies are ignored.
- 32 KB and 5 seconds in total, whatever the server drips (C oversized, streaming and slow tests); `application/json` only;
  the document's `client_id` must equal the URL, and it needs a name and valid redirect URIs (C `test_a_document_about_another_client_is_refused`).
- Errors never repeat fetched content (C `test_errors_never_repeat_what_was_fetched`). Results are cached an hour; a stale
  client whose document vanished is refused (fail closed).
- The authorize endpoint is rate limited per IP. Fetching a stranger's URL also has **shared** limits that many addresses
  can not get around: at most `OAUTH_FETCH_LIMIT` (60) fetches a minute for everyone together (C `test_metadata_fetches_are_limited_for_everyone_together`),
  at most 8 at once per process (C `test_only_a_few_fetches_run_at_once`), and the request's database connection is released before
  the fetch starts (C `test_the_metadata_fetch_does_not_hold_a_database_connection`). The limits themselves must not become a
  denial-of-service lever, so: a URL or host that is refused (bad shape, unresolvable, any non-public address) is rejected **before**
  any budget or slot is spent (C `test_refused_urls_and_hosts_cost_no_budget`); each caller (keyed hash of the IP) also has a much smaller
  budget of its own, `OAUTH_FETCH_IP_LIMIT` (10 a minute), spent first, so one address can not use up the shared one (C
  `test_one_caller_cannot_spend_the_shared_budget`); and when the shared budget or every slot is spent, an app seen before is served from
  its cached row (up to 7 days old) instead of failing, while an app never seen fails with 503 (C `test_an_app_seen_before_keeps_working_when_the_budget_is_spent`,
  `test_an_app_seen_before_keeps_working_when_every_slot_is_busy`). A fetch that fails (as opposed to being out of budget) still fails closed.
- Every failure while fetching answers the caller with **one message** ("could not be fetched"); the detail (private address,
  redirect, bad content, size, time) goes to the server log, so the endpoint is not an oracle for what the Vault can reach (C
  `test_every_failure_while_fetching_looks_the_same_to_the_caller`, `test_names_that_resolve_to_private_addresses_are_never_contacted`).
- The raw query string is what is stored (consent row, session), so its own length is limited (2000), not the decoded one (C
  `test_a_heavily_percent_encoded_query_is_refused_not_a_server_error`); every other stored value is length-checked by its parser
  (client id 512, redirect URI 2000, challenge 128, resource and scopes fixed values).
- Cached documents have their own cap (`OAUTH_CIMD_CAP`, 5000), apart from registrations, so neither crowds out the other; at the cap the
  oldest unused documents are dropped and fetched again if used, and documents a grant uses are never dropped (C `test_cached_documents_and_registrations_have_separate_caps`,
  `test_the_cache_is_capped_by_dropping_the_oldest_unused`, `test_documents_in_use_are_not_evicted`).
- Two first fetches of one URL at once no longer fail on the unique index: the loser re-reads the winner's row (C `test_two_first_fetches_of_one_url_at_once_do_not_fail`).
- Residual: the Vault can be made to fetch any public https URL on 443 at the rate limit; the response is
  discarded unless it is a valid document.

### 11. Dynamic registration abuse
- Public clients only, validated redirect URIs (same rules), at most 10 URIs, 8 KB body, names cleaned and capped.
- Rate limited per IP (`OAUTH_REGISTER_RATE_LIMIT`, 20 a minute), and at most `OAUTH_CLIENT_CAP` (2000) client rows;
  unused registrations expire after a day and are removed when room is needed (C `test_the_number_of_registrations_is_capped`,
  `test_unused_registrations_expire_and_cannot_be_used`, M `test_the_authorize_token_and_registration_endpoints_are_rate_limited`).
- Registered clients are shown as unverified. A registration grants nothing by itself.
- Residual: an attacker who gets past the rate limit can fill the registration cap until the rows expire (a day) and block new
  registrations; cached metadata clients have a separate cap and are unaffected.

### 12. Tenancy leaks
- A grant belongs to one user and every token resolves to that user; MCP tool calls use the existing user-scoped
  API (M `test_a_token_only_reaches_its_own_owners_data`, `test_two_people_connecting_the_same_app_get_separate_grants`).
- Connected apps are listed and revoked by their owner only, 404 for anyone else's id (`tests/test_tenancy.py::test_connected_apps_are_private`).
- `oauth_clients` hold only what apps said about themselves, never personal data.

### 13. Secrets in logs, storage and responses
- Codes, access tokens and refresh tokens are stored as SHA-256 hashes of 256+ bit random values; nothing in the
  flow logs them (M `test_secrets_never_reach_the_logs`, `test_tokens_are_stored_only_as_hashes`).
- Token responses are `no-store` (M `test_the_token_response_is_not_cacheable`); authorize redirects are `no-store`
  and pages use `Referrer-Policy: no-referrer`.
- The data export lists connected apps without tokens (M `test_the_export_lists_connected_apps_without_tokens_and_erasure_removes_them`).
- The session cookie holds the pending request (at most 2000 characters) and the consent nonce, both signed; neither is a credential.

### 14. Timing and enumeration
- Hash comparison for PKCE uses `secrets.compare_digest`; consent nonces use `hmac.compare_digest`; tokens are looked up by hash.
- An unknown code, an expired one and a used one produce the same `invalid_grant` answer (M `test_unknown_and_known_codes_fail_the_same_way`);
  unknown and expired registered clients produce the same `invalid_client`.
- Residual: lookup by hash is a database index probe, not constant time; with 256-bit random secrets, learning anything from it is infeasible.

### 15. Revocation and lifecycle
- Revoking a connected app deletes the grant and its retired tokens: access and refresh stop at once (M `test_connected_apps_are_listed_and_can_be_revoked`).
- RFC 7009 revocation by the client (M `test_revoking_a_token_with_rfc_7009_*`); account deletion removes grants, codes and retired tokens
  (`vault.privacy.personal_data`, a test enforces the table list).
- At most 50 connected apps per person; connecting more drops the oldest.
- Signing out everywhere (browser sessions) does not touch connected apps, which are their own credentials; revoke them in Connected apps.

### 16. Availability
All OAuth endpoints are rate limited per IP in the existing database counters (`vault.ratelimit`), with their own, more
generous limit than sign-in (`OAUTH_RATE_LIMIT`, 120 a minute) because ChatGPT and Claude connect from shared addresses.

### 17. Client assertion abuse (`private_key_jwt`, added with #210 on 2026-10-06)

ChatGPT proves itself at the token endpoint with a signed assertion (RFC 7523) instead of PKCE alone. New paths, and
what stops each (`vault/client_auth.py`; tests in `tests/test_client_auth.py`, `tests/test_chatgpt_twin.py`):

| Attack | Mitigation |
|---|---|
| Replay a captured assertion | `jti` is single use per client (a counter row kept until the assertion expires); `exp` at most 10 minutes away |
| Forge one by choosing the algorithm (`none`, HS256 with the public key as secret) | Only the algorithm the client's document declared is accepted, and only RS256, PS256 or ES256 |
| Point `jwks_uri` at an attacker's key set | The `jwks_uri` must be on the same host as the `client_id`; a document served from elsewhere is refused |
| Make the Vault fetch arbitrary URLs by naming key ids | Keys are cached for an hour; an unknown `kid` refetches at most once a minute per URL, through the SSRF-hardened fetcher |
| Key rotation locks the client out | A new `kid` triggers one refetch; the last known keys keep working if the client's server is briefly away |
| Oversized or malformed assertion | 8 KB cap, `iss`/`sub` must equal the `client_id`, `aud` must name this token endpoint |
| A private key published by mistake | Keys with a private part (`d`) are dropped on load |

Residual: a client that leaks its own signing key can be impersonated until it rotates; the person still approves every
grant on the consent screen.

## Decisions to review

- **No custom-scheme redirects.** Some desktop apps (for example Cursor) use their own URL scheme. Allowing them lets any
  app on the device claim the scheme and receive codes. They can use a loopback redirect or an https one.
- **`resource` is required** and must be exactly `<BASE_URL>/api/mcp`.
- **The 401 challenge has no `scope` parameter**, so clients fall back to `scopes_supported` (`read write`) and the person
  decides about `write` on the consent screen. A `scope="read"` challenge would have kept clients from ever asking for write.
- **OAuth tokens do not work on `/api/v1` directly**, only through MCP. The REST API is a separate surface; a client that
  wants it can use a personal access token.
- **Refresh tokens last 30 days, sliding, within an absolute 90 days** from first consent.
- **Consent is asked every time**, even for a client the person already connected.
- **Public clients and `private_key_jwt` clients are supported; shared-secret (`client_secret_*`) clients are not.** claude.ai uses a metadata document with PKCE only (`none`); ChatGPT's document declares `private_key_jwt` (section 17).

## Residual risks

- Loopback impersonation: a local program can claim a legitimate client's metadata URL and a loopback port. Mitigated by
  warning text, PKCE and the person's consent, not eliminated (spec-acknowledged).
- Phishing with look-alike names on look-alike domains; the domain is shown, nothing more.
- A copied refresh token used before the owner's next refresh gives the thief access until reuse is detected.
- Registration storage can be filled to its cap by an anonymous attacker for up to a day (the metadata cache is separate and evicts).
- Real ChatGPT and Claude behaviour is untested here: see `docs/mcp-oauth-host-checklist.md` (issue #48).
