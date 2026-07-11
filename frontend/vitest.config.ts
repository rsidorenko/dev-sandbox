import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "happy-dom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Tailwind/CSS не нужны в тестах — пропускаем обработку.
    css: false,
    // На случай, если Next/Vite что-то не удаётся разрешить через ESM-анализ.
    server: {
      deps: {
        inline: [/@(next|react-hook-form|zod|@hookform)/],
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
