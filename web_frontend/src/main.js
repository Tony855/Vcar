import { createApp, h } from 'vue'
import { RouterView } from 'vue-router'
import router from './router'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

// 创建一个根组件，里面放 <router-view> 来显示路由页面
const app = createApp({
  render: () => h(RouterView)
})

app.use(router)
app.use(ElementPlus)
app.mount('#app')
