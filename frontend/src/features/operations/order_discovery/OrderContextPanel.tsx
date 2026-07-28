import { useState } from "react";
import { CheckCircle2, Loader2, LockKeyhole, Send, ShieldCheck } from "lucide-react";
import type { AssociateConversation } from "../../../contracts/associateReturns";
import { formatBadgeLabel, inputClass, primaryButton } from "../shared";
import { CandidateList } from "./CandidateList";

const reasonCodes = [
  "DAMAGED",
  "WRONG_ITEM",
  "DEFECTIVE",
  "NOT_AS_DESCRIBED",
  "MISSING_PARTS",
] as const;

const shippingPaths = [
  { label: "PPL", value: "PREPAID_PARCEL" },
  { label: "Branch UPS", value: "BRANCH_UPS" },
  { label: "BOL", value: "BRANCH_LTL" },
  { label: "Customer Ship", value: "OFFSITE_PARCEL" },
  { label: "Offsite LTL", value: "OFFSITE_LTL" },
  { label: "Direct Vendor", value: "DIRECT_VENDOR" },
  { label: "Field Scrap", value: "FIELD_SCRAP" },
  { label: "No Physical Return", value: "NO_PHYSICAL_RETURN" },
] as const;

type ShippingPath = (typeof shippingPaths)[number]["value"];

export function parseApiUtcTimestamp(value: string): number {
  const includesTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  return new Date(includesTimezone ? value : `${value}Z`).getTime();
}

export type OrderContextPanelProps = {
  readonly conversation: AssociateConversation | null;
  readonly candidateIndex: number;
  readonly selectedLineId: string;
  readonly onSelectCandidate: (index: number) => void;
  readonly onSelectLine: (lineId: string) => void;
  readonly onSelectClarification: (value: string) => void;
  readonly onConfirmDiscovery: () => void;
  readonly isConfirming: boolean;
  readonly isClarifying: boolean;
  readonly onSubmitDetails?: (payload: {
    reasonCode: (typeof reasonCodes)[number];
    returnQuantity: number;
    packageCount: number;
    shippingPathExpectation: ShippingPath;
    branchReference?: string;
    attachmentIds?: readonly string[];
    notes?: string;
  }) => void;
  readonly isSubmittingDetails?: boolean;
}

