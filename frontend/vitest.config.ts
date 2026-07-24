import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    react(),
  ],

  test: {
    environment: "jsdom",
    pool: "forks",
    isolate: true,

    setupFiles: [
      "./src/test/setup.ts",
    ],

    include: [
      "src/**/*.{test,spec}.{ts,tsx}",
    ],

    clearMocks: true,
    restoreMocks: true,
    unstubGlobals: true,

    coverage: {
      provider: "v8",

      reporter: [
        "text",
        "json",
        "html",
      ],

      exclude: [
        "node_modules/**",
        "dist/**",
        "coverage/**",
        "src/test/**",
        "src/env.ts",
        "src/main.tsx",
        "**/*.d.ts",
        "**/*.config.{js,ts}",
      ],
    },
  },
});