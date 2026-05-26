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

**Update (post PRs #51-#53):** Since this doc was written, three merged PRs have already collapsed the symptoms above by roughly a third — `session_doc.py` is now 1642 lines, chunk mode and `--output` one-shot assembly are gone, and the `Inputs` dataclass + `load_inputs` + `narrate_section` extractions have imposed structure on the `main()` body. What's left to fix is narrower: ~700 lines of prompt prose still live as Python string constants, **three** live LLM calls (consistency / plan / narrate) are still bundled behind one CLI entry point, and the bundled flow re-derives state at each pass instead of re-loading it from the on-disk artefacts it just wrote. The phases below target this residual scope, not the original five-tool decomposition.

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
- **No new pipeline modes.** Most of the legacy modes the original draft proposed to retire are already gone in PRs #51-#53 (chunk mode, `--by-scene`, `--from-extractions`, `--roleplay-extract-dir`, `--output` one-shot assembly). What survives across the surviving scene-anchored flow (`--narrator`, `--scene N`, `--prose-mode`, `--narration-genre`, `--reflections`, DGX endpoint routing) is documented in §6 Phase 5. No new modes are introduced.
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

`session_doc.py` is the bundled stage. On post-#53 `main` only three live LLM calls remain inside it. We split into three tools, one per call:

| New CLI | Inputs (from disk) | Output (to disk) | Source on `main` |
|---|---|---|---|
| `sd_consistency.py` | recap + `--context` files (+ optional `--session-summary`) | `consistency_report.md` | Pass 1 block in `session_doc.py:1428-1467` |
| `sd_plan.py` | `scene_extractions/` + party + character roster (+ optional `--session-summary`) | `plan.md` | Pass 3 block in `session_doc.py:1469-1525` |
| `sd_narrate.py` | `plan.md` + `scene_extractions/` + voice/ + examples/ | `narration/NN_*.md` | `narrate_section()` loop in `session_doc.py:1148-1280` |
| `assemble.py` *(exists)* | `narration/*.md` | `session_doc.md` | unchanged — Stage 4 already external |

Each tool: ~80-200 lines. Single LLM call (or, for narrate, a per-scene loop calling once per scene). No "skip if previous stage already ran" branches — if a required input file isn't on disk, the tool errors with a one-line "run `sd_<previous>.py` first".

The original draft proposed five tools. Two of those (`sd_enhance.py`, `sd_extract.py`) are gone here:
- **`sd_enhance.py` was for Pass 2 (`ENHANCE_SYSTEM`).** On `main`, `ENHANCE_SYSTEM` has no API call site, `--enhanced-sections` has no producer anywhere in the repo, and the consumer code inside `narrate_section` reads a file that is never written. All of it is deleted in Phase 0 before the tool split happens.
- **`sd_extract.py` was for Pass 4 chunk-mode character extraction.** PR #52 deleted `CHAR_EXTRACT_SYSTEM`, `build_char_extract_prompt`, `parse_extraction_file`, `extract_section_text`, and `extraction_filename`. There is no LLM call left to wrap.

### Why split this way and not differently

- **One tool ≡ one LLM call ≡ one human checkpoint.** This is the unit of review the [LLM Pipeline Design Rule](../../CLAUDE.md) (global) cares about. Anything finer (split a pass into pre-prompt assembly + LLM call + post-processing) is over-decomposition; anything coarser (e.g. fold consistency + plan into one script) bundles two LLM calls behind one entry point and recreates the original problem.
- **The per-scene narration loop stays inside `sd_narrate.py`** rather than becoming `sd_narrate_scene.py` invoked N times. The loop has tight prompt-cache and handoff continuity (`narrate_section()` at `session_doc.py:1148` carries `handoff` and `plan_ctx.plan_narrator_by_scene` across scenes) that benefits from a single process. `--scene N [M …]` and `--narrator NAME` filtering both move with the tool.
- **Consistency runs once, not per-narrate-call.** Today the editor passes `--context` to both `_build_plan_cmd` and `_build_narrate_cmd`, so Pass 1 fires twice per cycle. After the split, `sd_consistency.py` runs once before `sd_plan.py`; `sd_narrate.py` does not accept `--context`.

### Module layout

```
session_doc/                  # package; absorbs session_doc.py's helpers
  __init__.py
  io.py                       # load_scene_extractions, parse_plan,
                              # extract_scene_text, _SCENE_FRONTMATTER_RE,
                              # _split_scene_body
  roster.py                   # extract_character_roster
  voice.py                    # load_voice_files, get_voice_note,
                              # extract_contrast_sample
  examples.py                 # per-character examples discovery & routing
                              # (separate from voice.py — different concerns
                              # that were entangled in the monolith)
  prompts.py                  # load_agent_prompt re-export / cache shim
  narrate_prompt.py           # build_narrate_system, build_narrate_prompt,
                              # estimate_narration_tokens
sd_consistency.py             # CLI shim, ~80 lines
sd_plan.py                    # CLI shim, ~120 lines
sd_narrate.py                 # CLI shim, ~200 lines (carries the per-scene loop)
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
      plan.md                     # was PLAN_SYSTEM (only the scene-anchored one survives)
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

Eleven session_doc prompts, down from the original draft's fourteen. Three dropped:
- **`enhance_sections.md`** — `ENHANCE_SYSTEM` is dead code and gets deleted in Phase 0.
- **`plan_chunk.md`** — chunk-mode `PLAN_SYSTEM` was deleted in PR #52; only the scene-anchored variant survives.
- **`char_extract.md`** — `CHAR_EXTRACT_SYSTEM` was deleted in PR #52.

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
  scene_extractions/
    NN_<slug>.md                     # ← scene_extract.py    (Stage 2)
    NN_<slug>.scaffold.md            # ← human-edited override (Editor UI)
  plan.md                            # ← sd_plan.py
  narration/
    session_doc_scene_NN_<slug>.md   # ← sd_narrate.py
  session_doc.md                     # ← assemble.py         (Stage 4)
  logs/
    <timestamp>_<tool>.md            # ← save_log per tool
```

The original draft listed two more artefacts that are dropped here:
- **`enhanced_sections.md`** had no producer anywhere in the repo on `main`. The flag (`--enhanced-sections FILE`), the `Inputs.enhanced_sections` field, and the consumer inside `narrate_section` are all deleted in Phase 0 — *before* any tool split happens — so Phase 4 doesn't inherit a phantom dependency.
- **`extractions/`** was the chunk-mode artefact written by the old Pass 4. PR #52 deleted that code path; the directory will not exist in any session produced under post-#53 flow.

Two rules:

1. **A tool reads what it needs and errors if missing.** No "skip if not present" fallbacks. `sd_narrate.py` requires `plan.md` and `scene_extractions/`. If either is missing, it tells the user which tool to run. No hidden retry-as-something-else logic.
2. **A tool writes its own output and only its own output.** `sd_consistency.py` writes `consistency_report.md` and nothing else; today the bundled flow writes it as a side effect of the `--context` path inside `session_doc.py`, which makes its provenance unclear. Lifting it into its own tool makes the file authoritative.

This is also the boundary the FastAPI router/UI sees. `server/routers/scene_editor.py` already has SSE endpoints per stage; they shed their `session_doc.py`-flag knowledge and become "shell out to one tool, stream stdout." The Session Doc Editor's stage gauges become real — "consistency_report.md exists?" is one stat call.

## 6. Migration phases

Each phase is a self-contained PR on the `refactor/session-doc-decomposition` branch. The branch stays open until all phases land; `main` is untouched.

### Phase 0 — Dead-code sweep + Vue cleanup (NEW)

Two coordinated PRs land before any structural work:

- **0a (Python).** Delete from `session_doc.py`: the `ENHANCE_SYSTEM` constant (~30 lines around `:85`), the `--enhanced-sections` argparse declaration (~`:1383`), the `Inputs.enhanced_sections` field (~`:917`), the read block at `~:1097-1104`, the call `extract_scene_text(inputs.enhanced_sections, …)` inside `narrate_section` (~`:1217`), and the misleading "dead code and will be deleted in a follow-up" comment at `:1428` (which describes a block that is *not* dead — see §3). Update any test in `tests/` that mentions the flag.
- **0b (Vue / router).** Coordinated removal of every reference to `--enhanced-sections` / `enhanced_sections.md` / `use_enhanced_sections` across `server/routers/scene_editor.py` (the auto-attach block, `_done` callbacks that enumerate it as an expected output, pipeline-status stat by filename, and the `api_get_enhanced_sections` endpoint if present); `server/config_models.py` plus the typed-config key map (if `use_enhanced_sections` lives there); and the Vue surface (`frontend/src/views/session/SessionDocEditor.vue`, `frontend/src/views/session/ReviewAssemble.vue`, `frontend/src/components/scene-editor/ExtractionEditor.vue`). **Implementation prerequisite:** re-grep `main` for these strings before scoping the PR — the exploratory count came from this branch's pre-#52/#53 state and the actual post-#53 footprint is likely smaller but non-zero.

After Phase 0, `session_doc.py` contains only the three live LLM calls (consistency / plan / narrate). Phase 2's prompt-externalization scope is honest.

### Phase 1 — Establish the prompt loader

- Add `load_agent_prompt()` to `campaignlib.py` with the resolution/substitution/caching semantics above.
- Add `tests/test_agent_prompt_loader.py` covering: CWD override, missing placeholder = ValueError, extra placeholder = ValueError, cache reuse, missing file = clear error message.
- **No existing scripts change.** This phase is purely additive — the loader sits next to the inline constants until Phase 2/3 swap them in.

### Phase 2 — Extract `session_doc.py` prompts

- Create `config/agents/session_doc/` with **11** prompt files (see §4 — the original draft's `enhance_sections.md` / `plan_chunk.md` / `char_extract.md` are not created; their source constants are either gone after Phase 0 or were deleted in PR #52). Move text **byte-for-byte** from the constants. Add the placeholder set each template uses to its file header (a comment block) so they're self-documenting.
- Replace each constant with a `load_agent_prompt(...)` call. The diff is per-call, not per-prompt — keeps the test surface small.
- Run the existing `tests/test_session_doc*.py` suite. Add a snapshot test that calls `build_narrate_system(...)` with the full matrix of flag combinations (genre, prose_mode, scene_anchored, examples, voice_note, prev_voice_sample) and compares against a frozen golden file in `tests/golden/prompts/`. The golden file is generated on `main` **immediately before Phase 0 lands** so it captures the current behaviour without the dead Pass-2 prompt polluting the matrix.
- **Behaviour-preserving.** No prompt text changes. Same `session_doc.py` shape; just thinner.

### Phase 3 — Extract remaining script prompts

- One PR per script: `enhance_summary.py`, `scene_extract.py`, `distill.py`, `planning.py`, `party.py`, `campaign_state.py`, `narrative.py`, `polish.py`. Each PR is mechanical: move the system prompt(s) to `config/agents/<name>.md`, swap to `load_agent_prompt`. Each PR adds a snapshot test for its script's prompt assembly.
- **Two sharp edges to call out in the PR descriptions:**
    - `enhance_recap.py` and the (now-deleted-in-Phase-0) `session_doc.py:ENHANCE_SYSTEM` are **two different prompts with the same constant name**. Phase 0 deletes the session_doc copy; Phase 3 externalizes the enhance_recap copy. Do not consolidate.
    - `narrative.py` has its own copies of `PLAN_SYSTEM`, `PLAN_SCENE_SYSTEM`, `CHAR_EXTRACT_SYSTEM`, `NARRATE_SYSTEM_BASE`, and `EXAMPLES_BLOCK` that **have diverged** from session_doc.py's deleted versions. Externalize side-by-side; flag any dedupe as a separate follow-up, not part of this phase.
- Recommended order: session_doc prompts (Phase 2) first → other scripts in any order → `narrative.py` last (the divergence audit benefits from seeing the session_doc files in their final shape).
- After this phase, `config/agents/` is the inventory of every prompt the repo ships.

### Phase 4 — Split `session_doc.py` into discrete tools

Order is bottom-up so each tool can be exercised end-to-end as it lands:

1. **Carve helpers into `session_doc/` package** (`io.py`, `roster.py`, `voice.py`, `examples.py`, `narrate_prompt.py`, `prompts.py`). `session_doc.py` keeps working — it just imports from the package.
2. **`sd_narrate.py`** (Pass 5, the most-iterated tool — earliest payoff). Keep the per-scene loop, the `--scene N [M …]` filter, and the `--narrator NAME` plan filter. UI endpoint switches over once the CLI is green.
3. **`sd_plan.py`** (Pass 3). `--plan-only` is no longer a flag — it's "just run `sd_plan.py`."
4. **`sd_consistency.py`** (Pass 1). Tiny tool — reads recap + `--context` + optional `--session-summary`, writes `consistency_report.md`, exits.

### Phase 5 — Retire residual modes, rewire editor, delete `session_doc.py`

Most legacy modes are gone already (chunk mode in #52, `--output` in #53). What survives this phase:

**Editor router rewiring** (`server/routers/scene_editor.py`):
- New `_build_consistency_cmd` → `sd_consistency.py`.
- `_build_plan_cmd` → `sd_plan.py`. **Drop `--context` from this builder** — consistency is now its own explicit stage.
- `_build_narrate_cmd` → `sd_narrate.py`. **Drop `--context` here too** — no double consistency run.
- **Open UI question** (intentionally not pre-decided here): does the editor invoke consistency automatically before plan, or expose it as a user-triggered button next to the existing stage gauges? Either is defensible; the question belongs to the UI PR, not this design doc.
- `_llm_env()` currently plumbs DGX env vars only through narrate+scrub. Document the env contract so `sd_plan.py` and `sd_consistency.py` either inherit it consistently or explicitly opt out.
- Snapshot-test the new per-stage `_build_*_cmd` outputs.

**Modes that disappear** (they existed only as "skip to stage N" knobs inside the bundled flow):
- `--plan-file` — `sd_narrate.py` always reads `plan.md` from disk.
- `--plan-only`, `--extract-only` — replaced by "just run the tool you want."
- `--no-plan-review`, `--extract-dir` — bundled-flow checkpoints; no longer meaningful.

**Modes that survive** (migrate to the tool that uses them):
- `sd_narrate.py`: `--narrator`, `--scene`, `--prose-mode`, `--narration-genre`, `--reflections`, `--dgx-endpoint`, `--dgx-model`, `--narrate-tokens`, `--dry-run`, `--verbose`, `--model`, `--fast`.
- `sd_plan.py`: `--session-name`, `--session-summary`, `--characters`, `--party`, `--summary-extract-dir`, `--dossier-dir`, `--require-proposal`, plus the universal `--verbose`/`--model`/`--fast`.
- `sd_consistency.py`: `--context`, `--session-summary`, plus the universal flags.

**Doc sweep** — list explicitly so the implementer doesn't miss any. (Verified by `grep -rln 'session_doc\.py\|enhanced_sections' docs/`.)
- `CLAUDE.md` (project rules table)
- `docs/core/architecture.md` (pipeline diagram, Layer-4 CLI table)
- `docs/cli/cli_tools.md`
- `docs/cli/session_doc_pipeline.md` — largest rewrite; the entire flag table changes and the "Passes" table becomes per-tool
- `docs/cli/post_session.md` — Stage-3 diagram + example invocations
- `docs/cli/session_prep_workflow.md` — two passing references to update
- `docs/web/session_doc_editor.md` — UI workflow; has a directory tree referencing `enhanced_sections.md`
- `docs/web/web_ui.md` — describes Scene Extraction as "passes 1–4" and the Narrate workflow as `session_doc.py --from-extractions --scene N` (both stale)
- `docs/specs/formats.md` — currently lists `session_doc.py` as producer/consumer in several entries
- `docs/archive/*` — left as-is by design (archived plans are frozen)

**Final step:** `git rm session_doc.py`. Merge `refactor/session-doc-decomposition` to `main`.

## 7. Tradeoffs and open questions

### Worth being honest about

- **More files to navigate.** "Where's that prompt?" goes from "ctrl-F in `session_doc.py`" to "find the right file in `config/agents/`." We mitigate with a 1-line index comment at the top of `config/agents/session_doc/README.md` mapping every prompt to the tool that loads it. Net win because today the prompts are tangled with control flow; after the split each is a standalone artefact.
- **Multi-process overhead for the four-stage flow.** Each tool is a fresh Python interpreter — with the scope collapsed to three tools (not five), this is ~200 ms × 3 = ~600 ms of overhead per full run. Even more negligible against per-call LLM latency. The actually-meaningful overhead is that the editor now makes three separate SSE-streamed shell-outs instead of one; the per-stage gauges becoming real (each stage's output file is one `stat` call away) is the user-facing win that justifies it. A `Makefile` or shell wrapper at the workspace level can chain them for CLI users who want one command.
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
- A "skip to stage N" master CLI. The three tools are the master CLI. If chaining is annoying enough, a shell script in `bin/` is the right answer, not new code.

---

**Next steps if approved:** Phase 1 PR (loader + tests). Each subsequent phase its own PR on this branch. No merge to `main` until Phase 5 is green and `docs/cli/session_doc_pipeline.md` is rewritten.
