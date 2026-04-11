"""Quote Ledger API routes — sync, query, assign, auto-assign, generate extraction."""

import json
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from quote_ledger import QuoteLedger

# Module-level ledger instance
_LEDGER: QuoteLedger | None = None


def _config() -> dict:
    """Return the editor CONFIG — single source of truth for paths."""
    from server.routers.scene_editor import CONFIG
    return CONFIG


def init_ledger_config(config: dict) -> None:
    """Legacy: called from main.py startup. Config now comes from scene_editor.CONFIG."""
    pass


def _get_ledger() -> QuoteLedger:
    global _LEDGER
    cfg = _config()
    db_path = Path(cfg["extract_dir"]) / "quote_ledger.db"
    # Re-create if extract_dir changed or the db file was deleted under us
    if _LEDGER is not None and (_LEDGER.db_path != db_path or not db_path.exists()):
        _LEDGER.close()
        _LEDGER = None
    if _LEDGER is None:
        _LEDGER = QuoteLedger(db_path)
    return _LEDGER


def _load_scenes() -> list[dict]:
    """Reuse scene loading from scene_editor."""
    from server.routers.scene_editor import _load_scenes
    return _load_scenes()


def _sse_event(data: str) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _sse_done(returncode: int = 0) -> str:
    return f"event: done\ndata: {json.dumps({'returncode': returncode})}\n\n"


# ── Existing endpoints ────────────────────────────────────────────────────

@router.post("/sync")
def api_ledger_sync():
    ledger = _get_ledger()
    scenes = _load_scenes()
    result = ledger.sync(
        roleplay_dir=Path(_config()["roleplay_extract_dir"]),
        extract_dir=Path(_config()["extract_dir"]),
        scenes=scenes,
    )
    return result


@router.get("/quotes")
def api_ledger_quotes():
    global _LEDGER
    if _LEDGER is None:
        return {"scenes": [], "unassigned": []}
    scenes = _load_scenes()
    return _LEDGER.get_quotes_grouped(scenes)


@router.post("/assign")
async def api_ledger_assign(request: Request):
    global _LEDGER
    if _LEDGER is None:
        return JSONResponse({"ok": False, "error": "ledger not synced"}, status_code=400)
    data = await request.json()
    quote_id = data["quote_id"]
    scene_index = data.get("scene_index")
    ok = _LEDGER.assign(quote_id, scene_index)
    return {"ok": ok}


# ── Bulk endpoints ────────────────────────────────────────────────────────

@router.post("/bulk-assign")
async def api_bulk_assign(request: Request):
    global _LEDGER
    if _LEDGER is None:
        return JSONResponse({"ok": False, "error": "ledger not synced"}, status_code=400)
    data = await request.json()
    count = _LEDGER.bulk_assign(data["quote_ids"], data["scene_index"])
    return {"ok": True, "count": count}


@router.post("/bulk-unassign")
async def api_bulk_unassign(request: Request):
    global _LEDGER
    if _LEDGER is None:
        return JSONResponse({"ok": False, "error": "ledger not synced"}, status_code=400)
    data = await request.json()
    count = _LEDGER.bulk_unassign(data["quote_ids"])
    return {"ok": True, "count": count}


@router.post("/exclusive")
async def api_exclusive(request: Request):
    global _LEDGER
    if _LEDGER is None:
        return JSONResponse({"ok": False, "error": "ledger not synced"}, status_code=400)
    data = await request.json()
    count = _LEDGER.make_exclusive(data["quote_ids"], data["scene_index"])
    return {"ok": True, "count": count}


@router.get("/scene/{n}")
def api_scene_quotes(n: int):
    global _LEDGER
    if _LEDGER is None:
        return {"quotes": []}
    return {"quotes": _LEDGER.get_scene_quotes(n)}


@router.get("/all-quotes")
def api_all_quotes():
    global _LEDGER
    if _LEDGER is None:
        return {"quotes": []}
    return {"quotes": _LEDGER.get_all_quotes()}


# ── Auto-assign (deterministic by chunk range) ───────────────────────────

