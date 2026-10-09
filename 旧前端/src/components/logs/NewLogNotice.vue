<script setup lang="ts">
/** 有筛选条件或暂停实时时，新日志不直接插入列表，而是显示这个待刷新提示 */
defineProps<{ count: number }>()
defineEmits<{ (e: 'refresh'): void }>()
</script>

<template>
  <button v-if="count > 0" type="button" class="new-log-btn" @click="$emit('refresh')">
    收到 {{ count }} 条新日志，点击刷新
  </button>
</template>

<style scoped>
.new-log-btn {
  border: 1px solid #b7e2cd;
  background: var(--green-bg);
  color: var(--green);
  font-weight: 600;
  font-size: 12px;
  height: 26px;
  padding: 0 10px;
  border-radius: 3px;
}

.new-log-btn:hover {
  border-color: var(--green);
}
</style>
