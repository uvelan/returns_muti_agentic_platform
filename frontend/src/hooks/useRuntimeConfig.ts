import { createContext, useContext } from "react";
import type { RuntimeConfig } from "../api/runtimeConfig";

export const RuntimeConfigContext = createContext<RuntimeConfig | null>(null);

/**
 * The runtime configuration, or `null` while it is in flight or unavailable.
 *
 * Nullable on purpose: `RuntimeConfigProvider` no longer blocks first paint, so
 * a caller can render before the fetch resolves. It used to throw when the
 * context was empty, which conflated "outside the provider" -- a wiring bug --
 * with "not loaded yet", which is the normal first render.
 */
export function useRuntimeConfig(): RuntimeConfig | null {
  return useContext(RuntimeConfigContext);
}
