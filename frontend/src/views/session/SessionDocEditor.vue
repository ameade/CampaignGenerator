<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useConfigStore } from '../../stores/config'
import { resolvePath, resolvePathList } from '../../utils/paths'
import { apiFetch, apiPut, apiPost } from '../../api/client'
import { connectSSE } from '../../api/sse'
import PathField from '../../components/shared/PathField.vue'
import MultiPathField from '../../components/shared/MultiPathField.vue'
import SceneList from '../../components/scene-editor/SceneList.vue'
import type { Scene } from '../../components/scene-editor/SceneList.vue'
import ExtractionEditor from '../../components/scene-editor/ExtractionEditor.vue'
import NarrationOutput from '../../components/scene-editor/NarrationOutput.vue'
import VttPanel from '../../components/scene-editor/VttPanel.vue'

const config = useConfigStore()

// ── Editor config ─────────────────────────────────────────────────
const configured = ref(false)
const configError = ref('')

// Form fields for editor config
const session = ref('')
const outputDir = ref('')
const sessionSummary = ref('')
const sceneExtractionsDir = ref('')
const narrationDir = ref('')
const party = ref('')
const voiceDir = ref('')
const examplesDir = ref('')
const characters = ref('')
const context = ref('')
const narrateTokens = ref(16000)
const proseMode = ref(false)
const reflections = ref(false)
const narrationGenre = ref('')
const useEnhancedSections = ref(true)
const showOverrides = ref(false)
// Batch mode toggle for Stage 1 / Stage 2 (Anthropic Message Batches API,
// 50% off list price; replaces token streaming with poll-progress lines).
const useBatch = ref(false)
// LLM backend selector — narrate + scrub honor this; Stage 1/2/Plan stay
// on Anthropic regardless (their paths use tool-use which the OpenAI-
// compat adapter doesn't support).
const backend = ref<'anthropic' | 'dgx'>('anthropic')

function loadConfigFields() {
  const v = config.values
  session.value = v.sd_session || ''
  outputDir.value = v.sd_output_dir || v.session_dir || ''
  sessionSummary.value = v.sd_session_summary || 'session-summary.md'
  sceneExtractionsDir.value = v.sd_scene_extractions_dir || 'scene_extractions_new'
  narrationDir.value = v.sd_narration_dir || 'narration'
  party.value = v.sd_party || ''
  voiceDir.value = v.sd_voice_dir || v.session_doc_voice_dir || ''
  examplesDir.value = v.sd_examples_dir || v.session_doc_examples_dir || ''
  characters.value = v.sd_characters || v.session_doc_characters || ''
  context.value = v.vtt_context || ''
  narrateTokens.value = v.sd_narrate_tokens || v.session_doc_narrate_tokens || 16000
  proseMode.value = v.sd_prose_mode || false
  reflections.value = v.sd_reflections || false
  narrationGenre.value = v.sd_narration_genre || ''
  useEnhancedSections.value = v.sd_use_enhanced_sections !== false
  useBatch.value = v.sd_batch === true
  backend.value = v.sd_backend === 'dgx' ? 'dgx' : 'anthropic'
}

async function persistBatchToggle() {
  // Mirror into the legacy overlay so other views on the flat shape see
  // the new value immediately, then persist via the typed section so it
  // survives a restart.
  config.values.sd_batch = useBatch.value
  try {
    await config.updateSection('session_doc', { batch: useBatch.value })
  } catch {
    /* non-fatal: toggle will still apply to in-flight calls */
  }
}

async function setBackend(b: 'anthropic' | 'dgx') {
  if (backend.value === b) return
  backend.value = b
  config.values.sd_backend = b
  try {
    await config.updateSection('session_doc', { backend: b })
  } catch {
    /* non-fatal — the next subprocess will still read the in-memory CONFIG */
  }
}

const contextFiles = computed(() => resolvePathList(context.value))

const configReady = computed(() =>
  !!(session.value.trim() && sceneExtractionsDir.value.trim())
)

