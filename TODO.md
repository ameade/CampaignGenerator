# CampaignGenerator — TODO

## UI

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

