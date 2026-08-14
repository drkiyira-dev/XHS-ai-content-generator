<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  ElAlert,
  ElButton,
  ElIcon,
  ElInput,
  ElMessage,
  ElRadioButton,
  ElRadioGroup,
  ElSwitch,
  ElTag,
  ElUpload
} from 'element-plus'
import type { UploadInstance } from 'element-plus'
import {
  Close,
  Collection,
  Delete,
  DocumentCopy,
  EditPen,
  MagicStick,
  Picture,
  Plus,
  RefreshRight,
  UploadFilled,
  Warning
} from '@element-plus/icons-vue'

import { USE_MOCK } from '../services/api'
import type { RiskField, RiskSeverity } from '../services/generation'
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
  currentVersion,
  previousVersion,
  hasResult,
  isGenerating,
  isRegenerating,
  configState,
  draftDirty,
  riskSnapshotStale,
  handleImageChange,
  clearImage,
  handleGenerate,
  copyAll,
  copyVersion,
  restoreGeneratedDraft
} = useWorkspace()

const uploadRef = ref<UploadInstance>()
const titleInputRef = ref<{ focus: () => void } | null>(null)
const bodyInputRef = ref<{ focus: () => void } | null>(null)
const firstTagInputRef = ref<{ focus: () => void } | null>(null)
const newTag = ref('')
const previousDrawerOpen = ref(false)
const comparisonExpanded = ref(false)

const RISK_SEVERITY_LABELS: Record<RiskSeverity, string> = {
  low: '低',
  medium: '中',
  high: '高'
}
const RISK_FIELD_LABELS: Record<RiskField, string> = {
  title: '标题',
  body: '正文',
  tags: '话题标签'
}

const preferenceSummary = computed(() => {
  const emojiLabel = form.emojiLevel === 'off'
    ? '关闭 Emoji'
    : form.emojiLevel === 'expressive'
      ? '丰富 Emoji'
      : '轻量 Emoji'
  return `${emojiLabel} · ${form.relatedTags ? '本地词库标签' : '不补充本地标签'}`
})

const primaryGenerateLabel = computed(() => {
  if (isGenerating.value) {
    if (USE_MOCK) {
      return hasResult.value ? '正在切换演示版本...' : '正在准备演示数据...'
    }
    return hasResult.value ? '正在生成新版本...' : '生成中...'
  }
  if (USE_MOCK) {
    return hasResult.value
      ? '保留旧稿并切换演示版本'
      : '运行交互演示（不会识图）'
  }
  return hasResult.value ? '保留旧稿并重新生成' : '生成初稿'
})

const activeRiskFields = computed(() => new Set(
  currentVersion.value?.server.risk_assessment.findings.map(finding => finding.field) ?? []
))

const canRemoveTag = computed(() => (
  currentVersion.value?.draft.tags.length ?? 0
) > 3)

function riskTagType(severity: RiskSeverity): 'info' | 'warning' | 'danger' {
  if (severity === 'high') {
    return 'danger'
  }
  return severity === 'medium' ? 'warning' : 'info'
}

function normalizeTag(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) {
    return ''
  }
  return trimmed.startsWith('#') ? trimmed : `#${trimmed}`
}

function addTag(): void {
  const version = currentVersion.value
  if (version === null) {
    return
  }
  if (version.draft.tags.length >= 5) {
    ElMessage.warning('最多保留 5 个话题标签。')
    return
  }
  const normalized = normalizeTag(newTag.value)
  if (!normalized) {
    ElMessage.warning('请输入话题标签。')
    return
  }
  if (Array.from(normalized).length > 100) {
    ElMessage.warning('单个话题标签不能超过 100 个字符。')
    return
  }
  if (version.draft.tags.includes(normalized)) {
    ElMessage.warning('这个话题标签已经存在。')
    return
  }
  version.draft.tags.push(normalized)
  newTag.value = ''
}

function removeTag(index: number): void {
  const version = currentVersion.value
  if (version === null || version.draft.tags.length <= 3) {
    ElMessage.warning('至少保留 3 个话题标签。')
    return
  }
  version.draft.tags.splice(index, 1)
}

function normalizeEditedTag(index: number): void {
  const version = currentVersion.value
  if (version === null || !version.draft.tags[index]) {
    return
  }
  const normalized = normalizeTag(version.draft.tags[index])
  if (!normalized) {
    if (version.draft.tags.length > 3) {
      version.draft.tags.splice(index, 1)
    } else {
      version.draft.tags[index] = version.server.tags[index] ?? `#话题标签${index + 1}`
      ElMessage.warning('至少保留 3 个非空话题标签。')
    }
    return
  }
  if (version.draft.tags.some((tag, tagIndex) => tagIndex !== index && tag === normalized)) {
    ElMessage.warning('话题标签不能重复。')
    version.draft.tags[index] = version.server.tags[index] ?? '#图文记录'
    return
  }
  version.draft.tags[index] = normalized
}

function captureFirstTagInput(value: unknown, index: number): void {
  if (index === 0) {
    firstTagInputRef.value = value as { focus: () => void } | null
  }
}

async function focusRiskField(field: RiskField): Promise<void> {
  await nextTick()
  if (field === 'title') {
    titleInputRef.value?.focus()
  } else if (field === 'body') {
    bodyInputRef.value?.focus()
  } else {
    firstTagInputRef.value?.focus()
  }
}

