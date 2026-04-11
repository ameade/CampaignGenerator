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


# ── Generate extraction (Claude SSE) ─────────────────────────────────────

GENERATE_EXTRACTION_SYSTEM = """\
You are assembling a scene extraction file for a D&D session narrative.

You are given:
- A scene's narrator, name, and focus
- Verbatim VTT quotes assigned to this scene (the ONLY permitted source of dialogue)
- (Optional) GM recap bullet points for this scene (the ONLY permitted source of action beats)

Produce the extraction as grouped moments in chronological order.

Each moment is:
- An action beat on its own line starting with "-" (copied verbatim from the recap)
- Followed immediately (no indentation) by any quotes that occurred during that beat:
  Speaker: "exact quote text"
- Followed by a blank line before the next moment

Place each assigned quote under the beat it most naturally belongs to — the beat that
was happening when the character said it. Keep quotes in their original order relative
to each other. Beats with no associated quotes appear alone. If quotes clearly precede
all action beats, list them first with no preceding beat line.

IMPORTANT: Use ONLY the assigned quotes for dialogue. Do not invent dialogue or use
any dialogue from the recap text. Do not reorder quotes relative to each other.
Do not add labels, headers, emotional closing lines, or any prose of your own.

SPEAKER LABEL NORMALISATION — apply to every dialogue line:
- GM or DM with any player name in parentheses (e.g. "GM (Gabe)", "DM (Gabe)") → write as "GM"
- Character names with a player name in parentheses (e.g. "Thorin (Joe)") → strip parenthetical, write only the character name
- Unnamed NPCs → keep as-is

OOC TABLE-TALK — omit these quotes entirely:
- Damage/roll announcements: "16 more damage", "nat 20", "crit for 10 plus 6"
- Player mechanic explanations: "I action surge", "I cast X at Y level"
- GM mechanical rulings (not NPC speech): "You take 18", "roll perception"
- Out-of-character reactions and cross-talk

The test: does the line contain mechanical game language (damage numbers, conditions, spell
names as mechanics)? If yes — cut it, regardless of speaker label.
"""

GENERATE_EXTRACTION_PROSE_ADDENDUM = """\

PROSE MODE: For action beat lines only, translate mechanical language into plain narrative
description — no damage numbers, HP values, spell slot counts, or die results.
- Damage amounts → the weight of the hit (glancing / real impact / serious / brutal)
- Spell slots → the effort or resource the character draws on
- Saving throws → whether the character held, struggled, or was overcome
Dialogue lines are always kept verbatim. Do not alter quoted text.
"""


async def _stream_generate_extraction(scene_num: int) -> AsyncGenerator[str, None]:
    """Generate a full extraction file from assigned quotes + recap."""
    import asyncio

    ledger = _get_ledger()
    scenes = _load_scenes()

    if scene_num < 1 or scene_num > len(scenes):
        yield _sse_event(f"Invalid scene number: {scene_num}\n")
        yield _sse_done(1)
        return

    scene = scenes[scene_num - 1]
    quotes = ledger.get_scene_quotes(scene_num)

    yield _sse_event(f"Generating extraction for Scene {scene_num}: "
                     f"{scene['narrator']} — {scene.get('scene', '')} "
                     f"({len(quotes)} quote(s))...\n")

    # Build quote text
    quote_block = []
    for q in quotes:
        quote_block.append(f"{q['character']}: \"{q['quote_text']}\"")
        if q.get("context"):
            quote_block.append(f"  [{q['context']}]")

    # Load recap text for this scene
    recap_text = ""
    session_path = _config().get("session")
    if session_path and Path(session_path).exists():
        full_recap = Path(session_path).read_text(encoding="utf-8")
        from session_doc import extract_scene_text
        scene_name = scene.get("scene", "")
        if scene_name:
            recap_text = extract_scene_text(full_recap, scene_name)

    if not recap_text and not quote_block:
        yield _sse_event("No quotes assigned and no recap found for this scene — nothing to generate.\n")
        yield _sse_done(1)
        return

    user_prompt = (
        f"## Scene {scene_num}\n"
        f"Narrator: {scene['narrator']}\n"
        f"Scene: {scene.get('scene', 'N/A')}\n"
        f"Focus: {scene.get('focus', 'N/A')}\n"
    )
    if quote_block:
        user_prompt += f"\n## Assigned Quotes\n" + "\n".join(quote_block)
    if recap_text:
        user_prompt += f"\n\n## GM Recap Context\n{recap_text}"

    yield _sse_event("Calling Claude...\n\n")

    try:
        from campaignlib import make_client
        client = make_client()
        model = _config().get("model", "claude-sonnet-4-6")

        # Stream the response
        extract_system = GENERATE_EXTRACTION_SYSTEM
        if _config().get("prose_mode"):
            extract_system += GENERATE_EXTRACTION_PROSE_ADDENDUM

        chunks: list[str] = []
        response_gen = await asyncio.to_thread(
            lambda: client.messages.stream(
                model=model,
                max_tokens=8192,
                system=extract_system,
                messages=[{"role": "user", "content": user_prompt}],
            )
        )

        with response_gen as stream:
            for text in stream.text_stream:
                chunks.append(text)
                yield _sse_event(text)

        full_response = "".join(chunks)

        # Save extraction file
        from session_doc import extraction_filename
        fname = extraction_filename(scene_num, scene["narrator"], scene.get("scene", ""))
        extract_path = Path(_config()["extract_dir"]) / fname
        extract_path.write_text(full_response, encoding="utf-8")

        yield _sse_event(f"\n\nSaved to {fname}\n")
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
