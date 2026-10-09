"""Spreadsheet-safe CSV cells (#351): a cell that starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is
read by Excel, LibreOffice and Google Sheets as a formula. A collection file the person imported (their own, or one
from someone else, or one an assistant sent) can put such text in a card name, a folder or a note, and the exports the
person opens in a spreadsheet would run it (OWASP, "CSV Injection").

The rule (OWASP's): a cell that could be read as a formula starts, in the file we write, with one single quote. The
quote is the spreadsheet's own "this is text" marker, so a person opening the file sees the original text.

Round trip. The Vault reads its own Moxfield and generic exports back (``restore_csv``), so the marker has to be
removed there, and only where we put it. The two rules are one test on the cell's text:

    marked(cell) = after any run of leading single quotes, the next character is one of ``= + - @ TAB CR``

* writing: a marked cell gets one more leading quote (``=1+1`` -> ``'=1+1``; a name that really starts with a quote,
  ``'=1+1`` -> ``''=1+1``);
* reading: a marked cell loses one leading quote, and every other cell is left alone.

Every cell the Vault writes is either unmarked (written as it is, and read back as it is) or marked (one quote
added, one quote removed), so what comes out of an export goes back in unchanged. Ordinary data is untouched
(``'Tis the season`` has a letter after its quote); a folder called ``-- trade --`` starts like a formula, is written
as ``'-- trade --`` (a spreadsheet shows ``-- trade --``) and comes back as ``-- trade --``.

The one thing that does not survive is a file that did not come from the Vault: in a Moxfield or generic CSV from
elsewhere, a cell that really starts with a quote followed by one of those characters loses that quote on import.

Only the files other apps import are neutralised. The Dragon Shield export is the person's own round-trip format and
stays byte for byte what was imported (``vault.importer.export_entries``).
"""

from __future__ import annotations

import csv
import io
import re

TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
_MARKER_SOMEWHERE = re.compile(r"'[=+\-@\t\r]")


def _is_marked(text: str) -> bool:
    return text.lstrip("'")[:1] in TRIGGERS


def neutralise_cell(value) -> str:
    """The cell's text, with one single quote in front when a spreadsheet could read it as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if _is_marked(text) else text


def restore_cell(text: str) -> str:
    """The inverse of :func:`neutralise_cell`: removes the one quote it adds, and touches no other cell."""
    return text[1:] if text[:1] == "'" and _is_marked(text) else text


def _delimiter(text: str) -> tuple[str, str, str]:
    """(the ``sep=X`` line with its line break or '', the delimiter, the rest) for an Excel-style file."""
    first, _, rest = text.partition("\n")
    declared = first.strip("\r ﻿")
    if declared.lower().startswith("sep=") and len(declared) == 5:
        return first + "\n", declared[4], rest
    return "", ",", text


def _rewrite(text: str, cell) -> str:
    head, delimiter, body = _delimiter(text)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    try:
        for row in csv.reader(io.StringIO(body, newline=""), delimiter=delimiter):
            writer.writerow([cell(c) for c in row])
    except csv.Error:  # not a CSV we can walk (e.g. a field over the size limit): the caller's own parser reports it
        return text
    return head + out.getvalue()


def neutralise_csv(text: str) -> str:
    """A CSV file with every cell that starts like a formula defused. The header row and ordinary cells are unchanged."""
    return _rewrite(text, neutralise_cell)


def restore_csv(text: str) -> str:
    """A CSV file the Vault wrote with :func:`neutralise_csv`, back to the cells it was made from.

    A file with no quote in front of a formula character (every file that never went through an export) is returned
    untouched, byte for byte, so its line endings and quoting do not matter.
    """
    if not _MARKER_SOMEWHERE.search(text):
        return text
    return _rewrite(text, restore_cell)
