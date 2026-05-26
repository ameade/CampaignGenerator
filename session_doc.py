#!/usr/bin/env python3
"""Generate a D&D session document from scene-anchored extractions.

Input shape: a directory of `NN_*.md` scene files (written by `scene_extract.py`).
Each file pairs a gm-assist scene summary (Pass 5's structural skeleton) with
the VTT-derived verbatim moments (Pass 5's quote source). This is the only
supported input shape.

Passes that still run:

  1. Consistency check (silent) — compares the recap against campaign context
     documents and produces a list of errors and contradictions.

  3. Narrative plan — assigns one narrator per scene from --characters,
     using the scene-extractions directory as the authoritative scene list.

  5. Narration (once per scene) — writes the scene in the assigned narrator's
     voice, anchored to the scene's gm-assist summary and verbatim moments.

(Pass 2 enhancement and Pass 4 character extraction are skipped — the
scene-extraction files already supply both inputs.)

The final document: rotating-voice narrative sections, optionally followed by
any pre-built `--enhanced-sections` block (Memorable Moments, Scenes, NPCs,
Locations, Items, Spells, Consistency Notes).

Writes one narration file per scene under `--per-scene-output DIR`. Use
`assemble.py` (Stage 4) to combine them into a single session document.

Usage:
  python session_doc.py session-mar \
      --scene-extractions vtt_scene_extractions/ \
      --context docs/campaign_state.md docs/world_state.md docs/party.md \
      --characters "Vukradin, Valphine, Soma, Brewbarry" \
      --examples examples/ \
      --per-scene-output narration/
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from campaignlib import (
    build_alias_normalizer,
    format_npc_roster,
    load_agent_prompt,
    load_alias_map,
    load_file_optional,
    make_client,
    stream_api,
)


# ── Pass 1: Consistency check ──────────────────────────────────────────────────

CONSISTENCY_SYSTEM = load_agent_prompt("session_doc/consistency")

# ── Pass 2: Enhance structured sections ───────────────────────────────────────

ENHANCE_SYSTEM = """\
You are enhancing the structured sections of a D&D session recap.
You will be given:
- The original recap
- Roleplay extractions — raw quoted dialogue and character moments from the session
- Session extractions — action detail, events, environmental context
- A consistency report flagging errors in the original
- (Optionally) a party document for character voice reference

Your job: produce improved versions of the NON-SUMMARY sections only.
The Summary will be replaced by a separate narrative pass — do not include it.

1. MEMORABLE MOMENTS — Keep all existing entries. Add new ones for any significant
   roleplay moment, memorable line, or dramatic beat in the extractions that isn't
   already captured. Format new entries consistently with the existing ones:
   bold description, italicised context note, blockquote for direct quotes.

2. CONSISTENCY NOTES — Append a new section at the end listing any issues from the
   consistency report that couldn't be silently fixed in the text (ambiguities,
   unresolved contradictions, things the GM should verify). Omit this section if
   there are no issues to flag.

3. ALL OTHER SECTIONS (Scenes, NPCs, Locations, Items, Spells) — Preserve exactly
   as they are. Do not rewrite, reorder, or add to them.