export function OrderContextPanel({
  conversation,
  candidateIndex,
  selectedLineId,
  onSelectCandidate,
  onSelectLine,
  onSelectClarification,
  onConfirmDiscovery,
  isConfirming,
  isClarifying,
  onSubmitDetails,
  isSubmittingDetails = false,
}: OrderContextPanelProps) {
  const [reasonCode, setReasonCode] = useState<(typeof reasonCodes)[number]>("DAMAGED");
  const [returnQuantity, setReturnQuantity] = useState(1);
  const [packageCount, setPackageCount] = useState(1);
  const [shippingPath, setShippingPath] = useState<ShippingPath>("PREPAID_PARCEL");
  const [branchReference, setBranchReference] = useState("");
  const [photoEvidenceReference, setPhotoEvidenceReference] = useState("");
  const [notes, setNotes] = useState("");

  const isComplete = conversation?.status === "SUBMITTED";
  const [now] = useState(() => Date.now());
  const candidateSetExpired = Boolean(
    conversation?.candidateSetExpiresAt
      && parseApiUtcTimestamp(conversation.candidateSetExpiresAt) <= now,
  );
  const lockedCandidate = conversation?.candidates.find(
    (candidate) => candidate.orderReference === conversation.discoveryLock?.orderReference,
  );
  const inferredBranchReference = (
    lockedCandidate?.sellWarehouseId
    ?? lockedCandidate?.shipFromWarehouseId
    ?? ""
  );
  const effectiveBranchReference = branchReference.trim() || inferredBranchReference;
  const photoEvidenceRequired = ["DAMAGED", "DEFECTIVE", "WRONG_ITEM"].includes(reasonCode);

  return (
    <aside className="flex min-h-0 flex-1 flex-col overflow-y-auto border-l border-stone-200 bg-stone-100/70 p-4 lg:p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <LockKeyhole size={17} className="text-teal-800" />
          <h2 className="font-semibold text-slate-950">Live Order Context</h2>
        </div>
        {conversation ? (
          <span className="rounded-md bg-stone-200/80 px-2 py-0.5 text-[10px] font-semibold text-slate-700">
            {conversation.status}
          </span>
        ) : null}
      </div>

      {conversation ? (
        <section className="mb-3 rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-[11px] text-slate-600 shadow-xs">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-slate-500">Dialogue state</span>
            <strong className="text-right text-slate-900">
              {formatBadgeLabel(conversation.activeDialogueState)}
            </strong>
          </div>
          {conversation.activeRequestedSlots.length ? (
            <div className="mt-1.5 flex items-center justify-between gap-3">
              <span className="font-medium text-slate-500">Requested detail</span>
              <strong className="text-right text-teal-900">
                {conversation.activeRequestedSlots.map(formatBadgeLabel).join(", ")}
              </strong>
            </div>
          ) : null}
          {conversation.configurationReleaseId ? (
            <div className="mt-1.5 flex items-center justify-between gap-3">
              <span className="font-medium text-slate-500">Configuration release</span>
              <span className="max-w-[170px] truncate font-mono text-slate-700" title={conversation.configurationReleaseId}>
                {conversation.configurationReleaseId}
              </span>
            </div>
          ) : null}
        </section>
      ) : null}

      {!conversation?.candidates.length && !conversation?.discoveryLock ? (
        <section className="rounded-2xl border border-dashed border-stone-300 bg-white/70 p-5 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-stone-100 text-stone-400">
            <ShieldCheck size={20} />
          </div>
          <p className="mt-3 text-sm font-semibold text-slate-800">Context appears here</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            Matching orders, item confirmation, and verified cryptographic locks stay beside your conversation without interrupting it.
          </p>
        </section>
      ) : null}

      {conversation?.candidates.length
        && !conversation.discoveryLock
        && conversation.clarificationPrompt ? (
          <section className="rounded-2xl border border-teal-200 bg-white p-4 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal-700">
              One detail needed
            </p>
            <h3 className="mt-2 text-base font-semibold leading-6 text-slate-950">
              {conversation.clarificationPrompt.question}
            </h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              Choose a verified value. The assistant will use only this field to narrow the
              candidate set, then ask for the next useful detail if needed.
            </p>
            <div className="mt-4 grid gap-2">
              {conversation.clarificationPrompt.options.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className="flex items-center justify-between gap-3 rounded-xl border border-stone-200 bg-stone-50 px-3 py-3 text-left text-sm font-medium text-slate-800 transition hover:border-teal-600 hover:bg-teal-50 disabled:cursor-wait disabled:opacity-50"
                  disabled={isClarifying}
                  onClick={() => { onSelectClarification(option.value); }}
                >
                  <span>{option.label}</span>
                  <span className="rounded-full bg-white px-2 py-0.5 text-[10px] text-slate-500 ring-1 ring-stone-200">
                    {option.candidateCount} {option.candidateCount === 1 ? "match" : "matches"}
                  </span>
                </button>
              ))}
            </div>
            {isClarifying ? (
              <p className="mt-3 flex items-center justify-center gap-2 text-xs font-medium text-teal-800">
                <Loader2 className="animate-spin" size={14} />
                Narrowing verified candidates...
              </p>
            ) : null}
          </section>
        ) : null}

      {conversation?.candidates.length
        && !conversation.discoveryLock
        && !conversation.clarificationPrompt ? (
        <section className="flex flex-col gap-3">
          {candidateSetExpired ? (
            <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-950">
              These candidates expired. Send the details again to refresh verified evidence.
            </div>
          ) : null}
          <p className="text-xs leading-5 text-slate-500">
            The agent ranked these candidates. Select the exact order line below to verify and lock evidence.
          </p>
          <div className="sticky top-0 z-20 -mx-1 rounded-xl border border-teal-200 bg-white/95 p-2 shadow-sm backdrop-blur">
            <button
              type="button"
              className={`${primaryButton} w-full justify-center`}
              disabled={!selectedLineId || isConfirming || candidateSetExpired}
              onClick={onConfirmDiscovery}
            >
              {isConfirming ? <Loader2 className="mr-1.5 animate-spin" size={16} /> : <LockKeyhole className="mr-1.5" size={16} />}
              {isConfirming ? "Locking order evidence..." : "Confirm selected item and continue"}
            </button>
            <p className="mt-1.5 text-center text-[11px] text-slate-500">
              Selecting an item does not submit it until you confirm here.
            </p>
          </div>
          <CandidateList
            candidates={conversation.candidates}
            selectedIndex={candidateIndex}
            selectedLineId={selectedLineId}
            onSelectCandidate={onSelectCandidate}
            onSelectLine={onSelectLine}
            isLoading={isConfirming}
          />
        </section>
        ) : null}

      {conversation?.discoveryLock && !isComplete ? (
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-xs">
          <div className="flex items-center justify-between border-b border-stone-100 pb-3">
            <p className="flex items-center gap-1.5 text-sm font-semibold text-teal-950">
              <CheckCircle2 size={17} className="text-teal-700" />
              Order evidence locked
            </p>
            <span className="rounded bg-teal-50 px-1.5 py-0.5 text-[10px] font-mono text-teal-800 border border-teal-200">
              SHA-256
            </span>
          </div>
          <div className="mt-3 space-y-1 text-xs text-slate-600 bg-stone-50 p-2.5 rounded-lg border border-stone-150">
            <div className="flex justify-between">
              <span className="text-slate-400 font-medium">Order:</span>
              <strong className="text-slate-900">{conversation.discoveryLock.orderReference}</strong>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400 font-medium">Line ID:</span>
              <strong className="text-slate-900">{conversation.discoveryLock.orderLineId}</strong>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400 font-medium">Product:</span>
              <strong className="text-slate-900">{conversation.discoveryLock.productId}</strong>
            </div>
            <div className="flex justify-between border-t border-stone-200 pt-1 mt-1 text-[10px]">
              <span className="text-slate-400">Digest:</span>
              <span className="font-mono text-slate-700 truncate max-w-[150px]" title={conversation.discoveryLock.lockDigest}>
                {conversation.discoveryLock.lockDigest}
              </span>
            </div>
          </div>

          <form
            className="mt-5 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              onSubmitDetails?.({
                reasonCode,
                returnQuantity,
                packageCount,
                shippingPathExpectation: shippingPath,
                branchReference: effectiveBranchReference || undefined,
                attachmentIds: photoEvidenceReference.trim()
                  ? [photoEvidenceReference.trim()]
                  : [],
                notes: notes || undefined,
              });
            }}
          >
            <fieldset>
              <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">Reason</legend>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {reasonCodes.map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => { setReasonCode(value); }}
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium transition ${
                      reasonCode === value
                        ? "border-teal-800 bg-teal-50 text-teal-950 shadow-2xs"
                        : "border-stone-200 bg-white hover:bg-stone-50 text-slate-700"
                    }`}
                  >
                    {formatBadgeLabel(value)}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">Expected route</legend>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {shippingPaths.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => { setShippingPath(option.value); }}
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium transition ${
                      shippingPath === option.value
                        ? "border-teal-800 bg-teal-50 text-teal-950 shadow-2xs"
                        : "border-stone-200 bg-white hover:bg-stone-50 text-slate-700"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </fieldset>
            <label className="block text-xs font-medium text-slate-600">
              Processing branch
              <input
                type="text"
                className={inputClass}
                value={branchReference || inferredBranchReference}
                onChange={(event) => { setBranchReference(event.target.value); }}
                placeholder="Required when the product is at a branch"
              />
            </label>
            {photoEvidenceRequired ? (
              <label className="block text-xs font-medium text-slate-600">
                Photo evidence reference
                <input
                  type="text"
                  required
                  className={inputClass}
                  value={photoEvidenceReference}
                  onChange={(event) => { setPhotoEvidenceReference(event.target.value); }}
                  placeholder="Attachment or evidence ID"
                />
                <span className="mt-1 block text-[11px] text-amber-700">
                  Photo evidence is required by policy for {formatBadgeLabel(reasonCode).toLowerCase()} returns.
                </span>
              </label>
            ) : null}
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs font-medium text-slate-600">
                Quantity
                <input
                  type="number"
                  min={1}
                  className={inputClass}
                  value={returnQuantity}
                  onChange={(event) => { setReturnQuantity(Number(event.target.value)); }}
                />
              </label>
              <label className="text-xs font-medium text-slate-600">
                Packages
                <input
                  type="number"
                  min={1}
                  className={inputClass}
                  value={packageCount}
                  onChange={(event) => { setPackageCount(Number(event.target.value)); }}
                />
              </label>
            </div>
            <label className="block text-xs font-medium text-slate-600">
              Notes
              <textarea
                className={inputClass}
                rows={2}
                value={notes}
                onChange={(event) => { setNotes(event.target.value); }}
                placeholder="Optional associate notes..."
              />
            </label>
            <button
              className={`${primaryButton} w-full justify-center`}
              disabled={
                isSubmittingDetails
                || !effectiveBranchReference
                || (photoEvidenceRequired && !photoEvidenceReference.trim())
              }
              type="submit"
            >
              {isSubmittingDetails ? <Loader2 className="mr-1.5 animate-spin" size={16} /> : <Send className="mr-1.5" size={16} />}
              {isSubmittingDetails ? "Submitting to workflow..." : "Send to workflow"}
            </button>
          </form>
        </section>
      ) : null}
    </aside>
  );
}
