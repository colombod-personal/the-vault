"""Small robustness gaps from the data-route review (#350): look-alike hosts, odd digits, huge ids and nested cursors are
refused or labelled correctly, never a 500."""

import base64

import pytest

V1 = "/api/v1"


@pytest.mark.parametrize("link,kind", [
    ("https://archidekt.com/decks/1", "archidekt"), ("https://www.archidekt.com/decks/1", "archidekt"),
    ("https://evilarchidekt.com/decks/1", "link"), ("https://archidekt.com.evil.example/decks/1", "link"),
    ("https://moxfield.com/decks/abc", "moxfield"), ("https://www.moxfield.com/decks/abc", "moxfield"),
    ("https://xmoxfield.com/decks/abc", "link"), ("https://example.org/deck", "link")])
def test_a_deck_is_labelled_archidekt_or_moxfield_only_for_that_domain_or_its_subdomains(signed_in, link, kind):
    made = signed_in.post(f"{V1}/decks", json={"name": "d", "text": "1 Sol Ring", "source_url": link})
    assert made.status_code == 201 and made.json()["source"] == kind, link


def test_an_archidekt_link_with_a_non_ascii_digit_is_a_400_on_import_and_refresh(signed_in):
    assert signed_in.post(f"{V1}/decks/import-link", json={"url": "https://archidekt.com/decks/²"}).status_code == 400
    deck = signed_in.post(f"{V1}/decks", json={"name": "d", "text": "1 Sol Ring",
                                               "source_url": "https://archidekt.com/decks/²"}).json()
    refreshed = signed_in.post(f"{V1}/decks/{deck['id']}/refresh", json={})
    assert refreshed.status_code == 400 and "deck number" in refreshed.json()["detail"]


def test_an_upload_id_that_cannot_exist_is_a_422_not_a_500(signed_in):
    assert signed_in.get(f"{V1}/uploads/99999999999").status_code == 422
    assert signed_in.post(f"{V1}/uploads/99999999999/apply").status_code in (404, 405, 422)
    assert signed_in.get(f"{V1}/uploads/0").status_code == 422


def test_a_cursor_nested_too_deeply_is_a_400(signed_in):
    cursor = base64.urlsafe_b64encode(b"[" * 5000).decode().rstrip("=")
    res = signed_in.get(f"{V1}/decks", params={"cursor": cursor})
    assert res.status_code == 400 and res.json()["detail"] == "Invalid cursor"
