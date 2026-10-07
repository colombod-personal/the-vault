"""The web app's Undo on a history entry (#81, docs/owned-cards-updates.md rule 5).

The server side (the history marks the one entry that can be undone; the person's own session previews and applies the
undo) is tests/test_owned_changes.py::test_the_history_marks_the_one_change_that_can_be_undone_and_the_web_session_can_undo_it,
and the client's two-step call is tests/js/api_client.test.mjs. This pins the Account panel to them, and the screen was
used in a real browser (docs/graph-and-lab-review.md, "Undo")."""

import re
from pathlib import Path

PUBLIC = Path(__file__).parent.parent / "public"
ACCOUNT = (PUBLIC / "views" / "account.jsx").read_text(encoding="utf-8")
APP = (PUBLIC / "app.jsx").read_text(encoding="utf-8")
SECTION = ACCOUNT[ACCOUNT.index("function CollectionHistorySection"):ACCOUNT.index("function ConnectedAppsSection")]


def test_the_account_panel_has_a_collection_history_with_undo_on_the_entry_that_can_be_undone():
    assert "<CollectionHistorySection onCollectionChanged={onCollectionChanged} />" in ACCOUNT
    assert '<Section title="Collection history">' in SECTION
    assert "{i.undoable && !asking && <button className=\"btn xs\" disabled={busy} onClick={ask}>Undo</button>}" in SECTION
    assert "api.recentImports()" in SECTION


def test_undo_shows_what_it_will_change_and_applies_only_the_confirmed_preview():
    assert "api.undoPreview()" in SECTION and "api.undoApply(asking.preview.confirmation)" in SECTION
    assert "Undoing this puts back:" in SECTION and "Undo this change" in SECTION and "Keep it" in SECTION
    assert SECTION.index("api.undoPreview()") < SECTION.index("api.undoApply(")
    assert "asking.preview.lines.map" in SECTION  # the card, copies before and after, as the server previewed them


def test_an_entry_that_cannot_be_undone_says_why_and_the_collection_reloads_after_an_undo():
    assert "Can't be undone any more: your collection changed after it." in SECTION and "Undone." in SECTION
    assert "onCollectionChanged && onCollectionChanged()" in SECTION
    assert "onCollectionChanged={() => { setData(null); loadCollection({ afterImport: true }); }}" in APP
    assert re.search(r"undoable: bool", (Path(__file__).parent.parent / "vault" / "api" / "schemas.py").read_text(encoding="utf-8"))
