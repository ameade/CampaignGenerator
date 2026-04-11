# Post-Session Workflow

After each session you have a Zoom VTT transcript, a GM recap from gmassisstant.app,
and a story worth telling. This guide walks through converting those raw materials into
a finished narrated session document — rotating first-person voices from each character,
fact-checked against your campaign docs.

The full pipeline: **VTT transcript → session summary → per-scene extraction files →
review & edit → narrate each scene → assemble final doc.**

---

## Prerequisites

Before starting, you need:

- The Zoom `.vtt` transcript for the session
- The GM recap from gmassisstant.app (Scenes, NPCs, Memorable Moments sections)
- The server running with the correct campaign and session directories

---

## Step 1 — Start the Server

```bash
~/CampaignGenerator/start \
  --campaign-dir ~/campaigns/Phandalin \
  --session-dir ~/campaigns/Phandalin/summaries/20260407
```

Open **http://localhost:5000** in your browser.

`start` runs the server in the background and logs to `<session-dir>/logs/`. Use
`~/CampaignGenerator/stop` to shut it down.

If you're already running, just navigate to the URL — the server picks up the
campaign and session dirs from the last `start` call.

---

## Step 2 — Session Config

**UI: Session Workflow → Session Config**

Set `campaign_dir` and `session_dir`. Click **Apply** — this pushes the config to the
backend and derives all other paths automatically:

| Derived path | Default |
|---|---|
| VTT roleplay extractions | `<session_dir>/vtt_roleplay_extractions/` |
| VTT summary extractions | `<session_dir>/vtt_extractions/` |
| Scene extractions | `<session_dir>/scene_extractions/` |
| Output dir | `<session_dir>/` |
| Voice dir | `<campaign_dir>/voice/` |
| Examples dir | `<campaign_dir>/examples/` |
| Party doc | `<campaign_dir>/docs/party.md` |

You can override any path in the config panel on the Session Doc Editor page.

---

## Step 3 — Convert VTT Transcript

**UI: Session Workflow → VTT Summary**

**Input**: Zoom `.vtt` file + gmassisstant recap  
**Output**: `session-summary.md` + `session-roleplay.md` in `<session_dir>/`

The gmassisstant recap is the authoritative account of what happened — the VTT
extraction is anchored on it. Every scene in the recap gets corresponding dialogue
pulled from the transcript.

**Skip this step** if you already ran it for this session (files exist). The VTT
Summary step is idempotent but re-runs all API calls.

For details on what this step produces: see [GMASSISTANT_PIPELINE.md](GMASSISTANT_PIPELINE.md).

---

## Step 4 — Scene Extraction

**UI: Session Workflow → Scene Extraction**

**Input**: `session-summary.md` + roleplay extractions (from Step 3)  
**Output** in `scene_extractions/`:
- `plan.md` — which character narrates which scene
- `NN_narrator_scene.md` — per-scene extraction files with action beats + assigned quotes
- `enhanced_sections.md` — enhanced Memorable Moments, NPCs, Items, Spells, Consistency Notes

This runs Passes 1–4 of `session_doc.py`:
1. Consistency check (silent — flags contradictions between recap and campaign docs)
2. Enhance structured sections → `enhanced_sections.md`
3. Narrative plan → `plan.md`
4. Per-scene character extraction → `NN_narrator_scene.md` files

**Re-running**: overwrites all extraction files. Re-run if the recap changed or
extraction quality was poor. Existing narrations (`scene{N}.md`) are not affected
until you re-narrate.

For details on what each extraction file contains: see [SCENE_EXTRACTION.md](SCENE_EXTRACTION.md).

---

## Step 5 — Review & Edit Extractions

**UI: Session Workflow → Session Doc Editor**

This is the human checkpoint. Each extraction file is the direct input to narration —
what's in the file is what the narrator works from. Review each scene before narrating.

**For each scene:**

1. Click the scene in the left panel to load it
2. Read the **Extraction tab** — action beats (lines starting with `-`) and dialogue
   quotes beneath each beat
3. Check the **Session Notes tab** for additional quotes and moments from
   `enhanced_sections.md` — copy anything missing into the Extraction tab
4. Check the **Quote Ledger** (right panel) for unassigned VTT quotes — if a quote
   belongs here, add it manually to the extraction
5. Edit the extraction directly in the textarea; click **Save**

**What good extractions look like:**

```
- The party discovers the treasure slag fused in residium-infused ice
Soma: "I don't think this was natural — something planar did this."
Vukradin: "So we can't spend it. I have opinions about this."

- Brewbarry kills the paralyzed veteran
Brewbarry: "I hope this will inspire a song in your new music studio."
```

Each action beat starts with `-`. Dialogue quotes follow directly beneath. The
narrator uses this grouped format to weave beat and quotes together.

**Typora**: click **Edit in Typora** to open the extraction in Typora on Windows
for easier editing of long files.

---

## Step 6 — Narrate Scenes

**UI: Session Doc Editor → Narrate button (per scene)**

With a scene loaded, click **Narrate**. This streams `session_doc.py --from-extractions --scene N`
and writes output to `scene{N}.md`.

**Toolbar options:**

| Toggle | What it does |
|---|---|
| **Prose** | Strips mechanical language from action beats (damage numbers → impact weight; spell slots → effort drawn on) |
| **Memories** | Injects campaign history so the narrator can draw on past events as reflections |
| **Enhanced** | Passes `enhanced_sections.md` to the narration as scene context |

Watch the narration stream in the output panel. When it finishes, click
**Open narration in Typora** to read it.

**Re-narrating**: edit the extraction, save, click Narrate again. The output file
is overwritten. Other scenes are not affected.

---

## Step 7 — Assemble Final Doc

**UI: Session Doc Editor → Assemble Doc button** (bottom of scene list)

Concatenates all `scene{N}.md` files into `<session-name>-doc.md` in the output dir.
Scenes are joined with `---` dividers. Missing scenes (not yet narrated) are listed
in the status message but don't block assembly — you get a partial doc.

**Open assembled doc in Typora**: the Assemble button shows a Typora link after assembly.

---

## Re-running Individual Steps

| Situation | What to do |
|---|---|
| Re-narrate one scene after editing extraction | Load scene → edit extraction → Save → Narrate |
| Extraction was poor quality | Scene Extraction page → re-run (overwrites all extraction files) |
| VTT transcript changed | VTT Summary → re-run → Scene Extraction → re-run |
| Want to adjust narration style only | Change Prose/Memories toggles → Narrate again |
| One extraction file manually edited | Just click Narrate — `--from-extractions` uses the file as-is |
| Plan was wrong (wrong narrator for a scene) | Edit `scene_extractions/plan.md` directly → re-run extraction |

---

## Further Reading

- [SCENE_EXTRACTION.md](SCENE_EXTRACTION.md) — what each extraction file contains and how the plan works
- [SESSION_DOC_PIPELINE.md](SESSION_DOC_PIPELINE.md) — deep dive into all five passes, narration modes, and engineering decisions
- [GMASSISTANT_PIPELINE.md](GMASSISTANT_PIPELINE.md) — why the gmassisstant recap is the authoritative anchor
- [PLAYER_VOICE_GUIDE.md](PLAYER_VOICE_GUIDE.md) — how players write voice files that shape their narrator's prose
