# CampaignGenerator — TODO

## UI

### [ ] Session Doc Editor — vertical stepper redesign

**Context**
The post-session pipeline is split across two screens with a
horizontal wizard on top:
1. `SessionWorkflow.vue` + `WizardShell` — 6-step horizontal wizard
   whose steps are routes (`/workflow/config`, `/workflow/editor?stage=…`).
2. `SessionConfig.vue` — Step 1 fields (`campaignDir`, `sessionDir`,
   `vttInput`, `sdSession`, `characters`, `voiceDir`, `examplesDir`,
   `vttContext`, …).
3. `SessionDocEditor.vue` — Steps 2-6 share this single page. It has
   its own `configured: boolean` toggle that flips between a config
   form and a 3-column workspace, duplicating `characters`, `voiceDir`,
   `examplesDir`, `context` from `SessionConfig`, plus adding `session`,
   `sessionSummary`, `sceneExtractionsDir`, `narrationDir`, `party`,
   `narrateTokens`, and mode flags.
4. Stage buttons (1/2/Plan/Final) live in one header bar at
   `SessionDocEditor.vue:638-678`. Stage 1, Stage 2, and Plan & Check
   stream subprocess stdout via `connectSSE` into a single shared
   `narrationOutput` ref (lines 309/336/360/390), rendered by
   `NarrationOutput → StreamOutput`. Plan & Check is the third button
   even though the user thinks of it as a distinct phase that produces
   its own artifacts (`plan.md`, `consistency_report.md`,
   `enhanced_sections.md`).

The user has to (a) re-enter overlapping config across two screens,
(b) mentally translate the horizontal wizard into the actual data
flow, (c) interpret a single shared output pane reused for every
stage with no per-stage history, and (d) mode-switch between "config"
and "editor" inside one page that already represents 5 wizard steps.

**Outcome**
One page. Left-side vertical stepper. Center pane changes per step.
Config is unified. Each pipeline stage owns its own run controls,
streaming/batch output panel, and a post-run "ready" artifact preview.
The 3-column scene/extraction editor is itself one step. Assemble is
the last step.

**Recommended approach**

*1. Page shell.* Replace `SessionWorkflow.vue`'s `<WizardShell>` +
`<router-view/>` with a single page that owns a left-side vertical
stepper and a step-driven center pane. Keep the route
`/workflow/editor` (drop the `?stage=` query — local state, not the
URL, drives the active step). Drop `/workflow/config` and `?stage=…`;
redirect old links to `/workflow/editor`.
- Rewrite `frontend/src/views/SessionWorkflow.vue` as the new shell.
- Delete `frontend/src/components/wizard/WizardShell.vue` after
  callers are migrated (`grep -r WizardShell frontend/src` first).
- Update `frontend/src/router/index.ts` to drop `/workflow/config` and
  redirect to `/workflow/editor`.

*2. Left pane — vertical stepper.* New
`frontend/src/components/wizard/VerticalStepNav.vue`. Props:
`steps: { id, label, status }[]`, `activeId`. Emits `select(id)`.
Steps:
1. Session Config (unified form)
2. Stage 1 — Enhance Summary
3. Stage 2 — Re-Extract Quotes
4. Plan & Check
5. Session Doc Editor (3-column scene workspace)
6. Assemble

Status badge per step driven by file existence checks:
- Stage 1: `session-summary.md` exists
- Stage 2: `scene_extractions/` non-empty
- Plan: `narration_dir/plan.md` exists (reuse `_using_new_flow` from
  `server/routers/scene_editor.py:112-114`)
- Editor: derived from scene narration file count
- Assemble: hit existing `GET /api/editor/assembled-exists`

Steps with unmet prerequisites render disabled with a tooltip
("requires Stage 1 output"). Same dependency rules already enforced
by the backend (`scene_editor.py:332, 631`).

*3. Step 1 — unified config.* Merge `SessionConfig.vue` and the
`SessionDocEditor.vue` config form (lines 491-600) into one panel
`frontend/src/views/session/UnifiedConfigPanel.vue`. Group fields:
- **Workspace** — `campaignDir`, `sessionDir`
- **Inputs** — `vttInput`/`vtt`, `gm-assist (session)`
- **Outputs** — `sessionSummary`, `sceneExtractionsDir`, `narrationDir`
- **Party / characters** — `party`, `characters`, `voiceDir`,
  `examplesDir`
- **Context** — `vttContext` / context files (multi-path)
- **Run options** — `narrateTokens`, `proseMode`, `reflections`,
  `useEnhancedSections`

Reuse `PathField.vue` and `MultiPathField.vue`. Keep the existing
Pinia config store (`frontend/src/stores/config.ts`) as the single
source of truth — just stop writing the same value under two keys.
Drop the `sd_*` / `session_doc_*` legacy fallbacks once a one-time
migration step copies them to canonical keys
(`SessionDocEditor.vue:24-46, 64`). The "Open Editor" button goes
away; the stepper itself is the navigation.

