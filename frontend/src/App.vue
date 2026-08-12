<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  provide,
  reactive,
  ref,
  watch
} from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { Reading } from '@element-plus/icons-vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import AuthDialog from './components/AuthDialog.vue'
import { AUTH_ENABLED } from './services/api'
import {
  deleteGeneration,
  generate,
  listGenerations,
  type GenerationHistoryItem,
  type GenerationResponse,
  GenerationError
} from './services/generation'
import { readSafeNext, type ProtectedRouteName } from './router'
import { authSession, type AuthMode } from './state/auth'
import { WORKSPACE_KEY, type HistoryStatus } from './state/workspace'

// ===== 页面状态 =====
// idle: 空闲
// loading: 生成中
// success: 生成成功
// error: 生成失败
type Status = 'idle' | 'loading' | 'success' | 'error'
const status = ref<Status>('idle')
const errorMessage = ref('')
const errorRetryable = ref(false)

// ===== 路由与账号状态 =====
const route = useRoute()
const router = useRouter()
const authStatus = authSession.status
const currentUser = authSession.user
const authBusy = authSession.busy
const authError = authSession.error
const authRevision = authSession.revision
const authDialogOpen = computed(
  () => AUTH_ENABLED && (route.name === 'login' || route.name === 'register')
)
const authMode = computed<AuthMode>(() => route.name === 'register' ? 'register' : 'login')

let generationRequestId = 0
let historyRequestId = 0
let imageSelectionId = 0
let historyDeleteSequence = 0
let previousRouteName = route.name

// ===== 历史记录状态 =====
const HISTORY_LIMIT = 20
const historyStatus = ref<HistoryStatus>('idle')
const historyItems = ref<GenerationHistoryItem[]>([])
const historyCount = ref(0)
const historyError = ref('')
const historyLoaded = ref(false)
const historyDeletingIds = ref<Set<string>>(new Set())
const historyDeleteTokens = new Map<string, number>()
let historyRevision = 0

// ===== 用户输入 =====
const form = reactive({
  productName: '',
  targetAudience: '',
  tone: ''
})

// ===== 图片相关 =====
const imageFile = ref<File | null>(null)
const imagePreviewUrl = ref('')
const imageIsHeif = ref(false)
const imageResetEpoch = ref(0)
const restoredFromHistory = ref(false)

// 常量配置
type SupportedImageFormat = 'JPEG' | 'PNG' | 'WEBP' | 'HEIF'
const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10MB
const HEADER_BYTES = 256
const FORMAT_BY_MIME: Record<string, SupportedImageFormat> = {
  'image/jpeg': 'JPEG',
  'image/png': 'PNG',
  'image/webp': 'WEBP',
  'image/heic': 'HEIF',
  'image/heif': 'HEIF'
}
const FORMAT_BY_EXTENSION: Record<string, SupportedImageFormat> = {
  '.jpg': 'JPEG',
  '.jpeg': 'JPEG',
  '.png': 'PNG',
  '.webp': 'WEBP',
  '.heic': 'HEIF',
  '.heif': 'HEIF'
}
const ALLOWED_TYPES = Object.keys(FORMAT_BY_MIME)
const ALLOWED_EXTENSIONS = Object.keys(FORMAT_BY_EXTENSION)
const HEIF_SINGLE_IMAGE_MAJOR_BRANDS = new Set(['heic', 'heix', 'heim', 'heis', 'mif1'])
const HEIF_REJECTED_BRANDS = new Set(['avif', 'avis', 'hevc', 'hevx', 'hevm', 'hevs', 'msf1'])

// 检查文件扩展名是否合法
function isValidExtension(filename: string): boolean {
  const lower = filename.toLowerCase()
  return ALLOWED_EXTENSIONS.some(ext => lower.endsWith(ext))
}

function formatFromExtension(filename: string): SupportedImageFormat | undefined {
  const lower = filename.toLowerCase()
  const extension = ALLOWED_EXTENSIONS.find(ext => lower.endsWith(ext))
  return extension ? FORMAT_BY_EXTENSION[extension] : undefined
}

// 检查 MIME 类型是否合法（防止改扩展名）
function isValidMimeType(type: string): boolean {
  return ALLOWED_TYPES.includes(type)
}

