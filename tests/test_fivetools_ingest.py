"""Tests for ``fivetools_ingest.py``.

The ingest pipeline is structured as pure functions + a thin I/O
driver. We test the pure layer exhaustively (entry walk, drawer shape,
statblock routing, idempotence signature) and the I/O driver with a
``FakeMempalaceClient`` so no subprocess is needed.
"""

import json
from pathlib import Path

import pytest

import fivetools_ingest as fti
from mempalace_client import FakeMempalaceClient


# ── Pure helpers ─────────────────────────────────────────────────────────


class TestSanitizeRoomSlug:
    def test_basic_title(self):
        assert fti.sanitize_room_slug("Temple of Elemental Evil") == "temple-of-elemental-evil"

    def test_strips_non_ascii(self):
        assert fti.sanitize_room_slug("Obojima — Tales from the Tall Grass") == (
            "obojima-tales-from-the-tall-grass"
        )

    def test_all_punctuation_yields_fallback(self):
        assert fti.sanitize_room_slug("!!!") == "unknown"
        assert fti.sanitize_room_slug("") == "unknown"
        assert fti.sanitize_room_slug(None) == "unknown"  # type: ignore[arg-type]

    def test_collapses_whitespace_and_symbols(self):
        assert fti.sanitize_room_slug("  A: Lost/Mines of 'Phandelver'  ") == (
            "a-lost-mines-of-phandelver"
        )


class TestWalkEntries:
    def test_depth_first_ordering(self):
        doc = {
            "data": [
                {
                    "type": "section",
                    "name": "Chapter 1",
                    "entries": [
                        {"type": "p", "entry": "Intro prose."},
                        {
                            "type": "entries",
                            "name": "Scene A",
                            "entries": [{"type": "p", "entry": "Scene text."}],
                        },
                    ],
                }
            ]
        }
        names = []
        for top in fti._iter_top_level_entries(doc):
            for node, path in fti._walk_entries(top):
                names.append((node.get("type"), path))
        assert names == [
            ("section", ()),
            ("p", ("Chapter 1",)),
            ("entries", ("Chapter 1",)),
            ("p", ("Chapter 1", "Scene A")),
        ]

    def test_homebrew_shape_routes_through_adventureData(self):
        doc = {
            "adventure": [{"id": "idx"}],
            "adventureData": [
                {"data": [{"type": "section", "name": "Only chapter", "entries": []}]}
            ],
        }
        tops = list(fti._iter_top_level_entries(doc))
        assert len(tops) == 1
        assert tops[0]["name"] == "Only chapter"

    def test_bare_list_document(self):
        doc = [{"type": "p", "entry": "stray prose"}]
        tops = list(fti._iter_top_level_entries(doc))
        assert tops == [{"type": "p", "entry": "stray prose"}]


