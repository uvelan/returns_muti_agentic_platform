import { Check, Database, MapPin } from "lucide-react";
import type { OrderCandidate } from "../../../contracts/associateReturns";
import { ToneBadge } from "../shared";

export type CandidateCardProps = {
  readonly candidate: OrderCandidate;
  readonly index: number;
  readonly isSelected: boolean;
  readonly selectedLineId: string;
  readonly onSelectCandidate: (index: number) => void;
  readonly onSelectLine: (lineId: string) => void;
}

export function CandidateCard({
  candidate,
  index,
  isSelected,
  selectedLineId,
  onSelectCandidate,
  onSelectLine,
}: CandidateCardProps) {
  const matchLabel = candidate.retrievalScore != null
    ? "Full-text candidate"
    : "Exact evidence";

  return (
    <div
      className={`min-w-0 overflow-hidden rounded-xl border p-3.5 transition-all ${
        isSelected
          ? "border-teal-700 bg-teal-50/80 shadow-sm ring-1 ring-teal-700/20"
          : "border-stone-200 bg-white hover:border-stone-300 hover:bg-stone-50/50"
      }`}
    >
      <button
        type="button"
        className="flex w-full min-w-0 flex-wrap items-start justify-between gap-2 text-left"
        onClick={() => {
          onSelectCandidate(index);
          const firstLine = candidate.lines.at(0);
          if (firstLine) {
            onSelectLine(firstLine.orderLineId);
          }
        }}
      >
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <strong className="block break-all text-sm font-semibold text-slate-900">
              {candidate.orderReference}
            </strong>
            <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-teal-100/80 px-2 py-0.5 text-[10px] font-semibold text-teal-800">
              <Database size={11} className="text-teal-600" />
              {matchLabel}
            </span>
          </div>
          <span className="mt-0.5 block break-words text-xs text-slate-500">
            {candidate.customerName ?? candidate.customerReference} · {candidate.evidenceSource || "Source not reported"}
          </span>
          {candidate.billingCity || candidate.postalCode || candidate.accountType ? (
            <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
              {candidate.billingCity ? (
                <span className="inline-flex items-center gap-1">
                  <MapPin size={11} />
                  {candidate.billingCity}
                </span>
              ) : null}
              {candidate.postalCode ? <span>Postal {candidate.postalCode}</span> : null}
              {candidate.accountType ? <span>{candidate.accountType}</span> : null}
            </span>
          ) : null}
        </div>
        <span className="shrink-0"><ToneBadge value={candidate.orderStatus ?? "UNKNOWN"} /></span>
      </button>

      {isSelected ? (
        <div className="mt-3 space-y-2 border-t border-teal-100 pt-3">
          <p className="text-[11px] font-medium text-teal-900/80">Select item to lock:</p>
          {candidate.lines.map((line) => {
            const isLineSelected = selectedLineId === line.orderLineId;
            return (
              <button
                key={line.orderLineId}
                type="button"
                onClick={() => { onSelectLine(line.orderLineId); }}
                className={`flex w-full min-w-0 items-start gap-2 rounded-lg border p-2.5 text-left text-xs transition ${
                  isLineSelected
                    ? "border-teal-700 bg-white shadow-xs font-medium text-slate-900"
                    : "border-transparent bg-teal-100/40 text-slate-700 hover:bg-teal-100/70"
                }`}
              >
                <span
                  className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full transition ${
                    isLineSelected
                      ? "bg-teal-700 text-white"
                      : "bg-white text-transparent border border-teal-300"
                  }`}
                >
                  <Check size={10} strokeWidth={3} />
                </span>
                <span className="min-w-0 flex-1">
                  <strong className="block break-all font-semibold text-slate-900">
                    {line.sku ?? line.productId}
                  </strong>
                  <span className="block break-words text-slate-600">
                    {line.productDescription ?? `Line ID: ${line.orderLineId}`}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
