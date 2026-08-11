import { fileURLToPath, URL } from "node:url";
import fs from "node:fs";
import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const repositoryRoot = fileURLToPath(
  new URL("../", import.meta.url),
);

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(
    mode,
    repositoryRoot,
    "FRONTEND_",
  );

  const loadedBackendTarget: unknown = Reflect.get(
    env,
    "FRONTEND_BACKEND_TARGET",
  );
  const backendTarget = (
    typeof loadedBackendTarget === "string"
      ? loadedBackendTarget
      : process.env.FRONTEND_BACKEND_TARGET ?? ""
  ).trim();

  if (!backendTarget) {
    throw new Error(
      "FRONTEND_BACKEND_TARGET is required.",
    );
  }

  if (command === "build" && (process.env.VITE_MOCK_MODE === "true" || mode === "mock")) {
    throw new Error("Production build rejects mock mode.");
  }

  return {
    plugins: [
      react(),
      {
        name: "remove-msw-worker",
        apply: "build",
        closeBundle() {
          const workerPath = path.resolve(process.cwd(), 'dist', 'mockServiceWorker.js');
          if (fs.existsSync(workerPath)) {
            fs.unlinkSync(workerPath);
          }
        }
      }
    ],

    envDir: repositoryRoot,

    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      proxy: {
        // One `/api` prefix, not one entry per version. The canonical Wave D
        // surfaces are versionless (`/api/principal`, `/api/returns`,
        // `/api/config`, `/api/ai`, `/api/graph-schema`), so the previous
        // `/api/v1` + `/api/v2` pair silently failed to forward every one of
        // them -- the dev server answered its SPA fallback and each screen saw
        // HTML where it expected JSON. This covers the versioned paths too.
        "/api": {
          target: backendTarget,
          changeOrigin: true,
        },
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
