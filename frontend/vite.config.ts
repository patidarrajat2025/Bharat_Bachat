import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["pwa-192.png", "pwa-512.png", "apple-touch-icon.png", "bharat-bachat-logo.png", "bharat-bachat-splash.png"],
      manifest: {
        name: "Bharat Bachat",
        short_name: "Bharat Bachat",
        description: "Aapki Bachat, Aapka Vikas",
        theme_color: "#0F9D58",
        background_color: "#F8FAFC",
        id: "/",
        orientation: "any",
        display: "standalone",
        prefer_related_applications: false,
        lang: "en-IN",
        start_url: "/login",
        scope: "/",
        icons: [
          { src: "/pwa-192.png", sizes: "192x192", type: "image/png" },
          { src: "/pwa-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
          { src: "/pwa-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" }
        ]
      }
    })
  ]
})
