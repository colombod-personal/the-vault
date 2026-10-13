# Threat model: the Vault as an OAuth authorization server for MCP

Issue #41 (part of epic #40). It covers what #42 to #47 build: how ChatGPT, Claude and other MCP
clients connect to a person's Vault by URL, with no token pasting. Each mitigation names the test that proves it.
**It was not written before the code**: see "What happened" below, which corrects the claim this paragraph used to make.

Targets: OAuth 2.1 (draft 13), RFC 9728 (protected resource metadata), RFC 8414 (server metadata),
RFC 7636 (PKCE), RFC 8707 (resource indicators), RFC 9207 (`iss` in the response), RFC 8252
(loopback redirects), RFC 7591 (dynamic registration), RFC 7009 (revocation), RFC 9700 (security
best practice), Client ID Metadata Documents (draft-ietf-oauth-client-id-metadata-document), and
the MCP authorization specification (2025-11-25). The 2026-07-28 revision was not read for this
work; re-check it before the real-host run (`docs/mcp-oauth-host-checklist.md`).

## What happened (history, recorded 2026-10-07)

Issue #41 asked for two things that did not happen in that order, and history cannot be changed. Read from `git log`:

| When (2026-10-04, +0100) | Commit | What |
|---|---|---|
| 01:11 | `66bb839` | The OAuth 2.1 authorization server (metadata, authorize, consent, tokens, client identification, connected apps) lands on `main` |
| 01:14 | `fe51c01` | **First commit of this document**, three minutes after the endpoints, together with the host checklist and the connected-apps panel |
| 01:48 | `f8982d3` | "OAuth review fixes": a security review found a redirect bypass (the "review exploit string", `test_the_review_exploit_string_is_refused`), consent and code-redemption races, and unsafe metadata fetching |
| 02:18 | `8dc6d5d` | "OAuth second review": fetch budgets that could be used for denial of service, stale rows, generic errors |

So the threat model was **written after the endpoints**, and the review that found real defects happened **after the code was
on `main`**, not before the merge. The commit messages carry no pull request number, and the repository does not show
whether a pull request or a review record existed. Nothing here claims otherwise. The two criteria of #41 that ask for the
order ("required before the endpoints", "a review before merge") cannot be met retroactively; they are reported as such
and need the owner to waive them.

