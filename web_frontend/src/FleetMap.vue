<template>
  <section class="fleet-panel">
    <div class="fleet-toolbar">
      <div><strong>实时车队地图</strong><span class="muted">在线 {{ onlineCount }} / {{ drivers.length }}</span></div>
      <button @click="loadFleet" :disabled="loading">{{ loading ? '刷新中…' : '刷新位置' }}</button>
    </div>
    <div ref="mapElement" class="fleet-map"></div>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import axios from 'axios'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const props = defineProps({ token: { type: String, required: true } })
const mapElement = ref(null)
const drivers = ref([])
const loading = ref(false)
const error = ref('')
const onlineCount = computed(() => drivers.value.filter(x => x.online).length)
let map
let socket
let refreshTimer
const markers = new Map()

function upsertDriver(driver) {
  if (!driver || !driver.telegram_id || !Number.isFinite(Number(driver.latitude)) || !Number.isFinite(Number(driver.longitude))) return
  const id = String(driver.telegram_id)
  const point = [Number(driver.latitude), Number(driver.longitude)]
  const color = driver.online ? '#16a34a' : '#94a3b8'
  let marker = markers.get(id)
  if (!marker) {
    marker = L.circleMarker(point, { radius: 8, color, fillColor: color, fillOpacity: 0.9 }).addTo(map)
    markers.set(id, marker)
  } else {
    marker.setLatLng(point).setStyle({ color, fillColor: color })
  }
  marker.bindPopup(`司机 ${id}<br>${driver.first_name || ''}<br>${driver.plate_number || '未登记车牌'}<br>${driver.online ? '在线' : '离线'}`)
  const index = drivers.value.findIndex(x => String(x.telegram_id) === id)
  if (index >= 0) drivers.value[index] = { ...drivers.value[index], ...driver }
  else drivers.value.push(driver)
}

async function loadFleet() {
  loading.value = true
  error.value = ''
  try {
    const response = await axios.get('/api/admin/fleet', { headers: { Authorization: `Bearer ${props.token}` } })
    drivers.value = response.data
    const present = new Set()
    for (const driver of response.data) {
      present.add(String(driver.telegram_id))
      if (driver.latitude != null && driver.longitude != null) upsertDriver(driver)
    }
    for (const [id, marker] of markers) {
      if (!present.has(id)) { map.removeLayer(marker); markers.delete(id) }
    }
  } catch (err) {
    error.value = err.response?.status === 401 ? '管理员会话已过期，请重新登录' : '车队数据暂时不可用'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  map = L.map(mapElement.value).setView([11.5564, 104.9282], 12)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map)
  await loadFleet()
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  socket = new WebSocket(`${protocol}//${location.host}/api/v1/admin/fleet/stream?token=${encodeURIComponent(props.token)}`)
  socket.onmessage = event => {
    try {
      const update = JSON.parse(event.data)
      upsertDriver({ ...update, online: true })
    } catch { /* Ignore malformed telemetry frame. */ }
  }
  socket.onclose = () => { error.value = '实时连接已断开，地图将继续定时刷新' }
  refreshTimer = window.setInterval(loadFleet, 15000)
})
onUnmounted(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
  if (socket) socket.close()
  if (map) map.remove()
})
</script>

<style scoped>
.fleet-panel { display: grid; gap: 12px; }
.fleet-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.fleet-toolbar > div { display: flex; gap: 14px; align-items: baseline; }
.muted { color: #64748b; font-size: 13px; }
.fleet-toolbar button { border: 1px solid #cbd5e1; border-radius: 8px; background: white; padding: 7px 12px; cursor: pointer; }
.fleet-map { height: min(68vh, 620px); min-height: 360px; border-radius: 12px; z-index: 0; }
.error { color: #b91c1c; font-size: 13px; }
</style>
