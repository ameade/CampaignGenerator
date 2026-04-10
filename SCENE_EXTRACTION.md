# Scene Extraction

Scene Extraction is Step 3 of the Session Workflow. It takes raw VTT data and a GM recap and produces a set of **per-character extraction files** that are then reviewed, edited, and fed into the narration step (Pass 5) to produce the final session document.

It runs Passes 1–4 of `session_doc.py`.

---

## What it produces

After running, the output directory (default: `scene_extractions/`) contains:

### `plan.md`
The narrative plan — which character narrates which part of the session.

```markdown
## Section 1
narrator: Vukradin
chunks: 1
focus: Vukradin arrives at the glacier and confronts the Drake for the first time.

## Section 2
narrator: Soma
chunks: 2
focus: Soma navigates the collapse and finds the hidden passage.
```

Each section has:
- **narrator** — the character whose voice narrates this section
- **chunks** — which VTT chunk(s) they cover (e.g. `1`, `2`, `1-2`)
- **scene** — scene name, in scene-by-scene mode
- **focus** — one-sentence summary of their emotional arc in this section

### Per-scene extraction files
One file per section in the plan, named `NN_narrator[_scene].md`:

```
01_vukradin.md
02_soma.md
03_valphine_stone_giants.md
04_brewbarry.md
```

Each file contains the raw material for that character's narration: verbatim dialogue exchanges, action beats, and environmental moments pulled from the VTT extractions. This is what you edit before narrating.

---

## The four passes

### Pass 1 — Consistency Check
Compares the GM recap against campaign context (campaign state, world state, party document). Flags factual errors, contradictions, or lore violations and prints them to the console. Does not modify any files.

### Pass 2 — Enhance Structured Sections
Rewrites **Memorable Moments** (preserving existing entries, adding moments from the VTT that weren't captured) and appends **Consistency Notes** for unresolved issues. Preserves Scenes, NPCs, Locations, Items, and Spells verbatim.

> **Known gap — Session Doc Editor:** Pass 2 output is held in memory and only written to the final document when `session_doc.py` runs end-to-end. In the editor workflow, `--extract-only` causes the script to exit before assembly, so the enhanced sections are discarded. The Session Doc Editor's **Assemble** step concatenates the narrated scene files only — it does not include the Pass 2 output, nor does it fall back to the original structured sections from the GM recap. The assembled document currently contains narration only. This is a known limitation to be addressed.

### Pass 3 — Narrative Plan
Reads all the VTT roleplay extractions, session extractions, and the character roster, then assigns each character a portion of the session to narrate. Produces `plan.md`. Every character in the roster must appear at least once; every chunk of the session must be covered.

### Pass 4 — Per-Scene Extraction
For each section in the plan, runs a focused extraction pass that pulls only that narrator's relevant moments from their assigned VTT chunks. The model extracts three types of content:
- **Dialogue exchanges** — verbatim lines with full back-and-forth and all parties
- **Action beats** — combat, physical challenges, discoveries
- **Environmental moments** — travel, atmosphere, sensory details

Each extraction is saved as a separate file.

---

## Input files

| File / Directory | What it is | Required? |
|---|---|---|
| **GMassistant recap** | Session structure: Summary, Memorable Moments, Scenes, NPCs, etc. | Yes |
| **`vtt_roleplay_extractions/`** | Chunked roleplay moments from the VTT (dialogue, character voice) | Yes |
| **Characters** | Comma-separated narrator roster (e.g. `Vukradin, Soma, Valphine, Brewbarry`) | Yes |
| `vtt_extractions/` | Chunked session actions/events for richer action context | Optional |
| `session-summary.md` | Synthesized authoritative event log from vtt_summary.py | Optional |
| `campaign_state.md` | Completed content, current NPC states | Optional (for Pass 1) |
| `world_state.md` | World lore, factions, places | Optional (for Pass 1) |
| `party.md` | Character backstories, personalities | Optional |
| Voice files | Per-character speaking style guides | Optional |
| Examples | Handcrafted narration samples for style reference | Optional |

All paths other than the recap and roleplay directory are auto-populated from the Session Config page.

---

## Output directory layout

```
scene_extractions/
    plan.md                         ← narrative plan (Pass 3)
    01_vukradin.md                  ← extraction for section 1
    02_soma.md                      ← extraction for section 2
    03_valphine_stone_giants.md     ← extraction for section 3 (scene mode)
    04_brewbarry.md                 ← extraction for section 4
```

In scene-by-scene mode (`--by-scene`), filenames include the slugified scene name. In chunk mode (default), they are just `NN_narrator.md`.

---

## What to do after extraction

Extraction files are **meant to be edited** before narration. Pass 5 (narration) reads them as-is, so what's in them is what gets narrated.

Typical edits:
- Add missing dialogue lines you noticed in the VTT
- Remove lines that were extracted from the wrong section
- Add a `tokens: 6000` header at the top to override the token estimate for a dense scene
- Reorder moments for better narrative flow

The Scene Doc Editor (Step 4) shows each scene alongside the VTT source and Quote Ledger to make this review easy. Once you're satisfied with an extraction file, click **Narrate** to run Pass 5 for just that scene.

---

## Narration modes

### Chunk mode (default)
Each character narrates a sequential portion of the session. Sections cover chronological chunks of the VTT. Best when the session naturally divides by time (combat → travel → roleplay).

### Scene mode (`--by-scene`)
Each scene in the recap is narrated by a different character, rotating through the roster. Requires the recap to have a `## Scenes` section. Best when you want multiple POVs across the same events. Produces narrower extraction files, which keeps per-scene narration costs low.

---

## Re-running

### Re-run extraction for one scene
After editing a plan section or adjusting roleplay extractions, re-run Pass 4 for a single scene:

```bash
python session_doc.py gm-assist.md \
    --roleplay-extract-dir vtt_roleplay_extractions/ \
    --by-scene --extract-dir scene_extractions/ \
    --extract-only --scene 3 --output /dev/null
```

### Re-run narration from existing extractions
Once extractions are reviewed and edited, run Pass 5 without re-extracting:

```bash
python session_doc.py gm-assist.md \
    --roleplay-extract-dir vtt_roleplay_extractions/ \
    --by-scene --from-extractions scene_extractions/ \
    --output session-doc.md
```

### Use a hand-edited plan
If the auto-generated plan assigns characters poorly, edit `scene_extractions/plan.md` directly and re-run:

```bash
python session_doc.py gm-assist.md \
    --roleplay-extract-dir vtt_roleplay_extractions/ \
    --by-scene --plan-file scene_extractions/plan.md \
    --extract-dir scene_extractions/ \
    --extract-only --output /dev/null
```
