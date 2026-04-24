"""Tests for ``rpg_retriever.py``.

The retriever splits into:
  * ``search_rpglib`` — direct SQLite; tested against a throwaway DB.
  * ``reconcile`` — pure function; tested with in-memory fixtures.
  * ``retrieve`` — top-level; tested end-to-end with the
    :class:`FakeMempalaceClient` stand-in.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import rpg_retriever as rpgr
from mempalace_client import FakeMempalaceClient


# ── search_rpglib ────────────────────────────────────────────────────────


@pytest.fixture
def rpglib_db(tmp_path: Path) -> Path:
    db = tmp_path / "rpg_library.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            filename TEXT,
            filepath TEXT,
            publisher TEXT,
            game_system TEXT,
            product_type TEXT,
            series TEXT,
            tags TEXT,
            page_count INTEGER,
            pdf_title TEXT,
            description TEXT,
            min_level INTEGER,
            max_level INTEGER
        )
        """
    )
    rows = [
        (1, "TofEE.pdf", "/mnt/g/TofEE.pdf", "WotC", "D&D 5e", "adventure",
         "Greyhawk", json.dumps(["classic", "dungeon"]), 160,
         "Temple of Elemental Evil", "Classic high-level dungeon crawl", 4, 8),
        (2, "Obojima.pdf", "/mnt/g/Obojima.pdf", "Atlas", "D&D 5e", "adventure",
         "Obojima", json.dumps(["asian", "island", "horror"]), 260,
         "Obojima — Tales from the Tall Grass", "Japanese-inspired island adventure", 3, 10),
        (3, "Bestiary.pdf", "/mnt/g/Bestiary.pdf", "Paizo", "Pathfinder 2e", "bestiary",
         None, json.dumps(["creatures", "reference"]), 400,
         "Pathfinder Bestiary", "Official creature compendium", None, None),
        (4, "DM5eToolkit.pdf", "/mnt/g/DM5eToolkit.pdf", "WotC", "D&D 5e", "sourcebook",
         None, json.dumps(["gm-tools"]), 220,
         "5e DM Toolkit", "Random tables and advice for DMs", None, None),
    ]
    conn.executemany(
        "INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()
    return db


def test_search_rpglib_matches_title_substring(rpglib_db):
    results = rpgr.search_rpglib("Obojima", db_path=rpglib_db)
    titles = [r["pdf_title"] for r in results]
    assert "Obojima — Tales from the Tall Grass" in titles


def test_search_rpglib_matches_tag(rpglib_db):
    results = rpgr.search_rpglib("horror", db_path=rpglib_db)
    assert any("Obojima" in r["pdf_title"] for r in results)


def test_search_rpglib_filters_by_system(rpglib_db):
    results = rpgr.search_rpglib("", db_path=rpglib_db, game_system="Pathfinder 2e")
    assert len(results) == 1
    assert results[0]["game_system"] == "Pathfinder 2e"


def test_search_rpglib_filters_by_product_type(rpglib_db):
    results = rpgr.search_rpglib("", db_path=rpglib_db, product_type="bestiary")
    assert len(results) == 1
    assert results[0]["product_type"] == "bestiary"


def test_search_rpglib_level_filter(rpglib_db):
    # Request tier-1 adventures (levels 1-4): TofEE (min 4) qualifies,
    # Obojima (min 3) qualifies, bestiary + sourcebook have no levels (skipped).
    results = rpgr.search_rpglib(
        "", db_path=rpglib_db, min_level=1, max_level=4, product_type="adventure"
    )
    titles = {r["pdf_title"] for r in results}
    assert "Temple of Elemental Evil" in titles
    assert "Obojima — Tales from the Tall Grass" in titles


def test_search_rpglib_missing_db_returns_empty(tmp_path):
    results = rpgr.search_rpglib("anything", db_path=tmp_path / "nope.db")
    assert results == []


def test_search_rpglib_tags_parsed_as_list(rpglib_db):
    results = rpgr.search_rpglib("Obojima", db_path=rpglib_db)
    assert isinstance(results[0]["tags"], list)
    assert "horror" in results[0]["tags"]


# ── reconcile ────────────────────────────────────────────────────────────


def _fake_mp_result(
    hits: list[dict] | None = None,
    *,
    fallback: bool = False,
    path: dict | None = None,
) -> dict:
    return {
        "query": "x",
        "max_depth": 2,
        "path": path or {"wings": [], "rooms": []},
        "results": hits or [],
        "total_before_filter": len(hits or []),
        "fallback": fallback,
    }


class TestReconcile:
    def test_drawer_hit_joined_with_rpglib_metadata(self):
        mp = _fake_mp_result(
            [
                {
                    "drawer_id": "d1",
                    "wing": "wing_rpglib",
                    "room": "room_obojima",
                    "text": "An encounter with a kappa in the river.",
                    "similarity": 0.82,
                    "distance": 0.18,
                    "book_id": 2,
                    "page": 45,
                    "source_file": "Obojima.json",
                }
            ]
        )
        books = [
            {"id": 2, "pdf_title": "Obojima", "publisher": "Atlas",
             "game_system": "D&D 5e", "product_type": "adventure",
             "filepath": "/mnt/g/Obojima.pdf", "tags": ["asian"]},
        ]
        got = rpgr.reconcile("kappa", rpglib_books=books, mempalace_result=mp)
        assert got.drawer_hits == 1
        assert got.statblock_hits == 0
        shaped = got.hits[0]
        assert shaped["kind"] == "drawer"
        assert shaped["book_id"] == 2
        assert shaped["title"] == "Obojima"
        assert shaped["game_system"] == "D&D 5e"

    def test_bestiary_hit_becomes_statblock(self):
        mp = _fake_mp_result(
            [
                {
                    "drawer_id": "sb1",
                    "wing": "wing_bestiary",
                    "room": "room_obojima",
                    "text": "# Kappa\ntag: creature\nsource: CUSTOM",
                    "similarity": 0.88,
                    "distance": 0.12,
                    "book_id": 2,
                }
            ]
        )
        got = rpgr.reconcile(
            "kappa statblock",
            rpglib_books=[{"id": 2, "pdf_title": "Obojima", "filepath": "/mnt/g/o.pdf", "tags": []}],
            mempalace_result=mp,
        )
        assert got.statblock_hits == 1
        assert got.drawer_hits == 0
        assert got.hits[0]["kind"] == "statblock"

    def test_unconverted_book_becomes_pointer(self):
        mp = _fake_mp_result([])
        books = [
            {
                "id": 7,
                "pdf_title": "Some Unconverted Adventure",
                "filepath": "/mnt/g/uncv.pdf",
                "filename": "uncv.pdf",
                "publisher": "Indie",
                "game_system": "D&D 5e",
                "product_type": "adventure",
                "page_count": 80,
                "tags": ["urban"],
            },
        ]
        got = rpgr.reconcile("urban adventure", rpglib_books=books, mempalace_result=mp)
        assert got.pointers_emitted == 1
        assert got.drawer_hits == 0
        hit = got.hits[0]
        assert hit["kind"] == "pointer"
        assert hit["book_id"] == 7
        assert hit["suggest_conversion"]["convert_command"][1].endswith("convert_book.py")
        assert hit["suggest_conversion"]["ingest_command"][1].endswith("fivetools_ingest.py")

    def test_drawer_suppresses_pointer_for_same_book(self):
        mp = _fake_mp_result(
            [
                {
                    "drawer_id": "d1",
                    "wing": "wing_rpglib",
                    "room": "room_obojima",
                    "text": "prose",
                    "book_id": 2,
                }
            ]
        )
        books = [
            {"id": 2, "pdf_title": "Obojima", "filepath": "/mnt/g/o.pdf",
             "filename": "o.pdf", "page_count": 100, "tags": []},
            {"id": 3, "pdf_title": "Bestiary", "filepath": "/mnt/g/b.pdf",
             "filename": "b.pdf", "page_count": 200, "tags": []},
        ]
        got = rpgr.reconcile("q", rpglib_books=books, mempalace_result=mp)
        # Book 2 shows up as drawer; book 3 (no drawer) becomes a pointer.
        kinds = [h["kind"] for h in got.hits]
        assert kinds == ["drawer", "pointer"]
        assert got.hits[0]["book_id"] == 2
        assert got.hits[1]["book_id"] == 3

    def test_max_pointers_caps_emission(self):
        mp = _fake_mp_result([])
        books = [
            {
                "id": i,
                "pdf_title": f"Book {i}",
                "filepath": f"/mnt/g/b{i}.pdf",
                "filename": f"b{i}.pdf",
                "page_count": 50,
                "tags": [],
            }
            for i in range(1, 11)
        ]
        got = rpgr.reconcile("q", rpglib_books=books, mempalace_result=mp, max_pointers=3)
        assert got.pointers_emitted == 3

    def test_include_pointers_false_suppresses_all(self):
        mp = _fake_mp_result([])
        books = [
            {"id": 1, "pdf_title": "X", "filepath": "/p.pdf", "filename": "p.pdf",
             "page_count": 10, "tags": []}
        ]
        got = rpgr.reconcile(
            "q",
            rpglib_books=books,
            mempalace_result=mp,
            include_unconverted_pointers=False,
        )
        assert got.pointers_emitted == 0
        assert got.hits == []

    def test_drawer_with_unknown_book_id_passes_through(self):
        mp = _fake_mp_result(
            [
                {
                    "drawer_id": "d1",
                    "wing": "wing_rpglib",
                    "room": "room_unknown",
                    "text": "prose",
                    "book_id": 99,  # not in rpglib_books
                }
            ]
        )
        got = rpgr.reconcile("q", rpglib_books=[], mempalace_result=mp)
        assert got.drawer_hits == 1
        assert got.hits[0].get("book_id") is None
        assert got.hits[0].get("title") is None

    def test_fallback_flag_propagates(self):
        mp = _fake_mp_result([], fallback=True)
        mp["fallback_reason"] = "indices empty"
        got = rpgr.reconcile("q", rpglib_books=[], mempalace_result=mp)
        assert got.fallback is True
        assert got.fallback_reason == "indices empty"


# ── retrieve (top-level orchestrator) ────────────────────────────────────


def test_retrieve_end_to_end_with_fake_client(rpglib_db, monkeypatch):
    fake = FakeMempalaceClient(
        responses={
            "mempalace_search_hierarchical": lambda **kw: {
                "query": kw["query"],
                "max_depth": kw.get("max_depth", 2),
                "path": {"wings": [{"wing": "wing_rpglib"}], "rooms": []},
                "results": [
                    {
                        "drawer_id": "d1",
                        "wing": "wing_rpglib",
                        "room": "room_obojima",
                        "text": "A kappa waits by the river.",
                        "similarity": 0.8,
                        "distance": 0.2,
                        "book_id": 2,
                    }
                ],
                "total_before_filter": 1,
                "fallback": False,
            },
        },
    )
    # "classic" hits the TofEE title/description; Obojima description contains
    # "Japanese-inspired island adventure" — "island" is another multi-book hit.
    # We want at least one book other than book 2 (Obojima, which already has
    # a drawer hit) to surface as a pointer. The empty query matches everything.
    out = rpgr.retrieve(
        "",  # no title filter — match all books
        rpglib_db=rpglib_db,
        mp_client=fake,
        limit=5,
        max_pointers=2,
    )
    assert out["drawer_hits"] == 1
    assert out["pointers_emitted"] >= 1
    assert out["fallback"] is False
    # Calls: exactly one search_hierarchical invocation.
    assert len(fake.calls) == 1
    assert fake.calls[0]["tool"] == "mempalace_search_hierarchical"