class TestBuildDrawer:
    def test_prose_entry_goes_to_wing_rpglib(self):
        entry = {"type": "p", "entry": "Some session prose."}
        drawer = fti.build_drawer(
            entry,
            ("Book Title", "Chapter 1"),
            book={"id": 42, "filename": "book.pdf", "tags": ["adventure"]},
            book_room_slug="book-title",
            source_filepath="/mnt/g/book.pdf",
        )
        assert drawer is not None
        assert drawer["wing"] == "wing_rpglib"
        assert drawer["room"] == "room_book-title"
        assert "Some session prose." in drawer["content"]
        assert drawer["metadata"]["entry_type"] == "p"
        assert drawer["metadata"]["section_path"] == "Book Title / Chapter 1"
        assert drawer["metadata"]["book_id"] == 42
        assert drawer["metadata"]["tags"] == "adventure"

    def test_statblock_goes_to_wing_bestiary(self):
        entry = {
            "type": "statblock",
            "tag": "creature",
            "source": "CUSTOM",
            "name": "Bugbear Chieftain",
        }
        drawer = fti.build_drawer(
            entry,
            ("Obojima", "Ch.3"),
            book={"id": 1, "filename": "obojima.pdf", "tags": []},
            book_room_slug="obojima",
            source_filepath="/mnt/g/obojima.pdf",
        )
        assert drawer["wing"] == "wing_bestiary"
        assert drawer["room"] == "room_obojima"
        assert "Bugbear Chieftain" in drawer["content"]
        assert drawer["metadata"]["statblock_name"] == "Bugbear Chieftain"
        assert drawer["metadata"]["statblock_source"] == "CUSTOM"

    def test_empty_container_skipped(self):
        drawer = fti.build_drawer(
            {"type": "entries", "entries": []},
            ("Book",),
            book=None,
            book_room_slug="book",
            source_filepath="x.pdf",
        )
        assert drawer is None

    def test_container_with_prose_children_included(self):
        entry = {
            "type": "inset",
            "name": "Read-aloud",
            "entries": [
                {"type": "p", "entry": "You enter the hall."},
                "A stray string child.",
            ],
        }
        drawer = fti.build_drawer(
            entry,
            ("Book",),
            book=None,
            book_room_slug="book",
            source_filepath="x.pdf",
        )
        assert drawer is not None
        assert drawer["wing"] == "wing_rpglib"
        assert "You enter the hall." in drawer["content"]
        assert "A stray string child." in drawer["content"]

    def test_table_renders_column_labels(self):
        entry = {
            "type": "table",
            "caption": "Encounter Difficulty",
            "colLabels": ["Roll", "Result"],
            "rows": [[1, "Bandits"], [2, "Owlbear"]],
        }
        drawer = fti.build_drawer(
            entry,
            (),
            book=None,
            book_room_slug="book",
            source_filepath="x.pdf",
        )
        assert drawer is not None
        assert "Encounter Difficulty" in drawer["content"]
        assert "Roll" in drawer["content"]


class TestIdempotenceState:
    def test_state_roundtrip(self, tmp_path: Path):
        j = tmp_path / "adv.json"
        j.write_text("{}", encoding="utf-8")
        sig = fti.file_signature(j)
        fti.write_state(j, {"version": 1, "size": sig["size"], "mtime": sig["mtime"]})
        got = fti.read_state(j)
        assert got and got["size"] == sig["size"]

    def test_missing_state_returns_none(self, tmp_path: Path):
        j = tmp_path / "adv.json"
        j.write_text("{}", encoding="utf-8")
        assert fti.read_state(j) is None


# ── End-to-end ingest with fake MCP client ───────────────────────────────


@pytest.fixture
def homebrew_doc(tmp_path: Path) -> Path:
    doc = {
        "_meta": {"title": "Test Adventure"},
        "adventure": [{"id": "idx"}],
        "adventureData": [
            {
                "data": [
                    {
                        "type": "section",
                        "name": "Chapter 1",
                        "entries": [
                            {"type": "p", "entry": "Introductory prose."},
                            {
                                "type": "entries",
                                "name": "Scene: The Gate",
                                "entries": [
                                    {
                                        "type": "p",
                                        "entry": "Two goblins guard the gate.",
                                    },
                                    {
                                        "type": "statblock",
                                        "tag": "creature",
                                        "source": "CUSTOM",
                                        "name": "Goblin Scout",
                                    },
                                ],
                            },
                        ],
                    }
                ]
            }
        ],
    }
    p = tmp_path / "test-adventure.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_ingest_file_emits_expected_drawers(homebrew_doc, tmp_path, monkeypatch):
    fake = FakeMempalaceClient()
    # Route validation through a no-op so the test doesn't require
    # pdf-translators installed on the machine running pytest.
    monkeypatch.setattr(fti, "validate_adventure_json", lambda raw, root, strict=False: [])
    monkeypatch.setattr(fti, "lookup_book", lambda db, book_id=None, filepath=None: None)

    report = fti.ingest_file(
        homebrew_doc,
        palace=None,
        book_id=None,
        rpglib_db=tmp_path / "fake.db",
        pdf_translators=tmp_path / "fake_pdf_translators",
        mp_client=fake,
    )

    assert report["status"] == "ingested"
    wings = {c["arguments"]["wing"] for c in fake.calls}
    rooms = {c["arguments"]["room"] for c in fake.calls}
    assert wings == {"wing_rpglib", "wing_bestiary"}
    # Book title comes from _meta.title; slug the expected result.
    assert rooms == {"room_test-adventure"}
    # Every drawer includes the metadata block as kwargs to add_drawer.
    for c in fake.calls:
        assert "metadata" in c["arguments"]
        assert c["arguments"]["metadata"]["source_filepath"].endswith("test-adventure.pdf") or \
               c["arguments"]["metadata"]["source_filepath"].endswith(str(homebrew_doc))

    # Statblock drawer sits in the bestiary wing and carries statblock_name.
    bestiary_calls = [c for c in fake.calls if c["arguments"]["wing"] == "wing_bestiary"]
    assert len(bestiary_calls) == 1
    assert bestiary_calls[0]["arguments"]["metadata"]["statblock_name"] == "Goblin Scout"


