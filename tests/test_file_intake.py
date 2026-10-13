"""vault-0n9.3: a collection file dropped in the chat is staged by the Vault and previewed in the same chat.

The chat app hands the tool ``{download_url, file_id, mime_type?, file_name?}`` (``_meta["openai/fileParams"]``) and the Vault
FETCHES that address, which is an SSRF surface: the first half of this file is the fetcher (vault/file_fetch.py) against hostile
addresses, hosts, answers and servers; the second half is the tool (vault/intake.py): the file is staged as the person's own,
previewed, imported only by confirm_staged_upload (write scope) with the preview's content_hash, and nothing the host sent that
is not a web address (the string a phone app sends, a path, a missing key) reaches the network. Synthetic data only.
"""

import json
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_agents import CSV, V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from vault import file_fetch, importer, intake
from vault.api import mcp, mcp_profile
from vault.file_fetch import FileFetcher, Handoff, Refused
from vault.models import Import, StagedUpload

HOST = "files.oaiusercontent.com"
PUBLIC = "93.184.216.34"
TOKEN = "sig-9f2c71d4e0a5-SECRET-TOKEN"  # stands for the signed token a real address carries
GOOD = f"https://{HOST}/file-AbC123?se=2026-10-12T10%3A00%3A00Z&sig={TOKEN}"


def fetcher(handler=None, *, resolver=None, hosts=("oaiusercontent.com", "openai.com", "chatgpt.com"), **kw) -> FileFetcher:
    """A fetcher on a stand-in network: ``handler(request)`` answers, DNS answers a public address. It pins the connection like
    production does, so the requests it sees carry the checked address, not the name."""
    handler = handler or (lambda request: httpx.Response(200, content=CSV, headers={"content-type": "text/csv"}))
    return FileFetcher(hosts, httpx.MockTransport(handler), resolver or (lambda host: [PUBLIC]), pin=True, **kw)


def refused(call) -> Refused:
    with pytest.raises(Refused) as caught:
        call()
    return caught.value


def fetch(f: FileFetcher, url: str = GOOD, **meta):
    return f.fetch(Handoff(url, **meta))


# ============================== the address ====================================================================================

@pytest.mark.parametrize("url", [
    f"https://{HOST}/f?sig=1", "https://chatgpt.com/backend-api/files/x", "https://sub.deep.openai.com/x", "https://openai.com/x"])
def test_an_address_on_the_allow_list_is_accepted(url):
    assert file_fetch.check_url(url, file_fetch.DEFAULT_HOSTS) == url.split("/")[2]


@pytest.mark.parametrize("url", [
    "http://files.oaiusercontent.com/x",  # not https
    "ftp://files.oaiusercontent.com/x", "file:///etc/passwd", "file://localhost/c:/Users/x/my.csv", "//files.oaiusercontent.com/x",
    "https://evil.example/x",  # not on the list
    "https://evilopenai.com/x", "https://openai.com.evil.example/x", "https://files.oaiusercontent.com.evil.example/x",
    "https://oaiusercontent.com.evil.example/", "https://notchatgpt.com/x",  # a suffix without a dot is another domain
    "https://files.oaiusercontent.com@evil.example/x", "https://evil.example@files.oaiusercontent.com/x",  # credentials
    "https://user:pw@files.oaiusercontent.com/x",
    "https://files.oaiusercontent.com:8443/x", "https://files.oaiusercontent.com:80/x",  # not 443
    "https://127.0.0.1/x", "https://2130706433/x", "https://0x7f000001/x", "https://[::1]/x", "https://[::ffff:10.0.0.1]/x",
    "https://169.254.169.254/latest/meta-data", "https://localhost/x", "https://openai/x",
    "https://files.oaiusercontent.com./x",  # a trailing dot is another name
    "https://files.oaiusercontent.com/x#frag", "https://files.oaiusercontent.com/x y", "https://files.oaiusercontent.com/x\\y",
    "https://files.oaiusercontent.com/x\nHost: evil", "https://filés.oaiusercontent.com/x", "",
    "https://files.oaiusercontent.com/" + "a" * file_fetch.MAX_URL,
])
def test_an_address_that_could_aim_the_fetch_is_refused(url):
    why = refused(lambda: file_fetch.check_url(url, file_fetch.DEFAULT_HOSTS))
    assert why.kind == "cannot_hand_over"


def test_the_allow_list_is_a_setting_and_a_domain_allows_its_subdomains_only():
    from vault.config import Settings, parse_hosts
    assert parse_hosts(" *.Example.com , other.org,,") == ("example.com", "other.org")
    assert Settings(database_url="x", file_intake_hosts=("example.com",)).file_intake_hosts == ("example.com",)
    assert file_fetch.host_allowed("a.b.example.com", ("example.com",)) and file_fetch.host_allowed("example.com", ("example.com",))
    assert not file_fetch.host_allowed("badexample.com", ("example.com",))
    assert not file_fetch.host_allowed("example.com.evil.example", ("example.com",))
    with pytest.raises(RuntimeError, match="FILE_INTAKE_HOSTS"):
        Settings(database_url="x", file_intake_hosts=("com",)).check()


def test_the_default_allow_list_is_openais_own_file_hosts():
    assert file_fetch.DEFAULT_HOSTS == ("oaiusercontent.com", "openai.com", "chatgpt.com")


# ============================== where the name resolves ========================================================================

