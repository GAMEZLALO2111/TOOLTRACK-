import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo (npm run dev) las llamadas a la API se reenvían al backend; en producción
// FastAPI sirve web/dist en el mismo origen.
const backend = "http://127.0.0.1:8000";
const apiPaths = ["/login", "/logout", "/me", "/users", "/workflow", "/audit", "/herramientas", "/catalogo"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(apiPaths.map((path) => [path, backend])),
  },
  build: { outDir: "dist", sourcemap: false },
});