function formatVersionTime(value: string): string {
  const createdAt = new Date(value)
  if (Number.isNaN(createdAt.getTime())) {
    return '时间未知'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short'
  }).format(createdAt)
}

function togglePreviousDrawer(): void {
  if (previousVersion.value === null) {
    return
  }
  previousDrawerOpen.value = !previousDrawerOpen.value
  if (!previousDrawerOpen.value) {
    comparisonExpanded.value = false
  }
}

watch(
  imageResetEpoch,
  () => {
    uploadRef.value?.clearFiles()
  },
  { flush: 'sync' }
)

watch(
  () => currentVersion.value?.key,
  (key, previousKey) => {
    newTag.value = ''
    if (key !== undefined && previousKey !== undefined && previousVersion.value !== null) {
      previousDrawerOpen.value = true
      comparisonExpanded.value = false
    }
  },
  { flush: 'post' }
)

watch(previousVersion, value => {
  if (value === null) {
    previousDrawerOpen.value = false
    comparisonExpanded.value = false
  }
})
</script>

<template>
  <section
    id="generator-panel"
    class="workspace-page"
    aria-labelledby="page-title"
    :aria-busy="isGenerating"
  >
    <h1 id="page-title" class="sr-only" tabindex="-1">生成小红书内容初稿</h1>

    <div class="workspace-grid">
      <aside class="creation-rail" aria-labelledby="creation-rail-title">
        <div class="rail-section rail-section--image">
          <h2 id="creation-rail-title" class="rail-heading">1. 已上传图片</h2>

          <el-alert
            v-if="restoredFromHistory"
            class="restored-history-note"
            title="已载入历史生成结果"
            description="这张图片仅用于回忆与预览；重新生成前仍需重新上传本地图片。"
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
            aria-describedby="upload-help upload-privacy-help"
          >
            <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
            <div class="el-upload__text">
              拖拽图片到这里，或 <em>点击上传</em>
            </div>
            <template #tip>
              <div id="upload-help" class="el-upload__tip">
                JPG / JPEG / PNG / WebP / HEIC / HEIF，最大 10MB
              </div>
            </template>
          </el-upload>

          <div v-else class="preview">
            <div
              v-if="imageIsHeif"
              class="heif-preview"
              role="img"
              aria-label="HEIC 或 HEIF 图片已选择"
            >
              <el-icon aria-hidden="true"><Picture /></el-icon>
              <strong>{{ imageFile?.name }}</strong>
              <span>浏览器不直接预览此格式，将由后端安全转换为 JPEG。</span>
            </div>
            <img
              v-else
              :src="imagePreviewUrl"
              :alt="restoredFromHistory ? '历史记录图片预览' : '已选择图片预览'"
              referrerpolicy="no-referrer"
            />
            <span v-if="!restoredFromHistory" class="image-ready-badge">已校验</span>
            <el-button
              class="replace-image-button"
              :icon="Delete"
              :disabled="isGenerating"
              @click="clearImage"
            >
              重新上传
            </el-button>
          </div>
        </div>

        <div class="rail-divider"></div>

        <div class="rail-section">
          <h2 class="rail-heading">2. 补充创作提示（可选）</h2>
          <div class="form-fields">
            <div class="form-field">
              <label for="product-name">主题或名称</label>
              <el-input
                id="product-name"
                v-model="form.productName"
                :disabled="isGenerating"
                placeholder="例如：秋日湖景 / 柠檬气泡水"
              />
            </div>
            <div class="form-field">
              <label for="target-audience">目标读者</label>
              <el-input
                id="target-audience"
                v-model="form.targetAudience"
                :disabled="isGenerating"
                placeholder="例如：户外爱好者"
              />
            </div>
            <div class="form-field">
              <label for="tone">表达风格</label>
              <el-input
                id="tone"
                v-model="form.tone"
                :disabled="isGenerating"
                placeholder="例如：轻松自然"
              />
            </div>
          </div>
        </div>

        <div class="rail-section">
          <h2 class="rail-heading">3. 创作偏好</h2>
          <details class="preference-panel">
            <summary>
              <el-icon class="preference-summary-icon" aria-hidden="true"><EditPen /></el-icon>
              <span>{{ preferenceSummary }}</span>
            </summary>
            <div class="preference-content">
              <fieldset class="enhancement-field">
                <legend id="emoji-style-label">Emoji 风格</legend>
                <el-radio-group
                  v-model="form.emojiLevel"
                  :disabled="isGenerating"
                  aria-labelledby="emoji-style-label"
                  aria-describedby="emoji-style-help"
                >
                  <el-radio-button value="off">关闭</el-radio-button>
                  <el-radio-button value="light">轻量</el-radio-button>
                  <el-radio-button value="expressive">丰富</el-radio-button>
                </el-radio-group>
                <p id="emoji-style-help">只调整 Emoji 数量，不改变图片事实判断。</p>
              </fieldset>

              <fieldset class="enhancement-field">
                <legend>本地词库相关标签</legend>
                <div class="related-tags-control">
                  <el-switch
                    v-model="form.relatedTags"
                    :disabled="isGenerating"
                    aria-label="本地词库相关标签"
                    aria-describedby="related-tags-help"
                  />
                  <span>{{ form.relatedTags ? '开启' : '关闭' }}</span>
                </div>
                <p id="related-tags-help">不读取平台热门榜或实时趋势。</p>
              </fieldset>
            </div>
          </details>
        </div>

        <el-button
          class="primary-generate-button"
          type="primary"
          size="large"
          :icon="RefreshRight"
          :loading="isGenerating"
          :disabled="!imageFile || isGenerating"
          @click="handleGenerate"
        >
          {{ primaryGenerateLabel }}
        </el-button>
        <p v-if="!imageFile" class="generation-requirement" role="status">
          请先上传一张图片，历史缩略图不能直接再次提交。
        </p>
        <p v-else-if="hasResult" class="generation-safety-note">
          重新生成失败或未通过检查时会保留当前稿件。
        </p>

        <el-alert
          v-if="errorMessage"
          :title="errorMessage"
          :type="errorRetryable ? 'warning' : 'error'"
          show-icon
          :closable="false"
          class="generation-error"
          role="alert"
        />

        <div
          v-if="configState === 'dirty'"
          class="config-callout"
          role="status"
          aria-live="polite"
        >
          <el-icon aria-hidden="true"><Warning /></el-icon>
          <div>
            <strong>设置已修改，当前仍是上一版结果</strong>
            <p>新设置只会在再次生成后生效。</p>
          </div>
          <el-button
            :disabled="!imageFile || isGenerating"
            @click="handleGenerate"
          >
            使用新设置生成
          </el-button>
        </div>

        <div v-else-if="configState === 'unknown'" class="config-callout config-callout--neutral">
          <el-icon aria-hidden="true"><Warning /></el-icon>
          <div>
            <strong>历史记录未保存原创作设置</strong>
            <p>当前表单不是这篇历史稿件的生成条件。</p>
          </div>
        </div>

        <details v-if="currentVersion" class="left-check-details">
          <summary>检查详情</summary>
          <p>
            本地规则 · {{ currentVersion.server.risk_assessment.rule_version }} ·
            {{ currentVersion.server.risk_assessment.findings.length }} 项提示
          </p>
        </details>

        <p id="upload-privacy-help" class="privacy-note">
          <template v-if="USE_MOCK">
            Mock 模式只在浏览器内使用图片进行预览和历史缩略图演示，
            不会发送后端或模型，也不会生成图片答案。
          </template>
          <template v-else>
            生成时，图片会经本地后端发送至已配置的第三方视觉模型服务。
            请勿上传敏感或无授权图片；AI 输出请在发布前人工核对。
          </template>
        </p>
      </aside>

      <div
        class="output-grid"
        :class="{
          'output-grid--drawer-open': previousDrawerOpen && previousVersion,
          'output-grid--expanded': comparisonExpanded
        }"
      >
        <section class="result-pane" aria-labelledby="generation-result-title">
          <div
            v-if="status === 'loading' && !hasResult"
            data-testid="initial-generation-skeleton"
            class="result-skeleton-panel"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <div class="result-header result-loading-header">
              <div>
                <p class="result-loading-kicker">{{ USE_MOCK ? '交互演示' : '正在生成' }}</p>
                <h2 id="generation-result-title">
                  {{ USE_MOCK ? '正在准备演示数据' : '正在分析图片并生成初稿' }}
                </h2>
              </div>
              <span class="result-loading-mark" aria-hidden="true"></span>
            </div>
            <p class="result-loading-note">
              {{ USE_MOCK
                ? '不会读取图片内容；这里只模拟加载、版本与编辑交互。'
                : '模型处理可能需要一些时间。结果会在完成格式与内容检查后一次性展示。' }}
            </p>
            <div class="result-skeleton" aria-hidden="true">
              <div class="result-skeleton-summary">
                <span class="result-skeleton-label">图片理解摘要</span>
                <span class="skeleton-shape skeleton-line skeleton-line--wide"></span>
                <span class="skeleton-shape skeleton-line skeleton-line--medium"></span>
              </div>
              <div class="result-skeleton-block">
                <span class="result-skeleton-label">标题</span>
                <span class="skeleton-shape skeleton-line skeleton-line--title"></span>
              </div>
              <div class="result-skeleton-block">
                <span class="result-skeleton-label">正文</span>
                <span class="skeleton-shape skeleton-line skeleton-line--wide"></span>
                <span class="skeleton-shape skeleton-line skeleton-line--wide"></span>
                <span class="skeleton-shape skeleton-line skeleton-line--body"></span>
              </div>
              <div class="result-skeleton-block">
                <span class="result-skeleton-label">话题标签</span>
                <div class="skeleton-tags">
                  <span class="skeleton-shape skeleton-tag"></span>
                  <span class="skeleton-shape skeleton-tag"></span>
                  <span class="skeleton-shape skeleton-tag"></span>
                  <span class="skeleton-shape skeleton-tag"></span>
                </div>
              </div>
            </div>
          </div>

          <div v-else-if="!currentVersion" class="empty-result">
            <span class="empty-result-icon" aria-hidden="true"><EditPen /></span>
            <p class="empty-result-kicker">编辑式工作台</p>
            <h2 id="generation-result-title">结果会在这里成为可编辑草稿</h2>
            <p>上传图片并生成后，可以先查看风险提示，再调整标题、正文与话题标签。</p>
          </div>

          <section
            v-else
            :key="currentVersion.key"
            class="result-canvas result--ready"
            role="region"
            aria-labelledby="generation-result-title"
          >
            <el-alert
              v-if="USE_MOCK"
              class="mock-result-warning"
              title="交互演示数据｜未读取上传图片"
              description="以下标题、正文、标签和风险状态都是界面占位数据，不是图片生成答案。"
              type="warning"
              show-icon
              :closable="false"
            />
            <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">
              文案生成完成，共 {{ currentVersion.draft.tags.length }} 个话题标签，
              {{ currentVersion.server.risk_assessment.findings.length }} 项本地规则提示。
            </p>

            <div v-if="isRegenerating" class="regeneration-banner" role="status">
              <span class="result-loading-mark" aria-hidden="true"></span>
              <div>
                <strong>正在生成新版本</strong>
                <p>当前稿件仍可查看和复制；新结果通过检查后才会替换。</p>
              </div>
            </div>

            <header class="result-titlebar">
              <div>
                <p class="result-eyebrow">当前编辑稿</p>
                <h2 id="generation-result-title">
                  {{ USE_MOCK ? '交互示例（非图片答案）' : '生成结果' }}
                  <el-icon aria-hidden="true"><MagicStick /></el-icon>
                </h2>
              </div>
              <div class="version-controls">
                <el-button
                  :icon="Collection"
                  :disabled="!previousVersion"
                  :aria-expanded="previousDrawerOpen"
                  aria-controls="previous-version-drawer"
                  @click="togglePreviousDrawer"
                >
                  版本 {{ previousVersion ? 2 : 1 }}
                </el-button>
                <span>{{ previousVersion ? '当前 · 上一版可比较' : '当前版本' }}</span>
              </div>
            </header>

            <section class="risk-overview" aria-labelledby="risk-assessment-title">
              <div class="risk-overview-heading">
                <div>
                  <h3 id="risk-assessment-title">
                    {{ USE_MOCK ? '风险检查示意（未执行真实检查）' : '发布前风险提示（非平台审核）' }}
                  </h3>
                  <p>
                    {{ USE_MOCK
                      ? 'Mock 只展示风险区布局，0 项不代表图片或文本已经通过检查。'
                      : '不能代表平台审核，也不能预测限流或处罚；发布前请人工核对。' }}
                  </p>
                </div>
                <el-tag
                  :type="USE_MOCK
                    ? 'info'
                    : (currentVersion.server.risk_assessment.findings.length ? 'warning' : 'success')"
                  effect="plain"
                >
                  {{ USE_MOCK
                    ? '未检查'
                    : `${currentVersion.server.risk_assessment.findings.length} 项提示` }}
                </el-tag>
              </div>

              <template v-if="currentVersion.server.risk_assessment.findings.length">
                <ul class="risk-findings">
                  <li
                    v-for="(finding, index) in currentVersion.server.risk_assessment.findings"
                    :key="`${finding.code}-${finding.field}-${index}`"
                  >
                    <div class="risk-finding-heading">
                      <el-tag :type="riskTagType(finding.severity)" size="small">
                        {{ RISK_SEVERITY_LABELS[finding.severity] }}风险
                      </el-tag>
                      <strong>{{ RISK_FIELD_LABELS[finding.field] }}</strong>
                      <el-button text @click="focusRiskField(finding.field)">
                        定位字段
                      </el-button>
                    </div>
                    <p>{{ finding.reason }}</p>
                    <p><strong>修改建议：</strong>{{ finding.suggestion }}</p>
                  </li>
                </ul>
              </template>
              <p v-else class="risk-empty">
                {{ USE_MOCK
                  ? '当前为交互占位状态，没有执行真实内容风险扫描。'
                  : '当前规则暂未发现提示，仍需人工检查图片事实、表达和账号发布要求。' }}
              </p>

              <details class="risk-technical-details">
                <summary>检查详情</summary>
                <p>规则版本：{{ currentVersion.server.risk_assessment.rule_version }}</p>
                <ul v-if="currentVersion.server.risk_assessment.findings.length">
                  <li
                    v-for="(finding, index) in currentVersion.server.risk_assessment.findings"
                    :key="`${finding.code}-detail-${index}`"
                  >
                    {{ finding.code }} · {{ RISK_FIELD_LABELS[finding.field] }}
                  </li>
                </ul>
              </details>
            </section>

            <div class="result-actions">
              <el-button :icon="DocumentCopy" @click="copyAll">复制全部文案</el-button>
              <el-button
                :icon="RefreshRight"
                :disabled="!imageFile || isGenerating"
                @click="handleGenerate"
              >
                使用新设置生成
              </el-button>
              <span v-if="previousVersion" class="previous-version-indicator">
                结果来自当前版本
              </span>
            </div>

            <details class="image-summary-details">
              <summary>图片理解摘要</summary>
              <p>{{ currentVersion.server.image_summary }}</p>
            </details>

            <div
              v-if="riskSnapshotStale"
              class="draft-stale-note"
              role="status"
              aria-live="polite"
            >
              <el-icon aria-hidden="true"><Warning /></el-icon>
              <div>
                <strong>内容已在本地修改</strong>
                <p>风险提示仍对应生成时版本，编辑后的文字尚未重新检测，也不会自动写回历史记录。</p>
              </div>
              <el-button :disabled="isGenerating" @click="restoreGeneratedDraft">
                恢复生成原稿
              </el-button>
            </div>

            <div class="editor-field">
              <div class="editor-label-row">
                <label for="draft-title">标题</label>
                <span>{{ Array.from(currentVersion.draft.title).length }} / 20</span>
              </div>
              <el-input
                id="draft-title"
                ref="titleInputRef"
                v-model="currentVersion.draft.title"
                maxlength="20"
                :disabled="isGenerating"
                :aria-describedby="activeRiskFields.has('title') ? 'title-risk-note' : undefined"
              />
              <p v-if="activeRiskFields.has('title')" id="title-risk-note" class="field-risk-note">
                标题有本地规则提示，请结合上方建议核对。
              </p>
            </div>

            <div class="editor-field">
              <div class="editor-label-row">
                <label for="draft-body">正文</label>
                <span>{{ Array.from(currentVersion.draft.body).length }} / 10000</span>
              </div>
              <el-input
                id="draft-body"
                ref="bodyInputRef"
                v-model="currentVersion.draft.body"
                type="textarea"
                :rows="10"
                maxlength="10000"
                resize="vertical"
                :disabled="isGenerating"
                :aria-describedby="activeRiskFields.has('body') ? 'body-risk-note' : undefined"
              />
              <p v-if="activeRiskFields.has('body')" id="body-risk-note" class="field-risk-note">
                正文有本地规则提示，请结合上方建议核对。
              </p>
            </div>

            <fieldset class="editor-field tag-editor">
              <div class="editor-label-row">
                <legend>话题标签</legend>
                <span>{{ currentVersion.draft.tags.length }} / 5</span>
              </div>
              <p v-if="activeRiskFields.has('tags')" id="tags-risk-note" class="field-risk-note">
                话题标签有本地规则提示，请结合上方建议核对。
              </p>
              <div class="editable-tags" :aria-describedby="activeRiskFields.has('tags') ? 'tags-risk-note' : undefined">
                <div
                  v-for="(tag, index) in currentVersion.draft.tags"
                  :key="`${currentVersion.key}-${index}`"
                  class="editable-tag"
                >
                  <el-input
                    :ref="value => captureFirstTagInput(value, index)"
                    v-model="currentVersion.draft.tags[index]"
                    :aria-label="`话题标签 ${index + 1}`"
                    maxlength="100"
                    :disabled="isGenerating"
                    @blur="normalizeEditedTag(index)"
                  />
                  <el-button
                    text
                    :icon="Close"
                    :disabled="isGenerating || !canRemoveTag"
                    :aria-label="`删除话题标签 ${tag}`"
                    @click="removeTag(index)"
                  />
                </div>
              </div>
              <div v-if="currentVersion.draft.tags.length < 5" class="add-tag-row">
                <el-input
                  v-model="newTag"
                  maxlength="100"
                  aria-label="新增话题标签"
                  placeholder="输入相关标签"
                  :disabled="isGenerating"
                  @keyup.enter="addTag"
                />
                <el-button :icon="Plus" :disabled="isGenerating" @click="addTag">
                  添加话题标签
                </el-button>
              </div>
            </fieldset>

            <p v-if="draftDirty" class="local-draft-footnote">
              本地编辑仅保留在当前页面会话中；复制会使用当前编辑稿。
            </p>
          </section>
        </section>

        <aside
          v-if="previousDrawerOpen && previousVersion"
          id="previous-version-drawer"
          class="previous-version-drawer"
          aria-labelledby="previous-version-title"
        >
          <header class="previous-drawer-header">
            <div>
              <p class="result-eyebrow">版本对比</p>
              <h2 id="previous-version-title">上一版</h2>
            </div>
            <el-button
              text
              :icon="Close"
              aria-label="关闭上一版对比"
              @click="togglePreviousDrawer"
            />
          </header>
          <p class="previous-version-time">
            更新时间：{{ formatVersionTime(previousVersion.server.created_at) }}
          </p>

          <div class="previous-version-block">
            <h3>标题</h3>
            <p>{{ previousVersion.draft.title }}</p>
          </div>
          <div class="previous-version-block">
            <h3>正文</h3>
            <p :class="{ 'previous-body--collapsed': !comparisonExpanded }">
              {{ previousVersion.draft.body }}
            </p>
          </div>
          <div v-if="comparisonExpanded" class="previous-version-block">
            <h3>话题标签</h3>
            <div class="previous-tags">
              <el-tag v-for="tag in previousVersion.draft.tags" :key="tag" effect="plain">
                {{ tag }}
              </el-tag>
            </div>
          </div>

          <el-button class="drawer-action" @click="comparisonExpanded = !comparisonExpanded">
            {{ comparisonExpanded ? '收起对比' : '展开对比' }}
          </el-button>
          <el-button
            class="drawer-action"
            :icon="DocumentCopy"
            @click="copyVersion(previousVersion)"
          >
            复制上一版
          </el-button>

          <div class="drawer-hint">
            <strong>提示</strong>
            <p>版本对比只保留在当前页面会话，不会提交给第三方。</p>
          </div>
        </aside>
      </div>
    </div>
  </section>
