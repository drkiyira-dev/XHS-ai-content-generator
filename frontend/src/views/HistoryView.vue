<script setup lang="ts">
import { onMounted } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Delete, DocumentCopy, RefreshRight } from '@element-plus/icons-vue'

import { AUTH_ENABLED } from '../services/api'
import type { GenerationHistoryItem } from '../services/generation'
import { useWorkspace } from '../state/workspace'

const {
  historyStatus,
  historyItems,
  historyCount,
  historyError,
  historyDeletingIds,
  historyLimit,
  loadHistory,
  restoreHistoryItem,
  deleteHistoryItem,
  formatCreatedAt,
  copyGeneration
} = useWorkspace()

async function confirmDelete(item: GenerationHistoryItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除这条历史记录？删除后无法恢复。',
      '确认删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true
      }
    )
  } catch {
    return
  }

  await deleteHistoryItem(item)
}

onMounted(() => {
  // 路由页面每次重新进入都会重新挂载，因此主动取一次最新记录；共享状态仍
  // 负责加载占位、竞态版本和失败重试，不依赖可能过期的页面缓存。
  void loadHistory()
})
</script>

<template>
  <section
    id="history-panel"
    class="card history-panel"
    role="region"
    aria-labelledby="page-title"
    :aria-busy="historyStatus === 'loading'"
  >
    <div class="history-header">
      <div>
        <h1 id="page-title" tabindex="-1">历史记录</h1>
        <p>
          仅显示最近 {{ historyLimit }} 条成功记录。
          {{ AUTH_ENABLED ? '记录只属于当前登录账号。' : '当前为免登录演示模式。' }}
        </p>
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

    <div v-else-if="historyStatus === 'error'" class="history-state">
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
        {{ AUTH_ENABLED
          ? '当前账号还没有成功记录；完成一次生成后可在这里查看结果。'
          : '免登录演示模式下暂无成功记录；完成一次生成后可在这里查看结果。' }}
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
        <div class="history-item-layout">
          <button
            class="history-thumbnail"
            type="button"
            :disabled="historyDeletingIds.has(item.generation_id)"
            :aria-label="`载回工作台：${item.title}`"
            @click="restoreHistoryItem(item)"
          >
            <img
              v-if="item.has_image_preview && item.image_preview_url"
              :src="item.image_preview_url"
              alt=""
              loading="lazy"
              referrerpolicy="no-referrer"
            />
            <span v-else class="history-thumbnail-placeholder" aria-hidden="true">
              暂无图片预览
            </span>
          </button>

          <div class="history-item-content">
            <div class="history-item-header">
              <div>
                <h2>{{ item.title }}</h2>
                <time :datetime="item.created_at">{{ formatCreatedAt(item.created_at) }}</time>
              </div>
              <div class="history-actions">
                <el-button
                  type="primary"
                  plain
                  :disabled="historyDeletingIds.has(item.generation_id)"
                  :aria-label="`载回工作台：${item.title}`"
                  @click="restoreHistoryItem(item)"
                >
                  载回工作台
                </el-button>
                <el-button
                  type="success"
                  plain
                  :icon="DocumentCopy"
                  :disabled="historyDeletingIds.has(item.generation_id)"
                  :aria-label="`复制历史文案：${item.title}`"
                  @click="copyGeneration(item)"
                >
                  复制文案
                </el-button>
                <el-button
                  type="danger"
                  plain
                  :icon="Delete"
                  :loading="historyDeletingIds.has(item.generation_id)"
                  :disabled="historyDeletingIds.has(item.generation_id)"
                  :aria-label="`删除历史记录：${item.title}`"
                  @click="confirmDelete(item)"
                >
                  删除
                </el-button>
              </div>
            </div>

            <details class="image-summary-details">
              <summary :aria-label="`图片理解摘要：${item.title}`">图片理解摘要</summary>
              <p>{{ item.image_summary }}</p>
            </details>
            <p class="history-body">{{ item.body }}</p>
            <div class="tags" aria-label="历史记录话题标签">
              <el-tag v-for="tag in item.tags" :key="tag" type="primary">
                {{ tag }}
              </el-tag>
            </div>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.card {
  box-sizing: border-box;
  width: min(100%, 1040px);
  margin: 38px auto 0;
  padding: 28px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
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

.history-header h1 {
  margin: 0 0 8px;
  color: #1f2937;
  font-size: 28px;
}

.history-header h1:focus-visible {
  outline: 3px solid rgba(177, 15, 42, 0.3);
  outline-offset: 4px;
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

.history-item-layout {
  display: grid;
  grid-template-columns: minmax(180px, 220px) minmax(0, 1fr);
  gap: 20px;
}

.history-thumbnail {
  box-sizing: border-box;
  width: 100%;
  aspect-ratio: 4 / 3;
  padding: 0;
  overflow: hidden;
  border: 1px solid #ded5d9;
  border-radius: 10px;
  color: #6b7280;
  background: #f4f1f2;
  cursor: pointer;
}

.history-thumbnail:hover:not(:disabled) {
  border-color: #b10f2a;
}

.history-thumbnail:focus-visible {
  outline: 3px solid rgba(177, 15, 42, 0.32);
  outline-offset: 3px;
}

.history-thumbnail:disabled {
  cursor: wait;
  opacity: 0.7;
}

.history-thumbnail img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.history-thumbnail-placeholder {
  display: grid;
  width: 100%;
  height: 100%;
  padding: 16px;
  place-items: center;
  background:
    linear-gradient(135deg, rgba(177, 15, 42, 0.05), transparent 55%),
    #f7f4f5;
  font-size: 13px;
}

.history-item-content {
  min-width: 0;
}

.history-item-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.history-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.history-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.history-item-header h2 {
  margin: 0 0 6px;
  color: #ff2442;
  font-size: 18px;
}

.history-item-header time {
  color: #6b7280;
  font-size: 13px;
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

.history-body {
  margin: 0 0 16px;
  color: #1f2937;
  line-height: 1.65;
  white-space: pre-wrap;
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

  .history-header,
  .history-item-header {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .history-header > .el-button,
  .history-actions,
  .history-actions > .el-button {
    width: 100%;
  }

  .history-item {
    padding: 16px;
  }

  .history-item-layout {
    grid-template-columns: 1fr;
  }

  .history-thumbnail {
    aspect-ratio: 16 / 9;
  }
}
</style>
