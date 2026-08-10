// 小红书文案生成接口服务层

export interface GenerationResponse {
  generation_id: string
  image_summary: string
  title: string
  body: string
  tags: string[]
  created_at: string
}

export interface GenerationHistoryResponse {
  items: GenerationResponse[]
  count: number
}

export interface ApiErrorResponse {
  error: {
    code: string
    message: string
    retryable: boolean
  }
}

// 默认连接真实后端；仅在环境变量显式设为 true 时启用 Mock。
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
const API_TIMEOUT = 90000 // 90 秒，模型调用可能较慢
const HISTORY_TIMEOUT = 40000 // 覆盖后端默认数据库读取超时并保留安全错误响应
const mockHistory: GenerationResponse[] = []

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
  const productName = (formData.get('product_name') as string) || '这杯饮品'

  return new Promise((resolve) => {
    setTimeout(() => {
      const generated: GenerationResponse = {
        generation_id: `mock-${Date.now()}`,
        image_summary:
          '这是一张清新的饮品照片，主体是一杯装满冰块的柠檬气泡水，背景是木质桌面，整体光线明亮，给人夏日清爽的感觉。',
        title:
          productName.length > 10
            ? `${productName}｜太清爽了`
            : `夏日续命水｜${productName}太清爽了`,
        body: '姐妹们谁懂啊！\n\n今天随手点的这杯柠檬气泡水真的戳中我了，冰块满满，酸度刚好，不齁甜。\n\n拍照的时候阳光刚好洒进来，原图就很有氛围感，完全不用加滤镜。\n\n夏天不想喝奶茶的时候来一杯这个，解腻又解渴，真的很爱～',
        tags: ['#夏日饮品', '#柠檬气泡水', '#清爽解腻', '#下午茶'],
        created_at: new Date().toISOString()
      }
      mockHistory.unshift(generated)
      mockHistory.splice(50)
      resolve(generated)
    }, 1500)
  })
}

async function readApiError(response: Response): Promise<GenerationError> {
  let errorData: ApiErrorResponse | undefined
  try {
    errorData = (await response.json()) as ApiErrorResponse
  } catch {
    // 非 JSON 上游错误统一使用固定兜底信息。
  }

  return new GenerationError(
    errorData?.error?.message || `请求失败（HTTP ${response.status}）`,
    errorData?.error?.code || 'INTERNAL_ERROR',
    errorData?.error?.retryable ?? false
  )
}

/**
 * 真实生成：调用后端 POST /api/v1/generations
 */
async function realGenerate(formData: FormData): Promise<GenerationResponse> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT)

  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/generations`, {
      method: 'POST',
      body: formData,
      signal: controller.signal
      // 注意：禁止手动设置 Content-Type，浏览器会自动生成 multipart boundary
    })

    clearTimeout(timeoutId)

    if (!response.ok) {
      throw await readApiError(response)
    }

    return (await response.json()) as GenerationResponse
  } catch (err: any) {
    clearTimeout(timeoutId)

    if (err instanceof GenerationError) {
      throw err
    }

    if (err.name === 'AbortError') {
      throw new GenerationError('请求超时，请稍后重试', 'MODEL_TIMEOUT', true)
    }

    throw new GenerationError(err.message || '网络请求失败', 'INTERNAL_ERROR', false)
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
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), HISTORY_TIMEOUT)

  try {
    const response = await fetch(
      `${API_BASE_URL}/api/v1/generations?limit=${encodeURIComponent(limit)}`,
      {
        method: 'GET',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        signal: controller.signal
      }
    )

    if (!response.ok) {
      throw await readApiError(response)
    }

    return (await response.json()) as GenerationHistoryResponse
  } catch (err: any) {
    if (err instanceof GenerationError) {
      throw err
    }

    if (err.name === 'AbortError') {
      throw new GenerationError('历史记录读取超时，请稍后重试', 'HISTORY_TIMEOUT', true)
    }

    throw new GenerationError('无法连接后端，请确认服务已启动', 'NETWORK_ERROR', true)
  } finally {
    clearTimeout(timeoutId)
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
    const items = mockHistory.slice(0, limit)
    return { items, count: items.length }
  }

  return realListGenerations(limit)
}
