"""
rpg_retriever.py — the Phase 2 orchestrator for RPG lookup.

Three-state retrieval per the RLM integration plan:

    * ``kind="drawer"``    — MemPalace hit, verbatim prose or table, book
                              metadata joined from rpglib.
    * ``kind="statblock"`` — MemPalace hit in ``wing_bestiary``. Same
                              shape as drawer but callers treat it as a
                              compact creature reference.
    * ``kind="pointer"``   — rpglib candidate the user hasn't converted
                              yet. Carries a ``suggest_conversion``
                              payload so the AI can surface a concrete
                              "want me to convert X?" question.

Reads are deterministic: rpglib via read-only SQLite, MemPalace via the
existing MCP tool surface (``mempalace_search_hierarchical``). No LLM
in the retrieval path — the LLM only renders results (or runs inside
pdf-translators when the user approves a conversion).

Usage (library):

    from rpg_retriever import retrieve

    result = retrieve("fey forest encounter mid-level 5e",
                      palace="/mnt/data/mempalace/palaces/chat",
                      limit=10)
    for hit in result["hits"]:
        ...

Usage (CLI):

    python rpg_retriever.py "fey forest encounter mid-level 5e"
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from mempalace_client import MempalaceClient
from suggest_conversion import build_suggestion

logger = logging.getLogger(__name__)


_DEFAULT_RPGLIB_DB = Path.home() / "src" / "mytools" / "rpg-lib" / "rpg_library.db"


# ── rpglib search (direct SQLite) ────────────────────────────────────────


_STOPWORDS = frozenset(
    {"a", "an", "and", "of", "or", "the", "to", "with", "in", "on", "for"}
)


def _rpglib_like_clause(
    query: str, *, title_only: bool = False
) -> tuple[str, list[Any]]:
    """Build an OR-of-tokens LIKE clause over title / filename / description / tags.

    The query is tokenized on whitespace; each non-stopword token becomes
    one ``LIKE '%tok%'`` match. A row qualifies if any token hits any
    column. This makes multi-word queries like "curse of strahd gothic
    horror" surface books whose title matches "strahd" even if the rest
    of the words don't appear verbatim.
    """
    tokens = [
        t.lower()
        for t in query.split()
        if t and t.lower() not in _STOPWORDS and len(t) >= 2
    ]
    if not tokens:
        # Caller passed an empty / stopword-only query → match everything.
        return ("1", [])

    if title_only:
        columns = ("LOWER(pdf_title)", "LOWER(COALESCE(filename, ''))")
    else:
        columns = (
            "LOWER(pdf_title)",
            "LOWER(COALESCE(filename, ''))",
            "LOWER(COALESCE(description, ''))",
            "LOWER(COALESCE(tags, ''))",
        )

    per_token = []
    params: list[Any] = []
    for tok in tokens:
        pattern = f"%{tok}%"
        per_token.append(
            "(" + " OR ".join(f"{col} LIKE ?" for col in columns) + ")"
        )
        params.extend([pattern] * len(columns))

    return ("(" + " OR ".join(per_token) + ")", params)


def search_rpglib(
    query: str,
    *,
    db_path: Path,
    limit: int = 20,
    game_system: str | None = None,
    product_type: str | None = None,
    min_level: int | None = None,
    max_level: int | None = None,
) -> list[dict]:
    """Return the top-``limit`` rpglib books matching ``query``.

    Ranks by a simple LIKE match over title/description/tags; filters
    optionally by ``game_system`` / ``product_type`` / level range.
    """
    if not db_path.is_file():
        logger.warning("rpglib db not found at %s — returning no candidates", db_path)
        return []

    sql_parts = [
        "SELECT id, filename, filepath, publisher, game_system, product_type, "
        "series, tags, page_count, pdf_title, description, min_level, max_level "
        "FROM books WHERE 1=1"
    ]
    params: list[Any] = []
    if query:
        clause, qparams = _rpglib_like_clause(query)
        sql_parts.append(f"AND {clause}")
        params.extend(qparams)
    if game_system:
        sql_parts.append("AND game_system = ?")
        params.append(game_system)
    if product_type:
        sql_parts.append("AND product_type = ?")
        params.append(product_type)
    if isinstance(min_level, int):
        sql_parts.append("AND (max_level IS NULL OR max_level >= ?)")
        params.append(min_level)
    if isinstance(max_level, int):
        sql_parts.append("AND (min_level IS NULL OR min_level <= ?)")
        params.append(max_level)
    sql_parts.append("ORDER BY COALESCE(page_count, 0) DESC LIMIT ?")
    params.append(int(limit))
    sql = " ".join(sql_parts)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    books: list[dict] = []
    for row in rows:
        book = dict(row)
        tags_raw = book.get("tags")
        if isinstance(tags_raw, str):
            try:
                book["tags"] = json.loads(tags_raw)
            except json.JSONDecodeError:
                book["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        elif not isinstance(book.get("tags"), list):
            book["tags"] = []
        books.append(book)
    return books


# ── Result shaping ───────────────────────────────────────────────────────


@dataclass
class RetrievalResult:
    query: str
    hits: list[dict] = field(default_factory=list)
    path: dict = field(default_factory=dict)
    total_before_filter: int = 0
    rpglib_candidates_seen: int = 0
    pointers_emitted: int = 0
    drawer_hits: int = 0
    statblock_hits: int = 0
    fallback: bool = False
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "query": self.query,
            "hits": self.hits,
            "path": self.path,
            "total_before_filter": self.total_before_filter,
            "rpglib_candidates_seen": self.rpglib_candidates_seen,
            "pointers_emitted": self.pointers_emitted,
            "drawer_hits": self.drawer_hits,
            "statblock_hits": self.statblock_hits,
            "fallback": self.fallback,
        }
        if self.fallback_reason:
            out["fallback_reason"] = self.fallback_reason
        return out


def _classify_drawer_hit(hit: dict) -> str:
    wing = hit.get("wing", "")
    if wing == "wing_bestiary":
        return "statblock"
    return "drawer"


def _shape_drawer_hit(hit: dict, *, book: dict | None) -> dict:
    """Convert a MemPalace hit into the retriever's external hit dict."""
    kind = _classify_drawer_hit(hit)
    entry_type = None
    # Some callers stash entry_type in the MemPalace drawer metadata at
    # ingest time via fivetools_ingest.build_drawer(). Expose it flatly.
    for candidate in ("entry_type", "matched_via"):
        if hit.get(candidate):
            entry_type = hit[candidate]
            break

    shaped: dict[str, Any] = {
        "kind": kind,
        "source": f"{hit.get('wing', '?')}/{hit.get('room', '?')}",
        "wing": hit.get("wing"),
        "room": hit.get("room"),
        "drawer_id": hit.get("drawer_id"),
        "drawer_text": hit.get("text"),
        "similarity": hit.get("similarity"),
        "distance": hit.get("distance"),
        "entry_type": entry_type,
        "page": hit.get("page"),
        "source_file": hit.get("source_file"),
    }
    if book:
        shaped["book_id"] = book.get("id")
        shaped["title"] = book.get("pdf_title") or book.get("filename")
        shaped["publisher"] = book.get("publisher")
        shaped["game_system"] = book.get("game_system")
        shaped["product_type"] = book.get("product_type")
        shaped["tags"] = list(book.get("tags") or [])
    return shaped


