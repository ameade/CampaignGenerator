<script setup lang="ts">
import { computed, inject } from 'vue'
import type { FileTreeContext, TreeNode } from './fileTreeTypes'
import { FILE_TREE_KEY } from './fileTreeTypes'

const props = defineProps<{ node: TreeNode }>()

const injected = inject(FILE_TREE_KEY)
if (!injected) throw new Error('FileTreeNode must be used inside a FileTree')
const ctx: FileTreeContext = injected

const visible = computed(() => ctx.nodeMatches(props.node))
const expanded = computed(() => ctx.isExpanded(props.node))
const fState = computed(() =>
  props.node.isFolder ? ctx.folderState(props.node) : 'none' as const
)
const selected = computed(() =>
  !props.node.isFolder && ctx.selected.value.has(props.node.path)
)

function onCheckbox(e: Event) {
  e.stopPropagation()
  if (props.node.isFolder) ctx.toggleFolder(props.node)
  else ctx.toggleFile(props.node.path)
}

function onRowClick() {
  if (props.node.isFolder) ctx.toggleExpand(props.node)
  else ctx.toggleFile(props.node.path)
}

// Tri-state checkbox via template ref binding
function setIndeterminate(el: HTMLInputElement | null) {
  if (el && props.node.isFolder) {
    el.indeterminate = fState.value === 'some'
  }
}
</script>

<template>
  <li v-if="visible" class="tree-node">
    <div
      class="tree-row"
      :class="{ 'is-folder': node.isFolder, 'is-selected': selected }"
      @click="onRowClick"
    >
      <span v-if="node.isFolder" class="chevron">{{ expanded ? '▾' : '▸' }}</span>
      <span v-else class="chevron spacer"></span>

      <input
        type="checkbox"
        :ref="(el) => setIndeterminate(el as HTMLInputElement | null)"
        :checked="node.isFolder ? fState === 'all' : selected"
        @change="onCheckbox"
        @click.stop
      />

      <span class="node-name">{{ node.name }}</span>
      <span v-if="node.isFolder" class="node-count">{{ node.descendantFiles.length }}</span>
    </div>

    <ul v-if="node.isFolder && expanded" class="tree-children">
      <FileTreeNode v-for="child in node.children" :key="child.path" :node="child" />
    </ul>
  </li>
</template>

<style scoped>
.tree-node { list-style: none; }
.tree-row {
  display: flex; align-items: center; gap: 6px;
  padding: 3px 4px; font-size: 11px; cursor: pointer;
  border-radius: 3px; user-select: none;
}
.tree-row:hover { background: var(--bg-surface0); }
.tree-row.is-selected { background: color-mix(in srgb, var(--mauve) 15%, transparent); }
.chevron {
  display: inline-block; width: 12px; color: var(--text-muted);
  font-size: 10px; text-align: center;
}
.chevron.spacer { visibility: hidden; }
.tree-row input { accent-color: var(--mauve); cursor: pointer; }
.node-name {
  font-family: var(--mono); color: var(--text-sub); flex: 1;
}
.tree-row.is-folder .node-name { color: var(--text); font-weight: 600; }
.node-count {
  font-size: 10px; color: var(--text-muted);
  background: var(--bg-surface0); padding: 1px 6px; border-radius: 3px;
}
.tree-children {
  list-style: none; padding: 0 0 0 16px; margin: 0;
  border-left: 1px solid var(--bg-surface1); margin-left: 6px;
}
</style>
