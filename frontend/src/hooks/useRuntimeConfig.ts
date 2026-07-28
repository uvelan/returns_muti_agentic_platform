import { createContext, useContext } from "react";
import type { RuntimeConfig } from "../api/runtimeConfig";

export const RuntimeConfigContext = createContext<RuntimeConfig | null>(null);

export function useRuntimeConfig() {
  const context = useContext(RuntimeConfigContext);
  if (!context) {
    throw new Error("useRuntimeConfig must be used within a RuntimeConfigProvider");
  }
  return context;
}
