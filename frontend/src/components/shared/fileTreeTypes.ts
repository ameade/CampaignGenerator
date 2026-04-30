import type { ComputedRef, InjectionKey } from 'vue'

export interface TreeNode {
  name: string
  path: string                 // absolute path for files, slug for folders
  isFolder: boolean
  children: TreeNode[]
  descendantFiles: string[]
}

export interface FileTreeContext {
  selected: ComputedRef<Set<string>>
  folderState: (node: TreeNode) => 'none' | 'some' | 'all'
  toggleFile: (path: string) => void
  toggleFolder: (node: TreeNode) => void
  toggleExpand: (node: TreeNode) => void
  isExpanded: (node: TreeNode) => boolean
  nodeMatches: (node: TreeNode) => boolean
  basePath: ComputedRef<string>
}

export const FILE_TREE_KEY: InjectionKey<FileTreeContext> = Symbol('FileTree')
