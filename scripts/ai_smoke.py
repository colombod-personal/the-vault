"""AI-integration smoke test: drive a running Vault's MCP server the way an agent would, on real data, and check
the answers a skill's procedure depends on. Not a unit test (those use small synthetic data and missed four real
bugs); this needs a Vault with the catalog loaded and a personal access token.

    VAULT_URL=http://localhost:8010 VAULT_TOKEN=vault_pat_... python scripts/ai_smoke.py
    python scripts/ai_smoke.py --url https://mtgvault.cards --token vault_pat_... --only rules

It prints each check and exits 1 if any fails. Read-only: a read token is enough, nothing is written.
Checks use well-known cards and rules (Lightning Bolt, Sol Ring, protection, trample) so they hold on any catalog
loaded from Scryfall and the Comprehensive Rules. See docs/ai-integration-testing.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

FAILS: list[str] = []
# The MCP Apps views the server serves (ui://vault/<name>). tests/test_ai_smoke.py compares this with vault/api/mcp_ui.py, so adding
# a view without updating it fails in CI instead of on the next live run.
VIEWS = {"card", "printings", "deck", "combos", "upgrades", "steps", "shopping"}
APPS = {"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}}


class Client:
    def __init__(self, url: str, token: str):
        self.url, self.token, self.n = url.rstrip("/") + "/api/mcp", token, 0

    def rpc(self, method: str, params: dict | None = None, session: str | None = None, headers_out: dict | None = None) -> dict:
        self.n += 1
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if session:
            headers["Mcp-Session-Id"] = session
        r = httpx.post(self.url, timeout=90, headers=headers, json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        if r.status_code == 401:
            sys.exit("401: the token was refused (create a personal access token in the Vault: Account, Agents & API)")
        if headers_out is not None:
            headers_out.update(r.headers)
        return r.json()

    def call(self, tool: str, **args) -> dict:
        out = self.rpc("tools/call", {"name": tool, "arguments": args})
        if "error" in out:
            return {"_rpc_error": out["error"]}
        res = out["result"]
        body = res.get("structuredContent") or json.loads(res["content"][0]["text"])
        return {"_is_error": True, **body} if res.get("isError") else body


def check(ok: bool, what: str, detail: str = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + what + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(what)


def has_provenance(body: dict) -> bool:
    blocks = body.get("provenance") or []
    return bool(blocks) and all(b.get("kind") in ("source", "computed") and b.get("source") for b in blocks)


def connection(c: Client) -> None:
    print("connection")
    init = c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ai-smoke", "version": "1"}})["result"]
    check("tools" in init["capabilities"] and "prompts" in init["capabilities"] and "resources" in init["capabilities"], "server declares tools, prompts and resources")
    check("Never answer a rules or card question from memory" in init["instructions"], "the grounding rules are in the server instructions")
    tools = {t["name"] for t in c.rpc("tools/list")["result"]["tools"]}
    need = {"whoami", "get_card_oracle", "get_rulings", "search_rules", "get_rule", "verify_citation", "present_steps",
            "deck_stats", "deck_legality", "find_upgrades", "validate_deck_changes", "find_combos", "shopping_list"}
    check(need <= tools, "all the catalog and deck tools are listed", str(need - tools))
    who = c.call("whoami")
    data = who.get("data", {})
    check(bool(data.get("rules_version")) and has_provenance(who), "whoami names the rules edition and carries provenance", str(who)[:120])
    print(f"       rules {data.get('rules_version')}; sources: {sorted(data.get('sources', {}))}")


def cards(c: Client) -> dict:
    print("cards and rulings")
    bolt = c.call("get_card_oracle", name="Lightning Bolt")
    card = bolt.get("card") or {}
    check(card.get("name") == "Lightning Bolt" and "3 damage" in (card.get("oracle_text") or ""), "Lightning Bolt: the card, not a token or a lookalike")
    check(has_provenance(bolt) and any(b.get("notice") for b in bolt["provenance"]), "card answer carries provenance and the Fan Content notice")
    check(bolt.get("price") is not None and bolt["price"].get("as_of"), "card answer includes a dated price (this broke once: the model dropped it)", str(bolt.get("price")))
    elves = c.call("get_card_oracle", name="Llanowar Elves")
    check((elves.get("card") or {}).get("layout") != "token" and (elves["card"]["legalities"].get("commander") == "legal"),
          "Llanowar Elves is the creature, not its token (a token shadowed it once)")
    face = c.call("get_card_oracle", name="Fire")
    check((face.get("card") or {}).get("name") == "Fire // Ice", "a front-face name finds the double-faced card")
    typo = c.call("get_card_oracle", name="Lightnin Bolt")
    check(typo.get("card") is None and "Lightning Bolt" in (typo.get("suggestions") or []), "a misspelling gets suggestions, never a guess",
          str(typo)[:120] + " (a 500 here usually means the database lacks the pg_trgm extension)")
    return card


def rules(c: Client) -> None:
    print("rules, citations")
    found = c.call("search_rules", query="protection from red damage prevented", limit=5)
    check(bool(found.get("results")), "a plain-language rules search finds something (it found nothing once: every word was required)")
    check(found.get("matched") in ("all words", "any word") and has_provenance(found), "search says how it matched and carries provenance")
    rule = c.call("get_rule", number="702.16")
    check((rule.get("rule") or {}).get("text", "").startswith("Protection") and len(rule.get("subrules", [])) >= 5, "rule 702.16 (protection) with its subrules")
    sub = next((s for s in rule.get("subrules", []) if s["number"] == "702.16b"), (rule.get("subrules") or [{}])[0])
    true_quote = (sub.get("text") or "")[:100]
    check(c.call("verify_citation", kind="rule", ref=sub.get("number", ""), quote=true_quote).get("verified") is True, "a verbatim quote verifies")
    fake = c.call("verify_citation", kind="rule", ref=sub.get("number", ""), quote="Protection means nothing can ever happen to a permanent")
    check(fake.get("verified") is False and bool((fake.get("detail") or {}).get("source_text")), "an invented quote fails and the true text comes back")
    check(c.call("verify_citation", kind="oracle_text", ref="Lightning Bolt", quote="deals 3 damage to any target").get("verified") is True, "Oracle text verifies")
    glossary = c.call("get_rule", number="glossary:Trample")
    check((glossary.get("rule") or {}).get("kind") == "glossary", "a glossary term is found")
    steps = c.call("present_steps", title="smoke", steps=[{"text": "real", "rules": ["702.16b"]}, {"text": "invented", "rules": ["999.99"]}])
    check(steps.get("unknown_rules") == [{"step": 2, "rule": "999.99"}] and len(steps["steps"][0]["rules"]) == 1, "present_steps attaches real rules and flags an invented number")
    big = c.call("get_rule", number="100")
    check(len(json.dumps(big)) < 60_000, "a rules answer stays small (nothing can be dumped)")


def decks(c: Client) -> None:
    print("deck analysis (a 100-card Commander deck of real cards)")
    spells = ["Llanowar Elves", "Elvish Mystic", "Sol Ring", "Arcane Signet", "Cultivate", "Kodama's Reach", "Rampant Growth", "Heroic Intervention",
              "Beast Within", "Regrowth", "Eternal Witness", "Craterhoof Behemoth", "Imperious Perfect", "Elvish Archdruid", "Priest of Titania",
              "Wirewood Symbiote", "Heritage Druid", "Nettle Sentinel", "Birchlore Rangers", "Reclamation Sage", "Wellwisher", "Joraga Warcaller",
              "Elvish Visionary", "Lys Alana Huntmaster", "Dwynen, Gilt-Leaf Daen", "Ezuri's Predation", "Overrun", "Natural Order", "Survival of the Fittest"]
    deck = "Commander\n1 Ezuri, Renegade Leader\n\nDeck\n" + "\n".join(f"1 {s}" for s in spells) + f"\n{100 - 1 - len(spells)} Forest\n"
    st = c.call("deck_stats", text=deck)
    r = st.get("result", {})
    check(r.get("cards") == 100 and not r.get("unmatched") and has_provenance(st), "deck_stats counts 100 cards, finds every name, carries provenance", str(r.get("unmatched")))
    check(r.get("roles", {}).get("ramp", {}).get("count", 0) >= 5, "roles come from Scryfall Tagger tags (ramp found)")
    lg = c.call("deck_legality", text=deck, format="commander")["result"]
    check(lg["legal"] is True, "the deck is legal in Commander (a token once made Llanowar Elves illegal)", str(lg["issues"])[:160])
    up = c.call("find_upgrades", text=deck, format="commander", budget_usd=3, roles=["ramp"], limit=6)["result"]
    cands = up["candidates"].get("ramp", [])
    check(bool(cands) and all(x["price_usd"] is not None and x["price_usd"] <= 3 for x in cands), "upgrade candidates exist and every one is priced within the budget")
    adds = [x["name"] for x in cands[:2]]
    cuts = [x["name"] for x in up["cut_candidates"][:2]]
    ok = c.call("validate_deck_changes", text=deck, format="commander", adds=adds, cuts=cuts, budget_usd=5)["result"]
    check(ok["valid"] is True and ok["added_cost_usd"] <= 5, "a plan built from the candidates validates", str(ok["issues"])[:160])
    bad = c.call("validate_deck_changes", text=deck, format="commander", adds=["Counterspell", "Black Lotus"], cuts=["Forest"], budget_usd=5)["result"]
    kinds = {i["kind"] for i in bad["issues"]}
    check(bad["valid"] is False and {"color_identity", "not_legal"} <= kinds, "a bad plan is caught (off-color, banned)", str(kinds))
    flawed = deck.replace(f"{100 - 1 - len(spells)} Forest", f"{100 - 2 - len(spells)} Forest\n1 Counterspell")
    existing = c.call("validate_deck_changes", text=flawed, format="commander", adds=adds[:1], cuts=cuts[:1], budget_usd=5)["result"]
    check(existing["valid"] is True and any(i["card"] == "Counterspell" for i in existing["existing_issues"]),
          "a problem the deck already had does not make a good plan invalid, and is reported")
    cb = c.call("find_combos", text=deck)
    if "_is_error" in cb:
        print("  skip find_combos: Commander Spellbook not reachable from the server:", str(cb)[:100])
    else:
        sources = [(b["kind"], b["source"]) for b in cb["provenance"]]
        check(("source", "Commander Spellbook") in sources and ("computed", "The Vault") in sources, "combos are attributed to Commander Spellbook")
    sh = c.call("shopping_list", text="1 Sol Ring\n1 Rhystic Study")
    check(has_provenance(sh) and "does not contact stores" in " ".join(sh["result"]["notes"]), "shopping list carries provenance and says the Vault never contacts stores")


def prompts(c: Client) -> None:
    print("prompts and views")
    names = {p["name"] for p in c.rpc("prompts/list")["result"]["prompts"]}
    check({"vault_start", "rules_judge", "explain_interaction", "upgrade_deck", "shopping_help"} <= names, "the first-run tour and the four prompts are listed")
    got = c.rpc("prompts/get", {"name": "rules_judge", "arguments": {"question": "Does Bolt kill a 3/3?"}})["result"]
    check("verify_citation" in got["messages"][0]["content"]["text"], "the rules_judge prompt tells the agent to verify citations")
    res = c.rpc("resources/list")["result"]["resources"]
    check({r["uri"] for r in res} == {f"ui://vault/{v}" for v in VIEWS} and all(r["mimeType"] == "text/html;profile=mcp-app" for r in res),
          f"the {len(VIEWS)} MCP Apps views are served", str(sorted(r["uri"] for r in res)))
    enforced = os.environ.get("MCP_APPS_REQUIRE_CAPABILITY", "") not in ("0", "false", "no")  # as the server under test is configured
    for name, caps, expect in (("a host with MCP Apps", APPS, True), ("a host without", {}, not enforced)):
        out: dict = {}
        c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": caps, "clientInfo": {"name": "ai-smoke", "version": "1"}}, headers_out=out)
        session = out.get("mcp-session-id")
        listed = c.rpc("tools/list", session=session)["result"]["tools"]
        with_views = {t["name"] for t in listed if "_meta" in t}
        check(bool(session) and (len(with_views) >= len(VIEWS)) == expect and (expect or not with_views),
              f"tools/list for {name}: view links {'present' if expect else 'absent'}", f"session={bool(session)} views on {sorted(with_views)}")
    page = c.rpc("resources/read", {"uri": "ui://vault/card"})["result"]["contents"][0]
    check("innerHTML" not in page["text"] and page["_meta"]["ui"]["csp"] == {"resourceDomains": ["https://cards.scryfall.io"]}, "the card view inserts text only and allows only Scryfall's image host")


GROUPS = {"connection": connection, "cards": cards, "rules": rules, "decks": decks, "prompts": prompts}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("VAULT_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("VAULT_TOKEN", ""))
    ap.add_argument("--only", choices=sorted(GROUPS), action="append", help="run only these groups")
    args = ap.parse_args()
    if not args.token:
        sys.exit("give a personal access token: --token or VAULT_TOKEN")
    c = Client(args.url, args.token)
    for name, fn in GROUPS.items():
        if not args.only or name in args.only:
            fn(c)
    print(f"\n{len(FAILS)} failed" if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