async function applyConfig() {
  configError.value = ''
  const editorConfig = {
    session: resolvePath(session.value),
    output_dir: resolvePath(outputDir.value) || config.cwd || '',
    session_summary: resolvePath(sessionSummary.value) || undefined,
    scene_extractions_dir: resolvePath(sceneExtractionsDir.value) || undefined,
    narration_dir: resolvePath(narrationDir.value) || undefined,
    party: resolvePath(party.value) || undefined,
    voice_dir: resolvePath(voiceDir.value) || undefined,
    examples: resolvePath(examplesDir.value) || undefined,
    characters: characters.value || undefined,
    context: contextFiles.value.length ? contextFiles.value : [],
    narrate_tokens: narrateTokens.value || undefined,
    prose_mode: proseMode.value || undefined,
    reflections: reflections.value || undefined,
    narration_genre: narrationGenre.value.trim() || undefined,
    use_enhanced_sections: useEnhancedSections.value,
    work_dir: config.cwd,
  }
  try {
    await apiPut('/api/editor/config', editorConfig)
    configured.value = true
    await loadScenes()
    await checkAssembled()
    await loadEnhancedSections()
  } catch (e: any) {
    configError.value = `Failed to configure editor: ${e.message}`
  }
}

async function loadEnhancedSections() {
  const data = await apiFetch('/api/editor/enhanced-sections')
  enhancedContent.value = data.content || ''
  hasEnhanced.value = data.exists || false
}

// ── Scene state ───────────────────────────────────────────────────
const scenes = ref<Scene[]>([])
const currentScene = ref<number | null>(null)
const extractionContent = ref('')
const sceneLabel = ref('')
const estimatedTokens = ref<number | null>(null)
const hasExtraction = ref(false)
const narrating = ref(false)
// One scrub at a time — covers both per-scene and Scrub-All; both buttons
// disable while it's true.
const scrubbing = ref(false)
const extracting = ref(false)
const enhancing = ref(false)
const planning = ref(false)
const narrationOutput = ref('')
const statusMsg = ref('')
const enhancedContent = ref('')
const hasEnhanced = ref(false)
const assembledExists = ref(false)

const activeSSE = ref<EventSource | null>(null)

// ── Scene navigation ─────────────────────────────────────────────

async function loadScenes() {
  try {
    scenes.value = await apiFetch('/api/editor/scenes')
  } catch {
    scenes.value = []
  }
}

const currentSceneReviewed = computed(() => {
  if (currentScene.value == null) return false
  const s = scenes.value.find(sc => sc.index === currentScene.value)
  return !!s?.reviewed
})

async function setReviewed(reviewed: boolean) {
  if (currentScene.value == null) return
  await apiPut(`/api/editor/reviewed/${currentScene.value}`, { reviewed })
  await loadScenes()
}

async function selectScene(n: number) {
  currentScene.value = n
  await loadEditorScene(n)
}

async function loadEditorScene(n: number) {
  const data = await apiFetch(`/api/editor/extraction/${n}`)
  extractionContent.value = data.content || ''
  hasExtraction.value = data.exists
  sceneLabel.value = data.scene_label || `Scene ${n}`
  estimatedTokens.value = data.estimated_tokens || null

  try {
    await apiFetch(`/api/editor/output/${n}`)
  } catch { /* no output yet */ }
}

async function saveExtraction(content: string) {
  if (currentScene.value === null) return
  extractionContent.value = content
  await apiPut(`/api/editor/extraction/${currentScene.value}`, { content })
  await loadScenes()
}

async function reload() {
  if (currentScene.value !== null) {
    await loadEditorScene(currentScene.value)
    setStatus('Reloaded from disk.')
  }
}

async function narrate() {
  if (currentScene.value === null || narrating.value) return
  await saveExtraction(extractionContent.value)

  narrating.value = true
  narrationOutput.value = ''
  setStatus('Running narration...')

  activeSSE.value = connectSSE(`/api/editor/narrate/${currentScene.value}`, {
    onData(text) {
      narrationOutput.value += text
    },
    onDone(rc) {
      activeSSE.value = null
      narrating.value = false
      setStatus(rc === 0 ? 'Done.' : 'Narration failed.')
      loadScenes()
    },
    onError() {
      activeSSE.value = null
      narrating.value = false
      setStatus('Stream error — check terminal.')
    },
  })
}

async function scrubScene() {
  if (currentScene.value === null || scrubbing.value || narrating.value) return
  scrubbing.value = true
  narrationOutput.value = ''
  setStatus(`Scrubbing scene ${currentScene.value}...`)

  activeSSE.value = connectSSE(`/api/editor/scrub/${currentScene.value}`, {
    onData(text) {
      narrationOutput.value += text
    },
    onDone(rc) {
      activeSSE.value = null
      scrubbing.value = false
      setStatus(rc === 0
        ? `Scrubbed scene ${currentScene.value} — .scrubbed.md written.`
        : 'Scrub failed.')
      loadScenes()
    },
    onError() {
      activeSSE.value = null
      scrubbing.value = false
      setStatus('Stream error — check terminal.')
    },
  })
}

