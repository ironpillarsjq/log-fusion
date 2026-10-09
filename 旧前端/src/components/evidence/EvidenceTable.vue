<script setup lang="ts">
import { computed } from 'vue'
import { Copy } from 'lucide-vue-next'
import EvidenceStateTag from './EvidenceStateTag.vue'
import { EVIDENCE_COLUMNS, truncateHash } from '@/constants/evidence'
import type { EvidenceListItem } from '@/types/evidence'
import { useToastStore } from '@/stores/toast'
import { formatDateTime, formatNumber } from '@/utils/format'

const props = defineProps<{
  rows: EvidenceListItem[]
  total: number
  page: number
  size: number
  loading?: boolean
  verifyingIds?: string[]
}>()

const emit = defineEmits<{
  (e: 'detail', row: EvidenceListItem): void
  (e: 'verify', evidenceId: string): void
  (e: 'update:page', page: number): void
  (e: 'update:size', size: number): void
}>()

const PAGE_SIZE_OPTIONS = [20, 50, 100]
const toast = useToastStore()

const rangeText = computed(() => {
  if (!props.total) return '共 0 条'
  const start = (props.page - 1) * props.size + 1
  const end = Math.min(props.page * props.size, props.total)
  return `显示第 ${start}-${end} 条，共 ${formatNumber(props.total)} 条`
})

function indexMethod(index: number): number {
  return (props.page - 1) * props.size + index + 1
}

function isVerifying(evidenceId: string): boolean {
  return props.verifyingIds?.includes(evidenceId) ?? false
}

/** 表格里只显示首尾；复制的是完整 64 位值 */
async function copyHash(hash: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(hash)
    toast.success('已复制完整 SHA-256')
  } catch {
    toast.error('浏览器不允许访问剪贴板，请手动选中复制')
  }
}
</script>

<template>
  <div class="evidence-table-wrap">
    <el-table
      v-loading="loading"
      :data="rows"
      row-key="evidenceId"
      height="505"
      border
      size="small"
    >
      <el-table-column type="index" label="no.（序号）" width="80" fixed :index="indexMethod" />

      <el-table-column
        v-for="column in EVIDENCE_COLUMNS"
        :key="column.key"
        :prop="column.key"
        :label="column.label"
        :width="column.width"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <span v-if="column.key === 'receivedAt' || column.key === 'lastVerifiedAt'" class="mono">
            {{ formatDateTime(row[column.key]) }}
          </span>

          <span v-else-if="column.key === 'rawHash'" class="evidence-hash-cell">
            <span class="evidence-hash" :title="row.rawHash">{{ truncateHash(row.rawHash) }}</span>
            <button
              class="evidence-copy"
              type="button"
              title="复制完整哈希"
              aria-label="复制完整哈希"
              @click.stop="copyHash(row.rawHash)"
            >
              <Copy />
            </button>
          </span>

          <EvidenceStateTag v-else-if="column.key === 'verifyStatus'" :status="row.verifyStatus" />

          <span v-else-if="column.key === 'categoryLabel'" class="evidence-tag">
            {{ row.categoryLabel || '未识别' }}
          </span>

          <span v-else-if="column.key === 'type'" class="type-code">{{ row.type || '-' }}</span>

          <span v-else-if="column.key === 'evidenceId'" class="mono">{{ row.evidenceId }}</span>

          <span v-else :title="String(row[column.key] ?? '-')">{{ row[column.key] ?? '-' }}</span>
        </template>
      </el-table-column>

      <el-table-column label="operation（操作）" width="132" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="emit('detail', row)">详情</el-button>
          <el-button
            link
            type="primary"
            :loading="isVerifying(row.evidenceId)"
            @click="emit('verify', row.evidenceId)"
          >
            校验
          </el-button>
        </template>
      </el-table-column>

      <template #empty>
        <div class="evidence-empty">
          <span class="empty-title">没有找到符合条件的存证记录</span>
          <span class="empty-hint">请调整主机、类别、状态或时间范围后重试</span>
        </div>
      </template>
    </el-table>
  </div>

  <div class="table-footer">
    <span class="range-text">{{ rangeText }}</span>
    <el-pagination
      background
      layout="sizes, prev, pager, next, jumper"
      :total="total"
      :current-page="page"
      :page-size="size"
      :page-sizes="PAGE_SIZE_OPTIONS"
      @current-change="emit('update:page', $event)"
      @size-change="emit('update:size', $event)"
    />
  </div>
</template>
