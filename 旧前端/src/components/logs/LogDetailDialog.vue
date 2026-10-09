<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { FileJson } from 'lucide-vue-next'
import BaseModal from '@/components/common/BaseModal.vue'
import LogCell from './LogCell.vue'
import { auditLogApi } from '@/api/auditLogs'
import { isRequestCanceled } from '@/api/http'
import { TECHNICAL_FIELD_LABELS } from '@/constants/auditLogs'
import type { LogDetail, LogField } from '@/types/auditLogs'

/**
 * 详情弹窗：按「类别 + 分类表行 ID」调用详情接口取完整字段，
 * 不依赖列表里可能被截断的内容。
 */
const props = defineProps<{
  open: boolean
  category: string
  categoryLabel?: string
  recordId: number | null
  /** 该类别已加载的字段，作为详情接口未返回字段时的兜底 */
  fields: LogField[]
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const detail = ref<LogDetail | null>(null)
const loading = ref(false)
const error = ref('')

watch(
  () => [props.open, props.category, props.recordId] as const,
  async ([open, category, recordId]) => {
    if (!open || !category || recordId === null) return
    detail.value = null
    error.value = ''
    loading.value = true
    try {
      detail.value = await auditLogApi.getDetail(category, recordId, props.fields)
    } catch (e) {
      if (!isRequestCanceled(e)) error.value = e instanceof Error ? e.message : '详情加载失败'
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

const record = computed<Record<string, unknown>>(() => detail.value?.record ?? {})

/** 行 ID + 元数据字段 + 记录里出现的技术字段，全部展示，一个不漏 */
const displayFields = computed<LogField[]>(() => {
  const result: LogField[] = []
  const seen = new Set<string>()
  const push = (field: LogField) => {
    if (!field.key || seen.has(field.key)) return
    seen.add(field.key)
    result.push(field)
  }

  push({ key: 'id', label: '行 ID', type: 'LONG', searchable: false })
  const base = detail.value?.fields?.length ? detail.value.fields : props.fields
  base.forEach(push)
  Object.keys(record.value).forEach((key) => {
    push({ key, label: TECHNICAL_FIELD_LABELS[key] ?? key, type: 'STRING', searchable: false })
  })
  return result
})

const title = computed(() => `${props.categoryLabel || props.category} 日志详情`)
</script>

<template>
  <BaseModal :open="open" :icon="FileJson" wide @close="emit('close')">
    <template #title>{{ title }}</template>

    <div v-if="loading && !detail" class="dialog-state">详情加载中…</div>
    <template v-else>
      <div v-if="error" class="inline-error" role="alert" style="margin-bottom: 12px">
        <span class="message">{{ error }}<span class="hint">记录可能已按保留策略清理</span></span>
      </div>
      <div v-if="displayFields.length" class="detail-grid">
        <template v-for="field in displayFields" :key="field.key">
          <div class="label">{{ field.key }}（{{ field.label }}）</div>
          <div :class="{ wide: field.key === 'tips' }">
            <LogCell :field="field" :row="record" />
          </div>
        </template>
      </div>
      <div v-else class="dialog-state">没有取到该条日志的字段</div>
    </template>
  </BaseModal>
</template>
