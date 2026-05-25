# Session Doc Decomposition — Design

Status: **proposal** (branch `refactor/session-doc-decomposition`)
Replaces: `session_doc.py` monolith
Adjacent docs: [TheFlow.md](TheFlow.md), [session_doc_pipeline.md](../cli/session_doc_pipeline.md), [architecture.md](../core/architecture.md)

## 1. Why

`session_doc.py` was originally a single end-to-end "recap → narrative document" pipeline. Over time the team learned that scope, ordering, and attribution decisions need a human checkpoint between every LLM call (see the global rule in `~/.claude/CLAUDE.md` and the design principle in [TheFlow.md §"The design principle"](TheFlow.md)). So we grew per-stage entry/exit points — `--from-extractions`, `--scene-extractions`, `--plan-file`, `--extract-only`, `--per-scene-output`, `--narrator`, `--scene N` — without splitting the script.

The result today (concretely):

- **1,986 lines, one `main()`** that fan-outs through ~8 mode flags. Every pass is preceded by a "did the previous stage already run on disk?" cascade (e.g. `session_doc.py:1488-1545` for Pass 1, `:1574-1583` for Pass 3, `:1767-1804` for Pass 4).
- **~900 lines of prompt text inlined as Python string constants** (`CONSISTENCY_SYSTEM`, `ENHANCE_SYSTEM`, `PLAN_SYSTEM`, `PLAN_SCENE_SYSTEM`, `CHAR_EXTRACT_SYSTEM`, `NARRATE_SYSTEM_BASE`, `EXAMPLES_BLOCK`, `PER_CHAR_EXAMPLES_BLOCK`, `VOICE_SPEC_BLOCK`, `PREV_VOICE_CONTRAST_BLOCK`, `DIALOGUE_INSTRUCTION_FULL`, `DIALOGUE_INSTRUCTION_CONDITIONAL`, `PROSE_MODE_INSTRUCTION`, `SCENE_ANCHORED_DIRECTIVE`). Editing a prompt requires editing Python.
- **Two conflicting mental models in one file.** Docstring still describes "five passes" as a single end-to-end run. [`docs/cli/session_doc_pipeline.md`](../cli/session_doc_pipeline.md) describes the same code as part of a four-stage human-gated pipeline. Both are true, depending on which flags you set.
- **Disk is already the contract — code doesn't trust it.** `consistency_report.md`, `enhanced_sections.md`, `plan.md`, `extractions/NN_*.md`, `scene_extractions/NN_*.md`, `narration/session_doc_scene_NN_*.md` are all already written and re-read by the same script across runs. The boundary between stages already exists on disk; the script just doesn't honour it at the entry point.

This refactor brings the code in line with how the system is actually used: **a series of discrete, independently-invokable stages, each with disk-resident inputs and outputs, each with its own prompts kept outside Python.**

## 2. Goals and non-goals

### Goals

- **One tool per LLM "pass."** Each tool reads its inputs from disk, makes one LLM call (or a tight per-scene loop of one call each), writes its output to disk, exits. No internal "did stage N-1 run?" branching.
- **All prompts externalised to versioned markdown files** under `config/agents/`, loaded through a shared helper. The pattern applies to **all CLI scripts in the repo**, not just `session_doc.py`.
- **The on-disk session directory is the explicit interface** between tools. Adding a new entry point or a new variant becomes "write a tool that reads X and writes Y," not "add a new flag and a new conditional."
- **The web UI's per-stage gates become real.** Each FastAPI endpoint shells out to exactly one CLI tool — matching the "CLI ↔ UI symmetry" principle in [architecture.md](../core/architecture.md#recurring-concepts-read-once-recognize-forever).
- **`main` remains usable throughout the refactor.** All work happens on the `refactor/session-doc-decomposition` branch.

### Non-goals

