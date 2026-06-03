# Architecture Specification Gaps and Required Updates

- **1. Anthropic import rule** – Spec states “Never `import anthropic` from a script”, but `campaignlib.py` directly imports `anthropic` to instantiate the client.
- **2. High‑level diagram – User entry points** – Diagram omits new experimental API routes (`/api/experimental`, `/api/setup`) and the expanded CLI script list (e.g., `polish.py`).
- **3. High‑level diagram – Server section** – Server diagram does not show the additional routers introduced for experimental endpoints and scene editor integration.
- **4. Pipeline data flows – Post‑session pipeline** – Current flow misses the `--collect` mode for batch processing and the optional `polish.py` refinement step after `assemble.py`.
- **5. CLI pipelines – Script inventory** – The script table omits `polish.py` and does not list new utilities such as `scene_editor.py` hooks.
- **6. CLI pipelines – Batch API usage** – Spec lists “Supports `--batch`” for `enhance_summary.py` only; it should note that `scene_extract.py` also supports `--batch` and `--collect`.
- **7. Typical session lifecycle** – Steps 2 and 3 lack explicit mention of batch options (`--batch`, `--collect`) and the optional polish refinement step.
- **8. MCP integration – Additional read‑only functions** – Spec enumerates read‑only MCP functions but omits newer ones like `search_document` expansions and any experimental read helpers added after the last draft.
- **9. Tests – New test suites** – Spec’s test overview does not mention the `test_polish.py` suite and other recently added structural tests.
- **10. Recurring concepts – Batch mode extensions** – Spec describes three sub‑modes (`block-and-poll`, `--submit-only`, `--collect`) but does not highlight the new `--collect` usage for downstream consumption.
- **11. Detailed docs references** – Some linked documents (e.g., `session_prep_workflow.md`) have moved; the spec still points to outdated paths.
- **12. Configuration and workspace setup** – Spec’s workspace layout does not explicitly include the `notes/` directory usage clarified in recent changes, nor the updated `new_workspace.py` behavior.