async function scrubAll() {
  if (scrubbing.value || narrating.value) return
  scrubbing.value = true
  narrationOutput.value = ''
  setStatus('Scrubbing all scene narrations...')

  activeSSE.value = connectSSE('/api/editor/scrub-all', {
    onData(text) {
      narrationOutput.value += text
    },
    onDone(rc) {
      activeSSE.value = null
      scrubbing.value = false
      setStatus(rc === 0
        ? 'Scrub-All complete — .scrubbed.md files written.'
        : 'Scrub-All failed.')
      loadScenes()
    },
    onError() {
      activeSSE.value = null
      scrubbing.value = false
      setStatus('Stream error — check terminal.')
    },
  })
}

async function runExtract() {
  if (extracting.value || narrating.value || enhancing.value || planning.value) return
  extracting.value = true
  narrationOutput.value = ''
  setStatus(useBatch.value
    ? 'Submitting Stage 2 batch (Message Batches API)...'
    : 'Re-extracting quotes (Stage 2)...')

  // force=1 — clicking Re-Extract always means "do the work" (overwrite
  // existing files; backend snapshots prior content to .prev for diff view).
  const url = useBatch.value
    ? '/api/editor/extract?batch=1&force=1'
    : '/api/editor/extract?force=1'
  activeSSE.value = connectSSE(url, {
    onData(text) {
      narrationOutput.value += text
    },
    onDone(rc) {
      activeSSE.value = null
      extracting.value = false
      setStatus(rc === 0 ? 'Re-extraction complete.' : 'Re-extraction failed.')
      loadScenes()
    },
    onError() {
      activeSSE.value = null
      extracting.value = false
      setStatus('Stream error — check terminal.')
    },
  })
}

async function runPlan() {
  if (planning.value || enhancing.value || extracting.value || narrating.value) return
  planning.value = true
  narrationOutput.value = ''
  setStatus('Planning & consistency check (Stage 3)...')

  activeSSE.value = connectSSE('/api/editor/plan', {
    onData(text) {
      narrationOutput.value += text
    },
    onDone(rc) {
      activeSSE.value = null
      planning.value = false
      setStatus(rc === 0
        ? 'Plan & check complete — plan.md + enhanced_sections.md saved.'
        : 'Plan & check failed.')
      loadScenes()
      loadEnhancedSections()
    },
    onError() {
      activeSSE.value = null
      planning.value = false
      setStatus('Stream error — check terminal.')
    },
  })
}

async function runEnhance() {
  if (enhancing.value || extracting.value || narrating.value || planning.value) return
  enhancing.value = true
  narrationOutput.value = ''
  setStatus(useBatch.value
    ? 'Submitting Stage 1 batch (Message Batches API)...'
    : 'Enhancing summary (Stage 1)...')

  const url = useBatch.value ? '/api/editor/enhance?batch=1' : '/api/editor/enhance'
  activeSSE.value = connectSSE(url, {
    onData(text) {
      narrationOutput.value += text
    },
    onDone(rc) {
      activeSSE.value = null
      enhancing.value = false
      setStatus(rc === 0 ? 'Stage 1 complete — review session-summary.md.' : 'Stage 1 failed.')
    },
    onError() {
      activeSSE.value = null
      enhancing.value = false
      setStatus('Stream error — check terminal.')
    },
  })
}

async function openTypora(type: string) {
  if (currentScene.value === null) return
  try {
    await apiPost(`/api/editor/open/${type}/${currentScene.value}`)
  } catch {
    setStatus('File not found.')
  }
}

async function assembleDoc() {
  setStatus('Assembling session doc...')
  try {
    const data = await apiPost('/api/editor/assemble')
    if (data.ok) {
      setStatus(`Saved → ${data.filename} (${data.scenes_included} scenes)`)
      assembledExists.value = true
    } else {
      setStatus(`Assembly failed: ${data.error}`)
    }
  } catch {
    setStatus('Assembly error.')
  }
}

async function openAssembled() {
  try {
    await apiPost('/api/editor/open/assembled/0')
  } catch {
    setStatus('Could not open assembled file.')
  }
}

