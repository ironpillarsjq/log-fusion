<script setup lang="ts">
import { computed } from 'vue'
import { SearchX } from 'lucide-vue-next'
import StatusTag from '@/components/common/StatusTag.vue'
import { PAGE_SIZE_OPTIONS } from '@/constants/status'
import type { ClientRow } from '@/types/monitor'
import { formatDateTime, formatDelaySeconds, formatHeartbeatAge, formatNumber } from '@/utils/format'

const props = defineProps<{
  rows: ClientRow[]
  total: number
  page: number
  size: number
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'detail', row: ClientRow): void
  (e: 'logs', row: ClientRow): void
  (e: 'update:page', page: number): void
  (e: 'update:size', size: number): void
}>()

/** 首屏加载且还没有任何数据时显示骨架行 */
const showSkeleton = computed(() => props.loading === true && props.rows.length === 0)
const showEmpty = computed(() => props.loading !== true && props.rows.length === 0)
const skeletonRows = [0, 1, 2, 3, 4]

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.size))))

/** 页码窗口，避免几十页时把按钮铺满整行 */
const pageItems = computed<Array<number | 'gap'>>(() => {
  const count = pageCount.value
  const current = props.page
  if (count <= 7) return Array.from({ length: count }, (_, index) => index + 1)

  const items: Array<number | 'gap'> = [1]
  const start = Math.max(2, current - 2)
  const end = Math.min(count - 1, current + 2)
  if (start > 2) items.push('gap')
  for (let pageNo = start; pageNo <= end; pageNo += 1) items.push(pageNo)
  if (end < count - 1) items.push('gap')
  items.push(count)
  return items
})

function rowNumber(index: number): number {
  return (props.page - 1) * props.size + index + 1
}

function go(page: number): void {
  if (page < 1 || page > pageCount.value || page === props.page) return
  emit('update:page', page)
}

function changeSize(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value)) emit('update:size', value)
}
</script>

<template>
  <div class="table-scroll">
    <table v-if="!showEmpty">
      <thead>
        <tr>
          <th>序号</th>
          <th>IP 地址</th>
          <th>服务器名称</th>
          <th>系统类型</th>
          <th>系统版本</th>
          <th>Fluent Bit</th>
          <th>心跳周期</th>
          <th>最近心跳</th>
          <th>心跳延迟</th>
          <th>最后日志时间</th>
          <th>近 5 分钟日志量</th>
          <th>心跳状态</th>
          <th>日志状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody v-if="showSkeleton">
        <tr v-for="index in skeletonRows" :key="index">
          <td v-for="column in 14" :key="column"><span class="skeleton" /></td>
        </tr>
      </tbody>
      <tbody v-else>
        <tr v-for="(row, index) in props.rows" :key="row.clientId">
          <td>{{ rowNumber(index) }}</td>
          <td :title="row.ipAddress">{{ row.ipAddress }}</td>
          <td :title="row.clientId">{{ row.serverName }}</td>
          <td>{{ row.osType }}</td>
          <td :title="row.osVersion">{{ row.osVersion }}</td>
          <td>{{ row.fluentBitVersion || '-' }}</td>
          <td>{{ row.heartbeatIntervalSec }} 秒</td>
          <td>{{ formatHeartbeatAge(row.heartbeatDelaySec) }}</td>
          <td>{{ formatDelaySeconds(row.heartbeatDelaySec) }}</td>
          <td>{{ formatDateTime(row.lastLogAt) }}</td>
          <td>{{ formatNumber(row.logsLast5m) }} 条</td>
          <td><StatusTag kind="heartbeat" :status="row.heartbeatStatus" /></td>
          <td><StatusTag kind="log" :status="row.logStatus" /></td>
          <td>
            <button class="link-btn" type="button" @click="emit('detail', row)">详情</button>
            <button class="link-btn" type="button" @click="emit('logs', row)">日志</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-else class="empty">
      <SearchX />
      <div class="empty-title">没有找到符合条件的客户端</div>
      <div class="empty-hint">可以调整关键字、状态或系统类型后重新查询</div>
    </div>
  </div>

  <div class="table-footer">
    <div class="page-size">
      <span>每页</span>
      <select class="control" :value="props.size" aria-label="每页条数" @change="changeSize">
        <option v-for="option in PAGE_SIZE_OPTIONS" :key="option" :value="option">{{ option }}</option>
      </select>
      <span>条</span>
    </div>
    <div class="pager">
      <span>共 {{ total }} 条</span>
      <button class="page-btn" type="button" :disabled="props.page <= 1" @click="go(props.page - 1)">‹</button>
      <template v-for="(item, index) in pageItems" :key="`${item}-${index}`">
        <button
          v-if="item !== 'gap'"
          class="page-btn"
          :class="{ active: item === props.page }"
          type="button"
          @click="go(item)"
        >
          {{ item }}
        </button>
        <button v-else class="page-btn ellipsis" type="button" disabled>…</button>
      </template>
      <button
        class="page-btn"
        type="button"
        :disabled="props.page >= pageCount"
        @click="go(props.page + 1)"
      >
        ›
      </button>
    </div>
  </div>
</template>
