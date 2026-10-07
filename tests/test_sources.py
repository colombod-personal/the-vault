"""Credits and how the Vault uses outside services (owner's rule): one list (vault/sources.py), and every place that
credits sources must show all of it: README, credits page, plugin and agent READMEs, manifests, connect page."""

import html
import re
from pathlib import Path

from vault import sources

ROOT = Path(__file__).resolve().parent.parent

# The first words of each source's name as a person would look for them on a page.
KEYS = {"Wizards of the Coast": "Wizards of the Coast", "Scryfall": "Scryfall", "Scryfall Tagger contributors": "Tagger",
        "Commander Spellbook": "Commander Spellbook", "Archidekt": "Archidekt", "Moxfield": "Moxfield",
        "Dragon Shield (Card Manager)": "Dragon Shield", "TCGplayer, Cardmarket and Cardhoarder": "Cardhoarder", "EDHREC": "EDHREC",
        "Card illustrators": "illustrat", "Google, Microsoft, Apple and Facebook sign-in, and passkeys": "Facebook",
        "Vercel, Neon and GitHub": "Neon", "Open source software": "open source"}


def text_of(path: str) -> str:
    raw = (ROOT / path).read_text(encoding="utf-8")
    return html.unescape(" ".join(re.sub(r"<(script|style).*?</\1>|<[^>]+>", " ", raw, flags=re.S).split()))


def test_every_source_has_a_search_key_and_says_what_it_sends_and_how_it_is_used():
    assert set(KEYS) == set(sources.names()), "add the new source to KEYS (and to every page below)"
    for s in sources.SOURCES:
        assert s.how.strip() and s.credit.strip() and s.gives.strip() and s.url.startswith("https://"), s.name
    sending = " ".join(s.how for s in sources.SOURCES).lower()
    for phrase in ("nothing about you", "never connects", "never contacts", "stores no copy", "keeps no copy"):
        assert phrase in sending, phrase


def test_the_readme_block_is_the_generated_one_and_names_every_source():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert sources.sync_block(readme) == readme, "run: python scripts/build_plugin.py"
    for key in KEYS.values():
        assert key.lower() in readme.lower(), key
    assert "never sends your name, e-mail or collection" in readme


def test_the_credits_page_names_every_source_and_the_fan_content_notice():
    page = text_of("public/credits.html").lower()
    for name, key in KEYS.items():
        assert key.lower() in page, f"credits.html does not credit {name}"
    assert "unofficial fan content permitted under the fan content policy" in page


def test_the_plugin_and_agent_readmes_credit_every_source():
    for path in ("plugins/the-vault/README.md", "agent-definitions/README.md"):
        text = (ROOT / path).read_text(encoding="utf-8").lower()
        for name, key in KEYS.items():
            assert key.lower() in text, f"{path} does not credit {name}"
        assert "unofficial fan content" in text, path


def test_the_plugin_manifests_carry_the_short_credit_line():
    import json
    line = sources.short_credit_line()
    for scheme in ("Scryfall", "Wizards of the Coast", "Commander Spellbook", "Archidekt", "Dragon Shield", "Moxfield", "Tagger"):
        assert scheme in line, scheme
    openai = json.loads((ROOT / "plugins" / "the-vault-openai" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert sources.CREDITS_URL in openai["interface"]["longDescription"] and "Scryfall" in openai["interface"]["longDescription"]
    claude = json.loads((ROOT / "plugins" / "the-vault" / "plugin.json").read_text(encoding="utf-8"))
    assert "Scryfall" in claude["description"] and "credits" in claude["description"].lower()


def test_the_connect_page_credits_the_sources_and_says_the_vault_is_not_endorsed():
    page = text_of("public/connect.html")
    assert "Scryfall" in page and "Commander Spellbook" in page and "Archidekt" in page and sources.CREDITS_URL.split(".cards")[1] in (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    assert "Not approved/endorsed by Wizards" in page