function readBrand(bytes: Uint8Array, offset: number): string {
  if (offset < 0 || offset + 4 > bytes.length) {
    return ''
  }
  return String.fromCharCode(
    bytes[offset],
    bytes[offset + 1],
    bytes[offset + 2],
    bytes[offset + 3]
  )
}

// 与后端保持同一组有界品牌规则：接受单图 HEIF，拒绝 AVIF 与序列品牌。
function isSingleImageHeifHeader(bytes: Uint8Array): boolean {
  if (bytes.length < 16 || readBrand(bytes, 4) !== 'ftyp') {
    return false
  }

  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const boxSize = view.getUint32(0, false)
  if (boxSize < 16 || boxSize > bytes.length || boxSize % 4 !== 0) {
    return false
  }

  const majorBrand = readBrand(bytes, 8)
  const allBrands = new Set([majorBrand])
  for (let offset = 16; offset < boxSize; offset += 4) {
    allBrands.add(readBrand(bytes, offset))
  }

  return HEIF_SINGLE_IMAGE_MAJOR_BRANDS.has(majorBrand) &&
    !Array.from(allBrands).some(brand => HEIF_REJECTED_BRANDS.has(brand))
}

// 读取文件头并校验是否为真实图片
function detectImageFormat(file: File): Promise<SupportedImageFormat | null> {
  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      const buffer = e.target?.result as ArrayBuffer
      if (!buffer || buffer.byteLength < 12) {
        resolve(null)
        return
      }
      const bytes = new Uint8Array(buffer)

      // JPEG: FF D8 FF
      const isJpeg = bytes[0] === 0xFF && bytes[1] === 0xD8 && bytes[2] === 0xFF

      // PNG: 89 50 4E 47 0D 0A 1A 0A
      const isPng = bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4E &&
                    bytes[3] === 0x47 && bytes[4] === 0x0D && bytes[5] === 0x0A &&
                    bytes[6] === 0x1A && bytes[7] === 0x0A

      // WebP: RIFF....WEBP
      const isWebp = bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 &&
                     bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50

      const isHeif = isSingleImageHeifHeader(bytes)

      if (isJpeg) resolve('JPEG')
      else if (isPng) resolve('PNG')
      else if (isWebp) resolve('WEBP')
      else if (isHeif) resolve('HEIF')
      else resolve(null)
    }
    reader.onerror = () => resolve(null)
    reader.readAsArrayBuffer(file.slice(0, HEADER_BYTES))
  })
}

// 用户选择图片后触发
async function handleImageChange(file: UploadFile) {
  const selectionId = ++imageSelectionId
  // 重置状态
  restoredFromHistory.value = false
  status.value = 'idle'
  errorMessage.value = ''
  errorRetryable.value = false

  // file.raw 是真正的 File 对象
  const raw = file.raw as File

  // 1. 校验文件扩展名
  if (!isValidExtension(raw.name)) {
    ElMessage.error('仅支持 JPG / JPEG / PNG / WebP / HEIC / HEIF 格式的图片')
    clearImage()
    return
  }

  // 2. 校验 MIME 类型
  if (!isValidMimeType(raw.type)) {
    ElMessage.error('文件类型不正确，请上传真实的图片文件')
    clearImage()
    return
  }

  const extensionFormat = formatFromExtension(raw.name)
  const mimeFormat = FORMAT_BY_MIME[raw.type]
  if (!extensionFormat || extensionFormat !== mimeFormat) {
    ElMessage.error('图片扩展名与 MIME 类型不一致')
    clearImage()
    return
  }

  // 3. 校验文件大小
  if (raw.size > MAX_FILE_SIZE) {
    ElMessage.error('图片大小不能超过 10MB')
    clearImage()
    return
  }

  // 4. 校验文件头（防止改扩展名）
  const detectedFormat = await detectImageFormat(raw)
  if (selectionId !== imageSelectionId) {
    return
  }
  if (!detectedFormat || detectedFormat !== extensionFormat) {
    ElMessage.error('文件内容不是真实图片，请勿修改扩展名后上传')
    clearImage()
    return
  }

  // 通过校验，保存图片
  imageFile.value = raw
  imageIsHeif.value = detectedFormat === 'HEIF'
  imagePreviewUrl.value = URL.createObjectURL(raw)
  ElMessage.success('图片上传成功')
}

