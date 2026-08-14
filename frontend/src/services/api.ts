// Shared browser boundary for the local FastAPI backend.

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000'
const LOOPBACK_API_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])
const URL_SCHEME_PATTERN = /^[a-z][a-z\d+.-]*:\/\//i

/**
 * Keep this local demonstration from being pointed at an arbitrary remote API.
 * Validation runs even in Mock mode so an unsafe value cannot remain dormant
 * and later become active merely by changing VITE_USE_MOCK.
 */
export function normalizeLocalApiBaseUrl(value: string | undefined): string {
  const candidate = value === undefined || value === ''
    ? DEFAULT_API_BASE_URL
    : value

  if (candidate !== candidate.trim()) {
    throw invalidApiBaseUrl()
  }

  const scheme = candidate.match(URL_SCHEME_PATTERN)
  if (scheme === null) {
    throw invalidApiBaseUrl()
  }

  const authorityStart = scheme[0].length
  const remainder = candidate.slice(authorityStart)
  const authorityEndOffset = remainder.search(/[/?#]/)
  const authority = authorityEndOffset === -1
    ? remainder
    : remainder.slice(0, authorityEndOffset)
  const suffix = authorityEndOffset === -1
    ? ''
    : remainder.slice(authorityEndOffset)

  if (
    authority === '' ||
    authority.includes('@') ||
    (suffix !== '' && suffix !== '/')
  ) {
    throw invalidApiBaseUrl()
  }

  let parsed: URL
  try {
    parsed = new URL(candidate)
  } catch {
    throw invalidApiBaseUrl()
  }

  const rawHostname = hostnameFromAuthority(authority)
  if (
    (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') ||
    !LOOPBACK_API_HOSTS.has(rawHostname) ||
    !LOOPBACK_API_HOSTS.has(parsed.hostname.toLowerCase()) ||
    parsed.username !== '' ||
    parsed.password !== '' ||
    parsed.pathname !== '/' ||
    parsed.search !== '' ||
    parsed.hash !== ''
  ) {
    throw invalidApiBaseUrl()
  }

  return parsed.origin
}

function hostnameFromAuthority(authority: string): string {
  if (authority.startsWith('[')) {
    const closingBracket = authority.indexOf(']')
    if (closingBracket < 0) {
      return ''
    }
    const port = authority.slice(closingBracket + 1)
    if (port !== '' && !/^:\d+$/.test(port)) {
      return ''
    }
    return authority.slice(0, closingBracket + 1).toLowerCase()
  }

  const portSeparator = authority.lastIndexOf(':')
  if (portSeparator === -1) {
    return authority.toLowerCase()
  }
  const port = authority.slice(portSeparator + 1)
  if (!/^\d+$/.test(port)) {
    return ''
  }
  return authority.slice(0, portSeparator).toLowerCase()
}

function invalidApiBaseUrl(): Error {
  return new Error(
    'VITE_API_BASE_URL 必须是无凭据、无额外路径的本机 loopback HTTP(S) 地址。'
  )
}

export const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === 'true'
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'
export const API_BASE_URL = normalizeLocalApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

const CSRF_HEADER_NAME = 'X-XHS-CSRF'
const CSRF_HEADER_VALUE = '1'

interface SafeErrorDefinition {
  message: string
  retryable: boolean
}

const SAFE_ERRORS: Readonly<Record<string, SafeErrorDefinition>> = Object.freeze({
  AUTH_INPUT_INVALID: { message: '注册信息无效，请检查后重试。', retryable: false },
  AUTH_REQUIRED: { message: '请先登录。', retryable: false },
  AUTH_UNAVAILABLE: { message: '账号服务暂时不可用，请稍后重试。', retryable: true },
  CSRF_REJECTED: { message: '请求来源校验失败。', retryable: false },
  DATABASE_ERROR: { message: '数据服务暂时不可用，请稍后重试。', retryable: true },
  EMAIL_UNAVAILABLE: { message: '该邮箱暂不可用于注册。', retryable: false },
  FORM_FIELD_TOO_LARGE: { message: '表单内容过长，请缩短后重试。', retryable: false },
  IMAGE_DECODE_FAILED: { message: '无法解析该图片，请换一张后重试。', retryable: false },
  IMAGE_REQUIRED: { message: '请上传一张图片。', retryable: false },
  IMAGE_TOO_LARGE: { message: '图片大小超出限制。', retryable: false },
  INTERNAL_ERROR: { message: '请求处理失败，请稍后重试。', retryable: false },
  INVALID_CREDENTIALS: { message: '邮箱或密码错误。', retryable: false },
  INVALID_FORM_DATA: { message: '表单字段无效，请检查后重试。', retryable: false },
  INVALID_IMAGE_DIMENSIONS: { message: '图片尺寸无效或超出限制。', retryable: false },
  MODEL_FAILED: { message: '模型服务暂时不可用，请稍后重试。', retryable: true },
  MODEL_OUTPUT_INVALID: { message: '模型返回的内容格式无效，请重试。', retryable: true },
  MODEL_TIMEOUT: { message: '模型处理超时，请稍后重试。', retryable: true },
  RATE_LIMITED: { message: '请求过于频繁，请稍后重试。', retryable: true },
  UNSUPPORTED_IMAGE_TYPE: { message: '图片类型不受支持。', retryable: false }
})

const FALLBACK_ERROR: SafeErrorDefinition = Object.freeze({
  message: '请求失败，请稍后重试。',
  retryable: false
})

interface ApiErrorEnvelope {
  error?: {
    code?: unknown
  }
}

export class ApiRequestError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly status: number | null

  constructor(
    message: string,
    code: string,
    retryable: boolean,
    status: number | null = null
  ) {
    super(message)
    this.name = 'ApiRequestError'
    this.code = code
    this.retryable = retryable
    this.status = status
  }
}

export interface ApiRequestOptions {
  init?: RequestInit
  timeoutMs: number
  timeoutCode: string
  timeoutMessage: string
  networkMessage: string
  csrf?: boolean
  expectNoContent?: boolean
}

/**
 * Execute one bounded request. Cookie credentials and the CSRF marker are
 * enabled together only when the local account feature is explicitly on.
 */
export async function requestApi<T>(
  url: string,
  options: ApiRequestOptions
): Promise<T> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), options.timeoutMs)
  const headers = new Headers(options.init?.headers)
  const init: RequestInit = {
    ...options.init,
    headers,
    signal: controller.signal
  }

  if (AUTH_ENABLED) {
    init.credentials = 'include'
    if (options.csrf) {
      headers.set(CSRF_HEADER_NAME, CSRF_HEADER_VALUE)
    }
  }

  try {
    const response = await fetch(url, init)
    if (!response.ok) {
      throw await responseError(response)
    }

    if (options.expectNoContent) {
      if (response.status !== 204) {
        throw invalidResponseError(response.status)
      }
      return undefined as T
    }
    if (response.status === 204) {
      throw invalidResponseError(response.status)
    }

    try {
      return (await response.json()) as T
    } catch {
      throw invalidResponseError(response.status)
    }
  } catch (error: unknown) {
    if (error instanceof ApiRequestError) {
      throw error
    }
    if (error instanceof Error && error.name === 'AbortError') {
      throw new ApiRequestError(
        options.timeoutMessage,
        options.timeoutCode,
        true
      )
    }
    throw new ApiRequestError(options.networkMessage, 'NETWORK_ERROR', true)
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function responseError(response: Response): Promise<ApiRequestError> {
  let envelope: ApiErrorEnvelope | null = null
  try {
    envelope = (await response.json()) as ApiErrorEnvelope
  } catch {
    envelope = null
  }

  const candidate = envelope?.error?.code
  const code = typeof candidate === 'string' && candidate in SAFE_ERRORS
    ? candidate
    : 'REQUEST_FAILED'
  const safe = SAFE_ERRORS[code] ?? FALLBACK_ERROR
  return new ApiRequestError(safe.message, code, safe.retryable, response.status)
}

function invalidResponseError(status: number): ApiRequestError {
  return new ApiRequestError(
    '后端返回的数据格式无效。',
    'INVALID_RESPONSE',
    true,
    status
  )
}
