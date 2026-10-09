"""Nothing contacts a shop (#84, docs/data-sources.md, issue #80's finding: Cardmarket, Card Kingdom and Magic Madhouse are linked to,
never called). The digital twin universe has a twin for every outside service the Vault calls; a request to any other host is an
"escape" that every test asserting ``universe.escapes`` fails on. This pins both halves: no twin answers for a shop host, and a call to one
is recorded as an escape."""

import httpx
import pytest

from twins.universe import Universe

SHOPS = ("cardmarket.com", "cardkingdom.com", "magicmadhouse.co.uk", "tcgplayer.com")


def test_no_twin_answers_for_a_shop_so_the_vault_calls_none():
    u = Universe()
    assert [h for h in u.by_host if any(h == s or h.endswith("." + s) for s in SHOPS)] == []


@pytest.mark.parametrize("url", ["https://www.cardmarket.com/en/Magic/Products/Search?searchString=Sol+Ring",
                                 "https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=Sol+Ring"])
def test_a_call_to_a_shop_is_recorded_as_an_escape_that_fails_the_tests_that_watch_for_one(url):
    u = Universe()
    with httpx.Client(transport=u.transport) as client:
        try:
            client.get(url)
        except Exception:  # the twin universe refuses what it has no twin for
            pass
    assert u.escapes and url.split("?")[0] in u.escapes[0]
