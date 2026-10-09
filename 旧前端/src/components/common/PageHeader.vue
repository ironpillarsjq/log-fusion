<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    /** 面包屑父级，默认「客户端管理」 */
    parent?: string
    /** 面包屑当前项，默认取 title */
    current?: string
  }>(),
  { subtitle: '', parent: '客户端管理', current: '' },
)
</script>

<template>
  <div class="breadcrumb">
    {{ parent }}&nbsp;&nbsp;/&nbsp;&nbsp;<span>{{ current || title }}</span>
  </div>
  <div class="page-heading">
    <div>
      <h1>{{ title }}</h1>
      <p v-if="subtitle">{{ subtitle }}</p>
    </div>
    <div class="actions">
      <slot />
    </div>
  </div>
</template>