PRIVATE = ["10.0.0.5", "172.16.0.1", "192.168.1.1", "127.0.0.1", "0.0.0.0", "169.254.169.254", "100.64.0.1", "224.0.0.1",
           "240.0.0.1", "::1", "fe80::1", "fd00::1", "ff02::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
           "64:ff9b::a00:1", "2002:a00:1::", "2001::1"]


@pytest.mark.parametrize("address", PRIVATE)
def test_a_name_that_resolves_to_a_private_address_is_never_contacted(address):
    contacted = []
    f = fetcher(lambda request: contacted.append(request) or httpx.Response(200, content=CSV), resolver=lambda host: [address])
    why = refused(lambda: fetch(f))
    assert why.kind == "cannot_hand_over" and why.host == HOST and not contacted


def test_one_private_address_among_public_ones_refuses_the_host():
    contacted = []
    f = fetcher(lambda request: contacted.append(request) or httpx.Response(200, content=CSV),
                resolver=lambda host: [PUBLIC, "10.0.0.5"])
    assert refused(lambda: fetch(f)).kind == "cannot_hand_over" and not contacted


def test_a_name_that_does_not_resolve_is_a_failed_fetch():
    def nowhere(host):
        raise OSError("no such host")
    assert refused(lambda: fetch(fetcher(resolver=nowhere))).kind == "fetch_failed"


def test_the_connection_goes_to_the_address_that_was_checked_and_the_name_is_resolved_once():
    seen, lookups = [], []

    def handler(request):
        seen.append((request.url.host, request.headers.get("host"), request.extensions.get("sni_hostname")))
        return httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})

    def resolver(host):
        lookups.append(host)
        return [PUBLIC] if len(lookups) == 1 else ["10.0.0.5"]  # DNS that answers differently the second time (rebinding)

    assert fetch(fetcher(handler, resolver=resolver)).content == CSV
    assert seen == [(PUBLIC, HOST, HOST)] and lookups == [HOST]


# ============================== redirects ======================================================================================

def test_a_redirect_to_another_allowed_host_is_followed_and_checked_again():
    hosts, lookups = [], []

    def handler(request):
        hosts.append(request.headers["host"])
        if request.headers["host"] == HOST:
            return httpx.Response(302, headers={"location": "https://cdn.openai.com/file-1"})
        return httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})

    def resolver(host):
        lookups.append(host)
        return [PUBLIC]

    assert fetch(fetcher(handler, resolver=resolver)).content == CSV
    assert hosts == [HOST, "cdn.openai.com"] and lookups == [HOST, "cdn.openai.com"]


@pytest.mark.parametrize("target", [
    "http://169.254.169.254/latest/meta-data", "https://169.254.169.254/", "https://evil.example/x", "https://127.0.0.1/x",
    "http://files.oaiusercontent.com/x", "file:///etc/passwd", "//evil.example/x"])
def test_a_redirect_that_breaks_a_rule_is_not_followed(target):
    contacted = []

    def handler(request):
        contacted.append(request.headers["host"])
        return httpx.Response(302, headers={"location": target})

    why = refused(lambda: fetch(fetcher(handler)))
    assert why.kind == "fetch_failed" and contacted == [HOST]


def test_a_redirect_to_a_name_that_resolves_privately_is_not_followed():
    def resolver(host):
        return [PUBLIC] if host == HOST else ["10.0.0.9"]
    contacted = []

    def handler(request):
        contacted.append(request.headers["host"])
        return httpx.Response(307, headers={"location": "https://internal.openai.com/x"})

    assert refused(lambda: fetch(fetcher(handler, resolver=resolver))).kind in ("fetch_failed", "cannot_hand_over")
    assert contacted == [HOST]


def test_at_most_two_redirects_are_followed():
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(302, headers={"location": f"https://{HOST}/again"})

    assert refused(lambda: fetch(fetcher(handler))).reason == "too many redirects" and len(count) == 3


# ============================== size, time, type ===============================================================================

def test_the_size_cap_is_the_importers_upload_cap():
    assert FileFetcher().max_bytes == importer.MAX_UPLOAD_BYTES == 20 * 1024 * 1024


def test_a_declared_size_over_the_cap_is_refused_before_reading():
    read = []

    class Spy(httpx.SyncByteStream):
        def __iter__(self):
            read.append(1)
            yield b"x"

    f = fetcher(lambda r: httpx.Response(200, headers={"content-type": "text/csv", "content-length": "5000"}, stream=Spy()),
                max_bytes=1000)
    assert refused(lambda: fetch(f)).kind == "too_large" and not read


def test_a_body_over_the_cap_is_cut_off_whatever_it_declares():
    class Endless(httpx.SyncByteStream):
        def __iter__(self):
            while True:
                yield b"a,b,c\n" * 100

    f = fetcher(lambda r: httpx.Response(200, headers={"content-type": "text/csv"}, stream=Endless()), max_bytes=10_000)
    assert refused(lambda: fetch(f)).kind == "too_large"


def test_a_file_exactly_at_the_cap_is_taken():
    f = fetcher(lambda r: httpx.Response(200, content=b"a" * 1000, headers={"content-type": "text/csv"}), max_bytes=1000)
    assert len(fetch(f).content) == 1000


