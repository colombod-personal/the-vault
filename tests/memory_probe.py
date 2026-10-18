"""Peak memory of one import at the import-size limit (vault-0n9.3). Not a test: ``tests/test_import_size_memory.py`` runs it in a
process of its own (so the peak is this import's and not the test run's) against the throwaway database in VAULT_TEST_DATABASE_URL.

    python tests/memory_probe.py preview|parts [rows_shape]

``preview`` posts the file to ``/api/v1/imports/preview`` (the path the Import CSV button, the upload page and a fetched file
share: ``importer.preview_import``); ``parts`` sends it as 16 KiB parts to ``/api/v1/uploads/from-file`` (the new route: parts stored,
checked, assembled once, previewed). The file is synthetic and Dragon Shield-shaped, with invented names, and is exactly
``IMPORT_MAX_BYTES`` long. ``rows_shape`` is ``short`` (about 100 bytes a row: the most rows a file of this size can hold, the worst
case for memory) or ``real`` (about 243 bytes a row, the owner's real export). Prints one JSON line: the process's peak resident set
size (``ru_maxrss``, MB) before and after, and what the import answered.
"""

from __future__ import annotations

import json
import os
import sys
import time
import zlib

HEADER = ('"sep=,"\nFolder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,'
          "Price Bought,Date Bought,LOW,MID,MARKET\n")
PART_CHARS = 16384
SETS = 40
NUMBERS = 125  # 5,000 printings in the catalog the rows are matched against; the rows cycle through them


def rss_mb() -> float:
    import resource  # not on Windows: the file builder below is imported by tests that run there too

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux


def synthetic_csv(size: int, shape: str) -> bytes:
    """A file of exactly ``size`` bytes: the header, then rows of invented cards, the last one's name padded with spaces to fill the size."""
    long_name = " of the Long Winding Road and the Quiet Harbour Beyond the Western Hills" if shape == "real" else ""
    long_set = " and the Sleeping Cartographers of the Hollow Coast Archive" if shape == "real" else ""
    rows, total, i = [], len(HEADER.encode()), 0
    while True:
        s, n = i % SETS, (i // SETS) % NUMBERS + 1
        row = (f"my cards,1,0,Invented Card {i:06d}{long_name},Z{s:02d},Invented Set {s:02d}{long_set},{n},Mint,Normal,English,"
               "0.10,2024-01-01,0.05,0.10,0.08\n")
        if total + len(row) > size:
            break
        rows.append(row)
        total += len(row)
        i += 1
    pad = size - total  # less than one row
    rows[-1] = rows[-1].replace(f"Invented Card {i - 1:06d}", f"Invented Card {i - 1:06d}" + " " * pad, 1)
    out = (HEADER + "".join(rows)).encode()
    assert len(out) == size, (len(out), size)
    return out


def main(mode: str, shape: str) -> None:
    from fastapi.testclient import TestClient

    from vault.app import create_app
    from vault.config import Settings, import_max_bytes
    from vault.models import Card

    settings = Settings(database_url=os.environ["VAULT_TEST_DATABASE_URL"], session_secret="probe", dev_login=True,
                        base_url="http://testserver")
    app = create_app(settings, serve_static=False)
    with TestClient(app) as client:
        client.post("/api/auth/dev-login", params={"email": "memory@example.com"})
        with app.state.db.sessions() as db:
            db.add_all(Card(scryfall_id=f"probe-{s}-{n}", oracle_id=f"00000000-0000-4000-8000-{s:06d}{n:06d}", name=f"Invented Card {s * NUMBERS + n:06d}",
                            set_code=f"z{s:02d}", set_name=f"Invented Set {s:02d}", collector_number=str(n), finishes=["nonfoil"])
                       for s in range(SETS) for n in range(1, NUMBERS + 1))
            db.commit()
        limit = import_max_bytes()
        before = rss_mb()
        content = synthetic_csv(limit, shape)
        started = time.perf_counter()
        if mode == "preview":
            res = client.post("/api/v1/imports/preview", files={"file": ("my_cards.csv", content, "text/csv")})
            answer = res.json()
        else:
            text = content.decode("utf-8")
            pieces = [text[i:i + PART_CHARS] for i in range(0, len(text), PART_CHARS)]
            facts = {"parts": len(pieces), "lines": text.count("\n"), "sum": zlib.crc32(text.encode("utf-16-le"))}
            for number, piece in enumerate(pieces, 1):
                res = client.post("/api/v1/uploads/from-file", json={"text": piece, "part": number, **facts})
                answer = res.json()
        seconds = round(time.perf_counter() - started, 1)
        app.state.db.engine.dispose()
    print(json.dumps({"mode": mode, "shape": shape, "bytes": len(content), "status": res.status_code, "answer": answer.get("status", "preview"),
                      "rows": answer.get("rows"), "matched_rows": answer.get("matched_rows"), "seconds": seconds,
                      "rss_before_mb": round(before), "rss_peak_mb": round(rss_mb()), "rss_added_mb": round(rss_mb() - before)}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "short")
