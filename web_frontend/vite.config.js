import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/', // 👈 关键修改：让资源直接请求 /assets/xxx.js
  build: { outDir: 'dist' }
})