def _shape_pointer_hit(book: dict, *, convert_script: str, ingest_script: str) -> dict:
    suggestion = build_suggestion(
        book,
        convert_script=convert_script,
        ingest_script=ingest_script,
    )
    return {
        "kind": "pointer",
        "source": "rpglib only — not converted",
        "book_id": book.get("id"),
        "title": suggestion.title,
        "publisher": suggestion.publisher,
        "game_system": suggestion.game_system,
        "product_type": suggestion.product_type,
        "page_count": suggestion.page_count,
        "tags": suggestion.tags,
        "filepath": suggestion.filepath,
        "suggest_conversion": suggestion.to_dict(),
    }


def reconcile(
    query: str,
    *,
    rpglib_books: Iterable[dict],
    mempalace_result: dict,
    include_unconverted_pointers: bool = True,
    max_pointers: int = 5,
    convert_script: str = "convert_book.py",
    ingest_script: str = "fivetools_ingest.py",
) -> RetrievalResult:
    """Pure function. Merge rpglib candidates + mempalace hits into the
    three-state retrieval result.

    ``mempalace_result`` is whatever ``mempalace_search_hierarchical``
    returned (dict). Drawers it knows about take precedence; rpglib
    books that have no matching drawers become pointer hits at the tail
    (capped at ``max_pointers``).
    """
    rpglib_index = {int(b["id"]): b for b in rpglib_books if b.get("id") is not None}
    drawer_hits = mempalace_result.get("results") or []

    result = RetrievalResult(
        query=query,
        path=mempalace_result.get("path") or {},
        total_before_filter=int(mempalace_result.get("total_before_filter", 0) or 0),
        rpglib_candidates_seen=len(rpglib_index),
        fallback=bool(mempalace_result.get("fallback")),
        fallback_reason=mempalace_result.get("fallback_reason"),
    )

    covered_book_ids: set[int] = set()
    for raw in drawer_hits:
        book_id = _coerce_int(raw.get("book_id"))
        if book_id is None:
            # The drawer may carry book_id in a nested metadata dict
            # (depends on how the caller packed it). Try one level down.
            md = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else None
            if md:
                book_id = _coerce_int(md.get("book_id"))
        book = rpglib_index.get(book_id) if book_id is not None else None
        shaped = _shape_drawer_hit(raw, book=book)
        if shaped["kind"] == "statblock":
            result.statblock_hits += 1
        else:
            result.drawer_hits += 1
        result.hits.append(shaped)
        if book_id is not None:
            covered_book_ids.add(book_id)

    if include_unconverted_pointers and max_pointers > 0:
        for bid, book in rpglib_index.items():
            if bid in covered_book_ids:
                continue
            if not book.get("filepath"):
                continue
            result.hits.append(
                _shape_pointer_hit(
                    book,
                    convert_script=convert_script,
                    ingest_script=ingest_script,
                )
            )
            result.pointers_emitted += 1
            if result.pointers_emitted >= max_pointers:
                break

    return result


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    return None


