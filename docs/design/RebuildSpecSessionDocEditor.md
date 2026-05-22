# Session Doc Editor — Rebuild Spec

**For:** Claude Code, working in `~/src/CampaignGenerator`.
**Visual reference:** `Session Doc Editor — Wireframes.html` in the design project — the **F · Recommended** artboard is the target. The other artboards (A–E) are the option space we rejected or absorbed.
**Existing flow doc:** `docs/web/session_doc_editor.md` — the operator flow this UI serves. Keep it accurate as you go.

---

## TL;DR — what's changing

The Session Doc Editor was built around an old four-step flow whose Quotes ledger / picker / assignment UI is now dead weight. The page is being rebuilt around how the work actually runs today: **sequential per-scene editing, with a pre-Assemble review gate at the end.**

The editor itself (extraction textarea, narrate/scrub, save/diff/typora) is **good and stays**. The chrome around it is being replaced.

**Net:**
1. **Delete** the entire Quotes mode + ledger surface.
2. **Replace** the giant pre-flight config form with a `Config ⚙` **slide-out drawer**.
3. **Add** a **Profile** picker at the top (named presets that capture a flag set).
4. **Add** **lifecycle dots** per scene in the sidebar (extract · review · narrate · scrub).
5. **Gate** Assemble behind a new **Review** screen that summarizes what the user did across the session.

Non-goals: no changes to the python CLI scripts; no changes to the SSE streaming; no changes to grounding-doc pages.

---

## Phase 1 — Rip out the dead flow

These are dead because the user no longer touches the quote ledger. The Stage-2 file (`scene_extractions_new/NN_<slug>.md`) is now the only input to per-scene narrate; the ledger / picker / assignment layer is gone.

### Frontend deletions

`frontend/src/views/session/SessionDocEditor.vue`:
- Remove the `editorMode` ref and the `Quotes`/`Editor` mode toggle in the header.
- Remove `<QuoteAssignmentPanel>`, `<QuotePicker>`, `<QuoteLedger>` and their imports.
- Remove `syncQuotes()`, `autoAssign()`, `generateExtraction()`, `onPickerAdded()`, `onQuotesChanged()`, `loadQuoteCounts()`, `quoteCounts`, `syncing`, `autoAssigning`, `showPicker`, `assignmentPanel`.
- Remove the `extractDir`, `roleplayExtractDir`, `summaryExtractDir`, `roleplaySummary` fields from the config form and from `loadConfigFields` / `applyConfig`. (The legacy `scene_extractions/` dir, `vtt_extractions/`, `vtt_roleplay_extractions/` are no longer surfaced.)
- Remove the right-side `tab-bar` with `VTT Source` / `Quote Ledger` tabs. The right column becomes VTT only (see Phase 2).
- Remove the `Sync Quotes` / `Auto-Assign` buttons from `SceneList`.

Delete files outright:
- `frontend/src/components/scene-editor/QuoteAssignmentPanel.vue`
- `frontend/src/components/scene-editor/QuoteLedger.vue`
- `frontend/src/components/scene-editor/QuotePicker.vue`
- `frontend/src/components/scene-editor/QuoteRow.vue`

