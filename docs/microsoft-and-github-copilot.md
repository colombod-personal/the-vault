# Microsoft Copilot and GitHub Copilot: can the Vault connect? (#401)

Read on 2026-10-09 from the pages linked below. Statements marked **verified** come from Microsoft's or GitHub's own page read that day; statements marked **not verified** are what a search or a vendor page said, or what is inferred. Nothing here comes from running the apps yet (that needs the owner's sign-ins: see "Left to do").

## Microsoft Copilot, the consumer one (copilot.microsoft.com, the Copilot apps)

- **Verified:** Microsoft's support page "Connecting Microsoft Copilot to other services" ([source](https://support.microsoft.com/en-us/microsoft-copilot/connecting-microsoft-copilot-to-other-services)) lists exactly these connectors for a personal account: OneDrive, Outlook.com (email, calendar, contacts), Google Drive, and Gmail, Google Calendar and Google Contacts. It applies to personal accounts with Microsoft 365 Personal, Family, Premium or Pro, on Copilot.com and the iOS and Android apps. It does not mention MCP, custom connectors, plugins, third-party apps or adding your own server, and shows no last-updated date.
- **Not verified:** one vendor's documentation (time cockpit) says the consumer app does not support custom MCP servers. No Microsoft page found says so, or says the opposite.
- **Verified in the app (2026-10-10, Microsoft Copilot for Windows, personal account):** in **+ then Use connectors** and in **Settings then Connectors** the list is fixed: Microsoft OneDrive, Outlook, Google Drive, Google Calendar, Gmail, Google Contacts, Box and Dropbox. Settings has Preferences, Memory, Account, Connectors, Privacy, About and Web browsing; none has a custom connector or MCP option, and Copilot Labs is unrelated.
- **Conclusion:** a person with a personal Microsoft account cannot add the Vault to the Copilot app, and nothing the Vault does changes that. Decision of the owner on 2026-10-10: no workarounds that would complicate supporting the other assistants; parked (issue #436, closed as not planned).

## Microsoft's routes that do take an MCP server (organisations and makers, not consumers)

- **Declarative agent with an MCP plugin** ([Microsoft Learn, updated 2026-09-30](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/build-mcp-plugins)): verified. A developer wraps a remote MCP server as a plugin in a declarative agent with the Microsoft 365 Agents Toolkit (6.12.0 or later), sideloads it in a development tenant (needs Custom App Upload and Copilot access enabled by an admin) and uses it at m365.cloud.microsoft/chat. The page says the plugin "enable[s] ... access to your MCP-exposed services for business users". Authentication choices: OAuth with static registration (client id and secret entered by the developer; redirect `https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect`), OAuth with **dynamic client registration (DCR)**, Entra SSO, or none. The Vault's server supports DCR and client metadata documents, so the DCR choice would apply; **not verified** by a run.
- **Copilot Studio** (MCP generally available; a maker adds a remote MCP server as a tool) and **Microsoft 365 Copilot federated connectors** (set up by an admin): from search results, **not verified** from the pages themselves.
- All of these need a work or school tenant. A person on a personal Microsoft account has none of them.

## What would be bespoke work for the Vault if we wanted Microsoft reach

1. A **declarative agent package** for Microsoft 365 Copilot (manifest, plugin manifest `ai-plugin.json` with the Vault's `https://mtgvault.cards/api/mcp`, DCR). Audience: people with a work or school Copilot licence whose admin allows custom apps. Cost: a package we maintain, an Entra/Teams-side review if published to a store, a test tenant (the owner would have to provide a tenant and sign-ins). Not worth building before we know the audience wants it.
2. Nothing for the consumer app until Microsoft documents a way in. Record it as a host limit in `docs/listings.md` after the in-app check.

## GitHub Copilot

- **Verified (changelog read 2026-10-09):** Agent Plugins 1.0 ([source](https://github.blog/changelog/2026-08-12-agent-plugins-1-0-in-vs-code-copilot-cli-and-the-copilot-app/)) is generally available in VS Code, Copilot CLI, the Copilot SDK and the GitHub Copilot app on all Copilot plans. Layout: a `$schema` field in `plugin.json`, skills under `skills/`, MCP configuration in `mcp.json`, Copilot-specific files (custom agents, commands, rules, hooks) under `com.github.copilot/`. Existing plugins that do not target 1.0 keep working. Business and Enterprise organisations can allow-list MCP servers by URL, command or name. The page says nothing about OAuth for remote servers.
- **The Vault's plugin today** (`plugins/the-vault`): has `.claude-plugin/plugin.json`, `.mcp.json` (a Claude-style `user_config` token header) and a `com.github.copilot/agents/*.agent.md` folder. Gaps against the layout above: no `$schema` in `plugin.json`, no `mcp.json` in the form 1.0 reads, and the `.mcp.json` asks for a bearer token where the other hosts use OAuth. **Not verified:** whether VS Code or the CLI install it today; the gap list is from reading the layout, not from an install.
- **Not verified (search results, GitHub docs of 2025-2026):** the remote MCP route with OAuth works in VS Code (client metadata documents supported); Copilot CLI ignores a static OAuth client id and always uses dynamic client registration; the Copilot cloud agent and code review do not support remote MCP servers that use OAuth. The Vault supports dynamic client registration, so the CLI route should connect; this has not been run.

## Left to do (the criteria of #401 still open)

- ~~Open the consumer Copilot on a personal account and look for any way to add a connector or MCP server~~ Done 2026-10-10: there is none (see above).
- Microsoft 365 Copilot (work or school): the Vault would need to hand out a client secret when Microsoft registers itself (Microsoft's page: dynamic client registration "without a client secret isn't supported yet"), and the Vault registers public clients only (`vault/oauth_clients.py`, `register`). Not built; parked with #436.
- Install the Vault's plugin and connect the server in VS Code, Copilot CLI and the Copilot app; record what each calls (owner's GitHub sign-in).
- Fix the plugin's layout gaps above if the install fails or to follow the standard: a separate issue with a test in `tests/test_plugin.py`.

Sources: the pages linked above and Microsoft's Copilot Studio MCP announcement and GitHub's remote MCP docs as found in search on 2026-10-09 (not read in full).
