<template>
  <div v-if="!isLoggedIn" class="login-wrapper">
    <el-card class="login-card">
      <h2 style="text-align: center; margin-bottom: 20px;">🔒 Vcar 运营登录</h2>
      <el-form @submit.prevent="login">
        <el-form-item><el-input v-model="loginForm.username" placeholder="账号 admin" /></el-form-item>
        <el-form-item><el-input v-model="loginForm.password" type="password" placeholder="密码 vcar888" show-password /></el-form-item>
        <el-button type="primary" native-type="submit" style="width: 100%" :loading="loading">登 录</el-button>
      </el-form>
    </el-card>
  </div>

  <el-container v-else class="admin-layout">
    <el-header class="header">
      <div class="logo">🚗 Vcar 运营控制台</div>
      <el-button type="danger" plain size="small" @click="logout">退出登录</el-button>
    </el-header>
    <el-main>
      <!-- 统计卡片 -->
      <el-row :gutter="20" class="mb-4">
        <el-col :xs="12" :sm="12" :md="6" v-for="(item, key) in statsDisplay" :key="key">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-title">{{ item.label }}</div>
            <div class="stat-value">{{ item.value }}</div>
          </el-card>
        </el-col>
      </el-row>
      
      <el-tabs v-model="activeTab">
        <el-tab-pane label="📋 实时订单 & 配置" name="orders">
          <el-row :gutter="20">
            <!-- 左侧：配置区 -->
            <el-col :xs="24" :md="8" class="mb-4">
              <!-- 动态调价 -->
              <el-card>
                <template #header>⚙️ 动态调价</template>
                <el-form label-width="80px">
                  <el-form-item label="起步价"><el-input-number v-model="pricing.base_fare" :precision="2" :step="0.1" style="width: 100%" /></el-form-item>
                  <el-form-item label="每公里"><el-input-number v-model="pricing.per_km" :precision="2" :step="0.1" style="width: 100%" /></el-form-item>
                  <el-button type="success" @click="savePricing" style="width: 100%">保存生效</el-button>
                </el-form>
              </el-card>
              
              <!-- 客服配置 (新增) -->
              <el-card style="margin-top: 20px;">
                <template #header>🎧 系统客服配置</template>
                <el-form label-width="80px">
                  <el-form-item label="客服TG号">
                    <el-input v-model="supportConfig.username" placeholder="例如: Tony855,Kefu2 (多个用英文逗号隔开)" />
                  </el-form-item>
                  <el-button type="primary" @click="saveSupport" style="width: 100%">保存客服设置</el-button>
                </el-form>
              </el-card>
            </el-col>

            <!-- 右侧：订单表格 -->
            <el-col :xs="24" :md="16">
              <el-card>
                <template #header>📋 实时订单</template>
                <el-table :data="orders" stripe style="width: 100%" height="500">
                  <el-table-column prop="id" label="ID" width="80" />
                  <el-table-column prop="status" label="状态" width="120" />
                  <el-table-column prop="price_estimate" label="一口价" width="100"><template #default="s">${{ Number(s.row.price_estimate).toFixed(2) }}</template></el-table-column>
                  <el-table-column prop="passenger_name" label="乘客" show-overflow-tooltip />
                  <el-table-column prop="driver_name" label="司机" show-overflow-tooltip />
                  <el-table-column prop="driver_plate" label="车牌" fixed="right">
                    <template #default="scope">
                      <el-tag v-if="scope.row.driver_plate" type="info" size="small" effect="dark">{{ scope.row.driver_plate }}</el-tag>
                      <span v-else style="color: #999; font-size: 12px;">待接单</span>
                    </template>
                  </el-table-column>
                </el-table>
              </el-card>
            </el-col>
          </el-row>
        </el-tab-pane>
      </el-tabs>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({ baseURL: '/api' })
const token = localStorage.getItem('vcar_token')
if (token) api.defaults.headers.common['Authorization'] = `Bearer ${token}`

const isLoggedIn = ref(!!token)
const loading = ref(false)
const loginForm = ref({ username: '', password: '' })
const activeTab = ref('orders')
const statsData = ref({ total_orders: 0, active_orders: 0, total_users: 0, drivers_online: 0 })
const orders = ref([])
const pricing = ref({ base_fare: 1, per_km: 0.5 })
const supportConfig = ref({ username: '' })

const statsDisplay = computed(() => ({
  orders: { label: "总订单", value: statsData.value.total_orders },
  active: { label: "进行中", value: statsData.value.active_orders },
  users: { label: "注册用户", value: statsData.value.total_users },
  drivers: { label: "在线司机", value: statsData.value.drivers_online }
}))

const login = async () => {
  loading.value = true
  try {
    const res = await api.post('/login', loginForm.value)
    localStorage.setItem('vcar_token', res.data.token)
    api.defaults.headers.common['Authorization'] = `Bearer ${res.data.token}`
    isLoggedIn.value = true
    fetchAdminData()
  } catch { ElMessage.error('登录失败') }
  finally { loading.value = false }
}

const logout = () => {
  localStorage.removeItem('vcar_token')
  isLoggedIn.value = false
}

const fetchAdminData = async () => {
  try {
    const [s, o, p, sup] = await Promise.all([
      api.get('/stats'), api.get('/orders'), api.get('/pricing'), api.get('/config/support')
    ])
    statsData.value = s.data
    orders.value = o.data
    pricing.value = p.data
    supportConfig.value = sup.data
  } catch (e) { if (e.response?.status === 401) logout() }
}

const savePricing = async () => {
  await api.post('/pricing', pricing.value)
  ElMessage.success('调价成功')
}

const saveSupport = async () => {
  try {
    await api.post('/config/support', supportConfig.value)
    ElMessage.success('🎉 客服配置已保存并生效！')
  } catch (err) {
    console.error(err)
    ElMessage.error('❌ 保存失败，请查看服务器后台日志！')
  }
}

onMounted(() => {
  if (isLoggedIn.value) fetchAdminData()
})
</script>

<style>
body { margin: 0; font-family: -apple-system, sans-serif; background: #f0f2f5; }
.stat-card { text-align: center; margin-bottom: 15px; }
.stat-title { font-size: 14px; color: #666; margin-bottom: 10px; }
.stat-value { font-size: 24px; font-weight: bold; color: #409EFF; }
.header { background: #fff; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #dcdfe6; }
.logo { font-size: 18px; font-weight: bold; }
.login-wrapper { display: flex; justify-content: center; align-items: center; height: 100vh; }
.login-card { width: 90%; max-width: 400px; }
@media (max-width: 768px) {
  .el-card__body { padding: 10px; }
}
</style>
