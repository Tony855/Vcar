<template>
  <div class="orders-page">
    <div class="header">
      <button class="back-btn" @click="goBack">← 返回</button>
      <h2 class="title">我的订单</h2>
    </div>

    <div v-if="loading" class="empty-state">正在加载订单...</div>
    <div v-else-if="orders.length === 0" class="empty-state">暂无历史订单</div>
    
    <div v-else class="order-list">
      <div v-for="o in orders" :key="o.id" class="order-card">
        <div class="order-header">
          <span class="order-id">订单 #{{ o.id }}</span>
          <span :class="['order-status', getStatusClass(o.status)]">{{ o.status }}</span>
        </div>
        <div class="order-detail">
          <span class="order-time">{{ o.time_str }}</span>
          <span class="order-price">${{ parseFloat(o.price).toFixed(2) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import axios from 'axios'

const route = useRoute()
const router = useRouter()
const tgId = route.params.tg_id
const orders = ref([])
const loading = ref(true)

const goBack = () => {
  router.push(`/mini/${tgId}`)
}

const getStatusClass = (status) => {
  if (status === 'COMPLETED') return 'status-success'
  if (status === 'CANCELLED') return 'status-danger'
  if (status === 'IN_PROGRESS') return 'status-warning'
  return 'status-default'
}

const formatTime = (timeStr) => {
  if (!timeStr) return '未知时间'
  return timeStr.substring(0, 16).replace('T', ' ')
}

onMounted(async () => {
  try {
    const res = await axios.get(`/api/driver/${tgId}/orders`)
    orders.value = res.data
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.orders-page {
  background-color: #f8fafc;
  min-height: 100vh;
  padding: 20px 16px;
  font-family: -apple-system, sans-serif;
}
.header {
  display: flex;
  align-items: center;
  margin-bottom: 24px;
}
.back-btn {
  background: none;
  border: none;
  font-size: 16px;
  font-weight: bold;
  color: #3b82f6;
  padding: 0;
  margin-right: 16px;
  cursor: pointer;
}
.title {
  font-size: 20px;
  font-weight: 900;
  color: #1f2937;
  margin: 0;
}
.empty-state {
  text-align: center;
  color: #9ca3af;
  font-size: 14px;
  padding: 40px 0;
}
.order-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.order-card {
  background: #ffffff;
  border-radius: 16px;
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  border: 1px solid #f3f4f6;
}
.order-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.order-id {
  font-weight: 900;
  color: #1f2937;
}
.order-status {
  font-size: 12px;
  font-weight: bold;
  padding: 4px 8px;
  border-radius: 6px;
}
.status-success { background: #dcfce7; color: #15803d; }
.status-danger { background: #fee2e2; color: #b91c1c; }
.status-warning { background: #fef3c7; color: #b45309; }
.status-default { background: #f3f4f6; color: #4b5563; }
.order-detail {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
}
.order-time { color: #6b7280; }
.order-price { font-weight: 900; color: #1f2937; }
</style>
