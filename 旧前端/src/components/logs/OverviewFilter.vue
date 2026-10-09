<script setup lang="ts">
import { ref, watch } from 'vue'
import dayjs from 'dayjs'
import { Info, Search } from 'lucide-vue-next'
import type { OverviewQuery } from '@/types/auditLogs'

/**
 * 总览筛选：主机名称/IP、系统类型、时间范围。
 * 输入只在点「查询」或回车时提交（指南 8.6：不要在每次键盘输入时请求百万级表）。
 */
const props = defineProps<{
  modelValue: OverviewQuery
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: OverviewQuery): void
  (e: 'query', value: OverviewQuery): void
}>()

const host = ref(props.modelValue.host ?? '')
const osType = ref<string>(props.modelValue.osType ?? '')
const range = ref<[Date, Date] | null>(toRange(props.modelValue))

function toRange(query: OverviewQuery): [Date, Date] | null {
  if (!query.startTime || !query.endTime) return null
  const start = new Date(query.startTime)
  const end = new Date(query.endTime)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null
  return [start, end]
}

// 外部（重置、切换类别）改变了查询条件时同步回本地草稿
watch(
  () => props.modelValue,
  (query) => {
    host.value = query.host ?? ''
    osType.value = query.osType ?? ''
    range.value = toRange(query)
  },
  { deep: true },
)

function build(): OverviewQuery {
  return {
    page: 1,
    size: props.modelValue.size,
    host: host.value.trim() || undefined,
    osType: (osType.value || undefined) as OverviewQuery['osType'],
    startTime: range.value ? dayjs(range.value[0]).toISOString() : undefined,
    endTime: range.value ? dayjs(range.value[1]).toISOString() : undefined,
  }
}

function submit(): void {
  const query = build()
  emit('update:modelValue', query)
  emit('query', query)
}

function reset(): void {
  host.value = ''
  osType.value = ''
  range.value = null
  submit()
}
</script>

<template>
  <div class="filter-zone">
    <div class="filter-primary">
      <div class="field">
        <label for="overview-host">主机名称 / IP 地址</label>
        <el-input
          id="overview-host"
          v-model="host"
          placeholder="例如 audit-prod-01"
          clearable
          :prefix-icon="Search"
          @keydown.enter="submit"
        />
      </div>
      <div class="field">
        <label for="overview-os">系统类型</label>
        <el-select id="overview-os" v-model="osType" placeholder="全部系统" clearable>
          <el-option label="全部系统" value="" />
          <el-option label="Linux" value="Linux" />
          <el-option label="Windows" value="Windows" />
        </el-select>
      </div>
      <div class="field field-range">
        <label for="overview-range">时间范围</label>
        <el-date-picker
          id="overview-range"
          v-model="range"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          :default-time="[new Date(2000, 0, 1, 0, 0, 0), new Date(2000, 0, 1, 23, 59, 59)]"
          unlink-panels
        />
      </div>
      <div class="filter-actions">
        <el-button type="primary" :loading="loading" @click="submit">查询</el-button>
        <el-button :disabled="loading" @click="reset">重置</el-button>
      </div>
    </div>
    <div class="filter-hint">
      <Info />
      <span>总览支持主机、系统和时间范围组合筛选；不填时间范围时后端按最近 24 小时查询，实时新日志自动出现在列表顶部。</span>
    </div>
  </div>
</template>
