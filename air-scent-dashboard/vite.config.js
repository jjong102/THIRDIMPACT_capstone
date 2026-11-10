import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { fragranceBridgePlugin } from './vite.fragranceBridge.js'
import { mqttBridgePlugin } from './vite.mqttBridge.js'
import { llmBridgePlugin } from './vite.llmBridge.js'
import { sttBridgePlugin } from './vite.sttBridge.js'
import { ttsBridgePlugin } from './vite.ttsBridge.js'
import { gdmBridgePlugin } from './vite.gdmBridge.js'
import { rosNavBridgePlugin } from './vite.rosNavBridge.js'
import { systemBridgePlugin } from './vite.systemBridge.js'
import { mapsBridgePlugin } from './vite.mapsBridge.js'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  for (const [key, value] of Object.entries(env)) {
    if (process.env[key] === undefined) {
      process.env[key] = value
    }
  }

  return {
    plugins: [
      react(),
      fragranceBridgePlugin(),
      mqttBridgePlugin(),
      llmBridgePlugin(),
      sttBridgePlugin(),
      ttsBridgePlugin(),
      gdmBridgePlugin(),
      rosNavBridgePlugin(),
      systemBridgePlugin(),
      mapsBridgePlugin(),
    ],
    server: {
      host: true,
      proxy: {
        '/api/fragrance': 'http://127.0.0.1:5174',
        '/api/mqtt': 'http://127.0.0.1:5175',
        '/api/llm': 'http://127.0.0.1:5176',
        '/api/stt': 'http://127.0.0.1:5177',
        '/api/tts': 'http://127.0.0.1:5180',
        '/api/gdm': 'http://127.0.0.1:5178',
        '/api/nav': {
          target: 'http://127.0.0.1:5179',
          timeout: 30000,
        },
      },
    },
  }
})
