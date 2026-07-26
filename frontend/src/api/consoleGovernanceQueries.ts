import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./client";
import type { AuditLog, ConsoleSettings, GovernanceSummary, HardeningSummary } from "../contracts/consoleGovernance";

export function useAuditLogs() {
  return useQuery({
    queryKey: ["console", "audit"],
    queryFn: ({ signal }) => apiClient<AuditLog[]>("/data-console/v1/audit", { signal }),
    select: (response) => response.data ?? [],
    staleTime: 10_000,
  });
}

export function useGovernanceSummary() {
  return useQuery({
    queryKey: ["console", "governance"],
    queryFn: ({ signal }) => apiClient<GovernanceSummary>("/data-console/v1/governance", { signal }),
    select: (response) => response.data,
    staleTime: 30_000,
  });
}

export function useConsoleSettings() {
  return useQuery({
    queryKey: ["console", "settings"],
    queryFn: ({ signal }) => apiClient<ConsoleSettings>("/data-console/v1/settings", { signal }),
    select: (response) => response.data,
    staleTime: 30_000,
  });
}

export function useHardeningSummary() {
  return useQuery({
    queryKey: ["console", "hardening"],
    queryFn: ({ signal }) => apiClient<HardeningSummary>("/data-console/v1/hardening", { signal }),
    select: (response) => response.data,
    staleTime: 15_000,
  });
}
