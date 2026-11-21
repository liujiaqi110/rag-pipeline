import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './styles/global.css'
import App from './App.vue'
import { router } from './router'
import { installUnauthorizedHandler } from './stores/auth'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
installUnauthorizedHandler()

window.addEventListener('kb:session-ended', () => {
  if (router.currentRoute.value.name !== 'login') router.replace({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
})

app.mount('#app')