def test_ingest_file_is_idempotent(homebrew_doc, tmp_path, monkeypatch):
    fake = FakeMempalaceClient()
    monkeypatch.setattr(fti, "validate_adventure_json", lambda raw, root, strict=False: [])
    monkeypatch.setattr(fti, "lookup_book", lambda db, book_id=None, filepath=None: None)

    r1 = fti.ingest_file(
        homebrew_doc,
        palace=None,
        book_id=None,
        rpglib_db=tmp_path / "fake.db",
        pdf_translators=tmp_path / "fake_pdf_translators",
        mp_client=fake,
    )
    first_call_count = len(fake.calls)
    assert r1["status"] == "ingested"

    r2 = fti.ingest_file(
        homebrew_doc,
        palace=None,
        book_id=None,
        rpglib_db=tmp_path / "fake.db",
        pdf_translators=tmp_path / "fake_pdf_translators",
        mp_client=fake,
    )
    assert r2["status"] == "unchanged"
    # No new drawers pushed on the second run.
    assert len(fake.calls) == first_call_count


def test_dry_run_does_not_call_client(homebrew_doc, tmp_path, monkeypatch):
    fake = FakeMempalaceClient()
    monkeypatch.setattr(fti, "validate_adventure_json", lambda raw, root, strict=False: [])
    monkeypatch.setattr(fti, "lookup_book", lambda db, book_id=None, filepath=None: None)

    report = fti.ingest_file(
        homebrew_doc,
        palace=None,
        book_id=None,
        rpglib_db=tmp_path / "fake.db",
        pdf_translators=tmp_path / "fake_pdf_translators",
        mp_client=fake,
        dry_run=True,
    )
    assert report["status"] == "dry-run"
    assert report["drawer_count"] > 0
    assert fake.calls == []


def test_ingest_uses_rpglib_metadata(homebrew_doc, tmp_path, monkeypatch):
    fake = FakeMempalaceClient()
    stub_book = {
        "id": 999,
        "filename": "obojima.pdf",
        "publisher": "Paizo",
        "game_system": "D&D 5e",
        "product_type": "adventure",
        "series": "Obojima",
        "tags": ["asian-fantasy", "island"],
        "pdf_title": "Obojima — Tales from the Tall Grass",
    }
    monkeypatch.setattr(fti, "validate_adventure_json", lambda raw, root, strict=False: [])
    monkeypatch.setattr(
        fti, "lookup_book", lambda db, book_id=None, filepath=None: stub_book
    )

    report = fti.ingest_file(
        homebrew_doc,
        palace=None,
        book_id=999,
        rpglib_db=tmp_path / "fake.db",
        pdf_translators=tmp_path / "fake_pdf_translators",
        mp_client=fake,
    )
    assert report["book_id"] == 999
    assert report["book_room_slug"] == "obojima-tales-from-the-tall-grass"
    # Every drawer metadata carries the rpglib lookup snapshot.
    for c in fake.calls:
        md = c["arguments"]["metadata"]
        assert md["book_id"] == 999
        assert md["game_system"] == "D&D 5e"
        assert md["tags"] == "asian-fantasy;island"