- **No behaviour changes.** Same prompts, same model defaults, same on-disk artefacts. A user running the new tools in sequence should get the same `session_doc.md` they get today from the four-stage flow.
- **No retrieval/render reshuffling.** The proposal-gate (`proposal_loader.require_approved_proposal`) and the retrieval-render isolation test stay exactly as they are. The new tools all sit inside the "render" half of the wall.
- **No new pipeline modes.** Chunk mode (`--by-scene` off), `--from-extractions`, `--narrator` single-character mode, `--plan-only`, `--extract-only`, `--reflections`, `--prose-mode`, `--narration-genre`, DGX endpoint routing — *the modes that are part of the current scene-anchored four-stage workflow survive*; modes that only existed to support the old single-shot run get retired (see §6). No new ones.
- **No prompt rewrites.** Prompts move from `.py` string constants to `.md` files byte-for-byte. Wording changes are a separate task.
- **`narrative.py`, `assemble.py`, `enhance_summary.py`, `scene_extract.py` are out of scope** for splitting. They are already discrete tools; they just need their prompts externalised (Phase 3).

## 3. Target tool boundaries

The four-stage post-session pipeline today:

```
gm-assist.md ──► enhance_summary.py ──► session-summary.md
session-summary.md + VTT ──► scene_extract.py ──► scene_extractions/NN_*.md
session-summary.md + scene_extractions/ ──► session_doc.py ──► narration/NN_*.md
narration/ ──► assemble.py ──► session_doc.md
```

`session_doc.py` is the bundled stage. We split it into five tools, one per LLM call:

| New CLI | Inputs (from disk) | Output (to disk) | Replaces in `session_doc.py` |
|---|---|---|---|
| `sd_consistency.py` | `session-summary.md` (or recap) + context docs | `consistency_report.md` | Pass 1 (`:1486-1530`) |
| `sd_enhance.py` | recap + roleplay/summary extractions + `consistency_report.md` (+ party) | `enhanced_sections.md` | Pass 2 (`:1532-1571`) |
| `sd_plan.py` | `scene_extractions/` (or recap `## Scenes`) + party + character roster | `plan.md` | Pass 3 (`:1573-1640`) |
| `sd_extract.py` | `plan.md` + roleplay extractions + recap | `extractions/NN_*.md` | Pass 4, chunk mode only (`:1805-1845`) |
| `sd_narrate.py` | `plan.md` + `scene_extractions/` or `extractions/` + voice/ + examples/ | `narration/NN_*.md` | Pass 5 (`:1857-1937`) |
| `assemble.py` *(exists)* | `narration/*.md` | `session_doc.md` | Stage 4 / final assembly (`:1949-1966`) |

Each tool: ~150-250 lines. Single LLM call (or a per-scene loop calling once per scene). No "skip if previous stage already ran" branches — if a required input file isn't on disk, the tool errors with a one-line "run `sd_<previous>.py` first".

### Why split this way and not differently

- **One tool ≡ one LLM call ≡ one human checkpoint.** This is the unit of review the [LLM Pipeline Design Rule](../../CLAUDE.md) (global) cares about. Anything finer (split a pass into pre-prompt assembly + LLM call + post-processing) is over-decomposition; anything coarser (e.g. fold consistency + enhance into one script) bundles two LLM calls behind one entry point and recreates the original problem.
- **`sd_extract.py` is its own tool even though Pass 4 is skipped in the dominant (scene-anchored) workflow.** Chunk mode is a legacy path we expect to retire (§6), but until it's actually deleted, keeping it isolated in one file makes deletion a `git rm`.
- **The per-scene narration loop stays inside `sd_narrate.py`** rather than becoming `sd_narrate_scene.py` invoked N times. The loop has tight prompt-cache and handoff continuity (`session_doc.py:1919`, `:1861-1872`) that benefits from a single process. `--scene N [M …]` filtering moves with the tool.

### Module layout

