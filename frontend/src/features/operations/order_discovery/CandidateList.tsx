import { Loader2, SearchX } from "lucide-react";
import type { OrderCandidate } from "../../../contracts/associateReturns";
import { CandidateCard } from "./CandidateCard";

export type CandidateListProps = {
  readonly candidates: readonly OrderCandidate[];
  readonly selectedIndex: number;
  readonly selectedLineId: string;
  readonly onSelectCandidate: (index: number) => void;
  readonly onSelectLine: (lineId: string) => void;
  readonly isLoading?: boolean;
}

export function CandidateList({
  candidates,
  selectedIndex,
  selectedLineId,
  onSelectCandidate,
  onSelectLine,
  isLoading = false,
}: CandidateListProps) {
  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-stone-200 bg-white p-8 text-center shadow-xs">
        <Loader2 className="animate-spin text-teal-700" size={28} />
        <p className="mt-3 text-sm font-medium text-slate-700">Searching configured sources for candidates...</p>
        <p className="mt-1 text-xs text-slate-500">Applying exact identifiers first, then controlled full-text retrieval.</p>
      </div>
    );
  }

  if (candidates.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-stone-300 bg-white/70 p-6 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-stone-100 text-stone-500">
          <SearchX size={20} />
        </div>
        <p className="mt-3 text-sm font-semibold text-slate-800">No matching orders found yet</p>
        <p className="mt-1 max-w-xs text-xs leading-5 text-slate-500">
          Provide an exact identifier or a customer or product description. The server applies the published exact and full-text policies.
        </p>
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-3">
      {candidates.map((candidate, index) => (
        <CandidateCard
          key={`${candidate.orderReference}-${String(index)}`}
          candidate={candidate}
          index={index}
          isSelected={selectedIndex === index}
          selectedLineId={selectedLineId}
          onSelectCandidate={onSelectCandidate}
          onSelectLine={onSelectLine}
        />
      ))}
    </div>
  );
}
