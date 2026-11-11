import axios from 'axios'

const SESSION_TOKEN_KEY = 'vcar_driver_token'
const SESSION_EXPIRY_KEY = 'vcar_driver_token_expires_at'
const SESSION_TG_ID_KEY = 'vcar_driver_tg_id'

let sessionRequest

async function ensureDriverSession(force = false) {
  const existingToken = sessionStorage.getItem(SESSION_TOKEN_KEY)
  const expiresAt = Number(sessionStorage.getItem(SESSION_EXPIRY_KEY) || 0)
  if (!force && existingToken && expiresAt > Date.now() + 30_000) {
    return {
      token: existingToken,
      tgId: sessionStorage.getItem(SESSION_TG_ID_KEY)
    }
  }

  if (sessionRequest) return sessionRequest

  sessionRequest = (async () => {
    const initData = window.Telegram?.WebApp?.initData
    if (!initData) {
      throw new Error('请从 Telegram 司机机器人内打开此页面')
    }

    window.Telegram.WebApp.ready()
    window.Telegram.WebApp.expand()

    const response = await axios.post('/api/driver/session', { init_data: initData })
    const { token, tg_id: tgId, expires_in: expiresIn } = response.data
    sessionStorage.setItem(SESSION_TOKEN_KEY, token)
    sessionStorage.setItem(SESSION_TG_ID_KEY, String(tgId))
    sessionStorage.setItem(SESSION_EXPIRY_KEY, String(Date.now() + expiresIn * 1000))
    return { token, tgId: String(tgId) }
  })()

  try {
    return await sessionRequest
  } finally {
    sessionRequest = undefined
  }
}

export async function createDriverApi(expectedTgId) {
  const session = await ensureDriverSession()
  if (expectedTgId && String(expectedTgId) !== String(session.tgId)) {
    throw new Error('当前 Telegram 账号与页面司机 ID 不匹配')
  }

  const api = axios.create({ baseURL: '/api' })
  api.interceptors.request.use(async config => {
    const current = await ensureDriverSession()
    config.headers.Authorization = `Bearer ${current.token}`
    return config
  })
  api.interceptors.response.use(
    response => response,
    async error => {
      const original = error.config
      if (error.response?.status === 401 && original && !original._driverRetry) {
        original._driverRetry = true
        sessionStorage.removeItem(SESSION_TOKEN_KEY)
        sessionStorage.removeItem(SESSION_EXPIRY_KEY)
        sessionStorage.removeItem(SESSION_TG_ID_KEY)
        const refreshed = await ensureDriverSession(true)
        original.headers = original.headers || {}
        original.headers.Authorization = `Bearer ${refreshed.token}`
        return api(original)
      }
      return Promise.reject(error)
    }
  )
  return { api, tgId: String(session.tgId) }
}