def test_a_server_that_never_answers_does_not_hold_the_caller():
    started = time.monotonic()
    f = fetcher(lambda r: (time.sleep(4), httpx.Response(200, content=CSV, headers={"content-type": "text/csv"}))[1], seconds=0.5)
    why = refused(lambda: fetch(f))
    assert why.reason == "too slow" and time.monotonic() - started < 2.5


def test_a_server_that_drips_the_body_is_cut_off_at_the_deadline():
    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                time.sleep(0.15)
                yield b"a,b\n"

    started = time.monotonic()
    f = fetcher(lambda r: httpx.Response(200, headers={"content-type": "text/csv"}, stream=Drip()), seconds=0.6)
    assert refused(lambda: fetch(f)).reason == "too slow" and time.monotonic() - started < 3
    # the worker itself stopped at the deadline and gave its slot back (it is not left reading a server that drips for 15 s)
    assert f._slots._value == file_fetch.MAX_CONCURRENT


def test_a_name_that_takes_forever_to_resolve_does_not_hold_the_caller():
    started = time.monotonic()
    f = fetcher(resolver=lambda host: (time.sleep(4), [PUBLIC])[1], seconds=0.5)
    assert refused(lambda: fetch(f)).reason == "too slow" and time.monotonic() - started < 2.5


def test_only_a_few_fetches_run_at_once_and_a_busy_process_says_so():
    import threading
    gate = threading.Event()
    f = fetcher(lambda r: (gate.wait(5), httpx.Response(200, content=CSV, headers={"content-type": "text/csv"}))[1], seconds=10)
    threads = [threading.Thread(target=lambda: f.fetch(Handoff(GOOD)), daemon=True) for _ in range(file_fetch.MAX_CONCURRENT)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    try:
        assert refused(lambda: fetch(f)).kind == "busy"
    finally:
        gate.set()


@pytest.mark.parametrize("kind", ["text/csv", "text/csv; charset=utf-8", "text/plain", "application/csv", "application/vnd.ms-excel"])
def test_a_text_like_answer_is_taken(kind):
    f = fetcher(lambda r: httpx.Response(200, content=CSV, headers={"content-type": kind}))
    assert fetch(f).content == CSV


@pytest.mark.parametrize("kind", ["application/json", "text/html", "text/html; charset=utf-8", "application/zip", "image/png",
                                  "application/pdf", "", "application/x-msdownload"])
def test_an_answer_that_is_not_text_is_refused(kind):
    f = fetcher(lambda r: httpx.Response(200, content=CSV, headers={"content-type": kind}))
    assert refused(lambda: fetch(f, mime_type="text/csv", file_name="my.csv")).kind == "not_text"


def test_a_generic_binary_type_is_taken_only_when_the_host_says_it_is_a_csv():
    f = fetcher(lambda r: httpx.Response(200, content=CSV, headers={"content-type": "application/octet-stream"}))
    assert refused(lambda: fetch(f)).kind == "not_text"
    assert refused(lambda: fetch(f, mime_type="image/png", file_name="a.png")).kind == "not_text"
    assert fetch(f, file_name="export.csv").content == CSV
    assert fetch(f, mime_type="text/csv").content == CSV


def test_a_compressed_answer_is_not_read():
    import gzip
    small = gzip.compress(CSV)  # a real gzip body: a fetcher that decoded it would succeed, so refusing is the check's own doing
    f = fetcher(lambda r: httpx.Response(200, content=small, headers={"content-type": "text/csv", "content-encoding": "gzip"}))
    why = refused(lambda: fetch(f))
    assert why.kind == "fetch_failed" and why.reason == "Content-Encoding is not identity"


@pytest.mark.parametrize("status", [401, 403, 404, 410, 429, 500, 503, 204, 206])
def test_an_answer_that_is_not_the_file_is_a_failed_fetch(status):
    f = fetcher(lambda r: httpx.Response(status, content=b"nope", headers={"content-type": "text/csv"}))
    why = refused(lambda: fetch(f))
    assert why.kind == "fetch_failed" and why.status == status


# ============================== what is sent ===================================================================================

def test_the_fetch_is_a_bare_get_with_no_cookie_no_authorization_and_no_referrer():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})

    fetch(fetcher(handler))
    (request,) = sent
    assert request.method == "GET" and not request.content
    names = {k.lower() for k in request.headers}
    assert not names & {"cookie", "authorization", "referer", "proxy-authorization", "x-forwarded-for"}, names
    assert request.headers["accept-encoding"] == "identity"
    assert request.url.scheme == "https" and request.url.port in (None, 443)


def test_environment_proxies_and_cookies_are_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert fetch(fetcher()).content == CSV


# ============================== the tool =======================================================================================

def token_of(agent, *scopes):
    return make_token(agent, scopes=list(scopes or ("read",)))


def file_arg(url=GOOD, **extra):
    return {"download_url": url, "file_id": "file-AbC123", "mime_type": "text/csv", "file_name": "my_cards.csv", **extra}


def intake_with(app, handler=None, **kw):
    app.state.file_fetcher = fetcher(handler, **kw)
    return app.state.file_fetcher


def staged_rows(app):
    with app.state.db.sessions() as db:
        return db.query(StagedUpload).count()


def imports_of(app):
    with app.state.db.sessions() as db:
        return db.query(Import).count()


