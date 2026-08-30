import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    // Listen on all interfaces, not just localhost, so the app is reachable
    // from other devices via this machine's LAN IP or hostname.
    host: true,
    // Vite 5+ rejects requests whose Host header isn't localhost/the bound
    // IP by default (DNS-rebinding protection) -- that's what actually
    // blocked http://<hostname>:5174 even once `host: true` let the socket
    // accept the connection. This is a local dev box on a private LAN, so
    // trusting any Host header here is an acceptable tradeoff.
    allowedHosts: true,
  },
})
