<script setup lang="ts">
export interface Scene {
  index: number
  narrator: string
  scene: string
  focus: string
  has_extraction: boolean
  has_output: boolean
  has_scrubbed?: boolean
  filename: string
  reviewed?: boolean
}

defineProps<{
  scenes: Scene[]
  currentScene: number | null
}>()

const emit = defineEmits<{
  select: [index: number]
}>()
</script>

<template>
  <div class="scenes">
    <h2>Scenes</h2>
    <div class="scene-list">
      <div v-if="scenes.length === 0" class="empty-msg">
        No plan yet.<br>
        Click <b class="mauve">Extract</b> in the header<br>
        to run passes 1-4.
      </div>
      <div
        v-for="s in scenes"
        :key="s.index"
        class="scene-item"
        :class="{ active: currentScene === s.index }"
        @click="emit('select', s.index)"
      >
        <div class="num">Scene {{ s.index }}</div>
        <div class="narrator">{{ s.narrator }}</div>
        <div class="sname">{{ s.scene || '—' }}</div>
        <div class="dots" :aria-label="`extract ${s.has_extraction ? 'done' : 'pending'}, review ${s.reviewed ? 'done' : 'pending'}, narrate ${s.has_output ? 'done' : 'pending'}, scrub ${s.has_scrubbed ? 'done' : 'pending'}`">
          <span
            class="dot"
            :class="{ ok: s.has_extraction }"
            title="Stage 2 — extraction file present"
          >E</span>
          <span
            class="dot"
            :class="{ ok: s.reviewed }"
            title="Order reviewed by GM"
          >R</span>
          <span
            class="dot"
            :class="{ ok: s.has_output }"
            title="Stage 4 — narration file present"
          >N</span>
          <span
            class="dot"
            :class="{ ok: s.has_scrubbed }"
            title="Stage 4½ — .scrubbed.md sibling present"
          >S</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scenes {
  background: var(--bg-mantle);
  border-right: 1px solid var(--bg-surface0);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.scenes h2 {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-muted);
  padding: 10px 12px 4px;
  flex-shrink: 0;
}
.scene-list {
  flex: 1;
  overflow-y: auto;
}
.empty-msg {
  padding: 12px;
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.6;
}
.empty-msg b { color: var(--mauve); }

.scene-item {
  padding: 7px 12px;
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: background .1s;
}
.scene-item:hover { background: #252535; }
.scene-item.active { background: #252535; border-left-color: var(--mauve); }

.num { font-size: 10px; color: var(--text-muted); font-weight: 600; }
.narrator { font-size: 12px; font-weight: 600; }
.sname {
  font-size: 11px;
  color: var(--text-sub);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dots {
  display: flex;
  gap: 3px;
  margin-top: 4px;
}
.dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  font-size: 8px;
  font-weight: 700;
  line-height: 14px;
  text-align: center;
  background: var(--bg-surface0);
  color: var(--text-muted);
  user-select: none;
}
.dot.ok {
  background: var(--green, #a6d189);
  color: #0e1018;
}
</style>