def test_the_tool_declares_the_file_parameter_the_way_chatgpt_reads_it():
    tool = mcp.BY_NAME["import_collection_file"]
    schema = tool.schema()
    assert schema["_meta"] == {"openai/fileParams": ["file"]}
    file = schema["inputSchema"]["properties"]["file"]
    assert file["type"] == "object" and set(file["properties"]) == {"download_url", "file_id", "mime_type", "file_name"}
    assert file["required"] == ["download_url", "file_id"]
    assert schema["annotations"]["destructiveHint"] is False and schema["annotations"]["readOnlyHint"] is False
    assert tool.any_scope and tool.write
    assert {t.name for t in mcp.TOOLS if t.write and t.any_scope} == {"start_collection_upload", "import_collection_file", "sign_out"}
    # the view of another tool keeps its own _meta
    assert "_meta" in mcp.BY_NAME["show_owned_printings"].schema(ui=True) and "_meta" not in mcp.BY_NAME["get_deck"].schema()


def test_the_file_parameter_survives_the_compact_list_which_hides_it_behind_vault_call_for_any_other_tool(agent, bot):
    token = token_of(agent, "read", "write")
    names = [t.name for t in mcp.TOOLS if t.listed_for({"read", "write"})]
    compact = mcp_profile.tool_list(mcp.BY_NAME, ("read", "write"), ui=True)
    by = {t["name"]: t for t in compact}
    assert "import_collection_file" in mcp_profile.CORE and "import_collection_file" in by
    assert by["import_collection_file"]["_meta"] == {"openai/fileParams": ["file"]}
    assert set(by["import_collection_file"]["inputSchema"]["properties"]["file"]["properties"]) >= {"download_url", "file_id"}
    # the two guards the owner's app depends on, with the views on: 30 tools, 30,000 bytes
    assert len(compact) <= 30 and len(json.dumps(compact)) < 30_000, (len(compact), len(json.dumps(compact)))
    read_only = mcp_profile.tool_list(mcp.BY_NAME, ("read",), ui=True)
    assert "import_collection_file" in {t["name"] for t in read_only} and len(read_only) <= 30
    assert names  # the full list is unchanged for every other client
    # a dispatcher cannot carry it: it is called by its own name
    assert "listed directly" in mcp_profile.refusal(mcp.BY_NAME["import_collection_file"], mcp_profile.CALL)
    assert "listed directly" in mcp_profile.refusal(mcp.BY_NAME["import_collection_file"], mcp_profile.WRITE)
    # the slot came from expert_brief, which a compact client reaches through vault_call
    assert "expert_brief" not in mcp_profile.CORE and mcp_profile.refusal(mcp.BY_NAME["expert_brief"], mcp_profile.CALL) is None


def test_a_file_dropped_in_the_chat_is_staged_and_previewed_and_nothing_is_imported(app, agent, bot):
    seen = intake_with(app)
    before = imports_of(app)
    write = token_of(agent, "read", "write")
    out = call_tool(bot, write, "import_collection_file", file=file_arg())
    assert not out["isError"], out
    answer = out["structuredContent"]
    assert answer["status"] == "uploaded" and answer["changed"] is False and answer["filename"] == "my_cards.csv"
    assert answer["rows"] > 0 and answer["copies"] > 0 and "changes" in answer and "merge" in answer
    assert len(answer["content_hash"]) == 64 and answer["id"] and "confirm_staged_upload" in answer["next"]
    assert imports_of(app) == before  # nothing was imported
    assert seen is app.state.file_fetcher
    # the same preview, read the other way: the staged upload is the person's own
    again = call_tool(bot, write, "get_staged_upload", upload_id=answer["id"])["structuredContent"]
    assert again["status"] == "uploaded" and again["content_hash"] == answer["content_hash"] and again["changes"] == answer["changes"]
    # the person's yes: confirm_staged_upload, bound to the preview's hash
    done = call_tool(bot, write, "confirm_staged_upload", upload_id=answer["id"], confirm=True, content_hash=answer["content_hash"])
    assert not done["isError"], done
    assert imports_of(app) == before + 1 and staged_rows(app) == 0  # applied, and the staged file is deleted


def test_a_read_only_connection_can_drop_a_file_and_see_the_preview_but_cannot_import_it(app, agent, bot):
    intake_with(app)
    read = token_of(agent)
    before = imports_of(app)
    out = call_tool(bot, read, "import_collection_file", file=file_arg())
    assert not out["isError"], out
    answer = out["structuredContent"]
    assert answer["status"] == "uploaded"
    refused_ = call_tool(bot, read, "confirm_staged_upload", upload_id=answer["id"], confirm=True, content_hash=answer["content_hash"])
    assert refused_["isError"] is True and "read-only" in refused_["content"][0]["text"]
    assert imports_of(app) == before and staged_rows(app) == 1
    # the person's write connection can confirm what the read-only one staged
    write = token_of(agent, "read", "write")
    done = call_tool(bot, write, "confirm_staged_upload", upload_id=answer["id"], confirm=True, content_hash=answer["content_hash"])
    assert not done["isError"] and imports_of(app) == before + 1


def test_the_rest_route_is_open_to_a_read_only_token_only_at_its_exact_path(app, agent):
    from vault.app import READ_ONLY_POSTS, is_write
    assert "/api/v1/uploads/from-file" in READ_ONLY_POSTS
    assert is_write("POST", "/api/v1/uploads/from-file") is False
    assert is_write("PUT", "/api/v1/uploads/from-file") and is_write("POST", "/api/v1/uploads/from-file/")
    assert is_write("POST", "/api/v1/uploads/1/apply") and not [p for p in READ_ONLY_POSTS if p.endswith("/apply")]