```
session_doc/                  # package; absorbs session_doc.py's helpers
  __init__.py
  io.py                       # load_scene_extractions, load_extractions, parse_plan,
                              # extract_scene_text, extract_section_text, etc.
  roster.py                   # extract_character_roster
  voice.py                    # load_voice_files, get_voice_note, get_char_examples,
                              # extract_contrast_sample
  prompts.py                  # tiny loader: load_agent_prompt(name) -> str
  narrate_prompt.py           # build_narrate_system, build_narrate_prompt,
                              # estimate_narration_tokens, parse_extraction_file
sd_consistency.py             # CLI shim, ~80 lines
sd_enhance.py
sd_plan.py
sd_extract.py
sd_narrate.py
```

`narrative.py` and `assemble.py` are not moved — they are already discrete tools, and `narrative.py` is consumed by other paths.

## 4. Prompt-externalization pattern (project-wide)

The pattern already exists for `prep.py` agents (`config/agents/{lore_oracle,encounter_architect,voice_keeper}.md`, loaded via `load_file(config["agents"][key], base_dir)` at [`prep.py:128`](../../prep.py)). We generalise it.

### Layout

```
config/
  agents/
    lore_oracle.md                # existing
    encounter_architect.md        # existing
    voice_keeper.md               # existing
    session_doc/
      consistency.md              # was CONSISTENCY_SYSTEM
      enhance_sections.md         # was ENHANCE_SYSTEM
      plan_chunk.md               # was PLAN_SYSTEM
      plan_scene.md               # was PLAN_SCENE_SYSTEM
      char_extract.md             # was CHAR_EXTRACT_SYSTEM (with {narrator}/{scene_block} placeholders)
      narrate/
        base.md                   # NARRATE_SYSTEM_BASE template
        dialogue_full.md          # DIALOGUE_INSTRUCTION_FULL
        dialogue_conditional.md   # DIALOGUE_INSTRUCTION_CONDITIONAL
        prose_mode.md             # PROSE_MODE_INSTRUCTION (the long one)
        scene_anchored.md         # SCENE_ANCHORED_DIRECTIVE
        examples_block.md         # EXAMPLES_BLOCK template
        per_char_examples.md      # PER_CHAR_EXAMPLES_BLOCK template
        voice_spec.md             # VOICE_SPEC_BLOCK template
        prev_voice_contrast.md    # PREV_VOICE_CONTRAST_BLOCK template
    enhance_summary.md            # was ENHANCE_SYSTEM_PREFIX in enhance_summary.py
    scene_extract.md              # was SCENE_EXTRACT_SYSTEM_PREFIX in scene_extract.py
    distill.md                    # was DISTILL prompts
    planning_extract.md
    planning_synthesize.md
    party.md
    campaign_state.md
    ...                           # one file per LLM call across the repo
```

### Loader

