"""The Vault only reads from Archidekt (issue #132, owner decision on #79; docs/compliance.md): one public deck, a GET, on a
person's request. No code may write to Archidekt, sign in to it, crawl it, or offer an assistant a way to."""

import inspect
import re
from pathlib import Path

import mtg_toolkits.archidekt as toolkit

from vault.api import mcp

ROOT = Path(__file__).parent.parent
WRITE_WORDS = re.compile(r"sync|push|upload|write|edit|update|save|login|log_in|sign_?in|import_to|export_to", re.IGNORECASE)


def test_the_archidekt_client_only_sends_get_requests():
    source = inspect.getsource(toolkit)
    verbs = set(re.findall(r"\.(get|post|put|patch|delete|request|send)\(", source))
    assert verbs == {"get"}, f"the Archidekt client uses {verbs}"


def test_the_vault_never_calls_the_paged_search_or_any_other_archidekt_method():
    allowed = {"get_deck"}
    used = set()
    for path in list((ROOT / "vault").rglob("*.py")) + list((ROOT / "jobs").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"ArchidektClient\([^)]*\)[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*\.(\w+)\(", text))
        if "ArchidektClient" in text:
            used |= {m for m in re.findall(r"client\.(get_deck|search_decks)\(", text)}
    assert used <= allowed, f"unexpected Archidekt calls: {used - allowed}"
    assert "search_decks" not in "".join(p.read_text(encoding="utf-8") for p in (ROOT / "vault").rglob("*.py")), \
        "search_decks is a paged search: it would crawl Archidekt"


def test_no_script_in_the_web_app_calls_archidekt_directly():
    for path in (ROOT / "public").rglob("*"):
        if path.suffix not in (".js", ".jsx", ".html") or path.name == "app.bundle.js" or "lib" in path.parts and path.name.endswith(".min.js"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"(fetch|axios\.\w+|XMLHttpRequest|\.open)\([^)]*archidekt", text, re.IGNORECASE), f"{path.name} calls Archidekt"


def test_no_tool_or_route_suggests_writing_to_archidekt():
    for name in mcp.BY_NAME:
        assert not ("archidekt" in name and WRITE_WORDS.search(name)), name
    app_py = (ROOT / "vault" / "api" / "v1.py").read_text(encoding="utf-8")
    routes = re.findall(r'@router\.(get|post|put|patch|delete)\("([^"]*archidekt[^"]*)"', app_py)
    assert routes and all(method == "get" for method, _ in routes), routes


def test_the_archidekt_twin_implements_reads_only():
    text = (ROOT / "twins" / "archidekt.py").read_text(encoding="utf-8")
    verbs = set(re.findall(r'self\.route\("(\w+)"', text))
    assert verbs == {"GET"}, verbs


def test_skills_and_agents_never_offer_to_edit_sync_or_sign_in_to_archidekt():
    """A line naming Archidekt with a write verb must be a prohibition: negated, under a "Do not" heading, or about the
    person doing it themselves."""
    negated = re.compile(r"(never|do not|don't|not allow|no tool|cannot|can't|without|nor|they can edit|the person applies|themselves)", re.IGNORECASE)
    for path in list((ROOT / "skills").rglob("SKILL.md")) + list((ROOT / "agents").glob("*.md")):
        in_prohibitions = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                in_prohibitions = line.lstrip("# ").lower().startswith("do not")
            if re.search(r"archidekt", line, re.IGNORECASE) and re.search(r"(sync|push|upload|write to|sign in|log in|edit)", line, re.IGNORECASE):
                assert in_prohibitions or negated.search(line), f"{path.name}: {line.strip()}"
