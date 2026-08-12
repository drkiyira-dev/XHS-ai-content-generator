// 小红书文案生成接口服务层

import {
  API_BASE_URL,
  AUTH_ENABLED,
  ApiRequestError,
  USE_MOCK,
  requestApi
} from './api'
import { getMockAccountId } from './auth'

export interface GenerationResponse {
  generation_id: string
  image_summary: string
  title: string
  body: string
  tags: string[]
  created_at: string
}

export interface GenerationHistoryItem extends GenerationResponse {
  has_image_preview: boolean
  image_preview_url: string | null
}

export interface GenerationHistoryResponse {
  items: GenerationHistoryItem[]
  count: number
}

// 默认连接真实后端；仅在环境变量显式设为 true 时启用 Mock。
const API_TIMEOUT = 90000 // 90 秒，模型调用可能较慢
const HISTORY_TIMEOUT = 40000 // 覆盖后端默认数据库读取超时并保留安全错误响应
const GENERATION_UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const mockHistoryByOwner = new Map<number, GenerationHistoryItem[]>()

function mockOwnerId(): number {
  if (!AUTH_ENABLED) {
    return 0
  }
  const userId = getMockAccountId()
  if (userId === null) {
    throw new GenerationError('请先登录。', 'AUTH_REQUIRED', false)
  }
  return userId
}

function mockHistoryFor(ownerId: number): GenerationHistoryItem[] {
  let history = mockHistoryByOwner.get(ownerId)
  if (history === undefined) {
    history = []
    mockHistoryByOwner.set(ownerId, history)
  }
  return history
}

// 扩展 Error，携带 code 和 retryable 供页面判断
export class GenerationError extends Error {
  code: string
  retryable: boolean

  constructor(message: string, code: string = 'INTERNAL_ERROR', retryable: boolean = false) {
    super(message)
    this.code = code
    this.retryable = retryable
  }
}

/**
 * Mock 生成：返回与真实接口完全一致的字段结构
 */
async function mockGenerate(formData: FormData): Promise<GenerationResponse> {
  const subjectName = (formData.get('product_name') as string) || '这张图片'
  const ownerHistory = mockHistoryFor(mockOwnerId())
  const mockPreviewUrl = await createMockHistoryPreview(formData.get('image'))

  return new Promise((resolve) => {
    setTimeout(() => {
      const generated: GenerationResponse = {
        generation_id: `mock-${Date.now()}`,
        image_summary:
          '这是一张用于本地 Mock 演示的图片，画面主体清晰，前景与背景层次分明。',
        title:
          subjectName.length > 10
            ? `${subjectName.slice(0, 10)}｜图片分享`
            : `${subjectName}｜图片分享初稿`,
        body: '先从画面本身说起：主体位于视觉中心，前后景层次清楚，整体色彩协调。\n\n这是一段本地 Mock 文案，用来演示标题、正文和话题标签的展示与复制流程。实际使用时，请切换到真实后端并核对生成内容。',
        tags: ['#图片分享', '#内容初稿', '#图文记录', '#本地演示'],
        created_at: new Date().toISOString()
      }
      ownerHistory.unshift({
        ...generated,
        has_image_preview: mockPreviewUrl !== null,
        image_preview_url: mockPreviewUrl
      })
      for (const discarded of ownerHistory.splice(50)) {
        revokeMockHistoryPreview(discarded)
      }
      resolve(generated)
    }, 1500)
  })
}

/**
 * 真实生成：调用后端 POST /api/v1/generations
 */
async function realGenerate(formData: FormData): Promise<GenerationResponse> {
  try {
    const response = await requestApi<unknown>(`${API_BASE_URL}/api/v1/generations`, {
      init: {
        method: 'POST',
        body: formData
        // 禁止手动设置 Content-Type，浏览器会自动生成 multipart boundary。
      },
      csrf: true,
      timeoutMs: API_TIMEOUT,
      timeoutCode: 'MODEL_TIMEOUT',
      timeoutMessage: '请求超时，请稍后重试',
      networkMessage: '无法连接后端，请确认服务已启动'
    })
    return parseGenerationResponse(response)
  } catch (error: unknown) {
    throw toGenerationError(error)
  }
}