// 清空图片和结果
function clearImage() {
  imageSelectionId += 1
  // 通知当前生成页清空 el-upload 的内部队列，避免 limit=1 阻止再次选择。
  imageResetEpoch.value += 1
  imageFile.value = null
  imageIsHeif.value = false
  restoredFromHistory.value = false
  if (imagePreviewUrl.value) {
    if (imagePreviewUrl.value.startsWith('blob:')) {
      URL.revokeObjectURL(imagePreviewUrl.value)
    }
    imagePreviewUrl.value = ''
  }
  status.value = 'idle'
  errorMessage.value = ''
  errorRetryable.value = false
}

// ===== 生成结果 =====
const result = reactive<GenerationResponse>({
  generation_id: '',
  image_summary: '',
  title: '',
  body: '',
  tags: [],
  created_at: ''
})

function clearGenerationResult() {
  Object.assign(result, {
    generation_id: '',
    image_summary: '',
    title: '',
    body: '',
    tags: [],
    created_at: ''
  })
}

function resetAccountScopedState() {
  generationRequestId += 1
  historyRequestId += 1
  historyDeleteSequence += 1
  historyDeleteTokens.clear()
  historyRevision += 1
  clearImage()
  clearGenerationResult()
  form.productName = ''
  form.targetAudience = ''
  form.tone = ''
  historyItems.value = []
  historyCount.value = 0
  historyError.value = ''
  historyLoaded.value = false
  historyDeletingIds.value = new Set()
  historyStatus.value = 'idle'
}

function authDestination(): ProtectedRouteName {
  return readSafeNext(route.query.next) ?? 'generate'
}

function openAuth(mode: AuthMode, destination: ProtectedRouteName | null = null): void {
  const query = destination === null ? {} : { next: destination }
  void router.push({ name: mode, query })
}

function closeAuthDialog(): void {
  if (authBusy.value || authStatus.value === 'restoring') {
    return
  }
  // 成功登录后的路由跳转会令 el-dialog 正常关闭并触发 close 事件。
  // 此时已经离开认证路由，不能让迟到的关闭事件再把目标页面覆盖成首页。
  if (route.name !== 'login' && route.name !== 'register') {
    return
  }
  authError.value = ''
  void router.replace({ name: 'home' })
}

function switchAuthMode(mode: AuthMode): void {
  authError.value = ''
  const next = readSafeNext(route.query.next)
  void router.replace({
    name: mode,
    query: next === null ? {} : { next }
  })
}

async function retryAccountSession(): Promise<void> {
  await authSession.retry()
  if (authStatus.value === 'authenticated' && authDialogOpen.value) {
    await router.replace({ name: authDestination() })
  }
}

function handleSessionExpired(destination: ProtectedRouteName): void {
  authSession.expire()
  void router.replace({
    name: 'login',
    query: { next: destination }
  })
}

async function handleAuthSubmit(credentials: { email: string; password: string }): Promise<void> {
  const submittedRoute = route.fullPath
  const submittedMode = authMode.value
  const destination = authDestination()
  let email = credentials.email
  let password = credentials.password
  credentials.email = ''
  credentials.password = ''
  try {
    const succeeded = await authSession.submit(submittedMode, email, password)
    if (!succeeded) {
      return
    }

    // 浏览器后退或另一条认证导航可能在请求期间改变 URL。账号状态可以安全
    // 安装为最新响应，但只有仍停留在认证页时才继续导航；此时目的地必须从
    // 当前 URL 的白名单 next 重新读取，不能使用已经过时的任意路径快照。
    if (route.fullPath !== submittedRoute) {
      if (route.name === 'login' || route.name === 'register') {
        await router.replace({ name: authDestination() })
      }
      return
    }

    await router.replace({ name: destination })
    ElMessage.success(submittedMode === 'register' ? '注册成功，已登录。' : '登录成功。')
  } finally {
    email = ''
    password = ''
  }
}

async function handleLogout(): Promise<void> {
  const succeeded = await authSession.logout()
  if (succeeded) {
    await router.replace({ name: 'home' })
    ElMessage.success('已安全退出。')
    return
  }
  if (authError.value) {
    ElMessage.error(authError.value)
  }
}

