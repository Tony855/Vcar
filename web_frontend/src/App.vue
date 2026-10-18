<template>
  <div class="login-wrapper" v-if="!isLoggedIn">
    <el-card class="login-card">
      <h2 style="text-align: center; margin-bottom: 20px;">🔒 Vcar 运营登录</h2>
      <el-form @submit.prevent="doLogin">
        <el-form-item>
          <el-input v-model="loginForm.username" placeholder="账号 admin" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="loginForm.password" type="password" placeholder="密码 vcar888" show-password />
        </el-form-item>
        <el-button type="primary" native-type="submit" style="width: 100%;" :loading="loading">登 录</el-button>
      </el-form>
    </el-card>
  </div>

  <el-container v-else class="admin-layout">
    <el-header class="header">
      <div class="logo">🚗 Vcar 运营控制台</div>
      <el-button type="danger" plain size="small" @click="logout">退出登录</el-button>
    </el-header>
    
    <el-main>
      <el-row :gutter="20" class="mb-4">
        <el-col :span="6" v-for="(val, key) in statsMap" :key="key">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-title">{{ val.label }}</div>
            <div class="stat-value">{{ val.value }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-tabs v-model="currentTab" class="demo-tabs">
        <el-tab-pane label="📋 实时订单 & 调价" name="orders">
          <el-row :gutter="20">
            <el-col :span="8">
              <el-card>
                <template #header>⚙️ 动态调价</template>
                <el-form label-width="80px">
                  <el-form-item label="起步价 ($)"><el-input-number v-model="pricing.base_fare" :precision="2" :step="0.1" /></el-form-item>
                  <el-form-item label="每公里 ($)"><el-input-number v-model="pricing.per_km" :precision="2" :step="0.1" /></el-form-item>
                  <el-button type="success" @click="savePricing" style="width:100%">保存生效</el-button>
                </el-form>
              </el-card>
            </el-col>
            <el-col :span="16">
              <el-card>
                <template #header>📋 实时订单</template>
                <el-table :data="orders" stripe style="width: 100%" height="400">
                  <el-table-column prop="id" label="ID" width="80" />
                  <el-table-column prop="status" label="状态" width="120" />
                  <el-table-column prop="distance_km" label="距离(km)" width="100" />
                  <el-table-column prop="price_estimate" label="一口价" width="100">
                    <template #default="scope">${{ Number(scope.row.price_estimate).toFixed(2) }}</template>
                  </el-table-column>
                  <el-table-column prop="passenger_name" label="乘客" />
                  <el-table-column prop="driver_name" label="司机" />
                </el-table>
              </el-card>
            </el-col>
          </el-row>
        </el-tab-pane>

        <el-tab-pane label="🛡️ 待审核司机" name="review">
          <el-card>
            <el-table :data="pendingDrivers" stripe style="width: 100%">
              <el-table-column prop="first_name" label="姓名 / TG" width="150">
                <template #default="scope">
                  <div>{{ scope.row.first_name }}</div>
                  <small>@{{ scope.row.username || scope.row.telegram_id }}</small>
                </template>
              </el-table-column>
              <el-table-column prop="phone" label="手机号" width="120" />
              <el-table-column prop="plate_number" label="车牌号" width="120" />
              <el-table-column label="证件照片">
                <template #default="scope">
                  <el-image style="width: 60px; height: 60px; margin-right: 5px;" :src="'/api/file/' + scope.row.car_photo" :preview-src-list="['/api/file/' + scope.row.car_photo]" fit="cover" />
                  <el-image style="width: 60px; height: 60px; margin-right: 5px;" :src="'/api/file/' + scope.row.license_photo" :preview-src-list="['/api/file/' + scope.row.license_photo]" fit="cover" />
                  <el-image style="width: 60px; height: 60px;" :src="'/api/file/' + scope.row.driving_license_photo" :preview-src-list="['/api/file/' + scope.row.driving_license_photo]" fit="cover" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="150" fixed="right">
                <template #default="scope">
                  <el-button type="success" size="small" @click="review(scope.row.telegram_id, 'APPROVED')">通过</el-button>
                  <el-button type="danger" size="small" @click="review(scope.row.telegram_id, 'REJECTED')">拒绝</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-tab-pane>

        <el-tab-pane label="📁 司机档案库 (服务端分页)" name="drivers">
          <el-card>
            <div style="margin-bottom: 15px; display: flex; justify-content: space-between;">
              <el-input v-model="searchKeyword" placeholder="搜索电话或车牌" style="width: 300px;" @keyup.enter="fetchDrivers(1)" clearable />
              <el-button type="primary" @click="fetchDrivers(1)">搜索</el-button>
            </div>
            <el-table :data="allDrivers" stripe style="width: 100%">
              <el-table-column prop="first_name" label="姓名 / TG" width="150">
                <template #default="scope">
                  <div>{{ scope.row.first_name }}</div>
                  <small>@{{ scope.row.username || scope.row.telegram_id }}</small>
                </template>
              </el-table-column>
              <el-table-column prop="phone" label="手机号" width="120" />
              <el-table-column prop="plate_number" label="车牌号" width="120" />
              <el-table-column prop="driver_status" label="状态" width="120">
                <template #default="scope">
                  <el-tag :type="scope.row.driver_status === 'APPROVED' ? 'success' : (scope.row.driver_status === 'REJECTED' ? 'danger' : 'warning')">
                    {{ scope.row.driver_status }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="证件">
                <template #default="scope">
                  <el-image v-if="scope.row.car_photo" style="width: 60px; height: 60px; margin-right: 5px;" :src="'/api/file/' + scope.row.car_photo" :preview-src-list="['/api/file/' + scope.row.car_photo]" fit="cover" />
                  <el-image v-if="scope.row.license_photo" style="width: 60px; height: 60px; margin-right: 5px;" :src="'/api/file/' + scope.row.license_photo" :preview-src-list="['/api/file/' + scope.row.license_photo]" fit="cover" />
                  <el-image v-if="scope.row.driving_license_photo" style="width: 60px; height: 60px;" :src="'/api/file/' + scope.row.driving_license_photo" :preview-src-list="['/api/file/' + scope.row.driving_license_photo]" fit="cover" />
                </template>
              </el-table-column>
            </el-table>
            <div style="margin-top: 20px; display: flex; justify-content: center;">
              <el-pagination
                background
                layout="prev, pager, next, total"
                :total="totalDrivers"
                :page-size="pageSize"
                :current-page="currentPage"
                @current-change="handlePageChange"
              />
            </div>
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const isLoggedIn = ref(!!localStorage.getItem('vcar_token'))
const loginForm = ref({ username: '', password: '' })
const loading = ref(false)
const currentTab = ref('orders')

const stats = ref({total_orders:0, active_orders:0, total_users:0, drivers_online:0})
const orders = ref([])
const pricing = ref({ base_fare: 1.0, per_km: 0.5 })
const pendingDrivers = ref([])

// 分页相关
const allDrivers = ref([])
const totalDrivers = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const searchKeyword = ref('')

const api = axios.create({ baseURL: '/api' })
api.interceptors.request.use(config => {
  const token = localStorage.getItem('vcar_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

const doLogin = async () => {
  loading.value = true
  try {
    const res = await axios.post('/api/login', loginForm.value)
    localStorage.setItem('vcar_token', res.data.token)
    isLoggedIn.value = true
    fetchData()
    fetchDrivers(1)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  }
  loading.value = false
}

const logout = () => {
  localStorage.removeItem('vcar_token')
  isLoggedIn.value = false
}

const statsMap = computed(() => ({
  orders: { label: '总订单', value: stats.value.total_orders },
  active: { label: '进行中', value: stats.value.active_orders },
  users: { label: '注册用户', value: stats.value.total_users },
  drivers: { label: '在线司机', value: stats.value.drivers_online }
}))

const fetchData = async () => {
  if (!isLoggedIn.value) return
  try {
    const [s, o, p, pd] = await Promise.all([
      api.get('/stats'), api.get('/orders'), api.get('/pricing'), api.get('/drivers/pending')
    ])
    stats.value = s.data
    orders.value = o.data
    pricing.value = p.data
    pendingDrivers.value = pd.data
  } catch (e) { if (e.response?.status === 401) logout() }
}

const fetchDrivers = async (page) => {
  currentPage.value = page
  try {
    const res = await api.get(`/drivers/all?page=${page}&page_size=${pageSize.value}&keyword=${searchKeyword.value}`)
    allDrivers.value = res.data.items
    totalDrivers.value = res.data.total
  } catch (e) {}
}

const handlePageChange = (page) => fetchDrivers(page)

const savePricing = async () => {
  await api.post('/pricing', pricing.value)
  ElMessage.success('调价成功')
}

const review = async (tg_id, status) => {
  await api.post('/drivers/review', { tg_id, status })
  ElMessage.success('操作成功')
  fetchData()
  fetchDrivers(currentPage.value)
}

onMounted(() => {
  if (isLoggedIn.value) {
    fetchData()
    fetchDrivers(1)
  }
})
</script>

<style>
body { margin: 0; background-color: #f0f2f5; font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif; }
.login-wrapper { height: 100vh; display: flex; align-items: center; justify-content: center; }
.login-card { width: 400px; }
.admin-layout { min-height: 100vh; }
.header { background-color: #fff; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 1px 4px rgba(0,21,41,.08); z-index: 10; }
.logo { font-size: 20px; font-weight: bold; }
.stat-card { text-align: center; }
.stat-title { color: #666; font-size: 14px; }
.stat-value { font-size: 28px; font-weight: bold; color: #409eff; margin-top: 10px; }
.mb-4 { margin-bottom: 20px; }
</style>
