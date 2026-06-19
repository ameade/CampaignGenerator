"""Shared utilities for CampaignGenerator scripts.

All file I/O, API calls, clipboard, and logging live here so individual
scripts only contain their own logic.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .constants import DEFAULT_MODEL
from .textproc import (
    strip_base64_images, chunk_text, chunk_by_chapters,
    annotate_chunks_with_pov, prepare_chunks,
)
from .config import (
    find_default_config, load_config, load_file, load_file_optional,
    _clear_prompt_cache, load_agent_prompt, assemble_docs,
)
from .util import copy_to_clipboard, save_log
from .api.client import make_client, call_api, call_api_with_tools, stream_api
from .api.batch import (
    build_batch_request, submit_batch, poll_batch, collect_batch,
    write_batch_sidecar, read_batch_sidecar, utc_now_iso, format_batch_progress,
)


# ── Extract / synthesize pipeline ─────────────────────────────────────────────

def run_extract_pipeline(
    client,
    text: str,
    *,
    extract_system: str,
    model: str,
    extract_dir: Path,
    chunk_size: int = 60000,
    split_chapters: str | None = None,
    split_label: str = "chunk",
    filename_template: str = "extract_{i:03d}.md",
    input_normalizer=None,
    system_suffix: str = "",
) -> list[Path]:
    """Chunk `text`, run `extract_system` against each chunk, cache each result to `extract_dir`.

    Files are named via `filename_template` (default `extract_NNN.md`; `{i}` is
    the 1-indexed chunk number). Existing files are skipped so a partial run
    can be resumed. Returns the ordered list of output paths (including skipped
    ones).

    input_normalizer — optional `Callable[[str], str]` applied to `text`
                       before chunking. Used to rewrite alias variants to
                       canonical names (see `build_alias_normalizer`).
    system_suffix    — optional string appended to `extract_system` with a
                       blank-line separator. Used to seed the prompt with a
                       "Known NPCs" roster (see `format_npc_roster`).
    """
    if input_normalizer:
        text = input_normalizer(text)
    if system_suffix:
        extract_system = extract_system + "\n\n" + system_suffix

    chunks, label = prepare_chunks(text, chunk_size, split_chapters, split_label=split_label)
    total = len(chunks)
    extract_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for i, chunk in enumerate(chunks, 1):
        out_file = extract_dir / filename_template.format(i=i)
        if out_file.exists():
            print(f"  [{i}/{total}] Skipping (already exists): {out_file.name}")
            saved.append(out_file)
            continue

        print(f"  [{i}/{total}] Extracting {label} ({len(chunk):,} chars)...")
        print("  " + "─" * 56)
        result = stream_api(client, extract_system, chunk, model)
        print("  " + "─" * 56)

        out_file.write_text(result, encoding="utf-8")
        saved.append(out_file)
        print(f"  Saved: {out_file.name}\n")

    return saved


def run_synthesize_pipeline(
    client,
    *,
    source_groups: list[tuple],
    synthesize_system: str,
    model: str,
    source_label: str = "Source",
    group_separator: str = "\n\n===\n\n",
    file_separator: str = "\n\n---\n\n",
    input_normalizer=None,
    system_suffix: str = "",
    dump_input: str | None = None,
    dump_only: bool = False,
) -> str:
    """Concat labeled file groups into a user prompt, call `stream_api`, return the response.

    source_groups — list of tuples in one of two shapes:
                      `(heading, files)` — uses the default `source_label`
                      `(heading, files, group_label)` — override label for this group
                    An empty heading renders the group's files without a
                    `# HEADING` line (used when a single unnamed group is the
                    whole input, e.g. distill.py). Groups with no files are
                    skipped.

    Each file is rendered as:
        <!-- {label}: {filename} -->

        <stripped contents>

    Files within a group are joined by `file_separator`; groups are joined by
    `group_separator`. Exits with SystemExit(1) if all groups are empty.

    input_normalizer — optional `Callable[[str], str]` applied to each file's
                       contents before it is rendered into the prompt.
    system_suffix    — optional string appended to `synthesize_system` with a
                       blank-line separator.
    dump_input       — if set, write the assembled user prompt to this path and
                       the system prompt to <path>.system.md (for `claude -p`).
    dump_only        — with dump_input: skip the API call and return "".
                       Callers should guard: `if dump_only: return` before writing output.
    """
    parts: list[str] = []
    total_files = 0
    for group in source_groups:
        if len(group) == 3:
            heading, files, group_label = group
        else:
            heading, files = group
            group_label = source_label
        if not files:
            continue
        blocks = []
        for f in files:
            body = f.read_text(encoding="utf-8").strip()
            if input_normalizer:
                body = input_normalizer(body)
            blocks.append(f"<!-- {group_label}: {f.name} -->\n\n{body}")
        body = file_separator.join(blocks)
        parts.append(f"# {heading}\n\n{body}" if heading else body)
        total_files += len(files)

    if not parts:
        print("Error: no source material to synthesize.", file=sys.stderr)
        raise SystemExit(1)

    if system_suffix:
        synthesize_system = synthesize_system + "\n\n" + system_suffix

    user_prompt = group_separator.join(parts)

    if dump_input:
        dump_path = Path(dump_input).expanduser().resolve()
        dump_path.write_text(user_prompt, encoding="utf-8")
        system_path = dump_path.with_suffix(dump_path.suffix + ".system.md")
        system_path.write_text(synthesize_system, encoding="utf-8")
        print(f"Dumped synthesis input: {dump_path}")
        print(f"Dumped system prompt:   {system_path}")
        if dump_only:
            print("[--dump-only: stopping before the API call]")
            return ""

    print(f"  Synthesizing {total_files} source file(s) ({len(user_prompt):,} chars total)...")
    print("  " + "─" * 56)
    result = stream_api(client, synthesize_system, user_prompt, model)
    print("  " + "─" * 56)
    return result


# ── Scene-anchored extraction ─────────────────────────────────────────────────
#
# The gm-assist recap already structures the session into scenes. Feeding that
# verified structure into extraction directly — instead of re-deriving structure
# from blind 50K-char chunks — keeps the LLM in its rendering lane (find verbatim
# moments inside a scene) instead of its architect lane (decide what a scene is).
# See CLAUDE.md "LLMs render, humans decide".

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


# ── NPC alias machinery ───────────────────────────────────────────────────────
#
# Dossier frontmatter records human-curated canonical ↔ alias mappings
# (see "Dossier merge workflow" in CLAUDE.md). Every extractor can pre-
# normalize its input against this map before the LLM sees it, and seed
# its system prompt with a "Known NPCs" roster. Normalization is a pure
# regex substitution — no LLM scope decision is introduced here.
#
# Empty alias maps collapse cleanly: normalize() becomes identity,
# format_npc_roster() returns "". Safe for campaigns without a planning
# workflow.

_DOSSIER_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n\n?(.*)\Z", re.DOTALL)


def parse_dossier(path: "Path") -> tuple[str, list[str], list[int], str]:
    """Return (canonical_name, aliases, source_extracts, body_without_frontmatter).

    `source_extracts` is the list of dossier_extract_NNN numbers already
    absorbed into this dossier (used by planning.py's sidecar dedup).
    Missing or malformed → empty list.

    Dossiers without frontmatter fall back to (filename_stem, [], [], full_text).
    """
    try:
        import yaml
    except ImportError:
        print("Error: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    text = path.read_text(encoding="utf-8")
    m = _DOSSIER_FRONTMATTER_RE.match(text)
    if not m:
        return (path.stem, [], [], text)
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return (path.stem, [], [], text)
    name = meta.get("name") or path.stem
    aliases = meta.get("aliases") or []
    if not isinstance(aliases, list):
        aliases = []
    source_extracts = meta.get("source_extracts") or []
    if not isinstance(source_extracts, list):
        source_extracts = []
    source_extracts = [
        int(n) for n in source_extracts
        if isinstance(n, int) or (isinstance(n, str) and n.isdigit())
    ]
    return (str(name), [str(a) for a in aliases], source_extracts, m.group(2))


def normalize_npc_key(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for alias-key lookups.

    LLM-emitted variants like "Harbin (Townmaster)" must match flat aliases
    like "Harbin Townmaster". Without normalization the parens block lookup.
    """
    s = re.sub(r"[\(\)\[\]\'\"`\-]", "", name.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_alias_normalizer(
    canonical_to_aliases: dict[str, list[str]],
):
    """Return (normalize(text) -> text, [(canonical, aliases), ...]).

    The returned `normalize` rewrites any alias occurrence in `text` to
    its canonical name. Whole-word, case-insensitive, longest-first
    (so "Captain Tolubb" wins over "Tolubb" when both are aliases).

    An empty map yields an identity function and an empty entries list,
    so every extractor can call this unconditionally.
    """
    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in canonical_to_aliases.items():
        for alias in aliases:
            alias_to_canonical[alias.lower()] = canonical

    if not alias_to_canonical:
        return (lambda text: text, [])

    sorted_aliases = sorted(alias_to_canonical.keys(), key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(a) for a in sorted_aliases) + r")\b",
        flags=re.IGNORECASE,
    )

    def normalize(text: str) -> str:
        return pattern.sub(lambda m: alias_to_canonical[m.group(0).lower()], text)

    entries = [(c, a) for c, a in canonical_to_aliases.items() if a]
    return (normalize, entries)


def load_alias_map(dossier_dir) -> dict[str, list[str]]:
    """Scan `dossier_dir` for `*.md` dossiers; return `{canonical: [aliases]}`.

    Returns `{}` when `dossier_dir` is None, missing, or contains no
    dossiers — makes the caller a no-op for campaigns without planning.
    """
    if dossier_dir is None:
        return {}
    d = Path(dossier_dir).expanduser()
    if not d.is_dir():
        return {}
    result: dict[str, list[str]] = {}
    for f in sorted(d.glob("*.md")):
        # Skip sidecar files — they're not canonical dossiers.
        if ".new_notes." in f.name:
            continue
        name, aliases, _, _ = parse_dossier(f)
        result[name] = aliases
    return result


_PLAYER_PLACEHOLDERS = {
    "", "not specified", "(not specified)", "[not specified]",
    "n/a", "na", "none", "unknown", "tbd",
}


def _is_player_placeholder(name: str) -> bool:
    return name.strip().lower().strip("()[]").strip() in _PLAYER_PLACEHOLDERS


def extract_player_character_map(party_text: str) -> dict[str, str]:
    """Parse party.md and return {player_name: character_name}.

    Supports two heading + info-line shapes:

    Old (single bold span):
        ## Soma
        **Tortle Druid 5, Player: Wade**

    New (party.py output, multiple bold spans separated by ``|``):
        ### Soma — Druid 5
        **Class/Level:** Druid 5 | **Species:** Tortle | **Player:** Wade

    When the Player slot holds multiple names separated by ``/`` or
    ``,``, both names map to the same character. Placeholder values
    like ``(Not specified)`` / ``[not specified]`` / ``N/A`` are
    treated as missing.
    """
    result: dict[str, str] = {}
    current_name: str | None = None

    def _record_players(raw: str) -> None:
        if _is_player_placeholder(raw):
            return
        for p in re.split(r'[/,]', raw):
            p = p.strip().rstrip('*').strip()
            if p and not _is_player_placeholder(p) and current_name:
                result[p] = current_name

    for line in party_text.splitlines():
        stripped = line.strip()
        m = re.match(r'^#{2,3}\s+(.+)$', stripped)
        if m:
            heading = m.group(1).strip()
            current_name = re.split(r'\s+[—–-]\s+', heading, maxsplit=1)[0].strip()
            continue
        if not current_name:
            continue
        new_pm = re.search(r'\*\*Player:\*\*\s*([^|]+?)(?:\s*\||\s*$)', stripped)
        if new_pm:
            _record_players(new_pm.group(1))
            current_name = None
            continue
        cm = re.match(r'^\*\*(.+\d+.+)\*\*$', stripped)
        if cm:
            pm = re.search(r',\s*Player:\s*(.+)', cm.group(1))
            if pm:
                _record_players(pm.group(1))
            current_name = None

    # First-name aliases: if a player's recorded name is "Joe Beda" → also map
    # "Joe" → that character. Skip when the first name is ambiguous (two
    # players share it but map to different characters) so we don't pick one
    # arbitrarily. Existing full-name keys always win.
    first_name_to_chars: dict[str, set[str]] = {}
    for player, char in result.items():
        first = player.split()[0] if player.split() else ""
        if first and first != player:
            first_name_to_chars.setdefault(first, set()).add(char)
    for first, chars in first_name_to_chars.items():
        if len(chars) == 1 and first not in result:
            result[first] = next(iter(chars))

    return result


def normalize_vtt_speakers(
    vtt_text: str,
    player_map: dict[str, str] | None = None,
    gm_player: str | None = None,
) -> str:
    """Rewrite speaker labels at the start of VTT lines.

    Maps each ``Player Name:`` prefix to the corresponding character
    name from ``player_map``. ``gm_player`` (if given) is rewritten to
    ``GM`` regardless of any party.md entry. Longer names match first
    so a player named ``Mike`` and a player named ``Mike Hall`` are
    both handled correctly.

    Body text is untouched — only labels at the start of a dialogue
    line are rewritten. This is a deterministic preprocessing step the
    LLM never sees and never has to derive itself.
    """
    if not player_map and not gm_player:
        return vtt_text
    full_map = dict(player_map or {})
    if gm_player:
        full_map[gm_player] = "GM"
    sorted_keys = sorted(full_map.keys(), key=len, reverse=True)
    out_lines: list[str] = []
    for line in vtt_text.splitlines():
        for key in sorted_keys:
            prefix = f"{key}:"
            if line.startswith(prefix):
                line = f"{full_map[key]}:" + line[len(prefix):]
                break
        out_lines.append(line)
    return "\n".join(out_lines)


def format_npc_roster(alias_map: dict[str, list[str]]) -> str:
    """Render an alias map as a 'Known NPCs' block to append to an extract prompt.

    Returns '' when the map is empty, so callers can write:
        system = BASE + ("\\n\\n" + roster if roster else "")
    """
    if not alias_map:
        return ""
    lines = [
        "Known NPCs in this campaign — use these exact canonical names when an NPC "
        "appears in the source text, even if the text uses a variant:"
    ]
    for canonical in sorted(alias_map):
        aliases = alias_map[canonical]
        if aliases:
            lines.append(f"- {canonical} (also: {', '.join(aliases)})")
        else:
            lines.append(f"- {canonical}")
    return "\n".join(lines)
