<template>
  <main class="booking-page">
    <header><h2>Vcar 叫车</h2><span>{{ authState }}</span></header>
    <div class="instructions">点击地图选择上车点，再点击选择目的地。第三次点击会重新选择上车点。</div>
    <div ref="mapElement" class="booking-map"></div>
    <div class="selection">
      <p>上车点：{{ pickup ? coords(pickup) : '请在地图上选择' }}</p>
      <p>目的地：{{ dropoff ? coords(dropoff) : '请在地图上选择' }}</p>
      <button @click="useMyLocation">使用我的当前位置</button>
      <button :disabled="!pickup || !dropoff || !token || submitting" @click="book">{{ submitting ? '正在提交…' : '确认叫车' }}</button>
    </div>
    <div v-if="order" class="order-state">
      <strong>订单 #{{ order.order_id }}</strong>
      <span>状态：{{ order.status }}</span>
      <span v-if="order.price_estimate != null">预估费用：{{ order.price_estimate }}</span>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
  </main>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import axios from 'axios'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const mapElement = ref(null)
const pickup = ref(null)
const dropoff = ref(null)
const token = ref('')
const authState = ref('正在验证 Telegram…')
const submitting = ref(false)
const order = ref(null)
const error = ref('')
let map
let pickupMarker
let dropoffMarker
let pollTimer
let orderSocket
let lastOrderEventId = 0
let reconnectTimer
let reconnectAttempts = 0
let connectingOrderSocket = false
let orderStreamStopped = false
let passengerSessionRequest

const coords = point => `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`
function setMarker(point, type) {
  if (type === 'pickup') {
    pickup.value = point
    if (pickupMarker) map.removeLayer(pickupMarker)
    pickupMarker = L.circleMarker([point.lat, point.lng], { radius: 9, color: '#15803d', fillColor: '#22c55e', fillOpacity: 1 }).addTo(map).bindPopup('上车点')
  } else {
    dropoff.value = point
    if (dropoffMarker) map.removeLayer(dropoffMarker)
    dropoffMarker = L.circleMarker([point.lat, point.lng], { radius: 9, color: '#b91c1c', fillColor: '#ef4444', fillOpacity: 1 }).addTo(map).bindPopup('目的地')
  }
}
async function authenticate() {
  if (passengerSessionRequest) return passengerSessionRequest
  passengerSessionRequest = (async () => {
    const initData = window.Telegram?.WebApp?.initData
    if (!initData) {
      authState.value = '请从乘客 Telegram 机器人打开'
      return false
    }
    window.Telegram.WebApp.ready()
    window.Telegram.WebApp.expand()
    try {
      const response = await axios.post('/api/passenger/session', { init_data: initData })
      token.value = response.data.token
      authState.value = '身份验证成功'
      return true
    } catch {
      authState.value = '身份验证失败'
      error.value = 'Telegram 身份验证失败，请重新打开小程序'
      return false
    }
  })()
  try {
    return await passengerSessionRequest
  } finally {
    passengerSessionRequest = undefined
  }
}

// Passenger access tokens expire after 30 minutes. Refresh the Telegram
// WebApp session once after a 401, then retry the original request exactly once.
async function passengerApi(method, url, data) {
  const request = () => axios({
    method,
    url,
    data,
    headers: { Authorization: `Bearer ${token.value}` }
  })
  try {
    return await request()
  } catch (err) {
    if (err.response?.status !== 401) throw err
    const refreshed = await authenticate()
    if (!refreshed) throw err
    return await request()
  }
}
function useMyLocation() {
  if (!navigator.geolocation) { error.value = '当前设备不支持定位'; return }
  navigator.geolocation.getCurrentPosition(position => {
    const point = { lat: position.coords.latitude, lng: position.coords.longitude }
    setMarker(point, 'pickup')
    map.setView([point.lat, point.lng], 16)
  }, () => { error.value = '无法获取当前位置，请检查定位权限' }, { enableHighAccuracy: true, timeout: 10000 })
}
async function book() {
  submitting.value = true
  error.value = ''
  try {
    const response = await passengerApi('post', '/api/passenger/orders', {
      pickup_lat: pickup.value.lat, pickup_lng: pickup.value.lng,
      dropoff_lat: dropoff.value.lat, dropoff_lng: dropoff.value.lng
    })
    order.value = response.data
    connectOrderStream()
    pollTimer = window.setInterval(refreshOrder, 10000)
  } catch (err) {
    error.value = err.response?.data?.detail || '下单失败，请检查路线服务或稍后重试'
  } finally { submitting.value = false }
}
function stopOrderStream() {
  orderStreamStopped = true
  if (reconnectTimer) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = undefined
  }
  if (orderSocket && orderSocket.readyState < WebSocket.CLOSING) orderSocket.close()
}