// ===== 点击生成 =====
async function handleGenerate() {
  if (AUTH_ENABLED && authStatus.value !== 'authenticated') {
    openAuth('login', 'generate')
    return
  }

  // 1. 简单校验
  if (!imageFile.value) {
    ElMessage.warning('请先上传图片')
    return
  }

  // 2. 进入 loading，禁用按钮
  status.value = 'loading'
  errorMessage.value = ''
  errorRetryable.value = false

  // 3. 组装 FormData
  const formData = new FormData()
  formData.append('image', imageFile.value, imageFile.value.name)

  if (form.productName.trim()) {
    formData.append('product_name', form.productName.trim())
  }
  if (form.targetAudience.trim()) {
    formData.append('target_audience', form.targetAudience.trim())
  }
  if (form.tone.trim()) {
    formData.append('tone', form.tone.trim())
  }

  const requestId = ++generationRequestId
  const requestedAuthRevision = authRevision.value
  const requestedUserId = currentUser.value?.user_id ?? null

  // 4. 调用 service 层（默认真实接口；仅显式 VITE_USE_MOCK=true 时使用 Mock）
  try {
    const data = await generate(formData)
    if (
      requestId !== generationRequestId ||
      requestedAuthRevision !== authRevision.value ||
      requestedUserId !== (currentUser.value?.user_id ?? null)
    ) {
      return
    }
    Object.assign(result, data)
    status.value = 'success'
    historyRevision += 1
    historyLoaded.value = false
    if (route.name === 'history') {
      void loadHistory()
    }
  } catch (err: unknown) {
    if (
      requestId !== generationRequestId ||
      requestedAuthRevision !== authRevision.value ||
      requestedUserId !== (currentUser.value?.user_id ?? null)
    ) {
      return
    }
    if (err instanceof GenerationError && err.code === 'AUTH_REQUIRED') {
      handleSessionExpired('generate')
      return
    }
    status.value = 'error'
    if (err instanceof GenerationError) {
      errorMessage.value = err.message
      errorRetryable.value = err.retryable
    } else {
      errorMessage.value = '生成失败，请重试。'
      errorRetryable.value = false
    }
  }
}

// ===== 历史记录 =====
async function loadHistory() {
  if (AUTH_ENABLED && authStatus.value !== 'authenticated') {
    openAuth('login', 'history')
    return
  }
  if (historyStatus.value === 'loading') {
    return
  }

  historyStatus.value = 'loading'
  historyError.value = ''
  const requestedRevision = historyRevision
  const requestedAuthRevision = authRevision.value
  const requestedUserId = currentUser.value?.user_id ?? null
  const requestId = ++historyRequestId

  try {
    const data = await listGenerations(HISTORY_LIMIT)
    if (
      requestId !== historyRequestId ||
      requestedAuthRevision !== authRevision.value ||
      requestedUserId !== (currentUser.value?.user_id ?? null)
    ) {
      return
    }
    if (requestedRevision !== historyRevision) {
      historyStatus.value = 'idle'
      if (route.name === 'history') {
        void loadHistory()
      }
      return
    }

    historyItems.value = data.items
    historyCount.value = data.count
    historyLoaded.value = true
    historyStatus.value = 'success'
  } catch (err: unknown) {
    if (
      requestId !== historyRequestId ||
      requestedAuthRevision !== authRevision.value ||
      requestedUserId !== (currentUser.value?.user_id ?? null)
    ) {
      return
    }
    if (err instanceof GenerationError && err.code === 'AUTH_REQUIRED') {
      handleSessionExpired('history')
      return
    }

    historyStatus.value = 'error'
    historyLoaded.value = false
    historyError.value = err instanceof GenerationError
      ? err.message
      : '历史记录读取失败，请稍后重试'
  }
}

async function restoreHistoryItem(generation: GenerationHistoryItem): Promise<void> {
  // A history restore is a newer workspace action than any pending model call.
  // Invalidating first prevents a delayed generation response from replacing it.
  generationRequestId += 1
  clearImage()
  clearGenerationResult()
  form.productName = ''
  form.targetAudience = ''
  form.tone = ''
  Object.assign(result, {
    generation_id: generation.generation_id,
    image_summary: generation.image_summary,
    title: generation.title,
    body: generation.body,
    tags: [...generation.tags],
    created_at: generation.created_at
  })
  imageFile.value = null
  imagePreviewUrl.value = generation.has_image_preview
    ? generation.image_preview_url ?? ''
    : ''
  imageIsHeif.value = false
  restoredFromHistory.value = true
  errorMessage.value = ''
  errorRetryable.value = false
  status.value = 'success'
  await router.push({ name: 'generate' })
}

