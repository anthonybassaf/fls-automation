import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

export default defineConfig(({ mode }) => ({
  server: {
    host: "0.0.0.0",
    port: 3000,
    strictPort: true,
    allowedHosts: true, // or ["verifire.dar.com"] if you prefer
    // Make HMR use plain ws on the same port (no https/wss)
    hmr: {
      protocol: "ws",
      host: undefined, // let Vite infer from current URL
      clientPort: 3000,
    },
    // (optional) proxy to your backend if you were using it
    // proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, ws: true } }
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
}));