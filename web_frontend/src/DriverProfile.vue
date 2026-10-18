<template>
  <div class="driver-page">
    <div class="header-bg"></div>
    <div class="content-container">
      <div class="info-card">
        <div class="avatar">🚗</div>
        <div class="info-text">
          <div class="plate-row">
            <h2 class="plate">{{ info.plate || '加载中...' }}</h2>
            <span class="status-badge">在线</span>
          </div>
          <div class="rating-row">
            <span class="stars">⭐ {{ info.rating || '5.0' }}</span>
            <span class="review-count">({{ info.total_reviews || 0 }} 条评价)</span>
          </div>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">累计收入</div>
          <div class="stat-value">$ {{ info.total_income || '0.00' }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">完成单量</div>
          <div class="stat-value">{{ info.completed_orders || 0 }} 单</div>
        </div>
      </div>

      <div class="menu-grid">
        <div class="menu-item" @click="goToOrders">
          <span class="menu-icon">📋</span>
          <span class="menu-text">我的订单</span>
        </div>
        <div class="menu-item" @click="goToIncome">
          <span class="menu-icon">💰</span>
          <span class="menu-text">收入明细</span>
        </div>
        <div class="menu-item" @click="goToAccount">
          <span class="menu-icon">👤</span>
          <span class="menu-text">账户信息</span>
        </div>
        <div class="menu-item" @click="contactSupport">
          <span class="menu-icon">🎧</span>
          <span class="menu-text">联系客服</span>
        </div>
      </div>

      <div class="reviews-section">
        <h3 class="section-title">📝 乘客评价</h3>
        <div v-if="reviews.length === 0" class="empty-state">暂无评价</div>
        <div v-for="(r, i) in reviews" :key="i" class="review-card">
          <div class="review-header">
            <span class="reviewer-name">匿名乘客</span>
            <span class="review-stars">{{ '⭐'.repeat(r.rating) }}</span>
          </div>
          <p class="review-comment">{{ r.comment || '该乘客没有留下文字评价' }}</p>
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
const info = ref({})
const reviews = ref([])

// 页面跳转
const goToOrders = () => router.push(`/mini/${tgId}/orders`)
const goToIncome = () => router.push(`/mini/${tgId}/income`)
const goToAccount = () => router.push(`/mini/${tgId}/account`)

// 联系客服 (跳转 TG)
const contactSupport = () => {
  // ⚠️ 请将下面的 vcar_support 改成你真实的客服 TG 账号
  const supportUsername = "vcar_support" 
  if (window.Telegram?.WebApp) {
    window.Telegram.WebApp.openTelegramLink(`https://t.me/${supportUsername}`)
  } else {
    window.open(`https://t.me/${supportUsername}`)
  }
}

onMounted(async () => {
  if (window.Telegram?.WebApp) {
    window.Telegram.WebApp.ready()
    window.Telegram.WebApp.expand()
  }
  try {
    const [res1, res2] = await Promise.all([
      axios.get(`/api/driver/${tgId}/profile`),
      axios.get(`/api/driver/${tgId}/reviews`)
    ])
    info.value = res1.data
    reviews.value = res2.data
  } catch (e) { console.error(e) }
})
</script>

<style scoped>
.driver-page { background-color: #f8fafc; min-height: 100vh; padding-bottom: 80px; font-family: -apple-system, sans-serif; position: relative; overflow-x: hidden; }
.header-bg { position: absolute; top: 0; left: 0; width: 100%; height: 160px; background: linear-gradient(135deg, #3b82f6, #4f46e5); border-bottom-left-radius: 40px; border-bottom-right-radius: 40px; z-index: 0; }
.content-container { position: relative; z-index: 10; padding: 40px 16px 0; max-width: 480px; margin: 0 auto; }
.info-card { background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-radius: 24px; padding: 24px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1); border: 1px solid rgba(255, 255, 255, 0.2); display: flex; align-items: center; gap: 16px; }
.avatar { width: 80px; height: 80px; background: #dbeafe; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 36px; border: 4px solid #ffffff; box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.05); flex-shrink: 0; }
.info-text { flex: 1; }
.plate-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.plate { font-size: 24px; font-weight: 900; color: #1f2937; margin: 0; }
.status-badge { background: #dcfce7; color: #15803d; font-size: 12px; font-weight: 700; padding: 2px 8px; border-radius: 6px; border: 1px solid #bbf7d0; }
.rating-row { display: flex; align-items: center; font-size: 14px; }
.stars { color: #eab308; font-weight: 700; }
.review-count { color: #9ca3af; margin-left: 8px; }
.stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 24px; }
.stat-card { background: #ffffff; border-radius: 16px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #f3f4f6; }
.stat-label { color: #6b7280; font-size: 12px; font-weight: 700; margin-bottom: 4px; }
.stat-value { color: #1f2937; font-size: 24px; font-weight: 900; }
.menu-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 24px; }
.menu-item { background: #ffffff; border-radius: 16px; padding: 12px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.05); display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: pointer; transition: transform 0.1s; }
.menu-item:active { transform: scale(0.95); }
.menu-icon { font-size: 24px; margin-bottom: 4px; }
.menu-text { font-size: 10px; font-weight: 700; color: #4b5563; }
.reviews-section { margin-top: 32px; }
.section-title { font-size: 18px; font-weight: 900; color: #1f2937; margin-bottom: 12px; }
.empty-state { text-align: center; color: #9ca3af; font-size: 14px; padding: 16px 0; }
.review-card { background: #ffffff; padding: 16px; border-radius: 16px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); border: 1px solid #f9fafb; margin-bottom: 12px; }
.review-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.reviewer-name { font-weight: 700; color: #374151; font-size: 14px; }
.review-stars { color: #eab308; font-size: 12px; }
.review-comment { color: #4b5563; font-size: 14px; line-height: 1.5; margin: 0; }
</style>