def test_another_person_cannot_read_or_import_a_file_that_was_dropped_in_the_chat(app, agent, bot):
    intake_with(app)
    write = token_of(agent, "read", "write")
    answer = call_tool(bot, write, "import_collection_file", file=file_arg())["structuredContent"]
    with TestClient(app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        theirs = make_token(bob, scopes=["read", "write"])
        for tool, args in (("get_staged_upload", {}), ("confirm_staged_upload", {"confirm": True, "content_hash": answer["content_hash"]})):
            out = call_tool(bot, theirs, tool, upload_id=answer["id"], **args)
            assert out["isError"] is True, (tool, out)
        # and their own drop is theirs: their id is not ours
        mine = call_tool(bot, theirs, "import_collection_file", file=file_arg())["structuredContent"]
        assert call_tool(bot, write, "get_staged_upload", upload_id=mine["id"])["isError"] is True
    assert call_tool(bot, write, "get_staged_upload", upload_id=answer["id"])["structuredContent"]["status"] == "uploaded"


def test_a_person_holds_one_dropped_file_at_a_time_and_the_same_file_is_the_same_upload(app, agent, bot):
    intake_with(app)
    token = token_of(agent, "read", "write")
    first = call_tool(bot, token, "import_collection_file", file=file_arg())["structuredContent"]
    same = call_tool(bot, token, "import_collection_file", file=file_arg(f"https://{HOST}/file-AbC123?sig=another-token"))["structuredContent"]
    assert same["id"] == first["id"] and staged_rows(app) == 1  # a host's duplicate call is the same row
    intake_with(app, lambda r: httpx.Response(200, content=CSV + b"\n", headers={"content-type": "text/csv"}))
    second = call_tool(bot, token, "import_collection_file", file=file_arg())["structuredContent"]
    assert second["id"] != first["id"] and staged_rows(app) == 1  # a different file replaces the earlier one
    assert call_tool(bot, token, "get_staged_upload", upload_id=first["id"])["isError"] is True


def test_a_dropped_file_goes_into_the_persons_own_bucket_only(app, agent, bot):
    intake_with(app)
    token = token_of(agent, "read", "write")
    mine = agent.post(f"{V1}/collection/buckets", json={"name": "Trade binder"}).json()
    out = call_tool(bot, token, "import_collection_file", file=file_arg(), bucket_id=mine["id"])["structuredContent"]
    assert out["bucket"]["name"] == "Trade binder"
    with TestClient(app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        theirs = bob.post(f"{V1}/collection/buckets", json={"name": "Bob's box"}).json()
    for bucket_id in (theirs["id"], 999_999):  # another person's bucket and nobody's: the same 404, and nothing is fetched
        fetched = []
        intake_with(app, lambda r: fetched.append(r) or httpx.Response(200, content=CSV, headers={"content-type": "text/csv"}))
        err = call_tool(bot, token, "import_collection_file", file=file_arg(), bucket_id=bucket_id)
        assert err["isError"] is True and "Bucket not found" in err["content"][0]["text"] and not fetched


def test_a_file_that_is_not_a_collection_is_not_staged(app, agent, bot):
    intake_with(app, lambda r: httpx.Response(200, content=b"\xff\xfe not utf8", headers={"content-type": "text/csv"}))
    out = call_tool(bot, token_of(agent), "import_collection_file", file=file_arg())
    assert not out["isError"] and out["structuredContent"]["status"] == "unreadable" and out["structuredContent"]["changed"] is False
    assert staged_rows(app) == 0
    intake_with(app, lambda r: httpx.Response(200, content=b"hello,world\n1,2\n", headers={"content-type": "text/csv"}))
    out = call_tool(bot, token_of(agent), "import_collection_file", file=file_arg())["structuredContent"]
    assert out["status"] == "unreadable" and staged_rows(app) == 0


def test_a_person_is_limited_to_their_import_allowance_a_minute(app, agent, bot):
    intake_with(app)
    token = token_of(agent)
    answers = [call_tool(bot, token, "import_collection_file", file=file_arg()) for _ in range(app.state.settings.import_limit + 2)]
    flags = [a["isError"] for a in answers]
    assert flags[: app.state.settings.import_limit] == [False] * app.state.settings.import_limit and flags[-1] is True
    assert "Too many" in answers[-1]["content"][0]["text"]


# ---- what the host sends when it cannot hand the file over ------------------------------------------------------------------

NOT_A_FILE = [
    ("the string a phone app sends", "chat_upload"),
    ("a local path", {"download_url": "C:\\Users\\someone\\Downloads\\my_cards.csv", "file_id": "C:\\Users\\someone\\Downloads\\my_cards.csv"}),
    ("a posix path", {"download_url": "/mnt/data/my_cards.csv", "file_id": "file-1"}),
    ("a file address", {"download_url": "file:///home/someone/my_cards.csv", "file_id": "file-1"}),
    ("a string path", "/mnt/data/my_cards.csv"),
    ("http, not https", {"download_url": f"http://{HOST}/f?sig={TOKEN}", "file_id": "file-1"}),
    ("no download_url", {"file_id": "file-1", "file_name": "my_cards.csv"}),
    ("an empty object", {}),
    ("a number", 7),
    ("a list of nothing", []),
    ("a download_url that is not text", {"download_url": 5, "file_id": "x"}),
]


@pytest.mark.parametrize("label, value", NOT_A_FILE, ids=[n for n, _ in NOT_A_FILE])
def test_a_value_that_is_not_a_web_address_is_answered_in_one_plain_sentence_and_reaches_no_network(app, agent, bot, label, value):
    contacted = []
    intake_with(app, lambda r: contacted.append(r) or httpx.Response(200, content=CSV, headers={"content-type": "text/csv"}))
    out = call_tool(bot, token_of(agent), "import_collection_file", file=value)
    assert not out["isError"], out
    answer = out["structuredContent"]
    assert answer["status"] == "cannot_receive" and answer["changed"] is False
    assert answer["say"].count(".") == 1 and "Import CSV" in answer["say"] and "cannot hand the file" in answer["say"]
    assert not contacted and staged_rows(app) == 0


def test_a_missing_file_key_is_the_same_sentence(app, agent, bot):
    intake_with(app)
    out = call_tool(bot, token_of(agent), "import_collection_file")
    assert not out["isError"] and out["structuredContent"]["status"] == "cannot_receive"


def event_lines(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "vault.access" and '"file_intake"' in r.getMessage()]


def test_the_shape_of_a_value_that_is_not_a_file_goes_to_the_log_and_never_a_path(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    token = token_of(agent)
    call_tool(bot, token, "import_collection_file", file="chat_upload")
    call_tool(bot, token, "import_collection_file",
              file={"download_url": "C:\\Users\\someone\\Downloads\\my_cards.csv", "file_id": "file-1"})
    first, second = event_lines(caplog)[-2:]
    assert first["outcome"] == "cannot_hand_over" and first["shape"] == {"type": "str", "kind": "token", "prefix": "chat_upload", "length": 11}
    path = r"C:\Users\someone\Downloads\my_cards.csv"
    assert second["shape"]["download_url"] == {"type": "str", "kind": "windows_path", "prefix": "C:", "length": len(path)}
    assert second["shape"]["keys"] == ["download_url", "file_id"] and second["shape"]["other_keys"] == 0
    assert "someone" not in caplog.text and "my_cards" not in caplog.text


def test_a_host_that_is_refused_is_logged_by_name_only_and_the_answer_says_the_app_cannot_hand_the_file_over(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    contacted = []
    intake_with(app, lambda r: contacted.append(r) or httpx.Response(200, content=CSV, headers={"content-type": "text/csv"}))
    url = f"https://files.example-storage.net/file-AbC123/my_cards.csv?X-Amz-Signature={TOKEN}"
    out = call_tool(bot, token_of(agent), "import_collection_file", file=file_arg(url))
    answer = out["structuredContent"]
    assert not out["isError"] and answer["status"] == "cannot_receive" and "cannot hand the file to the Vault yet" in answer["say"]
    (line,) = event_lines(caplog)[-1:]
    assert line["outcome"] == "cannot_hand_over" and line["host"] == "files.example-storage.net"
    assert not contacted
    for secret in (TOKEN, "file-AbC123", "X-Amz-Signature", "my_cards.csv", "/file-"):
        assert secret not in caplog.text and secret not in json.dumps(out)


def test_the_address_never_reaches_the_log_or_an_answer_whatever_happens_to_it(app, agent, bot, caplog):
    """The address carries the file's token. Success, a failed fetch, a redirect, a private address, a host not on the list, an
    unreadable file and a server error: not one log line or answer holds any part of it."""
    caplog.set_level(logging.DEBUG)
    token = token_of(agent, "read", "write")
    answers = []

    def run(handler=None, **kw):
        intake_with(app, handler, **kw)
        answers.append(call_tool(bot, token, "import_collection_file", file=file_arg()))

    run()  # staged
    run(lambda r: httpx.Response(500, content=b"boom " + GOOD.encode()))  # an error page that repeats the address
    run(lambda r: httpx.Response(302, headers={"location": f"https://evil.example/{TOKEN}?sig={TOKEN}"}))
    run(resolver=lambda host: ["10.1.2.3"])
    run(lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"cannot connect to {GOOD}")))  # an exception that says it
    run(lambda r: (_ for _ in ()).throw(RuntimeError(GOOD)))
    run(lambda r: httpx.Response(200, content=CSV, headers={"content-type": "application/json"}))
    assert all(not a.get("isError") for a in answers)
    everything = caplog.text + json.dumps(answers)
    for secret in (TOKEN, "SECRET-TOKEN", "file-AbC123", "se=2026", f"{PUBLIC}/file", HOST + "/file"):
        assert secret not in everything, secret
    # the first run really did log something: the check above is not vacuous
    assert any(e["outcome"] == "staged" for e in event_lines(caplog)) and len(event_lines(caplog)) == 7


def test_a_failed_fetch_says_what_to_do_in_plain_words_and_changes_nothing(app, agent, bot):
    intake_with(app, lambda r: httpx.Response(404, content=b"gone"))
    out = call_tool(bot, token_of(agent), "import_collection_file", file=file_arg())
    answer = out["structuredContent"]
    assert not out["isError"] and answer["status"] == "fetch_failed" and answer["changed"] is False
    assert "Import CSV" in answer["say"] and staged_rows(app) == 0


def test_a_file_over_the_cap_is_refused_in_plain_words(app, agent, bot):
    intake_with(app, lambda r: httpx.Response(200, content=b"a" * 2000, headers={"content-type": "text/csv"}), max_bytes=1000)
    answer = call_tool(bot, token_of(agent), "import_collection_file", file=file_arg())["structuredContent"]
    assert answer["status"] == "too_large" and "20 MB" in answer["say"] and staged_rows(app) == 0


def test_the_filename_shown_for_a_staged_file_is_the_declared_name_without_a_path(app, agent, bot):
    intake_with(app)
    token = token_of(agent)
    out = call_tool(bot, token, "import_collection_file", file=file_arg(file_name="C:\\Users\\x\\..\\evil\x00<b>.csv"))
    assert out["structuredContent"]["filename"] == "evil<b>.csv"
    intake_with(app, lambda r: httpx.Response(200, content=CSV + b"\n\n", headers={"content-type": "text/csv"}))
    out = call_tool(bot, token, "import_collection_file", file={"download_url": GOOD, "file_id": "f"})
    assert out["structuredContent"]["filename"] == "chat-upload.csv"  # never taken from the address


def test_a_dropped_file_lives_for_an_hour_like_a_link_and_is_marked_so_no_ticket_can_match_it(app, agent, bot):
    from datetime import datetime, timedelta, timezone
    intake_with(app)
    token = token_of(agent)
    answer = call_tool(bot, token, "import_collection_file", file=file_arg())["structuredContent"]
    with app.state.db.sessions() as db:
        row = db.get(StagedUpload, answer["id"])
        assert row.ticket_hash.startswith(intake.MARKER) and len(row.ticket_hash) <= 64
        assert row.expires_at > datetime.now(timezone.utc) + timedelta(minutes=50)
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert call_tool(bot, token, "get_staged_upload", upload_id=answer["id"])["isError"] is True


def test_a_fetched_upload_has_no_link_the_upload_page_could_open(app, agent, bot):
    intake_with(app)
    answer = call_tool(bot, token_of(agent), "import_collection_file", file=file_arg())["structuredContent"]
    assert "ticket" not in json.dumps(answer) and "url" not in answer
    with app.state.db.sessions() as db:
        ticket_hash = db.get(StagedUpload, answer["id"]).ticket_hash
    with TestClient(app) as browser:
        for guess in (ticket_hash, ticket_hash[len(intake.MARKER):], "intake-"):
            assert browser.get(f"/upload?ticket={guess}").status_code == 404
            assert browser.post("/upload", data={"ticket": guess}, files={"file": ("a.csv", CSV, "text/csv")}).status_code == 404


# ---- review of PR #478: the log, the race, the contract, the twin -------------------------------------------------------------

def test_a_host_name_is_logged_by_its_last_three_labels_so_a_secret_in_the_first_ones_stays_out():
    assert file_fetch._host_for_log("files.oaiusercontent.com") == "files.oaiusercontent.com"
    assert file_fetch._host_for_log("tok-9f2c71d4e0a5.files.oaiusercontent.com") == "*.files.oaiusercontent.com"
    assert file_fetch._host_for_log("a.b.c.d.example-storage.net") == "*.d.example-storage.net"
    assert file_fetch._host_for_log("FILES.Example.COM") == "files.example.com"
    assert len(file_fetch._host_for_log("x" * 400 + ".example.com")) <= 80


def test_a_refused_host_with_a_secret_in_its_name_does_not_reach_the_log(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    url = "https://tok-9f2c71d4e0a5.region-7.files.example-storage.net/file?sig=abc"
    out = call_tool(bot, token_of(agent), "import_collection_file", file=file_arg(url))
    assert out["structuredContent"]["status"] == "cannot_receive"
    assert "tok-9f2c71d4e0a5" not in caplog.text and "region-7" not in caplog.text
    assert event_lines(caplog)[-1]["host"] == "*.files.example-storage.net"


def test_two_drops_at_once_leave_one_row_and_one_upload_id(app, agent):
    """The decision (is there an identical row? replace the earlier one) is made once at a time per person: two simultaneous drops
    both found no row and each inserted one, so a host's duplicate call got two ids and a person held two fetched files."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from vault.models import User
    with app.state.db.sessions() as db:
        uid = db.query(User.id).first()[0]
    barrier = threading.Barrier(6)

    def drop(content: bytes) -> int:
        with app.state.db.sessions() as db:
            user = db.get(User, uid)
            barrier.wait()
            return intake.stage(db, user, content, "a.csv", None).id

    with ThreadPoolExecutor(6) as pool:
        same = list(pool.map(drop, [CSV] * 6))
    assert len(set(same)) == 1 and staged_rows(app) == 1
    barrier = threading.Barrier(6)
    with ThreadPoolExecutor(6) as pool:
        different = list(pool.map(drop, [CSV + b"\n" * i for i in range(1, 7)]))
    assert len(set(different)) == 6 and staged_rows(app) == 1  # each replaced the one before: one fetched file


def test_the_route_has_a_response_model_in_openapi(app):
    with TestClient(app) as c:
        spec = c.get("/api/openapi.json").json()
    op = spec["paths"]["/api/v1/uploads/from-file"]["post"]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    model = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
    assert {"status", "changed", "say", "id", "content_hash"} <= set(model["properties"]) and model["required"] == ["status"]


def test_the_file_host_twin_serves_a_file_the_way_the_documentation_says_and_the_vault_reads_it(app, agent, bot):
    """Not a recording (twins/openai_files.py says so): the host, a signed link valid for five minutes, a CSV content type."""
    from twins import Universe
    universe = Universe()
    token = token_of(agent, "read", "write")
    app.state.file_fetcher = FileFetcher(file_fetch.DEFAULT_HOSTS, universe.transport, universe.resolve)
    handed = universe.openai_files.attach(CSV, file_name="my_cards.csv")
    assert handed["download_url"].startswith("https://files.oaiusercontent.com/file-") and set(handed) == {
        "download_url", "file_id", "mime_type", "file_name"}
    out = call_tool(bot, token, "import_collection_file", file=handed)
    assert not out["isError"] and out["structuredContent"]["status"] == "uploaded", out
    assert universe.openai_files.calls[-1].path == "/" + handed["file_id"] and not universe.escapes
    # the link has run out (five minutes), or is answered with another type, or redirects away: nothing is staged
    universe.openai_files.advance(301)  # past the link's five minutes: the expiry in the link itself
    assert call_tool(bot, token, "import_collection_file", file=handed)["structuredContent"]["status"] == "fetch_failed"
    other = universe.openai_files.attach(CSV, file_name="other.csv")
    universe.openai_files.serve_as(other["file_id"], "application/json")
    assert call_tool(bot, token, "import_collection_file", file=other)["structuredContent"]["status"] == "not_text"
    away = universe.openai_files.attach(CSV + b"\n", file_name="away.csv")
    universe.openai_files.redirect(away["file_id"], "http://169.254.169.254/latest/meta-data")
    assert call_tool(bot, token, "import_collection_file", file=away)["structuredContent"]["status"] == "fetch_failed"
    assert not universe.escapes


def test_the_logged_shape_never_holds_a_value_that_could_be_a_token_or_a_name(app, agent, bot, caplog):
    """Review of PR #478: a short string was logged whole as its prefix and object keys verbatim. A label like chat_upload is
    kept (that is the evidence the owner asked for); anything else is logged as its form; unknown keys only as a count."""
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    token = token_of(agent)
    secret = "Ab3dEf9GhIjKlMn0pQr"  # an identifier or a token a host might send in place of a file
    call_tool(bot, token, "import_collection_file", file=secret)
    call_tool(bot, token, "import_collection_file", file={"download_url": "x", "file_id": "y", "jane.doe@example.com": 1, "sig-9f2c": 2})
    call_tool(bot, token, "import_collection_file", file="ftp-9f2c71d4e0a5b://x")
    first, second, third = event_lines(caplog)[-3:]
    for label in ("session_token", "john_smith", "secretkey://x"):  # words and schemes a caller chose are not kept either
        call_tool(bot, token, "import_collection_file", file=label)
    assert [e["shape"]["prefix"] for e in event_lines(caplog)[-3:]] == ["aaaaaaa_aaaa", "aaaa_aaaaa", "aaaaaaaaa://"]
    assert first["shape"] == {"type": "str", "kind": "token", "prefix": "Aa9aAa9AaAaA", "length": len(secret)}  # its form, not its characters
    assert second["shape"]["keys"] == ["download_url", "file_id"] and second["shape"]["other_keys"] == 2
    assert third["shape"]["kind"] == "other_scheme" and "9f2c71d4e0a5b" not in json.dumps(third)
    for secret_text in (secret, secret[:12], "jane.doe", "example.com", "sig-9f2c", "9f2c71d4e0a5b", "session_token", "john_smith", "secretkey"):
        assert secret_text not in caplog.text, secret_text
    assert file_fetch.mask("Ab3dEf9GhIjK") == "Aa9aAa9AaAaA" and file_fetch.shape_of("chat_upload")["prefix"] == "chat_upload"


def test_a_dropped_file_is_not_one_of_the_five_links_and_is_not_listed_with_them(app, agent, bot):
    """Review of PR #478: the dropped file shares the table with the upload links; the link cap, the replacement of the oldest
    waiting link and the list of held files must count links only, so 'one dropped file beside five links' holds."""
    from vault import uploads
    intake_with(app)
    token = token_of(agent, "read", "write")
    dropped = call_tool(bot, token, "import_collection_file", file=file_arg())["structuredContent"]
    links = [call_tool(bot, token, "start_collection_upload")["structuredContent"] for _ in range(uploads.MAX_OPEN)]
    assert all("url" in link and "replaced_upload_id" not in link for link in links)  # five links fit beside the dropped file
    assert staged_rows(app) == uploads.MAX_OPEN + 1
    sixth = call_tool(bot, token, "start_collection_upload")["structuredContent"]
    assert sixth["replaced_upload_id"] == links[0]["id"]  # the oldest waiting link goes, never the dropped file
    assert call_tool(bot, token, "get_staged_upload", upload_id=dropped["id"])["structuredContent"]["status"] == "uploaded"
    with app.state.db.sessions() as db:  # every link now holds a file
        for row in db.query(StagedUpload).filter(StagedUpload.content.is_(None)):
            row.content, row.filename, row.uploaded_at = b"x", "a.csv", row.expires_at
        db.commit()
    denied = call_tool(bot, token, "start_collection_upload")
    text = denied["content"][0]["text"]
    assert denied["isError"] is True and "a file has been uploaded through each one" in text
    assert f"id {sixth['id']} (" in text and f"id {dropped['id']} (" not in text  # the listing is of links
