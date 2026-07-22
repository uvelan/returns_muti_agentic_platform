import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const repositoryRoot = fileURLToPath(
  new URL("../", import.meta.url),
);

export default defineConfig(({ mode }) => {
  const env = loadEnv(
    mode,
    repositoryRoot,
    "FRONTEND_",
  );

  const backendTarget =
  env.FRONTEND_BACKEND_TARGET.trim();

  if (!backendTarget) {
    throw new Error(
      "FRONTEND_BACKEND_TARGET is required.",
    );
  }

  return {
    plugins: [react()],

    envDir: repositoryRoot,

    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      proxy: {
        "/data-console/v1": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/health": {
          target: backendTarget,
          changeOrigin: true,
        },
      },
    },

    preview: {
      host: "0.0.0.0",
      port: 4173,
      strictPort: true,
    },

    build: {
      target: "es2023",
      emptyOutDir: true,
      sourcemap: false,
    },
  };
});