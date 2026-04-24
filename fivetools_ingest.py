"""
fivetools_ingest.py — push a pdf-translators 5etools JSON into MemPalace.

This is the Phase 2 ingest CLI called out in the RLM integration plan.
The flow is intentionally explicit (never automatic):

    1. user runs convert_book.py on a PDF
    2. user reviews the JSON in adventure_editor / toc_editor
    3. user runs this script to push the approved JSON into MemPalace

Each typed entry in the JSON becomes one drawer:

    * statblocks    → wing_bestiary / room_<sanitized-book-title>
    * prose / section / inset / quote / table → wing_rpglib / room_<sanitized-book-title>

Book-level metadata (book_id, display_title, publisher, game_system,
product_type, tags, series) is snapshot-copied from ``rpg_library.db``
at ingest time so retrieval-time filtering stays a Chroma metadata
query (no cross-DB join). The source_filepath stays verbatim so callers
can jump back to the PDF at any point.

Idempotence: the (json_path, size, mtime) tuple is recorded in a
sidecar state file so re-running on an unchanged JSON is a no-op. Pass
``--force`` to override, or ``--replace`` to also wipe drawers from a
previous ingest of this book before re-adding.

Usage:
    python fivetools_ingest.py path/to/adventure.json
    python fivetools_ingest.py path/to/adventure.json --palace /path/to/palace
    python fivetools_ingest.py path/to/adventure.json --book-id 7421
    python fivetools_ingest.py path/to/adventure.json --force
    python fivetools_ingest.py path/to/adventure.json --replace
    python fivetools_ingest.py path/to/adventure.json --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from mempalace_client import MempalaceClient

logger = logging.getLogger(__name__)


# ── Defaults ─────────────────────────────────────────────────────────────

_DEFAULT_RPGLIB_DB = Path.home() / "src" / "mytools" / "rpg-lib" / "rpg_library.db"
_DEFAULT_PDF_TRANSLATORS = Path.home() / "src" / "5etools-kostadis" / "pdf-translators"
_STATE_DIRNAME = ".fivetools_ingest"
_STATE_VERSION = 1

# Entry types we ingest. Unknown types are skipped with a debug log.
_STATBLOCK_TYPES = {"statblock", "statblockInline"}
_PROSE_CONTAINER_TYPES = {
    "section",
    "entries",
    "inset",
    "insetReadaloud",
    "quote",
    "variantInner",
    "variantBlock",
}
_PROSE_LEAF_TYPES = {"p", "paragraph", "quote"}
_TABLE_TYPES = {"table", "tableGroup"}


# ── Validation hook into pdf-translators ─────────────────────────────────


def _load_adventure_model(module_root: Path):
    """Import adventure_model lazily so the script still runs for
    ``--dry-run`` or ``--help`` without pdf-translators on the machine.
    """
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))
    try:
        import adventure_model  # noqa: WPS433 — intentional late import

        return adventure_model
    except ImportError as exc:
        raise SystemExit(
            f"fivetools_ingest: cannot import adventure_model from {module_root!r}: {exc}. "
            "Pass --pdf-translators with the correct path."
        )


def validate_adventure_json(raw: dict, module_root: Path, strict: bool = False) -> list[str]:
    """Run pdf-translators' structural validator. Returns a list of issues
    (empty when the JSON is well-formed). Raises on malformed JSON only
    when ``strict=True``.
    """
    mod = _load_adventure_model(module_root)
    ctx = mod.BuildContext(mode=mod.ErrorMode.STRICT if strict else mod.ErrorMode.WARN)
    mod.parse_document(raw, ctx=ctx)
    errs = getattr(ctx.result, "errors", None) or []
    return [str(e) for e in errs]


# ── rpglib lookup (direct SQLite — read-only) ────────────────────────────


_BOOK_COLUMNS = (
    "id",
    "filename",
    "filepath",
    "publisher",
    "game_system",
    "product_type",
    "series",
    "tags",
    "page_count",
    "pdf_title",
    "pdf_author",
    "min_level",
    "max_level",
)


def lookup_book(
    db_path: Path,
    *,
    book_id: int | None = None,
    filepath: str | None = None,
) -> dict | None:
    """Return the rpglib row for ``book_id`` or a ``filepath`` match, or None.

    Direct SQLite read — rpglib has no Python API. Switching to MCP later
    is a one-function rewrite; callers only see the dict return shape.
    """
    if not db_path.is_file():
        logger.warning("rpglib db not found at %s — ingesting without book metadata", db_path)
        return None
    cols = ", ".join(_BOOK_COLUMNS)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        logger.warning("rpglib db open failed (%s) — skipping metadata lookup", exc)
        return None

    try:
        conn.row_factory = sqlite3.Row
        if book_id is not None:
            row = conn.execute(
                f"SELECT {cols} FROM books WHERE id = ?", (book_id,)
            ).fetchone()
        elif filepath is not None:
            row = conn.execute(
                f"SELECT {cols} FROM books WHERE filepath = ?", (filepath,)
            ).fetchone()
            if row is None:
                # Fuzzy fallback: match by basename when the caller didn't
                # pin the full path (common for JSONs copied out of the
                # source tree).
                row = conn.execute(
                    f"SELECT {cols} FROM books WHERE filename = ? LIMIT 1",
                    (Path(filepath).name,),
                ).fetchone()
        else:
            return None
    finally:
        conn.close()

    if row is None:
        return None
    data = dict(row)
    tags_raw = data.get("tags")
    if isinstance(tags_raw, str):
        try:
            data["tags"] = json.loads(tags_raw)
        except json.JSONDecodeError:
            data["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
    return data


# ── Entry walk ───────────────────────────────────────────────────────────


def _iter_top_level_entries(doc: dict) -> Iterator[dict]:
    """Yield every top-level 5etools entry regardless of doc shape.

    The two observed shapes:
        * Homebrew: ``{"adventure": [...index...], "adventureData": [{"data": [...]}]}``
        * Official: ``{"data": [...]}``
    Plus a couple of one-off outputs where the top level is simply a list.
    """
    if isinstance(doc, list):
        yield from (e for e in doc if isinstance(e, dict))
        return
    if not isinstance(doc, dict):
        return

    if isinstance(doc.get("adventureData"), list):
        for ad in doc["adventureData"]:
            if isinstance(ad, dict) and isinstance(ad.get("data"), list):
                yield from (e for e in ad["data"] if isinstance(e, dict))
    if isinstance(doc.get("data"), list):
        yield from (e for e in doc["data"] if isinstance(e, dict))
    if isinstance(doc.get("entries"), list) and "adventureData" not in doc and "data" not in doc:
        yield from (e for e in doc["entries"] if isinstance(e, dict))


def _walk_entries(
    node: Any,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[dict, tuple[str, ...]]]:
    """Depth-first walk yielding every dict node with its section path.

    The path is the chain of ``name`` fields from root down to the
    immediate parent — useful in metadata so hits can show "book →
    chapter → scene" without re-deriving at query time.
    """
    if isinstance(node, dict):
        yield (node, path)
        child_path = path + (node["name"],) if isinstance(node.get("name"), str) else path
        entries = node.get("entries")
        if isinstance(entries, list):
            for child in entries:
                yield from _walk_entries(child, child_path)
    elif isinstance(node, list):
        for child in node:
            yield from _walk_entries(child, path)


# ── Drawer shaping ──────────────────────────────────────────────────────


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9]+")


def sanitize_room_slug(text: str, *, fallback: str = "unknown") -> str:
    """Turn a book title into a room-safe slug (ascii-lower, ``-`` joined)."""
    if not isinstance(text, str) or not text.strip():
        return fallback
    slug = _NAME_SAFE_RE.sub("-", text.strip().lower()).strip("-")
    return slug or fallback


def build_drawer(
    entry: dict,
    section_path: tuple[str, ...],
    *,
    book: dict | None,
    book_room_slug: str,
    source_filepath: str,
) -> dict | None:
    """Convert one entry into a drawer-ready dict, or ``None`` to skip.

    Returned dict keys:
        wing, room, content, metadata (dict of scalars).
    """
    entry_type = entry.get("type") if isinstance(entry.get("type"), str) else None
    name = entry.get("name") if isinstance(entry.get("name"), str) else None
    page = entry.get("page") if isinstance(entry.get("page"), int) else None

    content = _render_entry_content(entry, entry_type, name, page)
    if not content:
        return None

    is_statblock = entry_type in _STATBLOCK_TYPES
    wing = "wing_bestiary" if is_statblock else "wing_rpglib"
    room = f"room_{book_room_slug}"
    section_str = " / ".join(section_path) if section_path else ""

    metadata: dict[str, Any] = {
        "entry_type": entry_type or "unknown",
        "section_name": name or "",
        "section_path": section_str,
        "page": int(page) if page is not None else -1,
        "source_filepath": source_filepath,
    }
    if book:
        metadata.update(
            {
                "book_id": int(book["id"]),
                "display_title": book.get("pdf_title") or book.get("filename") or "",
                "publisher": book.get("publisher") or "",
                "game_system": book.get("game_system") or "",
                "product_type": book.get("product_type") or "",
                "series": book.get("series") or "",
                "tags": ";".join(book["tags"]) if isinstance(book.get("tags"), list) else "",
            }
        )
    if is_statblock:
        metadata["statblock_name"] = name or ""
        metadata["statblock_source"] = entry.get("source") or ""
        metadata["statblock_tag"] = entry.get("tag") or ""

    return {"wing": wing, "room": room, "content": content, "metadata": metadata}


def _render_entry_content(entry: dict, entry_type: str | None, name: str | None, page) -> str:
    """Render an entry to a verbatim-ish text drawer.

    Prose containers (section, entries, inset, …) store the header +
    their own flat prose tokens so hierarchical pruning has something to
    match on. Leaf nodes (``p``, ``quote``) store their ``entry`` text.
    Statblocks store a compact header line; the full stat block lives in
    the source JSON and is fetched by callers via source_filepath + name.
    """
    if entry_type in _STATBLOCK_TYPES:
        tag = entry.get("tag", "")
        source = entry.get("source", "")
        lines = [f"# {name or 'Unnamed statblock'}"]
        if tag:
            lines.append(f"tag: {tag}")
        if source:
            lines.append(f"source: {source}")
        if page is not None:
            lines.append(f"page: {page}")
        return "\n".join(lines)

    # Leaf prose.
    leaf_text = entry.get("entry") if isinstance(entry.get("entry"), str) else None
    if leaf_text:
        header = f"# {name}\n\n" if name else ""
        return f"{header}{leaf_text.strip()}"

    # Container — emit its header + flat text of its direct children so a
    # container-level hit still has some prose to show.
    entries = entry.get("entries") if isinstance(entry.get("entries"), list) else []
    inline_tokens = []
    for child in entries:
        if isinstance(child, dict):
            t = child.get("entry")
            if isinstance(t, str) and t.strip():
                inline_tokens.append(t.strip())
        elif isinstance(child, str) and child.strip():
            inline_tokens.append(child.strip())
    if name or inline_tokens:
        header = f"# {name}\n\n" if name else ""
        return header + "\n\n".join(inline_tokens)

    # Tables — render the header labels so text search can hit a table
    # by the terms in its column headings.
    if entry_type in _TABLE_TYPES:
        labels = entry.get("colLabels") or []
        caption = entry.get("caption") or name or "Table"
        if labels:
            return f"# {caption}\n\n| " + " | ".join(str(x) for x in labels) + " |"
    return ""


# ── Idempotence state ────────────────────────────────────────────────────


def _state_path(json_path: Path) -> Path:
    state_dir = json_path.parent / _STATE_DIRNAME
    digest = hashlib.sha256(str(json_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return state_dir / f"{digest}.json"


def read_state(json_path: Path) -> dict | None:
    p = _state_path(json_path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_state(json_path: Path, payload: dict) -> None:
    p = _state_path(json_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def file_signature(json_path: Path) -> dict:
    st = json_path.stat()
    return {
        "size": int(st.st_size),
        "mtime": float(round(st.st_mtime, 6)),
        "path": str(json_path.resolve()),
    }


# ── Main ingest ──────────────────────────────────────────────────────────


def build_drawers_from_json(
    raw: dict,
    *,
    book: dict | None,
    book_room_slug: str,
    source_filepath: str,
) -> list[dict]:
    """Walk the adventure JSON and return every drawer-ready dict."""
    drawers: list[dict] = []
    for top in _iter_top_level_entries(raw):
        for node, path in _walk_entries(top):
            d = build_drawer(
                node,
                path,
                book=book,
                book_room_slug=book_room_slug,
                source_filepath=source_filepath,
            )
            if d is not None:
                drawers.append(d)
    return drawers


def ingest_file(
    json_path: Path,
    *,
    palace: str | None,
    book_id: int | None,
    rpglib_db: Path,
    pdf_translators: Path,
    mp_client: MempalaceClient | None = None,
    force: bool = False,
    replace: bool = False,
    dry_run: bool = False,
    strict: bool = False,
) -> dict:
    """Ingest one adventure JSON. Returns a report dict."""
    if not json_path.is_file():
        raise SystemExit(f"fivetools_ingest: {json_path} is not a file")

    sig = file_signature(json_path)
    prior = read_state(json_path)
    if prior and not force:
        if (
            prior.get("size") == sig["size"]
            and abs(prior.get("mtime", -1) - sig["mtime"]) < 0.001
        ):
            logger.info("fivetools_ingest: %s unchanged since last ingest (skip)", json_path)
            return {
                "status": "unchanged",
                "json_path": str(json_path),
                "prior": prior,
                "drawers_emitted": 0,
            }

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    errors = validate_adventure_json(raw, pdf_translators, strict=strict)
    if errors:
        logger.warning(
            "fivetools_ingest: %d validation issue(s) in %s (ingesting anyway)",
            len(errors),
            json_path,
        )

    # Locate the source PDF + rpglib metadata.
    source_filepath = raw.get("_meta", {}).get("sourceFilepath") if isinstance(raw, dict) else None
    if not source_filepath:
        # Best-effort fallback: the JSON usually lives alongside the PDF.
        candidate = json_path.with_suffix(".pdf")
        source_filepath = str(candidate) if candidate.exists() else str(json_path)

    book = lookup_book(rpglib_db, book_id=book_id, filepath=source_filepath)
    display_title = (
        (book or {}).get("pdf_title")
        or (book or {}).get("filename")
        or raw.get("_meta", {}).get("title")
        or json_path.stem
    )
    book_room_slug = sanitize_room_slug(display_title)

    drawers = build_drawers_from_json(
        raw,
        book=book,
        book_room_slug=book_room_slug,
        source_filepath=source_filepath,
    )

    if dry_run:
        return {
            "status": "dry-run",
            "json_path": str(json_path),
            "book_room_slug": book_room_slug,
            "book": book,
            "validation_errors": errors,
            "drawer_count": len(drawers),
            "drawers_preview": drawers[:3],
        }

    owns_client = mp_client is None
    if owns_client:
        mp_client = MempalaceClient(palace=palace)
        mp_client.start()
    try:
        if replace:
            logger.info("fivetools_ingest: --replace not yet implemented at MCP layer")
            # Tool surface doesn't expose a "delete drawers by metadata"
            # call yet. Deferred until that lands; for now --replace is a
            # no-op with a warning so callers notice.
        results = []
        for drawer in drawers:
            ack = mp_client.add_drawer(
                wing=drawer["wing"],
                room=drawer["room"],
                content=drawer["content"],
                metadata=drawer["metadata"],
            )
            results.append(ack)
    finally:
        if owns_client:
            mp_client.close()

    state_payload = {
        "version": _STATE_VERSION,
        "size": sig["size"],
        "mtime": sig["mtime"],
        "json_path": sig["path"],
        "source_filepath": source_filepath,
        "book_room_slug": book_room_slug,
        "book_id": (book or {}).get("id"),
        "drawer_count": len(drawers),
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_state(json_path, state_payload)

    return {
        "status": "ingested",
        "json_path": str(json_path),
        "book_room_slug": book_room_slug,
        "book_id": (book or {}).get("id"),
        "drawer_count": len(drawers),
        "validation_errors": errors,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument("json_path", type=Path, help="Path to the 5etools JSON file.")
    parser.add_argument(
        "--palace",
        default=None,
        help="Palace alias or absolute path (forwarded to mempalace-mcp).",
    )
    parser.add_argument(
        "--book-id",
        type=int,
        default=None,
        help="rpg_library.db book id. If omitted, the script looks up the "
        "book by filepath (the JSON's _meta.sourceFilepath or a sibling PDF).",
    )
    parser.add_argument(
        "--rpglib-db",
        type=Path,
        default=_DEFAULT_RPGLIB_DB,
        help=f"Path to rpg_library.db (default {_DEFAULT_RPGLIB_DB}).",
    )
    parser.add_argument(
        "--pdf-translators",
        type=Path,
        default=_DEFAULT_PDF_TRANSLATORS,
        help=f"Path to the pdf-translators checkout (default {_DEFAULT_PDF_TRANSLATORS}).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if the JSON is unchanged since last run.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="(Reserved) Wipe this book's drawers before re-ingesting.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Abort on the first structural validation error.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ingest plan (counts + preview) without writing drawers.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    report = ingest_file(
        args.json_path,
        palace=args.palace,
        book_id=args.book_id,
        rpglib_db=args.rpglib_db,
        pdf_translators=args.pdf_translators,
        force=args.force,
        replace=args.replace,
        dry_run=args.dry_run,
        strict=args.strict,
    )
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