</template>

<style scoped>
.workspace-page {
  width: 100%;
  min-width: 0;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(340px, 400px) minmax(0, 1fr);
  min-height: calc(100vh - 210px);
  overflow: hidden;
  border: 1px solid #eee5e7;
  border-radius: 22px;
  background: #fff;
  box-shadow: 0 18px 54px rgba(56, 31, 38, 0.08);
}

.creation-rail {
  min-width: 0;
  padding: 24px 22px;
  border-right: 1px solid #eee5e7;
  background: #fffdfd;
}

.rail-section {
  min-width: 0;
  margin-bottom: 20px;
}

.rail-section--image {
  margin-bottom: 16px;
}

.rail-heading {
  margin: 0 0 12px;
  color: #2a2227;
  font-size: 15px;
  font-weight: 760;
  line-height: 1.5;
}

.rail-divider {
  height: 1px;
  margin: 18px 0;
  background: #eee5e7;
}

.upload {
  width: 100%;
}

.upload :deep(.el-upload) {
  width: 100%;
}

.upload :deep(.el-upload-dragger) {
  box-sizing: border-box;
  width: 100%;
  min-height: 190px;
  padding: 42px 18px;
  border-color: #ddcfd3;
  border-radius: 15px;
  background: #fffafa;
}

.upload :deep(.el-upload__text em) {
  color: #a70f2a;
  font-weight: 750;
}

