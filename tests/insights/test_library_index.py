"""Tests for insights/library_index.py — local library → library_tracks."""

from insights import db, library_index


def test_normalize():
    assert library_index.normalize("  Aphex Twin ") == "aphex twin"
    assert library_index.normalize(None) == ""


def test_index_library_populates_normalized(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    fake = [
        {"artist": "Aphex Twin", "title": "Xtal"},
        {"artist": "  BURIAL ", "title": "Archangel"},
        {"artist": "", "title": "Untagged"},
        {"artist": "Aphex Twin", "title": "Xtal"},
    ]
    n = library_index.index_library(conn, "/music", scan=lambda d: fake)
    rows = conn.execute("SELECT artist, track FROM library_tracks ORDER BY artist").fetchall()
    keys = [(r["artist"], r["track"]) for r in rows]
    assert ("aphex twin", "xtal") in keys
    assert ("burial", "archangel") in keys
    assert len(rows) == 2
    assert n == 2


def test_index_library_is_idempotent_and_refreshes(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    library_index.index_library(conn, "/m", scan=lambda d: [{"artist": "A", "title": "t1"}])
    library_index.index_library(conn, "/m", scan=lambda d: [{"artist": "B", "title": "t2"}])
    rows = {(r["artist"], r["track"]) for r in
            conn.execute("SELECT artist, track FROM library_tracks").fetchall()}
    assert rows == {("b", "t2")}


def test_index_library_scan_error_returns_zero(tmp_path):
    conn = db.connect(str(tmp_path / "i.db"))
    # Pre-populate so we can confirm a failed scan does not wipe existing data.
    conn.execute("INSERT INTO library_tracks (artist, track) VALUES ('a', 't1')")
    conn.commit()

    def boom(_):
        raise RuntimeError("scan failed")

    n = library_index.index_library(conn, "/m", scan=boom)
    assert n == 0
    # Existing rows are untouched (the DELETE never runs on the exception path).
    rows = conn.execute("SELECT COUNT(*) FROM library_tracks").fetchone()[0]
    assert rows == 1
