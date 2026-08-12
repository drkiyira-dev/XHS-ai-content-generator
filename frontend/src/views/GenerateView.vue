<script setup lang="ts">
import { ref, watch } from 'vue'
import type { UploadInstance } from 'element-plus'
import { Delete, DocumentCopy, RefreshRight } from '@element-plus/icons-vue'

import { useWorkspace } from '../state/workspace'

const {
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
  copyAll
} = useWorkspace()

const uploadRef = ref<UploadInstance>()

watch(
  imageResetEpoch,
  () => {
    uploadRef.value?.clearFiles()
  },
  { flush: 'sync' }
)
</script>

<template>
  <section
    id="generator-panel"
    class="workspace-page"
    role="region"
    aria-labelledby="page-title"
    :aria-busy="status === 'loading'"
  >
    <header class="workspace-heading">
      <h1 id="page-title" class="title" tabindex="-1">生成小红书内容初稿</h1>
      <p class="subtitle">上传一张图片，生成标题、正文和话题标签初稿</p>
    </header>

    <div class="card">
      <div class="section">
        <h2 class="label">1. 上传图片</h2>
        <el-alert
          v-if="restoredFromHistory"
          class="restored-history-note"
          title="已载入历史生成结果"
          description="历史图片仅用于预览，浏览器不会把它当作可再次提交的本地文件；如需重新生成，请重新上传图片。"
          type="info"
          show-icon
          :closable="false"
        />
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
          aria-describedby="upload-help"
        >
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">
            拖拽图片到这里，或 <em>点击上传</em>
          </div>
          <template #tip>
            <div id="upload-help" class="el-upload__tip">
              仅支持 JPG / JPEG / PNG / WebP / HEIC / HEIF，最大 10MB
            </div>
          </template>
        </el-upload>

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
          <img
            v-else
            :src="imagePreviewUrl"
            :alt="restoredFromHistory ? '历史记录图片预览' : '已选择图片预览'"
            referrerpolicy="no-referrer"
          />
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

        <p class="privacy-note">
          点击生成后，图片会经本地后端发送至已配置的第三方视觉模型服务。
          请勿上传敏感或无授权图片；AI 输出可能有误，发布前请再次核对。
        </p>
      </div>

      <div class="section">
        <h2 class="label">2. 补充创作提示（可选）</h2>
        <div class="form-row">
          <div class="form-field">
            <label for="product-name">主题或名称</label>
            <el-input
              id="product-name"
              v-model="form.productName"
              placeholder="例如：秋日湖景 / 柠檬气泡水"
            />
          </div>
          <div class="form-field">
            <label for="target-audience">目标读者</label>
            <el-input
              id="target-audience"
              v-model="form.targetAudience"
              placeholder="例如：户外爱好者"
            />
          </div>
          <div class="form-field">
            <label for="tone">表达风格</label>
            <el-input id="tone" v-model="form.tone" placeholder="例如：轻松自然" />
          </div>
        </div>
      </div>

      <div class="section">
        <el-button
          type="primary"
          size="large"
          :loading="status === 'loading'"
          :disabled="!imageFile"
          @click="handleGenerate"
        >
          {{ status === 'loading' ? '生成中...' : '生成初稿' }}
        </el-button>
      </div>

      <el-alert
        v-if="status === 'error'"
        :title="errorMessage || '生成失败，请重试'"
        type="error"
        show-icon
        class="alert"
        role="alert"
      />

      <div
        v-if="status === 'success' || (status === 'error' && errorRetryable)"
        class="section"
      >
        <el-button
          type="default"
          size="large"
          :icon="RefreshRight"
          :disabled="!imageFile"
          @click="handleGenerate"
        >
          重新生成
        </el-button>
      </div>

      <div v-if="status === 'success'" class="result" aria-live="polite">
        <div class="result-header">
          <h2>生成结果</h2>
          <el-button type="success" :icon="DocumentCopy" @click="copyAll">
            复制全部文案
          </el-button>
        </div>

        <details class="image-summary-details">
          <summary>图片理解摘要</summary>
          <p>{{ result.image_summary }}</p>
        </details>

        <div class="result-block">
          <h3>标题</h3>
          <p class="title-text">{{ result.title }}</p>
        </div>

        <div class="result-block">
          <h3>正文</h3>
          <p class="body-text">{{ result.body }}</p>
        </div>

        <div class="result-block">
          <h3>话题标签</h3>
          <div class="tags">
            <el-tag v-for="tag in result.tags" :key="tag" type="primary">{{ tag }}</el-tag>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.workspace-page {
  width: 100%;
}

.workspace-heading {
  margin: 38px auto 28px;
  text-align: center;
}

.title {
  margin: 0 0 8px;
  color: #1f2937;
  font-size: 28px;
  text-align: center;
}

.title:focus-visible {
  outline: 3px solid rgba(177, 15, 42, 0.3);
  outline-offset: 4px;
}

.subtitle {
  margin: 0 0 32px;
  color: #6b7280;
  text-align: center;
}

.card {
  box-sizing: border-box;
  width: min(100%, 720px);
  margin: 0 auto;
  padding: 28px;
  border-radius: 12px;
  background: #fff;
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
  margin-top: 0;
  margin-bottom: 12px;
  color: #374151;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.5;
}

.upload :deep(.el-upload__text em) {
  color: #a70f2a;
  font-weight: 700;
}

.privacy-note {
  margin: 16px 0 0;
  padding: 12px 14px;
  border-left: 3px solid #c1122f;
  border-radius: 6px;
  color: #5f5661;
  background: #fff5f6;
  font-size: 13px;
  line-height: 1.65;
}

.restored-history-note {
  margin-bottom: 16px;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form-field {
  display: grid;
  gap: 7px;
  text-align: left;
}

.form-field label {
  color: #4b4450;
  font-size: 14px;
  font-weight: 650;
}

.form-field :deep(.el-input__wrapper) {
  min-height: 44px;
}

.form-field :deep(.el-input__inner) {
  font-size: 16px;
}

.preview {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  margin-top: 16px;
}

.preview img {
  max-width: 100%;
  max-height: 300px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.heif-preview {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 8px;
  width: min(100%, 480px);
  min-height: 160px;
  padding: 24px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  color: #334155;
  background: #f8fafc;
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
  align-items: center;
  justify-content: space-between;
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
  margin-bottom: 8px;
  color: #6b7280;
  font-size: 14px;
}

.result-block p {
  margin: 0;
  color: #1f2937;
  line-height: 1.6;
}

.image-summary-details {
  box-sizing: border-box;
  width: 100%;
  margin: 0 0 20px;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #f8fafc;
}

.image-summary-details summary {
  box-sizing: border-box;
  min-height: 44px;
  padding: 11px 14px;
  color: #3f3742;
  font-weight: 650;
  line-height: 1.5;
  overflow-wrap: anywhere;
  cursor: pointer;
}

.image-summary-details summary::marker {
  color: #b10f2a;
}

.image-summary-details summary:focus-visible {
  outline: 3px solid rgba(177, 15, 42, 0.32);
  outline-offset: -3px;
}

.image-summary-details[open] summary {
  border-bottom: 1px solid #e5e7eb;
}

.image-summary-details p {
  margin: 0;
  padding: 14px;
  color: #1f2937;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.title-text {
  color: #b10f2a;
  font-size: 18px;
  font-weight: 600;
}

.body-text {
  white-space: pre-line;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

@media (max-width: 640px) {
  .card {
    padding: 20px 16px;
  }

  .result-header {
    align-items: stretch;
    flex-direction: column;
    gap: 12px;
  }
}
</style>