function scheduleOrderReconnect() {
  if (orderStreamStopped || reconnectTimer || !order.value) return
  if (['COMPLETED', 'CANCELLED'].includes(order.value.status)) return
  const delay = Math.min(30000, 1000 * (2 ** Math.min(reconnectAttempts, 5)))
  reconnectAttempts += 1
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = undefined
    connectOrderStream()
  }, delay)
}

async function connectOrderStream() {
  if (!order.value || !token.value || connectingOrderSocket || orderStreamStopped) return
  connectingOrderSocket = true
  try {
    const response = await passengerApi('post', '/api/ws-ticket', {
      scope: `passenger-order:${order.value.order_id}`
    })
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const cursor = lastOrderEventId > 0 ? `&last_event_id=${lastOrderEventId}` : ''
    const socket = new WebSocket(`${protocol}//${location.host}/api/passenger/orders/${order.value.order_id}/stream?ticket=${encodeURIComponent(response.data.ticket)}${cursor}`)
    orderSocket = socket
    socket.onopen = () => { reconnectAttempts = 0 }
    socket.onmessage = event => {
      try {
        const payload = JSON.parse(event.data)
        if (payload.type === 'snapshot') {
          const snapshotEventId = Number(payload.last_event_id || 0)
          if (snapshotEventId >= lastOrderEventId) {
            lastOrderEventId = snapshotEventId
            order.value = { ...order.value, status: payload.status }
          }
        } else {
          const eventId = Number(payload.event_id || 0)
          if (eventId && eventId <= lastOrderEventId) return
          if (eventId) lastOrderEventId = eventId
          order.value = { ...order.value, ...payload }
        }
        if (['COMPLETED', 'CANCELLED'].includes(order.value.status)) {
          if (pollTimer) {
            window.clearInterval(pollTimer)
            pollTimer = undefined
          }
          stopOrderStream()
        }
      } catch { /* Keep polling and reconnecting if a stream frame is malformed. */ }
    }
    socket.onerror = () => socket.close()
    socket.onclose = () => {
      if (orderSocket === socket) orderSocket = undefined
      scheduleOrderReconnect()
    }
  } catch {
    // Polling remains active while ticket or Redis services recover.
    scheduleOrderReconnect()
  } finally {
    connectingOrderSocket = false
  }
}

async function refreshOrder() {
  if (!order.value) return
  try {
    const response = await passengerApi('get', `/api/passenger/orders/${order.value.order_id}`)
    order.value = { ...order.value, ...response.data }
    if (['COMPLETED', 'CANCELLED'].includes(order.value.status)) {
      if (pollTimer) window.clearInterval(pollTimer)
      pollTimer = undefined
      stopOrderStream()
    }
  } catch (err) {
    if (err.response?.status === 401 || err.response?.status === 404) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  }
}
onMounted(async () => {
  map = L.map(mapElement.value).setView([11.5564, 104.9282], 13)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map)
  map.on('click', event => {
    if (!pickup.value || (pickup.value && dropoff.value)) {
      if (dropoffMarker) map.removeLayer(dropoffMarker)
      dropoffMarker = undefined
      dropoff.value = null
      setMarker(event.latlng, 'pickup')
    } else setMarker(event.latlng, 'dropoff')
  })
  await authenticate()
})
onUnmounted(() => { if (pollTimer) window.clearInterval(pollTimer); stopOrderStream(); if (map) map.remove() })
</script>

<style scoped>
.booking-page { max-width: 900px; margin: 0 auto; padding: 16px; font-family: system-ui, sans-serif; }
header { display: flex; justify-content: space-between; align-items: center; }
header h2 { margin: 4px 0 12px; }
header span, .instructions { color: #64748b; font-size: 13px; }
.booking-map { height: min(58vh, 560px); min-height: 340px; border-radius: 12px; margin: 14px 0; }
.selection { display: grid; gap: 8px; }
.selection p { margin: 2px 0; font-size: 14px; }
button { border: 0; border-radius: 9px; padding: 11px 14px; background: #2563eb; color: white; font-weight: 600; }
button:disabled { opacity: .45; }
.order-state { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 14px; padding: 12px; background: #eff6ff; border-radius: 10px; }
.error { color: #b91c1c; }
</style>
