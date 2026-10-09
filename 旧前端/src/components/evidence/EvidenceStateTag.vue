<script setup lang="ts">
import { computed } from 'vue'
import type { VerifyStatus } from '@/types/evidence'
import { verifyStatusMeta } from '@/constants/evidence'

/** 校验状态标签：正常绿、异常红、待校验橙 */
const props = defineProps<{ status: VerifyStatus | string | null | undefined }>()

const meta = computed(() => verifyStatusMeta(props.status))
</script>

<template>
  <span class="evidence-state" :class="meta.className">
    <span class="dot" :class="meta.dotClass" />
    {{ meta.text }}
  </span>
</template>
