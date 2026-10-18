import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import DriverProfile from './DriverProfile.vue'

const routes = [
  { path: '/admin/', component: App },
  { path: '/mini/:tg_id', component: DriverProfile },
  { path: '/:pathMatch(.*)*', redirect: '/admin/' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
