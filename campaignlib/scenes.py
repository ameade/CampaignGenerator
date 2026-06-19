"""Scene-anchored VTT extraction tied to gm-assist recap structure."""

import re
import sys
from pathlib import Path

from .api.client import stream_api


_SCENE_HEADING_RE = re.compile(r"^### +(.+?)\s*$")
_TOP_HEADING_RE = re.compile(r"^## +(.+?)\s*$")


def parse_gmassist_scenes(text: str) -> list[dict]:
    """Parse the `## Scenes` block of a gm-assist recap into ordered scene dicts.

    Returns a list of `{"name": str, "body": str}` — one per `### Scene Name`
    heading found under the first `## Scenes` heading. `body` is the verbatim
    text between this scene's heading and the next `###` (or the end of the
    Scenes section), preserving the optional `#### subtitle` line and bullets.

    Returns `[]` when no `## Scenes` section exists or it has no scene headings.
    Empty list is the signal to callers that no human-verified structure is
    available — they should bail out, not silently fall back to chunk mode.
    """
    lines = text.splitlines()
    in_scenes = False
    scenes: list[dict] = []
    current: dict | None = None
    body: list[str] = []

    def flush():
        if current is not None:
            current["body"] = "\n".join(body).strip()
            scenes.append(current)

    for line in lines:
        stripped = line.strip()
        if not in_scenes:
            if stripped.lower() == "## scenes":
                in_scenes = True
            continue
        # Inside ## Scenes — leaving on next ## heading
        if _TOP_HEADING_RE.match(line):
            flush()
            current = None
            body = []
            in_scenes = False
            continue
        m = _SCENE_HEADING_RE.match(line)
        if m:
            flush()
            current = {"name": m.group(1).strip(), "body": ""}
            body = []
            continue
        if current is not None:
            body.append(line)

    flush()
    return scenes


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def snapshot_scene_for_rerun(out_file: "Path", new_text: str) -> bool:
    """Decide whether a re-extraction's `new_text` should overwrite `out_file`.

    Returns True if the caller should write `new_text` (content differs or no
    file existed), False if the existing file is byte-identical (no write
    needed). When content differs, the existing file is snapshotted to
    `<out_file>.prev` and any `<out_file>.reviewed` marker is removed —
    a re-run that changed content invalidates the GM's prior approval.
    """
    if not out_file.exists():
        return True
    old_text = out_file.read_text(encoding="utf-8")
    if old_text == new_text:
        return False
    prev = out_file.with_name(out_file.name + ".prev")
    prev.write_text(old_text, encoding="utf-8")
    reviewed = out_file.with_name(out_file.name + ".reviewed")
    if reviewed.exists():
        reviewed.unlink()
    return True


