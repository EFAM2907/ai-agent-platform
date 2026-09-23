import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Puerto fijo en 5173 (default de Vite) a proposito: es el origen que
// CORSMiddleware permite en app/main.py. Si este puerto cambia, el
// backend tiene que cambiar con el.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
