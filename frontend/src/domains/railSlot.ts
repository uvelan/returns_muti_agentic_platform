import { createContext } from "react";

/**
 * The DOM node the domain rail lends to the current screen, or `null` when
 * there is none -- a collapsed rail, the launcher, or a screen rendered outside
 * the shell in a test.
 *
 * Split from `DomainRail.tsx` for the reason `capabilityContext.ts` is split
 * from its provider: a `.tsx` module that exports both a component and a
 * non-component value breaks React Fast Refresh, and the lint rule that
 * enforces this is on.
 */
export const RailSlotContext = createContext<HTMLElement | null>(null);

export const RailSlotProvider = RailSlotContext.Provider;
