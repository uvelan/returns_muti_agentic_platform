import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendRoot = fileURLToPath(new URL("../", import.meta.url));
const repositoryRoot = path.resolve(frontendRoot, "..");
const backendRoot = path.join(repositoryRoot, "backend");
const exportScript = path.join(backendRoot, "scripts", "export_openapi.py");
const outputPath = path.join(frontendRoot, "openapi", "return-platform.openapi.json");

const candidates = process.platform === "win32"
  ? [
      path.join(backendRoot, ".venv", "Scripts", "python.exe"),
      "python3.13",
      "python",
    ]
  : [
      path.join(backendRoot, ".venv", "bin", "python"),
      "python3.13",
      "python3",
    ];

for (const candidate of candidates) {
  if (path.isAbsolute(candidate) && !existsSync(candidate)) {
    continue;
  }

  const result = spawnSync(candidate, [exportScript, outputPath], {
    cwd: frontendRoot,
    stdio: "inherit",
  });

  if (result.error?.code === "ENOENT") {
    continue;
  }

  process.exit(result.status ?? 1);
}

throw new Error("Python 3.13 is required to export the OpenAPI contract.");
