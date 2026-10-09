<script setup lang="ts">
import type { Component } from 'vue'
import { onBeforeUnmount, watch } from 'vue'
import { X } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    /** 标题前的小图标组件 */
    icon?: Component
    wide?: boolean
  }>(),
  { title: '', icon: undefined, wide: false },
)

const emit = defineEmits<{ (e: 'close'): void }>()

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') emit('close')
}

/** 打开时锁定页面滚动，并监听 Esc */
watch(
  () => props.open,
  (open) => {
    if (open) {
      document.addEventListener('keydown', onKeydown)
      document.body.style.overflow = 'hidden'
    } else {
      document.removeEventListener('keydown', onKeydown)
      document.body.style.overflow = ''
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="overlay"
      role="dialog"
      aria-modal="true"
      @click.self="emit('close')"
    >
      <div class="modal" :class="{ wide }">
        <div class="modal-head">
          <div class="modal-title">
            <component :is="icon" v-if="icon" />
            <slot name="title">{{ title }}</slot>
          </div>
          <button class="icon-btn" type="button" aria-label="关闭" title="关闭" @click="emit('close')">
            <X />
          </button>
        </div>
        <div class="modal-body">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>
