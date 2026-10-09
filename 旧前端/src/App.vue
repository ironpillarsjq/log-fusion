<script setup lang="ts">
import { ref } from 'vue'
import { RouterView } from 'vue-router'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppTopbar from '@/components/layout/AppTopbar.vue'
import AppToast from '@/components/common/AppToast.vue'

/** 平台外壳：顶栏 + 侧边导航 + 路由内容区 */
const menuOpen = ref(false)

function closeMenu(): void {
  menuOpen.value = false
}
</script>

<template>
  <div class="app-shell" :class="{ 'menu-open': menuOpen }">
    <AppTopbar :menu-open="menuOpen" @toggle-menu="menuOpen = !menuOpen" />
    <AppSidebar @navigate="closeMenu" />
    <main class="app-main">
      <div class="content">
        <RouterView />
      </div>
    </main>
    <AppToast />
  </div>
</template>
