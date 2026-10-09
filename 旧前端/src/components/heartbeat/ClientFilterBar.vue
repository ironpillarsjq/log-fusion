<script setup lang="ts">
import { ref, watch } from 'vue'
import { Search } from 'lucide-vue-next'
import { HEARTBEAT_STATUS_OPTIONS, OS_TYPE_OPTIONS, PAGE_SIZE_OPTIONS } from '@/constants/status'
import type { ClientQuery, HeartbeatStatus, OsType } from '@/types/monitor'

/**
 * 客户端列表筛选栏。
 * 输入先放在本地草稿里，点「查询」或回车才真正提交，避免每敲一个字符就请求一次。
 */
const props = defineProps<{
  modelValue: ClientQuery
  total: number
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: ClientQuery): void
  (e: 'query', value: ClientQuery): void
}>()

const ALL = 'ALL'

const keyword = ref(props.modelValue.keyword ?? '')
const heartbeatStatus = ref<string>(props.modelValue.heartbeatStatus ?? ALL)
const osType = ref<string>(props.modelValue.osType ?? ALL)
const size = ref<number>(props.modelValue.size)

// 外部（重置、分页、刷新）改变了查询条件时同步回草稿
watch(
  () => props.modelValue,
  (query) => {
    keyword.value = query.keyword ?? ''
    heartbeatStatus.value = query.heartbeatStatus ?? ALL
    osType.value = query.osType ?? ALL
    size.value = query.size
  },
  { deep: true },
)

function buildQuery(page = 1): ClientQuery {
  const trimmed = keyword.value.trim()
  return {
    page,
    size: size.value,
    keyword: trimmed ? trimmed : undefined,
    heartbeatStatus: heartbeatStatus.value === ALL ? undefined : (heartbeatStatus.value as HeartbeatStatus),
    osType: osType.value === ALL ? undefined : (osType.value as OsType),
  }
}

function submit(): void {
  const query = buildQuery(1)
  emit('update:modelValue', query)
  emit('query', query)
}

function reset(): void {
  keyword.value = ''
  heartbeatStatus.value = ALL
  osType.value = ALL
  const query = buildQuery(1)
  emit('update:modelValue', query)
  emit('query', query)
}
</script>

<template>
  <div class="table-toolbar">
    <div class="section-title">
      客户端列表
      <span class="table-meta">共 {{ total }} 个客户端</span>
    </div>
    <div class="toolbar">
      <label class="field-wrap">
        <Search />
        <input
          v-model="keyword"
          class="control"
          type="search"
          placeholder="服务器名称 / IP 地址"
          aria-label="搜索客户端"
          @keydown.enter="submit"
        />
      </label>
      <select v-model="heartbeatStatus" class="control" aria-label="状态筛选">
        <option :value="ALL">全部状态</option>
        <option v-for="item in HEARTBEAT_STATUS_OPTIONS" :key="item.value" :value="item.value">
          {{ item.label }}
        </option>
      </select>
      <select v-model="osType" class="control" aria-label="系统类型筛选">
        <option :value="ALL">全部系统</option>
        <option v-for="item in OS_TYPE_OPTIONS" :key="item.value" :value="item.value">
          {{ item.label }}
        </option>
      </select>
      <select v-model.number="size" class="control" aria-label="每页条数">
        <option v-for="option in PAGE_SIZE_OPTIONS" :key="option" :value="option">
          每页 {{ option }} 条
        </option>
      </select>
      <button class="btn primary" type="button" :disabled="loading" @click="submit">查询</button>
      <button class="btn" type="button" :disabled="loading" @click="reset">重置</button>
    </div>
  </div>
</template>
