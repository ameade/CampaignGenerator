<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue'
import FileTreeNode from './FileTreeNode.vue'
import type { TreeNode, FileTreeContext } from './fileTreeTypes'
import { FILE_TREE_KEY } from './fileTreeTypes'

const props = defineProps<{
  files: string[]
  basePath: string
  modelValue: string[]
  searchQuery?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

// ── Tree construction ────────────────────────────────────────────────────────

function buildTree(files: string[], basePath: string): TreeNode[] {
  const base = basePath.replace(/\/+$/, '')

  // Nested map keyed by folder name; special _files for leaf files.
  type Raw = { folders: Map<string, Raw>; files: string[] }
  const root: Raw = { folders: new Map(), files: [] }

  for (const absPath of files) {
    const rel = base && absPath.startsWith(base + '/')
      ? absPath.slice(base.length + 1)
      : absPath
    const parts = rel.split('/').filter(Boolean)
    if (!parts.length) continue

    let cursor = root
    for (let i = 0; i < parts.length - 1; i++) {
      const name = parts[i]
      let next = cursor.folders.get(name)
      if (!next) {
        next = { folders: new Map(), files: [] }
        cursor.folders.set(name, next)
      }
      cursor = next
    }
    cursor.files.push(absPath)
  }

  function toTree(node: Raw, folderPath: string): TreeNode[] {
    const out: TreeNode[] = []
    const folderNames = [...node.folders.keys()].sort()
    for (const name of folderNames) {
      const childFolderPath = folderPath ? `${folderPath}/${name}` : name
      const children = toTree(node.folders.get(name)!, childFolderPath)
      const descendantFiles = collectFiles(children)
      out.push({
        name,
        path: childFolderPath,
        isFolder: true,
        children,
        descendantFiles,
      })
    }
    for (const absPath of node.files.sort()) {
      const name = absPath.split('/').pop() || absPath
      out.push({
        name,
        path: absPath,
        isFolder: false,
        children: [],
        descendantFiles: [absPath],
      })
    }
    return out
  }

  function collectFiles(nodes: TreeNode[]): string[] {
    const acc: string[] = []
    for (const n of nodes) {
      if (n.isFolder) acc.push(...n.descendantFiles)
      else acc.push(n.path)
    }
    return acc
  }

  return toTree(root, base)
}

const tree = computed(() => buildTree(props.files, props.basePath))

// ── Selection (tri-state via injected helpers) ───────────────────────────────

const selected = computed(() => new Set(props.modelValue))

function folderState(node: TreeNode): 'none' | 'some' | 'all' {
  if (!node.descendantFiles.length) return 'none'
  let hits = 0
  for (const f of node.descendantFiles) if (selected.value.has(f)) hits++
  if (hits === 0) return 'none'
  if (hits === node.descendantFiles.length) return 'all'
  return 'some'
}

function toggleFile(path: string) {
  const next = new Set(selected.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  emit('update:modelValue', [...next])
}

function toggleFolder(node: TreeNode) {
  const state = folderState(node)
  const next = new Set(selected.value)
  if (state === 'all') {
    for (const f of node.descendantFiles) next.delete(f)
  } else {
    for (const f of node.descendantFiles) next.add(f)
  }
  emit('update:modelValue', [...next])
}

// ── Expand state ──────────────────────────────────────────────────────────────

const expanded = ref<Set<string>>(new Set())

function toggleExpand(node: TreeNode) {
  const next = new Set(expanded.value)
  if (next.has(node.path)) next.delete(node.path)
  else next.add(node.path)
  expanded.value = next
}

const query = computed(() => (props.searchQuery || '').trim().toLowerCase())

function nodeMatches(node: TreeNode): boolean {
  if (!query.value) return true
  if (node.isFolder) return node.children.some(nodeMatches)
  const base = props.basePath.replace(/\/+$/, '')
  const rel = base && node.path.startsWith(base + '/') ? node.path.slice(base.length + 1) : node.path
  return rel.toLowerCase().includes(query.value)
}

function isExpanded(node: TreeNode): boolean {
  if (query.value) return nodeMatches(node)  // auto-expand through matching branches
  return expanded.value.has(node.path)
}

// Auto-expand ancestor folders of any selected file (runs on first populate + updates)
watch(() => props.modelValue, (val) => {
  if (!val?.length) return
  const base = props.basePath.replace(/\/+$/, '')
  const next = new Set(expanded.value)
  for (const f of val) {
    if (!(base && f.startsWith(base + '/'))) continue
    const rel = f.slice(base.length + 1)
    const parts = rel.split('/').slice(0, -1)
    let acc = ''
    for (const p of parts) {
      acc = acc ? `${acc}/${p}` : p
      next.add(acc)
    }
  }
  expanded.value = next
}, { immediate: true })

// ── Provide context for recursive children ───────────────────────────────────

const ctx: FileTreeContext = {
  selected,
  folderState,
  toggleFile,
  toggleFolder,
  toggleExpand,
  isExpanded,
  nodeMatches,
  basePath: computed(() => props.basePath),
}
provide(FILE_TREE_KEY, ctx)
</script>

<template>
  <div class="file-tree">
    <ul class="tree-root">
      <FileTreeNode v-for="node in tree" :key="node.path" :node="node" />
    </ul>
    <p v-if="!tree.length" class="empty">No files found.</p>
  </div>
</template>

<style scoped>
.file-tree {
  max-height: 320px; overflow-y: auto;
  background: var(--bg-base); border: 1px solid var(--bg-surface1);
  border-radius: 4px; padding: 6px;
}
.tree-root { list-style: none; padding: 0; margin: 0; }
.empty { font-size: 11px; color: var(--text-muted); padding: 6px; margin: 0; }
</style>
