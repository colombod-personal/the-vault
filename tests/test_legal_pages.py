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


def test_the_pages_link_each_other_and_the_footer_and_the_manifest_names_the_terms():
    for name in ("privacy.html", "connect.html", "support.html"):
        assert "terms.html" in page(name), name
    for name in ("terms.html", "connect.html"):
        assert "support.html" in page(name) and "privacy.html" in page(name), name
    jsx = (PUBLIC / "views" / "account.jsx").read_text(encoding="utf-8")
    assert jsx.count('href="/terms.html"') >= 2 and jsx.count('href="/support.html"') >= 2  # sign-in page and footer
    manifest = json.loads((ROOT / "plugins" / "the-vault-openai" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["interface"]["termsOfServiceURL"] == "https://mtgvault.cards/terms.html"


def test_the_connect_page_documents_five_example_requests_and_what_it_will_not_do():
    html = page("connect.html")
    block = html.split('id="examples"')[1].split("</ol>")[0]
    assert block.count("<li>") == 5
    assert "Which shop is cheapest right now?" in html and "Delete my" in html