Update `frontend/src/components/scene-editor/SceneList.vue`:
- Drop the `quoteCounts`, `showQuoteActions`, `syncing`, `autoAssigning` props and the `sync` / `auto-assign` emits.
- Drop the `b-quotes` badge and the entire `.scene-actions` footer.
- (We'll add lifecycle dots in Phase 2.)

### Backend deletions

`server/routers/ledger.py`:
- Delete the router entirely. The `Scaffold-from-Stage-2` capability was used by the old Quotes mode and is no longer needed (the user edits the Stage-2 file directly). If `_parse_stage2_scaffold` is referenced from anywhere else, inline it or move it; otherwise drop it.

`server/main.py`:
- Stop mounting the ledger router.

`server/routers/scene_editor.py`:
- Remove `_build_narrate_cmd_old`, `_build_extract_cmd_old`, `_api_assemble_old`.
- Remove the legacy branch in `_using_new_flow()` and just assume new flow throughout. (Or delete `_using_new_flow()` and inline the new-flow path.)
- Remove fallbacks to `extract_dir` in `_scene_extractions_dir()` / `_narration_dir()`.
- Drop `extract_dir`, `roleplay_extract_dir`, `summary_extract_dir` from the config payload accepted by `PUT /api/editor/config`. Keep the fields in the in-memory `CONFIG` dict if other paths read them, but stop expecting them from the UI.

`session_doc.py`:
- Drop the old extract paths (`--from-extractions`, `--by-scene`, `--roleplay-extract-dir`). Keep `--plan-only`, `--scene-extractions`, `--per-scene-output`, `--scene`.

### Routes / sidebar

`frontend/src/router.ts` and `AppSidebar.vue`:
- Remove the `LEGACY` group entries (`VTT Summary`, `Scene Extraction`) and their routes.
- Delete `frontend/src/views/session/VttSummary.vue` and any scene-extraction page that's not the editor.

**Verification:** the page loads, runs Stage 1/2/3, lets you edit a scene's extraction and narrate it, and assembles. No reference to "quotes" remains anywhere in `SessionDocEditor.vue`.

---

## Phase 2 — Build the new shape (Wireframe F)

The page becomes:

```
┌─ Header ───────────────────────────────────────────────────────────────┐
│ Session Doc · <session>  [Profile: Memoir mode ▾]  ① ● ② ● ③ ⚠ ④ 5/8  │
│                                                  [Config ⚙] [Assemble→]│
├─ Re-run row ───────────────────────────────────────────────────────────┤
│ Re-run:  [Enhance] [Extract] [Plan & check] [Scrub all]               │
├─ Columns ──────────────────────────────────────────────────────────────┤
│ Scenes        │ 03 — Zalthir / The Inn   ~3.2k tok           │ VTT     │
│ 01 ● ● ● ●    │ [Extraction|Notes|Output|Diff]                │ search  │
│ 02 ● ● ● ○    │ ...textarea...                                │ ...     │
│ 03 ● ● ○ ○ ←  │ [Save] [Diff] [Narrate] [Scrub] ☐P ☐M ☑E ✓rev │         │
│ ...           │                                               │         │
└───────────────┴───────────────────────────────────────────────┴─────────┘
```

Plus: a `Config ⚙` slide-out drawer from the right edge, and a `Review` modal/route that intercepts the Assemble action.

### 2.1 Header bar (`SessionDocEditor.vue`)

Replace today's `editor-global-header` with a single, lighter strip:

| Element | Behavior |
|---|---|
| `Session Doc` title + `<session>/<date>` | Static. |
| **Profile** dropdown | New. See 2.5. |
| **Pipeline status** | Read-only inline strip: `① Enhance ● 2h` `② Extract ● 1h` `③ Plan ⚠ stale` `④ Narrate 5/8`. Status dot + label + last-run ago. Status comes from existing `/api/editor/...` endpoints (file mtime vs. inputs). |
| `Config ⚙` button | Opens the knob drawer (2.4). |
| `Assemble →` button | Opens the Review screen (2.6). Never calls `/api/editor/assemble` directly. |

Status dot rules (green / amber / red / cold):
- **green / `ok`** — output file exists and is newer than all of its inputs.
- **amber / `warn`** — output exists but is older than one of its inputs (stale).
- **red / `bad`** — last run failed (look at last `subprocess_runner` exit code; if not tracked yet, treat as cold).
- **cold** — never run.

Add a new endpoint `GET /api/editor/pipeline-status` that returns this for the four stages. Cheap — just stat the canonical output files in the session dir.

### 2.2 Re-run row

A thin row beneath the header with `Enhance`, `Extract`, `Plan & check`, `Scrub all` buttons. Hits the existing `/api/editor/enhance`, `/api/editor/extract`, `/api/editor/plan`, `/api/editor/scrub-all` endpoints. **Per-scene** Narrate/Scrub stays on the editor toolbar — never global.

### 2.3 Scene list with lifecycle dots (`SceneList.vue`)

Each scene row gets four small dots:

| Dot | Source |
|---|---|
| **Extract** | `s.has_extraction` |
| **Review** | `s.reviewed` (new — backed by the existing `*.reviewed` marker file in `_reviewed_marker_path`) |
| **Narrate** | `s.has_output` |
| **Scrub** | new — does a `*.scrubbed.md` sibling exist for the narration file? |

Backend: extend `_load_scenes()` in `scene_editor.py` to include `has_scrubbed: bool` (just look for the sibling file).

Dot semantics: `ok` if true, `cold` if false. If a dot is true but its predecessor is amber, render the dot itself amber (the work is done but might be stale).

Drop the existing `b-quotes`, `b-ext`, `b-rev`, `b-nar` text badges — the four dots replace them.

### 2.4 Knob drawer (new component)

New file: `frontend/src/components/scene-editor/KnobDrawer.vue`.

A right-edge slide-out, ~360px wide, opened/closed via the `Config ⚙` button. Persistent (in localStorage) whether it's open.

Contents — every knob from today's pre-flight config form, organized by the stage that reads it:

| Section | Fields |
|---|---|
| **Paths** | GMassistant recap, session summary, scene extractions dir, narration dir, output dir, voice dir, examples dir, characters, context files, party doc |
| **Stage ① Enhance** | `batch`, `backend` (anthropic / dgx) |
| **Stage ② Extract** | `batch`, `force` |
| **Stage ③ Plan & Check** | `use_enhanced_sections` |
| **Stage ④ Narrate** | `narrate_tokens`, `prose_mode`, `reflections`, `use_enhanced_sections`, `narration_genre`, `backend` |
| **Stage ⑤ Assemble** | (placeholder for polish toggle if/when wired) |

Every field is the same control as today's form (PathField, MultiPathField, number, checkbox, text). Same backing keys in the config store (`v.sd_*`). Saving a field PUTs to `/api/editor/config` the same way `applyConfig()` does today.

**Important:** the page itself no longer has a pre-flight config gate. On first load, if `session` is unset, open the drawer automatically and disable the editor area with a "Set the session file to begin" message. Once `session` + `scene_extractions_dir` are set, the editor enables and the drawer can be closed.

Delete the `configured` ref, the giant `.config-panel` template block, and the `applyConfig()` two-step flow. Replace with auto-apply on every field change (same `/api/editor/config` PUT).

### 2.5 Profiles

New, lightweight. A **profile** is a named set of values for the Stage-④ knobs that change between runs. (Paths don't belong in a profile — they're per-session.)

#### Data shape

```json
{
  "profiles": [
    {
      "name": "Memoir mode",
      "knobs": {
        "narrate_tokens": 16000,
        "prose_mode": true,
        "reflections": true,
        "use_enhanced_sections": true,
        "narration_genre": "First-person comic-noir fantasy memoir — observational, dry, irony-forward",
        "backend": "anthropic"
      }
    },
    { "name": "Fast draft", "knobs": { "narrate_tokens": 8000, "prose_mode": false, "backend": "dgx" } },
    { "name": "Consistency only", "knobs": { "narrate_tokens": 4000, "prose_mode": false } }
  ],
  "active": "Memoir mode"
}
```

#### Storage

A new `profiles` section in the existing typed config (see `server/config_models.py` and `config_service.py`). Add a `ProfilesSection` Pydantic model and wire it through `PUT /api/config/section/profiles`. Keep the *active* profile's knobs mirrored into the legacy `sd_*` keys so the rest of the system keeps working unchanged.

#### UI

Dropdown in the header:
- Lists all profiles + an inline "Save current as new…" and "Edit profiles" item.
- Selecting a profile rewrites the Stage-④ knob values (in the store + via `PUT /api/editor/config`).
- Editing happens inline in the knob drawer with a small "Profile" indicator at the top of the Stage-④ section ("currently editing: Memoir mode · [revert] [save changes]").

If the user edits a knob without saving, mark the profile as "dirty" with an asterisk: `Memoir mode * ▾`.

### 2.6 Review-before-Assemble screen

New route: `/workflow/editor/review` (or modal — see below).

The `Assemble →` button doesn't run `/api/editor/assemble` directly. Instead it opens this view.

#### Data the view needs

Three GETs (new — small, cheap):

1. `GET /api/editor/pipeline-status` — already added in 2.1.
2. `GET /api/editor/scene-roster` — for every scene, return:
   - `index`, `narrator`, `scene`, `tokens` (estimate), `lifecycle` (4 booleans), `applied_knobs` (the knob values from when this scene was last narrated — see below), `preview` (first ~120 chars of the narration if it exists).
3. `GET /api/editor/activity` — chronological list of pipeline actions. See below.

#### Recording activity & applied knobs

When a Narrate / Scrub / Enhance / Extract / Plan run completes, append a JSON line to `<session_dir>/.cg/activity.jsonl`:

```json
{ "ts": "2026-05-19T11:08:00Z", "stage": "narrate", "scene": 3, "rc": 0,
  "knobs": { "tokens": 16000, "prose": true, "reflections": false, "backend": "anthropic", "genre": "..." },
  "outputs": ["narration/session_doc_scene_03_the_inn.md"] }
```

Do this in `subprocess_runner` or in each route's `onDone` handler (the route already knows the knobs). For `narrate`, also stash the same knob dict next to the narration file (`session_doc_scene_NN_<slug>.knobs.json`) so the roster can look up "what flags were applied to this scene" cheaply.

`GET /api/editor/activity` just tails the JSONL and returns the last N entries.

#### The screen itself

Three blocks:

1. **Pipeline readiness strip** — same shape as the header strip but with verbose labels and timestamps. Green across = safe.
2. **Activity timeline** (left) — chronological list from `/api/editor/activity`. Group by day, show stage label + per-action detail + status dot.
3. **Per-scene roster** (right) — every scene as a row with lifecycle dots, applied-knob chips, token count, first line of the narration. Rows that block Assemble (not narrated, or narrated with a stale extraction) get a red border + a callout.

Footer rolls up "Knobs used across the session" — counts of prose/memories/enhanced/backend/genre — and the actual Assemble button. The button is **disabled** if any scene is blocking, with a one-line reason next to it ("blocked: scene 8 not narrated").

When the user clicks `Assemble Doc`, POST `/api/editor/assemble` (existing endpoint), show a success/failure state, and either offer Polish or `Open in Typora`.

Implementation suggestion: do this as a full-route page (`/workflow/editor/review`) so the URL is shareable and back-button works. Make `Assemble →` in the header just `router.push`.

---

## Phase 3 — Acceptance criteria

A clean install passes these:

1. **Cold start.** Open the page with no `sd_session` set → the knob drawer opens, editor area is disabled with a helpful empty state. Set the recap + scene extractions dir → editor enables, drawer closable.
2. **Sequential flow.** From a fresh extracted session: click each scene in order, edit + Save + Narrate + Scrub + mark Reviewed, advance via `next →`. The four lifecycle dots fill in. The pipeline `④` counter increments.
3. **Profile swap.** Select `Fast draft` → tokens drop to 8k, prose=off, backend=dgx in both the drawer and the editor's inline toggles. Edit a knob → profile shows `* ▾`. Save changes → asterisk clears.
4. **Stale detection.** After all scenes are narrated, edit `session-summary.md` on disk and refresh → `② Extract` becomes amber, `③ Plan` becomes amber, the scenes that depended on the stale plan show amber narrate dots.
5. **Review gate.** Click `Assemble →` → lands on `/workflow/editor/review` with pipeline strip, timeline, and roster populated. With one scene unnarrated, the `Assemble Doc` button is disabled with reason. Narrate it, return, button enables.
6. **Activity persistence.** The activity timeline survives a server restart (because it's a JSONL on disk).
7. **No quotes anywhere.** `grep -ri "quote" frontend/src` returns nothing in the editor surface. `grep -ri "ledger" server/routers` returns nothing.

---

## Phase 4 — Update docs

Rewrite `docs/web/session_doc_editor.md`:
- Replace the "Sidebar layout" + "The stages" + "Per-scene work — Scaffold + Narrate" sections to reflect the new shape.
- Drop the "Why two paths" / "Quotes mode" / "Scaffold from Quotes" sections entirely.
- Add a section on **Profiles** (data shape, where they live, how to edit).
- Add a section on the **Review before Assemble** screen.
- Move the `TODO` section's "Rip out legacy flows" item to a **CHANGELOG** entry — it's done.

---

## Code-map cheat sheet

| Concept | Frontend | Backend |
|---|---|---|
| Page shell | `frontend/src/views/session/SessionDocEditor.vue` | — |
| Scene list | `frontend/src/components/scene-editor/SceneList.vue` | `GET /api/editor/scenes` (`scene_editor.py`) |
| Extraction editor | `frontend/src/components/scene-editor/ExtractionEditor.vue` | `GET/PUT /api/editor/extraction/{n}`, `/prev` |
| Narration output | `frontend/src/components/scene-editor/NarrationOutput.vue` | `GET /api/editor/narrate/{n}` (SSE) |
| VTT panel | `frontend/src/components/scene-editor/VttPanel.vue` | `GET /api/editor/vtt` |
| Knob drawer | **new** `frontend/src/components/scene-editor/KnobDrawer.vue` | `GET/PUT /api/editor/config` |
| Profiles | **new** profile picker in `SessionDocEditor.vue` | **new** `PUT /api/config/section/profiles` (extend `config_models.py`) |
| Pipeline status | inline in header | **new** `GET /api/editor/pipeline-status` |
| Review screen | **new** `frontend/src/views/session/ReviewAssemble.vue` + route `/workflow/editor/review` | **new** `GET /api/editor/scene-roster`, `GET /api/editor/activity` |
| Assemble (unchanged) | called from Review footer | `POST /api/editor/assemble` |

---

## Ordering suggestion

Land in this order so each step is independently shippable:

1. **Phase 1 deletions.** Smallest blast radius, biggest cleanup.
2. **Lifecycle dots in SceneList.** Tiny visual win.
3. **Knob drawer + delete config-panel.** Removes the worst friction.
4. **Profiles.** Once the drawer exists, this is a small layer on top.
5. **Pipeline status endpoint + header strip.** Read-only first.
6. **Review screen.** Last because it depends on activity recording, which depends on the routes being clean.

Each phase keeps the editor fully usable.