# ── Top-level orchestrator ───────────────────────────────────────────────


def retrieve(
    query: str,
    *,
    palace: str | None = None,
    rpglib_db: Path | None = None,
    limit: int = 10,
    max_pointers: int = 5,
    include_unconverted_pointers: bool = True,
    game_system: str | None = None,
    product_type: str | None = None,
    min_level: int | None = None,
    max_level: int | None = None,
    mp_client: MempalaceClient | None = None,
    convert_script: str = "convert_book.py",
    ingest_script: str = "fivetools_ingest.py",
    max_depth: int = 2,
) -> dict:
    """Run one retrieval. Returns the three-state result as a dict."""
    db = rpglib_db or _DEFAULT_RPGLIB_DB
    rpglib_books = search_rpglib(
        query,
        db_path=db,
        limit=max(limit * 2, 10),
        game_system=game_system,
        product_type=product_type,
        min_level=min_level,
        max_level=max_level,
    )

    owns_client = mp_client is None
    if owns_client:
        mp_client = MempalaceClient(palace=palace)
        mp_client.start()
    try:
        mempalace_result = mp_client.search_hierarchical(
            query, limit=limit, max_depth=max_depth
        )
    finally:
        if owns_client:
            mp_client.close()

    reconciled = reconcile(
        query,
        rpglib_books=rpglib_books,
        mempalace_result=mempalace_result,
        include_unconverted_pointers=include_unconverted_pointers,
        max_pointers=max_pointers,
        convert_script=convert_script,
        ingest_script=ingest_script,
    )
    return reconciled.to_dict()


# ── CLI ──────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[1] if __doc__ else None,
    )
    parser.add_argument("query", help="Free-text retrieval query.")
    parser.add_argument("--palace", default=None)
    parser.add_argument("--rpglib-db", type=Path, default=_DEFAULT_RPGLIB_DB)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-pointers", type=int, default=5)
    parser.add_argument(
        "--no-pointers",
        dest="include_unconverted_pointers",
        action="store_false",
        default=True,
    )
    parser.add_argument("--game-system", default=None)
    parser.add_argument("--product-type", default=None)
    parser.add_argument("--min-level", type=int, default=None)
    parser.add_argument("--max-level", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=2, choices=[0, 1, 2])
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    out = retrieve(
        args.query,
        palace=args.palace,
        rpglib_db=args.rpglib_db,
        limit=args.limit,
        max_pointers=args.max_pointers,
        include_unconverted_pointers=args.include_unconverted_pointers,
        game_system=args.game_system,
        product_type=args.product_type,
        min_level=args.min_level,
        max_level=args.max_level,
        max_depth=args.max_depth,
    )
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
