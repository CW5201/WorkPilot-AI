import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/tasks': 'http://localhost:8000',
      '/eval': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
