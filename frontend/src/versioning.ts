/**
 * Path normalisation for the four-domain shell.
 *
 * `VERSION_ONE_PREFIX`, `COPILOT_V2_PATH` and `legacyRouteDestination` were
 * deleted with the legacy app in Wave F4. They existed to route everything that
 * was not a canonical domain into `/v1`; there is no `/v1` now, and `App`
 * redirects unrecognised paths to `/returns` instead.
 *
 * Kept as its own module rather than inlined because `App` and the domain
 * registry both need to agree on what a path *is* before comparing one --
 * a trailing slash making `/returns/` miss `/returns` is precisely the bug this
 * prevents.
 */
export function normalizeBrowserPath(pathname: string): string {
  return pathname.replace(/\/+$/, "") || "/";
}
