import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 프런트는 데이터를 직접 못 읽는다(parquet 는 파이썬 land). /api/* 는 FastAPI 백엔드로 넘긴다.
// 백엔드: uvicorn api.main:app --reload --port 8000  (리포 루트에서)
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
