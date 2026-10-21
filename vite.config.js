import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
// In development the React app (port 5173) forwards /api calls to the Flask server (port 5000). No database details ever reach the browser.
export default defineConfig({ plugins: [react()], server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:5000' } } })
