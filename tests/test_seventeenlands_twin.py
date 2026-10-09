"""The twin of 17Lands' file host answers like the real one (HEAD requests of 2026-10-09; twins/seventeenlands.py, docs/twins.md)."""

import gzip

import httpx
import pytest

from twins import Universe

BASE = "https://17lands-public.s3.amazonaws.com/analysis_data"
GAME = f"{BASE}/game_data/game_data_public.TST.PremierDraft.csv.gz"


@pytest.fixture
def twin():
    universe = Universe()
    universe.seventeenlands.publish("game", "TST", "PremierDraft", "expansion,won\nTST,True\n")
    with httpx.Client(transport=universe.transport) as client:
        client.universe = universe
        yield client


def test_a_published_file_answers_head_with_its_version_and_get_with_gzip(twin):
    head = twin.head(GAME)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["accept-ranges"] == "bytes" and head.headers["content-type"] == "text/csv"
    assert head.headers["etag"].startswith('"') and head.headers["last-modified"].endswith("GMT")
    body = twin.get(GAME)
    assert int(head.headers["content-length"]) == len(body.content) and gzip.decompress(body.content) == b"expansion,won\nTST,True\n"
    assert body.headers["etag"] == head.headers["etag"]


def test_a_file_that_does_not_exist_answers_403_not_404(twin):
    for path in ("/game_data/game_data_public.ZZZ.PremierDraft.csv.gz", "/draft_data/draft_data_public.TST.PremierDraft.csv.gz",
                 "/game_data/anything-else.txt"):
        assert twin.head(BASE + path).status_code == 403
        assert twin.get(BASE + path).status_code == 403
    assert "AccessDenied" in twin.get(BASE + "/game_data/game_data_public.ZZZ.PremierDraft.csv.gz").text


def test_the_etag_follows_the_content_and_a_range_is_honoured(twin):
    before = twin.head(GAME).headers["etag"]
    twin.universe.seventeenlands.publish("game", "TST", "PremierDraft", "expansion,won\nTST,True\n")
    assert twin.head(GAME).headers["etag"] == before  # the same bytes, the same ETag
    twin.universe.seventeenlands.publish("game", "TST", "PremierDraft", "expansion,won\nTST,False\n")
    assert twin.head(GAME).headers["etag"] != before
    part = twin.get(GAME, headers={"range": "bytes=0-3"})
    assert part.status_code == 206 and part.content[:2] == b"\x1f\x8b" and len(part.content) == 4
    assert part.headers["content-range"].startswith("bytes 0-3/")


def test_a_corrupt_body_and_an_oversized_length_can_be_served_for_the_jobs_refusals(twin):
    s3 = twin.universe.seventeenlands
    s3.publish_raw("game", "TST", "PremierDraft", b"not gzip at all", advertised_length=500_000_000)
    assert twin.head(GAME).headers["content-length"] == "500000000"
    assert twin.get(GAME).content == b"not gzip at all"
    s3.unpublish("game", "TST", "PremierDraft")
    assert twin.head(GAME).status_code == 403


def test_the_twin_only_knows_its_own_host_and_is_part_of_the_universe(twin):
    assert "seventeenlands" in twin.universe.twins and twin.universe.by_host["17lands-public.s3.amazonaws.com"].name == "seventeenlands"
    with pytest.raises(httpx.ConnectError):
        twin.get("https://www.17lands.com/public_datasets")  # the page is not a twin: a job never reads it
    assert twin.universe.escapes == ["https://www.17lands.com/public_datasets"]
