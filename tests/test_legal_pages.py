"""The pages both assistant directories require (#238): terms, support, privacy, documentation. Public, plain, and with no
personal data of the owner (owner's rule)."""

import json
import re
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"
ROOT = PUBLIC.parent


def page(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8")


def test_terms_state_the_fan_content_notice_scopes_deletion_and_that_the_code_is_public():
    text = " ".join(re.sub(r"<[^>]+>", " ", page("terms.html")).split())
    assert "unofficial Fan Content permitted under the Fan Content Policy" in text and "Not approved/endorsed by Wizards" in text
    for needed in ("Read", "Write", "never delete your account", "Connected apps", "delete your account", "without warranty",
                   "not any store's price today", "github.com/colombod-personal/the-vault"):
        assert needed.lower() in text.lower(), needed


def test_support_points_to_github_issues_and_the_connect_page_and_is_honest_about_response():
    html = page("support.html")
    assert "https://github.com/colombod-personal/the-vault/issues" in html and 'href="connect.html"' in html
    assert "best effort" in html and "no guaranteed response time" in html


def test_the_public_pages_carry_no_email_address_or_personal_data():
    for name in ("terms.html", "support.html", "connect.html"):
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", page(name)), name
        assert "mailto:" not in page(name), name


def test_the_guide_pages_carry_no_email_address_or_personal_data():
    """The same rule for the guide (docs/site, published with GitHub Pages): its pages and the shared layout name no e-mail address
    and link no mailto:, because the owner's own address must never appear (reports go through the support page)."""
    guide = ROOT / "docs" / "site"
    files = sorted(guide.rglob("*.html"))
    assert (guide / "_layout.html") in files and len(files) >= 6
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), path.name
        assert "mailto:" not in text.lower(), path.name


def test_the_pages_link_each_other_and_the_footer_and_the_manifest_names_the_terms():
    for name in ("privacy.html", "connect.html", "support.html"):
        assert "terms.html" in page(name), name
    for name in ("terms.html", "connect.html"):
        assert "support.html" in page(name) and "privacy.html" in page(name), name
    jsx = (PUBLIC / "views" / "account.jsx").read_text(encoding="utf-8")
    assert jsx.count('href="/terms.html"') >= 2 and jsx.count('href="/support.html"') >= 2  # sign-in page and footer
    manifest = json.loads((ROOT / "plugins" / "the-vault-openai" / "plugin.json").read_text(encoding="utf-8"))["extensions"]["com.openai"]
    assert manifest["interface"]["termsOfServiceURL"] == "https://mtgvault.cards/terms.html"


def test_the_connect_page_documents_five_example_requests_and_what_it_will_not_do():
    html = page("connect.html")
    block = html.split('id="examples"')[1].split("</ol>")[0]
    assert block.count("<li>") == 5
    assert "Which shop is cheapest right now?" in html and "Delete my" in html


def test_the_privacy_notice_names_every_recipient_of_data_the_code_sends_to():
    """#2 / docs/gdpr.md: the notice must name the sign-in providers the code offers and each outside service the server calls."""
    from vault import auth, combos

    text = " ".join(re.sub(r"<[^>]+>", " ", page("privacy.html")).split()).lower()
    for provider in auth.PROVIDERS:
        assert provider in text, provider
    for recipient in ("vercel", "neon", "github", "scryfall", "commander spellbook", "archidekt", "ai assistant",
                      "web analytics", "speed insights", "resend"):
        assert recipient in text, recipient
    assert "commanderspellbook.com" in combos.URL  # the service named above is the one vault/combos.py calls
    assert "analytics trackers" not in text  # the notice also describes the anonymous visitor statistics


def test_the_connect_page_and_the_reviewers_guide_give_the_one_line_that_makes_an_assistant_use_the_vault():
    """#319: claude.ai gives the model only the tool names, so a rules question that does not name the Vault is answered from memory;
    one line in a Project or at the start of a chat made 3 of 3 runs call the Vault (docs/ai-integration-testing.md)."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import build_plugin as bp

    line = bp.USE_THE_VAULT
    assert "never quote a rule from memory" in line and "The Vault's tools" in line
    connect = page("connect.html").replace("&#x27;", "'").replace("&#39;", "'")
    assert 'id="use-it"' in connect and line in connect
    from vault import reviewer_routes as rr

    guide = rr.page("https://example.test").replace("&#x27;", "'").replace("&#39;", "'")
    assert line in guide and "Used The Vault" in guide


def test_the_terms_and_the_privacy_notice_tell_the_truth_about_write():
    """vault-0n9.5 and the security review of #484 (H2, L1): Write is pre-ticked for an app identified by its web address, it can delete
    decks and replace the collection, and the Vault does not check the person's yes before most changes. The page must not promise a
    preview and a yes the server does not enforce, and must not say the person always chose Write."""
    from vault.oauth_routes import SCOPE_TEXT

    terms = " ".join(re.sub(r"<[^>]+>", " ", page("terms.html")).split())
    assert "Changes show a preview first and need your yes" not in terms
    for must in ("deleting decks", "replacing your whole collection", "already ticked", "untick it and the app can only read",
                 "The Vault does not ask you again before most changes", "some cannot be undone"):
        assert must in terms, must
    privacy = " ".join(re.sub(r"<[^>]+>", " ", page("privacy.html")).split())
    assert "write if you left it ticked or ticked it yourself" in privacy and "write if you chose it" not in privacy
    # the consent page's own Write text and the Terms name the same dangerous things
    assert "deleting decks" in SCOPE_TEXT["write"] and "replacing your whole collection" in SCOPE_TEXT["write"]


def test_no_setup_page_calls_the_connection_read_only_or_tells_the_person_to_leave_write_unticked():
    """The security review of #484 (M3): a plain Allow can now give Write, so a setup page must not promise read-only."""
    pages = sorted((PUBLIC / "setup").glob("*.md"))
    assert len(pages) >= 5
    for path in pages:
        text = path.read_text(encoding="utf-8")
        assert "This setup is read-only" not in text and "Write unticked" not in text and "Write is a separate step" not in text, path.name
        assert "deleting decks and replacing your whole collection" in text or "Write: delete decks, replace the collection" in text, path.name
