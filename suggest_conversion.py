"""
suggest_conversion.py — build a "here's what to run" payload for pointer hits.

When ``rpg_retriever.py`` finds a candidate book in rpglib that has no
drawers in MemPalace yet (a "pointer" result in the plan's three-state
taxonomy), it needs to surface a concrete suggestion to the user:

    * the PDF filepath on disk
    * the exact ``convert_book.py`` invocation to run
    * the exact ``fivetools_ingest.py`` invocation to run afterwards
    * a rough token estimate so the user knows the cost envelope

This module is import-friendly (``build_suggestion``) and also runs as
a CLI for ad-hoc inspection. Conversion is **never** auto-triggered —
the LLM surfaces this payload, the user approves, the user runs the
commands.

Usage (library):

    from suggest_conversion import build_suggestion
    payload = build_suggestion(book_row, campaign_workspace="~/camp/icespire")

Usage (CLI):

    python suggest_conversion.py --book-id 7421
    python suggest_conversion.py --filepath /mnt/g/some.pdf
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_RPGLIB_DB = Path.home() / "src" / "mytools" / "rpg-lib" / "rpg_library.db"
_DEFAULT_CONVERT_SCRIPT = "convert_book.py"
_DEFAULT_INGEST_SCRIPT = "fivetools_ingest.py"

# Average tokens per PDF page once pdf-translators renders prose through
# Claude. Measured empirically at ~500 tokens/page for adventure prose;
# scaled up for reference books that are denser. Conservative default.
_TOKENS_PER_PAGE = 650


@dataclass
class ConversionSuggestion:
    book_id: int | None
    title: str
    filepath: str
    publisher: str
    game_system: str
    product_type: str
    page_count: int
    tags: list[str]
    output_json_path: str
    convert_command: list[str]
    ingest_command: list[str]
    estimated_cost_tokens: int
    estimated_cost_usd_min: float  # at $0.80 / 1M input (Haiku) rough
    estimated_cost_usd_max: float  # at $3.00 / 1M input (Sonnet)
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lookup_book(
    db_path: Path, *, book_id: int | None = None, filepath: str | None = None
) -> dict | None:
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        if book_id is not None:
            row = conn.execute(
                "SELECT id, filename, filepath, publisher, game_system, "
                "product_type, series, tags, page_count, pdf_title "
                "FROM books WHERE id = ?",
                (book_id,),
            ).fetchone()
        elif filepath is not None:
            row = conn.execute(
                "SELECT id, filename, filepath, publisher, game_system, "
                "product_type, series, tags, page_count, pdf_title "
                "FROM books WHERE filepath = ?",
                (filepath,),
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
    elif not isinstance(data.get("tags"), list):
        data["tags"] = []
    return data


def estimate_tokens(page_count: int | None, *, per_page: int = _TOKENS_PER_PAGE) -> int:
    """Rough upper-bound on Claude tokens needed to convert ``page_count`` pages."""
    if not isinstance(page_count, int) or page_count <= 0:
        return 0
    return int(math.ceil(page_count * per_page))


def _cost_bounds(tokens: int) -> tuple[float, float]:
    if tokens <= 0:
        return (0.0, 0.0)
    # Haiku input  ~$0.80 / 1M tokens; Sonnet input ~$3.00 / 1M tokens.
    # Output costs 4-5× input; keep it rough and on the cheap side for
    # suggestion UX — the user sees the order of magnitude, not a quote.
    low = tokens * 0.8 / 1_000_000
    high = tokens * 3.0 / 1_000_000
    return (round(low, 3), round(high, 3))


def build_suggestion(
    book: dict,
    *,
    convert_script: str = _DEFAULT_CONVERT_SCRIPT,
    ingest_script: str = _DEFAULT_INGEST_SCRIPT,
    python: str = sys.executable,
) -> ConversionSuggestion:
    """Assemble the suggestion payload from an rpglib book row.

    ``book`` is the dict returned by :func:`_lookup_book` or
    :func:`fivetools_ingest.lookup_book` — the shared rpglib schema.
    """
    filepath = str(book.get("filepath") or "")
    if not filepath:
        raise ValueError("book row is missing 'filepath' — cannot suggest conversion")

    output_json = str(Path(filepath).with_suffix(".json"))
    title = (
        book.get("pdf_title")
        or book.get("filename")
        or Path(filepath).stem
        or "untitled"
    )
    page_count = int(book.get("page_count") or 0)
    tokens = estimate_tokens(page_count)
    cost_low, cost_high = _cost_bounds(tokens)

    notes: list[str] = []
    if page_count <= 0:
        notes.append("page count missing from rpglib row — cost estimate not reliable")
    if book.get("product_type") and "bestiary" in str(book["product_type"]).lower():
        notes.append(
            "product_type suggests a bestiary — statblocks will route to "
            "wing_bestiary/ at ingest time"
        )

    convert_command = [python, convert_script, filepath]
    ingest_command = [python, ingest_script, output_json]
    if book.get("id") is not None:
        ingest_command += ["--book-id", str(book["id"])]

    return ConversionSuggestion(
        book_id=(int(book["id"]) if book.get("id") is not None else None),
        title=str(title),
        filepath=filepath,
        publisher=str(book.get("publisher") or ""),
        game_system=str(book.get("game_system") or ""),
        product_type=str(book.get("product_type") or ""),
        page_count=page_count,
        tags=list(book.get("tags") or []),
        output_json_path=output_json,
        convert_command=convert_command,
        ingest_command=ingest_command,
        estimated_cost_tokens=tokens,
        estimated_cost_usd_min=cost_low,
        estimated_cost_usd_max=cost_high,
        notes=notes,
    )


def suggest_from_db(
    *,
    db_path: Path = _DEFAULT_RPGLIB_DB,
    book_id: int | None = None,
    filepath: str | None = None,
    **kwargs: Any,
) -> ConversionSuggestion | None:
    """Look up a book in rpglib and build a :class:`ConversionSuggestion`."""
    row = _lookup_book(db_path, book_id=book_id, filepath=filepath)
    if row is None:
        return None
    return build_suggestion(row, **kwargs)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument("--rpglib-db", type=Path, default=_DEFAULT_RPGLIB_DB)
    parser.add_argument("--book-id", type=int, default=None)
    parser.add_argument("--filepath", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.book_id is None and not args.filepath:
        raise SystemExit("suggest_conversion: pass --book-id or --filepath")
    suggestion = suggest_from_db(
        db_path=args.rpglib_db,
        book_id=args.book_id,
        filepath=args.filepath,
    )
    if suggestion is None:
        raise SystemExit("suggest_conversion: book not found in rpglib")
    print(json.dumps(suggestion.to_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
