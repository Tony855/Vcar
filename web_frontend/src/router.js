import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import DriverProfile from './DriverProfile.vue'
import OrderList from './OrderList.vue'
import IncomeList from './IncomeList.vue'

const routes = [
  { path: '/admin/', component: App },
  { path: '/mini/:tg_id', component: DriverProfile },
  { path: '/mini/:tg_id/orders', component: OrderList },
  { path: '/mini/:tg_id/income', component: IncomeList },
  { path: '/:pathMatch(.*)*', redirect: '/admin/' }
]

const router = createRouter({ history: createWebHistory(), routes })
export default router