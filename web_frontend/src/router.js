import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import DriverProfile from './DriverProfile.vue'
import OrderList from './OrderList.vue'
import IncomeList from './IncomeList.vue'
import AccountInfo from './AccountInfo.vue'
import PassengerBooking from './PassengerBooking.vue'
import DriverNavigation from './DriverNavigation.vue'

const routes = [
  { path: '/', redirect: '/admin/' },
  { path: '/admin/', component: App },
  { path: '/passenger', component: PassengerBooking },
  { path: '/mini/:tg_id', component: DriverProfile },
  { path: '/mini/:tg_id/navigation', component: DriverNavigation },
  { path: '/mini/:tg_id/orders', component: OrderList },
  { path: '/mini/:tg_id/income', component: IncomeList },
  { path: '/mini/:tg_id/account', component: AccountInfo },
  { path: '/:pathMatch(.*)*', redirect: '/admin/' }
]

const router = createRouter({ history: createWebHistory(), routes })
export default router
