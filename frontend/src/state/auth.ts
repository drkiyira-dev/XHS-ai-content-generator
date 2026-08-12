import { ref, type Ref } from 'vue'

import { AUTH_ENABLED } from '../services/api'
import {
  AuthError,
  loginAccount,
  logoutAccount,
  registerAccount,
  restoreSession,
  type AuthUser
} from '../services/auth'

export type AuthStatus =
  | 'disabled'
  | 'restoring'
  | 'anonymous'
  | 'authenticated'
  | 'unavailable'
export type AuthMode = 'login' | 'register'

export interface AuthSession {
  readonly status: Ref<AuthStatus>
  readonly user: Ref<AuthUser | null>
  readonly busy: Ref<boolean>
  readonly error: Ref<string>
  readonly revision: Ref<number>
  ensureRestored(): Promise<void>
  retry(): Promise<void>
  submit(mode: AuthMode, email: string, password: string): Promise<boolean>
  logout(): Promise<boolean>
  expire(message?: string): void
}

const UNAVAILABLE_CODES = new Set([
  'AUTH_UNAVAILABLE',
  'AUTH_TIMEOUT',
  'DATABASE_ERROR',
  'INVALID_RESPONSE',
  'NETWORK_ERROR',
  'REQUEST_FAILED'
])

function createAuthSession(): AuthSession {
  const status = ref<AuthStatus>(AUTH_ENABLED ? 'restoring' : 'disabled')
  const user = ref<AuthUser | null>(null)
  const busy = ref(false)
  const error = ref('')
  const revision = ref(0)

  let sessionCheckId = 0
  let actionId = 0
  let restoreTask: Promise<void> | null = null

  function installUser(nextUser: AuthUser): void {
    if (user.value?.user_id !== nextUser.user_id) {
      revision.value += 1
    }
    user.value = nextUser
    status.value = 'authenticated'
    error.value = ''
  }

  function installAnonymous(): void {
    if (user.value !== null) {
      revision.value += 1
    }
    user.value = null
    status.value = 'anonymous'
    error.value = ''
  }

  function detachRestore(): void {
    sessionCheckId += 1
    // The underlying fetch cannot be taken over by a newer action, but its
    // result is ignored through sessionCheckId. Detaching lets an explicit
    // retry start immediately instead of joining an obsolete request.
    restoreTask = null
  }

  async function runRestore(checkId: number): Promise<void> {
    try {
      const restoredUser = await restoreSession()
      if (checkId !== sessionCheckId) {
        return
      }
      if (restoredUser === null) {
        installAnonymous()
        return
      }
      installUser(restoredUser)
    } catch (cause: unknown) {
      if (checkId !== sessionCheckId) {
        return
      }

      const message = cause instanceof AuthError
        ? cause.message
        : '账号服务暂时不可用，请稍后重试。'
      // A failed re-check is not proof that a previously established local
      // identity was revoked. AUTH_REQUIRED is returned as null above.
      if (user.value !== null) {
        status.value = 'authenticated'
        error.value = message
        return
      }
      status.value = 'unavailable'
      error.value = message
    }
  }

  function startRestore(): Promise<void> {
    if (!AUTH_ENABLED) {
      status.value = 'disabled'
      user.value = null
      return Promise.resolve()
    }
    if (restoreTask !== null) {
      return restoreTask
    }

    const checkId = ++sessionCheckId
    status.value = 'restoring'
    error.value = ''
    const task = runRestore(checkId)
    restoreTask = task
    void task.finally(() => {
      if (restoreTask === task) {
        restoreTask = null
      }
    })
    return task
  }

  async function ensureRestored(): Promise<void> {
    if (!AUTH_ENABLED) {
      status.value = 'disabled'
      return
    }
    if (restoreTask !== null) {
      await restoreTask
      return
    }
    if (
      status.value === 'authenticated' ||
      status.value === 'anonymous' ||
      status.value === 'unavailable'
    ) {
      return
    }
    await startRestore()
  }

  async function retry(): Promise<void> {
    if (!AUTH_ENABLED || busy.value) {
      return
    }
    await startRestore()
  }

  async function submit(
    mode: AuthMode,
    submittedEmail: string,
    submittedPassword: string
  ): Promise<boolean> {
    if (!AUTH_ENABLED) {
      status.value = 'disabled'
      error.value = '账号功能尚未启用。'
      return false
    }
    if (busy.value || status.value === 'authenticated') {
      return false
    }

    const currentActionId = ++actionId
    detachRestore()
    busy.value = true
    status.value = 'anonymous'
    error.value = ''
    let email = submittedEmail
    let password = submittedPassword
    submittedEmail = ''
    submittedPassword = ''

    try {
      const authenticatedUser = mode === 'register'
        ? await registerAccount(email, password)
        : await loginAccount(email, password)
      if (currentActionId !== actionId) {
        return false
      }
      installUser(authenticatedUser)
      return true
    } catch (cause: unknown) {
      if (currentActionId !== actionId) {
        return false
      }
      const authError = cause instanceof AuthError ? cause : null
      status.value = authError && UNAVAILABLE_CODES.has(authError.code)
        ? 'unavailable'
        : 'anonymous'
      error.value = authError?.message ?? '账号操作失败，请稍后重试。'
      return false
    } finally {
      email = ''
      password = ''
      if (currentActionId === actionId) {
        busy.value = false
      }
    }
  }

  async function logout(): Promise<boolean> {
    if (
      !AUTH_ENABLED ||
      busy.value ||
      status.value !== 'authenticated' ||
      user.value === null
    ) {
      return false
    }

    const currentActionId = ++actionId
    detachRestore()
    busy.value = true
    error.value = ''
    try {
      await logoutAccount()
      if (currentActionId !== actionId) {
        return false
      }
      installAnonymous()
      return true
    } catch (cause: unknown) {
      if (currentActionId !== actionId) {
        return false
      }
      // Do not erase account-scoped UI until the server confirms revocation.
      status.value = 'authenticated'
      error.value = cause instanceof AuthError
        ? cause.message
        : '退出失败，请稍后重试。'
      return false
    } finally {
      if (currentActionId === actionId) {
        busy.value = false
      }
    }
  }

  function expire(message: string = '会话已失效，请重新登录。'): void {
    if (!AUTH_ENABLED) {
      return
    }
    detachRestore()
    actionId += 1
    busy.value = false
    user.value = null
    // Expiry is a new session generation even when another action already
    // cleared the visible identity. This prevents an old response from being
    // accepted after the same account logs in again.
    revision.value += 1
    status.value = 'anonymous'
    error.value = message
  }

  return {
    status,
    user,
    busy,
    error,
    revision,
    ensureRestored,
    retry,
    submit,
    logout,
    expire
  }
}

export const authSession = createAuthSession()
