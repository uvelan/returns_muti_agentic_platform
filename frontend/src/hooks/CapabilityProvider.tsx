import { useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchPrincipal } from "../api/principal";
import {
  CapabilityContext,
  isUnauthorized,
  type CapabilityState,
} from "./capabilityContext";

/**
 * Supplies the capability layer to the four-domain shell (Phase 17).
 *
 * Built here rather than inside a screen deliberately: the plan warns that if
 * this is deferred into Phases 18-21, four screens will each invent their own.
 */
export function CapabilityProvider({ children }: { children: ReactNode }) {
  const query = useQuery({
    queryKey: ["principal"],
    queryFn: fetchPrincipal,
    // A principal changes when someone's roles change, which is an
    // out-of-band administrative event -- not something worth refetching on
    // every window focus. It is still refetched on mount, so a role change
    // takes effect on the next page load rather than needing a hard reload.
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    // 401 is a settled answer, not a transient failure. Retrying it delays the
    // sign-in prompt for no benefit.
    retry: (failureCount: number, error: Error) =>
      !isUnauthorized(error) && failureCount < 2,
  });

  const value = useMemo<CapabilityState>(() => {
    const granted = new Set<string>(query.data?.capabilities ?? []);
    return {
      principal: query.data,
      isLoading: query.isLoading,
      isUnauthenticated: isUnauthorized(query.error),
      error: query.error,
      can: (capability) => granted.has(capability),
      canAny: (...capabilities) => capabilities.some((c) => granted.has(c)),
    };
  }, [query.data, query.isLoading, query.error]);

  return <CapabilityContext.Provider value={value}>{children}</CapabilityContext.Provider>;
}
