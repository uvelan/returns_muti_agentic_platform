import { useQuery } from "@tanstack/react-query";
import { fetchRuntimeConfig } from "../api/runtimeConfig";
import { RuntimeConfigContext } from "../hooks/useRuntimeConfig";

/**
 * Fetches `/api/runtime-config` and publishes it to the shell.
 *
 * It does not block first paint, and a failure does not replace the app with an
 * error screen. It used to do both -- for a value that no component reads, so a
 * transient blip on one endpoint took all four domains down and gained nothing.
 * Screens that come to depend on this should handle `null` (see
 * `useRuntimeConfig`) rather than reinstate a global gate: the four domains
 * already render their own loading and error states against their own data.
 */
export function RuntimeConfigProvider({ children }: { children: React.ReactNode }) {
  const { data } = useQuery({
    queryKey: ["runtime-config"],
    queryFn: fetchRuntimeConfig,
    staleTime: Infinity,
  });

  return (
    <RuntimeConfigContext.Provider value={data ?? null}>
      {children}
    </RuntimeConfigContext.Provider>
  );
}