async function deleteHistoryItem(generation: GenerationHistoryItem): Promise<boolean> {
  const generationId = generation.generation_id
  if (
    historyDeleteTokens.has(generationId) ||
    !historyItems.value.some(item => item.generation_id === generationId)
  ) {
    return false
  }

  const token = ++historyDeleteSequence
  historyDeleteTokens.set(generationId, token)
  historyDeletingIds.value = new Set(historyDeletingIds.value).add(generationId)
  const requestedAuthRevision = authRevision.value
  const requestedUserId = currentUser.value?.user_id ?? null

  try {
    await deleteGeneration(generationId)
    if (
      historyDeleteTokens.get(generationId) !== token ||
      requestedAuthRevision !== authRevision.value ||
      requestedUserId !== (currentUser.value?.user_id ?? null)
    ) {
      return false
    }

    // Cancel a list request that may contain the now-deleted record, then
    // install the successful local mutation without waiting for another fetch.
    historyRequestId += 1
    historyRevision += 1
    historyItems.value = historyItems.value.filter(
      item => item.generation_id !== generationId
    )
    historyCount.value = historyItems.value.length
    historyLoaded.value = true
    historyError.value = ''
    historyStatus.value = 'success'
    ElMessage.success('历史记录已删除。')
    return true
  } catch (err: unknown) {
    if (
      historyDeleteTokens.get(generationId) !== token ||
      requestedAuthRevision !== authRevision.value ||
      requestedUserId !== (currentUser.value?.user_id ?? null)
    ) {
      return false
    }
    if (err instanceof GenerationError && err.code === 'AUTH_REQUIRED') {
      handleSessionExpired('history')
      return false
    }

    ElMessage.error(
      err instanceof GenerationError
        ? err.message
        : '历史记录删除失败，请稍后重试。'
    )
    return false
  } finally {
    if (historyDeleteTokens.get(generationId) === token) {
      historyDeleteTokens.delete(generationId)
      const nextDeletingIds = new Set(historyDeletingIds.value)
      nextDeletingIds.delete(generationId)
      historyDeletingIds.value = nextDeletingIds
    }
  }
}

function formatCreatedAt(value: string): string {
  const createdAt = new Date(value)
  if (Number.isNaN(createdAt.getTime())) {
    return '时间未知'
  }

  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short'
  }).format(createdAt)
}

// ===== 复制文案 =====
async function copyGeneration(generation: GenerationResponse) {
  const text = `标题：${generation.title}\n\n正文：\n${generation.body}\n\n标签：${generation.tags.join(' ')}`
  if (!navigator.clipboard?.writeText) {
    ElMessage.error('复制失败，请手动复制')
    return
  }

  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动复制')
  }
}

function copyAll() {
  void copyGeneration(result)
}

provide(WORKSPACE_KEY, {
  status,
  errorMessage,
  errorRetryable,
  form,
  imageFile,
  imagePreviewUrl,
  imageIsHeif,
  imageResetEpoch,
  restoredFromHistory,
  result,
  handleImageChange,
  clearImage,
  handleGenerate,
  copyAll,
  historyStatus,
  historyItems,
  historyCount,
  historyError,
  historyLoaded,
  historyDeletingIds,
  historyLimit: HISTORY_LIMIT,
  loadHistory,
  restoreHistoryItem,
  deleteHistoryItem,
  formatCreatedAt,
  copyGeneration
})

// Auth revision is the sole account-boundary signal. Route changes for the
// same account do not touch the draft; logout, expiry and a different account
// synchronously clear every private value and invalidate pending work.
watch(authRevision, resetAccountScopedState, { flush: 'sync' })

