<script setup lang="ts">
import { computed } from 'vue'
import LogCell from './LogCell.vue'
import { PAGE_SIZE_OPTIONS } from '@/constants/auditLogs'
import type { DynamicLogRecord, LogField } from '@/types/auditLogs'
import { formatNumber } from '@/utils/format'

/**
 * 动态列日志表：列完全由字段元数据驱动，
 * 总览用固定 8 个字段，分类页用后端返回的字段，七类共用这一个组件。
 * 表头按指南要求显示 `event_time（事件时间）` 形式。
 */
const props = defineProps<{
  fields: LogField[]
  records: DynamicLogRecord[]
  total: number
  page: number
  size: number
  loading?: boolean
  /** 实时新增行的 id，用于短暂高亮 */
  newIds?: number[]
}>()

const emit = defineEmits<{
  (e: 'detail', row: DynamicLogRecord): void
  (e: 'update:page', page: number): void
  (e: 'update:size', size: number): void
}>()

const rangeText = computed(() => {
  if (!props.total) return '共 0 条'
  const start = (props.page - 1) * props.size + 1
  const end = Math.min(props.page * props.size, props.total)
  return `显示第 ${start}-${end} 条，共 ${formatNumber(props.total)} 条`
})

const WIDE_FIELDS = ['summary', 'tips', 'executable', 'object_path', 'target', 'command']
const ADDRESS_FIELDS = ['source_address', 'destination_address']

/**
 * 列宽按 `key（label）` 表头的实际长度给：
 * 表头不折行，正文超长由 show-overflow-tooltip 兜底；
 * 总览这 10 列在 1680px 窗口下刚好铺满，不出现横向滚动。
 */
function columnWidth(field: LogField): number {
  if (field.key === 'event_time') return 165
  if (WIDE_FIELDS.includes(field.key)) return 225
  if (ADDRESS_FIELDS.includes(field.key)) return 195
  if (field.key === 'category') return 165
  if (field.key === 'source_ip') return 160
  if (field.key === 'hostname') return 145
  if (field.key === 'type') return 140
  if (field.key === 'result') return 120
  if (field.key === 'os_type') return 115
  return 155
}

/** 表头按指南要求显示 `event_time（事件时间）` 形式 */
function headerLabel(field: LogField): string {
  return `${field.key}（${field.label}）`
}

/** 序号按当前页全局递增 */
function indexMethod(index: number): number {
  return (props.page - 1) * props.size + index + 1
}

/** 实时新增的行短暂高亮 */
function rowClassName({ row }: { row: DynamicLogRecord }): string {
  return props.newIds?.includes(Number(row.id)) ? 'row-new' : ''
}
</script>

<template>
  <div class="table-scroll">
    <el-table
      v-loading="loading"
      :data="records"
      row-key="id"
      height="510"
      border
      size="small"
      :row-class-name="rowClassName"
    >
      <el-table-column type="index" label="no.（序号）" width="90" fixed :index="indexMethod" />

      <el-table-column
        v-for="field in fields"
        :key="field.key"
        :prop="field.prop ?? field.key"
        :label="headerLabel(field)"
        :min-width="columnWidth(field)"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <LogCell :field="field" :row="row" />
        </template>
      </el-table-column>

      <el-table-column label="view（查看）" width="110" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="emit('detail', row)">详情</el-button>
        </template>
      </el-table-column>

      <template #empty>
        <div class="table-empty">
          <span class="empty-title">没有找到符合条件的融合日志</span>
          <span class="empty-hint">请调整检索字段或时间范围后重试</span>
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
