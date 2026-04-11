"""Session workflow API routes — VTT summary and scene extraction subprocess streaming."""

import sys
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse

from server.config import load_ui_config
from server.subprocess_runner import python_exe, stream_subprocess

router = APIRouter()

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent  # CampaignGenerator/


def _resolve_session_path(path_str: str, session_dir: str) -> str:
    """Resolve a relative path against session_dir if set."""
    if not path_str.strip():
        return path_str
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return path_str
    if session_dir.strip():
        return str(Path(session_dir).expanduser() / p)
    return path_str


def _cmd_opt(cmd: list[str], flag: str, value: str | int | None) -> None:
    if value:
        cmd += [flag, str(value)]


def _cmd_multi(cmd: list[str], flag: str, values: list[str]) -> None:
    if values:
        for v in values:
            if v.strip():
                cmd += [flag, v.strip()]


def _cmd_flag(cmd: list[str], flag: str, condition: bool) -> None:
    if condition:
        cmd.append(flag)


# ── VTT Summary ─────────────────────────────────────────────────────────────

@router.get("/run/vtt-summary")
async def run_vtt_summary(
    session_dir: str = "",
    vtt_input: str = "",
    output: str = "",
    roleplay_output: str = "",
    date: str = "",
    session_name: str = "",
    context: list[str] = Query(default=[]),
    reference_summaries: list[str] = Query(default=[]),
    extract_dir: str = "",
    chunk_size: int = 50000,
    synthesize_only: bool = False,
    no_log: bool = False,
    model: str = "claude-sonnet-4-6",
):
    cmd = [python_exe(), str(SCRIPT_DIR / "vtt_summary.py")]

    if not synthesize_only and vtt_input:
        cmd.append(vtt_input)

    _cmd_opt(cmd, "--output", _resolve_session_path(output, session_dir))
    _cmd_opt(cmd, "--date", date.strip())
    _cmd_opt(cmd, "--session-name", session_name.strip())
    _cmd_opt(cmd, "--roleplay-output", _resolve_session_path(roleplay_output, session_dir))

    for ctx in context:
        if ctx.strip():
            cmd += ["--context", ctx.strip()]

    for ref in reference_summaries:
        if ref.strip():
            cmd += ["--reference-summaries", ref.strip()]

    resolved_extract_dir = _resolve_session_path(extract_dir, session_dir)
    _cmd_opt(cmd, "--extract-dir", resolved_extract_dir)

    if chunk_size and chunk_size != 50000:
        cmd += ["--chunk-size", str(chunk_size)]

    _cmd_flag(cmd, "--synthesize-only", synthesize_only)
    _cmd_flag(cmd, "--no-log", no_log)
    cmd += ["--model", model]

    return StreamingResponse(
        stream_subprocess(cmd, cwd=str(Path.cwd())),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Scene Extraction (two-phase) ─────────────────────────────────────────────
#
# Phase 1: /run/generate-plan  — runs Passes 1-3, saves plan.md, stops at the
#           Pass 3 checkpoint.  The user reviews plan.md before continuing.
#
# Phase 2: /run/scene-extraction — runs Pass 4 using the reviewed plan.
#           Requires --plan-file so the checkpoint does not fire.
#
# Helper: /plan — returns plan.md content for display in the UI.


def _shared_session_doc_opts(
    cmd: list[str],
    summary_dir: str,
    session_summary: str,
    roleplay_summary: str,
    characters: str,
    party: str,
    voice_dir: str,
    examples_dir: str,
    campaign_state: str,
    world_state: str,
    context: list[str],
    session_name: str,
) -> None:
    """Append optional flags shared by both session_doc endpoints."""
    _cmd_opt(cmd, "--summary-extract-dir", summary_dir)
    _cmd_opt(cmd, "--session-summary", session_summary)
    _cmd_opt(cmd, "--roleplay-summary", roleplay_summary)
    _cmd_opt(cmd, "--characters", characters.strip())
    _cmd_opt(cmd, "--party", party)
    _cmd_opt(cmd, "--voice-dir", voice_dir)
    _cmd_opt(cmd, "--examples", examples_dir)
    for ctx in [campaign_state, world_state] + list(context):
        if ctx and ctx.strip():
            cmd += ["--context", ctx.strip()]
    _cmd_opt(cmd, "--session-name", session_name.strip())


@router.get("/run/generate-plan")
async def run_generate_plan(
    session: str = "",
    roleplay_dir: str = "",
    extract_dir: str = "",
    summary_dir: str = "",
    session_summary: str = "",
    roleplay_summary: str = "",
    characters: str = "",
    party: str = "",
    voice_dir: str = "",
    examples_dir: str = "",
    campaign_state: str = "",
    world_state: str = "",
    context: list[str] = Query(default=[]),
    session_name: str = "",
    model: str = "claude-sonnet-4-6",
):
    """Run Passes 1-3 (plan generation).  Stops at the Pass 3 checkpoint so
    the user can review plan.md before extraction begins."""
    cmd = [
        python_exe(), str(SCRIPT_DIR / "session_doc.py"),
        session,
        "--roleplay-extract-dir", roleplay_dir,
        "--by-scene",
        "--extract-dir", extract_dir,
        # No --plan-file and no --no-plan-review → checkpoint fires after Pass 3
        "--output", "/dev/null",
        "--model", model,
    ]
    _shared_session_doc_opts(
        cmd, summary_dir, session_summary, roleplay_summary,
        characters, party, voice_dir, examples_dir,
        campaign_state, world_state, list(context), session_name,
    )
    return StreamingResponse(
        stream_subprocess(cmd, cwd=str(Path.cwd())),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/run/scene-extraction")
async def run_scene_extraction(
    session: str = "",
    roleplay_dir: str = "",
    extract_dir: str = "",
    plan_file: str = "",
    summary_dir: str = "",
    session_summary: str = "",
    roleplay_summary: str = "",
    characters: str = "",
    party: str = "",
    voice_dir: str = "",
    examples_dir: str = "",
    campaign_state: str = "",
    world_state: str = "",
    context: list[str] = Query(default=[]),
    session_name: str = "",
    model: str = "claude-sonnet-4-6",
):
    """Run Pass 4 (per-scene extraction) from a human-reviewed plan.
    Derives plan_file from extract_dir/plan.md if not provided."""
    resolved_plan = plan_file.strip() or str(Path(extract_dir) / "plan.md")
    cmd = [
        python_exe(), str(SCRIPT_DIR / "session_doc.py"),
        session,
        "--roleplay-extract-dir", roleplay_dir,
        "--by-scene",
        "--extract-dir", extract_dir,
        "--plan-file", resolved_plan,  # human-reviewed plan → checkpoint skipped
        "--extract-only",
        "--output", "/dev/null",
        "--model", model,
    ]
    _shared_session_doc_opts(
        cmd, summary_dir, session_summary, roleplay_summary,
        characters, party, voice_dir, examples_dir,
        campaign_state, world_state, list(context), session_name,
    )
    return StreamingResponse(
        stream_subprocess(cmd, cwd=str(Path.cwd())),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/plan")
async def get_plan(extract_dir: str = ""):
    """Return the content of plan.md from the given extract_dir."""
    if not extract_dir.strip():
        return JSONResponse({"ok": False, "content": ""})
    plan_path = Path(extract_dir) / "plan.md"
    if not plan_path.exists():
        return JSONResponse({"ok": False, "content": ""})
    return JSONResponse({"ok": True, "content": plan_path.read_text(encoding="utf-8")})
