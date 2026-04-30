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

## Bugs

### [ ] `party.md` misreports which PCs have formal arc score tracks

**Symptom**
`party.md` currently claims *"Only Brewbarry has a formally defined arc score
mechanic in source materials. Consider establishing formal tracks for
Vukradin, Valphine, and Soma."* The individual character sections repeat this
(e.g. Valphine's section reads *"No named arc score mechanic assigned in
source material."*).

**Reality — discovered 2026-04-11 in Phandalin campaign**
Three of four PCs already have complete, formally-defined arc score tracks
living in `<campaign>/docs/tracking/`:

| PC | Track name | File |
|----|------------|------|
| Brewbarry | The Thistle's Echo Score — Telemetry of the Stolen Watch | `The Thistle's Echo Score_ Telemetry of the Stolen Watch.md` |
| Valphine | The Searing Dawn Score | `The Searing Dawn Score.md` |
| Soma | Soma's Meril's Legacy Score | `soma-legacy.md` |
| Vukradin | **Intentionally none** — by design | — |

So `party.md` is not just incomplete — it's actively **wrong** about Valphine
and Soma, and that bad assertion then propagates downstream because later
pipeline stages (and LLM agents) read `party.md` as ground truth.

**Root cause (hypothesis)**
`party.py` (or whichever stage generates `party.md`) has no explicit mapping
from PC → tracking file. It appears to do some fuzzy scan and gives up on
anything it can't auto-associate. There is no configuration input telling
the pipeline "Valphine's arc lives in `The Searing Dawn Score.md`."

**Fix — what's needed**

1. **Introduce an explicit PC → tracking-file mapping.** Options, roughly in
   order of effort:
   - Add a `tracking_files:` block to the per-character section of
     `config.yaml` (or equivalent), listing one or more tracking file paths
     per PC.
   - Or: add a frontmatter header to each tracking file naming its owner
     (`character: Valphine`) and let `party.py` glob `docs/tracking/*.md`
     and build the map at runtime.
   - Or: convention — require `{character}_score.md` or
     `{character}-*.md` naming in `docs/tracking/`, but this is brittle
     and the Phandalin files don't all follow it.

   **Recommendation:** config block. Explicit beats convention; filenames
   are allowed to be whatever the DM wants them to be.

2. **`party.py` must consume the mapping** when generating each character's
   section. For each PC, read their tracking files, extract the track name,
   current value (if recorded), thresholds, and any current-score narrative
   hints. Render these into `party.md` instead of the current
   "no formal arc score mechanic" boilerplate.

3. **Fail loud, not quiet.** If a config lists a tracking file that doesn't
   exist, the pipeline should error — not silently fall through to "no
   mechanic."

4. **Back-fill Phandalin's `config.yaml`** once the mapping format exists.

**Acceptance criteria**
- Regenerating `party.md` for Phandalin produces accurate sections for
  Brewbarry, Valphine, and Soma that name their respective arc score tracks,
  reference the source files, and do NOT claim any of them lack a formal
  mechanic.
- The general note at the bottom of `party.md` that currently says *"Only
  Brewbarry has a formally defined arc score mechanic"* is either removed or
  rewritten to reflect reality.
- **Vukradin has no tracking file by design.** The config must support
  "intentionally no arc track" as a first-class state — not a missing file,
  not a warning. `party.md` should render Vukradin's section without an arc
  score track and without suggesting one should be created. (Design rationale:
  Vukradin is written as a character with zero internal tension — a naive
  Silver Tongue worldview that sees no conflict in his own goals. An arc
  score implies a trajectory from one state to another; that's not the
  character.)

**Related files**
- `party.py` — generator
- `make_tracking.py` — possibly relevant if tracking files are also generated
- `config/` — where the mapping config likely belongs
- Phandalin: `/home/kroussos/campaigns/Phandalin/docs/tracking/`
- Phandalin: `/home/kroussos/campaigns/Phandalin/docs/party.md` (the broken output)

### [ ] `--build-dossiers` re-sidecars extracts that are already in the canonical

**Symptom**
Re-running `planning.py --build-dossiers` against an existing dossier directory
drops `<stem>.new_notes.NNN.md` sidecars for *every* extract that mentions the
NPC, including extracts whose facts were already absorbed into the canonical
during the original synthesis. In Phandalin this produced 177 sidecars across
74 NPCs, the majority of which were redundant. Folding them in (via
`sidecar_merge_batch.py`) cost LLM tokens to re-dedupe content that had already
been deduped.

**Root cause**
`planning.py:376–382` — the only sidecar-dedup check is `if new_note_file.exists(): continue`,
i.e. "have we previously written a sidecar with this exact filename?" There's
no record of which extracts contributed to the canonical at synthesis time,
so the script can't tell "extract 006 is already in there."

Concrete trace: ser_kaelen's canonical was synthesized from extracts 6, 7, 10,
13, 16, 17, 23, 24, 29. A later run added sessions yielding extracts 31, 36,
37, 38. The re-run dropped sidecars for *all 13* extract numbers, not just
the 4 new ones.

**Fix**
1. **Persist the source-extract list on the canonical.** When synthesizing a
   fresh dossier, write `source_extracts: [6, 7, 10, ...]` into its YAML
   frontmatter alongside `name:` and `aliases:`.
2. **Read it back before sidecaring.** In the existing-dossier branch of
   `planning.py:367+`, parse `source_extracts` from the canonical's frontmatter
   and skip any `extract_num` already in that list. Only genuinely new extracts
   get sidecars.
3. **Append to the list when sidecars are merged.** When
   `sidecar_merge_batch.py` (or any future merge tool) folds a sidecar into the
   canonical, add its `extract_num` to `source_extracts` so the next
   `--build-dossiers` won't re-emit it.
4. **Update `parse_dossier()`** to return `source_extracts` alongside name and
   aliases, and **`write_dossier()`** to round-trip it.

**Acceptance criteria**
- Running `--build-dossiers` twice in a row (no new extracts between runs)
  produces zero sidecars on the second run.
- After running `sidecar_merge_batch.py` and then `--build-dossiers`, no
  sidecar is written for any extract number listed in the canonical's
  `source_extracts:`.
- Existing dossiers without `source_extracts` frontmatter still work
  (treated as "unknown — sidecar everything", current behavior).

**Status — 2026-04-18**
Phandalin's 78 dossiers already have `source_extracts: [1..38]` backfilled
via `~/.claude/skills/dossier-merge/backfill_source_extracts.py`. So once the
planning.py read-side ships, no migration is needed for that campaign.

**Related files**
- `planning.py` — `parse_dossier`, `write_dossier`, Phase 3 sidecar logic at line ~367
- `~/.claude/skills/dossier-merge/sidecar_merge_batch.py` — needs to update `source_extracts` when merging
- `~/.claude/skills/dossier-merge/backfill_source_extracts.py` — one-shot to mark existing dossiers as "all current extracts consumed"
