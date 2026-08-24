/**
 * The browser-visible environment, validated once at boot.
 *
 * `main.tsx` imports this for the side effect: a deployment whose configuration
 * is malformed should fail loudly on load rather than halfway through a return,
 * when an associate is holding a box.
 *
 * **This used to be a zod schema.** Four variables -- three of which Vite
 * inlines at build time as literal constants -- cost the eagerly loaded entry
 * chunk 57.21 kB raw and 12.89 kB gzipped, measured by building both ways. That
 * is roughly a fifth of the chunk every visitor downloads before anything
 * renders, spent on checking values that cannot vary at runtime. The library is
 * excellent and this was the wrong job for it.
 *
 * The rules below are the same rules, and they still throw on the same inputs.
 */

/**
 * Add browser-visible variables here using the `VITE_` prefix.
 *
 * Never expose passwords, credentials, or private connection strings: anything
 * reachable from `import.meta.env` in this file is compiled into a public
 * JavaScript bundle and is readable by anyone who loads the page.
 *
 * A flag follows the shape below -- `"true"` or `"false"` as a string, because
 * that is all an environment variable can hold, parsed once here so the rest of
 * the application sees a boolean.
 */
const RAW = import.meta.env;

type FieldErrors = Record<string, string>;

const errors: FieldErrors = {};

function requiredString(key: string, value: unknown): string {
  if (typeof value === "string" && value.length > 0) return value;
  errors[key] = "must be a non-empty string";
  return "";
}

function requiredBoolean(key: string, value: unknown): boolean {
  if (typeof value === "boolean") return value;
  errors[key] = "must be a boolean";
  return false;
}

/**
 * A `"true"` / `"false"` flag with a default.
 *
 * Absent is not an error -- that is what the default is for. Present and
 * something else is, because a flag misspelled as `"yes"` silently reading as
 * `false` is how a feature stays off while its configuration says it is on.
 */
function flag(key: string, value: unknown, fallback: boolean): boolean {
  if (value === undefined || value === "") return fallback;
  if (value === "true") return true;
  if (value === "false") return false;
  errors[key] = 'must be "true" or "false"';
  return fallback;
}

export const env = {
  MODE: requiredString("MODE", RAW.MODE),
  DEV: requiredBoolean("DEV", RAW.DEV),
  PROD: requiredBoolean("PROD", RAW.PROD),
  VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED: flag(
    "VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED",
    RAW.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED,
    false,
  ),
} as const;

if (Object.keys(errors).length > 0) {
  console.error("Invalid frontend environment configuration.", errors);

  throw new Error("Frontend environment validation failed.");
}

export type FrontendEnvironment = typeof env;
