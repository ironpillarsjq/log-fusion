<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { FileText, RefreshCw } from 'lucide-vue-next'
import BaseModal from '@/components/common/BaseModal.vue'
import { monitorApi } from '@/api/monitor'
import type { ClientLogEntry, ClientRow } from '@/types/monitor'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{
  open: boolean
  client: ClientRow | null
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const LOG_LIMIT = 20

const logs = ref<ClientLogEntry[]>([])
const loading = ref(false)
const error = ref('')

/** 把一条日志拼成一行终端风格文本 */
function formatLogLine(entry: ClientLogEntry): string {
  if (!entry || typeof entry !== 'object') return String(entry)
  const hasKnownField =
    entry.logTime !== undefined || entry.message !== undefined || entry.level !== undefined
  if (!hasKnownField) return JSON.stringify(entry)

  const time = formatDateTime(entry.logTime ?? null)
  const level = entry.level ? String(entry.level).toLowerCase() : 'info'
  const category = entry.category ? `[${entry.category}] ` : ''
  const source = entry.source ? `source=${entry.source} ` : ''
  const message = entry.message ?? ''
  return `${time} [${level}] ${category}${source}${message}`.trimEnd()
}

const lines = computed(() => logs.value.map(formatLogLine))

async function load(): Promise<void> {
  const clientId = props.client?.clientId
  if (!clientId) return
  loading.value = true
  error.value = ''
  try {
    logs.value = await monitorApi.getLogs(clientId, LOG_LIMIT)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '日志加载失败'
    logs.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.open, props.client?.clientId] as const,
  ([open]) => {
    if (!open) return
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <BaseModal :open="open" :icon="FileText" wide @close="emit('close')">
    <template #title>{{ client ? client.serverName : '' }} 最近日志</template>

    <div class="log-toolbar">
      <span class="log-meta">
        <template v-if="client">客户端 {{ client.clientId }} · 最多 {{ LOG_LIMIT }} 条</template>
      </span>
      <button class="btn" type="button" :disabled="loading" @click="load">
        <RefreshCw :class="{ spin: loading }" />刷新
      </button>
    </div>

    <div v-if="error" class="inline-error" role="alert" style="margin-bottom: 12px">
      <span class="message">{{ error }}</span>
    </div>

    <div v-if="loading && !lines.length" class="dialog-state">日志加载中…</div>
    <div v-else-if="!lines.length" class="dialog-state">该客户端最近没有日志记录</div>
    <div v-else class="log-box">
      <span v-for="(line, index) in lines" :key="index" class="log-line">{{ line }}</span>
    </div>
  </BaseModal>
</template>

<style scoped>
.log-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.log-meta {
  color: var(--muted);
  font-size: 12px;
}
</style>