/**
 * 生成小红书文案
 * @param formData 必须包含 image（File），可选 product_name / target_audience / tone
 */
export async function generate(formData: FormData): Promise<GenerationResponse> {
  if (USE_MOCK) {
    return mockGenerate(formData)
  }
  return realGenerate(formData)
}

async function realListGenerations(limit: number): Promise<GenerationHistoryResponse> {
  try {
    const response = await requestApi<unknown>(
      `${API_BASE_URL}/api/v1/generations?limit=${encodeURIComponent(limit)}`,
      {
        init: {
          method: 'GET',
          headers: { Accept: 'application/json' },
          cache: 'no-store'
        },
        timeoutMs: HISTORY_TIMEOUT,
        timeoutCode: 'HISTORY_TIMEOUT',
        timeoutMessage: '历史记录读取超时，请稍后重试',
        networkMessage: '无法连接后端，请确认服务已启动'
      }
    )
    return parseHistoryResponse(response)
  } catch (error: unknown) {
    throw toGenerationError(error)
  }
}

/**
 * 读取最近成功生成的本地历史记录，不触发模型调用。
 */
export async function listGenerations(limit: number = 20): Promise<GenerationHistoryResponse> {
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
    throw new GenerationError('历史记录数量必须在 1 到 50 之间', 'INVALID_HISTORY_LIMIT', false)
  }

  if (USE_MOCK) {
    const items = mockHistoryFor(mockOwnerId()).slice(0, limit)
    return { items, count: items.length }
  }

  return realListGenerations(limit)
}

async function realDeleteGeneration(generationId: string): Promise<void> {
  try {
    await requestApi<void>(
      `${API_BASE_URL}/api/v1/generations/${encodeURIComponent(generationId)}`,
      {
        init: {
          method: 'DELETE',
          headers: { Accept: 'application/json' }
        },
        csrf: true,
        expectNoContent: true,
        timeoutMs: HISTORY_TIMEOUT,
        timeoutCode: 'HISTORY_TIMEOUT',
        timeoutMessage: '历史记录删除超时，请稍后重试',
        networkMessage: '无法连接后端，请确认服务已启动'
      }
    )
  } catch (error: unknown) {
    throw toGenerationError(error)
  }
}

/**
 * 删除当前账号拥有的一条历史记录。服务端以会话归属为最终授权边界。
 */
export async function deleteGeneration(generationId: string): Promise<void> {
  if (!isBoundedString(generationId, 1, 128)) {
    throw new GenerationError('历史记录标识无效。', 'INVALID_GENERATION_ID', false)
  }

  if (USE_MOCK) {
    const history = mockHistoryFor(mockOwnerId())
    const index = history.findIndex(item => item.generation_id === generationId)
    if (index >= 0) {
      const [removed] = history.splice(index, 1)
      if (removed !== undefined) {
        revokeMockHistoryPreview(removed)
      }
    }
    return
  }

  await realDeleteGeneration(generationId)
}