Output starting from ## Memorable Moments (or the first non-Summary section in the recap).
Do not include a Summary section — it is generated separately.
No preamble or commentary.
"""

# ── Pass 3: Narrative plan ────────────────────────────────────────────────────

PLAN_SYSTEM = load_agent_prompt("session_doc/plan")

# ── Pass 5: Per-character narration ───────────────────────────────────────────

NARRATE_SYSTEM_BASE = load_agent_prompt("session_doc/narrate/base")

EXAMPLES_BLOCK = load_agent_prompt("session_doc/narrate/examples_block")

PER_CHAR_EXAMPLES_BLOCK = load_agent_prompt("session_doc/narrate/per_char_examples")

VOICE_SPEC_BLOCK = load_agent_prompt("session_doc/narrate/voice_spec")

PREV_VOICE_CONTRAST_BLOCK = load_agent_prompt("session_doc/narrate/prev_voice_contrast")


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_character_roster(party_text: str) -> str:
    """Parse party.md and return a compact name → class list for prompt injection.

    Expects sections like:
        ## Soma
        **Tortle Druid 5, Player: Wade**

    Outputs:
        - Soma (Wade): Tortle Druid 5
    """
    roster = []
    current_name: str | None = None
    for line in party_text.splitlines():
        m = re.match(r'^## (.+)$', line.strip())
        if m:
            current_name = m.group(1).strip()
        elif current_name:
            cm = re.match(r'^\*\*(.+\d+.+)\*\*$', line.strip())
            if cm:
                class_info = cm.group(1)
                # Extract player name(s) if present
                # Supports: "Player: Wade", "Player: Wade/Kostadis"
                pm = re.search(r',\s*Player:\s*(.+)', class_info)
                if pm:
                    player = pm.group(1).strip().rstrip('*')
                    class_only = class_info[:pm.start()].strip()
                    roster.append(f"- {current_name} ({player}): {class_only}")
                else:
                    roster.append(f"- {current_name}: {class_info}")
                current_name = None
    return "\n".join(roster)


def load_voice_files(voice_dir: Path) -> dict[str, str]:
    """Load per-character voice files from a directory.

    Looks for files named {character_name}_voice.md or {character_name}.md
    (case-insensitive). Returns a dict mapping lowercased character name to content.
    """
    voices: dict[str, str] = {}
    for f in voice_dir.glob("*.md"):
        stem = f.stem.lower()
        # Strip trailing _voice suffix if present
        key = stem.removesuffix("_voice")
        voices[key] = f.read_text(encoding="utf-8").strip()
    return voices


def get_voice_note(voices: dict[str, str], narrator: str) -> str | None:
    """Look up a voice note for a narrator by case-insensitive name match."""
    key = narrator.lower().split()[0]  # match on first name
    return voices.get(key) or voices.get(narrator.lower())


def get_char_examples(per_char_examples: dict[str, str], narrator: str) -> str | None:
    """Look up per-character style examples by case-insensitive first-name match."""
    key = narrator.lower().split()[0]
    return per_char_examples.get(key) or per_char_examples.get(narrator.lower())


def extract_contrast_sample(text: str, max_sentences: int = 5) -> str:
    """First substantive paragraph's first ~5 sentences — Phase-3 contrast signal.

    Skips markdown headings, italic-only captions, and `---` separators so the
    sample is drawn from the first verbatim passage in a per-char examples file
    rather than the file's title or subtitle. Title and italic subtitle are often
    joined into one paragraph (single newline between them), so the skip checks
    chrome line-by-line, not chunk-as-a-whole.
    """
    def is_chrome(line: str) -> bool:
        s = line.strip()
        if not s or s == "---":
            return True
        if s.startswith("#"):
            return True
        if s.startswith("*") and s.endswith("*") and len(s) > 1:
            return True
        return False

    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk or chunk == "---":
            continue
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines or all(is_chrome(ln) for ln in lines):
            continue
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', chunk) if s.strip()]
        if not sentences:
            return chunk
        return " ".join(sentences[:max_sentences])
    return ""


def load_extractions(path: Path) -> list[tuple[str, str]]:
    files = sorted(path.glob("extract_*.md"))
    return [(f.name, f.read_text(encoding="utf-8").strip()) for f in files]


_SCENE_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n\n?(.*)\Z", re.DOTALL)


def _split_scene_body(body: str) -> tuple[str, str]:
    """Split the body of a scene_extract.py file into (gm_summary, verbatim_moments).

    The conventional shape produced by scene_extract.py:
        # Scene Name
        ## Scene summary (from gm-assist, verbatim)
        <gm-assist body>
        ## Verbatim moments
        <vtt-derived moments>

    Returns ('', body) when the headings are absent — the caller treats the
    whole file as moments and lets Pass 5 work out the structure.
    """
    summary_match = re.search(r"(?ms)^## Scene summary[^\n]*\n(.*?)(?=^## |\Z)", body)
    moments_match = re.search(r"(?ms)^## Verbatim moments[^\n]*\n(.*?)(?=^## |\Z)", body)
    if summary_match and moments_match:
        return summary_match.group(1).strip(), moments_match.group(1).strip()
    return "", body.strip()


def load_scene_extractions(path: Path) -> list[dict]:
    """Load scene-anchored extraction files written by scene_extract.py.

    Looks for `NN_*.md` files (sorted), parses the YAML frontmatter for the
    canonical `scene:` name, and returns ordered dicts:
        [{"name": str, "path": Path, "summary": str, "moments": str, "body": str}, ...]

    For each scene, prefers the user-edited `NN_<slug>.scaffold.md` over
    the raw Stage-2 `NN_<slug>.md` when both exist — matching the Editor
    behavior in `server/routers/scene_editor.py` so Narrate consumes the
    same file the GM was looking at.

    `summary` is the gm-assist scene body (used as Pass 5's structural
    skeleton) and `moments` is the VTT-derived verbatim extraction (used as
    Pass 5's quote source). When a file does not follow the dual-section
    layout, `summary` is empty and `moments` holds the full body.

    Files named `plan.md`, `enhanced_sections.md`, `consistency_report.md`,
    or starting with `_` are skipped (they are sibling artifacts, not scene
    extractions).
    """
    SKIP = {"plan.md", "enhanced_sections.md", "consistency_report.md"}
    by_stem: dict[str, Path] = {}
    for f in path.glob("*.md"):
        if f.name in SKIP or f.name.startswith("_"):
            continue
        if f.name.endswith(".scaffold.md"):
            stem = f.name[: -len(".scaffold.md")]
            is_scaffold = True
        else:
            stem = f.stem
            is_scaffold = False
        if not re.match(r"^\d{2}_", stem):
            continue
        # Scaffold wins over Stage-2; otherwise first one in.
        if is_scaffold or stem not in by_stem:
            by_stem[stem] = f
    items: list[dict] = []
    for stem in sorted(by_stem):
        f = by_stem[stem]
        text = f.read_text(encoding="utf-8")
        fallback_name = stem.split("_", 1)[1].replace("_", " ").title() if "_" in stem else stem
        m = _SCENE_FRONTMATTER_RE.match(text)
        if m:
            name = ""
            for line in m.group(1).splitlines():
                if line.strip().lower().startswith("scene:"):
                    name = line.split(":", 1)[1].strip()
                    break
            body = m.group(2).strip()
            if not name:
                name = fallback_name
        else:
            name = fallback_name
            body = text.strip()
        summary, moments = _split_scene_body(body)
        items.append({
            "name": name,
            "path": f,
            "body": body,
            "summary": summary,
            "moments": moments,
        })
    return items


def format_extractions(extractions: list[tuple[str, str]], heading: str) -> str:
    parts = [f"### Chunk {i}\n\n{content}"
             for i, (_, content) in enumerate(extractions, 1)]
    return f"## {heading}\n\n" + "\n\n---\n\n".join(parts)


DIALOGUE_INSTRUCTION_FULL = load_agent_prompt("session_doc/narrate/dialogue_full")

DIALOGUE_INSTRUCTION_CONDITIONAL = load_agent_prompt("session_doc/narrate/dialogue_conditional")

PROSE_MODE_INSTRUCTION = load_agent_prompt("session_doc/narrate/prose_mode")


SCENE_ANCHORED_DIRECTIVE = load_agent_prompt("session_doc/narrate/scene_anchored")


def build_narrate_system(examples_text: str | None, scene: str | None = None,
                         prose_mode: bool = False,
                         has_scene_events: bool = False,
                         scene_anchored: bool = False,
                         narrator: str = "",
                         char_examples: str | None = None,
                         voice_note: str | None = None,
                         genre: str | None = None) -> str:
    if examples_text:
        block = "\n" + EXAMPLES_BLOCK.replace("{examples}", examples_text.strip()) + "\n"
    else:
        block = ""
    if genre and genre.strip():
        genre_block = f"GENRE: {genre.strip()}\n"
    else:
        genre_block = ""
    if scene:
        scope = (f"- The scene you are writing: **{scene}**\n"
                 f"  STOP when this scene ends. Do not continue into what happened next.\n"
                 f"  Do not summarise what came before. Do not foreshadow what comes after.\n"
                 f"  This scene only.\n")
        length = ("Write as many paragraphs as needed to give every extracted moment its due — "
                  "do not compress multiple distinct beats into a single paragraph. "
                  "Target 600-900 words for a typical scene; expand each extracted moment into "
                  "2-3 sentences of observation, voice, or aside. Do NOT summarize the moments — "
                  "render each one with concrete sensory detail and the narrator's reaction. "
                  "A short, plot-beat-only output is a failure mode: if your draft is under "
                  "500 words, you have summarized rather than narrated; go back and expand. "
                  "Stop as soon as the scene is complete. "
                  "If you find yourself describing a new location or the next event, you have gone too far — stop.")
        dialogue = DIALOGUE_INSTRUCTION_CONDITIONAL
    else:
        scope = ""
        length = "Write as many paragraphs as needed to cover all the extracted moments — typically 4–8, but do not stop early."
        dialogue = DIALOGUE_INSTRUCTION_FULL
    if has_scene_events:
        scene_events_line = ("- Scene Events (authoritative) — the ordered account of what "
                             "happened; render from this faithfully\n"
                             "- Campaign Context — character backstory, NPC states, world detail\n")
        rendering = ("The Scene Events list is the authoritative account of what occurred. "
                     "Render it in this character's voice. Do not add events that are not listed. "
                     "The extracted moments below are your primary source for verbatim quotes — "
                     "weave those lines in exactly as written.\n\n")
    else:
        scene_events_line = ""
        rendering = ""
    result = (NARRATE_SYSTEM_BASE
              .replace("{genre_directive}", genre_block)
              .replace("{examples_block}", block)
              .replace("{scene_scope_line}", scope)
              .replace("{scene_events_line}", scene_events_line)
              .replace("{rendering_instruction}", rendering)
              .replace("{length_instruction}", length)
              .replace("{dialogue_instruction}", dialogue))
    if scene_anchored and narrator:
        result += "\n\n" + SCENE_ANCHORED_DIRECTIVE.replace("{narrator}", narrator)
    if prose_mode:
        result += "\n\n" + PROSE_MODE_INSTRUCTION
    if char_examples and narrator:
        block = (PER_CHAR_EXAMPLES_BLOCK
                 .replace("{narrator}", narrator)
                 .replace("{examples}", char_examples.strip()))
        result += "\n\n" + block
    if voice_note and narrator:
        block = (VOICE_SPEC_BLOCK
                 .replace("{narrator}", narrator)
                 .replace("{voice_note}", voice_note.strip()))
        result += "\n\n" + block
    if genre and genre.strip():
        # Repeat the genre directive at the tail of the prompt. The opening copy
        # is buried under ~150 lines of prose-mode/voice rules by the time
        # generation starts; smaller models lose the genre signal to recency.
        # Claude is unaffected by the duplicate — same instruction, same prompt.
        result += (
            "\n\nGENRE — FINAL REMINDER (this overrides any generic register the "
            "above rules suggest):\n" + genre.strip()
        )
    return result


def parse_plan(plan_text: str, total_chunks: int) -> list[dict]:
    sections = []
    for block in re.split(r"(?m)^## (?:Section|Scene) \d+", plan_text):
        block = block.strip()
        if not block:
            continue
        section: dict = {}
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("narrator:"):
                section["narrator"] = line.split(":", 1)[1].strip()
            elif line.startswith("chunks:"):
                raw = line.split(":", 1)[1].strip()
                m = re.match(r"(\d+)\s*[-–]\s*(\d+)", raw)
                if m:
                    section["chunk_start"] = int(m.group(1))
                    section["chunk_end"]   = int(m.group(2))
                else:
                    single = re.match(r"(\d+)", raw)
                    if single:
                        n = int(single.group(1))
                        section["chunk_start"] = n
                        section["chunk_end"]   = n
            elif line.startswith("scene:"):
                section["scene"] = line.split(":", 1)[1].strip()
            elif line.startswith("focus:"):
                section["focus"] = line.split(":", 1)[1].strip()
        if "narrator" in section and "chunk_start" in section:
            section["chunk_start"] = max(1, min(section["chunk_start"], total_chunks))
            section["chunk_end"]   = max(section["chunk_start"],
                                         min(section["chunk_end"], total_chunks))
            sections.append(section)
    return sections


def estimate_narration_tokens(text: str) -> int:
    """Rough estimate of how many tokens the narration pass will need.

    Prose narration expands compressed extraction notes by roughly 4x for
    dialogue-heavy scenes (the quotes are written out in full) and 3x for
    action/environment-only scenes. Rounded up to the nearest 250.
    """
    has_dialogue = bool(re.search(r'(?m)^[A-Z][^:\n]+:\s*"', text))
    expansion = 4 if has_dialogue else 3
    estimated = int(len(text) / 4 * expansion)
    return max(500, ((estimated + 249) // 250) * 250)


def extract_scene_text(recap: str, scene_name: str) -> str:
    """Return the text of a single named scene from the recap's ## Scenes section."""
    lines = recap.splitlines()
    in_scenes = False
    in_target = False
    collected: list[str] = []
    for line in lines:
        if line.strip() == "## Scenes":
            in_scenes = True
            continue
        if in_scenes and line.startswith("## "):
            break  # left the Scenes section
        if in_scenes and line.startswith("### "):
            if in_target:
                break  # reached the next scene
            if line.strip("# ").strip().lower() == scene_name.lower():
                in_target = True
            continue
        if in_target:
            collected.append(line)
    return "\n".join(collected).strip()


def build_narrate_prompt(narrator: str, focus: str, char_moments: str,
                          party: str | None, handoff: str, roster: str = "",
                          scene_text: str | None = None,
                          context_docs: list[str] | None = None,
                          prev_narrator: str | None = None,
                          prev_voice_sample: str | None = None) -> str:
    parts = [f"## Narrator: {narrator}\n## Focus: {focus}"]
    if roster:
        parts.append(f"## Character Classes (definitive — never contradict these)\n\n{roster}")
    if party:
        parts.append(f"## Party Document (authoritative source for character classes, "
                     f"abilities, and roles)\n\n{party.strip()}")
    if context_docs:
        combined = "\n\n---\n\n".join(context_docs)
        parts.append(
            f"## Campaign History\n\n"
            f"This is the accumulated campaign context — past events, faction relationships, "
            f"NPC histories, world conditions. When the current scene creates a natural "
            f"opening, draw on this for a brief memory, reflection, or flashback:\n"
            f"- A past decision that echoes in the current one\n"
            f"- An NPC the narrator has history with\n"
            f"- A cost or consequence that has been accumulating\n"
            f"- A pattern the narrator has noticed repeating\n\n"
            f"Keep it brief: one or two sentences of interior thought, then return to the "
            f"present. Do not summarize the history. Let it surface as the narrator's "
            f"inner life.\n\n"
            f"{combined}"
        )
    if scene_text:
        parts.append(
            f"## Scene: What Happened\n\n"
            f"This is the GM's authoritative account of what occurred in this scene. "
            f"Use it as the structural skeleton — the events, decisions, and NPC reactions "
            f"that the narration must cover. The character's Roleplay Moments (below) "
            f"provide verbatim quotes and character-specific beats to weave in.\n\n"
            f"{scene_text.strip()}"
        )
    if (prev_narrator and prev_voice_sample
            and prev_narrator.lower() != narrator.lower()):
        contrast = (PREV_VOICE_CONTRAST_BLOCK
                    .replace("{prev_narrator}", prev_narrator)
                    .replace("{prev_voice_sample}", prev_voice_sample.strip())
                    .replace("{narrator}", narrator))
        parts.append(contrast)
    if handoff:
        parts.append(f"## Handoff from previous narrator\n\"{handoff}\"")
    # When an authoritative scene account is provided, rename the extraction block to
    # make clear it is the quote source, not the event source
    if scene_text:
        parts.append(f"## Verbatim Quotes — {narrator}\n"
                     f"(weave these into the narrative exactly as written)\n\n"
                     f"{char_moments.strip()}")
    else:
        parts.append(
            f"## {narrator}'s Scene Moments\n"
            f"(grouped format: each action beat line starting with \"-\" is followed by "
            f"the dialogue that occurred during it — narrate beat and quotes together "
            f"as a single moment; beats with no quotes are action-only moments)\n\n"
            f"{char_moments.strip()}"
        )
    return "\n\n---\n\n".join(parts)


# ── Inputs ────────────────────────────────────────────────────────────────────

@dataclass
class Inputs:
    # Mandatory
    recap_path: Path
    recap: str
    scene_extractions: list[dict]
    # Alias normalization
    alias_map: dict
    normalize: Callable[[str], str]
    npc_roster: str
    # Optional text inputs
    summary_extractions: list[tuple[str, str]] = field(default_factory=list)
    session_summary: str = ""
    context_parts: list[str] = field(default_factory=list)
    party: str | None = None
    roster: str = ""
    voice_files: dict[str, str] = field(default_factory=dict)
    characters: list[str] = field(default_factory=list)
    examples_text: str | None = None
    per_char_examples: dict[str, str] = field(default_factory=dict)
    enhanced_sections: str = ""
    # Output / cache directories
    extract_dir: Path | None = None
    per_scene_output_dir: Path | None = None


def _check_proposal(args, parser) -> None:
    """Fail-fast guard for the dossier-proposal approval check.

    Runs BEFORE any Claude calls so an unapproved proposal aborts before tokens
    are spent. No-op unless --require-proposal is set.
    """
    if not args.require_proposal:
        return
    import os as _os

    from proposal_loader import (
        ProposalNotApproved,
        ProposalRequired,
        require_approved_proposal,
    )
    campaign_dir = (
        args.campaign_dir
        or _os.environ.get("CAMPAIGN_DIR")
        or str(Path(args.recap).expanduser().resolve().parent)
    )
    try:
        require_approved_proposal(campaign_dir)
    except (ProposalRequired, ProposalNotApproved) as exc:
        parser.error(str(exc))


def load_inputs(args, parser) -> Inputs:
    """Load every file/dir argument into a single Inputs object.

    Order matters: the alias normalizer is built before any text source is
    normalized. The on-screen diagnostic prints match the prior in-line
    behaviour byte-for-byte.
    """
    # Per-scene output dir (created up-front so Pass 1's report has a home)
    per_scene_output_dir: Path | None = None
    if args.per_scene_output:
        per_scene_output_dir = Path(args.per_scene_output).expanduser()
        per_scene_output_dir.mkdir(parents=True, exist_ok=True)

    # Recap (mandatory)
    recap_path = Path(args.recap).expanduser()
    if not recap_path.exists():
        print(f"Error: recap file not found: {recap_path}", file=sys.stderr)
        sys.exit(1)
    recap = recap_path.read_text(encoding="utf-8")
    print(f"  Recap: {recap_path.name} ({len(recap):,} chars)")

    # Alias map + normalizer (must come before any normalize() call)
    alias_map = load_alias_map(args.dossier_dir)
    normalize, _ = build_alias_normalizer(alias_map)
    npc_roster = format_npc_roster(alias_map)
    if alias_map:
        print(f"  Alias map: {len(alias_map)} NPC(s) from {args.dossier_dir}")
    recap = normalize(recap)

    # Scene extractions (mandatory after Phase 1)
    if not args.scene_extractions:
        parser.error("--scene-extractions is required")
    sx_dir = Path(args.scene_extractions).expanduser()
    if not sx_dir.is_dir():
        print(f"Error: --scene-extractions directory not found: {sx_dir}", file=sys.stderr)
        sys.exit(1)
    scene_extractions = load_scene_extractions(sx_dir)
    if not scene_extractions:
        print(f"Error: no scene extraction files found in {sx_dir} "
              f"(expected NN_*.md files written by scene_extract.py)", file=sys.stderr)
        sys.exit(1)
    print(f"  Scene extractions: {len(scene_extractions)} scene(s) from {sx_dir}")
    for i, sx in enumerate(scene_extractions, 1):
        print(f"    {i}. {sx['name']}")

    # Summary extractions (optional)
    summary_extractions: list[tuple[str, str]] = []
    if args.summary_extract_dir:
        summary_extractions = load_extractions(Path(args.summary_extract_dir).expanduser())
        if alias_map:
            summary_extractions = [(n, normalize(c)) for n, c in summary_extractions]
        print(f"  Session extractions:  {len(summary_extractions)} chunk(s)")

    # Session summary (optional)
    session_summary: str = ""
    if args.session_summary:
        _p = Path(args.session_summary).expanduser()
        if not _p.exists():
            print(f"Error: --session-summary file not found: {_p}", file=sys.stderr)
            sys.exit(1)
        session_summary = normalize(_p.read_text(encoding="utf-8"))
        print(f"  Session summary:      {_p.name} ({len(session_summary):,} chars)")

    # Context files (optional, for Pass 1)
    context_parts: list[str] = []
    if args.context:
        for ctx in args.context:
            p = Path(ctx).expanduser()
            if p.exists():
                context_parts.append(f"## {p.name}\n\n{p.read_text(encoding='utf-8').strip()}")
            else:
                print(f"  Warning: context file not found: {p}", file=sys.stderr)
        if context_parts:
            print(f"  Context files: {len(context_parts)}")

    # Party + roster (optional)
    party: str | None = None
    roster: str = ""
    if args.party:
        _p = Path(args.party).expanduser()
        if not _p.exists():
            print(f"Error: --party file not found: {_p}", file=sys.stderr)
            sys.exit(1)
        party = _p.read_text(encoding="utf-8")
        roster = extract_character_roster(party)
        if roster:
            print(f"  Character roster: {roster.count(chr(10)) + 1} character(s)")

    # Voice files (optional)
    voice_files: dict[str, str] = {}
    if args.voice_dir:
        vd = Path(args.voice_dir).expanduser()
        if vd.is_dir():
            voice_files = load_voice_files(vd)
            if voice_files:
                print(f"  Voice files: {len(voice_files)} character(s) "
                      f"({', '.join(voice_files.keys())})")
        else:
            print(f"  Warning: voice-dir not found: {vd}", file=sys.stderr)

    # Character list (optional, used by --examples routing)
    characters = (
        [c.strip() for c in args.characters.split(",") if c.strip()]
        if args.characters else []
    )

    # Examples — split global vs per-character (per-character keyed off `characters`)
    examples_text: str | None = None
    per_char_examples: dict[str, str] = {}
    if args.examples:
        ed = Path(args.examples).expanduser()
        if ed.is_dir():
            # Files whose stem (after stripping an optional _examples suffix)
            # matches a character's first name route to that character only.
            # Everything else falls into the global pool, preserving the
            # pre-existing behaviour.
            char_keys = {c.lower().split()[0] for c in characters if c}
            global_parts: list[str] = []
            for p in sorted(ed.glob("*.md")):
                stem_lower = p.stem.lower()
                key = stem_lower.removesuffix("_examples")
                snippet = f"### Example: {p.name}\n\n{p.read_text(encoding='utf-8').strip()}"
                if key in char_keys:
                    existing = per_char_examples.get(key, "")
                    per_char_examples[key] = (
                        existing + "\n\n---\n\n" + snippet if existing else snippet
                    )
                else:
                    global_parts.append(snippet)
            if global_parts:
                examples_text = "\n\n---\n\n".join(global_parts)
                print(f"  Style examples (global): {len(global_parts)} file(s) "
                      f"from {ed} ({len(examples_text):,} chars)")
            if per_char_examples:
                print(f"  Style examples (per-character): "
                      f"{', '.join(sorted(per_char_examples.keys()))}")
            if not global_parts and not per_char_examples:
                print(f"  Warning: no .md files found in examples dir: {ed}", file=sys.stderr)
        else:
            print(f"  Warning: examples dir not found: {ed}", file=sys.stderr)

    # Extract-dir (validated up-front so failures surface before API calls)
    extract_dir: Path | None = None
    if args.extract_dir:
        extract_dir = Path(args.extract_dir).expanduser()
        extract_dir.mkdir(parents=True, exist_ok=True)

    # Enhanced sections (pre-built Memorable Moments / NPCs / Scenes block)
    enhanced_sections: str = ""
    if args.enhanced_sections:
        _p = Path(args.enhanced_sections).expanduser()
        if not _p.exists():
            print(f"Error: --enhanced-sections file not found: {_p}", file=sys.stderr)
            sys.exit(1)
        enhanced_sections = _p.read_text(encoding="utf-8")
        print(f"  Enhanced sections: {_p.name} ({len(enhanced_sections):,} chars)")

    return Inputs(
        recap_path=recap_path,
        recap=recap,
        scene_extractions=scene_extractions,
        alias_map=alias_map,
        normalize=normalize,
        npc_roster=npc_roster,
        summary_extractions=summary_extractions,
        session_summary=session_summary,
        context_parts=context_parts,
        party=party,
        roster=roster,
        voice_files=voice_files,
        characters=characters,
        examples_text=examples_text,
        per_char_examples=per_char_examples,
        enhanced_sections=enhanced_sections,
        extract_dir=extract_dir,
        per_scene_output_dir=per_scene_output_dir,
    )


# ── Per-section narration ─────────────────────────────────────────────────────

@dataclass
class PlanContext:
    """Cross-iteration plan state read by narrate_section.

    `plan_narrator_by_scene` maps the 1-based plan position to its narrator
    name; the section loop uses position i-1 to find the previous narrator
    for the contrast signal, surviving any --scene filter.
    """
    plan_narrator_by_scene: dict[int, str]


@dataclass
class SectionResult:
    label: str
    narration: str
    handoff: str


def narrate_section(
    i: int,
    section: dict,
    inputs: Inputs,
    args,
    client,
    plan_ctx: PlanContext,
    handoff: str,
) -> SectionResult | None:
    """Run Passes 4 and 5 for a single scene.

    Returns `None` when --extract-only short-circuits the run (no narration
    was produced and `handoff` should not advance). Otherwise returns the
    rendered narration, its label, and the handoff line for the next section.
    """
    narrator   = section["narrator"]
    focus      = section.get("focus", "")
    scene_name = section.get("scene", "")
    label      = f"{narrator} — {scene_name}" if scene_name else narrator

    narrate_tokens = args.narrate_tokens or 16000

    # Pass 4: load the scene-anchored extraction file by name
    # (case-insensitive), falling back to the i-th file. Character
    # extraction itself is skipped — the scene file already carries both
    # the gm-assist summary and the verbatim moments needed for Pass 5.
    match: dict | None = None
    sn = (scene_name or "").lower().strip()
    if sn:
        for sx in inputs.scene_extractions:
            if sx["name"].lower().strip() == sn:
                match = sx
                break
    if match is None and 1 <= i <= len(inputs.scene_extractions):
        match = inputs.scene_extractions[i - 1]
    if match is None:
        print(f"Error: no scene extraction matches '{scene_name}' (scene {i}).",
              file=sys.stderr)
        sys.exit(1)
    char_moments = match["moments"] or match["body"]
    scene_summary_override = match["summary"] or None
    print(f"\n[Pass 4 scene {i}: Skipped — scene-anchored extraction loaded — {label}]")
    est = estimate_narration_tokens(char_moments)
    warn = f"  ⚠ estimated {est} — add 'tokens: {est}' to override" if est > narrate_tokens else ""
    print(f"  → {len(char_moments):,} chars from {match['path'].name}"
          f"  (limit: {narrate_tokens}, est. ~{est}){warn}")

    if args.extract_only:
        return None

    # Pass 5: narrate from the scene's moments + gm-assist summary
    voice_note = (get_voice_note(inputs.voice_files, narrator)
                  if inputs.voice_files else None)
    char_examples = (get_char_examples(inputs.per_char_examples, narrator)
                     if inputs.per_char_examples else None)
    # Phase 3 contrast: sample the previous narrator's voice from their
    # per-char examples (not from the prior scene's output) so single-scene
    # runs from the UI still get the contrast signal.
    prev_narrator = plan_ctx.plan_narrator_by_scene.get(i - 1)
    prev_voice_sample = None
    if prev_narrator and prev_narrator.lower() != narrator.lower():
        prev_text = (get_char_examples(inputs.per_char_examples, prev_narrator)
                     if inputs.per_char_examples else None)
        if prev_text:
            prev_voice_sample = extract_contrast_sample(prev_text)
        else:
            prev_narrator = None
    scene_events_str = scene_summary_override or ""
    if not scene_events_str and scene_name:
        if inputs.enhanced_sections:
            scene_events_str = extract_scene_text(inputs.enhanced_sections, scene_name)
        elif inputs.recap:
            scene_events_str = extract_scene_text(inputs.recap, scene_name)
    narrate_context = (inputs.context_parts
                       if args.reflections and inputs.context_parts else None)
    extras = [x for x in ["voice notes" if voice_note else "",
                           "per-char examples" if char_examples else "",
                           "prev-narrator contrast" if prev_voice_sample else "",
                           "enhanced context" if (scene_events_str or narrate_context) else ""] if x]
    print(f"[Pass 5 scene {i}: Narrate — {label}"
          f"{' (' + ', '.join(extras) + ')' if extras else ''}]")
    print("─" * 60)
    # In scene mode skip the heavy examples block to keep the prompt lean —
    # the style constraint is already carried by voice notes and the handoff.
    narrate_system = build_narrate_system(
        None if scene_name else inputs.examples_text,
        scene=scene_name or None,
        prose_mode=args.prose_mode,
        has_scene_events=bool(scene_events_str or narrate_context),
        scene_anchored=bool(scene_summary_override),
        narrator=narrator,
        char_examples=char_examples,
        voice_note=voice_note,
        genre=args.narration_genre,
    )
    narrate_prompt = build_narrate_prompt(narrator, focus, char_moments,
                                          inputs.party, handoff,
                                          inputs.roster,
                                          scene_text=scene_events_str or None,
                                          context_docs=narrate_context,
                                          prev_narrator=prev_narrator,
                                          prev_voice_sample=prev_voice_sample)
    narration = stream_api(client, narrate_system, narrate_prompt,
                           args.model, max_tokens=narrate_tokens, verbose=args.verbose)
    print("─" * 60)

    narration = narration.strip()
    new_handoff = narration.rsplit("\n", 1)[-1].strip().strip('"').strip("'")

    # Per-scene output: write narration to disk immediately so users can
    # edit single scenes and re-assemble via assemble.py.
    if inputs.per_scene_output_dir is not None:
        slug_scene = re.sub(r"[^a-z0-9]+", "_", (scene_name or narrator).lower()).strip("_")
        session_id = inputs.recap_path.parent.name
        per_scene_file = (inputs.per_scene_output_dir
                          / f"session_doc_scene_{i:02d}_{slug_scene}.md")
        frontmatter = (
            "---\n"
            f"scene: {i:02d}\n"
            f"slug: {slug_scene}\n"
            f"narrator: {narrator}\n"
            f"scene_name: {scene_name}\n"
            f"session: {session_id}\n"
            "---\n\n"
        )
        per_scene_file.write_text(frontmatter + narration + "\n", encoding="utf-8")
        print(f"  Wrote per-scene narration: {per_scene_file.name}")

    return SectionResult(label=label, narration=narration, handoff=new_handoff)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a D&D session document: narrative voice + enhanced structured sections."
    )
    parser.add_argument("recap", metavar="FILE",
                        help="Existing session recap file (e.g. from gmassisstant.app)")
    parser.add_argument("--per-scene-output", default=None, metavar="DIR",
                        help="Write one narration file per scene "
                             "(session_doc_scene_NN_<slug>.md, with YAML "
                             "frontmatter) into this directory. Run "
                             "assemble.py (Stage 4) to combine the per-scene "
                             "files into a single session document. Compatible "
                             "with --scene N for re-narrating a single scene.")
    parser.add_argument("--summary-extract-dir", metavar="DIR",
                        help="vtt_extractions/ — action detail and event context")
    parser.add_argument("--session-summary", metavar="FILE",
                        help="Synthesised VTT session summary (e.g. session-clean.md). "
                             "Used as an authoritative event log in passes 1, 3, and 4.")
    parser.add_argument("--context", nargs="+", metavar="FILE",
                        help="Campaign context files for consistency check "
                             "(e.g. campaign_state.md world_state.md party.md)")
    parser.add_argument("--party", metavar="FILE",
                        help="party.md — backstory, personality, relationships")
    parser.add_argument("--characters", metavar="NAMES",
                        help='Comma-separated roster, e.g. "Vukradin, Valphine, Soma, Brewbarry"')
    parser.add_argument("--session-name", default="", metavar="NAME",
                        help='e.g. "Session 12 — Icespire Hold"')
    parser.add_argument("--examples", metavar="DIR",
                        help="Directory of handcrafted summary files as style references for narration")
    parser.add_argument("--voice-dir", metavar="DIR",
                        help="Directory of per-character voice files written by players. "
                             "Name files {character}_voice.md or {character}.md. "
                             "Each file is injected only into that character's narration pass.")
    parser.add_argument("--narrator", metavar="NAME",
                        help="Generate narration for one character only (skips passes 1–2, "
                             "runs the plan, then extracts and narrates the named character). "
                             "Useful for tweaking voice files without regenerating the full doc.")
    parser.add_argument("--plan-file", metavar="FILE",
                        help="Use a pre-written plan file instead of running pass 3. "
                             "Write the file in the same format as --plan-only output. "
                             "Useful when the auto-generated plan has overlap issues.")
    parser.add_argument("--plan-only", action="store_true",
                        help="Run through the narrative plan and exit without generating text")
    parser.add_argument("--no-plan-review", action="store_true",
                        help="Skip the Pass 3 plan-review checkpoint and continue into "
                             "extraction immediately. Use when --plan-file is not available "
                             "but the plan is already known-good.")
    parser.add_argument("--extract-dir", metavar="DIR",
                        help="Save plan.md and consistency_report.md to this directory "
                             "for human review before narration.")
    parser.add_argument("--scene", nargs="+", type=int, metavar="N",
                        help="Run only the specified scene number(s) from the plan (1-based). "
                             "Useful for re-running a single scene without regenerating the rest.")
    parser.add_argument("--extract-only", action="store_true",
                        help="Run passes 1–4, save extractions to --extract-dir, then stop. "
                             "Skips narration so you can review/edit before committing tokens.")
    parser.add_argument("--narrate-tokens", type=int, default=None, metavar="N",
                        help="Override the narration token limit for all scenes in this run "
                             "(default: 1500 for scene mode, 12000 for chunk mode). "
                             "Individual scenes can also be overridden by adding 'tokens: N' "
                             "as the first line of their extraction file.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and print all prompts for passes 4-5 without calling the API. "
                             "Useful for inspecting what each scene sends before committing.")
    parser.add_argument("--prose-mode", action="store_true",
                        help="Strip all mechanical/game language and GM framing from narration. "
                             "GM descriptions become direct world perception; dice rolls and HP "
                             "become narrative consequence.")
    parser.add_argument("--narration-genre", default=None, metavar="TEXT",
                        help="One-line genre/register directive injected into the "
                             "Pass-5 narration system prompt (e.g. 'First-person "
                             "comic-noir fantasy memoir — observational, dry, "
                             "irony-forward'). When unset, no genre line is "
                             "added — narration prompt is identical to no-flag "
                             "behaviour.")
    parser.add_argument("--reflections", action="store_true",
                        help="Inject campaign_state and world_state context into the narration "
                             "prompt so the narrator can draw on past events as memories, "
                             "flashbacks, and reflections. Requires --context files.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print the full system and user prompt before each API call")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument(
        "--dgx-endpoint",
        default=None,
        metavar="URL",
        help="Route LLM calls to an OpenAI-compatible server instead of Anthropic "
             "(e.g. http://192.168.1.147:8001/v1 for vLLM on the DGX Spark). "
             "Falls back to the DGX_ENDPOINT env var when unset.",
    )
    parser.add_argument(
        "--dgx-model",
        default=None,
        metavar="NAME",
        help="Model name to send to the DGX endpoint "
             "(default: Qwen/Qwen2.5-14B-Instruct-AWQ, or DGX_MODEL env var). "
             "Ignored when --dgx-endpoint is unset.",
    )
    parser.add_argument("--enhanced-sections", metavar="FILE",
                        help="Pre-built structured sections (Memorable Moments, NPCs, Scenes, "
                             "etc.). Injected as scene context and campaign context in "
                             "narration (Pass 5).")
    parser.add_argument("--fast", action="store_true",
                        help="Use Haiku instead of Sonnet (~4x cheaper, faster, slightly lower quality)")
    parser.add_argument("--dossier-dir", metavar="DIR", default=None,
                        help="Directory of per-NPC dossier files (built by "
                             "planning.py --build-dossiers). If given, every "
                             "alias in dossier frontmatter is rewritten to its "
                             "canonical name in recap/extractions before Pass 4, "
                             "and a 'Known NPCs' roster seeds the extract prompt.")
    parser.add_argument("--scene-extractions", metavar="DIR", default=None,
                        help="Directory of scene-anchored extractions (written by "
                             "scene_extract.py). Required. Pass 3 reads the scene "
                             "checklist from this directory's filenames/frontmatter; "
                             "each scene file is fed directly to Pass 5 with a "
                             "narrator-POV directive.")
    parser.add_argument("--campaign-dir", default=None,
                        help="Campaign workspace root (default: $CAMPAIGN_DIR "
                             "or the recap file's parent directory). Used to "
                             "locate docs/dossier_proposal.md.")
    parser.add_argument("--require-proposal", action="store_true",
                        help="Refuse to run unless "
                             "<campaign-dir>/docs/dossier_proposal.md exists "
                             "and has been approved (status banner edited "
                             "away from `candidates only`).")
    args = parser.parse_args()
    if args.per_scene_output is None and not args.plan_only and not args.extract_only:
        parser.error("--per-scene-output is required "
                     "(or use --plan-only / --extract-only).")
    if args.fast:
        args.model = "claude-haiku-4-5-20251001"
        print("  [fast mode: claude-haiku-4-5-20251001]")

    # Proposal check runs BEFORE input loading — refuse guard for unapproved
    # dossier proposals, fail-fast before any Claude calls.
    _check_proposal(args, parser)

    inputs = load_inputs(args, parser)
    client = make_client(endpoint=args.dgx_endpoint, model_override=args.dgx_model)

    single_narrator = args.narrator.strip() if args.narrator else None

    # Re-narration: explicit plan + scene filter means Passes 1–2 already ran.
    renarration_mode = bool(args.plan_file and args.scene)

    # ── Pass 1: Consistency check ─────────────────────────────────────────────
    # This block is dead code and will be deleted in a follow-up.
    # Consistency check is now handled entirely by the enhanced_sections input.
    consistency_report = ""
    if inputs.context_parts:
        print(f"\n[Pass 1: Consistency check | model: {args.model}]")
        print("=" * 60)
        consistency_parts = ["## Session Recap\n\n" + inputs.recap.strip()]
        if inputs.session_summary:
            consistency_parts.append(
                "## This Session — VTT Summary (authoritative event log)\n\n"
                + inputs.session_summary.strip()
            )
        consistency_parts.append(
            "## Campaign Context\n\n" + "\n\n---\n\n".join(inputs.context_parts)
        )
        consistency_prompt = "\n\n---\n\n".join(consistency_parts)
        consistency_report = stream_api(client, CONSISTENCY_SYSTEM, consistency_prompt,
                                        args.model, silent=True, verbose=args.verbose)
        issue_count = consistency_report.count("**Location**")
        if issue_count:
            print(f"  Found {issue_count} potential issue(s):")
            for line in consistency_report.splitlines():
                if line.startswith("- **Issue**") or line.startswith("**Issue**"):
                    print(f"    {line.strip()}")
        else:
            print("  No issues found.")
        print("=" * 60)

        # Save to disk so the user can read the report on its own (it also
        # gets folded into Pass 2's enhanced_sections.md, but that's buried
        # in a much larger file).
        report_dir = inputs.extract_dir or inputs.per_scene_output_dir
        if report_dir and consistency_report.strip():
            report_out = report_dir / "consistency_report.md"
            report_out.write_text(consistency_report, encoding="utf-8")
            print(f"  Consistency report saved: {report_out}")
    else:
        print("\n[Pass 1: Consistency check skipped — no --context files provided]")

    # Pass 2 (enhance structured sections) is no longer run inline.
    # `enhanced_sections` is supplied via --enhanced-sections.

    # ── Pass 3: Narrative plan ─────────────────────────────────────────────────
    if args.plan_file:
        plan_path = Path(args.plan_file).expanduser()
        if not plan_path.exists():
            print(f"Error: plan file not found: {plan_path}", file=sys.stderr)
            sys.exit(1)
        plan_text = plan_path.read_text(encoding="utf-8")
        print(f"\n[Pass 3: Narrative plan loaded from {plan_path.name}]")
    else:
        print(f"\n[Pass 3: Narrative plan | {len(inputs.scene_extractions)} scene(s) | model: {args.model}]")
        print("=" * 60)

        plan_parts: list[str] = []
        if args.session_name:
            plan_parts.append(f"# Session: {args.session_name}")
        if inputs.characters:
            plan_parts.append("## Available narrators\n"
                              + "\n".join(f"- {c}" for c in inputs.characters))
        if inputs.summary_extractions:
            s_parts = [f"### Chunk {i}\n\n{content}"
                       for i, (_, content) in enumerate(inputs.summary_extractions, 1)]
            plan_parts.append("## Session Extractions\n"
                              "(action detail, events, environmental context)\n\n"
                              + "\n\n---\n\n".join(s_parts))
        if inputs.session_summary:
            plan_parts.append(
                "## Session Summary (authoritative — use to understand the full event arc "
                "and assign scenes to the character with the most interesting perspective)\n\n"
                + inputs.session_summary.strip()
            )
        if inputs.party:
            plan_parts.append(f"## Party Document\n\n{inputs.party.strip()}")
        # Authoritative checklist: the scene_extractions/ directory.
        # The recap is irrelevant here — the user has already committed to
        # the human-verified scene list when they ran scene_extract.py.
        scene_lines = [f"### {sx['name']}" for sx in inputs.scene_extractions]
        if scene_lines:
            checklist = "\n".join(scene_lines)
            source = "scene_extractions/"
            plan_parts.append(
                f"## Session Scenes (from {source} — every scene below must "
                f"appear in your plan, in this exact order)\n\n"
                + checklist
            )

        plan_text = stream_api(client, PLAN_SYSTEM, "\n\n---\n\n".join(plan_parts), args.model,
                               verbose=args.verbose)
        print("=" * 60)

    sections = parse_plan(plan_text, len(inputs.scene_extractions) or 1)
    if not sections:
        print("Error: could not parse narrative plan. Raw output:", file=sys.stderr)
        print(plan_text, file=sys.stderr)
        sys.exit(1)

    print(f"\nPlan: {len(sections)} section(s)")
    for i, s in enumerate(sections, 1):
        scene_label = f"  [{s['scene']}]" if s.get("scene") else ""
        print(f"  {i}. {s['narrator']:15s}  chunks {s['chunk_start']}–{s['chunk_end']}"
              f"{scene_label}  — {s.get('focus', '')}")

    if inputs.characters:
        # Warn about narrators the model invented outside the roster
        roster_lower = {c.lower() for c in inputs.characters}
        intruders = [s["narrator"] for s in sections
                     if s["narrator"].lower() not in roster_lower]
        if intruders:
            print(f"\nWarning: plan contains narrator(s) not in --characters: "
                  f"{', '.join(intruders)}")
            print("  Re-run with --plan-only or use --plan-file to fix.")

        assigned = {s["narrator"] for s in sections}
        missing = [c for c in inputs.characters if c not in assigned]
        if missing:
            print(f"\nWarning: these characters have no section: {', '.join(missing)}")
            print("  Re-run with --plan-only to inspect the plan.")

    # Warn when characters share a multi-chunk overlap — two chars on the same
    # single chunk is the normal 2+2 distribution and is fine (extraction
    # isolates their moments). The problem is when one char's range spans
    # multiple chunks that another char is also covering in full.
    for i, a in enumerate(sections):
        for b in sections[i + 1:]:
            a_range = set(range(a["chunk_start"], a["chunk_end"] + 1))
            b_range = set(range(b["chunk_start"], b["chunk_end"] + 1))
            overlap = a_range & b_range
            if overlap and (len(a_range) > 1 or len(b_range) > 1):
                print(f"\nWarning: {a['narrator']} (chunks {a['chunk_start']}–{a['chunk_end']}) "
                      f"and {b['narrator']} (chunks {b['chunk_start']}–{b['chunk_end']}) "
                      f"overlap — they will both narrate the same events.")
                print("  Consider re-running with --plan-only and adjusting the plan.")

    if single_narrator:
        matched = [s for s in sections
                   if s["narrator"].lower() == single_narrator.lower()]
        if not matched:
            names = ", ".join(s["narrator"] for s in sections)
            print(f"Error: narrator '{single_narrator}' not found in plan. "
                  f"Plan has: {names}", file=sys.stderr)
            sys.exit(1)
        sections = matched
        print(f"\nSingle-narrator mode: running passes 4–5 for {sections[0]['narrator']} only.")

    # Plan-position lookup for the Phase 3 contrast signal — survives --scene
    # filtering so single-scene runs can still look up the prior narrator.
    plan_narrator_by_scene: dict[int, str] = {
        idx: s["narrator"] for idx, s in enumerate(sections, 1)
    }

    if args.scene:
        total = len(sections)
        bad = [n for n in args.scene if n < 1 or n > total]
        if bad:
            print(f"Error: scene number(s) out of range: {bad} (plan has {total} scene(s))",
                  file=sys.stderr)
            sys.exit(1)
        # Keep original 1-based index on the section so filenames stay consistent
        sections = [(n, sections[n - 1]) for n in args.scene]
        labels = ", ".join(
            f"{n}. {s['narrator']}" + (f" [{s['scene']}]" if s.get('scene') else "")
            for n, s in sections
        )
        print(f"\nScene filter: running passes 4–5 for {labels} only.")
    else:
        sections = list(enumerate(sections, 1))

    # Save plan.md alongside extract_dir / per_scene_output_dir so a later run
    # can pass --plan-file to skip Pass 3. Must happen before --plan-only return
    # so Plan & Check actually writes plan.md (with narrators and per-scene focus).
    plan_cache_dir = inputs.extract_dir or inputs.per_scene_output_dir
    if plan_cache_dir:
        plan_save = plan_cache_dir / "plan.md"
        plan_save.write_text(plan_text, encoding="utf-8")
        print(f"  Plan saved to: {plan_save}")

    if args.plan_only:
        return

    if inputs.extract_dir:
        # Mandatory human checkpoint: stop here so the user can review plan.md
        # before narration commits tokens. Skipped when --plan-file was supplied
        # (human already reviewed it) or --no-plan-review.
        if not args.plan_file and not args.no_plan_review:
            print(
                f"\n[Pass 3 checkpoint] Review the plan before running narration:\n"
                f"  {plan_save}\n\n"
                f"Then re-run with:\n"
                f"  --plan-file {plan_save}"
            )
            return

    # ── Passes 4 & 5: Extract then narrate ────────────────────────────────────
    plan_ctx = PlanContext(plan_narrator_by_scene=plan_narrator_by_scene)
    section_texts: list[tuple[str, str]] = []
    handoff = ""

    for i, section in sections:
        result = narrate_section(i, section, inputs, args, client, plan_ctx, handoff)
        if result is None:
            continue
        section_texts.append((result.label, result.narration))
        handoff = result.handoff

    if args.extract_only:
        print(f"\nPlan and consistency report saved to: {inputs.extract_dir}")
        print("Review, then re-run with --plan-file <plan.md> to narrate.")
        return

    print(f"\nWrote {len(section_texts)} per-scene narration file(s) to: "
          f"{inputs.per_scene_output_dir}")
    print("Run assemble.py to combine them into a single session document.")


if __name__ == "__main__":
    main()