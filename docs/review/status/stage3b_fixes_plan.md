# Stage 3B — Fixes for Foundation Review Implementation Plan

This plan addresses the P0 and P1 blocking findings from the `stage3b_data_console_foundation_verdict.md`.

## User Review Required

- Please review the plan below for resolving the Docker/NPM peer conflict and tightening the mock boundaries.
- I will need to use Docker to run the verifications if possible.

## Proposed Changes

### 1. Configuration and Build Fixes
- **`package.json`**: Fix the `typescript` version in `devDependencies`. The version `~6.0.2` is causing peer dependency conflicts with `openapi-typescript` (which requires `^5.x`). Change it to `~5.6.2`. This addresses the P0 `npm ci` failure.
- **`vite.config.ts`**: Update the mock mode check to reject mock mode during *any* build command. We can use the `command` argument passed to `defineConfig(({ mode, command }))` and throw if `command === "build"` and (`process.env.VITE_MOCK_MODE === "true"` or `mode === "mock"`).

### 2. Application Shell & Routing Fixes
- **`main.tsx`**: Add `import.meta.env.DEV` to the `enableMocking` check to strictly bind the MSW worker to the development environment, addressing the P1 finding.
- **`routes.ts`**: 
  - Remove the `@typescript-eslint/no-explicit-any` suppression.
  - Change `React.LazyExoticComponent<any>` to `React.LazyExoticComponent<React.ComponentType<unknown>>` or an exact type to strictly type the lazy components without `any`.

### 3. Testing Improvements
- **`a11y.spec.ts`**: Add `await page.locator('h1').waitFor()` or similar to ensure the lazy loaded component (which includes the `h1`) has fully rendered before executing the `AxeBuilder` scan.
- **Component Tests**: Add the following missing unit tests for the shared components:
  - `Shell.test.tsx`: Tests for `Shell`, navigation rendering, and route rendering.
  - `ConfirmationDialog.test.tsx`: Tests for cancel and confirm behavior.
  - `ToastProvider.test.tsx`: Tests for rendering and timeout behavior.
  - `States.test.tsx`: Tests for `LoadingState`, `ErrorState`, and `PartialWarningBanner`.

### 4. Documentation Updates
- **`README.md`**: Temporarily revert Stage 3B from `COMPLETE` to `CHANGES REQUIRED`.
- **`docs/evidence/data_console_complete_ui/stage3b/evidence.md`**: Update the completion status to note the ongoing fixes for the review findings.
- **`docs/review/status/stage3b_status.md`**: Create/update the status document following the required template to reflect the current state (In Progress) and track the Docker gates.

## Verification Plan

### Automated Tests
1. Run `npm ci` cleanly to verify the typescript dependency fix.
2. Run `npm run check`, `npm run lint`, `npm run typecheck` to verify no errors.
3. Run `npm run test` to verify Vitest tests, including the newly added shared component tests.
4. Run `npm run test:a11y` to verify the accessibility issues are resolved (the wait fixes the heading issue).
