# Plan: Session Doc Editor Rebuild

## 1. Objective
Transition the Session Doc Editor from a "Quotes-centric" workflow to a "Sequential Scene-centric" workflow. This involves removing the Quote Ledger/Assignment infrastructure and implementing a new UI pattern featuring a Profile picker, a "Knob Drawer" for configuration, lifecycle status dots for scenes, and a mandatory "Review" gate before final assembly.

## 2. Execution Phases

### Phase 1: Cleanup & Deletion (Reducing Technical Debt)
*Goal: Remove all references to the legacy "Quotes" and "Ledger" systems.*

*   **Frontend Deletions:**
    *   `SessionDocEditor.vue`: Remove `editorMode` toggle, Quote imports, and all Quote-related logic (`syncQuotes`, `autoAssign`, etc.).
    *   Remove Quote-specific components: `QuoteAssignmentPanel.vue`, `QuoteLedger.vue`, `QuotePicker.vue`, `QuoteRow.vue`.
    *   `SceneList.vue`: Remove quote badges and sync/assignment buttons.
    *   `router.ts` & `AppSidebar.vue`: Remove legacy `VTT Summary` and `Scene Extraction` routes/links.
    *   Delete `frontend/src/views/session/VttSummary.vue`.
*   **Backend Deletions:**
    *   `server/routers/ledger.py`: Delete the entire router.
    *   `server/main.py`: Unmount the ledger router.
    *   `server/routers/scene_editor.py`: Remove legacy `_old` command builders and the `_using_new_flow` branch.
    *   `session_doc.py`: Remove deprecated extraction flags (`--from-extractions`, etc.).

### Phase 2: The New UI Shape (Wireframe F)
*Goal: Implement the core visual and functional changes.*

*   **Header Reconstruction:**
    *   Implement a lightweight header strip showing Session Name, a new **Profile Dropdown**, **Pipeline Status Dots** (Enhance, Extract, Plan, Narrate), and the **Config ⚙** button.
    *   **New Endpoint:** `GET /api/editor/pipeline-status` to calculate status based on file mtimes.
*   **Lifecycle Dots (`SceneList.vue`):**
    *   Add four dots per scene: **Extract** (has file), **Review** (has `.reviewed` marker), **Narrate** (has output), and **Scrub** (has `.scrubbed.md` sibling).
    *   **New Backend logic:** Extend `_load_scenes()` in `scene_editor.py` to detect these markers.
*   **Knob Drawer (`KnobDrawer.vue`):**
    *   Create a slide-out right-side drawer containing all existing configuration fields, organized by pipeline stage.
    *   Replace the old giant config form with this drawer.
    *   Implement auto-save on field change via `PUT /api/editor/config`.
*   **Profiles System:**
    *   Implement a `ProfilesSection` in the backend config model.
    *   **New Endpoint:** `PUT /api/config/section/profiles`.
    *   Add a Profile picker in the header that applies saved Stage-④ knobs to the current session.

### Phase 3: The Review Gate & Activity Tracking
*Goal: Ensure quality control before final assembly.*

*   **Activity Recording:**
    *   Implement `.cg/activity.jsonl` logging in `subprocess_runner.py` or route handlers to track every pipeline action (timestamp, stage, scene, knobs used).
    *   Stash `.knobs.json` next to narration files for quick roster lookup.
*   **Review Screen (`ReviewAssemble.vue`):**
    *   Create a new route `/workflow/editor/review` triggered by the `Assemble →` button.
    *   **New Endpoints:** `GET /api/editor/scene-roster` and `GET /api/editor/activity`.
    *   The screen will show a Pipeline Readiness strip, an Activity Timeline, and a Per-Scene Roster.
    *   **Gate Logic:** The "Assemble Doc" button is disabled if the roster shows incomplete/stale scenes.

### Phase 4: Documentation Update
*   Rewrite `docs/web/session_doc_editor.md` to reflect the new architecture, removing all mention of the old "two-path" (Quotes vs Editor) logic.

## 3. Proposed Implementation Order (Incremental Delivery)
1.  **Cleanup (Phase 1):** Clean the codebase first to make subsequent UI changes easier.
2.  **Lifecycle Dots:** A low-effort visual improvement.
3.  **Knob Drawer:** Moves configuration out of the main view.
4.  **Profiles:** Adds the intelligence layer to the knobs.
5.  **Status & Header:** Provides the high-level overview.
6.  **Review Screen:** The final, most complex piece that ties everything together.

## 4. Acceptance Criteria
*   [ ] **No Quotes:** `grep -ri "quote" frontend/src` and `server/routers` returns no results for the editor.
*   [ ] **Cold Start:** Opening the editor with no session set automatically opens the Knob Drawer.
*   [ ] **Stale Detection:** Changing a source file (e.g., `session-summary.md`) correctly turns pipeline status/dots to amber.
*   [ ] **Review Gate:** The Assemble button is non-functional until all scenes are narrated and reviewed.
