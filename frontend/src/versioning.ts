export const VERSION_ONE_PREFIX = "/v1";
export const COPILOT_V2_PATH = "/v2/copilot";

export function normalizeBrowserPath(pathname: string): string {
  return pathname.replace(/\/+$/, "") || "/";
}

export function legacyRouteDestination(
  pathname: string,
  search = "",
  hash = "",
): string {
  const normalized = normalizeBrowserPath(pathname);
  return normalized === "/"
    ? `${VERSION_ONE_PREFIX}/associate/returns${search}${hash}`
    : `${VERSION_ONE_PREFIX}${normalized}${search}${hash}`;
}
