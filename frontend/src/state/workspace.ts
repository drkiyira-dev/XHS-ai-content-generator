import { inject, type ComputedRef, type InjectionKey, type Ref } from 'vue'
import type { UploadFile } from 'element-plus'

import type {
  EmojiLevel,
  GenerationHistoryItem,
  GenerationResponse
} from '../services/generation'

export type GenerationStatus = 'idle' | 'loading' | 'success' | 'error'
export type HistoryStatus = 'idle' | 'loading' | 'success' | 'error'

export interface WorkspaceForm {
  productName: string
  targetAudience: string
  tone: string
  emojiLevel: EmojiLevel
  relatedTags: boolean
}

export interface SubmittedGenerationConfig extends WorkspaceForm {
  imageToken: number
}

export interface EditableGenerationDraft {
  title: string
  body: string
  tags: string[]
}

export interface WorkspaceVersion {
  key: number
  server: GenerationResponse
  draft: EditableGenerationDraft
  submitted: SubmittedGenerationConfig | null
  source: 'generated' | 'history'
}

export type GenerationConfigState = 'none' | 'clean' | 'dirty' | 'unknown'

export interface WorkspaceContext {
  status: Ref<GenerationStatus>
  errorMessage: Ref<string>
  errorRetryable: Ref<boolean>
  form: WorkspaceForm
  imageFile: Ref<File | null>
  imagePreviewUrl: Ref<string>
  imageIsHeif: Ref<boolean>
  imageResetEpoch: Ref<number>
  restoredFromHistory: Ref<boolean>
  currentVersion: Ref<WorkspaceVersion | null>
  previousVersion: Ref<WorkspaceVersion | null>
  hasResult: ComputedRef<boolean>
  isGenerating: ComputedRef<boolean>
  isRegenerating: ComputedRef<boolean>
  configState: ComputedRef<GenerationConfigState>
  draftDirty: ComputedRef<boolean>
  riskSnapshotStale: ComputedRef<boolean>
  handleImageChange: (file: UploadFile) => void | Promise<void>
  clearImage: () => void
  handleGenerate: () => void | Promise<void>
  copyAll: () => void
  copyVersion: (version: WorkspaceVersion) => void | Promise<void>
  restoreGeneratedDraft: () => void

  historyStatus: Ref<HistoryStatus>
  historyItems: Ref<GenerationHistoryItem[]>
  historyCount: Ref<number>
  historyError: Ref<string>
  historyLoaded: Ref<boolean>
  historyDeletingIds: Ref<Set<string>>
  historyLimit: number
  loadHistory: () => void | Promise<void>
  restoreHistoryItem: (generation: GenerationHistoryItem) => void | Promise<void>
  deleteHistoryItem: (generation: GenerationHistoryItem) => Promise<boolean>
  formatCreatedAt: (value: string) => string
  copyGeneration: (generation: GenerationResponse) => void | Promise<void>
}

export const WORKSPACE_KEY: InjectionKey<WorkspaceContext> = Symbol('workspace')

export function useWorkspace(): WorkspaceContext {
  const workspace = inject(WORKSPACE_KEY)
  if (workspace === undefined) {
    throw new Error('Workspace context is unavailable.')
  }
  return workspace
}