def run_scene_extraction(
    client,
    *,
    vtt_text: str,
    scenes: list[dict],
    extract_dir: "Path",
    model: str,
    extraction_instruction: str,
    system_prefix: str = "",
    system_suffix: str = "",
    input_normalizer=None,
    cache_vtt: bool = True,
    filename_template: str = "{i:02d}_{slug}.md",
    max_tokens: int = 8192,
    force: bool = False,
) -> list[Path]:
    """For each scene in `scenes`, run a scene-anchored extraction over `vtt_text`.

    Each call sends the full VTT in the system prompt (cached as a prefix when
    `cache_vtt=True`) and a per-scene user prompt: the scene name + body from
    gm-assist plus `extraction_instruction`. Output is one markdown file per
    scene under `extract_dir`, named `NN_<slug>.md` by default.

    Existing files are skipped so a partial run can be resumed. Pass
    `force=True` to re-extract every scene; in that mode the prior file is
    snapshotted to `<file>.prev` (only if content differs) and any
    `<file>.reviewed` marker is cleared.

    extraction_instruction — the per-call task description. Receives `{name}`
                              and `{body}` substitutions and is rendered as the
                              user message. The caller controls the prompt — the
                              engine just orchestrates the loop and caching.
    system_prefix          — prepended to the system prompt (general-purpose
                              instructions that should be cached alongside the
                              VTT).
    system_suffix          — appended to the system prompt (e.g. NPC roster
                              from `format_npc_roster`).
    input_normalizer       — optional `Callable[[str], str]` applied to
                              `vtt_text` (alias normalization).
    """
    if not scenes:
        print("Error: no scenes provided — cannot run scene-anchored extraction.",
              file=sys.stderr)
        raise SystemExit(1)

    if input_normalizer:
        vtt_text = input_normalizer(vtt_text)

    extract_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    total = len(scenes)

    parts = []
    if system_prefix:
        parts.append(system_prefix.strip())
    parts.append("# TRANSCRIPT (full session VTT)\n\n" + vtt_text.strip())
    if system_suffix:
        parts.append(system_suffix.strip())
    system_prompt = "\n\n".join(parts)

    for i, scene in enumerate(scenes, 1):
        name = scene["name"]
        body = scene.get("body", "").strip()
        slug = _slugify(name) or f"scene_{i}"
        out_file = extract_dir / filename_template.format(i=i, slug=slug)
        if out_file.exists() and not force:
            print(f"  [{i}/{total}] Skipping (already exists): {out_file.name}")
            saved.append(out_file)
            continue

        user_prompt = extraction_instruction.format(name=name, body=body)
        action = "Re-extracting" if force and out_file.exists() else "Scene-extracting"
        print(f"  [{i}/{total}] {action}: {name}")
        print("  " + "─" * 56)
        result = stream_api(client, system_prompt, user_prompt, model,
                            max_tokens=max_tokens, cache_system=cache_vtt)
        print("  " + "─" * 56)

        new_text = format_scene_output(name, body, result)
        if snapshot_scene_for_rerun(out_file, new_text):
            out_file.write_text(new_text, encoding="utf-8")
            print(f"  Saved: {out_file.name}\n")
        else:
            print(f"  Unchanged (no overwrite): {out_file.name}\n")
        saved.append(out_file)

    return saved


def format_scene_output(name: str, body: str, result: str) -> str:
    """Render a scene extraction file body — shared by live and batch paths.

    Layout: front-matter + scene heading + scene summary (verbatim from
    gm-assist) + LLM-extracted verbatim moments. The live and batch paths
    must produce byte-identical files for the same `result` so that a
    user re-running with `--batch` sees no spurious diffs.
    """
    return (
        f"---\n"
        f"scene: {name}\n"
        f"source: gmassist\n"
        f"---\n\n"
        f"# {name}\n\n"
        f"## Scene summary (from gm-assist, verbatim)\n\n"
        f"{body.strip()}\n\n"
        f"## Verbatim moments\n\n"
        f"{result.strip()}\n"
    )


def build_scene_extraction_system_prompt(
    *,
    vtt_text: str,
    system_prefix: str = "",
    system_suffix: str = "",
    input_normalizer=None,
) -> str:
    """Build the system prompt that scene-extraction reuses across all scenes.

    Same shape as the inline assembly in `run_scene_extraction` so live
    and batch callers share one cache breakpoint.
    """
    if input_normalizer:
        vtt_text = input_normalizer(vtt_text)
    parts: list[str] = []
    if system_prefix:
        parts.append(system_prefix.strip())
    parts.append("# TRANSCRIPT (full session VTT)\n\n" + vtt_text.strip())
    if system_suffix:
        parts.append(system_suffix.strip())
    return "\n\n".join(parts)


def plan_scene_extraction(
    *,
    scenes: list[dict],
    extract_dir: "Path",
    filename_template: str = "{i:02d}_{slug}.md",
) -> list[dict]:
    """Map scenes to per-scene custom_ids and on-disk paths.

    Returns one dict per scene: {i, name, body, slug, custom_id, path,
    exists}. Used by both the live loop (for resumability) and the batch
    submitter (to build one Request per non-existent scene).
    """
    plan = []
    for i, scene in enumerate(scenes, 1):
        name = scene["name"]
        body = scene.get("body", "").strip()
        slug = _slugify(name) or f"scene_{i}"
        out_file = extract_dir / filename_template.format(i=i, slug=slug)
        plan.append({
            "i": i,
            "name": name,
            "body": body,
            "slug": slug,
            "custom_id": f"{i:02d}_{slug}",
            "path": out_file,
            "exists": out_file.exists(),
        })
    return plan
