import { createContext, useContext } from "react";

import type { Capability, PrincipalView } from "../api/principal";

/**
 * Context, types and the hook for the RBAC capability layer (Phase 17).
 *
 * Split from `CapabilityProvider.tsx` because a `.tsx` module that exports both
 * a component and non-component values breaks React Fast Refresh -- the lint
 * rule that enforces this is on, and the split is the fix rather than an
 * override.
 *
 * **This hides; it does not protect.** Backend authorization is authoritative
 * and re-checks every capability on the route that performs the action. Asking
 * here only avoids *offering* an action that would be refused.
 */
export type CapabilityState = {
  readonly principal: PrincipalView | undefined;
  readonly isLoading: boolean;
  /** Distinguishes "not signed in" (401) from "signed in with nothing granted". */
  readonly isUnauthenticated: boolean;
  readonly error: Error | null;
  readonly can: (capability: Capability) => boolean;
  readonly canAny: (...capabilities: readonly Capability[]) => boolean;
};

export const CapabilityContext = createContext<CapabilityState | undefined>(undefined);

export function useCapabilities(): CapabilityState {
  const context = useContext(CapabilityContext);
  if (context === undefined) {
    // Throwing beats returning a permissive default: a screen rendered outside
    // the provider would otherwise silently grant nothing and look like a
    // permissions bug rather than the wiring bug it is.
    throw new Error("useCapabilities must be used within a CapabilityProvider.");
  }
  return context;
}

export function isUnauthorized(error: unknown): boolean {
  return (
    typeof error === "object"
    && error !== null
    && "status" in error
    && error.status === 401
  );
}
