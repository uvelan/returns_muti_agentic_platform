/**
 * The boot-time environment gate, which used to be a zod schema.
 *
 * Replacing a validated library with hand-written checks is only safe if the
 * checks refuse the same inputs, so these assert the refusals rather than the
 * happy path alone. The module validates at import time and throws, so each
 * case resets the module registry and imports it again under a different
 * environment.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

async function loadEnv() {
  vi.resetModules();
  return import("./env");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("the environment gate", () => {
  it("accepts the environment the test runner provides", async () => {
    const { env } = await loadEnv();

    expect(env.MODE.length).toBeGreaterThan(0);
    expect(typeof env.DEV).toBe("boolean");
    expect(typeof env.PROD).toBe("boolean");
  });

  it("defaults an absent flag to false rather than refusing", async () => {
    vi.stubEnv("VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED", undefined);
    const { env } = await loadEnv();

    expect(env.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED).toBe(false);
  });

  it('turns "true" into true and "false" into false', async () => {
    vi.stubEnv("VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED", "true");
    const on = await loadEnv();
    expect(on.env.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED).toBe(true);

    vi.stubEnv("VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED", "false");
    const off = await loadEnv();
    expect(off.env.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED).toBe(false);
  });

  it("refuses a flag that is neither, instead of quietly reading it as off", async () => {
    // The failure this guards: a flag misspelled "yes" reads as false while the
    // deployment's configuration says the feature is on, and nothing says so.
    vi.stubEnv("VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED", "yes");
    const noise = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(loadEnv()).rejects.toThrow("Frontend environment validation failed.");

    const [message, reported] = noise.mock.calls[0] as [string, Record<string, string>];
    expect(message).toBe("Invalid frontend environment configuration.");
    // The message has to name the accepted values, or the operator is told the
    // flag is wrong without being told what right looks like.
    expect(reported.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED).toContain("true");
    expect(reported.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED).toContain("false");
    noise.mockRestore();
  });

  it("refuses an empty MODE", async () => {
    vi.stubEnv("MODE", "");
    const noise = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(loadEnv()).rejects.toThrow("Frontend environment validation failed.");

    noise.mockRestore();
  });

  it("names every offending field at once, not just the first", async () => {
    vi.stubEnv("MODE", "");
    vi.stubEnv("VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED", "yes");
    const noise = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(loadEnv()).rejects.toThrow();

    const reported = noise.mock.calls[0]?.[1] as Record<string, string>;
    expect(Object.keys(reported).sort()).toEqual([
      "MODE",
      "VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED",
    ]);
    noise.mockRestore();
  });
});
