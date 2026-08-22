import { test } from "@playwright/test";

/**
 * Whether this run is allowed to claim a persisted outcome.
 *
 * The mock project may prove that a screen rendered and that a request got a
 * well-shaped answer. It may not prove that anything was written, because
 * nothing is behind it. Tests that assert persistence call `requireRealStack()`
 * and skip -- visibly, with a reason -- when the run is mock-backed.
 */
export const REAL_BASE_URL = process.env.E2E_REAL_BASE_URL;

export function requireRealStack(): void {
  test.skip(
    REAL_BASE_URL === undefined,
    "E2E_REAL_BASE_URL is unset: this run is mock-backed and cannot prove a persisted outcome",
  );
}

/** Skips everything in a mock project, so real-stack specs run once, not twice. */
export function realStackOnly(projectName: string): void {
  test.skip(
    !projectName.startsWith("real"),
    "real-stack spec; runs only in the real-* projects",
  );
}
