<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { Info, Search } from 'lucide-vue-next'
import type { CategoryQuery, LogField } from '@/types/auditLogs'

/**
 * 分类字段筛选：字段下拉只显示后端标记 searchable=true 的字段。
 * 值控件按字段类型切换：数字用数字输入框，时间用日期选择器，其余是文本（包含匹配）。
 */
const props = defineProps<{
  modelValue: CategoryQuery
  /** 已经过滤好的可检索字段 */
  fields: LogField[]
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: CategoryQuery): void
  (e: 'query', value: CategoryQuery): void
}>()

const fieldKey = ref(props.modelValue.field ?? '')
const textValue = ref('')
const numberValue = ref<number | null>(null)
const dateValue = ref<Date | null>(null)

const selectedField = computed(() => props.fields.find((item) => item.key === fieldKey.value) ?? null)
const isNumeric = computed(
  () => selectedField.value?.type === 'LONG' || selectedField.value?.type === 'INTEGER',
)
const isDate = computed(() => selectedField.value?.type === 'DATETIME')

const operatorHint = computed(() => {
  if (!selectedField.value) return '请选择检索字段'
  if (isNumeric.value) return '数字字段按相等匹配'
  if (isDate.value) return '时间字段按相等匹配'
  return '字符串字段按包含匹配（效果等同 LIKE %值%）'
})

function clearValue(): void {
  textValue.value = ''
  numberValue.value = null
  dateValue.value = null
}

// 切换字段时清空输入，避免把字符串值套到数字/时间字段上
watch(fieldKey, () => clearValue())

/** 外部改动（重置）时同步回草稿 */
watch(
  () => props.modelValue,
  (query) => {
    fieldKey.value = query.field ?? ''
    clearValue()
    if (query.value === undefined || query.value === null || query.value === '') return
    const type = props.fields.find((item) => item.key === query.field)?.type
    if (type === 'LONG' || type === 'INTEGER') numberValue.value = Number(query.value)
    else if (type === 'DATETIME') dateValue.value = new Date(query.value)
    else textValue.value = String(query.value)
  },
  { deep: true },
)

function currentValue(): string | undefined {
  if (isNumeric.value) return numberValue.value === null ? undefined : String(numberValue.value)
  if (isDate.value) return dateValue.value ? dayjs(dateValue.value).toISOString() : undefined
  return textValue.value.trim() || undefined
}

function build(): CategoryQuery {
  const value = currentValue()
  const operator = !value ? undefined : isNumeric.value || isDate.value ? 'EQ' : 'CONTAINS'
  return {
    page: 1,
    size: props.modelValue.size,
    field: fieldKey.value || undefined,
    operator,
    value,
    // 时间范围由后端默认（最近 24 小时），分类页不额外暴露时间控件
    startTime: props.modelValue.startTime,
    endTime: props.modelValue.endTime,
  }
}

function submit(): void {
  const query = build()
  emit('update:modelValue', query)
  emit('query', query)
}

function reset(): void {
  fieldKey.value = ''
  clearValue()
  submit()
}
</script>

<template>
  <div class="filter-zone">
    <div class="filter-category">
      <div class="field">
        <label for="category-field">检索字段</label>
        <el-select id="category-field" v-model="fieldKey" placeholder="选择检索字段" clearable filterable>
          <el-option
            v-for="field in fields"
            :key="field.key"
            :label="`${field.key}（${field.label}）`"
            :value="field.key"
          />
        </el-select>
      </div>
      <div class="field">
        <label for="category-value">字段值</label>
        <el-input-number
          v-if="isNumeric"
          id="category-value"
          v-model="numberValue"
          :controls="false"
          placeholder="输入数字"
          class="value-control"
          @keydown.enter="submit"
        />
        <el-date-picker
          v-else-if="isDate"
          id="category-value"
          v-model="dateValue"
          type="datetime"
          placeholder="选择时间"
          class="value-control"
        />
        <el-input
          v-else
          id="category-value"
          v-model="textValue"
          placeholder="输入字段值，支持包含匹配"
          clearable
          :prefix-icon="Search"
          @keydown.enter="submit"
        />
      </div>
      <div class="filter-actions">
        <el-button type="primary" :loading="loading" @click="submit">按字段查询</el-button>
        <el-button :disabled="loading" @click="reset">重置</el-button>
      </div>
    </div>
    <div class="filter-hint">
      <Info />
      <span>{{ operatorHint }}；可检索字段与标签由后端类别元数据接口提供。</span>
    </div>
  </div>
</template>

<style scoped>
.value-control {
  width: 100%;
}
</style>
