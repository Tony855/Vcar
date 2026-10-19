<template>
  <div class="account-page">
    <div class="header">
      <button class="back-btn" @click="goBack">← 返回</button>
      <h2 class="title">账户信息</h2>
    </div>

    <div class="info-list">
      <div class="info-item">
        <span class="label">Telegram ID</span>
        <span class="value">{{ tgId }}</span>
      </div>
      <div class="info-item">
        <span class="label">绑定车牌</span>
        <span class="value plate-val">{{ info.plate || '未绑定' }}</span>
      </div>
      <div class="info-item">
        <span class="label">司机状态</span>
        <span :class="['value', info.is_online === '在线' ? 'status-online' : 'status-offline']">{{ info.is_online || '离线' }}</span>
      </div>
    </div>

    <div class="tips">
      💡 如需修改手机号或更换车辆信息，请点击主页的“联系客服”由人工为您处理。
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
const info = ref({})

const goBack = () => router.push(`/mini/${tgId}`)

onMounted(async () => {
  try {
    const res = await axios.get(`/api/driver/${tgId}/profile`)
    info.value = res.data
  } catch (e) { console.error(e) }
})
</script>

<style scoped>
.account-page { background-color: #f8fafc; min-height: 100vh; padding: 20px 16px; font-family: -apple-system, sans-serif; }
.header { display: flex; align-items: center; margin-bottom: 24px; }
.back-btn { background: none; border: none; font-size: 16px; font-weight: bold; color: #3b82f6; margin-right: 16px; cursor: pointer; }
.title { font-size: 20px; font-weight: 900; color: #1f2937; margin: 0; }
.info-list { background: #ffffff; border-radius: 16px; padding: 0 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.info-item { display: flex; justify-content: space-between; align-items: center; padding: 18px 0; border-bottom: 1px solid #f3f4f6; font-size: 15px; }
.info-item:last-child { border-bottom: none; }
.label { color: #6b7280; }
.value { color: #1f2937; font-weight: bold; }
.plate-val { color: #3b82f6; background: #eff6ff; padding: 4px 8px; border-radius: 6px; }

.tips { margin-top: 24px; font-size: 13px; color: #9ca3af; line-height: 1.6; padding: 0 8px; text-align: center; }

.status-online { color: #059669; font-weight: bold; }
.status-offline { color: #6b7280; font-weight: bold; }
</style>
