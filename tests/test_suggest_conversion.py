"""Tests for suggest_conversion + convert_book helpers."""

import json
import sqlite3
from pathlib import Path

import pytest

import convert_book
import suggest_conversion as sc


# ── build_suggestion ─────────────────────────────────────────────────────


def test_build_suggestion_fills_commands():
    book = {
        "id": 101,
        "filename": "Adventure.pdf",
        "filepath": "/mnt/g/Adventure.pdf",
        "publisher": "WotC",
        "game_system": "D&D 5e",
        "product_type": "adventure",
        "series": "Series X",
        "tags": ["horror"],
        "page_count": 100,
        "pdf_title": "The Adventure",
    }
    s = sc.build_suggestion(book)
    assert s.book_id == 101
    assert s.title == "The Adventure"
    assert s.filepath == "/mnt/g/Adventure.pdf"
    assert s.output_json_path == "/mnt/g/Adventure.json"
    assert s.convert_command[-1] == "/mnt/g/Adventure.pdf"
    assert "--book-id" in s.ingest_command
    assert s.ingest_command[s.ingest_command.index("--book-id") + 1] == "101"
    assert s.page_count == 100
    assert s.estimated_cost_tokens == 100 * 650
    assert s.estimated_cost_usd_min > 0
    assert s.estimated_cost_usd_max >= s.estimated_cost_usd_min


def test_build_suggestion_flags_missing_page_count():
    book = {
        "id": 2,
        "filepath": "/x.pdf",
        "tags": [],
    }
    s = sc.build_suggestion(book)
    assert s.estimated_cost_tokens == 0
    assert any("page count" in n for n in s.notes)


def test_build_suggestion_rejects_missing_filepath():
    with pytest.raises(ValueError):
        sc.build_suggestion({"id": 1})


def test_bestiary_flag_surfaces_note():
    book = {"id": 3, "filepath": "/b.pdf", "product_type": "bestiary", "tags": []}
    s = sc.build_suggestion(book)
    assert any("bestiary" in n for n in s.notes)


def test_cost_bounds_scale_with_pages():
    low_tokens = sc.estimate_tokens(50)
    high_tokens = sc.estimate_tokens(500)
    assert high_tokens == 10 * low_tokens


# ── suggest_from_db ──────────────────────────────────────────────────────


def _seed_rpglib(tmp_path: Path) -> Path:
    db = tmp_path / "rpg.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT,
            publisher TEXT, game_system TEXT, product_type TEXT,
            series TEXT, tags TEXT, page_count INTEGER, pdf_title TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO books VALUES (1,'a.pdf','/x/a.pdf','WotC','D&D 5e','adventure','S',?,120,'Alpha')",
        (json.dumps(["dungeon"]),),
    )
    conn.commit()
    conn.close()
    return db


def test_suggest_from_db_by_id(tmp_path: Path):
    db = _seed_rpglib(tmp_path)
    s = sc.suggest_from_db(db_path=db, book_id=1)
    assert s is not None
    assert s.title == "Alpha"
    assert s.filepath == "/x/a.pdf"


def test_suggest_from_db_by_filepath(tmp_path: Path):
    db = _seed_rpglib(tmp_path)
    s = sc.suggest_from_db(db_path=db, filepath="/x/a.pdf")
    assert s is not None
    assert s.book_id == 1


def test_suggest_from_db_missing_returns_none(tmp_path: Path):
    db = _seed_rpglib(tmp_path)
    assert sc.suggest_from_db(db_path=db, book_id=9999) is None


# ── convert_book helpers ─────────────────────────────────────────────────


def test_convert_book_ingest_hint_contains_path_and_book_id():
    hint = convert_book.format_ingest_hint(
        Path("/mnt/g/adv.json"), book_id=42, python="/usr/bin/python3"
    )
    assert "fivetools_ingest.py" in hint
    assert "/mnt/g/adv.json" in hint
    assert "--book-id 42" in hint


def test_convert_book_ingest_hint_omits_book_id_when_absent():
    hint = convert_book.format_ingest_hint(Path("/tmp/x.json"), python="/usr/bin/python3")
    assert "--book-id" not in hint


def test_convert_book_default_output_path():
    assert convert_book._default_output_json(Path("/x/y/z.pdf")) == Path("/x/y/z.json")


def test_convert_book_rejects_missing_converter(tmp_path: Path):
    with pytest.raises(SystemExit):
        convert_book._resolve_converter(tmp_path, "nope.py")
