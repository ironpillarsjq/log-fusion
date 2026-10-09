<script setup lang="ts">
import { TriangleAlert } from 'lucide-vue-next'

withDefaults(
  defineProps<{
    /** 主提示文案 */
    message: string
    /** 次级说明，例如「页面展示的是最后一次成功获取的数据」 */
    hint?: string
    /** 是否显示重试按钮 */
    retryable?: boolean
    /** 重试请求进行中 */
    retrying?: boolean
    retryText?: string
  }>(),
  { hint: '', retryable: true, retrying: false, retryText: '重试' },
)

defineEmits<{ (e: 'retry'): void }>()
</script>

<template>
  <div class="inline-error" role="alert">
    <TriangleAlert />
    <span class="message">
      {{ message }}
      <span v-if="hint" class="hint">（{{ hint }}）</span>
    </span>
    <button v-if="retryable" class="btn" type="button" :disabled="retrying" @click="$emit('retry')">
      {{ retrying ? '重试中…' : retryText }}
    </button>
  </div>
</template>