watch(
  () => route.name,
  async (name) => {
    const shouldFocusPageTitle = previousRouteName !== undefined && previousRouteName !== name
    previousRouteName = name
    const titles: Record<string, string> = {
      home: '图文种草助手 · 图片生成小红书内容初稿',
      generate: '生成小红书内容 · 图文种草助手',
      history: '历史记录 · 图文种草助手',
      login: '登录 · 图文种草助手',
      register: '注册 · 图文种草助手'
    }
    document.title = titles[String(name)] ?? titles.home
    await nextTick()
    // 首次完整页面加载由浏览器自然建立阅读起点；只有后续 SPA 导航才
    // 主动把焦点移到新页面标题，避免首屏出现无来由的大块焦点轮廓。
    if (shouldFocusPageTitle && name !== 'login' && name !== 'register') {
      document.getElementById('page-title')?.focus({ preventScroll: true })
    }
  },
  { immediate: true, flush: 'post' }
)

onMounted(() => {
  void authSession.ensureRestored()
})

onBeforeUnmount(() => {
  generationRequestId += 1
  historyRequestId += 1
  historyDeleteSequence += 1
  historyDeleteTokens.clear()
  imageSelectionId += 1
  if (imagePreviewUrl.value) {
    if (imagePreviewUrl.value.startsWith('blob:')) {
      URL.revokeObjectURL(imagePreviewUrl.value)
    }
    imagePreviewUrl.value = ''
  }
})
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#page-title">跳到主要内容</a>

    <header class="site-header">
      <RouterLink class="brand" :to="{ name: 'home' }" aria-label="返回首页">
        <span class="brand-mark" aria-hidden="true"><Reading /></span>
        <strong>图文种草助手</strong>
      </RouterLink>

      <div class="header-controls">
        <nav class="route-nav" aria-label="页面导航">
          <RouterLink :to="{ name: 'home' }">首页</RouterLink>
          <RouterLink :to="{ name: 'generate' }">生成文案</RouterLink>
          <RouterLink :to="{ name: 'history' }">历史记录</RouterLink>
        </nav>

        <div class="account-actions">
          <span
            v-if="authStatus === 'disabled'"
            class="account-mode-badge"
            role="status"
          >
            免登录演示
          </span>
          <span
            v-else-if="authStatus === 'restoring'"
            class="account-status"
            role="status"
            aria-live="polite"
          >
            正在恢复会话...
          </span>
          <template v-else-if="authStatus === 'authenticated' && currentUser">
            <span class="account-identity">
              <strong>{{ currentUser.email }}</strong>
              <small>{{ currentUser.email_verified ? '邮箱已验证' : '演示账号 · 邮箱未验证' }}</small>
            </span>
            <el-button :loading="authBusy" :disabled="authBusy" @click="handleLogout">
              退出
            </el-button>
          </template>
          <template v-else>
            <span
              v-if="authStatus === 'unavailable'"
              class="account-status account-status--warning"
              role="status"
            >
              账号服务不可用
            </span>
            <RouterLink class="account-link" :to="{ name: 'login' }">登录</RouterLink>
            <RouterLink class="account-link account-link--primary" :to="{ name: 'register' }">
              注册
            </RouterLink>
          </template>
        </div>
      </div>
    </header>

    <main id="main-content" class="site-main">
      <RouterView />
    </main>

    <footer class="site-footer">
      <span>本地演示工具</span>
      <span aria-hidden="true">·</span>
      <span>AI 输出请人工核对</span>
      <span aria-hidden="true">·</span>
      <span>不含自动发布</span>
    </footer>

    <AuthDialog
      :open="authDialogOpen"
      :mode="authMode"
      :busy="authBusy || authStatus === 'restoring'"
      :service-unavailable="authStatus === 'unavailable'"
      :error-message="authError"
      @close="closeAuthDialog"
      @retry-session="retryAccountSession"
      @switch-mode="switchAuthMode"
      @submit="handleAuthSubmit"
    />
  </div>
</template>
<style scoped>
.app-shell {
  width: min(100%, 1240px);
  min-height: 100vh;
  margin: 0 auto;
  padding: 24px 24px 18px;
}

.skip-link {
  position: fixed;
  z-index: 3000;
  top: 12px;
  left: 12px;
  padding: 11px 16px;
  border-radius: 10px;
  color: #fff;
  background: #801027;
  font-weight: 700;
  text-decoration: none;
  transform: translateY(-180%);
}

.skip-link:focus {
  transform: translateY(0);
}

