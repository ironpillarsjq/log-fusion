<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Fingerprint } from 'lucide-vue-next'
import BaseModal from '@/components/common/BaseModal.vue'
import EvidenceStateTag from './EvidenceStateTag.vue'
import { evidenceApi } from '@/api/evidence'
import { isRequestCanceled } from '@/api/http'
import { categoryLabelOf, truncateHash } from '@/constants/evidence'
import { useEvidenceStore } from '@/stores/evidence'
import { useToastStore } from '@/stores/toast'
import type { EvidenceDetail } from '@/types/evidence'
import { formatDateTime } from '@/utils/format'

/**
 * 存证详情（指南 10.9）：存证信息 / 哈希证明 / 原始日志正文 / 融合日志关联 / 三步校验结果。
 * 原始正文由后端按 path + offset + length 现读，前端不缓存。
 */
const props = defineProps<{ open: boolean; evidenceId: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const store = useEvidenceStore()
const toast = useToastStore()

const detail = ref<EvidenceDetail | null>(null)
const loading = ref(false)
const error = ref('')
const verifying = ref(false)

async function loadDetail(): Promise<void> {
  if (!props.evidenceId) return
  loading.value = true
  error.value = ''
  try {
    detail.value = await evidenceApi.detail(props.evidenceId)
  } catch (e) {
    if (!isRequestCanceled(e)) {
      error.value = e instanceof Error ? e.message : '详情加载失败'
      // 详情读取失败时清空正文，但保留（如果有）元数据展示能力
      detail.value = null
    }
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.open, props.evidenceId] as const,
  ([open]) => {
    if (!open) return
    detail.value = null
    void loadDetail()
  },
  { immediate: true },
)

const status = computed(() => detail.value?.verifyStatus ?? 'PENDING')
const isMissing = computed(() => status.value === 'RAW_MISSING')
const isMismatch = computed(() => status.value === 'HASH_MISMATCH')
const isPending = computed(() => status.value === 'PENDING')

const hashText = computed(() => detail.value?.rawHash ?? '')
const hashHint = computed(() =>
  hashText.value ? `${truncateHash(hashText.value)}（完整值见下方）` : '-',
)

async function verify(): Promise<void> {
  if (!props.evidenceId || verifying.value) return
  verifying.value = true
  try {
    const result = await store.verifyOne(props.evidenceId)
    if (result) {
      await loadDetail()
      if (result.verifyStatus === 'VALID') toast.success(`${props.evidenceId} 校验通过`)
      else toast.error(`${props.evidenceId} 校验异常：${result.verifyStatus}`)
    }
  } finally {
    verifying.value = false
  }
}

async function copyHash(): Promise<void> {
  if (!hashText.value) return
  try {
    await navigator.clipboard.writeText(hashText.value)
    toast.success('已复制完整 SHA-256')
  } catch {
    toast.error('浏览器不允许访问剪贴板，请手动选中复制')
  }
}
</script>

<template>
  <BaseModal :open="open" :icon="Fingerprint" wide @close="emit('close')">
    <template #title>{{ evidenceId }} 存证详情</template>

    <div v-if="loading && !detail" class="dialog-state">存证详情加载中…</div>
    <template v-else-if="detail">
      <section class="evidence-detail">
        <h3>存证信息</h3>
        <div class="evidence-grid">
          <div class="label">evidence_id（存证 ID）</div>
          <div class="mono">{{ detail.evidenceId }}</div>
          <div class="label">verify_status（校验状态）</div>
          <div><EvidenceStateTag :status="detail.verifyStatus" /></div>

          <div class="label">received_at（接收时间）</div>
          <div>{{ formatDateTime(detail.receivedAt) }}</div>
          <div class="label">source_client_id（客户端 ID）</div>
          <div>{{ detail.sourceClientId || '-' }}</div>

          <div class="label">hostname / source_ip</div>
          <div>{{ detail.hostname || '-' }} / {{ detail.sourceIp || '-' }}</div>
          <div class="label">last_verified_at（最近校验）</div>
          <div>{{ formatDateTime(detail.lastVerifiedAt) }}</div>

          <div class="label">raw_storage_path（存储路径）</div>
          <div class="wide mono">{{ detail.rawStoragePath }}</div>

          <div class="label">raw_offset / raw_length</div>
          <div>{{ detail.rawOffset }} / {{ detail.rawLength }} 字节</div>
          <div class="label">hash_algorithm（算法）</div>
          <div>{{ detail.hashAlgorithm }}</div>
        </div>
      </section>

      <section class="evidence-detail">
        <h3>哈希证明</h3>
        <div class="evidence-grid">
          <div class="label">algorithm_version（版本）</div>
          <div>{{ detail.algorithmVersion }}</div>
          <div class="label">摘要长度</div>
          <div>SHA-256 / 32 字节（64 位十六进制）</div>
          <div class="label">raw_hash（原始哈希）</div>
          <div class="wide">
            <span class="mono">{{ hashText || '-' }}</span>
            <el-button link type="primary" @click="copyHash">复制完整哈希</el-button>
            <div class="evidence-hash-hint">{{ hashHint }}</div>
          </div>
        </div>
      </section>

      <section class="evidence-detail">
        <h3>原始日志正文</h3>
        <pre class="evidence-raw">{{ detail.rawLog || '[原始日志文件不存在或无法按 offset/length 读取]' }}</pre>
      </section>

      <section class="evidence-detail">
        <h3>融合日志关联</h3>
        <div class="evidence-grid">
          <div class="label">category（类别）</div>
          <div>{{ detail.categoryLabel || categoryLabelOf(detail.category) }}</div>
          <div class="label">type（日志类型）</div>
          <div class="mono">{{ detail.type || '-' }}</div>
          <div class="label">normalized_table（数据表）</div>
          <div class="mono">{{ detail.normalizedTable || '未识别 type，未进入融合表' }}</div>
          <div class="label">normalized_row_id（行 ID）</div>
          <div class="mono">{{ detail.normalizedRowId ?? '-' }}</div>
        </div>
      </section>

      <section class="evidence-detail">
        <h3>单条完整性校验</h3>
        <div class="evidence-steps">
          <div class="evidence-step">
            <strong :class="{ error: isMissing }">1. 原始文件{{ isMissing ? '不存在' : '存在' }}</strong>
            <span>确认存储路径中的文件可访问</span>
          </div>
          <div class="evidence-step">
            <strong :class="{ error: isMissing }">2. 原始字节{{ isMissing ? '读取失败' : '读取成功' }}</strong>
            <span>按 offset 与 length 读取对应内容</span>
          </div>
          <div class="evidence-step">
            <strong :class="{ error: isMissing || isMismatch }">
              3. SHA-256 {{ isMissing ? '未执行' : isMismatch ? '不一致' : isPending ? '待校验' : '一致' }}
            </strong>
            <span>重新计算并与存证哈希比对</span>
          </div>
        </div>
      </section>
    </template>
    <div v-else class="dialog-state">
      {{ error || '没有取到该存证的详情' }}
    </div>

    <div v-if="error && detail" class="inline-error" role="alert" style="margin-bottom: 12px">
      <span class="message">{{ error }}</span>
    </div>

    <div class="evidence-modal-actions">
      <el-button :disabled="loading || verifying" @click="emit('close')">关闭</el-button>
      <el-button type="primary" :loading="verifying" :disabled="!evidenceId" @click="verify">
        重新校验这一条
      </el-button>
    </div>
  </BaseModal>
</template>
