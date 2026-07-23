# Codex Continuation Context — Data Console Management

## Purpose

Continue the repository from its current dirty worktree without resetting or
discarding existing changes. The active priority is now the Data Console rather
than the Return workflow scenario runner.

## Standing execution rules

- Use Docker for frontend and backend execution and validation.
- Do not create a Git commit unless the user explicitly changes this instruction.
- Preserve all existing user and Codex worktree changes.
- Keep screenshot capture deferred until the hardening page.
- Use the repository-root `.env`; do not create `backend/.env`.
- Never expose credentials, DSNs, provider errors, or unredacted secrets.

## Current validated foundation

- Infrastructure overview API/UI is implemented.
- SQL Server and MongoDB metadata inventory contracts are implemented and sandbox
  validated through scripts.
- Bounded sampling contracts exist; live UI sampling is not enabled.
- Customer graph write/read-back and Platform MongoDB evidence are sandbox validated.
- Six read-only Graph Evidence API routes and the Graph Evidence frontend are
  contract tested and live proxy validated.
- The Temporal Return workflow has seven deterministic stage-context contracts:
  intake, discovery, eligibility, return request, fulfillment tracking, bay
  assignment, and feedback learning.
- All seven workflow contexts passed live Temporal/MongoDB persistence validation.
- Latest complete backend gate before this priority shift: Ruff passed, strict mypy
  passed for 128 checked files, and 929 tests passed.

## New product priority

Build a full Data Console that allows an administrator/developer to:

1. View SQL Server tables/views and their columns.
2. View MongoDB collections, counts, and indexes.
3. View Neo4j labels, relationship types, constraints, and graph data.
4. Browse bounded rows/documents/nodes.
5. Add, edit, and delete data only in explicitly writable platform or sandbox assets.
6. Import CSV, JSON, and JSONL through preview, validation, and approval.
7. Export permitted datasets and selections.
8. Generate coherent scenario data through a provider-neutral AI boundary, validate
   it, preview it, and require approval before import.

## Locked ownership and mutation boundary

- SQL Server/OMC and governed source MongoDB are read-only unless an asset is
  explicitly approved as writable.
- Platform MongoDB owns platform sessions, audit, outbox, configuration, and evidence.
- Neo4j is derived and rebuildable, never authoritative business state.
- Initial CRUD/import/delete/AI-generated writes target dedicated platform or sandbox
  workspaces only.
- AI generation never writes directly. It produces a bounded preview package that
  must pass canonical validation and explicit approval.
- No arbitrary SQL, MongoDB filters, or Cypher are accepted from the frontend.
- Deletes require audit evidence and confirmation; prefer soft delete where the data
  contract supports it.

## Planned implementation order

1. Unified inventory API for SQL Server, MongoDB, and Neo4j structure.
2. Data Sources / Inventory frontend page.
3. Governed bounded data browser.
4. Writable sandbox CRUD with audit evidence.
5. Import/export jobs and preview reports.
6. Interactive graph explorer and governed graph CRUD.
7. AI Scenario Data Studio with provider-neutral generation and deterministic fixtures.
8. End-to-end scenario runner.
9. Hardening and screenshot capture.

## Active bounded step

Implement the unified inventory API and Data Console Inventory page using existing
inventory contracts and lifespan-owned clients. The endpoint must return partial
results with safe warnings when one dependency is unavailable. It must remain
metadata-only and read-only.

## Acceptance boundary for the active step

- SQL Server metadata uses the existing fixed metadata collector.
- MongoDB metadata uses the existing fixed metadata collector.
- No database credentials or arbitrary queries enter API responses.
- Partial dependency failure preserves healthy inventory data.
- Frontend has a routed Inventory page with loading, error, partial, and empty states.
- Backend and frontend Docker lint/type/test/build gates pass.
- Live Docker proxy validation is performed where the local dependencies permit it.
- No screenshots and no Git commit.