What changed so it cannot happen silently again: `.github/pull_request_template.md` asks every pull request for a
`Threat model:` line and a `Security review before merge:` line, `scripts/check_pr_rules.py` (workflow `pr-rules.yml`,
`tests/test_pr_rules.py`) fails a pull request that changes the OAuth, sign-in, token, privacy or sharing code without both,
and `AGENTS.md` states the rule. CI cannot prove a review took place; it makes the claim visible to whoever merges.

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
- An unknown client or a redirect URI that does not match produces an error **page**, never a redirect. Any other
  request error (a bad `response_type`, challenge, resource or scope) is found after the client and its redirect address
  are validated, but "validated" only means "equal to what the client declared", and anyone can declare an address
  (registration, or a hosted metadata document). So since 2026-10-08 (#339) the Vault does not redirect on those
  either: it shows a page that names the app and the host, says what was wrong, and offers a **Return to <host>** button
  carrying `error`, `state` and `iss` (M `test_other_request_problems_are_shown_with_a_return_button_not_redirected`,
  `test_a_request_error_never_redirects_a_stranger_even_when_nobody_is_signed_in`, `test_errors_to_the_redirect_encode_the_state`).
  Before that change this was an unauthenticated redirector to an app-declared address, which the earlier wording
  of this section ("cannot be used as an open redirector") overstated. The redirect after the person answers the
  consent screen is unchanged: they chose it.
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
so an app cannot revoke or list its siblings. A copied web session or native token does carry them: what that allows, what is built
against it and what is built (a recent sign-in, re-confirmed by a passkey, a provider or an e-mailed one-time code) is in
"A copied session, and the sign-in methods it can add" below.

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
- The REST layer enforces scopes: a read grant gets 403 `insufficient_scope` on writes, and write tools are not listed, with two exceptions: `start_collection_upload` and `POST /api/v1/uploads` (a link that waits for the person; the section "The upload link for a read-only connection" below says what it can cause), and `sign_out` with `POST /api/v1/agent/sign-out` (ends only the caller's own OAuth connection; the section "Signing out from the chat" below).

### 10. SSRF through Client ID Metadata Documents
A stranger sets `client_id=https://x/...` and the Vault fetches it. Mitigations in `vault.oauth_clients.ClientFetcher`:
- https, port 443 only, a path, no credentials or fragment, no IP literals, no dot segments (a segment that is `.` or `..`, also percent-encoded once or twice; a name that only starts with a dot is fine: `/.well-known/mcp-client.json` is the standard place for a client metadata document and is what Perplexity uses, #363; the fetch is made safe by the address check below, not by the shape of the path), no `.local`/`.internal`/single-label names, 512 characters (C `test_client_id_urls_that_could_aim_the_fetch_are_refused`).
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

Hardening added with the file intake (vault-0n9.3, review of PR #478): `public_address` also refuses the IPv4-compatible form
(`::a.b.c.d`), SIIT-translated addresses, site-local `fec0::/10`, the local-use NAT64 prefix and the 6to4 relay anycast
`192.88.99.0/24` (C `test_the_public_address_check_refuses_the_forms_a_plain_is_global_test_passes` in `tests/test_file_intake.py`).
None was reachable on the lane host; it closes a gap that needed DNS control of an allowed name.

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
- **One row per app, Disconnect revokes every connection of it (#246).** An app that authorizes again (a second device, or
  re-added so a host reads a new tool list) gets a new grant and the older ones stay valid, on purpose: "newest wins" would
  make two devices of the same person sign each other out in a loop. So `GET /me/apps` groups a person's grants by app (the
  client's web address; self-registered clients, which get a new id each time they are added, by name, and never together with
  an app that has a verified address) and `DELETE /me/apps/{id}` on any grant of the row revokes **all** that person's grants
  of that app, with their retired refresh tokens (M `test_disconnect_stops_every_connection_of_the_app_each_one_by_its_id`,
  `test_disconnecting_by_an_older_connections_id_disconnects_the_whole_app`, `test_disconnect_removes_the_retired_refresh_tokens_of_every_connection`).
  The row shows the widest scopes of its connections, so an older write connection is not hidden behind a newer read-only one
  (M `test_the_row_shows_the_widest_scopes_*`); the revocation is one transaction (M `test_disconnect_is_one_transaction_all_or_nothing`).
  Grouping only ever reads the caller's own grants, so it widens nothing across people (`tests/test_tenancy.py::test_connected_apps_are_private`).
  Residual: two unrelated self-registered apps that chose the same name share a row (Disconnect then revokes both: over-revoking,
  never under-revoking), and the row is idle only when all its connections are, with the idle ones counted beside it.
  Residual: a stolen refresh token is not revoked by the app connecting again (it is by reuse detection, by the person pressing
  Disconnect, and when it is not used for 30 days); the Account page marks a connection unused for 14 days as idle so it is seen.
- **Unused connections end by themselves.** A refresh token lasts 30 days from its last use (each refresh renews it, capped at 90
  days from consent). Connections past that are removed, with their retired tokens, whenever the person's list is read and
  whenever anyone makes a new connection (every person's expired ones go then), so they do not linger as rows (M `test_a_connection_is_removed_when_its_refresh_token_expires_but_its_siblings_stay`,
  `test_an_app_whose_only_connection_expired_disappears_from_the_list`, `test_connecting_an_app_clears_everyones_expired_connections_even_if_nobody_opens_the_list`). A connection unused for 14 days is marked idle
  (M `test_a_connection_unused_for_14_days_is_marked_idle_and_the_app_is_idle_when_all_are`).
- RFC 7009 revocation by the client (M `test_revoking_a_token_with_rfc_7009_*`); account deletion removes grants, codes and retired tokens
  (`vault.privacy.personal_data`, a test enforces the table list).
- At most 50 connections (grants) per person; connecting more drops the oldest.
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
- **Pre-registered clients (issue #45, "where needed"): not built, because no client needs it.** The idea is an
  operator-configured list of clients (fixed `client_id`, redirect URIs, a name, `kind='preregistered'`) for a host that
  supports neither metadata documents nor dynamic registration. claude.ai and ChatGPT use metadata documents, and every other
  client we know can register dynamically, so no such host has been shown. `OAuthClient.kind` is `cimd` or `dcr`, and this
  model analyses neither the configuration channel (settings or environment, who can change it), nor how such a client would
  be shown on the consent screen ("verified" would be a claim the Vault makes), nor secret handling if one needed a secret.
  Building a path into an authorization server without a user or an analysis would add risk for nothing, so it is not built.
  **Owner decision, recommended: accept "not needed today".** If a host needs it, the mechanism, a section in this document
  and its tests go in one pull request, with the review lines the pull request template asks for.
- **A recent sign-in for account-level actions (#347): decided yes by the owner on 2026-10-09, and built.** 10 minutes, for delete, export,
  creating a personal access token, connecting an AI app, adding a passkey, removing an older passkey and linking a provider, confirmed with a
  passkey, a linked provider or a code e-mailed to a verified address (Resend, inert until `RESEND_API_KEY` is set). The design, the routes, the
  attacks considered and the residual risks are in "A copied session, and the sign-in methods it can add"; "Sign out everywhere" stays free of it.
- **Public clients and `private_key_jwt` clients are supported; shared-secret (`client_secret_*`) clients are not.** claude.ai uses a metadata document with PKCE only (`none`); ChatGPT's document declares `private_key_jwt` (section 17).

## Residual risks

- Loopback impersonation: a local program can claim a legitimate client's metadata URL and a loopback port. Mitigated by
  warning text, PKCE and the person's consent, not eliminated (spec-acknowledged).
- Phishing with look-alike names on look-alike domains; the domain is shown, nothing more.
- A copied refresh token used before the owner's next refresh gives the thief access until reuse is detected.
- Registration storage can be filled to its cap by an anonymous attacker for up to a day (the metadata cache is separate and evicts).
- Real ChatGPT and Claude behaviour is untested here: see `docs/mcp-oauth-host-checklist.md` (issue #48).
- The metadata fetch has no hard wall-clock limit on the **header phase**: httpx's timeout is per read, so a server that
  sends response headers a byte at a time (under the timeout apart, up to the HTTP parser's header limit) keeps one of the
  8 fetch slots of a process busy. It only affects first-time apps (known apps are served from the cache), each caller may
  start 10 fetches a minute, and a slot is only held for as long as the server keeps dripping. A real limit needs an async
  fetch under `asyncio.timeout` and an async twin transport; not built (design and cost: issue #337, third row).
- The per-IP limits trust the first `X-Forwarded-For` entry when `VERCEL` is set. That is right only while Vercel's edge
  overwrites the header; behind another proxy that appends to it, a caller could choose its own key. Not checked against
  Vercel's documentation in the review.

## Security review 2026-10-08 (issue #48)

**Method.** A read-only review of the OAuth server as it stands on `main` (a reviewing agent reading `vault/oauth_server.py`,
`oauth_routes.py`, `oauth_clients.py`, `client_auth.py`, `tokens.py`, the MCP authentication path in `vault/api/mcp.py` and `app.py`,
the models and the tests, against RFC 6749, 7636, 7591, 8707, 9728, 9700 and the client metadata document draft), then each
reported finding re-read by the author against the code before it was filed. Nothing was run. It was asked for verified findings
only, with the exploit path.

**Result.** No path to account takeover, cross-user data, token confusion or code/refresh theft was found. Seven lower findings,
all fixed or recorded:

| # | Finding | Disposition |
|---|---|---|
| 1 | The "5 seconds in total" fetch limit was not enforced across several addresses or in the header phase | Addresses: capped at 3, the deadline checked before each attempt, each attempt gets the time left (#337). Header phase: residual risk above |
| 2 | A compressed metadata response was decoded a whole chunk at a time before the size check (a few KB could become tens of MB) | The fetch asks for `identity` and refuses any other `Content-Encoding` (#337) |
| 3 | A request error was redirected at once to the redirect address the client declared | Page with a "Return to" button instead (#339); section 2 corrected |
| 4 | Retired refresh hashes grew without a limit per grant | Newest 10 per grant kept (#338) |
| 5 | Per-IP limits were per IPv6 address, so a /64 could rotate | Limited per /64 (#338) |
| 6 | Deeply nested JSON raised `RecursionError` (a 500, no leak) | Refused like any bad JSON (#338) |
| 7 | Caller-chosen `client_id`, `client_assertion_type` and `grant_type` went unescaped into a log line | Written with `%r` and cut short (#338) |

**Checked and fine** (read end to end, with the reviewer's references in the review notes of the pull request): strict redirect
matching and rebuilt targets; the consent page is validated before it is shown; repeated parameters refused; S256 only and
mandatory; the code is bound to client, redirect URI, challenge, resource and user, claimed by one conditional update, a replay
revokes; refresh rotation by compare-and-swap with reuse detection and a 90-day cap; scope can only narrow on refresh; consent
CSRF (nonce in session and in the database for that user, taken once, no cross-site origin for `/oauth/authorize`); the consent
cannot be answered for another user; framing, caching and CSP headers on every page; the resource indicator checked at issue and
at use; an OAuth token accepted only on `/api/mcp` and its in-process calls, never account-level; secrets stored as SHA-256
hashes; fixed error texts; SSRF (https on 443, no IP literals or credentials, every resolved address public including mapped,
NAT64, 6to4 and Teredo forms, the connection pinned to the checked address, no redirects, JSON only, 32 KB); client assertions
(same-host `jwks_uri`, asymmetric algorithms, `iss`, `sub`, `aud`, `exp`, single-use `jti`); bounded growth of consents, grants
and clients; the documented lifetimes equal the code's.

**Could not verify, left as is.**
- `X-Forwarded-For` trust (above).
- Fail-open at the token endpoint: if a client's row is gone (evicted by the cache cap), `authenticate_client` treats the client
  as public, so a `private_key_jwt` client's code could be redeemed with PKCE alone. It needs the code and the verifier and an
  attacker who filled about 5,000 cache rows between consent and redemption (a 60-second window), so it was judged not
  reachable in practice. A safer design would read the client from the code row. Not changed here, because the other half of the
  change (a public client after its row expired) needs its own decision.
- Metadata served stale for up to 7 days when the shared fetch budget is spent: a documented trade-off (a client that removed a
  redirect address stays honoured for that long).

## The reviewer demo account (#239, #345)

The stores' reviewers sign in with a passphrase (`REVIEWER_PASSPHRASE`; `vault/reviewer.py`) to one fixed synthetic account. Since
2026-10-08 (found by the review of the sign-in code): its session has **no account-level powers**: creating a personal access
token, adding a passkey, linking a provider, exporting and deleting the account are refused (403), so nothing made through it
outlives the passphrase and one reviewer cannot wipe the account under another. Its cookie carries a keyed hash of the passphrase
(`rv`), and the session is refused (401) whenever the passphrase is unset or different, so unsetting or rotating the variable ends
every reviewer session at once, including the OAuth consent step. A provider sign-in on a reviewer session switches accounts
instead of linking to the demo account. Tests: `tests/test_reviewer.py` (the 2026-10-08 cases). Residual: a demo session cookie
made before this change has no `rv` and is not restricted until it expires (30 days at most); the demo account can be reset with
`jobs/seed_reviewer.py`.

## Passkey challenges (#346, 2026-10-09)

The WebAuthn challenge of a passkey ceremony used to be a row in one global table with a cap, so anyone could fill the cap with
unauthenticated `…/options` calls and make passkey sign-in answer 429 to everybody. It now lives in the signed session cookie (kind,
random id, challenge, expiry) and `options` writes nothing. Finishing a ceremony spends it: `_take` refuses a cookie that is expired,
of another kind or incomplete, then inserts the ceremony's id into `passkey_challenges` (primary key) with `ON CONFLICT DO NOTHING
RETURNING id`, so a replayed cookie or two racing requests succeed once; a failed verification still burns the ceremony. The cookie is
signed, not encrypted: a challenge is public to the browser anyway, and the signature stops a client changing the expiry, kind or
id. A read-only review of the change (2026-10-09, before the merge) found no replay, cross-ceremony or race problem; its two timing
notes (one clock reading for the expiry and the clean-up) are applied.

**IPv6 rate-limit keys (decision, #346).** The per-IP limits key an IPv6 caller by its /64 (`vault/ratelimit.py`, `limit_key`), as
now. A /64 is what one home or phone connection is given, so it is the unit an attacker can rotate through for free; a /56 or /48
would also lump in neighbours on the same ISP delegation (a whole site's users sharing one allowance), while an attacker who rents a
/48 can already rotate through 65,536 /64s. The /64 is kept: it stops the cheap case, and a determined attacker with a /48 is the
same problem as one with many IPv4 addresses, which the per-IP limit never claimed to stop. With the challenge stateless, the limit
no longer protects other people's sign-in, only the server's work.

## Resetting the collection (#129, 2026-10-09)

A reset (`POST /collection/reset`, the MCP tool `reset_collection`) empties the whole inventory or one bucket: an account-level
destructive action that is reachable by an OAuth app or a personal token **with the write scope**, because it is a collection
operation like an import with `replace_everything` (which such a caller can already do), not an account power (`account_user`, section 5:
creating tokens, deleting the account). It touches `vault/privacy.py` only to add the snapshot table to erasure and export.

What stops an assistant resetting without the person:

- **Scope.** A read-only token and a read-only grant are refused (403), the preview included: the preview is step one of a destructive
  action, so `POST /collection/reset` is not in `READ_ONLY_POSTS` (`tests/test_collection_reset.py`). The MCP tools are not listed
  without the write scope, and are marked destructive so a host asks first.
- **Two steps, bound to the preview.** Without a `confirmation` the call only previews. The confirmation is an HMAC over the person, the
  scope, the options (keep tags, keep history, no undo), the collection version, a digest of the rows (and tags) it would remove and its
  expiry (15 minutes); apply recomputes the preview and refuses (409) anything that differs, so a changed collection, other options or
  another scope are stale. An assistant that previews and confirms in one go is still a write-scope app doing what it was allowed: the
  protection against that is the one every write tool has (the host's approval of a destructive tool, the server instructions and the
  skill that say to show the numbers and the export link and to wait for the person's exact yes). **The person's own session** (the
  web or a native app: the `account` scope, which no OAuth app or token has) must also send `typed: "RESET"` exactly with the
  confirmation, and the server refuses (422) without it, as account deletion asks for `DELETE`.
- **What an app cannot choose** (review of 2026-10-09: a prompt-injected assistant with write scope could preview, take the token and
  apply in one turn). An OAuth app or a personal token is refused (422, preview and apply) when it asks for `no_undo`, `keep_tags: false`
  or `keep_history: false`, and the MCP tool does not offer them: making a reset permanent, or wiping the person's tags, notes and
  history, is the person's choice in the Account panel. Tests: `tests/test_collection_reset.py`.
- **The undo is best-effort, not a guarantee.** For 7 days the latest reset keeps a snapshot (`reset_snapshots`) and
  `undo_collection_reset` restores the same rows, buckets, folders and baselines, **but only while nothing else has changed the
  collection**: any later write that moves the collection version (an import, an edit, a move) ends the undo, which is then refused
  rather than guessed. A hostile assistant that resets and then writes once more has therefore made the reset permanent as far as the
  undo goes; the export the preview links, and the person's own app file, are the real backup. The answer says so in one sentence. The
  reset is recorded in the import history under the app's name (`imports.kind = reset`).
- **Tenancy.** The scope is the caller's: another person's `bucket_id` is a 404, as is another person's snapshot (the snapshot is
  keyed by the caller's id, there is no id to guess). Rate limit 10 a minute per person; `Idempotency-Key` honoured.

Residual: a connected app with write scope can reset the copies after a confirm, as it can already import a file with
`replace_everything`; a hostile one can also follow it with another write and so end the undo (above), leaving the export and the
person's own app file as the way back. It cannot clear tags, notes or history, nor drop the snapshot. The snapshot holds the same personal data as the copies it removed, so
it is in the data map, the export (`last_reset.json`), erasure and the daily retention job (`docs/gdpr.md`).

## A copied session, and the sign-in methods it can add (#347, 2026-10-09)

Found by a read-only review of the sign-in code (2026-10-08). The threat: someone gets a copy of a person's web session cookie
(`vault_session`, signed with `SESSION_SECRET`, valid up to 30 days after it was last written) or a one-hour native access token. Both
satisfy `account_user`, so the holder can do every account-level action: delete the account, export everything, create a personal
access token, add a passkey, link a provider. "Sign out everywhere" rotates `User.session_key`, which ends every copied cookie, **but
not a sign-in method the holder added meanwhile** (their own passkey, their own Google identity): with it they sign in again after the
person has signed out everywhere. This needs a stolen cookie first, so it is a depth problem, not a way in. The issue splits the fix in
four criteria. The third was built first (below); the owner decided yes on 2026-10-09 and the others (the recent sign-in and its e-mailed code) are built after it.

### Built: "Sign out everywhere" ends the other sessions, then shows and removes sign-in methods (criterion 3)

Five read-only agent reviews of this change (the author's and four independent ones) (2026-10-09, before any merge; no human review yet) found the problems recorded
below as "found by review"; each is fixed and tested. The first version listed only what was added in the last 24 hours and signed out last.

- **What the person sees, in this order on purpose.** Account, Sign-in methods, "Sign out everywhere" opens a panel.
  *Step 1*, "Sign out the other browsers": `POST /api/auth/sign-out-others` replaces the account's `session_key` and re-issues this
  browser's cookie with the new one, so a copied cookie is dead from this moment and this browser stays signed in. Because a copied
  cookie can also mint more than a browser session (sign in with the attacker's own Google and hand over to an app, or create a
  personal access token or consent to a connected app), step 1 also signs out **every app session** (`api_sessions` with their retired
  refresh tokens, and unused hand-over codes; the Vault app asks the person to sign in again), the **OAuth authorization codes not yet
  redeemed** (a code lives 60 seconds and is redeemed at `/oauth/token` with no cookie, so one minted by the copy would otherwise make a
  30-day grant after the answer) and unanswered consent screens, and removes the **personal access tokens and connected apps created in
  the last 24 hours**; older ones are the person's own doing and stay (they are listed under Agents &
  API and Connected apps). The page says exactly this before the button and counts what went afterwards. *Step 2*: the panel
  lists **all** the sign-in methods with their dates (the ones added in the last 24 hours first, marked "new"), each with Remove where the
  server says it can be removed, and the reason where it cannot. Then "Done", or "Also sign out of this browser".
  *Found by review:* the first version removed methods first and signed out last, so a live holder of the copied cookie could add a way
  back in after the check; and it said "Nothing was added in the last 24 hours" about a method added two days ago (a false all-clear).
  Now the other sessions end before anything is shown, and older methods are listed with their dates.
- **API** (`docs/api.md`; the person only, through `account_user`, so a personal access token, a connected app and a reviewer's demo
  session get 403): `GET /api/v1/me/sign-in-methods[?recent_only=true]` (passkeys and linked providers, newest first, paged, with
  `recently_added`, `removable` and `removable_reason`: `only_method`, `provider_too_old`, `needs_older_method` or, for a passkey older than 24 hours on a session that has not signed in recently, `recent_sign_in_required`),
  `DELETE /api/v1/me/identities/{id}` (new: unlink a provider), `DELETE /api/v1/me/sign-in-methods/recent` (new: remove everything
  added in the last 24 hours, in one request, after the page's confirmation), `POST /api/auth/sign-out-others` (new) and the existing
  `DELETE /api/v1/me/passkeys/{id}`. The window is the server's constant `RECENT_SIGN_IN_METHOD_HOURS` (`vault/auth.py`); the page only
  renders it. No migration: `identities` and `passkeys` already had `created_at`.
- **Every removal ends the sessions that could have signed in through the method.** Sessions share the account's key, so a session an
  attacker started *through* a planted passkey or provider after step 1 would outlive its removal. `remove_passkey`, `remove_identity`
  and `remove_recent_methods` therefore replace the key again whenever they delete something and re-issue the caller's cookie (the owner
  stays signed in; their other browsers sign in again). Removing nothing ends nothing. *Found by the second review* (the "Done" of the
  first version never rotated again). Test: the attacker signs in through the planted passkey, the owner removes it, the attacker's new
  cookie is 401 (`tests/test_passkeys.py`).
- **A request already past authentication cannot mint anything after the session ended, and the account lock orders everything.**
  `vault/locks.py` takes the account lock one way: `SELECT … FOR NO KEY UPDATE` on the user row (not `FOR UPDATE`: a refresh or a consent
  inserts a row with a foreign key to the user, which takes `FOR KEY SHARE`; `FOR UPDATE` conflicts with it, and a refresh holding its
  `api_sessions` row while waiting for the user row could deadlock with step 1, which holds the user row and deletes that session; the
  test holds the lock and a refresh still completes) with `SET LOCAL lock_timeout = '5s'` (a lock that cannot be had is the Vault's usual
  503 with `Retry-After`, not a hung worker). `require_live_session` (`vault/auth.py`) takes it and compares the key it reads with the
  cookie's (401 `SessionEnded` on a mismatch); an **app session** (a bearer token with the account scope, which a copied cookie can mint
  through the hand-over) is held to its own `api_sessions` row, which must still exist under the lock, and step 1 deletes those rows under
  the same lock. It runs right before the insert in the passkey register (the count of passkeys is under it, for app callers too),
  `POST /me/tokens` (inside the idempotent work), the native sign-in, `POST /api/auth/app-handoff`, the OAuth consent answer, and the
  provider link (for a new identity and for moving one over: `find_or_create(..., hold=request)`; the callback sends the person back to `/`
  with `session_ended`). **Every removal and step 1 call it first** (`hold_account`), so a request authenticated with an old key that
  waited for step 1's lock is refused instead of rotating the key again and writing the new key into its own stale cookie (that would
  have revived the stale cookie and killed the owner's); `rotate_session_key` also refuses a cookie that does not hold the live key. **The
  code redeems take the lock before they claim** (`tokens.redeem_code`, `oauth_server.exchange_code`), and the app code's claim and the
  session it makes are one transaction (before, the code was deleted and committed first and the session made outside any lock, so a
  redeem that had already claimed the code survived step 1). A redeem is thus either finished before step 1 (and its session or recent grant
  deleted by it) or finds the code gone. The async handlers that take these locks (`native_sign_in`, the provider callback) do their database
  work in a worker thread, so waiting for a lock never stops the event loop. Tests (`tests/test_sign_out_others_*.py`,
  `tests/test_passkeys.py`, `tests/test_recent_sign_in_methods.py`): for each route the key (or the app session) is ended between
  authentication and the check and nothing is minted or removed and no cookie revived; a redeem waits for step 1 and finds the code gone;
  a lock past its timeout is a 503; with the check, the order or `NO KEY` removed they fail. *Found by the third and fourth reviews.*
- **The one rule for every removal: a method older than 24 hours must remain afterwards.** `established_methods` (`vault/auth.py`) counts
  the OTHER providers and passkeys older than the window, and each removal (`remove_identity`, `remove_passkey`,
  `remove_recent_methods`) has it inside its own `DELETE` statement after locking the account row, so two removals at once take turns
  and the account is never left with none (`tests/test_recent_sign_in_methods.py`, with threads). The passkey identity row is the
  WebAuthn handle, not a method: it is not counted and cannot be removed here (404). Another person's method is never listed, and
  removing it is a 404. *Found by review:* the first version held only provider unlinking to this, so a copied session could register
  its own passkey B and delete the owner's old passkeys, leaving B as the only method (a takeover). Now it cannot: the last old method
  can never be removed while only new ones would remain.
- **Behaviour change, stated plainly.** Before, any passkey could be removed while another way to sign in remained. Now a passkey (new
  or old) is removed only while another method older than 24 hours remains. So an account whose only old method is one passkey cannot
  remove it (add another passkey or sign-in, wait a day, then remove the first), and a young account (every method added today)
  removes nothing: the owner's and a copied session's methods cannot be told apart, and the person is told so (with the reason). This is the cost of having no recent-sign-in
  check. It stays: the built recent sign-in (below) adds a requirement for an older passkey (a fresh session) and keeps "a method older than 24 hours must remain".
- **Unlinking a provider is also limited to one linked in the last 24 hours** (409 otherwise, `provider_too_old`): if any provider could
  be unlinked, a copied session could add its own passkey and then unlink every provider the owner uses. An older provider is shown with
  its date and the reason, (a fresh session unlinking it was built and withdrawn after review: below).
- **Passkeys per account are capped at 20** (`MAX_PASSKEYS`, `vault/passkeys.py`; 409 with a message at the options step, and again
  under the account lock when the credential is saved, so two devices cannot overshoot it). With "Remove everything added in the last 24
  hours" (one request, 409 unless an older method stays) a flood of recent passkeys is bounded and removable. *Found by review:* the
  panel showed only the first 100 and a copied session could add more.
- **The date has to be the date it was added to this account.** `_claim_identity` moves a sign-in from an empty account of its own to
  the account that links it. Its `created_at` is now set to the move. Otherwise a sign-in made a week earlier on a throw-away account
  and then linked with a copied session would look a week old (`test_a_sign_in_moved_over_from_an_older_empty_account_counts_from_the_move`,
  checked to fail without that line). A claim racing an unlink answers `identity_in_use`, not a 500 (tested with a held lock).
- **A passkey's name is the person's or the attacker's choice** (up to 80 characters, escaped by React): a passkey called "iPhone" can
  look like the owner's. The page shows when it was added to the minute and, for new ones, the time of day.
- **Tests:** `tests/test_recent_sign_in_methods.py` and `tests/test_passkeys.py` (the cap): a method added 2 hours ago is listed and
  removable; 3 days ago is not highlighted; the last method is refused; another person's are never listed or removed; a token and a
  reviewer session get 403 on every new route; the 24-hour edge; old providers, young accounts and passkey replacement are refused;
  `removable_reason`; remove-everything-recent; the cap; next links keep `limit`; sign-out-others kills a second session's cookie
  and keeps this one; concurrent removals and claim-versus-unlink. Screenshots at 1400 and 390 px:
  `docs/screenshots/signout-everywhere-*.jpg`.
- **What it does not do.** It does not stop a copied session adding a method in the first place, and the person has to open the panel:
  nobody is told when a method is added (the Vault holds an e-mail address only from some providers and sends no mail). It does not
  end a copied native access token, a personal access token or a connected app's grant (they are listed and revoked under the
  Account panel's apps and tokens). A method an attacker added more than 24 hours ago is listed with its date but may not be
  removable yet (the reason is shown). The recent sign-in below is what stops the adding.

**Residual risks of the 24-hour rule, found by review in #407.** Resolved or reduced by the recent sign-in below (each says how); kept here
as they were written:
- *The 24-hour rule delays a takeover, it does not prevent one by a patient attacker.* With a copied session the attacker registers their own
  passkey at time 0 and keeps hold of the account; if the owner does not open "Sign out everywhere" within a day, that passkey is older than
  24 hours, counts as an "older method", and the attacker can then remove the owner's passkeys. A recent-sign-in check, which the attacker
  cannot pass, stops the registration at time 0.
- *A provider an attacker linked more than 24 hours ago cannot be unlinked by the owner.* The attacker links their own Google with the
  copied session; the owner notices on day three; the page lists it with its date but unlinking is refused (`provider_too_old`), so the
  attacker can keep signing in until the account is deleted. With a recent sign-in, unlinking any provider would be allowed for a fresh session.
- *An account younger than 24 hours removes nothing.* Signed up this morning, the owner and a copied session cannot be told apart (every method
  is new), so no removal is allowed and an attacker's method added today stays until tomorrow. Sign out the other browsers still ends the copied
  cookie, but not a method the attacker already planted.
- *Step 1 ends every app session, but a personal access token or connected app made more than 24 hours ago is
  not ended,* because it is the person's own doing; if a copied session made one and the owner waited a day, it must be revoked by hand.

**Known effect on the owner (not fixed).** The cookie is re-issued by the response that rotates the key. A parallel request from the
owner's own browser sent just before that response arrives still carries the old cookie and gets a 401: the web app then shows the
sign-in screen and clears its offline copy. A reload recovers it (the new cookie is in the browser by then). It happens after step 1
and after each removal of a sign-in method, the moments the owner is looking at the panel.

### Built (owner decided yes, 2026-10-09): a recent sign-in for the serious account actions, and an e-mailed code (criteria 1, 2 and 4)

**The decision.** The owner, in chat on 2026-10-09, recorded on #347: "The yes I agree and to proceed we do a one time code to their email
and they click there to authorize or type the code." The recommended 10 minutes and the recommended action list were taken as they stood
(the owner approved the recommended defaults). This section replaces the proposal that stood here.

**Reviews (all by agents; no human has reviewed this).** The author's own read, then two independent read-only adversarial agent reviews of
this diff, each before any merge (2026-10-09). Round 1 found, and these are fixed and tested: the mail went to the unverified, first-wins
`users.email` (a copied session could place its own address on a passkey-only account and keep it after cleanup; now a provider-verified
address a day old, below); a reused request id let a copy of the cookie share a later ask (now a new id on every ask); the withdrawn
relaxation (below); the Approve button's `no-referrer` policy made browsers send `Origin: null`, which the cross-site-write guard refuses
(now `same-origin`). It also found that the OAuth consent screen mints a 30-day credential with no recent sign-in; round 2 confirmed each
fix and rated that one blocking against the owner's goal, so it is built too (below). A third read on the pull request (Copilot's review) found that the account deletion did not re-check the live session between authentication and the purge (a request past the freshness check could delete after "Sign out everywhere"; now `require_live_session` runs before `purge_user`, tested) and that returning from a provider broke the browser's Back button (fixed). What is left open is listed under "What this
resolves, and what it does not" and "Residual risks", and a follow-up issue holds it.

**The claim.** `sign_in()` (`vault/auth.py`) writes `auth_at` (seconds since the epoch, the server's clock) into the signed session cookie
when someone proves control of a sign-in method the account already had, and the e-mailed code writes it when confirmed. The cookie is
signed, so its holder can read but not change it. Two details decide whether it means anything, and both are tested:
1. **A provider sign-in that links a method new to the account keeps the old `auth_at`** (`sign_in` clears the session and rebuilds it, so
   the old value is carried across, as `app_flow` is). A copied session that links its own Google account is therefore not made fresh by
   it, and registering a passkey never touches it. Fresh means: someone just proved control of a method the account had before, or of its
   mailbox.
2. **Cookies made before the release have no `auth_at`** and are stale (a value in the future, or of another type, is stale too), so
   everybody confirms once on their first serious action after the release. The reviewer demo session has no account powers at all
   (#345) and is unaffected.

`vault/recent_signin.py` holds the check (`require_recent`, 403 with the stable code `recent_sign_in_required`, `X-Error` header and
`window_seconds`); `vault/app.py` has a `fresh_user` dependency next to `account_user`. The window is `RECENT_SIGNIN_SECONDS` (600; 60 to
3600 or the Vault does not start).

**Routes that need it** (a stale session is refused and nothing changes): `DELETE /api/v1/me`, `GET /api/v1/me/export`,
`POST /api/v1/me/tokens`, `POST /api/auth/passkey/register/options` and `/register/verify` (the first so nobody is led through a device
prompt and refused at the end; the second because the cookie may go stale in between), `DELETE /api/v1/me/passkeys/{id}` **for a passkey
added more than 24 hours ago**, and linking a provider: the callback
`GET|POST /api/auth/callback/{provider}` when a session is signed in and the identity is new to the account (a link, or the claim of an
empty account; it redirects to `/?link_error=recent_sign_in_required`, or to the app with `error=recent_sign_in_required`), and
`POST /api/v1/auth/native/{provider}` under the same condition, and **connecting an AI app**: `GET /oauth/authorize` shows a stale session
the sign-in page instead of the consent screen, and `POST /oauth/authorize` refuses an answer from a stale session (a reviewer's demo session is
exempt: it has no account powers and it is how the stores' review connects an app). Found by review: the consent mints a 30-day grant, up to
read and write over MCP, the same power as the personal access token that is gated. The callback checks, not only the Account page, because a copied session
can open `/api/auth/login/google` directly. **Left free on purpose:** "Sign out everywhere" (`POST /api/auth/sign-out-others`: a person in
a hurry must always be able to cut every session, and an attacker gains nothing by signing the owner out), removing a method added in the
last 24 hours (single or `DELETE /me/sign-in-methods/recent`: it can only remove what is new next to something the owner has used for
longer, so the panel works from a stale session), `PATCH /me` (the name), shares, collection edits, and disconnecting an app or a session.
Personal access tokens, connected apps and the reviewer session never had account powers: 403 as before, without the code.

**Removals: the relaxation was built and withdrawn.** The brief allowed letting a fresh session unlink an older provider (an attacker's
provider linked more than 24 hours ago cannot be unlinked today) "only if it can be done safely". It was built, the independent review
found it unsafe, and it is not shipped: an attacker who plants a passkey inside one fresh window and waits a day has a method older than
24 hours; signing in with it is a recent sign-in, and with the relaxation it could then unlink every provider the owner uses (the "older
method must remain" rule is satisfied by the attacker's own method). Nothing tells the owner's methods from the attacker's once both are a
day old. So an **older provider is still never unlinked here** (`provider_too_old`, fresh or not), and a method added in the last 24 hours
is always free to remove. A passkey older than 24 hours can be removed only by a fresh session (new: before, any session could), and the
rule "**a method older than 24 hours must remain afterwards**" holds for every removal (`established_methods`, inside the `DELETE`
statement itself, after locking the account row), so a fresh copy still cannot take the owner's last old method. Test:
`test_a_fresh_session_still_cannot_remove_the_last_old_method`, `test_an_older_provider_stays_linked_even_to_a_fresh_session_and_a_new_one_is_free`.
**Next step, not built:** unlinking an older provider needs a way to tell the owner's methods from an attacker's that survived a day, for
example a sign-in that was *used* by the owner after it was added (`Passkey.last_used_at` exists, identities have none), or a notice mail when a
method is added.

**App sessions.** An iOS access token has the `account` scope and no cookie. Its freshness is judged from the `ApiSession` row it belongs
to: `created_at`, the sign-in that made the session (a refresh does not renew it); signing in again makes a new session. Apps do not use
the e-mailed code (`app_sign_in_required`). Tests: `test_an_app_session_is_as_recent_as_its_sign_in`,
`test_an_app_cannot_link_another_sign_in_from_a_stale_session`. There is no iOS app in this repository to try it against.

**The e-mailed code** (`/api/auth/recent/email/*`, `vault/recent_signin.py`, `vault/email.py`). The person asks; the Vault mails a
six-digit code and a link to the address on the account. The code is typed into the page that asked, or the link is opened (on any
device) and its **Approve** button pressed; either confirms the browser that asked. What each attack meets:
- *A link prefetcher, a mail scanner or a preview.* Opening the link is a GET that shows a page (who asked: browser and system, minutes
  ago; Approve; This wasn't me) and changes nothing. Only the POST of the button approves. Test:
  `test_a_link_prefetcher_cannot_spend_the_code`. The page is `no-store`, `Referrer-Policy: same-origin` (not `no-referrer`: a browser then sends `Origin: null` on the form's POST and the cross-site-write guard would refuse the Approve button), and `frame-ancestors 'none'`
  (Vercel's header) stops it being framed under a fake button. The link's token (256 bits) is in the URL, hence in request logs: it is one
  use, ten minutes, and approving it confirms only the browser session that asked.
- *Someone asks for a code in the owner's name.* The code goes to the owner's mailbox and is bound to the asker's session: the account's
  session key and a random request id in the asker's cookie, hashed together. The owner reading the code out, or approving the link, helps
  the asker only; the e-mail says who asked (browser and system, time) and to ignore it otherwise. **Every ask writes a new request id**
  (found by review: a reused id let a copy of the cookie taken after an earlier ask share a later one), and a send that fails leaves the
  last good code alone; asking again ends the codes this cookie held. A copy of the cookie taken before the code was requested, another
  browser of the same account, and another account all get `no_code` (a code of their own is `wrong_code`). A copy taken *after* the ask
  holds the same request id as the browser that asked and is, for this purpose, the same session: it can use the code, approve-and-poll,
  or ask again, which cancels the owner's live code (and uses the account's hourly allowance below). That is what a cookie is.
  Signing everyone out ends a waiting code (the key changes). Tests: `test_a_code_requested_in_one_browser_cannot_be_used_in_another`,
  `test_a_code_belongs_to_one_account`, `test_signing_everyone_out_kills_a_code_that_was_waiting`,
  `test_a_second_ask_replaces_the_first_and_a_copy_of_the_old_cookie_gets_nothing`. **Residual:** a person talked into
  approving a link they did not ask for approves the attacker's request. The page names the browser and the minutes to make that
  visible; there is no number to compare. Anyone who can read the account's mailbox can confirm, which is the owner's chosen trade.
- *Guessing.* Six digits from `secrets.randbelow`, stored only as an HMAC keyed with `SESSION_SECRET` (a leaked table cannot be tried
  offline without the key), ten minutes, single use (the `used_at` update is the gate), at most 5 tries counted in the same `UPDATE` that
  reads them (parallel guesses cannot exceed five), a new code replaces the old one only after it was sent, compared with
  `hmac.compare_digest`, and the per-IP sign-in limit (`AUTH_VERIFY_RATE_LIMIT`) on top. **The odds, written down (found by review):** a
  stale cookie can ask for a code and guess at it. Per code that is 5 tries at one in a million. With only the limits above (3 codes an
  hour, 5 tries each) that is 15 guesses an hour, 10,800 over a cookie's 30 days, about 1 percent. So the account also has a **budget of
  10 failed tries an hour summed over all its codes** (`FAILED_TRIES_PER_HOUR`; past it `confirm` and `start` answer 429
  `email_attempts_exhausted` and only the passkey and provider paths remain) and **10 codes a day** (`SENDS_PER_DAY`, 429
  `email_account_daily_limit`): at most 50 guesses a day, 1,500 over 30 days, **about 0.15 percent** at the very most for an attacker who never
  stops, and the owner sees a mail for every code. Tests: `test_an_account_has_a_budget_of_failed_tries_an_hour_and_then_only_the_other_paths_remain`,
  `test_an_account_may_ask_for_ten_codes_a_day`.
- *Locks.* `start` counts the caps under the Vault-wide advisory lock and the account lock from `vault/locks.py` (`lock_account`,
  `FOR NO KEY UPDATE`, the 5 s timeout set with `SET LOCAL` before the advisory lock is asked for), so a lock held long by one request
  answers 503 with Retry-After for that ask instead of stalling every account's ask. Tests: `test_a_held_account_lock_makes_start_answer_503_and_does_not_hang`,
  `test_a_held_vault_wide_cap_lock_does_not_stall_an_ask_for_ever`.
- *Apple's private relay.* Apple forwards mail sent to a `privaterelay.appleid.com` address only from senders registered in the developer
  account's "Sign in with Apple for Email Communication" settings. Until the owner registers the sending domain (and sets
  `APPLE_RELAY_REGISTERED=1`), `start` answers 409 `email_relay_unregistered` ("Nothing was sent"), `GET /me/recent-sign-in` says
  `reason: relay_unregistered`, and the page offers the passkey and provider paths, so nobody is told "sent" for a mail Apple will drop.
  Tests: `test_an_apple_private_relay_address_is_not_mailed_until_the_sender_is_registered`, `..._once_the_owner_registered_the_sender`.
- *Mail flooding and cost.* At most 3 sends an hour per account (under a lock, so two requests cannot both pass) and `EMAIL_DAILY_CAP`
  (default 90, Resend's free plan is 100) a day for the whole Vault, then a clean 429 (`email_hourly_limit`, `email_daily_cap`) that points
  to the passkey or provider. A failed send deletes its row so it does not count. **Residual (found by review):** a stale copy of the
  cookie can ask three times an hour and so use the owner's hourly allowance and send the owner up to 72 "someone asked" mails a day (the
  mail says to ignore them); the owner then confirms with a passkey or provider. Someone with a few accounts, each with a verified address
  of their own, can use up the daily cap for everyone for a day; erasing an account deletes its rows, so the count can also be reset by
  creating and deleting accounts, which only makes the Vault-wide cap a cost guard: Resend itself refuses past its own plan's 100 a day
  (the twin does too, `daily_quota_exceeded`), and a refused send answers `email_send_failed` with the fallback paths.
- *Learning about an address.* Everything here needs a signed-in session of the account. The answers say a code went "to the address on
  this account", masked (`***@e***.com`), and never carry the address or the code. An account with no usable address, a Vault with no
  sender, or an app gets a 409 that says so and the page offers the passkey and provider paths (tests for both).
- *Where the address comes from (found by review, fixed).* The first version mailed `users.email`, which is whatever the first provider
  said, unverified, never cleared and not editable: a copied session inside its fresh window could link its own Google to a passkey-only
  account, put its own address there, survive "Sign out everywhere" (which removes the Google link, not the address) and receive every
  later code; and a Microsoft or Facebook address is unverified, so anyone could have the Vault mail a third party. Now the code goes to
  the e-mail of the **oldest linked provider whose address the provider vouches for** (`identities.email_verified`: Google and Apple say so
  in the ID token; Microsoft and Facebook are never taken as verified) **and that has been on the account for more than 24 hours**
  (`mail_address`). It leaves with the identity that gave it. Tests: `test_the_address_a_code_goes_to_is_one_a_provider_vouches_for_and_that_is_a_day_old`,
  `test_a_passkey_only_account_has_no_address_to_poison_for_a_day`, `test_an_unverified_google_address_receives_nothing`. Existing identities start
  as not verified and become verified at their next sign-in with Google or Apple (no data migration), so until then the code option is
  not offered and the passkey and provider paths are. **Residual:** a patient attacker who links their own Google and is not noticed for a
  day on a passkey-only account becomes that account's address; Account lists every method with its date, and "Sign out everywhere" removes
  methods added in the last 24 hours, but nothing tells the owner on day one.
- *Inert by default.* Without `RESEND_API_KEY` there is no sender: `/me/recent-sign-in` says `reason: no_sender`, start answers 409
  `email_unavailable`, and no outbound call exists. The sender is behind a small `EmailSender` interface (`ResendSender`: HTTPS to
  `api.resend.com`, bearer key, 5 s timeout, one retry made with the same `Idempotency-Key`, key and message never logged). Tests and local
  development use `twins/resend.py`; `universe.escapes` stays empty. The twin is built from Resend's documentation and **not yet
  compared with the live service** (`tests/conformance/test_resend_live.py` waits for an account and a key).
- *Data.* `email_codes` holds hashes, a coarse browser label and times, no address and no code; 10 minutes as a code, 2 days as the record
  the caps count (daily job), erased with the account. Resend receives the address and the message: `docs/gdpr.md` and the privacy notice
  name it, with what Resend's own pages say about retention (read 2026-10-09; not our figure).

**What a person sees.** On `recent_sign_in_required` the Account page shows "Confirm it's you" at the top of the panel (it also offers it next to an old passkey that "Sign out everywhere" lists): "Email me a code
(***@e***.com)", "Use my passkey" (the ordinary passkey sign-in; the page checks that `me.id` did not change), and "Continue with Google"
for each linked provider (which leaves the page: the Account panel opens again with "Confirmed. To delete my account, press it again").
The code field has `inputmode="numeric"` and `autocomplete="one-time-code"`; the page polls `POST /email/poll` every 4 seconds so a link
approved on a phone finishes the step on the laptop. The download of all data became a button that first asks
`GET /api/v1/me/recent-sign-in`, because a browser would show the 403 JSON as a page. Screenshots at 1400 and 390 px:
`docs/screenshots/confirm-its-you-*.jpg`.

**Alternatives considered** (as proposed; B chosen by the owner, with the e-mailed code added):
| Option | For | Against |
|---|---|---|
| A. Nothing (the 24-hour list alone) | no friction | the copied session can still add a method, export and delete |
| **B. 10 minutes, the actions above (built)** | a copied session does nothing lasting once ten minutes have passed since the last sign-in; no new login route | one extra step for rare actions; a copy taken in the 10 minutes after a sign-in works for those 10 minutes |
| C. 60 minutes | fewer prompts | a copy from a normal sitting is almost always inside it |
| D. Every time for delete and export | strongest for the irreversible | a step before every delete or export the person just chose |
| E. Shorter cookie lifetime | one line | does nothing inside the window, signs everybody out weekly |
| F. Mail the person when a method is added | tells them without looking | a passkey-only account has no address; a second mail type (the code mail is the first, and only on request) |

**What this resolves, and what it does not, of the residual risks of the 24-hour rule (#407):** *the patient attacker* (a method added at
time 0 and left for a day): reduced, not resolved. A copied cookie now needs a recent sign-in to add a passkey or link a provider, so the
attacker must have copied it inside the 10 minutes after the owner signed in; before, any copy did. *An old provider cannot be unlinked*:
**not resolved** (the relaxation was withdrawn, above). *An account younger than 24 hours removes nothing*: unchanged. **Still open, found
by review:** (1) creating a share (`POST /api/v1/shares`, an invite link a person accepts from their own account) needs only a session, and
"Sign out everywhere" does not revoke shares, so a stale copy can share the collection or a deck with itself and the owner finds it only in
the Shares list; (2) the export is gated, but the collection, decks and value history can all be read through the ordinary API with the same
stale cookie, so the check protects deletion, tokens, connecting apps, the sign-in methods and the single ZIP, not the privacy of the collection
from a copied cookie; (3) "Sign out everywhere", and removing a method added in the last 24 hours, are free of the check on purpose, so a stale
copy can unlink a provider or remove a passkey the owner added today (never the last old method), and `DELETE /me/sign-in-methods/recent`
signs the owner's other browsers out: a nuisance, never a way in. The owner's list for this decision did not include (1) or (2); a follow-up
issue holds them.

**Residual risks**
- A cookie copied inside the 10 minutes after a sign-in works for those 10 minutes (including adding a method and, a day later, signing
  in with it; the last old method still cannot be removed, and an old provider cannot be unlinked).
- The cookie is not bound to a device, so it can be replayed from anywhere; binding it to a client key (like DPoP) is a larger change and
  is not proposed.
- Anyone who can read the account's mailbox can confirm.
- A person who approves a link or reads out a code for a request they did not make confirms the attacker's browser.
- The Vault-wide daily mail cap can be exhausted by someone with many accounts, for a day.
- A stale copy of the cookie can use up the account's three sends an hour (and so send the owner "someone asked" mails); see the caps above.
- Resend's twin has not been compared with the live service, and Resend's delivery is outside our control (a late or lost mail leaves the
  passkey and provider paths).

**Cost for people.** A passkey touch, a trip to the provider (a few seconds) or a code from the mailbox, at most once per ten minutes, only
for delete, export, adding or removing an older sign-in method, linking a provider and creating a token; everyone with a cookie from before the
release meets it once. One person is worse off on purpose: someone who still has a session but no longer has any of their sign-in methods or
mailbox cannot add a passkey from it any more, as they could before.

**Tests** (`tests/test_recent_signin.py`, `tests/test_email.py`, `tests/test_recent_sign_in_methods.py`, `tests/test_passkeys.py`): for every
protected route a stale session is refused (403, code, nothing changed) and a fresh one accepted; the window expires and is the setting;
a cookie without a believable time is stale; a link does not renew, a known sign-in does; a personal access token, a reviewer session
and an app session behave as above; the code is mailed to the twin and read from its record; hashed; single use; five tries; ten minutes;
three an hour and the daily cap; bound to the browser and the account; dead after "Sign out everywhere"; the link is prefetch-safe, can
be approved elsewhere and once; no address and no sender fall back to the passkey and provider paths; a failed send costs nothing;
retention and erasure; nothing leaves the twin universe. With the check removed (`require_recent`) or the binding removed (`session_digest`
fixed), the tests above fail.

**Owner steps, after the merge** (nothing is sent until the key exists): create a Resend account, add and verify the domain
`mtgvault.cards` (DNS records at Vercel), create an API key and save it as `RESEND_API_KEY` in the Vercel project; sign Resend's DPA
(`docs/gdpr.md`); run `tests/conformance/test_resend_live.py` once and fix the twin where it differs; **for Apple private-relay
addresses, register the sending domain (the domain of `EMAIL_FROM`) in the Apple developer account under Certificates, Identifiers & Profiles,
Services, "Sign in with Apple for Email Communication", and then set `APPLE_RELAY_REGISTERED=1`**; until then those addresses are told "not
sent" and use a passkey or provider.

## Where to buy settings (#212, 2026-10-09)

The "Where to buy" menu keeps one small per-person record, `buy_settings`: a country the person chose and up to three shops they typed
(`vault/buy_links.py`, `vault/api/buy_api.py`). It touches `vault/privacy.py` only to add that table to erasure and export. What it
exposes, and what stops misuse:

- **A typed address is a link, never a request.** The server stores the text and renders it; nothing fetches it, so there is no SSRF
  and no way to make the Vault call a host (a test with the twin universe asserts no outbound call is made when settings are saved and
  menus built, and `tests/test_no_shop_calls.py` records a call to any of these hosts as an escape). The shape is checked on save:
  `https` only (never `http:`, `javascript:`, `data:` or a protocol-relative address), a real host name with a dot and not an IP
  address, no user name, password or non-standard port, at most 300 characters, no spaces or control characters, and `{card}` (the
  one place the card's name goes) only in the path or query, never in the host, so a card name can never choose where a link goes. The
  card's name is percent-encoded (`quote_plus`), so it cannot add a parameter or end the path. React renders the link; every external link
  has `target="_blank" rel="noopener noreferrer"`; an address is never a redirect target on the Vault's own domain.
- **Only the person writes it.** `GET`, `PUT` and `DELETE /me/buy-settings` use `account_user`: a personal token or a connected app gets 403
  (`tests/test_buy_api.py`), and there is no MCP tool that writes the settings, so a prompt-injected assistant cannot plant a phishing
  link in a menu or change the country. Another person's settings have no id to guess (the record is keyed by the caller); the menu of
  one person never shows another's stores (`test_each_person_has_their_own_settings`).
- **Where the person is.** The country is chosen, never derived: the server reads no IP address, `Accept-Language` or geo header for this (a
  test sends them and checks the answer is unchanged, and a source scan forbids them in the two modules). The browser orders the shops
  from its own language while no country is saved and tells the server nothing; there is no geolocation call, no place typed in the
  Vault and nothing in browser storage (`tests/test_buy_menu_page.py`, `tests/js/buy.test.mjs`). Residual: a connected assistant with
  read scope that calls `where_to_buy` sees the person's country and the stores they typed (it is what puts them first). That is coarse
  (a country, shop names the person chose to enter), the person controls whether any is set, and it is a read of their own data like
  the collection tools.
- **Storage and lifetime.** The record holds only `country`, `stores` and `updated_at`; it is in the data map and export
  (`buy_settings.json`) and erased with the account, and a `PUT` stores no settings in its idempotency record (only `{"saved": true}`; a replay is rebuilt from the live row, so "Remove" leaves nothing behind), "Remove" also deletes the person's stored `PUT` records, and the daily retention job deletes every stored answer past its 24 hours (found by the security review of this change: the 24 hours used to be enforced only when the same person made another keyed request).
  Writes are limited to 30 a minute per person.

## The upload link for a read-only connection (vault-wxy.6, 2026-10-11)

A person attached a collection file in ChatGPT, whose connection to the Vault was read-only (the default: the consent's Write
box is unticked), and the assistant could not offer the upload link: `start_collection_upload` was a write tool, so a read-only
connection neither saw it in `tools/list` nor could call it (`POST /api/v1/uploads` answered 403;
`tests/test_upload_read_only.py` fails on the code before this change). `get_staged_upload` was already a read. This change lets
**any connection, a read-only one included, start an upload link**: `Tool.any_scope` lists `start_collection_upload` for every
scope (it keeps `write=True`, so its annotations still say it is not read-only), and `POST /api/v1/uploads` is in
`READ_ONLY_POSTS` (`vault/app.py`; exact path, so `POST /api/v1/uploads/{id}/apply` is not). It touches one file of section 6's
list (`vault/oauth_routes.py`, one sentence of the consent text, below). Nothing else a read-only connection may do changes, and it still gets nothing that imports.

What a read-only caller can now cause, and what stops it (all in `vault/uploads.py`; the limits were already there; the review of 2026-10-11 added an allowance on the page, per link):

- **A row, bounded.** One `staged_uploads` row: the person's id, the hash of a random ticket, an expiry (one hour) and, if the caller
  names one, the person's own bucket (another person's is the same 404). At most 5 are open per person (a sixth replaces the oldest one that holds no file, see below; 409 only when all five hold one), at most
  10 are started a minute per person (429), expired rows are deleted by the next start and by the daily retention job
  (`tests/test_staged_upload.py`, `tests/test_upload_read_only.py::test_a_read_only_app_can_rotate_the_persons_waiting_links_but_not_read_or_import_what_they_hold`, `test_a_read_only_connection_names_only_the_persons_own_buckets`).
- **Storage, bounded and not new.** A file is stored only by `POST /upload`, which needs the ticket, which the tool answer gives the
  caller as it gives a write caller. One file per link (a new one replaces it), 20 MB at most (413, nothing kept:
  `test_a_link_started_by_a_read_only_connection_takes_no_more_than_twenty_megabytes`), so at most 5 x 20 MB for one hour per person,
  the same ceiling a write connection has. The cost is the person's own allowance, not the server's.
- **It imports nothing and changes nothing in the collection or decks.** The file waits. The preview (`GET /uploads/{id}`, a read) compares it with the collection, which
  a read connection can already read in full, so it shows the caller nothing new. The import is `POST /uploads/{id}/apply`: write
  scope, only for the `content_hash` that was previewed (#352), and a read-only connection is refused with 403
  (`test_a_read_only_connection_stages_and_previews_but_cannot_confirm`, also for a read-only personal token). `import_collection_csv`
  and every other write tool stay unlisted and refused.
- **No content is shown to the person by the app.** The upload page the person opens shows a fixed form and, for the person's own
  buckets, their escaped names. The "File received" summary is the answer to the upload itself, shown to whoever posted the file.
  An app that posts its own file with the ticket therefore cannot make the Vault show the person text of its choosing.
- **Same answers for either grant.** The preview, with the conflicts named, is identical for a read-only and a write connection
  (`test_the_preview_names_the_conflicts_the_same_for_a_read_only_and_a_write_connection`), and another person's upload is the same
  404 (`test_another_person_cannot_read_or_apply_an_upload_a_read_only_app_started`).
- **A tool a person is told about.** The consent screen's Read line (`SCOPE_TEXT["read"]` in `vault/oauth_routes.py`) now says so:
  "It can also give you a one-hour link to upload a collection file, and read what importing that file would change. Importing it
  needs Write." (`tests/test_upload_read_only.py::test_the_consent_screen_tells_a_person_that_read_includes_the_upload_link`.)
  This is the one file of section 6's list that this change touches, only for that sentence.
- **An allowance of its own on the page (review of 2026-10-11, corrected after the re-review of `56939d5`).** `POST /upload` is the
  one route that parses and matches up to 20 MB for a ticket's holder, and it had no limit; the first fix counted it against the
  person's `import` counter. That counter counts refused tries too, so an app the person had since disconnected, still holding its
  ticket, could loop on the page and keep the person's own `/imports` at 429 for as long as it looped. The page now has a counter
  per link (`import_limit` a minute for each link, 429 page with `Retry-After`, a different counter key kind from the person's), so
  a loop on a link costs the person that link only (they ask for a new one); the person's `/imports` is not touched. A made-up ticket
  is refused first and counted nowhere. A person is bounded by 5 links x `import_limit` a minute on the page. Tests:
  `test_a_loop_on_the_upload_page_does_not_touch_the_persons_imports`,
  `test_a_made_up_ticket_leaves_every_counter_untouched`, `test_the_upload_page_has_its_own_allowance_per_link`.
- **The allow-list is method-aware (review of 2026-10-11).** `READ_ONLY_POSTS` exempts only a `POST` at exactly those paths; a
  `PUT`, `PATCH` or `DELETE` at one of them, a trailing slash or a query-string variant that is another route is still a write
  (`test_the_allow_list_exempts_only_a_post_at_exactly_that_path`), and a sweep of every unsafe-method route in the app
  shows a read-only OAuth token gets 403 on all of them but the one exemption this change makes
  (`test_a_read_only_oauth_token_is_refused_on_every_unsafe_route_but_the_upload_link`).
- **Reached only through the MCP server.** An OAuth access token is accepted only on `/api/mcp` and the in-process calls its tools
  make; a direct `POST /api/v1/uploads` with an OAuth bearer is 401 (`test_an_oauth_token_is_not_accepted_on_the_rest_route_directly`).

Residual risks, stated:

- A staged file belongs to the person, not to the app that started the link (the id is per person). A write-scope assistant that is
  told an upload id (by the person, or by an injected instruction) could confirm a file that a read-only app staged. It still has to
  show the preview, send the preview's `content_hash` and wait for the person's yes, like every import; no tool lists another app's
  uploads.
- **Links are not attributed to an app, cannot be listed or revoked, and outlive a disconnect (review of 2026-10-11, accepted
  for now).** A staged upload records the person and the bucket, not the OAuth client that asked for it; there is no list of open
  links for the person or the assistant and no cancel; disconnecting an app does not remove the links it started, which work until
  they expire (one hour). The link is also not single-use: it works until it expires and a second upload replaces the staged file;
  a retry of `start_collection_upload` (an `Idempotency-Key` is not honoured on that route) makes another link and uses a slot.
  Attribution, a list, a revoke and removal on disconnect are bead vault-wxy.13 (P1); the upload page cannot yet say which app
  asked for the file for the same reason.
- A read-only app can use the ten starts a minute (a budget per person, shared by every app they connected), so a write connection's
  `start_collection_upload` answers 429 until the minute turns (`test_a_read_only_app_can_use_up_the_persons_start_budget_and_the_denial_is_plain`). It could
  already fill the five links; since vault-73p it can instead rotate the waiting ones (next section). Both are the reason for vault-wxy.13.
- `POST /upload` has no body-size limit before the ticket is checked (read from the code, not measured): the framework reads the multipart body before the handler
  rejects a made-up ticket. That is independent of scopes (the route takes no credential) and unchanged here; it is tracked as its own
  bead (vault-wxy.8).


### The sixth link replaces the oldest waiting one (vault-73p, 2026-10-12)

The vault-wxy.7.1 batch ran 8 unnamed phrasings headless and used up the account's five open links; 2 of 8 answers were the 409. A
new chat has no memory of the link it was given, and only a hash of the ticket is stored, so the link cannot be handed back: the
player who retried was stuck for up to an hour. Now, when the person already has five open links, `start_collection_upload`
(`POST /api/v1/uploads`) **removes the oldest link that has no file uploaded through it** and issues the new one; the answer carries
`replaced_upload_id` and says that link no longer works. A link with a file uploaded through it is never removed. When all five
have one, the answer is a 409 that says so, lists them (id, size, upload time and end of the link, never the file's name) and points
to `get_staged_upload` and `confirm_staged_upload`; there is still no discard (vault-wxy.13).

**What this does not fix, said plainly (review of b833d49, 2026-10-12): the lockout.** A file "uploaded through a link" is not
necessarily the person's: a connected app that made all five links holds all five tickets and can `POST /upload` its own file to
each. The links then all have a file, none can be replaced, and the person's next start is a 409 until the links end, up to an hour
from when each was made, and the person cannot discard them. This change replaces "fill" with "rotate" for links that have no file;
the lockout remains for as long as files are held, and it is the same denial the app could cause before. The 409 therefore does not
say "the person uploaded": it says a file was uploaded through each link, by the person or by the app that made it, that none is
imported, and to import one only if the person recognises it. The discard is vault-wxy.13 (with attribution, list and revoke). A
second consequence: with four files held there is one free slot, so two starts at about the same time both get a link and the
second replaces the first (the newest request owns the slot); the first link's holder finds it expired.

What a malicious connected read-only app can and cannot do with this:

- **Cannot read or import anything new.** It gets the id of the link it replaced, which is a row of the person's own, no ticket and
  no file: the id is the same number `get_staged_upload` would take from any read connection. The apply is write scope and needs the
  previewed `content_hash`; a read-only app is refused there (`test_a_read_only_token_and_a_write_token_get_the_same_replacement`,
  `test_a_read_only_app_can_rotate_the_persons_waiting_links_but_not_read_or_import_what_they_hold`).
- **Can rotate the person's waiting links, quickly.** The per-person start counter (10 a minute) is unchanged and still counts a
  start that replaces a link (`test_the_ten_starts_a_minute_limit_still_counts_when_a_link_is_replaced`). The review of b833d49
  measured what that allows: a hostile app makes the person's newest waiting link stop working in 5 starts (about 30 seconds), and
  can repeat it as often as it likes; because the counter is a fixed one-minute window, up to 20 starts fit in about 2 seconds
  across a window boundary. So "the oldest waiting link is the one replaced" is a protection against a benign assistant that asks
  again, not against a hostile one. What it costs the person: they ask their assistant for a new link; no data is lost, because a link
  with a file uploaded through it is never replaced. Before, the same app could fill the five slots and make the person's own
  assistant wait; now it can make a link the person was given stop working. Neither reads or imports anything.
- **Can strand a person who has a link's page open.** The person opened a link and is choosing a file when the link is replaced:
  their POST finds no link. They get the ordinary "This upload link has expired, ask your assistant for a new one" page, never
  "File received" (`test_a_file_that_arrives_after_its_link_was_replaced_is_refused_as_expired_and_not_reported_received`). A link
  that has begun receiving a file is no longer waiting. A minimum age or a last-opened time before a link may be replaced was
  considered and left out here (it brings the 409 back for a person who asks five times in a minute); it is logged under
  vault-wxy.13.
- **Cannot take another person's links**: the statement is made under the owner's id (`test_replacing_does_not_touch_another_persons_links`).
- **Cannot put its words into the 409.** The file name is chosen by whoever holds a link's ticket, and the 409 is read by the
  person's assistants. It carries no file name at all, only the id, the size and the times; the name is in the structured answer of
  `get_staged_upload`, as data (`test_a_hostile_file_name_never_reaches_the_409`,
  `test_another_persons_files_never_appear_in_the_409`).

The race, and whether the replacement is a transaction. Each start takes `pg_advisory_xact_lock(7301, user id)` after the
per-person counter and before it reads anything, so one person's starts are one at a time to the end of their transaction. (Without
it, 6 simultaneous starts at 3 open links all counted 3 and left 6 rows, and two starts with one waiting link and four held files
could both find the same waiting row, one then answering a false 409: the review reported both. The lock is PostgreSQL's, which is
what the Vault runs on. The wait for it is bounded by the Vault's lock timeout, `SET LOCAL lock_timeout` from `vault.locks` (5 s), and past it the start answers the usual 503 with Retry-After: `test_a_stalled_start_of_the_same_person_is_a_bounded_wait_and_a_503_not_a_hang`.) The replacement is then one `DELETE ... WHERE id = (SELECT ... ORDER BY expires_at, id LIMIT 1 FOR UPDATE
SKIP LOCKED) AND content IS NULL ... RETURNING id` in the same transaction as the insert of the new link, committed once: if the
insert fails the old link is still there (`test_the_replacement_and_the_new_link_are_one_transaction`), and a bad `bucket_id` replaces
nothing (`test_a_bad_bucket_id_replaces_nothing`). The count is five or fewer after any number of simultaneous starts
(`test_simultaneous_starts_below_the_cap_never_leave_more_than_five_links`,
`test_two_simultaneous_starts_at_one_waiting_link_and_four_held_files_both_get_a_link`). The `content IS NULL` test is made on the row
under its lock, so the three orders in which a file and a replacement can meet all end safely:
1. the file was committed first: the row is no longer waiting and is skipped (`test_a_file_that_was_committed_before_the_replacement_keeps_its_link`);
2. the file's write is in flight (row locked, not committed): the row is skipped and the next oldest waiting one is taken, and the
   file's commit stands (`test_a_file_being_written_while_the_sixth_link_is_made_keeps_its_link`, time-bounded so a replacement that
   waits for the lock fails the test instead of hanging it);
3. the replacement was committed first and the file's write comes after: the file is written with one conditional `UPDATE ... WHERE
   id = :id AND expires_at > now()` and, if no row matched, it is not kept and the page answers as for an expired link; a link that
   expired while the file was being read is refused the same way
   (`test_a_file_is_not_kept_for_a_link_that_expired_while_it_was_being_read`). This was an unconditional attribute write that would
   have raised on a missing row; the page also reads the row's plain values once, before its first commit, so it never reads a row
   that has gone.

No oracle: the replaced row is deleted, as expired ones are, so `/uploads/{id}` and the page answer exactly as for an expired or
made-up ticket (`test_a_sixth_link_replaces_the_oldest_waiting_one_and_the_old_one_answers_as_expired`). The 409's list is the
person's own links to their own authenticated caller, never another person's (`test_another_persons_files_never_appear_in_the_409`).
Rate limits are unchanged: the 10 starts a minute are counted before anything is replaced (a refused start replaces nothing), and
the page keeps its allowance per link (`test_the_page_allowance_per_link_is_unchanged`).

## The compact tool list for the Codex family (2026-10-11, vault-wxy.5)

What changed: a client whose `initialize` names it `codex-mcp-client` is shown a short list (core tools, `vault_tools`,
`vault_call`, and `vault_write` for a token that may write) instead of all 81 tools, because Codex drops a plugin's tools past
a size budget. The choice is carried in the existing signed `Mcp-Session-Id` (one more bit; nothing is stored).

- **What the session id grants:** nothing. It carries a presentation bit (two bits: MCP Apps views, compact list), signed under
  the server secret; it is not bound to a person. A replayed or copied id gives the caller their own data in a compact list. A
  client that spoofs the name only changes which list it is shown; scopes are checked per token on every call.
- **`vault_call` / `vault_write` use the caller's own credentials:** they build the same in-process request as a direct call
  (`call_api`), forwarding the caller's `Authorization`, so the REST layer's scope check and every ownership check apply
  unchanged. A read-only token running a write tool through them gets the same refusal as a direct call
  (`tests/test_mcp_profile.py`).
- **Two runners, not one:** a host's "always allow" attaches to a tool name. `vault_call` is read-only (`readOnlyHint`) and
  refuses write tools; `vault_write` is the one that changes data, is listed only to a token that may write, and carries
  `destructiveHint` true because it can run tools that replace or merge (`update_deck`, `set_*_metadata`, `rename_tag`).
  Approving reads never pre-approves changes, and approving `vault_write` is asked as a destructive tool is.
- **The refusal of destructive tools is presentation policy, not enforcement.** The runners will not run a tool marked
  `destructive` (deletes or revokes), a core tool (called by name) or each other, because a host that sees one runner cannot
  show that tool's own warning. A direct `tools/call` of such a tool by name still works for a token that may write, as it
  did before; the API decides. `tests/test_mcp_profile.py` derives the destructive set from the catalog (`DELETE` methods,
  `delete_`/`reset_`/`untag_`/`stop_` names, write tools with a confirmation or update flag must be marked destructive or
  recorded as reviewed, with the reason).
- **Reviewed, not marked destructive** (the list, with reasons, is `REVIEWED_NOT_DESTRUCTIVE` in the test): `tag_cards` and
  `move_cards` (previewed, reversible); `import_deck_from_link` (`update` with `confirm` replaces the saved copy of that one
  deck) and `refresh_deck` (replaces it with the source's list, destructive) are **listed directly** so the host shows each
  one's own approval; `update_deck`, `rename_tag`, `rename_bucket`, `set_card_metadata` and `set_bucket_metadata` replace or
  merge the person's own data and sit behind `vault_write`, whose approval is destructive.
- **Residual:** a prompt-injected model with `vault_write` approved can run any non-destructive write the token's scope allows
  (save or update a deck, move cards, accept a share). The scope reach is the same as with the full tool list, but the
  per-tool approval is coarser: one "always allow vault_write" covers all of them, where the full list lets a person allow
  `create_bucket` and keep asking for `update_deck`. Mitigation: `vault_write` is marked destructive so the host asks, the
  replacing tools that matter most (`import_deck_from_link`, `refresh_deck`, the collection edits and their confirmations) are
  listed directly with their own hints, and a person who wants per-tool control can use a client that gets the full list.

## Signing out from the chat: `sign_out` (bead vault-wxy.3.1, 2026-10-11)

A host such as the ChatGPT desktop app gives a plugin's MCP server no sign-out control and keeps reusing its saved token, so a
person could switch Vault accounts only through Account, "Connected apps", "Disconnect" on the web. `sign_out` (MCP) over
`GET` / `POST /api/v1/agent/sign-out` ends the one connection the request comes from (`vault/oauth_server.py`
`grant_of_access`, `revoke_user_grant`; `vault/api/catalog_api.py`). What it changes, and what stops misuse:

- **Only the caller's own grant.** The grant is found by the SHA-256 of the presented access token **and** the authenticated person's
  id, and only that row (with its retired refresh tokens) is deleted, as RFC 7009 revocation by the client already allows. The
  person's other connections (the same app on another device, other apps) stay (M `tests/test_sign_out.py::test_confirm_ends_only_this_connection_and_another_device_keeps_working`).
  There is no argument naming a grant, so a prompt-injected call cannot end anyone else's connection or another app of the same person.
- **Any scope.** A read-only connection may end itself: the `POST` is in `READ_ONLY_POSTS` and the tool is listed for read-only callers
  (`Tool.any_scope`). Ending your own credential grants nothing; it is the same power RFC 7009 gives every client
  (M `test_a_read_only_connection_lists_the_tool_as_destructive_and_can_sign_out`).
- **Nothing else is ended here.** A personal access token, the native app's own sign-in and a web session get 409 with where to end
  them (Account, "Agents & API"; the app; "Sign out"), and keep working (M `test_a_personal_access_token_is_refused_and_keeps_working`,
  `test_the_native_apps_own_sign_in_is_refused_and_keeps_working`, `test_a_web_session_is_refused_and_stays_signed_in`). A cross-site form POST with the cookie therefore revokes nothing.
- **Audience unchanged.** An OAuth access token is still accepted only on `/api/mcp` and its in-process tool calls: the REST route called
  directly with one answers 401 (M `test_the_route_called_directly_with_an_oauth_token_is_refused`).
- **Preview first, and the host asks.** Without `confirm` it only reads; the tool carries `destructiveHint: true` and
  `readOnlyHint: false`, and the server instructions and the collection-analyst skill say to confirm only after the person's yes.
  The worst an injected `confirm: true` does is sign the person out of that app: no data changes, and signing in again restores it.
- **After revocation** the old access token gets the MCP 401 with `WWW-Authenticate: Bearer realm="the-vault", error="invalid_token",
  resource_metadata="..."`, and its refresh token gets `invalid_grant`, so the host starts the authorization again (M `test_the_old_token_then_gets_401_with_the_resource_metadata_and_cannot_refresh`).
  What the person then meets depends on the age of this browser's Vault sign-in: within the recent-sign-in window (#347) the consent page, which
  offers "Use a different account"; older (the usual case: the cookie lives 30 days) the sign-in page, which offers it as links (the last bullets).
- **The email is masked.** The preview names the account by its name and the email with all but the first letter hidden (`d***@example.com`),
  so a connected app that calls the tool is not handed the whole address; it is enough for the person to tell their accounts apart
  (M `test_an_email_is_masked_to_its_first_letter_and_domain`, `test_the_preview_says_who_which_app_and_what_happens_and_changes_nothing`).
  A local part of one letter is shown whole (`a***@b.example` reveals `a`): for such an address the mask hides nothing, and the domain is always shown.
- **Revoked by id, so a refresh cannot misreport it.** The connection found for the request is revoked by its id and the person's
  id, not looked up again by an access token a refresh may just have replaced (M `test_a_token_refresh_between_the_lookup_and_the_revoke_still_signs_the_connection_out`);
  a connection ended meanwhile answers "already signed out", never the native app's message (M `test_a_connection_ended_meanwhile_says_already_signed_out_*`).
  The preview shows the scopes the current token carries (`access_scopes`), which a refresh may have narrowed (M `test_the_preview_shows_what_this_token_carries_*`).
  A token a refresh replaced between the request's authentication and the lookup finds no connection by its hash, and answers the same
  "already signed out (or its token was just renewed)... ask again" (`ALREADY_SIGNED_OUT`, 409): nothing is revoked, and asking again finds the
  connection under its new token (M `test_a_connection_ended_meanwhile_says_already_signed_out_*`).
- **The compact tool list (Codex family) lists it directly.** `sign_out` is one of the core tools, so the ChatGPT desktop app, where this
  was asked for, sees it with its own `destructiveHint`; `vault_call` and `vault_write` refuse it ("listed directly") like every core tool, so
  a host's "always allow" for `vault_write` never covers signing out. The list has 30 tools and 29,549 bytes with the view links (28,970
  without), against the guards of 30 and 30,000 (M `tests/test_mcp_profile.py::test_the_compact_list_stays_within_both_guards_with_and_without_the_views`,
  `test_sign_out_is_listed_directly_to_every_sign_in_and_the_runners_will_not_run_it`; M `tests/test_sign_out.py::test_a_codex_client_with_the_compact_list_is_offered_sign_out_and_it_ends_only_its_connection`).
- **A second call, or two at once.** The first call ends the connection, so the same token's second call is the MCP 401 with the resource
  metadata, and a call that loses a race answers "already signed out"; neither touches another connection (M
  `test_a_second_sign_out_with_the_same_token_is_a_plain_401_and_nothing_else_is_touched`,
  `test_a_connection_removed_between_the_lookup_and_the_revoke_answers_already_signed_out`).
- **Switching account from a browser whose Vault sign-in is older than ten minutes (found by the review of the first version of this
  change, which only worked through the developer sign-in).** Signing out ends the app's token, not the browser's Vault session, and that
  session is usually older than the recent-sign-in window (#347). `/oauth/authorize` already treats such a session as signed out and shows the
  sign-in page, but the old cookie stayed, so signing in at the provider as another person hit the provider callback's link check
  (current account, other owner, not fresh) and ended at `/?link_error=recent_sign_in_required` with the pending request lost; signing
  in as the same person proceeded silently. Now:
  - **The provider's answer replaces the old sign-in, only there.** `vault/auth.py` `callback`: when the session's sign-in is stale **and** the
    session carries a live `oauth_pending` request (set by `/oauth/authorize`, kept only by a login started with `continue=oauth`, ten
    minutes) **and** the login was started from a link of the sign-in page that carries its **one-time token** (`pending=`, rendered by
    `/oauth/authorize` into every provider link, spent by the first login that presents it, the last three kept for other tabs of the same
    request; `login` records it as `ok` in the pending request) **and** this is not an app (`app_flow`) or reviewer sign-in, the old `uid`/`sk`/`auth_at` are dropped before the sign-in, so it is
    a sign-in as whoever the provider authenticated, never a link or a claim. The pending request is then resumed (consent for that person).
    Every other path is unchanged: a stale cookie signing in at a provider without the pending request still gets `recent_sign_in_required`
    (M `test_the_pending_request_cannot_be_used_to_link_a_provider_from_another_page`, `tests/test_recent_signin.py`).
  - **What this can and cannot do.** A copied stale cookie that goes through the consent flow ends up signed in as the provider account it
    authenticated with (its own account, or a new one), not as the cookie's owner and with nothing linked to the owner's account; it learns
    nothing it did not have (M `test_a_copied_stale_cookie_in_the_consent_flow_signs_in_as_the_provider_account_and_links_nothing`). It is
    the same outcome as signing in from an empty browser. The provider's `state` and `nonce` still bind the answer to this browser.
  - **No logout or switch by link.** `GET /oauth/authorize` clears nothing (an earlier design cleared the session there, which would let any
    page that can send a person's browser to the authorization URL of a registered client end their Vault sign-in as a nuisance), and the
    provider's answer replaces the old sign-in only for a login started from the sign-in page's own link (the review of PR 471: a page on
    another site can prime the pending request with a GET and then send the browser to the login URL, and a provider with a session of its
    own may answer without asking, so the pending request alone is not a user action). The token is in the page's HTML, which a page on
    another origin cannot read; without it, or with a spent or wrong one, the answer meets the unchanged #347 refusal (M
    `test_a_link_to_the_authorization_page_does_not_end_the_browsers_sign_in`,
    `test_a_login_link_from_another_site_cannot_replace_the_sign_in_even_if_the_provider_answers_silently`, `test_the_token_of_the_sign_in_page_works_once`).
    A person who opens the sign-in page in a second tab keeps both working (the last three tokens), an older third tab asks again.
    The one place the session is cleared on purpose is the consent page's "Use a different account" button, a POST that needs the page's
    one-time nonce and a fresh sign-in, as before.
  - **The sign-in page offers a different account.** For a browser with an older sign-in the page says so and links "use a different
    account" per provider that supports it (`account=choose`); after "Use a different account" on the consent page every provider button
    carries it. `GET /api/auth/login/{provider}?continue=oauth&account=choose` adds `prompt=select_account` for Google and Microsoft (the
    documented value; the twins reject any other prompt), so a provider with one signed-in account does not silently continue as the
    account being left. It is sent only on request and only for the OAuth sign-in (M `test_only_the_oauth_sign_in_asks_for_the_account_chooser`),
    so the common reconnect as the same person costs no extra click. Apple and Facebook have no such prompt: their sign-in is unchanged.
    The page does not name the earlier account (a shared computer's next user would see it).
  - **Residual.** The provider decides which accounts its chooser lists. A person with a single Google account who wants a second Vault
    account must sign in to the provider with it (the chooser's "Use another account"). The tests go through the Google twin, which
    answers with the account it is told to: the chooser's behaviour at Google is the documented one, not exercised.
  - **The preview does not lend a name.** A client that registered itself picks its own name, so the preview says `Unverified app: <name>`
    (and `verified: false`), as the consent screen does; an app identified by its web address is named with that address (M
    `tests/test_sign_out_preview.py::test_a_self_registered_client_is_called_unverified_in_the_preview_and_after`). "Other connections"
    counts only connections whose refresh token has not expired, without deleting the expired ones (M
    `test_a_connection_whose_refresh_token_ran_out_is_not_counted_and_the_preview_deletes_nothing`).
  - **Conformance.** `prompt=select_account` is a value sent to two outside services: `tests/conformance/test_conformance.py::
    test_the_account_chooser_prompt_is_not_refused_by_the_real_authorization_endpoint` (nightly, `TWINS_LIVE=1`) checks that the real
    Google and Microsoft endpoints answer the request with it as they do without it (an unknown client, so only the client can be
    complained about); the twins' own rule on prompt values is `_extra_checks`.
  - **The conditions are each pinned** (`tests/test_sign_out_replace_rule.py`): a recent sign-in with a pending request still links another
    provider account as before; an app flow (`app_redirect_uri`) keeps the #347 answer and goes back to the app with the error; a reviewer's
    demo session switches accounts as before. Dropping the freshness or the app-flow condition fails its test; the reviewer condition is
    belt and braces (`sign_in` already ignores a reviewer session for linking, so dropping it changes no outcome: a test cannot tell).
## A file dropped in the chat (vault-0n9.3, 2026-10-12)

What changed: the person attaches a collection CSV in ChatGPT and says to update the collection. The new tool
`import_collection_file` declares `_meta["openai/fileParams"]: ["file"]`, so ChatGPT hands it `{download_url, file_id, mime_type?,
file_name?}`; the Vault **fetches `download_url`**, stages the bytes as the person's own `StagedUpload`, and answers with the preview
and the upload id. It changes nothing in the collection: the import is `confirm_staged_upload` (write scope, only for the preview's
`content_hash`), unchanged. Files: `vault/file_fetch.py` (the fetch), `vault/intake.py` (the route
`POST /api/v1/uploads/from-file`, in `READ_ONLY_POSTS` at exactly that path), `vault/api/mcp.py` (the tool, `any_scope` like
`start_collection_upload`, so a read-only connection may preview and cannot import).

**What the fetch can reach.** An address chosen by the caller of the tool: the model, the host app, or text a prompt injection put in
the conversation. That is an SSRF surface, so every step is bounded (C = `tests/test_file_intake.py`):

- **https only**, port 443, no credentials, no fragment, no address literal (decimal, hex, IPv6, IPv4-mapped), no space, backslash or
  control character, no trailing dot, ASCII host names only, 4096 characters at most (C `test_an_address_that_could_aim_the_fetch_is_refused`).
- **An allow-list of host names**, `FILE_INTAKE_HOSTS` (each entry is a domain and its subdomains; `Settings.check` refuses a
  single label). **Default: `oaiusercontent.com`, `openai.com`, `chatgpt.com`.** Why that default: the only host name any OpenAI page
  gives for a file download link is `files.oaiusercontent.com` (GPT Actions, "valid for five minutes"); the Apps SDK reference
  (developers.openai.com/plugins/reference, read 2026-10-12) says nothing about the host, lifetime or redirects of `download_url`.
  The first real run decides the real host, and a refused host costs a whole iteration, so OpenAI's own domains and their subdomains
  are allowed and the **resolved address is what protects** (next point). Residual: a subdomain of an allowed domain that OpenAI lets
  a third party serve content from would be fetchable; what is fetched is only ever parsed as a CSV by the existing importer.
  A refused host is logged **by host name only** (`file_intake` event, `host`), never with its path or query, which carry the token,
  and only its **last three labels** (`*.files.example.com` for `<token>.files.example.com`), so a secret in the first labels of a
  name stays out of the log (C `test_a_host_name_is_logged_by_its_last_three_labels_so_a_secret_in_the_first_ones_stays_out`).
- **The name is resolved here and every address must be public** (`oauth_clients.public_address`: private, loopback, link-local
  including the cloud metadata address, shared 100.64/10, multicast, reserved, unspecified, IPv4-mapped, NAT64 and 6to4 forms, Teredo;
  one bad address among good ones refuses the host), and **the connection goes to the address that was checked**, with the name kept
  for TLS (SNI) and the Host header: the name is resolved once per hop, so DNS rebinding between check and use does nothing (C
  `test_a_name_that_resolves_to_a_private_address_is_never_contacted`, `test_the_connection_goes_to_the_address_that_was_checked_and_the_name_is_resolved_once`).
- **At most 2 redirects**, each target checked by exactly the same rules (https, allow-list, public addresses, own DNS check; a
  relative `Location` is resolved against the address just fetched); any other redirect is refused (C
  `test_a_redirect_that_breaks_a_rule_is_not_followed`, `test_at_most_two_redirects_are_followed`,
  `test_a_relative_redirect_is_resolved_against_the_current_address_and_checked_again`). **This differs from the first draft of the
  brief ("no redirects")**, on purpose: the real host and its redirects are unknown before the first real run, a refused redirect
  would cost a whole iteration, and a hop to a host that is on the allow-list and resolves to public addresses is no wider than the
  first request.
- **Size and time.** At most `INTAKE_MAX_BYTES` (**5 MB**; the importer's own cap is 20 MB, see "Egress and time" for why the intake
  takes less) is read, in plain bytes (`Accept-Encoding: identity`; a
  compressed answer is refused), checked against `Content-Length` and while streaming; 15 seconds in total, as a wall clock the
  caller waits on (the fetch, including DNS, runs in a worker joined with a timeout), so a server that drips or never answers
  cannot hold the request (C size, drip and slow-resolver tests). At most 4 fetches at once per process; a fifth is told it is busy.
- **Content type is text-like** (`text/*` except `text/html`, `application/csv`, `application/vnd.ms-excel`); a generic
  `application/octet-stream` counts only when the host's own description says CSV or text (storage hosts answer octet-stream).
- **A bare GET**: no cookie, no authorization, no referrer, no proxy from the environment (C
  `test_the_fetch_is_a_bare_get_with_no_cookie_no_authorization_and_no_referrer`; the proxy is tested with the production client, no
  transport, a listener standing in for HTTPS_PROXY: `test_environment_proxies_are_ignored_by_the_real_client`).
- **The address is never logged and never returned.** `httpx` and `httpcore` log requests at INFO with the address, so their loggers
  are held at WARNING; exceptions from the client are reduced to their class name; answers are fixed sentences (C
  `test_the_address_never_reaches_the_log_or_an_answer_whatever_happens_to_it`). A non-address `file` value (the string
  `chat_upload` a phone app sends, a path, a missing key) is answered in one sentence and its **shape only** is logged: type, length
  and a masked prefix (only the known sentinel `chat_upload` as it is; anything else as its form); a path is logged by its root only
  (`C:`), never the rest, which may name the person.

**What a malicious file name or content can do.** The declared `file_name` is used only to label the staged file, with any path and
control, format (bidirectional overrides, zero-width), line-separator and unassigned characters removed, 255 characters; it is
still the app's own text and is treated as data wherever it is shown (it appears in the tool answer and in `list_imports`), and it
is never used to name a path on the server (C `test_the_filename_shown_for_a_staged_file_is_the_declared_name_without_a_path`).
A file that cannot be read is answered with a phrase the Vault wrote (not the library's message, which can quote a cell: C
`test_an_unreadable_answer_never_repeats_text_from_the_file`). The bytes are parsed by the existing
importer (UTF-8 check, 20 MB, the same row and column limits as an upload through the link), and shown to the person only through
the preview, whose text the Vault writes. A file holding an instruction to the assistant is data, as for any import.

**Rate and storage.** Every try counts against the person's import allowance (`IMPORT_LIMIT`, 10 a minute) on a counter of its own
(`file intake`), so a loop does not keep the person's own `/imports` at 429. A person holds **one** fetched file at a time (a new one
replaces the earlier, an identical one is the same row, so a host's duplicate call returns the same id); that is one 20 MB row beside
the five link rows already allowed (the link cap, the replacement of the oldest waiting link and the list of held
files count links only: C `test_a_dropped_file_is_not_one_of_the_five_links_and_is_not_listed_with_them`), expiring in one hour like a link, deleted by the daily retention job and when applied. Fetched
rows have no ticket: `ticket_hash` starts `intake-`, which no sha256 digest can equal, so `/upload` can never open one (C
`test_a_fetched_upload_has_no_link_the_upload_page_could_open`).

**Who can do what.** A read-only connection can drop a file and read the preview and cannot import it: the apply is
`POST /uploads/{id}/apply`, write scope, 403 (C `test_a_read_only_connection_can_drop_a_file_and_see_the_preview_but_cannot_import_it`).
A staged file belongs to the person, not to the app: another person's id is the same 404 for read and apply (C
`test_another_person_cannot_read_or_import_a_file_that_was_dropped_in_the_chat`); a bucket that is not the person's is a 404 before
anything is fetched. **A read-only connection can replace the pending fetched file of a write connection** (a person holds one
fetched file at a time): the write app's `confirm_staged_upload` with the old id then answers 404 and it drops the file again. It is
the same person's files, nothing is imported or changed in the collection, and the `content_hash` binding holds. The same residual as the link applies: a write-scope assistant told an upload id could confirm what a read-only
app staged, with the preview's `content_hash`, and the person's yes in the chat (text, not verified by the server).

**One at a time.** The replace-or-reuse decision is made under the per-person advisory lock the upload links use (5 s lock timeout,
then the usual 503 with Retry-After), so simultaneous drops cannot leave two fetched files or two ids for one file (C
`test_two_drops_at_once_leave_one_row_and_one_upload_id`).

**The twin.** `twins/openai_files.py` answers on `files.oaiusercontent.com` as OpenAI's GPT Actions documentation describes it (a
signed link, valid five minutes). It is the documentation's reading, **not a recording**: nothing real has been captured, so the
conformance check (`tests/conformance/test_openai_files_live.py`) is by hand and has not run; the log of the first real run is
its first evidence.

**What the person is told.** A read-only connection can now make the Vault fetch and keep a file for an hour, so the consent screen's Read line
(`SCOPE_TEXT["read"]` in `vault/oauth_routes.py`, the one file of section 6's list this change touches, only for that sentence) says so: it
can also "take a file you attach in the chat and keep it for an hour, and read what importing that file would change. Importing it needs
Write." (C `tests/test_upload_read_only.py::test_the_consent_screen_tells_a_person_that_read_includes_the_upload_link`.)

**What the log keeps of a value that is not a file** (review of PR #478): its type and length; a bare string as its first 12 characters
only when it is the one known sentinel `chat_upload` (ChatGPT mobile, issue #185), else as the form of those characters (letters
`a`/`A`, digits `9`), and a scheme the same way, so an identifier, a name or a token a host sends in place of a file is not written down; an object by the names of the keys a file object has and a count of the
others; a path by its root. (C `test_the_logged_shape_never_holds_a_value_that_could_be_a_token_or_a_name`.)

**Egress and time from a Vercel function.** The fetch is an outbound HTTPS request from the same function that answers the tool call
(60 seconds maximum, `vercel.json`): the fetch is capped at 15 seconds, leaving the rest for the preview. **The intake takes 5 MB, not the
importer's 20 MB** (review of PR #478): measured in a lane process, a 17.9 MB, 190,000-row file took 8.9 s and about 450 MB of memory for
one request, for any token, read-only included, 10 calls a minute per person; the upload page is cut near 4 MB by the hosting platform
in production, so 20 MB had never been processed there. Memory on Vercel is not measured. A larger file is answered `too_large`, naming
'Import CSV' on the website. No change to the function's permissions or secrets.

Residual risks, stated:

- The fetch has no wall-clock limit **inside** a worker that is blocked in a socket read: the caller is released at 15 seconds, the
  worker ends at its own read timeouts and gives its slot back then; a host that drips response headers can hold up to 4 slots of a
  process for as long as it drips. Only allow-listed hosts can do this.
- A host name with two or three labels is logged whole, and the first labels of a longer one are cut: a secret kept in a short
  host name (the apex a third party chose) would be logged. The address's path and query, where signed tokens are, never are.
- The allow-list includes subdomains of three OpenAI domains (above). It can be narrowed with `FILE_INTAKE_HOSTS` once the real host
  is known from the log (`file_intake` events, `outcome: staged` or `cannot_hand_over` with `host`).
- What ChatGPT really sends a remote plugin is not known before the first real run: the R1 probe on the desktop app handed a
  **local path** to a local server; mobile is reported to send the string `chat_upload`. Both are answered by the one-sentence
  refusal, and the evidence they leave is the logged shape.
