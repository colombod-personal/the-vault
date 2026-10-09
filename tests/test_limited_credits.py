"""17Lands' CC BY 4.0 credit (#178, docs/limited-data-design.md section 6) reaches everyone who sees the figures: the answers, the credits
page, the skills, the Limited expert, the server instructions and the plugin copies; and the repository holds no 17Lands data."""

import re
from pathlib import Path

from vault import limited_stats as ls
from vault import provenance as prov
from vault.api import mcp
from vault.sources import SOURCES

ROOT = Path(__file__).resolve().parent.parent


def words(path: str) -> str:
    raw = (ROOT / path).read_text(encoding="utf-8")
    return " ".join(re.sub(r"<(script|style).*?</\1>|<[^>]+>", " ", raw, flags=re.S).replace("&amp;", "&").split())


def test_the_attribution_has_every_piece_the_licence_asks_for():
    text = ls.attribution("HOB", "PremierDraft", "2026-10-01", "2026-10-09")
    for piece in ("Data from 17Lands (https://www.17lands.com/public_datasets)",  # the creator and a link to the material
                  "licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",  # the licence and its address
                  "provided without warranty (section 5)",  # the disclaimer notice
                  "Changed by the Vault: the per-game and per-pick rows were reduced to per-card counts",  # that the material was modified
                  "computed by the Vault, so they can differ from the figures on 17lands.com",
                  "Not produced or endorsed by 17Lands.",  # no implied endorsement
                  "Magic Arena games and drafts, set HOB, format PremierDraft",  # what the data is, and which
                  "17Lands files last updated 2026-10-01, read by the Vault on 2026-10-09"):
        assert piece in text, piece
    assert "17Lands" in text and "17lands" not in text.replace("17lands.com", "")  # spelled as they ask: capital L


def test_the_general_notice_is_the_same_text_without_a_set_a_format_or_a_date():
    assert prov.GENERIC_17LANDS_NOTICE == ls.GENERIC_ATTRIBUTION
    for piece in ("Data from 17Lands", "CC BY 4.0", "without warranty", "Changed by the Vault", "Not produced or endorsed by 17Lands."):
        assert piece in prov.GENERIC_17LANDS_NOTICE
    block = prov.for_catalog("limited_17lands")
    assert block.kind == "source" and block.source == "17Lands" and block.licence.name == "CC BY 4.0"
    assert block.licence.url == "https://creativecommons.org/licenses/by/4.0/" and block.changes and block.notice == prov.GENERIC_17LANDS_NOTICE


def test_the_credits_page_credits_17lands_with_the_licence_the_change_the_warranty_and_the_thanks():
    page = words("public/credits.html")
    assert "Limited statistics" in page and "17Lands" in page
    card = page.split("Limited statistics", 1)[1].split("Decks and collections", 1)[0]
    for piece in ("Creative Commons Attribution 4.0 International licence (CC BY 4.0)", "without warranty", "computed by the Vault",
                  "can differ from the figures on 17lands.com", "keeps only per-card counts", "throws the files away",
                  "Magic Arena games and drafts", "not paper Magic", "isn't produced or endorsed by 17Lands", "Thank you, 17Lands", "Patreon"):
        assert piece in card, piece
    raw = (ROOT / "public" / "credits.html").read_text(encoding="utf-8")
    assert 'href="https://creativecommons.org/licenses/by/4.0/"' in raw and 'href="https://www.17lands.com/public_datasets"' in raw


def test_17lands_is_one_of_the_sources_the_readme_and_the_plugin_list():
    [source] = [s for s in SOURCES if s.name == "17Lands"]
    assert "CC BY 4.0" in source.credit and "not produced or endorsed by 17Lands" in source.notice
    assert "per-card counts" in source.how and "nothing about you" in source.how
    assert "17Lands" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_skills_the_limited_expert_and_the_plugin_copies_carry_the_credit_and_the_sample_rules():
    for path in ("skills/vault-attribution/SKILL.md", "plugins/the-vault/skills/vault-attribution/SKILL.md"):
        text = words(path)
        for piece in ("17Lands", "CC BY 4.0", "According to data from 17Lands (set, format, date)", "attribution", "Arena data, not paper Magic",
                      "does not produce or endorse the Vault"):
            assert piece in text, (path, piece)
    for path in ("agents/vault-limited-expert.md", "plugins/the-vault/agents/vault-limited-expert.md", "skills/expert-council/SKILL.md"):
        text = words(path)
        for piece in ("get_limited_card_stats", "attribution", "CC BY 4.0", "too_few", "95% ranges", "Arena", "best pick"):
            assert piece in text, (path, piece)
    expert = words("agents/vault-limited-expert.md")
    assert "You have no draft statistics" not in expert  # the old line is gone
    assert "According to data from 17Lands (set, format, date)" in expert and "not produced or endorsed by 17Lands" in expert


def test_the_server_instructions_and_the_prompts_name_the_credit_and_the_limits():
    from vault.api.mcp_catalog import PROMPTS

    text = " ".join(mcp.INSTRUCTIONS.split())
    for piece in ("According to data from 17Lands (set, format, date)", "repeat the answer's `attribution`", "CC BY 4.0",
                  "does not endorse the Vault", "Never turn a rate into a grade"):
        assert piece in text, piece
    assert all("17Lands" in " ".join(p["text"].split()) for p in PROMPTS)  # every prompt ends with the grounding rules


def test_the_repository_holds_no_17lands_data_only_two_invented_samples_named_for_what_they_are():
    skip = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or skip & set(path.relative_to(ROOT).parts):
            continue
        if path.suffix in (".gz", ".csv", ".zip", ".json", ".parquet") or "_public." in path.name:
            if path.suffix in (".csv", ".json"):
                head = path.read_text(encoding="utf-8", errors="ignore")[:6000]
                if "opening_hand_" not in head and "pack_card_" not in head:
                    continue
            offenders.append(path.relative_to(ROOT).as_posix())
    assert sorted(offenders) == ["tests/fixtures/limited_draft_sample.csv", "tests/fixtures/limited_game_sample.csv"]
    for name in offenders:
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        assert len(lines) < 20 and {row.split(",")[0] for row in lines[1:]} == {"TST"}, f"{name}: an invented sample of a made-up set, not data"


def test_the_job_and_the_models_keep_no_raw_rows():
    """Design section 3, 'Never kept': no row of either file, draft id, rank, timestamp per game, deck or pool."""
    job = (ROOT / "jobs" / "sync_limited.py").read_text(encoding="utf-8")
    assert "tempfile" not in job and "open(" not in job and "write_bytes" not in job and "NamedTemporaryFile" not in job
    models = (ROOT / "vault" / "models.py").read_text(encoding="utf-8")
    section = models.split("class LimitedGameStat", 1)[1].split("class CatalogSource", 1)[0]
    for word in ("draft_id", "user_id", "rank", "pool_", "deck_", "game_time"):
        assert word not in section.replace("first_time", "").replace("last_time", ""), word