*4. Steps 2/3/4 — stage runner panel.* New shared component
`frontend/src/components/session/StageRunPanel.vue`. Each instance
owns one stage. Props: `stageId`, `endpoint`, `supportsBatch`,
`outputArtifacts: { label, path, kind: 'file' | 'dir' }[]`. Layout:
- Header row — Run button, Batch toggle (when `supportsBatch`),
  status text.
- Center top — `<StreamOutput>` for live SSE / batch poll lines
  (reuse `frontend/src/components/shared/StreamOutput.vue` exactly
  as-is; already used by `NarrationOutput`, `RunPanel`,
  `ExtractSynthesizePanel`).
- Center bottom — once `event: done` returncode 0 arrives, swap to a
  "ready" view that previews artifacts:
  - Stage 1 → render `session-summary.md` content (a small new
    endpoint `GET /api/editor/file?path=...` scoped to allowed
    config dirs, or extend the `/extraction/{n}` pattern).
  - Stage 2 → list files under `scene_extractions/` with line counts.
  - Plan & Check → render `consistency_report.md`, `plan.md`,
    `enhanced_sections.md` as collapsible sections.
- Per-stage history — keep last run's stdout in a ref keyed by
  `stageId` so switching stages doesn't wipe it. Today the single
  `narrationOutput` ref (`SessionDocEditor.vue:143`) is shared across
  stages — split it.

Backend stays largely unchanged. The existing endpoints already
match this model: `GET /api/editor/enhance?batch=0|1`
(`scene_editor.py:565-575`), `GET /api/editor/extract?batch=0|1`,
`GET /api/editor/plan` (`scene_editor.py:659-669`). Add one new
endpoint to fetch artifact contents safely:
`GET /api/editor/artifact?key={session_summary|consistency_report|plan|enhanced_sections}`
— returns file text, mapping the key to a CONFIG-derived path so the
URL never carries a raw filesystem path. Mirror
`api_get_enhanced_sections` (`scene_editor.py:539`).

*5. Step 5 — Session Doc Editor.* Extract the existing 3-column
workspace from `SessionDocEditor.vue:602-775` into its own
`frontend/src/views/session/SceneWorkspace.vue`. It already contains
everything needed: `SceneList` (left), `ExtractionEditor` /
`QuoteAssignmentPanel` (center), `NarrationOutput` / `VttPanel` /
`QuoteLedger` (right). The header's Stage 1/2/Plan/Assemble buttons
get **removed** from this component — they belong to their own
steps. Keep the in-workspace Quotes/Editor mode toggle and the
per-scene Narrate button (`SessionDocEditor.vue:299-322`).

*6. Step 6 — Assemble.* Small panel: button calls
`POST /api/editor/assemble` (already exists at
`scene_editor.py:700`), shows result, exposes "Open in Typora" if
`/assembled-exists` returns true. Reuse logic from
`SessionDocEditor.vue:414-451`.

