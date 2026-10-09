<script setup lang="ts">
import { computed } from 'vue'
import type { LogField } from '@/types/auditLogs'
import { resultMetaOf } from '@/constants/auditLogs'
import { formatDateTime } from '@/utils/format'

/**
 * 动态单元格：总览与七个分类页共用。
 * 特殊列按字段名渲染（事件时间、类别标签、类型码、结果徽标），其余按纯文本，
 * 空值统一显示 '-'。
 */
const props = defineProps<{
  field: LogField
  row: Record<string, unknown>
}>()

const value = computed(() => props.row[props.field.prop ?? props.field.key])

const isDateTime = computed(
  () => props.field.key === 'event_time' || props.field.type === 'DATETIME',
)
const isCategory = computed(() => props.field.key === 'category')
const isType = computed(() => props.field.key === 'type')
const isResult = computed(() => props.field.key === 'result')

const text = computed(() => {
  const raw = value.value
  if (raw === null || raw === undefined || raw === '') return '-'
  if (isDateTime.value) return formatDateTime(String(raw))
  return String(raw)
})

const categoryText = computed(() => {
  const label = props.row.categoryLabel
  if (typeof label === 'string' && label) return label
  return text.value
})

const result = computed(() => resultMetaOf(value.value))
</script>

<template>
  <span v-if="isResult" class="result" :class="result.className">
    <span class="dot" :class="result.className === 'success' ? 'green' : 'red'" />
    {{ result.text }}
  </span>
  <span v-else-if="isCategory" class="category-tag">{{ categoryText }}</span>
  <span v-else-if="isType" class="type-code">{{ text }}</span>
  <span v-else-if="isDateTime" class="mono">{{ text }}</span>
  <span v-else :title="text">{{ text }}</span>
</template>
