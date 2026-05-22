# Detailed Plan: Phase 1 — Cleanup & Deletion

**Goal:** Remove all references to the legacy "Quotes" and "Ledger" systems to reduce technical debt and prepare the codebase for the new sequential workflow.

---

## 1. Frontend Cleanup (Vue 3 / TypeScript)

### A. `SessionDocEditor.vue` (Main View)
*   **State Removal:** Delete `editorMode` ref (formerly used to toggle between `Quotes` and `Editor`).
*   **Component Removal:** Remove imports and template usage for:
    *   `<QuoteAssignmentPanel>`
    *   `<QuotePicker>`
    *   `<QuoteLedger>`
*   **Logic Removal:** Strip out all functions related to the old ledger flow:
    *   `syncQuotes()`, `autoAssign()`, `generateExtraction()`, `onPickerAdded()`, `onQuotesChanged()`, `loadQuoteCounts()`, `quoteCounts`, `syncing`, `autoAssigning`, `showPicker`, `assignmentPanel`.
*   **Config Cleanup:** Remove legacy directory fields from the config form and associated logic (`loadConfigFields`, `applyConfig`):
    *   `extractDir`, `roleplayExtractDir`, `summaryExtractDir`, `roleplaySummary`.
*   **Layout Adjustment:** Remove the right-side `tab-bar` containing the `VTT Source` and `Quote Ledger` tabs.

### B. Component Deletions
Delete the following files entirely from `frontend/src/components/scene-editor/`:
*   `QuoteAssignmentPanel.vue`
*   `QuoteLedger.vue`
*   `QuotePicker.vue`
*   `QuoteRow.vue`

### C. `SceneList.vue` (Sidebar)
*   **Prop/Emit Cleanup:** Remove `quoteCounts`, `showQuoteActions`, `syncing`, `autoAssigning` props, and the `sync` / `auto-assign` emits.
*   **UI Cleanup:** Remove the `.b-quotes` badge and the entire `.scene-actions` footer.

### D. Routing & Navigation
*   **`router.ts` & `AppSidebar.vue`:** Remove the `LEGACY` navigation group, including routes for `VTT Summary` and `Scene Extraction`.
*   **File Deletion:** Delete `frontend/src/views/session/VttSummary.vue`.

---

## 2. Backend Cleanup (FastAPI / Python)

### A. Router & Server Deletions
*   **`server/routers/ledger.py`:** Delete this router entirely.
    *   *Dependency Check:* If `_parse_stage2_scaffold` is used elsewhere, it must be moved or inlined before deletion.
*   **`server/main.py`:** Remove the mounting of the `ledger` router.

### B. `server/routers/scene_editor.py`
*   **Command Cleanup:** Remove legacy command builders: `_build_narrate_cmd_old`, `_build_extract_cmd_old`, `_api_assemble_old`.
*   **Logic Simplification:** Delete the `_using_new_flow()` conditional branch; assume the new flow is the default throughout the module.
*   **Path Cleanup:** Remove fallbacks to `extract_dir` in `_scene_extractions_dir()` and `_narration_dir()`.
*   **Config Cleanup:** Update the `PUT /api/editor/config` endpoint to stop expecting `extract_dir`, `roleplay_extract_dir`, or `summary_extract_dir` from the UI payload.

### C. `session_doc.py` (CLI Tool)
*   **Flag Cleanup:** Remove deprecated extraction flags: `--from-extractions`, `--by-scene`, `--roleplay-extract-dir`.
*   **Retain:** Ensure `--plan-only`, `--scene-extractions`, `--per-scene-output`, and `--scene` remain functional.

---

## 3. Phase 1 Verification Checklist

- [ ] **Build Integrity:** The application boots without errors.
- [ ] **Core Functionality:** `SessionDocEditor.vue` loads and allows the full sequential workflow (Extract $\rightarrow$ Review $\rightarrow$ Narrate $\rightarrow$ Scrub).
- [ ] **UI Audit:** No UI elements or buttons mention "Quotes", "Ledger", "Sync", or "Assign".
- [ ] **Code Audit:** Running `grep -ri "quote" frontend/src` and `server/routers` returns no hits related to the editor's functional surface.