async def _stream_auto_assign() -> AsyncGenerator[str, None]:
    """Assign unassigned quotes to scenes by chunk range (deterministic)."""
    ledger = _get_ledger()
    scenes = _load_scenes()

    all_quotes = ledger.get_all_quotes()
    unassigned = [q for q in all_quotes if q["scene_index"] is None]

    if not unassigned:
        yield _sse_event("No unassigned quotes to assign.\n")
        yield _sse_done(0)
        return

    if not scenes:
        yield _sse_event("No scenes found. Run extraction first.\n")
        yield _sse_done(1)
        return

    yield _sse_event(
        f"Assigning {len(unassigned)} quotes across {len(scenes)} scenes "
        f"by chunk range...\n"
    )
    result = ledger.chunk_assign(scenes)
    yield _sse_event(f"Assigned {result['assigned']} quotes.\n")
    if result["skipped"]:
        yield _sse_event(
            f"{result['skipped']} quotes skipped "
            f"(chunk not in any scene range).\n"
        )
    yield _sse_done(0)


@router.get("/auto-assign")
async def api_auto_assign():
    return StreamingResponse(
        _stream_auto_assign(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Generate extraction scaffold (deterministic) ──────────────────────────────

import re as _re


def _normalize_speaker(raw: str) -> str:
    """Strip parenthetical player names; normalize GM/DM variants.

    Examples:
      "Thorin (Joe)"   → "Thorin"
      "GM (Gabe)"      → "GM"
      "DM"             → "GM"
    """
    name = _re.sub(r'\s*\([^)]+\)', '', raw).strip()
    if name.upper() in ("GM", "DM"):
        name = "GM"
    return name


async def _stream_generate_extraction(scene_num: int) -> AsyncGenerator[str, None]:
    """Build a deterministic quote scaffold for scene N from assigned ledger quotes.

    No LLM is involved.  The human adds action beat lines and removes OOC
    table-talk before narrating — they are the filter, not the model.
    """
    ledger = _get_ledger()
    scenes = _load_scenes()

    if scene_num < 1 or scene_num > len(scenes):
        yield _sse_event(f"Invalid scene number: {scene_num}\n")
        yield _sse_done(1)
        return

    scene = scenes[scene_num - 1]
    narrator = scene["narrator"]
    scene_name = scene.get("scene", "")
    focus = scene.get("focus", "")
    quotes = ledger.get_scene_quotes(scene_num)

    yield _sse_event(
        f"Scaffolding Scene {scene_num}: {narrator} — {scene_name} "
        f"({len(quotes)} quote(s))...\n\n"
    )

    if not quotes:
        yield _sse_event("No quotes assigned to this scene — nothing to scaffold.\n")
        yield _sse_done(1)
        return

    lines = [
        f"[Scene {scene_num}] {scene_name}",
        f"Narrator: {narrator}",
        f"Focus: {focus}",
        "",
        "<!-- Add action beats as lines starting with - then place quotes under them. -->",
        "<!-- Remove any OOC lines (damage calls, mechanic announcements) before narrating. -->",
        "",
    ]
    for q in quotes:
        raw_speaker = q.get("character") or q.get("speaker") or "Unknown"
        speaker = _normalize_speaker(raw_speaker)
        text = q["quote_text"]
        context = q.get("context", "")
        if context:
            lines.append(f"<!-- {context} -->")
        lines.append(f'{speaker}: "{text}"')
        lines.append("")

    content = "\n".join(lines)

    try:
        from session_doc import extraction_filename
        fname = extraction_filename(scene_num, narrator, scene_name)
        extract_path = Path(_config()["extract_dir"]) / fname
        extract_path.write_text(content, encoding="utf-8")

        yield _sse_event(content)
        yield _sse_event(
            f"\n\n[Saved to {fname}]\n"
            "Add beat lines, remove OOC, then narrate."
        )
        yield _sse_done(0)

    except Exception as e:
        yield _sse_event(f"\nError: {e}\n")
        yield _sse_done(1)


@router.get("/generate-extraction/{n}")
async def api_generate_extraction(n: int):
    return StreamingResponse(
        _stream_generate_extraction(n),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
