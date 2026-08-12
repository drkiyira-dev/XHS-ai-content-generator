// Local demonstration account service. Raw session tokens remain HttpOnly.

import {
  API_BASE_URL,
  AUTH_ENABLED,
  ApiRequestError,
  USE_MOCK,
  requestApi
} from './api'

const AUTH_TIMEOUT = 15000

export interface AuthUser {
  user_id: number
  email: string
  email_verified: boolean
}

interface AuthResponse {
  user: AuthUser
}

export class AuthError extends Error {
  readonly code: string
  readonly retryable: boolean
  readonly status: number | null

  constructor(
    message: string,
    code: string,
    retryable: boolean = false,
    status: number | null = null
  ) {
    super(message)
    this.name = 'AuthError'
    this.code = code
    this.retryable = retryable
    this.status = status
  }
}

// Mock identity exists only in this JavaScript module and disappears on reload.
let mockCurrentUser: AuthUser | null = null
const mockUserIds = new Map<string, number>()
let nextMockUserId = 1

export function getMockAccountId(): number | null {
  return mockCurrentUser?.user_id ?? null
}

export async function restoreSession(): Promise<AuthUser | null> {
  if (!AUTH_ENABLED) {
    return null
  }
  if (USE_MOCK) {
    return mockCurrentUser
  }

  try {
    const response = await requestApi<AuthResponse>(
      `${API_BASE_URL}/api/v1/auth/me`,
      {
        init: {
          method: 'GET',
          headers: { Accept: 'application/json' },
          cache: 'no-store'
        },
        timeoutMs: AUTH_TIMEOUT,
        timeoutCode: 'AUTH_TIMEOUT',
        timeoutMessage: '账号状态读取超时，请重试。',
        networkMessage: '无法连接账号服务，请确认后端已启动。'
      }
    )
    return parseAuthUser(response)
  } catch (error: unknown) {
    if (error instanceof ApiRequestError && error.code === 'AUTH_REQUIRED') {
      return null
    }
    throw toAuthError(error)
  }
}

export async function registerAccount(
  email: string,
  password: string
): Promise<AuthUser> {
  requireAuthFeature()
  if (USE_MOCK) {
    mockCurrentUser = mockUser(email, password)
    return mockCurrentUser
  }
  return submitCredentials('/api/v1/auth/register', email, password)
}

export async function loginAccount(
  email: string,
  password: string
): Promise<AuthUser> {
  requireAuthFeature()
  if (USE_MOCK) {
    mockCurrentUser = mockUser(email, password)
    return mockCurrentUser
  }
  return submitCredentials('/api/v1/auth/login', email, password)
}

export async function logoutAccount(): Promise<void> {
  requireAuthFeature()
  if (USE_MOCK) {
    mockCurrentUser = null
    return
  }

  try {
    await requestApi<void>(`${API_BASE_URL}/api/v1/auth/logout`, {
      init: {
        method: 'POST',
        headers: { Accept: 'application/json' }
      },
      csrf: true,
      expectNoContent: true,
      timeoutMs: AUTH_TIMEOUT,
      timeoutCode: 'AUTH_TIMEOUT',
      timeoutMessage: '退出超时，请重试。',
      networkMessage: '无法连接账号服务，请稍后重试。'
    })
  } catch (error: unknown) {
    throw toAuthError(error)
  }
}

async function submitCredentials(
  path: '/api/v1/auth/register' | '/api/v1/auth/login',
  email: string,
  password: string
): Promise<AuthUser> {
  try {
    const response = await requestApi<AuthResponse>(`${API_BASE_URL}${path}`, {
      init: {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ email, password })
      },
      csrf: true,
      timeoutMs: AUTH_TIMEOUT,
      timeoutCode: 'AUTH_TIMEOUT',
      timeoutMessage: '账号请求超时，请重试。',
      networkMessage: '无法连接账号服务，请稍后重试。'
    })
    return parseAuthUser(response)
  } catch (error: unknown) {
    throw toAuthError(error)
  }
}

function parseAuthUser(response: unknown): AuthUser {
  if (!isRecord(response) || !isRecord(response.user)) {
    throw invalidAuthResponse()
  }
  const user = response.user
  if (
    typeof user.user_id !== 'number' ||
    !Number.isSafeInteger(user.user_id) ||
    user.user_id <= 0 ||
    typeof user.email !== 'string' ||
    user.email.length < 3 ||
    user.email.length > 254 ||
    typeof user.email_verified !== 'boolean'
  ) {
    throw invalidAuthResponse()
  }
  return {
    user_id: user.user_id,
    email: user.email,
    email_verified: user.email_verified
  }
}

function mockUser(email: string, password: string): AuthUser {
  const normalizedEmail = typeof email === 'string' ? email.trim().toLowerCase() : ''
  if (
    !normalizedEmail.includes('@') ||
    normalizedEmail.length > 254 ||
    typeof password !== 'string' ||
    password.length < 15 ||
    password.length > 128
  ) {
    throw new AuthError('账号信息无效，请检查后重试。', 'AUTH_INPUT_INVALID')
  }
  let userId = mockUserIds.get(normalizedEmail)
  if (userId === undefined) {
    userId = nextMockUserId
    nextMockUserId += 1
    mockUserIds.set(normalizedEmail, userId)
  }
  return {
    user_id: userId,
    email: normalizedEmail,
    email_verified: false
  }
}

function requireAuthFeature(): void {
  if (!AUTH_ENABLED) {
    throw new AuthError('账号功能尚未启用。', 'AUTH_DISABLED')
  }
}

function toAuthError(error: unknown): AuthError {
  if (error instanceof AuthError) {
    return error
  }
  if (error instanceof ApiRequestError) {
    return new AuthError(error.message, error.code, error.retryable, error.status)
  }
  return new AuthError(
    '账号服务暂时不可用，请稍后重试。',
    'AUTH_UNAVAILABLE',
    true
  )
}

function invalidAuthResponse(): AuthError {
  return new AuthError(
    '账号服务返回的数据格式无效。',
    'INVALID_RESPONSE',
    true
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
