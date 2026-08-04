# Working Application Baseline

## Current commit
0845d3f272d360e908e77705da00e56b4724887e

## Commands run
- `.\scripts\bootstrap_host.ps1` (Success)
- `npm run build` in frontend (Success)
- `python -c "import return_platform.main"` in backend (Success)
- `docker compose ps` (Failed: Docker daemon not available)
- `.\scripts\run_backend_host.ps1` (Failed: `httpx.ConnectError`)

## State
- **WORKING**: 
  - Backend dependencies install (`uv sync`, `uv pip install`)
  - Frontend dependencies install (`npm ci`)
  - Backend imports (`import return_platform.main`)
  - Frontend builds (`vite build`)
- **BROKEN**:
  - Infrastructure startup (Docker unavailable on host)
  - Backend startup (`run_backend_host.ps1` fails with `httpx.ConnectError`)
- **MISSING**:
  - Graceful configuration loading when infrastructure (Vault) is offline.

## Exact failure
The FastAPI backend application startup fails immediately with an `httpx.ConnectError` during the `lifespan` initialization. It attempts to fetch `runtime_settings` from Vault (`resolve_runtime_settings_from_vault`), but because the Docker infrastructure is offline, the HTTP connection to Vault fails, crashing the process.

## First failing component
Backend FastAPI Lifespan / Configuration Loading

## Files responsible
- `backend/src/return_platform/main.py`
- `backend/src/return_platform/secrets/runtime.py`
- `backend/src/return_platform/secrets/vault.py`

## Minimal correction order
1. Configuration loading
2. Environment bootstrap
3. Dependency initialization
4. Database connections
5. API router registration
6. Worker initialization
7. Frontend runtime endpoint
8. Health checks