async function createMockHistoryPreview(value: FormDataEntryValue | null): Promise<string | null> {
  if (!(value instanceof File) || !value.type.startsWith('image/')) {
    return null
  }

  let bitmap: ImageBitmap | null = null
  try {
    bitmap = await createImageBitmap(value)
    const scale = Math.min(1, 480 / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const context = canvas.getContext('2d')
    if (context === null) {
      return null
    }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const preview = await new Promise<Blob | null>(resolve => {
      canvas.toBlob(resolve, 'image/webp', 0.72)
    })
    if (preview === null || preview.size === 0 || preview.size > 256 * 1024) {
      return null
    }
    return URL.createObjectURL(preview)
  } catch {
    return null
  } finally {
    bitmap?.close()
  }
}

function revokeMockHistoryPreview(item: GenerationHistoryItem): void {
  if (item.image_preview_url?.startsWith('blob:')) {
    URL.revokeObjectURL(item.image_preview_url)
  }
}

function toGenerationError(error: unknown): GenerationError {
  if (error instanceof GenerationError) {
    return error
  }
  if (error instanceof ApiRequestError) {
    return new GenerationError(error.message, error.code, error.retryable)
  }
  return new GenerationError('请求失败，请稍后重试', 'INTERNAL_ERROR', false)
}

function parseGenerationResponse(value: unknown): GenerationResponse {
  if (!isRecord(value)) {
    throw invalidGenerationResponse()
  }
  if (
    !isBoundedString(value.generation_id, 1, 128) ||
    !isBoundedString(value.image_summary, 1, 10_000) ||
    !isBoundedString(value.title, 1, 200) ||
    !isBoundedString(value.body, 1, 50_000) ||
    !isBoundedString(value.created_at, 1, 128) ||
    !Array.isArray(value.tags) ||
    value.tags.length < 3 ||
    value.tags.length > 5 ||
    !value.tags.every(tag => isBoundedString(tag, 1, 200))
  ) {
    throw invalidGenerationResponse()
  }
  return {
    generation_id: value.generation_id,
    image_summary: value.image_summary,
    title: value.title,
    body: value.body,
    tags: [...value.tags],
    created_at: value.created_at
  }
}

function parseHistoryResponse(value: unknown): GenerationHistoryResponse {
  if (
    !isRecord(value) ||
    !Array.isArray(value.items) ||
    value.items.length > 50 ||
    typeof value.count !== 'number' ||
    !Number.isInteger(value.count) ||
    value.count < 0 ||
    value.count !== value.items.length
  ) {
    throw invalidGenerationResponse()
  }
  const items = value.items.map(item => parseHistoryItem(item))
  if (new Set(items.map(item => item.generation_id)).size !== items.length) {
    throw invalidGenerationResponse()
  }
  return { items, count: value.count }
}

function parseHistoryItem(value: unknown): GenerationHistoryItem {
  if (!isRecord(value) || typeof value.has_image_preview !== 'boolean') {
    throw invalidGenerationResponse()
  }

  const generation = parseGenerationResponse(value)
  const imagePreviewUrl = parseImagePreviewUrl(
    value.image_preview_url,
    value.has_image_preview,
    generation.generation_id
  )
  return {
    ...generation,
    has_image_preview: value.has_image_preview,
    image_preview_url: imagePreviewUrl
  }
}

function parseImagePreviewUrl(
  value: unknown,
  hasImagePreview: boolean,
  generationId: string
): string | null {
  if (!hasImagePreview) {
    if (value !== null) {
      throw invalidGenerationResponse()
    }
    return null
  }
  if (
    !isBoundedString(value, 1, 2048) ||
    !GENERATION_UUID_PATTERN.test(generationId) ||
    /[\u0000-\u001f\u007f\\]/.test(value)
  ) {
    throw invalidGenerationResponse()
  }

  try {
    const apiOrigin = new URL(API_BASE_URL, window.location.origin).origin
    const previewUrl = new URL(value, `${apiOrigin}/`)
    const expectedPath = `/api/v1/generations/${generationId}/image-preview`
    if (
      (previewUrl.protocol !== 'http:' && previewUrl.protocol !== 'https:') ||
      previewUrl.origin !== apiOrigin ||
      previewUrl.username !== '' ||
      previewUrl.password !== '' ||
      previewUrl.pathname !== expectedPath ||
      previewUrl.search !== '' ||
      previewUrl.hash !== ''
    ) {
      throw invalidGenerationResponse()
    }
    return previewUrl.toString()
  } catch (error: unknown) {
    if (error instanceof GenerationError) {
      throw error
    }
    throw invalidGenerationResponse()
  }
}

function invalidGenerationResponse(): GenerationError {
  return new GenerationError(
    '后端返回的数据格式无效。',
    'INVALID_RESPONSE',
    true
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isBoundedString(value: unknown, minimum: number, maximum: number): value is string {
  return typeof value === 'string' && value.length >= minimum && value.length <= maximum
}