function setStatus(msg: string) {
  statusMsg.value = msg
  if (msg) setTimeout(() => { if (statusMsg.value === msg) statusMsg.value = '' }, 5000)
}

function clearOutput() {
  narrationOutput.value = ''
}

async function checkAssembled() {
  try {
    const data = await apiFetch('/api/editor/assembled-exists')
    assembledExists.value = data.exists
  } catch { /* ignore */ }
}

function backToConfig() {
  configured.value = false
}

// ── Init ──────────────────────────────────────────────────────────
onMounted(async () => {
  loadConfigFields()

  // Check if editor is already configured (e.g. from CLI startup)
  try {
    const existing = await apiFetch('/api/editor/config')
    if (existing.session && existing.scene_extractions_dir) {
      configured.value = true
      await loadScenes()
      await checkAssembled()
      await loadEnhancedSections()
      return
    }
  } catch { /* not configured yet */ }

  // Auto-apply if we have enough from the config store
  if (configReady.value) {
    await applyConfig()
  }
})
</script>

<template>
  <!-- Config panel (shown when not yet configured) -->
  <div v-if="!configured" class="config-panel">
    <div class="page">
      <div class="page-header">
        <h2>Session Doc Editor</h2>
        <p class="subtitle">
          Configure the editor with your session files, then edit extractions and narrate scene by scene.
        </p>
      </div>

      <div class="form-grid">
        <!-- Required -->
        <div class="form-section">
          <PathField v-model="session" label="GMassistant recap file" required
            help="The structured session notes (e.g. gm-assist.md). Stage 1 input." />
          <PathField v-model="sessionSummary" label="Session summary file"
            help="Stage 1 output (session-summary.md). Created/updated by the Enhance Summary button." />
          <PathField v-model="sceneExtractionsDir" label="Scene extractions directory (Stage 2)"
            help="Per-scene verbatim quote files (NN_<slug>.md). Created by Re-Extract Quotes." />
          <PathField v-model="narrationDir" label="Narration directory (Stage 3)"
            help="Per-scene narration output (session_doc_scene_NN_<slug>.md). Created by Narrate." />
        </div>

        <div class="form-section">
          <PathField v-model="outputDir" label="Output directory"
            help="Where sceneN.md files and the assembled doc are saved." />
          <div class="field">
            <label class="field-label">Characters</label>
            <input type="text" class="field-input" v-model="characters"
              placeholder="Zalthir, Grygum, Daz, Thorin" />
            <div class="field-help">Comma-separated narrator roster (used by Extract)</div>
          </div>
          <div class="field">
            <label class="field-label">Narration token limit</label>
            <input type="number" class="field-input" v-model.number="narrateTokens"
              min="1000" step="500" />
            <div class="field-help">Per-scene output cap (default: 16000). Override per-scene with "tokens: N" in extraction file.</div>
          </div>
          <div class="field">
            <label class="field-label checkbox-label">
              <input type="checkbox" v-model="proseMode" />
              Prose mode
            </label>
            <div class="field-help">Strip all mechanical language and GM framing. GM descriptions become the narrator's direct perception; dice rolls and HP become narrative consequence.</div>
          </div>
          <div class="field">
            <label class="field-label">Narration genre</label>
            <input type="text" class="field-input" v-model="narrationGenre"
              placeholder='e.g. First-person comic-noir fantasy memoir — observational, dry, irony-forward' />
            <div class="field-help">
              One-line genre/register directive injected at the top of the Pass-5
              narration prompt. Leave blank to use the default neutral prompt.
            </div>
          </div>
          <div class="field">
            <label class="field-label checkbox-label">
              <input type="checkbox" v-model="useEnhancedSections" />
              Use enhanced scene data
            </label>
            <div class="field-help">
              When on, narration receives the corrected event list from
              <code>enhanced_sections.md</code> (Pass 2 output) and campaign context files.
              Turn off to narrate from the extraction file only — useful for comparing results.
            </div>
          </div>
          <div class="field">
            <label class="field-label checkbox-label">
              <input type="checkbox" v-model="reflections" />
              Reflections
            </label>
            <div class="field-help">
              Inject campaign history (context files) into narration as memories and backstory references.
              Useful when the scene calls for a character to reflect on past events or relationships.
            </div>
          </div>
        </div>

        <!-- Optional overrides -->
        <div class="form-section">
          <button class="btn-neutral btn-sm" @click="showOverrides = !showOverrides">
            {{ showOverrides ? 'Hide' : 'Show' }} path overrides
          </button>

          <div v-if="showOverrides" class="advanced-panel">
            <PathField v-model="party" label="Party document"
              help="party.md — backstory, personality, relationships." />
            <PathField v-model="voiceDir" label="Voice files directory"
              help="Directory of {name}_voice.md files." />
            <PathField v-model="examplesDir" label="Examples directory"
              help="Handcrafted .md style references for narration." />
            <MultiPathField v-model="context" label="Campaign context files"
              help="campaign_state.md, world_state.md — used by extraction passes and injected into narration as campaign context." />
          </div>
        </div>

        <div v-if="configError" class="error-box">{{ configError }}</div>

        <div class="form-section">
          <button
            class="btn-primary"
            :disabled="!configReady"
            @click="applyConfig"
          >
            Open Editor
          </button>
          <span v-if="!configReady" class="field-help" style="margin-left:8px">
            Fill in the required fields above.
          </span>
        </div>
      </div>
    </div>
  </div>

  <!-- Editor (shown after config is applied) -->
  <div v-else class="session-editor">
    <!-- Header -->
    <header class="editor-global-header">
      <h1>Session Doc</h1>

      <span class="status-msg">{{ statusMsg }}</span>

      <span class="stage-group">
        <label
          class="batch-toggle"
          title="Submit Stage 1 / Stage 2 via Anthropic's Message Batches API (50% off list price). Replaces token streaming with poll-progress lines; usually finishes in minutes (24h SLA worst case)."
        >
          <input
            type="checkbox"
            v-model="useBatch"
            @change="persistBatchToggle"
          />
          Batch
        </label>
      </span>

      <span class="stage-group backend-group" title="Backend for Narrate + Scrub. Stage 1/2/3 always use Anthropic (tool-use paths).">
        <span class="stage-label">Backend</span>
        <button
          class="btn-sm backend-btn"
          :class="{ active: backend === 'anthropic' }"
          @click="setBackend('anthropic')"
        >Anthropic</button>
        <button
          class="btn-sm backend-btn"
          :class="{ active: backend === 'dgx' }"
          @click="setBackend('dgx')"
        >DGX</button>
      </span>

      <span class="stage-group">
        <span class="stage-label">Stage 1</span>
        <button
          class="btn-neutral btn-sm"
          :disabled="enhancing || extracting || narrating || planning"
          @click="runEnhance"
          title="Stage 1 — rebuild session-summary.md from VTT + gm-assist.md"
        >{{ enhancing ? 'Enhancing…' : 'Enhance Summary' }}</button>
      </span>

      <span class="stage-group">
        <span class="stage-label">Stage 2</span>
        <button
          class="btn-neutral btn-sm"
          :disabled="enhancing || extracting || narrating || planning"
          @click="runExtract"
          title="Stage 2 — rebuild per-scene quote files from session-summary.md"
        >{{ extracting ? 'Re-extracting…' : 'Re-Extract Quotes' }}</button>
      </span>

      <span class="stage-group">
        <span class="stage-label">Stage 3</span>
        <button
          class="btn-neutral btn-sm"
          :disabled="planning || enhancing || extracting || narrating"
          @click="runPlan"
          title="Stage 3 — consistency check + plan + enhanced sections (run once per session, cached for Narrate)"
        >{{ planning ? 'Planning…' : 'Plan &amp; Check' }}</button>
      </span>

      <span class="stage-group">
        <span class="stage-label">Stage 4½</span>
        <button
          class="btn-success btn-sm"
          :disabled="scrubbing || narrating"
          @click="scrubAll"
          title="Run the second-pass mechanical scrub over every scene narration in narration_dir."
        >{{ scrubbing ? 'Scrubbing…' : 'Scrub All' }}</button>
      </span>

      <span class="stage-group">
        <span class="stage-label">Final</span>
        <button class="btn-neutral btn-sm" @click="assembleDoc">
          Assemble Doc
        </button>
        <button
          v-if="assembledExists"
          class="btn-neutral btn-sm"
          @click="openAssembled"
        >Open in Typora</button>
      </span>

      <button
        class="btn-neutral btn-sm config-btn"
        @click="backToConfig"
      >Config</button>
    </header>

    <!-- Three-column layout -->
    <div class="columns">
      <!-- Left: scene list -->
      <SceneList
        :scenes="scenes"
        :current-scene="currentScene"
        @select="selectScene"
      />

      <!-- Center: extraction editor + narration output -->
      <div class="center-col">
        <ExtractionEditor
          :extraction-content="extractionContent"
          :enhanced-content="enhancedContent"
          :has-enhanced="hasEnhanced"
          :scene-label="sceneLabel"
          :estimated-tokens="estimatedTokens"
          :default-narrate-tokens="narrateTokens"
          :has-extraction="hasExtraction"
          :current-scene="currentScene"
          :narrating="narrating"
          :extracting="extracting"
          :scrubbing="scrubbing"
          :prose-mode="proseMode"
          :reflections="reflections"
          :use-enhanced-sections="useEnhancedSections"
          :reviewed="currentSceneReviewed"
          @save-extraction="saveExtraction"
          @reload="reload"
          @narrate="narrate"
          @scrub="scrubScene"
          @open-typora="openTypora"
          @update:extraction-content="extractionContent = $event"
          @update:prose-mode="proseMode = $event; apiPut('/api/editor/config', { prose_mode: $event || undefined })"
          @update:reflections="reflections = $event; apiPut('/api/editor/config', { reflections: $event || undefined })"
          @update:use-enhanced-sections="useEnhancedSections = $event"
          @update:reviewed="setReviewed"
          @load-enhanced="loadEnhancedSections"
        />
        <NarrationOutput
          :output="narrationOutput"
          :current-scene="currentScene"
          @clear="clearOutput"
        />
      </div>

      <!-- Right: VTT source -->
      <div class="right-panel">
        <VttPanel />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Config panel styles */
