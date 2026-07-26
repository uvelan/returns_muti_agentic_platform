# Test and Runtime Matrix

Commands, environments, exit codes and blockers from this source state.

| Command | Environment | Exit code | Duration | Result | Details |
|---|---|---|---|---|---|
| python3 -m compileall -q backend/src backend/tests scripts | python:3.13-slim Linux container, Python 3.13.14 | 0 | not captured | PASSED | No output. |
| cd backend && ruff check . | Linux container, Ruff 0.15.21 | 0 | about 3s | PASSED | All checks passed. |
| cd backend && ruff format --check . | Linux container, Ruff 0.15.21 | 0 | about 3s | PASSED | 246 files already formatted. |
| cd backend && mypy --strict src | Linux container, MyPy 2.3.0 | 0 | about 28s | PASSED | Success: no issues found in 172 source files. |
| cd backend && pytest | Linux container, Python 3.13.14, Pytest 9.1.1 | 0 | 18.16s test time | PASSED | 987 passed, 1 deprecation warning. |
| cd frontend && npm run lint | Windows host Node 24.14.0/npm 11.9.0 because no general WSL distro | 0 | 14.146s | PASSED | ESLint clean. |
| cd frontend && npm run typecheck | Windows host, rerun outside sandbox after node_modules/.tmp EPERM | 0 | 5.188s | PASSED | Initial sandbox attempt blocked; approved rerun passed. |
| cd frontend && npm run test | Windows host | 0 | 7.06s test time | PASSED | 13 files, 39 tests passed. |
| cd frontend && npm run build | Windows host | 0 | 7.347s | PASSED | Vite production build and bundle mock-artifact check passed. |
| ./scripts/run_stage4n_ai_simulator_e2e.sh | Linux container, Python 3.13.14 | 0 | about 7s | PASSED | 9 validator checks passed; 5 focused tests passed, 6 deselected. |
| ./scripts/infra.sh start (docker compose up -d --wait equivalent) | Docker Desktop Linux engine | 1 | about 4m initial recovery | FAILED | Initial PostgreSQL/SQL/Neo4j health budget failure; dependencies later individually healthy. Retry still exited 1 because successful one-shot mongodb-rs-init was treated as exited. |
| ./scripts/infra.sh probe | Repository script inspection | 2 | not run | BLOCKED | scripts/infra.sh has no probe action; supported actions are start/full-containerized/stop/status/logs/reset/config. |
| docker compose --profile containerized-app up -d --build (containerized equivalent attempted for start_stage4m_simulation.sh) | Docker Desktop Linux engine | 1 | about 6m including build/start | FAILED | Seed succeeded and core dependencies became healthy. Backend/orchestrator restart on wrong /usr/local/lib/python3.13/config paths; concurrent Mongo index migration also raised IndexNotFound code 27. |
| ./scripts/run_stage4m_simulated_e2e.sh BRANCH_PARCEL | Full stack | None | not run | BLOCKED | Application profile did not reach validated readiness before evidence cutoff. |
| ./scripts/run_stage4m_simulated_e2e.sh OFFSITE_HEAVY | Full stack | None | not run | BLOCKED | Application profile did not reach validated readiness before evidence cutoff. |
| BRANCH_LTL/OFFSITE_PARCEL/DIRECT_VENDOR/NO_PHYSICAL_RETURN business E2E | Source inspection | 2 | not run | BLOCKED | run_stage4m_simulated_e2e.sh accepts only BRANCH_PARCEL and OFFSITE_HEAVY. |
| npm run test:e2e:real | Real full stack | None | not run | BLOCKED | Full stack not validated and required screens are absent. |