.preview {
  position: relative;
  display: grid;
  gap: 10px;
}

.preview img,
.heif-preview {
  box-sizing: border-box;
  width: 100%;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border: 1px solid #e5dadd;
  border-radius: 13px;
  background: #f7f1f3;
}

.preview img {
  display: block;
  object-fit: cover;
}

.heif-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 7px;
  padding: 20px;
  color: #5d5055;
  text-align: center;
}

.heif-preview :deep(svg) {
  width: 30px;
  height: 30px;
  color: #a90f2b;
}

.heif-preview strong,
.heif-preview span {
  max-width: 100%;
  overflow-wrap: anywhere;
}

.heif-preview span {
  color: #7b7074;
  font-size: 12px;
}

.image-ready-badge {
  position: absolute;
  top: 10px;
  right: 10px;
  padding: 5px 8px;
  border-radius: 999px;
  color: #fff;
  background: #a90f2b;
  font-size: 11px;
  font-weight: 700;
}

.replace-image-button {
  justify-self: start;
}

.restored-history-note {
  margin-bottom: 12px;
}

.form-fields {
  display: grid;
  gap: 12px;
}

.form-field {
  display: grid;
  gap: 6px;
}

.form-field label,
.editor-label-row label,
.editor-label-row legend {
  color: #40363b;
  font-size: 13px;
  font-weight: 720;
}