A new helper in `campaignlib.py` (single source of truth, per [Critical Rules](../../CLAUDE.md#critical-rules-apply-to-every-task)):

```python
def load_agent_prompt(name: str, base_dir: Path | None = None,
                      placeholders: dict[str, str] | None = None) -> str:
    """Load a prompt template from config/agents/<name>.md (or a campaign override).

    Resolution order:
      1. <cwd>/config/agents/<name>.md   (campaign override)
      2. <repo>/config/agents/<name>.md  (default)

    Placeholder substitution is opt-in and strict: every {key} in the template
    must appear in `placeholders`; every key in `placeholders` must appear in
    the template. Mismatch raises ValueError so prompt drift surfaces loudly.
    """
```

- The same resolution rule as `find_default_config()`: CWD wins so a campaign can ship its own prose-mode wording without forking the repo.
- Substitution is **strict on both sides** — if the template lacks a `{placeholder}` the loader is being asked to fill, that's a bug; if the template has a placeholder the caller forgot, that's also a bug. We want both to fail at load time, not silently produce a malformed prompt.
- The loader is cached per-path so repeated calls inside one process don't hit disk again.
- **No new templating language.** Plain `str.format`-style `{name}` placeholders. Avoid Jinja, j2, etc. — prompts should be readable to a non-engineer (a player editing voice files; a GM editing `prose_mode.md`).

### Composition

Today's `build_narrate_system()` concatenates ~6 sub-blocks based on flags. After the refactor:

```python
parts = [load_agent_prompt("session_doc/narrate/base", base_dir, placeholders={...})]
if examples_text:
    parts.append(load_agent_prompt("session_doc/narrate/examples_block",
                                    placeholders={"examples": examples_text}))
if scene_anchored:
    parts.append(load_agent_prompt("session_doc/narrate/scene_anchored",
                                    placeholders={"narrator": narrator}))
if prose_mode:
    parts.append(load_agent_prompt("session_doc/narrate/prose_mode"))
# ...
return "\n\n".join(parts)
```

The composition logic stays in Python (it's branchy and depends on per-call flags). What moves out is the prose.

### Why project-wide and not just session_doc

You picked the broader scope, and it's the right call:

- The same problem exists in `enhance_summary.py` (`ENHANCE_SYSTEM_PREFIX`), `scene_extract.py` (`SCENE_EXTRACT_SYSTEM_PREFIX`), `distill.py`, `planning.py`, `party.py`, `campaign_state.py`, etc.
- Doing it once across the repo means `config.yaml` becomes the authoritative inventory of "every LLM call this codebase makes" — useful for prompt audits, useful for per-campaign customisation, useful for the eventual "show me the prompt that produced this output" UI feature.
- The loader needs to exist anyway for `session_doc/`; the marginal cost of converting the other scripts is small and the marginal cost of *not* converting them is permanent inconsistency.

Phase 3 of the migration handles the non-`session_doc` scripts.

## 5. On-disk contract

Each session directory becomes the source of truth for "what stages have run." The layout we already partially produce, made explicit:

```
summaries/<session>/
  gm-assist.md                       # input — human-authored or from gm-assist
  session.vtt                        # input — Zoom transcript
  session-summary.md                 # ← enhance_summary.py  (Stage 1)
  consistency_report.md              # ← sd_consistency.py
  enhanced_sections.md               # ← sd_enhance.py
  scene_extractions/
    NN_<slug>.md                     # ← scene_extract.py    (Stage 2)
    NN_<slug>.scaffold.md            # ← human-edited override (Editor UI)
  plan.md                            # ← sd_plan.py
  extractions/                       # only in chunk mode (legacy path; retire)
    NN_<narrator>_<scene>.md         # ← sd_extract.py
  narration/
    session_doc_scene_NN_<slug>.md   # ← sd_narrate.py
  session_doc.md                     # ← assemble.py         (Stage 4)
  logs/
    <timestamp>_<tool>.md            # ← save_log per tool
```

Two rules:

1. **A tool reads what it needs and errors if missing.** No "skip if not present" fallbacks. `sd_narrate.py` requires `plan.md` and either `scene_extractions/` or `extractions/`. If neither exists, it tells the user which tool to run. No hidden retry-as-something-else logic.
2. **A tool writes its own output and only its own output.** `sd_consistency.py` writes `consistency_report.md` and nothing else. The current pattern where Pass 1 sometimes writes `consistency_report.md` *and* Pass 2 folds it into `enhanced_sections.md` goes away — Pass 2 reads the report from disk like every other downstream consumer.

This is also the boundary the FastAPI router/UI sees. `server/routers/scene_editor.py` already has SSE endpoints per stage; they shed their `session_doc.py`-flag knowledge and become "shell out to one tool, stream stdout." The Session Doc Editor's stage gauges become real — "consistency_report.md exists?" is one stat call.

## 6. Migration phases

Each phase is a self-contained PR on the `refactor/session-doc-decomposition` branch. The branch stays open until all phases land; `main` is untouched.

### Phase 1 — Establish the prompt loader

- Add `load_agent_prompt()` to `campaignlib.py` with the resolution/substitution/caching semantics above.
- Add `tests/test_agent_prompt_loader.py` covering: CWD override, missing placeholder = ValueError, extra placeholder = ValueError, cache reuse, missing file = clear error message.
- **No existing scripts change.** This phase is purely additive — the loader sits next to the inline constants until Phase 2/3 swap them in.

### Phase 2 — Extract `session_doc.py` prompts

- Create `config/agents/session_doc/` with all 14 prompt files. Move text **byte-for-byte** from the constants. Add the placeholder set each template uses to its file header (a comment block) so they're self-documenting.
- Replace each constant with a `load_agent_prompt(...)` call. The diff is per-call, not per-prompt — keeps the test surface small.
- Run the existing `tests/test_session_doc*.py` suite. Add a snapshot test that calls `build_narrate_system(...)` with the full matrix of flag combinations (genre, prose_mode, scene_anchored, examples, voice_note, prev_voice_sample) and compares against a frozen golden file. The golden file is generated *before* the refactor on `main` to lock current behaviour.
- **Behaviour-preserving.** No prompt text changes. Same `session_doc.py` shape; just thinner.

### Phase 3 — Extract remaining script prompts

- One PR per script: `enhance_summary.py`, `scene_extract.py`, `distill.py`, `planning.py`, `party.py`, `campaign_state.py`, `narrative.py`, `polish.py`. Each PR is mechanical: move the system prompt(s) to `config/agents/<name>.md`, swap to `load_agent_prompt`.
- Each PR adds the snapshot test for *its* script's prompt assembly.
- After this phase, `config/agents/` is the inventory of every prompt the repo ships.

### Phase 4 — Split `session_doc.py` into discrete tools

Order is bottom-up so each tool can be exercised end-to-end as it lands:

1. **Carve helpers into `session_doc/` package** (`io.py`, `roster.py`, `voice.py`, `narrate_prompt.py`). `session_doc.py` keeps working — it just imports from the package.
2. **`sd_narrate.py`** (Pass 5, the most-iterated tool — earliest payoff). Today's `--from-extractions DIR --plan-file ...` mode collapses into `sd_narrate.py <session>`. Keep `--scene N [M …]` filtering. UI endpoint switches over once the CLI is green.
3. **`sd_plan.py`** (Pass 3). `--plan-only` is no longer a flag — it's "just run `sd_plan.py`."
4. **`sd_enhance.py`** (Pass 2).
5. **`sd_consistency.py`** (Pass 1).
6. **`sd_extract.py`** (Pass 4, chunk-mode-only). Lands last because we expect to delete it in Phase 5.
7. **Update `server/routers/scene_editor.py`** to call each new tool directly. The router's `_build_session_doc_cmd()` collapses into per-stage builders.
8. **Update `docs/cli/session_doc_pipeline.md`** to describe the new tools. Rewrite the "All flags" table (most flags are gone — they were just "skip to stage N" knobs that no longer have anything to skip).

### Phase 5 — Retire legacy modes and delete `session_doc.py`

You picked "hard cut to new tools." This phase makes that real.

Modes that go away (these only exist because everything was in one script):

- `--from-extractions` — was "skip passes 1-4." Use `sd_narrate.py` directly.
- `--extract-only` — was "stop after pass 4." Use `sd_extract.py` directly.
- `--plan-only` — was "stop after pass 3." Use `sd_plan.py` directly.
- `--enhanced-sections` — was "skip pass 2." Default behaviour (read `enhanced_sections.md` from disk).
- `--per-scene-output` — was "Stage 3 mode." It's the only mode `sd_narrate.py` has.
- `--narrator NAME` — was "single-character chunk mode." Drops with chunk mode.
- The whole **chunk-mode code path**: `PLAN_SYSTEM` (vs. `PLAN_SCENE_SYSTEM`), `DIALOGUE_INSTRUCTION_FULL` (vs. `_CONDITIONAL`), `sd_extract.py`, and the chunk branches of `build_char_extract_prompt()` and `build_narrate_system()`. Scene-anchored is the only supported flow, matching [TheFlow.md Phase E-G](TheFlow.md).

Modes that survive: `--prose-mode`, `--narration-genre`, `--reflections`, DGX endpoint flags, `--dry-run`, `--verbose`, `--model`, `--fast`. They all live on `sd_narrate.py` (or whichever single tool they affect).

Final step: `git rm session_doc.py`. Update `docs/cli/session_doc_pipeline.md`, `docs/core/architecture.md`, `docs/cli/cli_tools.md`, `CLAUDE.md` to point at the new tools. Merge `refactor/session-doc-decomposition` to `main`.

## 7. Tradeoffs and open questions

### Worth being honest about

- **More files to navigate.** "Where's that prompt?" goes from "ctrl-F in `session_doc.py`" to "find the right file in `config/agents/`." We mitigate with a 1-line index comment at the top of `config/agents/session_doc/README.md` mapping every prompt to the tool that loads it. Net win because today the prompts are tangled with control flow; after the split each is a standalone artefact.
- **Multi-process overhead for the four-stage flow.** Each tool is a fresh Python interpreter — ~200 ms startup × 5 stages = ~1 s of overhead per full run. Negligible against per-call LLM latency (multi-second to minutes). A `Makefile` or shell wrapper at the workspace level can chain them for users who want one command.
- **Snapshot tests are fragile to prompt edits.** Anyone editing a `config/agents/*.md` will trip the snapshot. That's the *point* for Phase 2 (we want to verify byte-for-byte equivalence) and a known cost afterwards (snapshot updates become part of a prompt-edit PR). Add a `--update-snapshots` pytest flag like other projects do.
- **Per-campaign prompt overrides are now possible — and that's a footgun.** A campaign that ships a half-edited `config/agents/session_doc/narrate/prose_mode.md` will silently get different narration. Mitigation: log "loaded prompt from <path>" on every `load_agent_prompt()` call when `--verbose` is set, so the prompt provenance is visible in the saved log.

### Decisions

1. **Loader lives in `campaignlib.py`.** Consistent with the "single API surface" rule in [CLAUDE.md](../../CLAUDE.md#critical-rules-apply-to-every-task).
2. **Snapshot golden files: `tests/golden/prompts/`.** Self-documenting that they're frozen baselines, not freeform fixtures.
3. **The `config.yaml` `agents:` indirection is deprecated.** All scripts read from `config/agents/<name>.md` directly via `load_agent_prompt(name)`. Per-campaign overrides still work through CWD-first resolution. `prep.py`'s current `config["agents"][key]` dict lookups go away; `config/README.md` updates to drop the `agents:` block. No backwards-compatibility shim — this is a hard cut, in line with the Phase 5 deletion strategy. Simpler loader, simpler config, one resolution rule.

4. **No in-flight session migration.** The merge window will be timed against the user's own session cadence — Phase 5 lands only when there is no half-processed session in `summaries/`. No code is written to bridge old-flow artefacts into the new tools; if a session got partway through `session_doc.py` on `main`, it finishes on `main` before the cut. Removes a whole class of "what if `enhanced_sections.md` was written by the old script and the new script expects a different shape" edge cases.

## 8. Out of scope (deliberately)

Tracked here so reviewers don't suggest folding them in:

- The web UI's stage-gates and prompt-inspection features (a worthy follow-up, but separate work).
- Retiring `prep.py`'s "single mode" vs. "pipeline mode" distinction (different problem, different file).
- Anything in the RLM pipeline (`rpg_retriever.py`, `dossier_proposer.py`, `proposal_loader.py`). The proposal-gate is downstream-invariant of this refactor.
- Moving prompts to YAML or some structured format. Markdown is what humans (players, GMs) edit; keep it.
- A "skip to stage N" master CLI. The five tools are the master CLI. If chaining is annoying enough, a shell script in `bin/` is the right answer, not new code.

---

**Next steps if approved:** Phase 1 PR (loader + tests). Each subsequent phase its own PR on this branch. No merge to `main` until Phase 5 is green and `docs/cli/session_doc_pipeline.md` is rewritten.
