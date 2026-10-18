<template>
  <div class="income-page">
    <div class="header">
      <button class="back-btn" @click="goBack">← 返回</button>
      <h2 class="title">收入明细</h2>
    </div>
    
    <div class="total-card">
      <div class="total-label">累计总收入</div>
      <div class="total-amount">${{ totalIncome.toFixed(2) }}</div>
    </div>

    <div v-if="loading" class="empty-state">正在加载明细...</div>
    <div v-else-if="incomes.length === 0" class="empty-state">暂无收入记录</div>
    <div v-else class="list">
      <div v-for="o in incomes" :key="o.id" class="list-item">
        <span class="order-id">订单 #{{ o.id }}</span>
        <span class="amount">+ ${{ parseFloat(o.price).toFixed(2) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import axios from 'axios'

const route = useRoute()
const router = useRouter()
const tgId = route.params.tg_id
const incomes = ref([])
const loading = ref(true)

const goBack = () => router.push(`/mini/${tgId}`)

const totalIncome = computed(() => {
  return incomes.value.reduce((sum, item) => sum + parseFloat(item.price || 0), 0)
})

onMounted(async () => {
  try {
    const res = await axios.get(`/api/driver/${tgId}/income`)
    incomes.value = res.data
  } catch (e) { console.error(e) } finally { loading.value = false }
})
</script>

<style scoped>
.income-page { background-color: #f8fafc; min-height: 100vh; padding: 20px 16px; font-family: -apple-system, sans-serif; }
.header { display: flex; align-items: center; margin-bottom: 24px; }
.back-btn { background: none; border: none; font-size: 16px; font-weight: bold; color: #3b82f6; margin-right: 16px; cursor: pointer; }
.title { font-size: 20px; font-weight: 900; color: #1f2937; margin: 0; }
.total-card { background: linear-gradient(135deg, #10b981, #059669); border-radius: 20px; padding: 24px; text-align: center; color: white; margin-bottom: 24px; box-shadow: 0 10px 15px -3px rgba(16, 185, 129, 0.3); }
.total-label { font-size: 14px; opacity: 0.9; margin-bottom: 8px; font-weight: bold; }
.total-amount { font-size: 36px; font-weight: 900; }
.empty-state { text-align: center; color: #9ca3af; font-size: 14px; padding: 40px 0; }
.list { background: white; border-radius: 16px; padding: 8px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.list-item { display: flex; justify-content: space-between; padding: 16px 0; border-bottom: 1px solid #f3f4f6; }
.list-item:last-child { border-bottom: none; }
.order-id { color: #6b7280; font-size: 14px; }
.amount { color: #059669; font-weight: 900; }
</style>