// 小红书文案生成接口服务层

export interface GenerationResponse {
  generation_id: string
  image_summary: string
  title: string
  body: string
  tags: string[]
  created_at: string
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
      resolve({
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
      })
    }, 1500)
  })
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
      let errorData: ApiErrorResponse | undefined
      try {
        errorData = (await response.json()) as ApiErrorResponse
      } catch {
        // 解析失败时兜底
      }

      const code = errorData?.error?.code || 'INTERNAL_ERROR'
      const message = errorData?.error?.message || `请求失败（HTTP ${response.status}）`
      const retryable = errorData?.error?.retryable ?? false
      throw new GenerationError(message, code, retryable)
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