.form-field :deep(.el-input__wrapper) {
  min-height: 40px;
  border-radius: 9px;
  box-shadow: 0 0 0 1px #e2d8db inset;
}

.preference-panel,
.left-check-details,
.risk-technical-details {
  border: 1px solid #e8dfe2;
  border-radius: 11px;
  background: #fff;
}

.preference-panel summary,
.left-check-details summary,
.risk-technical-details summary {
  box-sizing: border-box;
  min-height: 44px;
  padding: 11px 13px;
  color: #4b4045;
  font-size: 13px;
  font-weight: 680;
  line-height: 1.5;
  cursor: pointer;
}

.preference-panel summary {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preference-summary-icon {
  display: grid;
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  place-items: center;
  border-radius: 50%;
  color: #9b1029;
  background: #fff0f3;
}

.preference-content {
  display: grid;
  gap: 12px;
  padding: 0 13px 13px;
}

.enhancement-field {
  min-width: 0;
  margin: 0;
  padding: 12px;
  border: 1px solid #eee5e7;
  border-radius: 9px;
  background: #fcf9fa;
}

.enhancement-field legend {
  padding: 0 4px;
  color: #4b4045;
  font-size: 12px;
  font-weight: 700;
}

.enhancement-field p {
  margin: 8px 0 0;
  color: #786d72;
  font-size: 11px;
  line-height: 1.55;
}

.enhancement-field :deep(.el-radio-group) {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  width: 100%;
}

.enhancement-field :deep(.el-radio-button__inner) {
  width: 100%;
  padding-inline: 7px;
}

.related-tags-control {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 32px;
  color: #4b4045;
  font-size: 13px;
  font-weight: 650;
}

.primary-generate-button {
  width: 100%;
  min-height: 48px;
  margin-top: 2px;
  --el-button-bg-color: #b30f2e;
  --el-button-border-color: #b30f2e;
  --el-button-hover-bg-color: #981026;
  --el-button-hover-border-color: #981026;
  --el-button-active-bg-color: #7f0e21;
  --el-button-active-border-color: #7f0e21;
}

.generation-requirement,
.generation-safety-note {
  margin: 8px 2px 0;
  color: #7c7075;
  font-size: 11px;
  line-height: 1.55;
}

.generation-error {
  margin-top: 14px;
}

.config-callout {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  margin-top: 14px;
  padding: 12px;
  border: 1px solid #efcf85;
  border-radius: 11px;
  color: #704d0f;
  background: #fff9e9;
}

.config-callout > .el-button {
  grid-column: 1 / -1;
  justify-self: end;
}

.config-callout--neutral {
  border-color: #ded4d8;
  color: #5f555a;
  background: #faf7f8;
}

.config-callout strong,
.draft-stale-note strong {
  font-size: 12px;
}

.config-callout p,
.draft-stale-note p {
  margin: 3px 0 0;
  font-size: 11px;
  line-height: 1.5;
}

.left-check-details {
  margin-top: 14px;
}

.left-check-details p,
.risk-technical-details p,
.risk-technical-details ul {
  margin: 0;
  padding: 0 13px 13px;
  color: #786e72;
  font-size: 11px;
  line-height: 1.6;
}

.risk-technical-details ul {
  padding-left: 31px;
}

.privacy-note {
  margin: 14px 0 0;
  padding: 12px;
  border: 1px solid #ead8c5;
  border-radius: 11px;
  color: #6c625f;
  background: #fffaf4;
  font-size: 11px;
  line-height: 1.65;
}

.output-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
  background: #fff;
}

