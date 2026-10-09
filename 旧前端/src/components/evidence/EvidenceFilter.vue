<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { Info, Search } from 'lucide-vue-next'
import { CATEGORY_OPTIONS, VERIFY_STATUS_OPTIONS } from '@/constants/evidence'
import type { EvidenceCategory, EvidenceQuery, VerifyStatus } from '@/types/evidence'
import { isValidRawHash } from '@/api/evidence'

/**
 * 存证筛选：主机/IP、类别、状态、开始时间、完整哈希。
 * 输入只在点「查询」或回车时提交（指南 10.8：不要每次键盘输入都请求后端）。
 */
const props = defineProps<{ modelValue: EvidenceQuery; loading?: boolean }>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: EvidenceQuery): void
  (e: 'query', value: EvidenceQuery): void
}>()

const host = ref(props.modelValue.host ?? '')
const category = ref<string>(props.modelValue.category ?? '')
const status = ref<string>(props.modelValue.status ?? '')
const startTime = ref<Date | null>(toDate(props.modelValue.startTime))
const rawHash = ref(props.modelValue.rawHash ?? '')

function toDate(value?: string): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

watch(
  () => props.modelValue,
  (query) => {
    host.value = query.host ?? ''
    category.value = query.category ?? ''
    status.value = query.status ?? ''
    startTime.value = toDate(query.startTime)
    rawHash.value = query.rawHash ?? ''
  },
  { deep: true },
)

const hashError = computed(() => {
  const value = rawHash.value.trim()
  if (!value) return ''
  return isValidRawHash(value) ? '' : 'SHA-256 必须是 64 位十六进制字符'
})

function build(): EvidenceQuery {
  const hash = rawHash.value.trim()
  return {
    page: 1,
    size: props.modelValue.size,
    host: host.value.trim() || undefined,
    category: (category.value || undefined) as EvidenceCategory | undefined,
    status: (status.value || undefined) as VerifyStatus | undefined,
    startTime: startTime.value ? dayjs(startTime.value).toISOString() : undefined,
    rawHash: hash || undefined,
  }
}

function submit(): void {
  if (hashError.value) return
  const query = build()
  emit('update:modelValue', query)
  emit('query', query)
}

function reset(): void {
  host.value = ''
  category.value = ''
  status.value = ''
  startTime.value = null
  rawHash.value = ''
  submit()
}
</script>

<template>
  <div class="evidence-filters">
    <div class="evidence-filter-grid">
      <div class="field">
        <label for="evidence-host">主机名称 / 来源 IP</label>
        <el-input
          id="evidence-host"
          v-model="host"
          placeholder="例如 audit-prod-01"
          clearable
          :prefix-icon="Search"
          @keydown.enter="submit"
        />
      </div>
      <div class="field">
        <label for="evidence-category">日志类别</label>
        <el-select id="evidence-category" v-model="category" placeholder="全部类别" clearable>
          <el-option label="全部类别" value="" />
          <el-option
            v-for="item in CATEGORY_OPTIONS"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </div>
      <div class="field">
        <label for="evidence-status">校验状态</label>
        <el-select id="evidence-status" v-model="status" placeholder="全部状态" clearable>
          <el-option label="全部状态" value="" />
          <el-option
            v-for="item in VERIFY_STATUS_OPTIONS"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </div>
      <div class="field">
        <label for="evidence-start">开始时间</label>
        <el-date-picker
          id="evidence-start"
          v-model="startTime"
          type="datetime"
          placeholder="默认最近 24 小时"
          :teleported="true"
        />
      </div>
      <div class="field">
        <label for="evidence-hash">SHA-256 精确检索</label>
        <el-input
          id="evidence-hash"
          v-model="rawHash"
          placeholder="输入完整 64 位哈希值"
          clearable
          @keydown.enter="submit"
        />
      </div>
      <div class="filter-actions">
        <el-button type="primary" :loading="loading" @click="submit">查询</el-button>
        <el-button :disabled="loading" @click="reset">重置</el-button>
      </div>
    </div>
    <div class="evidence-hint" :class="{ error: hashError }">
      <Info />
      <span v-if="hashError">{{ hashError }}</span>
      <span v-else>哈希值只支持精确匹配；时间筛选使用汇聚端接收时间，不填则默认最近 24 小时。</span>
    </div>
  </div>
</template>