.site-header {
  position: sticky;
  z-index: 100;
  top: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  min-height: 68px;
  margin-bottom: 32px;
  padding: 10px 12px 10px 16px;
  border: 1px solid rgba(234, 224, 228, 0.88);
  border-radius: 20px;
  background: rgba(255, 253, 254, 0.9);
  box-shadow: 0 14px 42px rgba(53, 28, 37, 0.08);
  backdrop-filter: blur(16px);
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  color: #211b23;
  text-decoration: none;
}

.brand-mark {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 12px;
  color: #fff;
  background: linear-gradient(145deg, #c1122f, #921126);
  box-shadow: 0 8px 18px rgba(193, 18, 47, 0.22);
}

.brand-mark svg {
  width: 19px;
  height: 19px;
}

.brand strong {
  font-size: 16px;
  white-space: nowrap;
}

.brand:focus-visible,
.route-nav a:focus-visible,
.account-link:focus-visible {
  outline: 3px solid rgba(167, 15, 42, 0.3);
  outline-offset: 3px;
}

.header-controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
  min-width: 0;
}

.route-nav {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border-radius: 13px;
  background: #f6f1f3;
}

.route-nav a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 40px;
  padding: 8px 14px;
  border-radius: 10px;
  color: #625963;
  font-size: 14px;
  font-weight: 650;
  text-decoration: none;
}

.route-nav a:hover {
  color: #8f1027;
  background: rgba(255, 255, 255, 0.78);
}

.route-nav a.router-link-exact-active {
  color: #fff;
  background: #9d1028;
  box-shadow: 0 7px 16px rgba(157, 16, 40, 0.18);
}

.account-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-height: 44px;
}

.account-mode-badge,
.account-status {
  padding: 7px 10px;
  border-radius: 999px;
  color: #665e68;
  background: #f3eef0;
  font-size: 12px;
  white-space: nowrap;
}

.account-status--warning {
  color: #875316;
  background: #fff4dc;
}

.account-identity {
  display: grid;
  max-width: 220px;
  text-align: right;
}

.account-identity strong,
.account-identity small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-identity strong {
  color: #302832;
  font-size: 13px;
}

.account-identity small {
  color: #746c76;
  font-size: 11px;
}

.account-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 40px;
  padding: 8px 13px;
  border: 1px solid #ded4d8;
  border-radius: 10px;
  color: #514851;
  background: #fff;
  font-size: 14px;
  font-weight: 650;
  text-decoration: none;
}

.account-link--primary {
  border-color: #9d1028;
  color: #fff;
  background: #9d1028;
}

.site-main {
  min-height: calc(100vh - 190px);
}

.site-footer {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 72px;
  padding: 24px 12px 8px;
  color: #837a83;
  font-size: 12px;
}

@media (prefers-reduced-motion: no-preference) {
  .skip-link,
  .route-nav a,
  .account-link {
    transition:
      color 160ms ease,
      background 160ms ease,
      transform 160ms ease,
      box-shadow 160ms ease;
  }
}

@media (max-width: 920px) {
  .site-header {
    position: static;
    align-items: stretch;
    flex-direction: column;
  }

  .header-controls {
    align-items: stretch;
    justify-content: space-between;
  }

  .route-nav {
    flex: 1 1 auto;
  }

  .route-nav a {
    flex: 1 1 0;
  }

  .account-actions {
    flex-wrap: wrap;
  }
}

@media (max-width: 640px) {
  .app-shell {
    padding: 12px 12px 16px;
  }

  .site-header {
    gap: 10px;
    margin-bottom: 20px;
    padding: 10px;
    border-radius: 16px;
  }

  .brand {
    align-self: center;
  }

  .header-controls {
    flex-direction: column;
  }

  .route-nav {
    width: 100%;
  }

  .route-nav a {
    min-width: 0;
    padding-inline: 7px;
    font-size: 13px;
  }

  .account-actions {
    justify-content: center;
  }

  .account-identity {
    max-width: min(100%, 230px);
    text-align: left;
  }

  .site-footer {
    margin-top: 48px;
  }
}

@media (max-width: 380px) {
  .route-nav {
    align-items: stretch;
    flex-direction: column;
  }

  .route-nav a {
    min-height: 44px;
  }
}
</style>
