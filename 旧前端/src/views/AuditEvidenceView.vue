<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Download, ShieldCheck } from 'lucide-vue-next'
import PageHeader from '@/components/common/PageHeader.vue'
import InlineError from '@/components/common/InlineError.vue'
import EvidenceSummary from '@/components/evidence/EvidenceSummary.vue'
import EvidencePolicyBanner from '@/components/evidence/EvidencePolicyBanner.vue'
import EvidenceFilter from '@/components/evidence/EvidenceFilter.vue'
import EvidenceTable from '@/components/evidence/EvidenceTable.vue'
import EvidenceDetailDialog from '@/components/evidence/EvidenceDetailDialog.vue'
import { evidenceExportUrl, isValidRawHash } from '@/api/evidence'
import { useEvidenceStore } from '@/stores/evidence'
import { useToastStore } from '@/stores/toast'
import type { EvidenceListItem, EvidenceQuery } from '@/types/evidence'
import { formatDateTime, formatNumber } from '@/utils/format'

/**
 * 原始日志存证与完整性校验页面（指南第 10 节）。
 * 页面只负责组合组件：数据在 Pinia store，接口路径在 api 目录。
 */
const store = useEvidenceStore()
const toast = useToastStore()

const detailOpen = ref(false)
const detailId = ref('')

onMounted(() => {
  void store.initialize()
  void store.startSummaryPolling()
})

onUnmounted(() => {
  store.stopSummaryPolling()
})

function openDetail(row: EvidenceListItem): void {
  detailId.value = row.evidenceId
  detailOpen.value = true
}

async function onQuery(next: EvidenceQuery): Promise<void> {
  await store.applyQuery(next)
}

/** 单条校验：失败只提示，不动列表 */
async function onVerify(evidenceId: string): Promise<void> {
  const result = await store.verifyOne(evidenceId)
  if (!result) return
  if (result.verifyStatus === 'VALID') toast.success(`${evidenceId} 校验通过`)
  else toast.error(`${evidenceId} 校验异常：${result.verifyStatus}`)
}

/** 当前页同步校验（最多 100 条），完成后刷新列表与统计 */
async function onVerifyPage(): Promise<void> {
  const result = await store.verifyCurrentPage()
  if (!result) return
  const abnormal = result.hashMismatch + result.rawMissing
  if (abnormal > 0) {
    toast.error(`当前页已校验 ${result.checked} 条，发现 ${abnormal} 条异常`)
  } else {
    toast.success(`当前页已校验 ${result.checked} 条，全部正常`)
  }
}

/**
 * 导出当前查询条件下的存证记录。
 * 由后端流式写出 CSV，浏览器不加载全部数据（指南 10.10）。
 */
function exportCurrent(): void {
  const hash = store.query.rawHash?.trim()
  if (hash && !isValidRawHash(hash)) {
    toast.error('SHA-256 必须是 64 位十六进制字符')
    return
  }
  if (!store.total) {
    toast.info('当前没有可导出的存证记录')
    return
  }
  window.location.href = evidenceExportUrl(store.query)
}

function retry(): void {
  void store.loadPage()
  void store.loadSummary()
}
</script>

<template>
  <div class="evidence-page">
    <PageHeader
      title="原始日志存证与完整性校验"
      subtitle="保存汇聚端接收的原始日志，并使用 SHA-256 验证内容是否发生变化"
      parent="日志完整性存证"
      current="单条存证校验"
    >
      <button class="btn" type="button" :disabled="store.verifyingPage" @click="onVerifyPage">
        <ShieldCheck />{{ store.verifyingPage ? '校验中…' : '重新校验当前页' }}
      </button>
      <button class="btn primary" type="button" @click="exportCurrent">
        <Download />导出存证记录
      </button>
    </PageHeader>

    <EvidenceSummary :summary="store.summary" />

    <InlineError
      v-if="store.error"
      :message="store.degraded ? `已连续 ${store.failureCount} 次查询失败：${store.error}` : `查询失败：${store.error}`"
      :hint="store.lastLoadedAt ? `页面保留的是上一次成功查询的数据（${formatDateTime(store.lastLoadedAt)}）` : ''"
      :retrying="store.loading"
      @retry="retry"
    />

    <section class="panel evidence-panel">
      <EvidencePolicyBanner algorithm="SHA-256" version="EVIDENCE-V1" />

      <EvidenceFilter
        :model-value="store.query"
        :loading="store.loading"
        @query="onQuery"
      />

      <div class="evidence-table-head">
        <div class="section-title">
          单条存证记录
          <span class="table-meta">共 {{ formatNumber(store.total) }} 条</span>
        </div>
        <div class="evidence-tools">
          <span>原始日志保存后自动计算哈希，列表不自动刷新</span>
          <span>
            数据更新时间：<b>{{ store.lastLoadedAt ? formatDateTime(store.lastLoadedAt) : '--:--:--' }}</b>
          </span>
        </div>
      </div>

      <EvidenceTable
        :rows="store.items"
        :total="store.total"
        :page="store.query.page"
        :size="store.query.size"
        :loading="store.loading"
        :verifying-ids="store.verifyingIds"
        @detail="openDetail"
        @verify="onVerify"
        @update:page="store.setPage($event)"
        @update:size="store.setSize($event)"
      />
    </section>

    <EvidenceDetailDialog :open="detailOpen" :evidence-id="detailId" @close="detailOpen = false" />
  </div>
</template>
