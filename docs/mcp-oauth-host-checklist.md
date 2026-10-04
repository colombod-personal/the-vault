# Real-host checklist: connecting ChatGPT and Claude.ai with OAuth

Issue #48. Everything in `tests/` runs against twins; nobody has yet tried the real hosts. A person
with a deployed Vault (a preview or production URL over **https**, with sign-in configured) runs this once
before the OAuth endpoints are announced. Tick each box in the issue, and write down what you saw.

Use a throwaway Vault account with a small imported collection. Do not use someone else's data.

## Before you start

- [ ] The deployment's `BASE_URL` is its public https address (on Vercel, leave it unset or set it to the real domain).
- [ ] `https://<host>/.well-known/oauth-protected-resource/api/mcp` answers JSON whose `resource` is `https://<host>/api/mcp` and `authorization_servers` is `["https://<host>"]`.
- [ ] `https://<host>/.well-known/oauth-authorization-server` lists `code_challenge_methods_supported: ["S256"]`, `client_id_metadata_document_supported: true` and the four endpoints.
- [ ] `curl -i -X POST https://<host>/api/mcp -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'` answers 401 with `WWW-Authenticate: Bearer ... resource_metadata="..."`.
- [ ] A personal access token still works: `claude mcp add --transport http vault https://<host>/api/mcp --header "Authorization: Bearer vault_pat_..."` lists tools.
- [ ] The Vault's own logs are open somewhere (Vercel logs) so you can see failures.

## ChatGPT (developer mode)

1. ChatGPT, Settings, Connectors (Apps), Advanced, turn on **Developer mode**. (Names move around; look for "create connector" / "add MCP server".)
2. Create a connector. URL: `https://<host>/api/mcp`. Authentication: **OAuth**. Leave client id and secret empty (it uses metadata documents or dynamic registration).
3. Press Connect. Expected: a browser window opens at `https://<host>/oauth/authorize?...`.
   - [ ] If not signed in, the Vault's sign-in page shows the client name. Sign in (try a provider, then repeat once with a passkey).
   - [ ] The consent screen names the app and its web address (or says "Unverified" for a registered one), lists Read, offers Write **unticked**, and lists what is never allowed.
   - [ ] Allow with Read only. ChatGPT reports the connection and lists tools. No write tools appear.
4. In a new chat, enable the connector and ask: "What is in my Vault collection summary?" Expected: it calls `get_collection_summary` and answers with your totals.
5. Disconnect and reconnect, this time ticking **Write**. Ask it to save a small deck. Expected: `save_deck` works.
6. Note exactly: the `client_id` ChatGPT used (from the Vault logs or the Connected apps list), whether it fetched a metadata document or registered itself, the `redirect_uri`, the scopes it asked for, and whether it sent `resource`.
   - [ ] Account, Connected apps lists it with its name, scopes and a recent "last used". Revoke it there; the next tool call in ChatGPT fails and asks to reconnect.
7. Wait more than an hour (access tokens last 1 hour) and use the connector again.
   - [ ] It refreshes silently (look for a `refresh_token` grant in the logs), with no new consent.

## Claude.ai (custom connector)

1. Claude.ai, Settings, Connectors, **Add custom connector**. URL: `https://<host>/api/mcp`. Leave the advanced client id and secret empty.
2. Connect. Expected: the same consent flow as above.
   - [ ] Sign-in page, consent screen and redirect back to claude.ai work. Claude lists the Vault's tools.
3. In a chat, enable the connector and ask for the collection summary and the five most valuable cards (`search_cards` with `sort=-value`).
   - [ ] Answers come back with data from your collection only.
4. Note the same details as for ChatGPT (client id kind, redirect URI, scopes, resource).
5. Claude Desktop and Claude Code (loopback redirect): `claude mcp add --transport http vault https://<host>/api/mcp` and run `/mcp` to authenticate.
   - [ ] The browser opens the consent screen with the "runs on this computer" notice; after Allow, the tools work.

## Things that must fail (try each once)

- [ ] Open the consent URL in a frame on another page (a scratch HTML file with an `<iframe>`): the browser refuses to render it.
- [ ] Edit the `redirect_uri` in a copied authorize URL to another https address: the Vault shows an error page and does not redirect.
- [ ] Reuse an authorization code (copy it from the redirect, POST it twice with curl): the second call fails and the connection stops working.
- [ ] Revoke the app under Connected apps: ChatGPT or Claude can no longer call tools until reconnected.

## What to record in issue #48

The date, the Vault version (commit), the host versions if shown, a pass or fail for each box, and anything the host
did that the twins do not (an unexpected parameter, a scope it asked for, a redirect URI form, a second registration).
If a host needs something we refuse (a custom-scheme redirect, a missing `resource`), write it down: it is a decision
for the owner, not something to loosen quietly. Update `docs/mcp-oauth-threat-model.md` if the findings change a mitigation.
