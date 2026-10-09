<script setup lang="ts">
import { ShieldCheck } from 'lucide-vue-next'

/**
 * 存证策略说明条：向使用者明确"先存原始字节、再解析"与哈希算法版本，
 * 避免把哈希误解成原始日志的替代品。
 */
withDefaults(defineProps<{ algorithm?: string; version?: string }>(), {
  algorithm: 'SHA-256',
  version: 'EVIDENCE-V1',
})
</script>

<template>
  <div class="evidence-banner">
    <div class="evidence-banner-left">
      <ShieldCheck />
      <span>
        <strong>存证策略正常：</strong>原始字节先保存、后解析；每条日志独立计算 {{ algorithm }}。
      </span>
    </div>
    <span>算法版本：{{ version }}</span>
  </div>
</template>
