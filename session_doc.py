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

# ── Re-exports for backwards compatibility ────────────────────────────────────
# Phase 4 carved the helpers into flat session_doc_* modules. Existing imports
# (`from session_doc import extract_character_roster`, etc.) keep working.

from session_doc_roster import extract_character_roster
from session_doc_voice import (
    extract_contrast_sample,
    get_voice_note,
    load_voice_files,
)
from session_doc_examples import get_char_examples
from session_doc_io import (
    _SCENE_FRONTMATTER_RE,
    _split_scene_body,
    extract_scene_text,
    format_extractions,
    load_extractions,
    load_scene_extractions,
    parse_plan,
)
from session_doc_narrate import (
    DIALOGUE_INSTRUCTION_CONDITIONAL,
    DIALOGUE_INSTRUCTION_FULL,
    EXAMPLES_BLOCK,
    NARRATE_SYSTEM_BASE,
    PER_CHAR_EXAMPLES_BLOCK,
    PREV_VOICE_CONTRAST_BLOCK,
    PROSE_MODE_INSTRUCTION,
    SCENE_ANCHORED_DIRECTIVE,
    VOICE_SPEC_BLOCK,
    build_narrate_prompt,
    build_narrate_system,
    estimate_narration_tokens,
)




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