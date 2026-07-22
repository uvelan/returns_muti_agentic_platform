import { useQuery } from "@tanstack/react-query";
import { getFullGraphEvidence, getLatestGraphEvidence, listGraphEvidence } from "./graphEvidence";

export const graphEvidenceKeys = {
  all: ["graph-evidence"] as const,
  latest: () => [...graphEvidenceKeys.all, "latest"] as const,
  list: (cursor?: string) => [...graphEvidenceKeys.all, "list", cursor ?? "first"] as const,
  full: (documentId: string) => [...graphEvidenceKeys.all, "full", documentId] as const,
};
const queryPolicy = { retry: false, refetchOnWindowFocus: false } as const;

export function useLatestGraphEvidence() {
  return useQuery({ queryKey: graphEvidenceKeys.latest(), queryFn: ({ signal }) => getLatestGraphEvidence(signal), ...queryPolicy });
}
export function useGraphEvidenceList(cursor?: string) {
  return useQuery({ queryKey: graphEvidenceKeys.list(cursor), queryFn: ({ signal }) => listGraphEvidence(cursor, signal), ...queryPolicy });
}
export function useFullGraphEvidence(documentId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: graphEvidenceKeys.full(documentId ?? "none"),
    queryFn: ({ signal }) => getFullGraphEvidence(documentId ?? "", signal),
    enabled: enabled && documentId !== null,
    ...queryPolicy,
  });
}
