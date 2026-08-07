<script setup lang="ts">
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { DocumentCopy, RefreshRight, Delete } from '@element-plus/icons-vue'
import { generate, type GenerationResponse, GenerationError } from './services/generation'

// ===== 页面状态 =====
// idle: 空闲
// loading: 生成中
// success: 生成成功
// error: 生成失败
type Status = 'idle' | 'loading' | 'success' | 'error'
const status = ref<Status>('idle')
const errorMessage = ref('')

// ===== 用户输入 =====
const form = reactive({
  productName: '',
  targetAudience: '',
  tone: ''
})

// ===== 图片相关 =====
const imageFile = ref<File | null>(null)
const imagePreviewUrl = ref('')

// 常量配置
const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10MB
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp']
const ALLOWED_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp']

// 检查文件扩展名是否合法
function isValidExtension(filename: string): boolean {
  const lower = filename.toLowerCase()
  return ALLOWED_EXTENSIONS.some(ext => lower.endsWith(ext))
}

// 检查 MIME 类型是否合法（防止改扩展名）
function isValidMimeType(type: string): boolean {
  return ALLOWED_TYPES.includes(type)
}

// 读取文件头并校验是否为真实图片
function validateImageHeader(file: File): Promise<boolean> {
  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      const buffer = e.target?.result as ArrayBuffer
      if (!buffer || buffer.byteLength < 12) {
        resolve(false)
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

      resolve(isJpeg || isPng || isWebp)
    }
    reader.onerror = () => resolve(false)
    reader.readAsArrayBuffer(file.slice(0, 12))
  })
}

// 用户选择图片后触发
async function handleImageChange(file: any) {
  // 重置状态
  status.value = 'idle'
  errorMessage.value = ''

  // file.raw 是真正的 File 对象
  const raw = file.raw as File

  // 1. 校验文件扩展名
  if (!isValidExtension(raw.name)) {
    ElMessage.error('仅支持 JPG / JPEG / PNG / WebP 格式的图片')
    clearImage()
    return
  }

  // 2. 校验 MIME 类型
  if (!isValidMimeType(raw.type)) {
    ElMessage.error('文件类型不正确，请上传真实的图片文件')
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
  const isRealImage = await validateImageHeader(raw)
  if (!isRealImage) {
    ElMessage.error('文件内容不是真实图片，请勿修改扩展名后上传')
    clearImage()
    return
  }

  // 通过校验，保存图片
  imageFile.value = raw
  imagePreviewUrl.value = URL.createObjectURL(raw)
  ElMessage.success('图片上传成功')
}

// 清空图片和结果
function clearImage() {
  imageFile.value = null
  if (imagePreviewUrl.value) {
    URL.revokeObjectURL(imagePreviewUrl.value)
    imagePreviewUrl.value = ''
  }
  status.value = 'idle'
  errorMessage.value = ''
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

  // 4. 调用 service 层（当前是 Mock，后续切换真实接口不用改这里）
  try {
    const data = await generate(formData)
    Object.assign(result, data)
    status.value = 'success'
  } catch (err: any) {
    status.value = 'error'
    if (err instanceof GenerationError) {
      errorMessage.value = err.message
    } else {
      errorMessage.value = err.message || '生成失败，请重试'
    }
  }
}

// ===== 复制全部文案 =====
function copyAll() {
  const text = `标题：${result.title}\n\n正文：\n${result.body}\n\n标签：${result.tags.join(' ')}`
  navigator.clipboard.writeText(text).then(() => {
    ElMessage.success('已复制到剪贴板')
  }).catch(() => {
    ElMessage.error('复制失败，请手动复制')
  })
}
</script>

<template>
  <div class="app">
    <h1 class="title">小红书文案生成平台</h1>
    <p class="subtitle">上传一张图，AI 帮你写小红书笔记</p>

    <div class="card">
      <!-- 图片上传 -->
      <div class="section">
        <label class="label">1. 上传图片</label>
        <el-upload
          v-if="!imagePreviewUrl"
          class="upload"
          drag
          action="#"
          :auto-upload="false"
          accept=".jpg,.jpeg,.png,.webp"
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
              仅支持 JPG / JPEG / PNG / WebP，最大 10MB
            </div>
          </template>
        </el-upload>

        <!-- 图片预览 + 删除按钮 -->
        <div v-if="imagePreviewUrl" class="preview">
          <img :src="imagePreviewUrl" alt="preview" />
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

      <!-- 重新生成按钮（成功或失败都显示） -->
      <div v-if="status === 'success' || status === 'error'" class="section">
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
    </div>
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
</style>