.output-grid--drawer-open {
  grid-template-columns: minmax(0, 1fr) minmax(250px, 290px);
}

.output-grid--expanded {
  grid-template-columns: minmax(0, 1fr) minmax(340px, 410px);
}

.result-pane {
  min-width: 0;
  padding: 30px 32px 38px;
}

.empty-result {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  min-height: 560px;
  color: #6d6267;
  text-align: center;
}

.empty-result-icon {
  display: grid;
  width: 58px;
  height: 58px;
  place-items: center;
  margin-bottom: 18px;
  border-radius: 18px;
  color: #a20f2a;
  background: #fff0f3;
}

.empty-result-icon svg {
  width: 26px;
  height: 26px;
}

.empty-result-kicker,
.result-eyebrow,
.result-loading-kicker {
  margin: 0 0 5px;
  color: #aa102d;
  font-size: 11px;
  font-weight: 780;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.empty-result h2 {
  max-width: 520px;
  margin: 0;
  color: #292126;
  font-size: clamp(24px, 3vw, 34px);
}

.empty-result > p:last-child {
  max-width: 520px;
  margin: 13px 0 0;
  line-height: 1.7;
}

.result-canvas {
  min-width: 0;
}

.result--ready {
  animation: result-enter 320ms cubic-bezier(0.22, 1, 0.36, 1) both;
}

.regeneration-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;
  padding: 12px 14px;
  border: 1px solid #ead6da;
  border-radius: 11px;
  color: #5a444a;
  background: #fff7f8;
}

