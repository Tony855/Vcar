<template>
  <main class="navigation-page">
    <header><div><h2>司机导航</h2><p>{{ gpsState }}</p></div><button @click="startGps" :disabled="tracking">开始共享位置</button></header>
    <p class="hint">允许定位后，当前位置会自动更新。点击地图设置目的地，再规划驾车路线。</p>
    <div ref="mapElement" class="navigation-map"></div>
    <section class="route-summary">
      <span>目的地：{{ destination ? destination.lat.toFixed(5) + ', ' + destination.lng.toFixed(5) : '点击地图选择' }}</span>
      <span v-if="routeInfo">距离 {{ (routeInfo.distance / 1000).toFixed(2) }} km · 预计 {{ Math.ceil(routeInfo.duration / 60) }} 分钟</span>
      <button @click="planRoute" :disabled="!current || !destination || routing">{{ routing ? '规划中…' : '规划路线' }}</button>
    </section>
    <p v-if="error" class="error">{{ error }}</p>
  </main>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { createDriverApi } from './api/driver'

const route = useRoute()
const mapElement = ref(null)
const gpsState = ref('尚未开启定位')
const current = ref(null)
const destination = ref(null)
const tracking = ref(false)
const routing = ref(false)
const routeInfo = ref(null)
const error = ref('')
let map, currentMarker, destinationMarker, routeLine, watchId
let api
let lastSentAt = 0

function updateCurrent(point) {
  current.value = point
  if (currentMarker) currentMarker.setLatLng([point.lat, point.lng])
  else currentMarker = L.circleMarker([point.lat, point.lng], { radius: 8, color: '#2563eb', fillColor: '#60a5fa', fillOpacity: 1 }).addTo(map)
}
async function sendGps(position) {
  const point = { lat: position.coords.latitude, lng: position.coords.longitude }
  updateCurrent(point)
  gpsState.value = '定位已连接'
  if (Date.now() - lastSentAt < 10000) return
  lastSentAt = Date.now()
  try {
    await api.post('/v1/driver/location', {
      latitude: point.lat, longitude: point.lng,
      accuracy_m: position.coords.accuracy,
      heading_deg: position.coords.heading == null ? null : position.coords.heading,
      speed_mps: position.coords.speed == null ? null : position.coords.speed,
      recorded_at: new Date(position.timestamp).toISOString()
    })
  } catch (err) { error.value = err.response?.data?.detail || '位置上传失败' }
}
async function startGps() {
  if (!navigator.geolocation) { error.value = '设备不支持 GPS 定位'; return }
  tracking.value = true
  watchId = navigator.geolocation.watchPosition(sendGps, err => {
    gpsState.value = '定位失败'
    error.value = err.message
    tracking.value = false
  }, { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 })
}
async function planRoute() {
  if (!current.value || !destination.value) return
  routing.value = true
  error.value = ''
  try {
    const response = await api.post('/v1/driver/navigation/route', {
      coordinates: [[current.value.lng, current.value.lat], [destination.value.lng, destination.value.lat]]
    })
    const route = response.data.routes?.[0]
    if (!route?.geometry?.coordinates) throw new Error('没有可行驶路线')
    if (routeLine) map.removeLayer(routeLine)
    routeLine = L.geoJSON(route.geometry, { style: { color: '#2563eb', weight: 5 } }).addTo(map)
    routeInfo.value = route
    map.fitBounds(routeLine.getBounds(), { padding: [24, 24] })
  } catch (err) { error.value = err.response?.data?.detail || err.message || '路线规划失败' }
  finally { routing.value = false }
}
onMounted(async () => {
  map = L.map(mapElement.value).setView([11.5564, 104.9282], 13)
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map)
  map.on('click', event => {
    destination.value = event.latlng
    if (destinationMarker) destinationMarker.setLatLng(event.latlng)
    else destinationMarker = L.marker(event.latlng).addTo(map).bindPopup('导航目的地')
  })
  try {
    const session = await createDriverApi(route.params.tg_id)
    api = session.api
  } catch (err) { error.value = err.message || '司机身份验证失败' }
})
onUnmounted(() => {
  if (watchId != null) navigator.geolocation.clearWatch(watchId)
  if (map) map.remove()
})
</script>

<style scoped>
.navigation-page { max-width: 900px; margin: 0 auto; padding: 16px; font-family: system-ui, sans-serif; }
header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
h2 { margin: 4px 0; }
header p, .hint { color: #64748b; font-size: 13px; }
.navigation-map { height: min(62vh, 600px); min-height: 340px; border-radius: 12px; margin: 12px 0; }
.route-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; justify-content: space-between; }
button { border: 0; border-radius: 9px; padding: 10px 14px; background: #2563eb; color: white; font-weight: 600; }
button:disabled { opacity: .45; }
.error { color: #b91c1c; }
</style>
