import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import { router } from './router'
// 顺序要求：先 Element Plus 自带样式，再项目全局样式，最后主题对齐覆盖
import 'element-plus/dist/index.css'
import './styles/main.css'
import './styles/element.css'

createApp(App)
  .use(createPinia())
  .use(router)
  .use(ElementPlus, { locale: zhCn })
  .mount('#app')