.regeneration-banner p {
  margin: 3px 0 0;
  color: #786b70;
  font-size: 12px;
}

.result-titlebar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 22px;
}

.result-titlebar h2,
.previous-drawer-header h2 {
  margin: 0;
  color: #241d21;
  font-size: 24px;
}

.result-titlebar h2 > .el-icon {
  color: #d19a13;
  font-size: 18px;
}

.version-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.version-controls > span {
  color: #776d72;
  font-size: 12px;
}

.risk-overview {
  margin-bottom: 18px;
  padding: 17px;
  border: 1px solid #eadfe2;
  border-radius: 13px;
  background: #fffafb;
}

.risk-overview-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.risk-overview-heading h3 {
  margin: 0;
  color: #49383e;
  font-size: 14px;
}

.risk-overview-heading p,
.risk-empty {
  margin: 5px 0 0;
  color: #75696e;
  font-size: 12px;
  line-height: 1.6;
}

.risk-findings {
  display: grid;
  gap: 9px;
  margin: 14px 0 0;
  padding: 0;
  list-style: none;
}

.risk-findings li {
  padding: 11px;
  border: 1px solid #e8dfe1;
  border-radius: 9px;
  background: #fff;
}

.risk-finding-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.risk-finding-heading strong {
  font-size: 12px;
}

.risk-findings p {
  margin: 6px 0 0;
  color: #5f5358;
  font-size: 12px;
  line-height: 1.55;
}

.risk-technical-details {
  margin-top: 12px;
  background: #fff;
}

.result-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 20px;
  padding-bottom: 20px;
  border-bottom: 1px solid #eee5e7;
}

.previous-version-indicator {
  margin-left: auto;
  color: #7c7075;
  font-size: 11px;
}

.image-summary-details {
  margin-bottom: 22px;
  overflow: hidden;
  border: 1px solid #e8dfe2;
  border-radius: 11px;
  background: #faf8f9;
}

.image-summary-details summary {
  box-sizing: border-box;
  min-height: 44px;
  padding: 11px 14px;
  color: #493f44;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.image-summary-details summary:focus-visible {
  outline: 3px solid rgba(177, 15, 42, 0.3);
  outline-offset: -3px;
}

.image-summary-details p {
  margin: 0;
  padding: 0 14px 14px;
  color: #62575c;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.draft-stale-note {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  margin-bottom: 20px;
  padding: 12px;
  border: 1px solid #efcf85;
  border-radius: 11px;
  color: #69470b;
  background: #fff9e9;
}

.editor-field {
  min-width: 0;
  margin: 0 0 22px;
  padding: 0;
  border: 0;
}

.editor-label-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.editor-label-row > span {
  color: #84797d;
  font-size: 11px;
}

