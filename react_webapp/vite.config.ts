import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    host: "::",
    port: 8080,
  },
  plugins: [
    react(),
    mode === 'development' &&
    componentTagger(),
  ].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));

// import { defineConfig } from 'vite'
// import react from '@vitejs/plugin-react'
// import history from 'connect-history-api-fallback'
// import type { Plugin } from 'vite'

// const spaFallback: Plugin = {
//   name: 'custom-spa-fallback',
//   configureServer(server) {
//     const middleware = history({
//       rewrites: [
//         { from: /^\/login/, to: '/index.html' }, // ✅ critical for your login flow
//         { from: /./, to: '/index.html' }
//       ]
//     })

//     server.middlewares.use((req, res, next) => {
//       middleware(req as any, res as any, next)
//     })
//   }
// }

// export default defineConfig({
//   plugins: [react(), spaFallback],
//   server: {
//     port: 8080,
//   },
// })




