<script setup lang="ts">
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadInstance } from 'element-plus'
import { DocumentCopy, RefreshRight, Delete } from '@element-plus/icons-vue'
import {
  generate,
  listGenerations,
  type GenerationResponse,
  GenerationError
} from './services/generation'

// ===== 页面状态 =====
// idle: 空闲
// loading: 生成中
// success: 生成成功
// error: 生成失败
type Status = 'idle' | 'loading' | 'success' | 'error'
const status = ref<Status>('idle')
const errorMessage = ref('')
const errorRetryable = ref(false)

// ===== 页面切换与历史记录状态 =====
type ActiveView = 'generate' | 'history'
type HistoryStatus = 'idle' | 'loading' | 'success' | 'error'
const HISTORY_LIMIT = 20
const activeView = ref<ActiveView>('generate')
const historyStatus = ref<HistoryStatus>('idle')
const historyItems = ref<GenerationResponse[]>([])
const historyCount = ref(0)
const historyError = ref('')
const historyLoaded = ref(false)
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
const uploadRef = ref<UploadInstance>()

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
async function handleImageChange(file: any) {
  // 重置状态
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
  // 同步清空 el-upload 的内部队列，避免 limit=1 阻止再次选择。
  uploadRef.value?.clearFiles()
  imageFile.value = null
  imageIsHeif.value = false
  if (imagePreviewUrl.value) {
    URL.revokeObjectURL(imagePreviewUrl.value)
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

// ===== 点击生成 =====
async function handleGenerate() {
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

  // 4. 调用 service 层（默认真实接口；仅显式 VITE_USE_MOCK=true 时使用 Mock）
  try {
    const data = await generate(formData)
    Object.assign(result, data)
    status.value = 'success'
    historyRevision += 1
    historyLoaded.value = false
    if (activeView.value === 'history') {
      void loadHistory()
    }
  } catch (err: any) {
    status.value = 'error'
    if (err instanceof GenerationError) {
      errorMessage.value = err.message
      errorRetryable.value = err.retryable
    } else {
      errorMessage.value = err.message || '生成失败，请重试'
      errorRetryable.value = false
    }
  }
}

// ===== 历史记录 =====
async function showHistory() {
  activeView.value = 'history'
  if (!historyLoaded.value) {
    await loadHistory()
  }
}

function showGenerator() {
  activeView.value = 'generate'
}

async function loadHistory() {
  if (historyStatus.value === 'loading') {
    return
  }

  historyStatus.value = 'loading'
  historyError.value = ''
  const requestedRevision = historyRevision

  try {
    const data = await listGenerations(HISTORY_LIMIT)
    if (requestedRevision !== historyRevision) {
      historyStatus.value = 'idle'
      await loadHistory()
      return
    }

    historyItems.value = data.items
    historyCount.value = data.count
    historyLoaded.value = true
    historyStatus.value = 'success'
  } catch (err: unknown) {
    if (requestedRevision !== historyRevision) {
      historyStatus.value = 'idle'
      await loadHistory()
      return
    }

    historyStatus.value = 'error'
    historyLoaded.value = false
    historyError.value = err instanceof GenerationError
      ? err.message
      : '历史记录读取失败，请稍后重试'
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
</script>

<template>
  <div class="app">
    <h1 class="title">小红书文案生成平台</h1>
    <p class="subtitle">上传一张图，AI 帮你写小红书笔记</p>

    <nav class="view-switch" aria-label="页面导航">
      <el-button
        id="generator-nav-button"
        :type="activeView === 'generate' ? 'primary' : 'default'"
        :aria-pressed="activeView === 'generate'"
        aria-controls="generator-panel"
        @click="showGenerator"
      >
        生成文案
      </el-button>
      <el-button
        id="history-nav-button"
        :type="activeView === 'history' ? 'primary' : 'default'"
        :aria-pressed="activeView === 'history'"
        aria-controls="history-panel"
        @click="showHistory"
      >
        历史记录
      </el-button>
    </nav>

    <section
      v-show="activeView === 'generate'"
      id="generator-panel"
      class="card"
      role="region"
      aria-labelledby="generator-nav-button"
    >
      <!-- 图片上传 -->
      <div class="section">
        <label class="label">1. 上传图片</label>
        <el-upload
          v-if="!imagePreviewUrl"
          ref="uploadRef"
          class="upload"
          drag
          action="#"
          :auto-upload="false"
          accept=".jpg,.jpeg,.png,.webp,.heic,.heif"
          :on-change="handleImageChange"
          :limit="1"
          :show-file-list="false"
        >
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">
            拖拽图片到这里，或 <em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              仅支持 JPG / JPEG / PNG / WebP / HEIC / HEIF，最大 10MB
            </div>
          </template>
        </el-upload>

        <!-- 图片预览 + 删除按钮 -->
        <div v-if="imagePreviewUrl" class="preview">
          <div
            v-if="imageIsHeif"
            class="heif-preview"
            role="img"
            aria-label="HEIC 或 HEIF 图片已选择"
          >
            <strong>{{ imageFile?.name }}</strong>
            <span>浏览器不直接预览此格式，将由后端安全转换为 JPEG</span>
          </div>
          <img v-else :src="imagePreviewUrl" alt="preview" />
          <el-button
            class="delete-btn"
            type="danger"
            :icon="Delete"
            size="small"
            @click="clearImage"
          >
            重新上传
          </el-button>
        </div>
      </div>

      <!-- 可选参数 -->
      <div class="section">
        <label class="label">2. 填写可选信息（不填也行）</label>
        <div class="form-row">
          <el-input v-model="form.productName" placeholder="产品名，例如：柠檬气泡水" />
          <el-input v-model="form.targetAudience" placeholder="目标人群，例如：年轻女生" />
          <el-input v-model="form.tone" placeholder="语气，例如：轻松种草" />
        </div>
      </div>

      <!-- 生成按钮 -->
      <div class="section">
        <el-button
          type="primary"
          size="large"
          :loading="status === 'loading'"
          :disabled="!imageFile"
          @click="handleGenerate"
        >
          {{ status === 'loading' ? '生成中...' : '生成文案' }}
        </el-button>
      </div>

      <!-- 错误提示 -->
      <el-alert
        v-if="status === 'error'"
        :title="errorMessage || '生成失败，请重试'"
        type="error"
        show-icon
        class="alert"
      />

      <!-- 成功时允许再次生成；失败时仅对可重试错误显示快捷重试按钮。 -->
      <div
        v-if="status === 'success' || (status === 'error' && errorRetryable)"
        class="section"
      >
        <el-button
          type="default"
          size="large"
          :icon="RefreshRight"
          @click="handleGenerate"
        >
          重新生成
        </el-button>
      </div>

      <!-- 结果展示 -->
      <div v-if="status === 'success'" class="result">
        <div class="result-header">
          <h2>生成结果</h2>
          <el-button type="success" :icon="DocumentCopy" @click="copyAll">
            复制全部文案
          </el-button>
        </div>

        <div class="result-block">
          <h3>图片理解</h3>
          <p>{{ result.image_summary }}</p>
        </div>

        <div class="result-block">
          <h3>标题</h3>
          <p class="title-text">{{ result.title }}</p>
        </div>

        <div class="result-block">
          <h3>正文</h3>
          <p class="body-text" style="white-space: pre-line;">{{ result.body }}</p>
        </div>

        <div class="result-block">
          <h3>标签</h3>
          <div class="tags">
            <el-tag v-for="tag in result.tags" :key="tag" type="primary">{{ tag }}</el-tag>
          </div>
        </div>
      </div>
    </section>

    <section
      v-show="activeView === 'history'"
      id="history-panel"
      class="card history-panel"
      role="region"
      aria-labelledby="history-nav-button"
      :aria-busy="historyStatus === 'loading'"
    >
      <div class="history-header">
        <div>
          <h2>历史记录</h2>
          <p>仅显示最近 {{ HISTORY_LIMIT }} 条成功记录，本功能限本地单用户使用。</p>
        </div>
        <el-button
          :icon="RefreshRight"
          :loading="historyStatus === 'loading'"
          :disabled="historyStatus === 'loading'"
          @click="loadHistory"
        >
          刷新
        </el-button>
      </div>

      <div
        v-if="historyStatus === 'loading'"
        class="history-state"
        role="status"
        aria-live="polite"
      >
        <p>正在读取历史记录...</p>
        <el-skeleton :rows="4" animated />
      </div>

      <div
        v-else-if="historyStatus === 'error'"
        class="history-state"
      >
        <el-alert
          title="历史记录读取失败"
          :description="historyError"
          type="error"
          show-icon
          :closable="false"
        />
        <el-button type="primary" :icon="RefreshRight" @click="loadHistory">
          重新加载
        </el-button>
      </div>

      <el-empty
        v-else-if="historyStatus === 'success' && historyItems.length === 0"
        description="暂无历史记录"
      >
        <p class="empty-hint">
          MySQL 未启用时历史记录为空；完成一次生成后可在这里查看结果。
        </p>
      </el-empty>

      <div
        v-else-if="historyStatus === 'success'"
        class="history-results"
        aria-live="polite"
      >
        <p class="history-count">本次读取 {{ historyCount }} 条成功记录</p>
        <article
          v-for="item in historyItems"
          :key="item.generation_id"
          class="history-item"
        >
          <div class="history-item-header">
            <div>
              <h3>{{ item.title }}</h3>
              <time :datetime="item.created_at">{{ formatCreatedAt(item.created_at) }}</time>
            </div>
            <el-button
              type="success"
              plain
              :icon="DocumentCopy"
              :aria-label="`复制历史文案：${item.title}`"
              @click="copyGeneration(item)"
            >
              复制文案
            </el-button>
          </div>

          <p class="history-summary">
            <strong>图片理解：</strong>{{ item.image_summary }}
          </p>
          <p class="history-body">{{ item.body }}</p>
          <div class="tags" aria-label="历史记录标签">
            <el-tag v-for="tag in item.tags" :key="tag" type="primary">
              {{ tag }}
            </el-tag>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.app {
  max-width: 720px;
  margin: 0 auto;
  padding: 40px 20px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.title {
  text-align: center;
  font-size: 28px;
  margin-bottom: 8px;
  color: #1f2937;
}

.subtitle {
  text-align: center;
  color: #6b7280;
  margin-bottom: 32px;
}

.view-switch {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-bottom: 20px;
}

.view-switch .el-button + .el-button {
  margin-left: 0;
}

.card {
  background: #fff;
  border-radius: 12px;
  padding: 28px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
}

.section {
  margin-bottom: 28px;
}

.section:last-child {
  margin-bottom: 0;
}

.label {
  display: block;
  font-weight: 600;
  margin-bottom: 12px;
  color: #374151;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.preview {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
}

.preview img {
  max-width: 100%;
  max-height: 300px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}

.heif-preview {
  box-sizing: border-box;
  width: min(100%, 480px);
  min-height: 160px;
  padding: 24px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 8px;
  color: #334155;
}

.heif-preview strong {
  overflow-wrap: anywhere;
}

.heif-preview span {
  color: #64748b;
  font-size: 14px;
}

.delete-btn {
  margin-top: 8px;
}

.alert {
  margin-bottom: 20px;
}

.result {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid #e5e7eb;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.result-header h2 {
  margin: 0;
  font-size: 20px;
}

.result-block {
  margin-bottom: 20px;
}

.result-block h3 {
  font-size: 14px;
  color: #6b7280;
  margin-bottom: 8px;
}

.result-block p {
  margin: 0;
  color: #1f2937;
  line-height: 1.6;
}

.title-text {
  font-size: 18px;
  font-weight: 600;
  color: #ff2442;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.history-panel {
  text-align: left;
}

.history-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 20px;
  border-bottom: 1px solid #e5e7eb;
}

.history-header h2 {
  margin: 0 0 8px;
  color: #1f2937;
  font-size: 20px;
}

.history-header p,
.history-count,
.empty-hint {
  margin: 0;
  color: #6b7280;
  font-size: 14px;
  line-height: 1.6;
}

.history-state {
  display: grid;
  gap: 20px;
  margin-top: 24px;
}

.history-state > p {
  margin: 0;
  color: #6b7280;
}

.history-results {
  display: grid;
  gap: 16px;
  margin-top: 20px;
}

.history-item {
  padding: 20px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #fdfdfd;
  overflow-wrap: anywhere;
}

.history-item-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.history-item-header h3 {
  margin: 0 0 6px;
  color: #ff2442;
  font-size: 18px;
}

.history-item-header time {
  color: #6b7280;
  font-size: 13px;
}

.history-summary,
.history-body {
  margin: 0 0 16px;
  color: #1f2937;
  line-height: 1.65;
}

.history-summary {
  padding: 12px;
  border-radius: 8px;
  background: #f8fafc;
}

.history-body {
  white-space: pre-wrap;
}

@media (max-width: 640px) {
  .app {
    padding: 24px 12px;
  }

  .card {
    padding: 20px 16px;
  }

  .view-switch,
  .history-header,
  .history-item-header {
    flex-wrap: wrap;
  }

  .view-switch .el-button {
    flex: 1 1 140px;
  }

  .history-header,
  .history-item-header {
    align-items: stretch;
  }

  .history-header > .el-button,
  .history-item-header > .el-button {
    width: 100%;
  }

  .history-item {
    padding: 16px;
  }
}
</style>
