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


def test_the_vault_never_calls_search_decks_from_a_job_or_a_loop():
    """``search_decks`` follows ``next`` across every page of results, so it is a crawl unless bounded (docs/compliance.md,
    #132). The Vault's only Archidekt call is ``get_deck`` for one named public deck. This reads the syntax tree, so a
    call hidden in a string, a loop, a comprehension or a job is found, not just a literal grep match; and no job (the
    scheduled code) may use the Archidekt client at all."""
    import ast

    offenders = []
    for folder in ("vault", "jobs", "api", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and node.attr == "search_decks") or (
                        isinstance(node, ast.Name) and node.id == "search_decks") or (
                        isinstance(node, ast.alias) and node.name == "search_decks") or (
                        isinstance(node, ast.Constant) and node.value == "search_decks"):
                    offenders.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}")
    assert not offenders, f"search_decks would crawl Archidekt; only get_deck is allowed: {offenders}"

    for path in list((ROOT / "jobs").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"ArchidektClient|mtg_toolkits\.archidekt|vault\.archidekt_cache|deck_import", text), \
            f"{path.name}: scheduled and operator scripts must not contact Archidekt"


def test_the_search_decks_rule_is_written_in_compliance_md():
    text = " ".join((ROOT / "docs" / "compliance.md").read_text(encoding="utf-8").split())
    assert "search_decks" in text and "follows `next` across every page" in text
    assert "never called from a job, a loop" in text