.editor-field :deep(.el-input__wrapper),
.editor-field :deep(.el-textarea__inner) {
  border-radius: 9px;
  box-shadow: 0 0 0 1px #ded4d8 inset;
}

.editor-field :deep(.el-input__wrapper) {
  min-height: 44px;
}

.editor-field :deep(.el-textarea__inner) {
  padding: 13px 14px;
  color: #332a2f;
  font-family: inherit;
  line-height: 1.75;
}

.field-risk-note,
.local-draft-footnote {
  margin: 7px 0 0;
  color: #8c5b10;
  font-size: 11px;
  line-height: 1.5;
}

.editable-tags {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.editable-tag {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  border: 1px solid #e2d8db;
  border-radius: 9px;
  background: #fff;
}

.editable-tag :deep(.el-input__wrapper) {
  box-shadow: none;
}

.add-tag-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  margin-top: 10px;
}

.previous-version-drawer {
  min-width: 0;
  padding: 28px 20px;
  border-left: 1px solid #eee5e7;
  background: #fffdfd;
}

.previous-drawer-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.previous-drawer-header h2 {
  font-size: 18px;
}

.previous-version-time {
  margin: 7px 0 28px;
  color: #83787d;
  font-size: 11px;
}

.previous-version-block {
  margin-bottom: 20px;
}

.previous-version-block h3 {
  margin: 0 0 8px;
  color: #4d4247;
  font-size: 12px;
}

.previous-version-block p {
  margin: 0;
  color: #554a4f;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.previous-body--collapsed {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 5;
}

.previous-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.drawer-action {
  width: 100%;
  min-height: 44px;
  margin: 0 0 9px;
}

.drawer-action + .drawer-action {
  margin-left: 0;
}

.drawer-hint {
  margin-top: 50px;
  padding-top: 16px;
  border-top: 1px solid #eee5e7;
  color: #786d72;
}

.drawer-hint strong {
  font-size: 12px;
}

.drawer-hint p {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.6;
}

.result-skeleton-panel {
  min-height: 560px;
  color: #332c31;
}

.result-loading-header,
.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.result-loading-header h2 {
  margin: 0;
  color: #292126;
  font-size: 24px;
}

.result-loading-mark {
  flex: 0 0 auto;
  width: 12px;
  height: 12px;
  border: 3px solid #f2cbd2;
  border-top-color: #b10f2a;
  border-radius: 50%;
  animation: loading-turn 900ms linear infinite;
}

.result-loading-note {
  margin: 12px 0 24px;
  color: #6b6268;
  font-size: 13px;
  line-height: 1.65;
}

.result-skeleton {
  display: grid;
  gap: 22px;
}

.result-skeleton-summary {
  display: grid;
  gap: 10px;
  padding: 15px;
  border: 1px solid #ebe1e4;
  border-radius: 11px;
  background: #fbf8f9;
}

.result-skeleton-block {
  display: grid;
  gap: 9px;
}

.result-skeleton-label {
  color: #746b71;
  font-size: 12px;
  font-weight: 650;
}

.skeleton-shape {
  display: block;
  background: #eee7e9;
  animation: skeleton-pulse 1.35s ease-in-out infinite alternate;
}

.skeleton-line {
  height: 13px;
  border-radius: 999px;
}

.skeleton-line--wide { width: 100%; }
.skeleton-line--medium { width: 72%; }
.skeleton-line--title { width: min(58%, 290px); height: 18px; }
.skeleton-line--body { width: 84%; }

.skeleton-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.skeleton-tag {
  width: 78px;
  height: 28px;
  border-radius: 999px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

summary:focus-visible,
.title:focus-visible {
  outline: 3px solid rgba(177, 15, 42, 0.3);
  outline-offset: 3px;
}

@keyframes loading-turn {
  to { transform: rotate(1turn); }
}

@keyframes skeleton-pulse {
  from { opacity: 0.55; }
  to { opacity: 1; }
}

@keyframes result-enter {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .result--ready,
  .result-loading-mark,
  .skeleton-shape {
    animation: none;
  }
}

@media (max-width: 1180px) {
  .workspace-grid {
    grid-template-columns: minmax(320px, 360px) minmax(0, 1fr);
  }

  .output-grid--drawer-open,
  .output-grid--expanded {
    grid-template-columns: minmax(0, 1fr);
  }

  .previous-version-drawer {
    border-top: 1px solid #eee5e7;
    border-left: 0;
  }
}

@media (max-width: 900px) {
  .workspace-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .creation-rail {
    border-right: 0;
    border-bottom: 1px solid #eee5e7;
  }

  .result-pane {
    padding: 26px 22px 32px;
  }
}

@media (max-width: 640px) {
  .workspace-grid {
    border-radius: 16px;
  }

  .creation-rail,
  .result-pane,
  .previous-version-drawer {
    padding: 20px 16px;
  }

  .result-titlebar,
  .risk-overview-heading,
  .draft-stale-note {
    align-items: stretch;
    grid-template-columns: minmax(0, 1fr);
    flex-direction: column;
  }

  .version-controls {
    justify-content: flex-start;
  }

  .result-actions,
  .add-tag-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .result-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .result-actions > .el-button,
  .add-tag-row > .el-button {
    width: 100%;
    margin-left: 0;
  }

  .previous-version-indicator {
    width: 100%;
    margin-left: 0;
  }

  .editable-tags {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
