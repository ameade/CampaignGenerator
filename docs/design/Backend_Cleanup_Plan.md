# Backend Cleanup Plan: Removing Quote/Ledger Systems

This plan details the removal of the legacy Quote and Ledger systems from the backend to reduce technical debt and align with the new scene-centric workflow.

## 1. Identification of Target Files

The following files and modules are identified for complete removal or significant refactoring:

### Core API Routers (To be Deleted)
- `server/routers/ledger.py`: Contains all endpoints for the quote ledger, syncing, and auto-assignment. **[REMOVED]**
- `server/routers/scene_editor.py`: (If it contains quote-specific logic) Should be refactored to only handle scene extraction/narration or deleted if redundant.

### Supporting Modules/Logic (To be Deleted/Refactored)
- `server/main.py`: Remove all ledger-related router registrations. **[COMPLETED]**
- Any utility files or services used exclusively by the ledger system.
- `campaignlib.py`: Remove any dead code related to the ledger. **[COMPLETED]**

## 2. Execution Status & Roadmap

### Phase 1: Impact Analysis & Dependency Check
- [x] **Search for imports**: Use `grep` to find all instances where `ledger` or quote-related logic is imported across the `server/` directory.
- [x] **Check `server/main.py`**: Identify where `ledger.router` (or equivalent) is included in the FastAPI app.
- [x] **Check `campaignlib.py`**: Identify any functions in the shared library that handle ledger/quote persistence or retrieval.

### Phase 2: Code Removal
- [x] **Delete Router Files**: Physically remove `server/routers/ledger.py`.
- [x] **Update Main Entry Point**: Remove the router registration lines in `server/main.py`.
- [x] **Clean up `campaignlib.py`**: Remove any dead code related to the ledger to keep the API surface clean.
- [ ] **Refactor `scene_editor.py`**: Ensure it is strictly focused on the new sequential pipeline (Extraction $\rightarrow$ Narration).

### Phase 3: Verification
- [ ] **Verify Startup**: Run the FastAPI server (or a test script) to ensure no `ImportError` or `Route not found` errors occur during initialization.
- [ ] **Unit Tests**: Run existing tests to ensure that the removal hasn't broken core functionality (like session doc generation) that might have had indirect dependencies.

## 3. Risks & Mitigations
- **Risk**: A shared utility in `campaignlib.py` is accidentally deleted, breaking a non-quote related feature.
- **Mitigation**: Perform thorough `grep` searches and run the full test suite immediately after any change to `campaignlib.py`.
- **Risk**: The frontend still attempts to call `/api/ledger/...` endpoints.
- **Mitigation**: This plan assumes Frontend Cleanup (Phase 1) is already complete. A final check in `frontend/` for any remaining `ledger` strings is recommended.
