import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // 按依赖分块：主包变小、vendor 利用浏览器缓存
        manualChunks: {
          react: ["react", "react-dom"],
          antd: ["antd"],
          query: ["@tanstack/react-query"],
          echarts: ["echarts"],
        },
      },
    },
  },
  server: {
    // 监听 0.0.0.0：手机/局域网设备可通过本机 IP 直接访问
    host: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