*7. State + step transitions.* A single `useSessionWorkflowStore`
Pinia store holds `activeStepId` and per-stage `lastRunOutput`
strings. Surviving across step switches matters because batch runs
may take minutes. Disable left-pane navigation while any subprocess
is streaming (any step's `running === true`); today the buttons gate
each other via `enhancing || extracting || narrating || planning`
(`SessionDocEditor.vue:642, 652, 662`) — lift that pattern into the
store. Step status badges refresh on `event: done` and on store
hydration.

**Reused, not reinvented**
- `StreamOutput.vue` — already auto-scrolls and renders `<pre>`,
  used by 4 places.
- `connectSSE` helper — already used at `SessionDocEditor.vue:191,
  214, 307, 334, 358, 388`.
- `PathField`, `MultiPathField`.
- `useConfigStore` — keep, just unify the keys.
- All `/api/editor/*` SSE endpoints — no protocol change needed.
- `_using_new_flow`, `_session_summary_path`,
  `_scene_extractions_dir`, `_narration_dir` helpers in
  `scene_editor.py:112-180` — drive both the new `/artifact`
  endpoint and frontend status badges.

**Where it lives**
- `frontend/src/views/SessionWorkflow.vue` — rewrite as new shell
- `frontend/src/views/session/SessionDocEditor.vue` — split into
  `UnifiedConfigPanel.vue`, `StageRunPanel.vue` (×3 instances),
  `SceneWorkspace.vue`, `AssemblePanel.vue`; this file shrinks to a
  thin wrapper or is removed
- `frontend/src/views/session/SessionConfig.vue` — merge into
  `UnifiedConfigPanel.vue`, then delete
- `frontend/src/components/wizard/WizardShell.vue` — delete after
  replacement
- `frontend/src/components/wizard/VerticalStepNav.vue` — new
- `frontend/src/components/session/StageRunPanel.vue` — new
- `frontend/src/views/session/UnifiedConfigPanel.vue` — new
- `frontend/src/views/session/SceneWorkspace.vue` — new (extracted)
- `frontend/src/views/session/AssemblePanel.vue` — new
- `frontend/src/stores/config.ts` — drop `sd_*` legacy fallbacks; add
  migration on load
- `frontend/src/stores/sessionWorkflow.ts` — new (active step +
  per-stage output)
- `frontend/src/router/index.ts` — collapse `/workflow/config` +
  `?stage=` into `/workflow/editor`
- `server/routers/scene_editor.py` — add
  `GET /api/editor/artifact?key=…` for previewing post-run files

**Verification**
1. `cd frontend && npm run typecheck && npm run build` passes.
2. `python -m pytest tests/` still green (no logic change to
   subprocess runner or stage endpoints, only the new `/artifact`
   GET).
3. `./startup` and walk the steps in a real campaign workspace:
   - Step 1: page hydrates from existing `config.values`; edit
     `session`, save persists via `PUT /config`.
   - Step 2 (Stage 1): Run streams; toggle Batch, Run again, watch
     poll lines. After completion the artifact pane shows
     `session-summary.md`. Step badge flips to "ready". Switching
     to Step 3 and back preserves the streamed output.
   - Step 3 (Stage 2): same flow; artifact list shows
     `scene_extractions/NN_*.md`.
   - Step 4 (Plan & Check): runs; artifact panel shows three
     collapsible files.
   - Step 5 (Editor): pick a scene, edit extraction, click Narrate
     per scene — existing flow unchanged.
   - Step 6 (Assemble): button produces final doc, "Open in
     Typora" appears.
4. A stage cannot start while another is running (button disabled +
   nav badge shows "running").
5. Mid-run reload: page rehydrates active step from store; running
   flag clears (we do not persist subprocess handles).
6. Old `/workflow/config` URL redirects to `/workflow/editor`.

### [ ] Web UI must accommodate two-phase extract→review→synthesize flow

**Context**
The `unified-pipeline` branch factors the shared extract→synthesize pipeline
into `campaignlib`. The next PR will expose an explicit review checkpoint
(e.g. `--extract-only`) between the two LLM passes. Today's UI for the four
affected scripts (`distill`, `campaign_state`, `party`, `planning`) is
single-click: one form, one button, one streamed run that produces the final
doc. That model no longer fits once the checkpoint exists.

**What needs to change**
Each affected page (`Campaign State`, `World State`/distill, `Party
Document`, `Planning Document`) needs a two-step interaction:

1. **Extract** — run the first pass, stream output, stop. Show the
   resulting extraction files in a browsable/editable list (one file per
   chunk). User reviews and optionally edits them in-place.
2. **Synthesize** — separate button. Runs the second pass against whatever
   is on disk in the extract dir. Produces the final doc.

Re-running extract should be resumable (already is at the CLI level —
existing files are skipped). Re-running synthesize without re-extracting
is the existing `--synthesize-only` path.

**Open questions**
- Where does the edit UI live? Options: inline textarea per extract,
  "open in Typora" buttons like the scene editor already uses, or just
  a read-only preview + a reminder to edit on disk.
- Should the "run both" single-click flow still exist as a convenience,
  or is forcing the checkpoint the point?
- Shared component vs four copies? Four scripts, same shape — a reusable
  `<ExtractSynthesizePanel>` probably pays for itself.

**Related files**
- `server/routers/grounding.py` — routes for all four scripts; will need
  an `extract_only` query param once the CLI flag lands
- `frontend/src/views/` — per-page Vue components for each of the four
  scripts
- `frontend/src/components/` — likely home for a shared
  extract→review→synthesize component

**Blocked on**
PR that adds the `--extract-only` checkpoint flag to the shared pipeline
(follow-up to the unified-pipeline refactor). UI work should happen after
that lands so the UI has a real flag to drive.

### [ ] Web UI config persistence is asymmetric — fix the load-but-never-save pages

**Context**
Most page components (Distill, Party, Planning, VttSummary, Query, Setup
pages, Experimental pages) read from `config.values` on mount but never write
back. Only `SessionConfig.vue`'s "Save Config" button, the sidebar model
dropdown, the Session Doc batch toggle, and the raw YAML editor actually
`apiPut('/api/config/')`. Edits made on the read-only-write pages live in
local refs and vanish on browser close.

Compounding this, several pages have OR-fallback key precedence
(`v.distill_input || v.summaries`, `v.plan_summaries || v.summaries`, etc.),
so fixing the fallback key in SessionConfig doesn't repair a stale preferred
key that some other page is loading from.

This is what made the "I changed the config but it still ran with the old
path" incident hard to diagnose. Pilot error in that specific case, but the
shape of the bug is structural.

**What needs to change**
Pick one of the four candidate fixes documented in
`docs/web_ui_config_persistence.md`:
1. Auto-save on field blur for every page
2. Drop the OR-fallbacks (one config key per field, no implicit defaults)
3. Have `derive_campaign_paths` predict-and-overwrite all per-page keys, plus
   `apiPut` after derive
4. Visible "unsaved changes" indicator (cheapest, signal-only)

**Related files**
- `docs/web_ui_config_persistence.md` — design doc with full failure flow
  and a Mermaid diagram
- `frontend/src/stores/config.ts` — central store; only `.save()` writes disk
- `frontend/src/views/session/SessionConfig.vue` — the only "normal" page
  with a working Save button
- `server/config.py:_SAVE_KEY_PREFIXES` — backend prefix filter that decides
  which keys land in `ui_config.yaml`

### [ ] Default per-scene narration tokens should be 16000, not 4000

**Context**
The Session Doc Editor's "Per-scene output cap" defaults to 4000 tokens.
That's too tight for a single scene's narration — scenes routinely truncate
or feel rushed. The intended default is 16000; users currently have to
remember to bump it manually.

**What needs to change**
Update the default in three places (the ref initial value, the
`loadConfigFields` fallback, and the help text):

- `frontend/src/views/session/SessionDocEditor.vue:39` — `ref(4000)` → `ref(16000)`
- `frontend/src/views/session/SessionDocEditor.vue:64` —
  `v.sd_narrate_tokens || v.session_doc_narrate_tokens || 4000` →
  `... || 16000`
- `frontend/src/views/session/SessionDocEditor.vue:526` — help text
  `(default: 4000)` → `(default: 16000)`

Check whether `session_doc.py` / `narrative.py` have their own CLI default
that should also move from 4000 to 16000 for consistency.

### [ ] `.scaffold.md` files are loaded as scenes by session_doc.py

**Context**
`session_doc.py:load_scene_extractions` selects files matching `^\d{2}_`
(two digits + underscore prefix) from the scene extractions directory.
The directory also contains scaffold files named like
`01_farewell_to_eldeth.scaffold.md` — these match the same regex and get
ingested as additional "scenes." A 6-scene session shows up as 9 scenes
in the run summary:

```
1. Farewell to Eldeth
2. Farewell To Eldeth.Scaffold      ← scaffold, not a real scene
3. Shadows at Dusk
4. Shadows At Dusk.Scaffold         ← scaffold
5. A Shadow in the Woods
6. A Shadow In The Woods.Scaffold   ← scaffold
7. Interrogation of the Drow Spy
8. The Pocket Spy and the Road to Candlekeep
9. Entry into Candlekeep
```

The Plan section then runs against the real 6 — but the noisy listing
makes it look like Pass 4/5 might pick a scaffold by index, and is
generally misleading.

**Where it lives**
`session_doc.py:449-495` — `load_scene_extractions`. Currently skips
`plan.md`, `enhanced_sections.md`, `consistency_report.md`, and `_`-prefix
files. Scaffolds aren't in the SKIP set and don't have a `_` prefix.

**What to do**
Add `.scaffold.md` to the skip rule. Either extend the filename check
(e.g. skip `*.scaffold.md`) or tighten the regex from `^\d{2}_` to
`^\d{2}_[^.]+\.md$` so that `01_foo.scaffold.md` no longer matches.

### [ ] Log the subprocess command line instead of just streaming it

**Context**
`server/subprocess_runner.py:22-23` echoes the full
`$ /path/to/python script.py --flag value \` command into the SSE
stream. The command is genuinely useful for debugging — it's
copy-pasteable for reproducing the run from a shell — but it currently
only lives in the transient SSE output, not in any log file. Once the
output buffer is cleared the command is gone.

**What to do**
Keep the SSE echo. Additionally write the command (and probably the
returncode + duration) to a per-run log file under `logs/` so failed
runs can be reproduced after the browser session is closed. The
session-doc CLI already manages timestamped logs via
`campaignlib.save_log` — the web runner should use the same scheme.

**Where it lives**
- `server/subprocess_runner.py` — wrap `stream_subprocess` to also
  append to a log file
- `campaignlib.save_log` — existing CLI log helper to mirror

### [ ] Scene Editor: signal that the review goal is "confirm order is right," not "re-order"

**Context**
The Stage 2 / scene-extraction checkpoint is easy to misread. New users
(and even experienced ones returning after a break) assume the review
step requires manually editing or resorting the `## Verbatim moments`
list before narrating. That assumption inflates the perceived cost of
the checkpoint and discourages running it.

What's actually true (now documented at
`docs/session_doc_pipeline.md` → "What 'review' means at the Stage 2 /
scene-extraction checkpoint"):

- VTT moments arrive roughly chronological from Zoom timestamps.
- Pass 5's prompt explicitly forbids reordering — it's a renderer over
  the order it's given.
- A "no edits needed" review is a valid, intended outcome. The goal is
  to **confirm order**, edit only when something is genuinely wrong
  (interleaved exchanges, stray lines from another scene, hallucinated
  quotes, missing GM-recap beats).

**What to do**
Surface this guidance in the Session Doc Editor UI itself, not just in
the docs. Possible shapes:

- A short helper banner at the top of the per-scene extraction view
  that reads something like *"Read through to confirm the moments are
  in the right order. You only need to edit if something is wrong —
  the LLM will not re-order."*
- A `[ ] Reviewed — order looks right` checkbox per scene that toggles
  green and is persisted (e.g. via a small marker in the scene file
  frontmatter, or a sidecar `.reviewed` file). Plays the same role as
  a code-review approval — captures the human checkpoint without
  requiring a code edit.
- Bonus: a "diff vs. previous extraction" view for re-runs, so the
  reviewer can see what changed without re-reading the whole file.

**Where it lives**
- `frontend/src/components/scene-editor/ExtractionEditor.vue` —
  natural home for the banner and checkbox
- `frontend/src/components/scene-editor/SceneList.vue` — show the
  reviewed/unreviewed state next to each scene
- `server/routers/scene_editor.py` — endpoint for persisting the
  reviewed flag if we go the sidecar/frontmatter route

**Why this matters**
The whole pipeline is designed around the global rule "LLM extracts →
human reviews → LLM renders." If users skip the review because they
think it implies mandatory editing, the rule degrades to "LLM extracts
→ LLM renders," which is exactly the failure mode the architecture
exists to prevent.

### [ ] scene_extract.py scaffolds keep raw player names instead of mapping to characters / GM

**Context**
A scaffold from `scene_extract.py` for OotA session 20260427 contains
speaker labels like:

```
Kostadis: "so you shove a mushroom down her throat..."
Mike Hall: "We can put her in the bag of holding with Glabbagool..."
Ben Pfaff: "I thought bags of holding could on… supply."
Gabe: "There you go."
Thorin: "Now, do we have a mushroom to grow her again?"
```

Those should always normalize to:

- `Kostadis` → `GM`
- `Mike Hall` → `Daz` (his PC)
- `Ben Pfaff` → `Grygum` (his PC)
- `Gabe` → `Zalthir` (his PC)
- `Thorin` → `Thorin` (already a character name; leave alone)

The system prompt at `scene_extract.py:90-93` already tries to handle
this:
```
- "GM (Name)" / "DM (Name)" / "Name (GM)" / "Name (DM)" → write as "GM"
- "Character (Player)" → strip the parenthetical; keep the character name
```
But that only works when the VTT has parenthetical disambiguation. Zoom
captions emit only the speaker's display name — usually the player's
real name — so the model has no signal to do the mapping. The result
is raw human names leaking into the scaffold and (eventually) into
narration.

**What to do**
Hand `scene_extract.py` an explicit player→character roster and rewrite
labels deterministically before the LLM ever sees the transcript (or at
minimum inject the roster into the system prompt as a hard mapping
table the model is told to apply).

`session_doc.py:367` already has `extract_character_roster(party_text)`
that builds `- Soma (Wade): Tortle Druid 5` lines from `party.md`. The
same parser can produce a `{player_name: character_name}` dict. The GM
mapping needs to come from somewhere — either a per-campaign config
key (`gm_player_name: Kostadis`) or auto-detected as "the most-frequent
speaker in the VTT who is not in the player→character map."

**Where it lives**
- `scene_extract.py:90-93` — speaker normalization prompt block
- `scene_extract.py:122-131` — `_submit_pending` builds the system
  prompt; this is where the mapping table should land
- `session_doc.py:367` — existing roster parser to reuse
- `campaignlib.py` / config — likely home for a `gm_player_name` field

**Why this matters**
Player names in scaffolds become player names in narration unless
caught by manual edit. That breaks immersion and forces the human
reviewer to do mechanical find-replace work that the pipeline could
do deterministically before the LLM call. It also violates the
attribution rule from `~/.claude/CLAUDE.md`: who-said-what is a
precision decision, not a render decision — it should be locked in by
the human-verified party config, not inferred by the LLM from raw
transcript names.

### [ ] Generalize `--since` (per-chunk re-extract) to all extract→synthesize pipelines

**Context**
`planning.py --build-dossiers` accepts `--since N`, which restricts
Phase 2 aggregation and Phase 3 synthesis to extracts numbered ≥ N
(`planning.py:228-234, 526-530`). The intended use is "I just added
session 11; only roll the new extract into the existing dossiers
instead of re-processing all 10 historical chunks." Phase 1 is already
cache-skipping, so adding `--since` makes Phase 2/3 incremental too.

The same shape would help every other extract→synthesize pipeline —
`distill.py`, `party.py`, `campaign_state.py` — for the same reason:
when a single new session lands, the user wants to fold its content
into the existing canonical doc without re-synthesizing from all
historical extracts.

**What to do**
Lift `--since` into the shared extract→synthesize machinery in
`campaignlib.py` so all four pipelines accept it uniformly. Wire it
through:

- `distill.py` — Phase 2 (synthesis of `world_state.md`) skips extracts
  numbered < N
- `party.py` — Phase 2 (synthesis of `party.md`) skips extracts
  numbered < N
- `campaign_state.py` — Phase 2 (synthesis of `campaign_state.md`)
  skips extracts numbered < N
- `planning.py` — already implemented for `--build-dossiers`; lift the
  validator so `--since` works for the synthesis path too if it makes
  sense

Open question: for synthesis pipelines whose output is a single
canonical doc (not per-NPC), `--since N` means "fold extracts ≥ N into
the existing doc on disk, don't re-synthesize from scratch." That
implies these pipelines need an "incremental synthesis" mode that
takes the existing output as input alongside the new extracts. That is
a bigger change than just plumbing the flag through — flag the design
question separately if so.

**Where it lives**
- `planning.py:228-300` — existing `--since` implementation to copy
- `campaignlib.py` — likely home for the shared extract→synthesize
  helper if/when the four pipelines get unified
- `distill.py`, `party.py`, `campaign_state.py` — argparse + main
  flow updates
- `server/routers/grounding.py` — expose `--since` as a query param
  on the matching `/run/*` endpoints
- `frontend/src/components/shared/ExtractSynthesizePanel.vue` — add
  an optional "Since extract #" field

**Why this matters**
Re-synthesizing from all historical extracts after every session is
expensive (tokens) and slow, and the new content is almost always
isolated to the latest extract. `--since` turns the synthesis step
from O(history) into O(new) — same payoff `--build-dossiers` already
delivers for planning.

### [ ] Remove "Chunk size (chars)" UI control from the web — keep CLI only

**Context**
Every extract→synthesize page in the web UI exposes a "Chunk size
(chars)" number input with a default of 40k–60k. That control is an
artifact of an earlier implementation when chunk sizing was something
the user routinely tuned. The current pipelines either auto-size or
work fine at the default; users do not need (and probably should not
touch) this knob from the web UI. The CLI still exposes
`--chunk-size` for power-user / debugging cases — that stays.

**Where it lives**
Remove the field, the local ref, the `loadFromConfig` line, and the
`runParams` entry from each of these pages:

- `frontend/src/views/grounding/CampaignState.vue:14, 25, 43, 109-110`
- `frontend/src/views/grounding/DistillWorldState.vue:12, 22, 34, 90-91`
  (also the linked `splitChapters` enable/disable behavior on the
  chunk-size input)
- `frontend/src/views/grounding/PartyDocument.vue:23, 38, 81, 198-199`
- `frontend/src/views/grounding/PlanningDocument.vue:20, 41, 47, 76,
  88, 187-188, 242` (two chunk-size fields — synthesis and
  build-dossiers)
- `frontend/src/views/session/VttSummary.vue:21, 34, 65, 169-170`
- `frontend/src/views/prep/QuerySummaries.vue:14, 32, 77-78`

The corresponding `cs_chunk_size`, `distill_chunk_size`,
`party_chunk_size`, `plan_chunk_size`, `plan_build_chunk_size`,
`vtt_chunk_size`, `query_chunk_size` keys can be left in
`ui_config.yaml` for now (harmless if unread) or stripped from the
backend save filter — pick one and be consistent.

**What stays untouched**
- CLI: `python distill.py ... --chunk-size N`, etc., keep working.
- `server/routers/grounding.py` and `session_workflow.py`:
  - either drop the `chunk_size` query parameter, or
  - keep it accepting the value with a sensible default so old
    clients don't break — but stop sending it from the web UI.
  Pick whichever is less risky to ship.

**Why this matters**
The web UI should expose decisions a user routinely makes. Tuning
chunk size isn't one of them — it's a debugging knob, and surfacing
it at the same visual weight as "input file" and "output file" makes
the form feel more complicated than the workflow actually is.

### [ ] Default "Split by session prefix" to `# Chapter` (per-campaign config)

**Context**
The extract→synthesize pipelines (distill, party, planning,
campaign_state) accept `--split-chapters "# Chapter"` so each session
becomes one chunk instead of being chopped at character-count
boundaries. This is the right default for almost every campaign —
session-aligned chunking respects narrative boundaries the chunk-size
slicer would cut through. Today the field is left empty by default
and the user has to remember to type `# Chapter` into "Split by
session prefix" on every page.

The web UI fields exist already
(`CampaignState.vue:103-104`, `PartyDocument.vue:192-193`,
`PlanningDocument.vue:181-182, 236-...`) — they just default to `''`
and are not pre-filled.

**What to do**
Make `split_chapters` a per-campaign config value with a default of
`# Chapter`, applied across all four pages so the user sets it once
(or relies on the default) and never thinks about it again.

Specifically:

- Add a single `split_chapters` (or `chapter_marker`) key to
  `ui_config.yaml` at the campaign level, defaulting to `# Chapter`.
  Surface it as one field on `SessionConfig.vue` so it's set
  alongside campaign / session directories.
- On each grounding page, replace the per-page
  `cs_split_chapters`/`party_split_chapters`/`plan_split_chapters`/
  `plan_build_split_chapters` lookups with a fallback to the
  campaign-level key:
  `splitChapters.value = v.cs_split_chapters || v.split_chapters || '# Chapter'`
- Have `derive_campaign_paths` (or a new derive helper) populate
  `split_chapters: '# Chapter'` if the user hasn't overridden it,
  same way it populates other auto-detected fields.

Open question: do per-page overrides still make sense? Probably yes
for `--build-dossiers` (which may want chapter granularity even when
synthesis is doing something else), but the per-page field can be
collapsed into "Override split prefix" inside the advanced panel
instead of a top-level field.

**Where it lives**
- `frontend/src/views/grounding/CampaignState.vue:15, 26, 44, 103-111`
- `frontend/src/views/grounding/DistillWorldState.vue:13, 23, 35, 84-87`
  (`splitChapters` ref + advanced field — same pattern)
- `frontend/src/views/grounding/PartyDocument.vue:24, 39, 82, 192-200`
- `frontend/src/views/grounding/PlanningDocument.vue:21, 42, 48, 77,
  89, 181-189, 236-...` (synthesis + build-dossiers)
- `frontend/src/views/session/SessionConfig.vue` — natural home for
  the new campaign-level field
- `server/config.py:derive_campaign_paths` — return
  `split_chapters: '# Chapter'` as a default
- `server/routers/grounding.py:49, 86, 126, ...` — already accepts
  the param; no change unless we want to default it on the server

**Why this matters**
Splitting on chunk-size boundaries breaks scenes mid-paragraph and
forces the synthesizer to reason across chunk seams. Splitting on
`# Chapter` keeps each session intact, which dramatically improves
extraction quality. It is the correct default; treating it as
"opt-in advanced flag" buries the right answer behind a friction step
the user has to redo for every campaign and every page.

### [ ] Per-step batch-mode toggle for distill / party / planning / campaign_state extractions

**Context**
`scene_extract.py` and `session_doc.py` already support Anthropic
Message Batches API (`--batch`) for the per-scene extraction +
narration passes — 50% off list price in exchange for
non-streaming, poll-based progress. The shared infrastructure is in
`campaignlib.py:803-902` (`build_batch_request`, `submit_batch`,
`poll_batch`, sidecar files for resumability). The four
extract→synthesize grounding pipelines (`distill.py`, `party.py`,
`planning.py`, `campaign_state.py`) do **not** use batch mode today —
their Phase 1 extraction runs synchronously, one chunk at a time, at
full price.

A typical full re-extract (e.g. distill 10 sessions into world_state)
fans out into 10 independent chunk extractions with no inter-chunk
dependency — exactly the shape Message Batches is built for. Same
for `planning.py --build-dossiers` Phase 1 (per-chunk NPC mention
extraction), `party.py` Phase 1, `campaign_state.py` Phase 1.
Synthesis (Phase 2/3) is one inherently-sequential call per pipeline,
so batch doesn't apply there — only extraction.

**What to do**
Add a per-step batch toggle so the user can independently choose
live-streaming or batched extraction for each pipeline:

- `distill.py --batch-extract` → Phase 1 extraction goes through
  Message Batches; synthesis still streams.
- `party.py --batch-extract` → same.
- `planning.py --batch-extract` → applies to Phase 1 of both the
  synthesis path and the `--build-dossiers` path.
- `campaign_state.py --batch-extract` → same shape.

UI surface: each grounding page (`CampaignState.vue`,
`DistillWorldState.vue`, `PartyDocument.vue`, `PlanningDocument.vue`)
gets a single checkbox in the run panel — *"Use batch mode for
extraction (50% off, no live progress)"*. Persist as
`{pipeline}_batch_extract` in `ui_config.yaml` (matches the existing
`sd_batch` precedent at
`SessionDocEditor.vue:68, 71-78`).

UI also needs to show batch progress instead of streaming output
while a batch is in flight — `RunPanel.vue` would need a small
adapter (or a parallel `BatchRunPanel.vue`) that polls
`batch.request_counts` and displays the `processing/succeeded/errored`
counts, the way `scene_extract.py` already does on the CLI.

**Where it lives**
- `campaignlib.py:803-902` — existing batch infra to reuse:
  `build_batch_request`, `submit_batch`, `poll_batch`,
  `_sidecar_path`, etc.
- `scene_extract.py:_submit_pending` — the existing template for
  "fan a list of independent extractions out as one batch"
- `distill.py`, `party.py`, `planning.py`, `campaign_state.py` —
  factor Phase 1 to optionally route through the batch helpers
- `server/routers/grounding.py` — accept `batch_extract: bool` query
  param; if true, the runner needs to surface batch poll output as
  SSE lines
- `frontend/src/components/shared/ExtractSynthesizePanel.vue` —
  natural home for the checkbox (or each page's RunPanel)
- `frontend/src/views/session/SessionDocEditor.vue:68, 71-78` —
  reference implementation for the persistent toggle

**Why this matters**
A 10-session distill at ~60k chars/chunk costs roughly
10 × (~15k input + ~3k output tokens) at full price. Batch halves
the cost. The user gives up live streaming progress in exchange,
which is a fair trade for re-runs the user fires off and walks away
from. Making it per-step means the user can keep streaming on the
fast Phase 1 calls when they're iterating, then flip to batch for the
big "rebuild from all of history" runs.

### [ ] Rename "Session summaries file" → "Canonical timeline" everywhere

**Context**
The master narrative bible (the big chronological document the
extract→synthesize pipelines chunk through) is referred to in the UI,
help text, and CLI as the "Session summaries file" — sometimes
"summaries", sometimes "session summaries", sometimes "summaries
file". The naming is inconsistent and easy to confuse with the
per-session `summaries/<date>/...` directories that hold scene
extractions, scaffolds, and the assembled gm-assist doc. When the UI
prompts for "Session summaries file" it's not obvious whether it
wants the master bible or a per-session summary, and users (including
me) keep guessing wrong.

The canonical name should be **"canonical timeline"** (or
"canonical timeline book" in long form) — that's what the document
actually is: the chronologically-ordered narrative book that all
extract pipelines treat as the source of truth for prior sessions.

**What to do**
Rename across the UI, CLI help, and docs. Likely surface area:

- Frontend labels and help text on every grounding page that asks
  for the master bible:
  - `frontend/src/views/grounding/CampaignState.vue`
  - `frontend/src/views/grounding/DistillWorldState.vue`
  - `frontend/src/views/grounding/PartyDocument.vue`
  - `frontend/src/views/grounding/PlanningDocument.vue`
  - `frontend/src/views/prep/QuerySummaries.vue`
- CLI argparse help in `distill.py`, `party.py`, `planning.py`,
  `campaign_state.py`, `query.py`, and any other scripts that take
  `--summaries` (consider whether the flag itself should also be
  renamed to `--canonical-timeline`, with `--summaries` kept as an
  alias for back-compat).
- Doc strings inside `campaignlib.py` for any helper that takes the
  bible as input.
- `docs/cli_tools.md`, `docs/session_prep_workflow.md`, and any
  other doc that says "session summaries file" referring to the
  master bible.

Do **not** rename:

- The `summaries/<date>/...` per-session directory tree (that one
  really is a per-session summary, the name is correct there).
- `vtt_summary.py` and its output (per-session summary doc that
  later gets folded into the canonical timeline).

**Why this matters**
The two concepts — *canonical timeline* (one big book) and
*per-session summary* (many small files) — feed different parts of
the pipeline and shouldn't share a label. Disambiguating the name in
the UI is the cheapest way to stop the recurring "wait, which one
does this field want?" confusion.

### [ ] assemble.py: scene heading should be "<narrator> — <title>", not "Scene NN — <title>" + "(narrated by …)"

**Context**
`assemble.py:91-98` currently writes each scene as:

```
## Scene 01 — Farewell to Eldeth
*(narrated by Thorin)*
```

The user wants the narrator promoted into the heading line itself,
e.g.:

```
## Thorin — Farewell to Eldeth
```

(or `## Thorin: Farewell to Eldeth` — pick whichever separator
matches the in-doc style we use elsewhere). The "Scene NN" prefix and
the italic `(narrated by ...)` line both go away.

Real example to verify against:
`~/campaigns/out-of-the-abyss/summaries/20260427/gm-assist-doc.md`
shows the current (incorrect) format at lines 6-7, 135-136, 216-217,
259-260, 412-413, etc.

**Where it lives**
`assemble.py:95-98`:

```python
narrator = meta.get("narrator", "")
header = f"## Scene {scene_num:02d} — {scene_name}"
attrib = f"*(narrated by {narrator})*\n" if narrator else ""
parts.append("---\n\n" + header + "\n" + attrib + "\n" + body)
```

**What to do**
Rewrite to:

```python
narrator = meta.get("narrator", "")
header = f"## {narrator} — {scene_name}" if narrator else f"## {scene_name}"
parts.append("---\n\n" + header + "\n\n" + body)
```

Drop the `attrib` line entirely. Decide whether to keep the scene
number anywhere (e.g. as a parenthetical suffix `## Thorin —
Farewell to Eldeth (Scene 01)`) or drop it — user can confirm
preference, default to dropping it since the file order already
preserves sequence.

**Why this matters**
The narrator is the most useful nav signal in the assembled doc —
when scrolling a long session you read by character voice, not by
scene number. Putting the narrator first in the H2 makes the
table-of-contents auto-generated by Markdown viewers actually
useful, and removes the visually noisy italic attribution line
beneath every heading.

