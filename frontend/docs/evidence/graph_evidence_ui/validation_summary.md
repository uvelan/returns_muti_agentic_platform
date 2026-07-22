# Data Console Customer Graph Evidence frontend validation

Date: 2026-07-22

## Classification

```text
Frontend source implementation:       CONTRACT_TESTED
Frontend tests:                       CONTRACT_TESTED
Frontend lint/typecheck/build:        PASS
Live Docker frontend-to-backend API:  PASS
Live visual browser validation:       DEFERRED TO HARDENING
Overall screen classification:        CONTRACT_TESTED; LIVE API PROXY VERIFIED
```

No `PRODUCTION_VALIDATED` claim is made.

## Docker execution

Frontend gates ran in `mcr.microsoft.com/playwright:v1.61.1-jammy` with Node
`v24.17.0`. The backend ran from repository source in `python:3.13-slim`.
MongoDB ran as the `mongodb` service from `compose.yaml`.

```text
docker run --rm -v <repo>/frontend:/app -v frontend_node_modules:/app/node_modules -w /app mcr.microsoft.com/playwright:v1.61.1-jammy sh -lc "npm run lint && npm run typecheck && npm run test -- src/api/graphEvidence.test.ts src/features/data-console/pages/GraphEvidencePage.test.tsx"

docker run --rm -e FRONTEND_BACKEND_TARGET=http://backend:8000 -v <repo>/frontend:/app -v frontend_node_modules:/app/node_modules -w /app mcr.microsoft.com/playwright:v1.61.1-jammy sh -lc "npm run build && npm run test"

docker compose up -d mongodb

docker run -d --name stage3-backend --network returns_muti_agentic_platform_default -p 127.0.0.1:8000:8000 --env-file .env -v <repo>:/workspace -v backend_python_packages:/usr/local/lib/python3.13/site-packages -w /workspace/backend python:3.13-slim python -m uvicorn return_platform.asgi:app --host 0.0.0.0 --port 8000

docker run -d --name stage3-frontend --network returns_muti_agentic_platform_default -p 127.0.0.1:5173:5173 -e FRONTEND_BACKEND_TARGET=http://stage3-backend:8000 -v <repo>/frontend:/app -v frontend_node_modules:/app/node_modules -w /app mcr.microsoft.com/playwright:v1.61.1-jammy npm run dev -- --host 0.0.0.0
```

## Gate results

```text
ESLint:                    PASS, exit 0
Strict TypeScript:         PASS, exit 0
Focused tests:             PASS, 19/19, exit 0
Complete tests:            PASS, 19/19, exit 0
Vite production build:     PASS, 1,576 modules transformed, exit 0
Production JS bundle:      323.97 kB (95.03 kB gzip)
```

## Live retained evidence

```text
Document ID:     CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7
Sync run ID:     d084d10c-5bdf-4002-befb-8ccb9948f9e7
Report digest:   75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8
Document digest: 6ce23e2568171b3f53827dfb8b822f4c4cd2cec60080a6c959326136bdb81f5b
```

All six read-only routes returned HTTP 200 through the live Vite proxy:

| Route | Request ID |
|---|---|
| List | `d0216bea-78bc-461a-b49b-ae4522b23df8` |
| Latest | `a390a1d3-0ea2-4ec7-9233-8221d602d9bc` |
| Document summary | `f8fbf878-b596-47f9-b454-8b34a7b4d0db` |
| Sync-run lookup | `2edfc3f0-dd01-4176-a200-4695f5451e24` |
| Report-digest lookup | `08daceb8-dfcd-4cd9-a8fc-c797ba9f4efe` |
| Admin full evidence | `c930f8e9-1af5-4f39-9dce-32608a749be8` |

## Deferred hardening evidence

Desktop and mobile screenshots are explicitly deferred to the hardening phase.
Eight page tests cover exact lookup, summary inspection, admin full evidence,
viewer-safe denial, seek pagination, manual refresh, empty state, and hard API
errors. All six routes were also exercised through the live Docker Vite proxy.

During hardening, open
`http://localhost:5173/data-console/graph-evidence`, repeat the retained-ID
lookups, inspect the admin payload, capture desktop and narrow screenshots,
and record the request IDs rendered by the page.