.config-panel {
  height: 100%;
  overflow-y: auto;
}
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.checkbox-label input { accent-color: var(--mauve); }
.page { padding: 20px 24px; max-width: 700px; }
.page-header { margin-bottom: 20px; }
.page-header h2 { font-size: 16px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
.subtitle { font-size: 12px; color: var(--text-muted); }

.form-grid { display: flex; flex-direction: column; gap: 16px; }
.form-section {
  padding-bottom: 12px;
  border-bottom: 1px solid var(--bg-surface0);
}
.form-section:last-child { border-bottom: none; }

.field { margin-bottom: 10px; }
.field-label {
  display: block; font-size: 11px; font-weight: 600;
  color: var(--text-sub); margin-bottom: 3px;
}
.field-input {
  width: 100%; padding: 6px 8px; border-radius: 4px;
  border: 1px solid var(--bg-surface1); background: var(--bg-base);
  color: var(--text); font-family: var(--mono); font-size: 11px;
  outline: none; box-sizing: border-box;
}
.field-input:focus { border-color: var(--mauve); }
.field-help { font-size: 10px; color: var(--text-muted); margin-top: 2px; }

.advanced-panel {
  margin-top: 10px; padding: 10px;
  background: var(--bg-mantle); border-radius: 4px;
}

.error-box {
  padding: 10px 14px; background: #3a1e1e; border-radius: 4px;
  font-size: 11px; color: var(--red); line-height: 1.5;
}

/* Editor styles */
.session-editor {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.editor-global-header {
  background: var(--bg-mantle);
  border-bottom: 1px solid var(--bg-surface0);
  padding: 8px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.editor-global-header h1 {
  font-size: 13px;
  font-weight: 700;
  color: var(--mauve);
}

.status-msg {
  font-size: 11px;
  color: var(--blue);
  margin-left: auto;
}

.stage-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-left: 1px solid var(--bg-surface0);
}
.stage-group:first-of-type {
  margin-left: 4px;
}
.stage-label {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-muted);
  margin-right: 2px;
}
.config-btn {
  margin-left: 4px;
}

.batch-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  cursor: pointer;
  user-select: none;
}
.batch-toggle input {
  cursor: pointer;
  margin: 0;
}

.backend-btn {
  background: transparent;
  border: 1px solid var(--bg-surface0);
  color: var(--text-muted);
  padding: 2px 8px;
  font-size: 11px;
  cursor: pointer;
  border-radius: 3px;
}
.backend-btn.active {
  background: var(--accent, #4a9eff);
  color: white;
  border-color: var(--accent, #4a9eff);
}

.columns {
  display: grid;
  grid-template-columns: 220px 1fr 320px;
  flex: 1;
  overflow: hidden;
}

.center-col {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.right-panel {
  background: var(--bg-mantle);
  border-left: 1px solid var(--bg-surface0);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
