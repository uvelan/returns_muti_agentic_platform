import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  Package,
  Plus,
  RefreshCw,
  Ticket,
  Truck,
} from "lucide-react";

import {
  rmaTicketsApi,
  type CreateRmaTicketRequest,
  type RecordTrackingRequest,
  type RmaTicketItem,
  type RmaTicketView,
  type SourceShipmentEcho,
  type TicketStatus,
  type TrackingType,
} from "../../api/rmaTickets";

/**
 * Issue the RMA the workflow agent asked for, and keep its tracking current.
 *
 * The create form is deliberately one screen and pre-shaped: every field here
 * is something the Return Workflow Agent already established
 * (`ReturnWorkflowAssessmentRequest` plus the assessment it returns), so an
 * associate arriving from a conversation is confirming facts rather than
 * re-entering them. Paste the agent's payload and the form fills itself.
 *
 * Tracking writes `dbo.return_tracking` first -- that row is authoritative --
 * and then annotates the source shipment document. The result of that second
 * write is shown rather than assumed, because it is the one place this platform
 * writes into a source system and an associate should be able to see whether it
 * landed.
 */

const TRACKING_TYPES: readonly TrackingType[] = [
  "CUSTOMER_SHIP",
  "PPL",
  "BOL",
  "NO_LABEL",
  "DIRECT_VENDOR",
  "FIELD_SCRAP",
];

const STATUS_TONE: Record<TicketStatus, string> = {
  DRAFT: "bg-surface-container text-on-surface-variant",
  SUBMITTED: "bg-sky-100 text-sky-900",
  CLARIFICATION_REQUIRED: "bg-amber-100 text-amber-900",
  RETURN_CREATED: "bg-emerald-100 text-emerald-900",
  REJECTED: "bg-red-100 text-red-900",
  CANCELLED: "bg-surface-container text-on-surface-variant",
  FAILED: "bg-red-100 text-red-900",
};

const EMPTY_ITEM: RmaTicketItem = {
  orderLineId: "",
  productId: "",
  requestedQuantity: 1,
  reasonCode: "",
};

function blankDraft(): CreateRmaTicketRequest {
  return {
    sessionId: "",
    orderReference: "",
    customerReference: "",
    correlationId: "",
    recommendedReturnMethod: "",
    productPresence: "",
    branchId: "",
    associateId: "",
    photoEvidenceRequired: false,
    supportDraft: "",
    missingFields: [],
    items: [EMPTY_ITEM],
    idempotencyKey: crypto.randomUUID(),
  };
}

export function RmaTicketsPage() {
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<CreateRmaTicketRequest>(blankDraft);
  const [creating, setCreating] = useState(false);
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [echo, setEcho] = useState<SourceShipmentEcho | null>(null);

  const queue = useQuery({
    queryKey: ["rma-tickets"],
    queryFn: () => rmaTicketsApi.list(),
  });

  const detail = useQuery({
    queryKey: ["rma-tickets", selected],
    queryFn: () => rmaTicketsApi.get(selected ?? ""),
    enabled: selected !== null,
  });

  const invalidate = async () => {
    await client.invalidateQueries({ queryKey: ["rma-tickets"] });
  };

  const create = useMutation({
    mutationFn: (input: CreateRmaTicketRequest) => rmaTicketsApi.create(input),
    onSuccess: async (result) => {
      setSelected(result.ticket.sessionId);
      setCreating(false);
      setDraft(blankDraft());
      await invalidate();
    },
  });

  const tracking = useMutation({
    mutationFn: ({ sessionId, input }: { sessionId: string; input: RecordTrackingRequest }) =>
      rmaTicketsApi.recordTracking(sessionId, input),
    onSuccess: async (result) => {
      setEcho(result.sourceShipment);
      await invalidate();
    },
  });

  const status = useMutation({
    mutationFn: ({ sessionId, next }: { sessionId: string; next: TicketStatus }) =>
      rmaTicketsApi.setStatus(sessionId, next),
    onSuccess: invalidate,
  });

  /** Fill the form from the payload the workflow agent posted. */
  const applyAgentPayload = (raw: string) => {
    setPasteError(null);
    try {
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed !== "object" || parsed === null) throw new Error("not an object");
      const payload = parsed as Record<string, unknown>;
      const items = Array.isArray(payload.items) ? (payload.items as RmaTicketItem[]) : [];
      setDraft((current) => ({
        ...current,
        sessionId: text(payload.sessionId, current.sessionId),
        orderReference: text(payload.orderReference, current.orderReference),
        customerReference: text(payload.customerReference, current.customerReference ?? ""),
        correlationId: text(payload.correlationId, current.correlationId ?? ""),
        recommendedReturnMethod: text(
          payload.recommendedReturnMethod,
          current.recommendedReturnMethod,
        ),
        productPresence: text(payload.productPresence, current.productPresence ?? ""),
        branchId: text(payload.branchId, current.branchId ?? ""),
        associateId: text(payload.associateId, current.associateId),
        photoEvidenceRequired: payload.photoEvidenceRequired === true,
        supportDraft: text(payload.supportDraft, current.supportDraft),
        missingFields: Array.isArray(payload.missingFields)
          ? payload.missingFields.filter((item): item is string => typeof item === "string")
          : [],
        items: items.length > 0 ? items : current.items,
      }));
    } catch {
      setPasteError(
        "That is not a workflow agent payload. Paste the JSON the agent posted, or fill the fields below.",
      );
    }
  };

  const ticket = detail.data ?? null;
  const canSubmit =
    draft.sessionId.trim().length > 0 &&
    draft.orderReference.trim().length > 0 &&
    draft.associateId.trim().length > 0 &&
    draft.supportDraft.trim().length > 0 &&
    draft.recommendedReturnMethod.trim().length > 0 &&
    draft.items.every((item) => item.orderLineId && item.productId && item.reasonCode);

  return (
    <section className="space-y-5" aria-label="RMA tickets">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-on-surface">RMA tickets</h2>
          <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
            Turn the workflow agent&apos;s assessment into an RMA the platform owns. The ticket,
            the return record, its items and its tracking are written to the platform tables; the
            source shipment document is annotated afterwards and reports what it did.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              void queue.refetch();
            }}
            className="inline-flex items-center gap-2 rounded-lg border border-outline-variant px-3 py-2 text-sm text-on-surface"
          >
            <RefreshCw size={15} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => {
              setCreating((value) => !value);
            }}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary"
          >
            <Plus size={15} />
            {creating ? "Close" : "New RMA ticket"}
          </button>
        </div>
      </header>

      {creating ? (
        <CreateForm
          draft={draft}
          onDraft={setDraft}
          onPaste={applyAgentPayload}
          pasteError={pasteError}
          submitting={create.isPending}
          error={create.error?.message ?? null}
          canSubmit={canSubmit}
          onSubmit={() => {
            create.mutate(draft);
          }}
        />
      ) : null}

      {create.data?.outcome === "DUPLICATE" ? (
        <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          A ticket already existed for that session, so none was created. The existing one is shown.
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        <Queue
          tickets={queue.data ?? []}
          loading={queue.isLoading}
          error={queue.error?.message ?? null}
          selected={selected}
          onSelect={(sessionId) => {
            setSelected(sessionId);
            setEcho(null);
          }}
        />
        <Detail
          ticket={ticket}
          loading={selected !== null && detail.isLoading}
          error={detail.error?.message ?? null}
          echo={echo}
          savingTracking={tracking.isPending}
          trackingError={tracking.error?.message ?? null}
          onTracking={(input) => {
            if (ticket !== null) tracking.mutate({ sessionId: ticket.sessionId, input });
          }}
          onStatus={(next) => {
            if (ticket !== null) status.mutate({ sessionId: ticket.sessionId, next });
          }}
          statusPending={status.isPending}
        />
      </div>
    </section>
  );
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function Queue({
  tickets,
  loading,
  error,
  selected,
  onSelect,
}: {
  readonly tickets: readonly RmaTicketView[];
  readonly loading: boolean;
  readonly error: string | null;
  readonly selected: string | null;
  readonly onSelect: (sessionId: string) => void;
}) {
  return (
    <aside className="rounded-2xl border border-outline-variant bg-surface-container-lowest p-4">
      <div className="mb-3 flex items-center gap-2">
        <ClipboardList size={16} className="text-primary" />
        <h3 className="font-semibold text-on-surface">Ticket queue</h3>
        <span className="text-xs text-on-surface-variant">{tickets.length}</span>
      </div>
      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((id) => (
            <div key={id} className="h-16 animate-pulse rounded-lg bg-surface-container" />
          ))}
        </div>
      ) : error !== null ? (
        <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          The ticket queue could not be loaded: {error}
        </p>
      ) : tickets.length === 0 ? (
        <div className="rounded-lg border border-dashed border-outline-variant p-6 text-center">
          <Ticket className="mx-auto text-outline" size={22} />
          <p className="mt-2 text-sm font-medium text-on-surface">No RMA tickets yet</p>
          <p className="mt-1 text-xs text-on-surface-variant">
            Create one from a workflow agent assessment.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {tickets.map((item) => (
            <li key={item.ticketId}>
              <button
                type="button"
                onClick={() => {
                  onSelect(item.sessionId);
                }}
                className={`w-full rounded-lg border p-3 text-left transition ${
                  selected === item.sessionId
                    ? "border-primary bg-primary/5"
                    : "border-outline-variant hover:bg-surface-container"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-on-surface">
                    {item.returnReference ?? item.ticketId}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${STATUS_TONE[item.status]}`}>
                    {item.status.replaceAll("_", " ")}
                  </span>
                </div>
                <p className="mt-1 truncate text-xs text-on-surface-variant">
                  Order {item.orderReference ?? "—"} · session {item.sessionId}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function Detail({
  ticket,
  loading,
  error,
  echo,
  savingTracking,
  trackingError,
  onTracking,
  onStatus,
  statusPending,
}: {
  readonly ticket: RmaTicketView | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly echo: SourceShipmentEcho | null;
  readonly savingTracking: boolean;
  readonly trackingError: string | null;
  readonly onTracking: (input: RecordTrackingRequest) => void;
  readonly onStatus: (next: TicketStatus) => void;
  readonly statusPending: boolean;
}) {
  if (loading) return <div className="h-96 animate-pulse rounded-2xl bg-surface-container" />;
  if (error !== null) {
    return (
      <p role="alert" className="rounded-2xl border border-red-300 bg-red-50 p-4 text-sm text-red-900">
        This ticket could not be loaded: {error}
      </p>
    );
  }
  if (ticket === null) {
    return (
      <div className="grid min-h-96 place-items-center rounded-2xl border border-dashed border-outline-variant text-center">
        <div>
          <Package className="mx-auto text-outline" size={26} />
          <p className="mt-2 font-medium text-on-surface">Select a ticket</p>
          <p className="mt-1 text-sm text-on-surface-variant">
            Its items, tracking and status appear here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-outline-variant bg-surface-container-lowest p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold text-on-surface">{ticket.returnReference ?? "RMA pending"}</h3>
            <p className="mt-1 text-xs text-on-surface-variant">
              Ticket {ticket.ticketId} · order {ticket.orderReference ?? "—"} · customer{" "}
              {ticket.customerReference ?? "—"}
            </p>
          </div>
          <span className={`rounded-full px-2.5 py-1 text-xs ${STATUS_TONE[ticket.status]}`}>
            {ticket.status.replaceAll("_", " ")}
          </span>
        </div>

        {ticket.missingFields.length > 0 ? (
          <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            The agent could not establish: {ticket.missingFields.join(", ")}
          </p>
        ) : null}

        {ticket.supportDraft ? (
          <p className="mt-3 whitespace-pre-wrap rounded-lg bg-surface-container p-3 text-sm text-on-surface">
            {ticket.supportDraft}
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap gap-2">
          {(["RETURN_CREATED", "CLARIFICATION_REQUIRED", "REJECTED", "CANCELLED"] as const).map(
            (next) => (
              <button
                key={next}
                type="button"
                disabled={statusPending || ticket.status === next}
                onClick={() => {
                  onStatus(next);
                }}
                className="rounded-lg border border-outline-variant px-3 py-1.5 text-xs text-on-surface disabled:opacity-40"
              >
                {next.replaceAll("_", " ")}
              </button>
            ),
          )}
        </div>
      </section>

      <section className="rounded-2xl border border-outline-variant bg-surface-container-lowest p-4">
        <h3 className="mb-3 font-semibold text-on-surface">Items</h3>
        {ticket.items.length === 0 ? (
          <p className="text-sm text-on-surface-variant">No item rows are recorded for this ticket.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-on-surface-variant">
                <tr>
                  <th className="pb-2">Order line</th>
                  <th className="pb-2">Product</th>
                  <th className="pb-2">Qty</th>
                  <th className="pb-2">Reason</th>
                  <th className="pb-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {ticket.items.map((item) => (
                  <tr key={item.returnItemId} className="border-t border-outline-variant">
                    <td className="py-2 font-mono text-xs">{item.orderLineId}</td>
                    <td className="py-2 font-mono text-xs">{item.productId}</td>
                    <td className="py-2">{item.quantity}</td>
                    <td className="py-2">{item.reasonCode}</td>
                    <td className="py-2 text-on-surface-variant">{item.itemStatus}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <TrackingSection
        ticket={ticket}
        saving={savingTracking}
        error={trackingError}
        echo={echo}
        onSubmit={onTracking}
      />
    </div>
  );
}

function TrackingSection({
  ticket,
  saving,
  error,
  echo,
  onSubmit,
}: {
  readonly ticket: RmaTicketView;
  readonly saving: boolean;
  readonly error: string | null;
  readonly echo: SourceShipmentEcho | null;
  readonly onSubmit: (input: RecordTrackingRequest) => void;
}) {
  const [form, setForm] = useState<RecordTrackingRequest>({
    trackingReference: "",
    trackingType: "CUSTOMER_SHIP",
    carrierCode: "",
    trackingStatus: "IN_TRANSIT",
    shipmentDetails: "",
  });

  return (
    <section className="rounded-2xl border border-outline-variant bg-surface-container-lowest p-4">
      <div className="mb-3 flex items-center gap-2">
        <Truck size={16} className="text-primary" />
        <h3 className="font-semibold text-on-surface">Return tracking</h3>
      </div>

      {ticket.tracking.length > 0 ? (
        <div className="mb-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-on-surface-variant">
              <tr>
                <th className="pb-2">Reference</th>
                <th className="pb-2">Type</th>
                <th className="pb-2">Carrier</th>
                <th className="pb-2">Status</th>
                <th className="pb-2">Event at</th>
              </tr>
            </thead>
            <tbody>
              {ticket.tracking.map((row) => (
                <tr key={row.trackingId} className="border-t border-outline-variant">
                  <td className="py-2 font-mono text-xs">{row.trackingReference}</td>
                  <td className="py-2">{row.trackingType}</td>
                  <td className="py-2">{row.carrierCode ?? "—"}</td>
                  <td className="py-2">{row.trackingStatus}</td>
                  <td className="py-2 text-on-surface-variant">
                    {row.eventAt === null ? "—" : new Date(row.eventAt).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mb-4 text-sm text-on-surface-variant">
          No tracking recorded yet for this RMA.
        </p>
      )}

      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!saving && form.trackingReference.trim().length > 0) {
            onSubmit({ ...form, orderReference: ticket.orderReference });
          }
        }}
      >
        <Field
          label="Tracking reference"
          value={form.trackingReference}
          onChange={(trackingReference) => {
            setForm((current) => ({ ...current, trackingReference }));
          }}
        />
        <label className="block text-xs font-medium text-on-surface-variant">
          Tracking type
          <select
            value={form.trackingType}
            onChange={(event) => {
              setForm((current) => ({
                ...current,
                trackingType: event.target.value as TrackingType,
              }));
            }}
            className="mt-1.5 w-full rounded-lg border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface"
          >
            {TRACKING_TYPES.map((option) => (
              <option key={option} value={option}>
                {option.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <Field
          label="Carrier code"
          required={false}
          value={form.carrierCode ?? ""}
          onChange={(carrierCode) => {
            setForm((current) => ({ ...current, carrierCode }));
          }}
        />
        <Field
          label="Tracking status"
          value={form.trackingStatus}
          onChange={(trackingStatus) => {
            setForm((current) => ({ ...current, trackingStatus }));
          }}
        />
        <label className="block text-xs font-medium text-on-surface-variant sm:col-span-2">
          Shipment details
          <textarea
            value={form.shipmentDetails ?? ""}
            onChange={(event) => {
              setForm((current) => ({ ...current, shipmentDetails: event.target.value }));
            }}
            rows={2}
            className="mt-1.5 w-full rounded-lg border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface"
          />
        </label>
        <div className="sm:col-span-2 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-on-surface-variant">
            Written to <code className="font-mono">dbo.return_tracking</code> first, then annotated
            onto the source shipment document.
          </p>
          <button
            type="submit"
            disabled={saving || form.trackingReference.trim().length === 0}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save tracking"}
          </button>
        </div>
      </form>

      {error !== null ? (
        <p role="alert" className="mt-3 text-sm text-red-700">
          Tracking could not be saved: {error}
        </p>
      ) : null}

      {echo !== null ? <SourceEcho echo={echo} /> : null}
    </section>
  );
}

/** What the source-shipment write did. Never inferred from the SQL outcome. */
function SourceEcho({ echo }: { readonly echo: SourceShipmentEcho }) {
  const failed = echo.outcome === "FAILED";
  const skipped = echo.outcome === "SKIPPED";
  return (
    <div
      role="status"
      className={`mt-3 flex items-start gap-2 rounded-lg border p-3 text-sm ${
        failed
          ? "border-red-300 bg-red-50 text-red-900"
          : skipped
            ? "border-amber-300 bg-amber-50 text-amber-900"
            : "border-emerald-300 bg-emerald-50 text-emerald-900"
      }`}
    >
      {failed ? (
        <AlertTriangle size={15} className="mt-0.5 shrink-0" />
      ) : (
        <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
      )}
      <span>
        <span className="font-medium">Source shipment: {echo.outcome.toLowerCase()}</span>
        {echo.matchedDocument === null ? null : (
          <span className="ml-1 font-mono text-xs">({echo.matchedDocument})</span>
        )}
        {echo.detail === null ? null : <span className="block">{echo.detail}</span>}
      </span>
    </div>
  );
}

function CreateForm({
  draft,
  onDraft,
  onPaste,
  pasteError,
  submitting,
  error,
  canSubmit,
  onSubmit,
}: {
  readonly draft: CreateRmaTicketRequest;
  readonly onDraft: (next: CreateRmaTicketRequest) => void;
  readonly onPaste: (raw: string) => void;
  readonly pasteError: string | null;
  readonly submitting: boolean;
  readonly error: string | null;
  readonly canSubmit: boolean;
  readonly onSubmit: () => void;
}) {
  const [raw, setRaw] = useState("");

  const setItem = (index: number, patch: Partial<RmaTicketItem>) => {
    onDraft({
      ...draft,
      items: draft.items.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    });
  };

  return (
    <form
      className="rounded-2xl border border-outline-variant bg-surface-container-lowest p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSubmit && !submitting) onSubmit();
      }}
    >
      <h3 className="font-semibold text-on-surface">New RMA ticket</h3>
      <p className="mt-1 text-sm text-on-surface-variant">
        Paste what the workflow agent posted and the form fills itself. Every field below is one
        the agent already established.
      </p>

      <label className="mt-3 block text-xs font-medium text-on-surface-variant">
        Workflow agent payload
        <textarea
          value={raw}
          onChange={(event) => {
            setRaw(event.target.value);
          }}
          onBlur={(event) => {
            // Read from the event, not from `raw`. The handler closes over the
            // value from its own render, so a change and a blur in the same tick
            // -- a paste followed immediately by a click elsewhere -- passed the
            // previous value, which on the first paste is the empty string.
            const pasted = event.target.value;
            if (pasted.trim().length > 0) onPaste(pasted);
          }}
          rows={3}
          placeholder='{"sessionId":"…","orderReference":"…","items":[…],"supportDraft":"…"}'
          className="mt-1.5 w-full rounded-lg border border-outline-variant bg-surface px-3 py-2 font-mono text-xs text-on-surface"
        />
      </label>
      {pasteError !== null ? (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {pasteError}
        </p>
      ) : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field
          label="Session id"
          value={draft.sessionId}
          onChange={(sessionId) => {
            onDraft({ ...draft, sessionId });
          }}
        />
        <Field
          label="Order reference"
          value={draft.orderReference}
          onChange={(orderReference) => {
            onDraft({ ...draft, orderReference });
          }}
        />
        <Field
          label="Customer reference"
          required={false}
          value={draft.customerReference ?? ""}
          onChange={(customerReference) => {
            onDraft({ ...draft, customerReference });
          }}
        />
        <Field
          label="Associate id"
          value={draft.associateId}
          onChange={(associateId) => {
            onDraft({ ...draft, associateId });
          }}
        />
        <Field
          label="Recommended return method"
          value={draft.recommendedReturnMethod}
          onChange={(recommendedReturnMethod) => {
            onDraft({ ...draft, recommendedReturnMethod });
          }}
        />
        <Field
          label="Branch id"
          required={false}
          value={draft.branchId ?? ""}
          onChange={(branchId) => {
            onDraft({ ...draft, branchId });
          }}
        />
      </div>

      <label className="mt-3 block text-xs font-medium text-on-surface-variant">
        Support draft (the agent&apos;s own narrative)
        <textarea
          value={draft.supportDraft}
          onChange={(event) => {
            onDraft({ ...draft, supportDraft: event.target.value });
          }}
          rows={3}
          className="mt-1.5 w-full rounded-lg border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface"
        />
      </label>

      <label className="mt-3 flex items-center gap-2 text-sm text-on-surface">
        <input
          type="checkbox"
          checked={draft.photoEvidenceRequired}
          onChange={(event) => {
            onDraft({ ...draft, photoEvidenceRequired: event.target.checked });
          }}
        />
        Photo evidence required
      </label>

      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-sm font-semibold text-on-surface">Items</h4>
          <button
            type="button"
            onClick={() => {
              onDraft({ ...draft, items: [...draft.items, EMPTY_ITEM] });
            }}
            className="text-xs text-primary"
          >
            Add item
          </button>
        </div>
        <div className="space-y-2">
          {draft.items.map((item, index) => (
            <div
              key={index}
              className="grid gap-2 rounded-lg border border-outline-variant p-3 sm:grid-cols-4"
            >
              <Field
                label="Order line"
                value={item.orderLineId}
                onChange={(orderLineId) => {
                  setItem(index, { orderLineId });
                }}
              />
              <Field
                label="Product"
                value={item.productId}
                onChange={(productId) => {
                  setItem(index, { productId });
                }}
              />
              <Field
                label="Quantity"
                type="number"
                value={String(item.requestedQuantity)}
                onChange={(value) => {
                  setItem(index, { requestedQuantity: Math.max(1, Number(value) || 1) });
                }}
              />
              <Field
                label="Reason code"
                value={item.reasonCode}
                onChange={(reasonCode) => {
                  setItem(index, { reasonCode });
                }}
              />
            </div>
          ))}
        </div>
      </div>

      {error !== null ? (
        <p role="alert" className="mt-3 text-sm text-red-700">
          The ticket could not be created: {error}
        </p>
      ) : null}

      <div className="mt-4 flex justify-end">
        <button
          type="submit"
          disabled={!canSubmit || submitting}
          className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary disabled:opacity-40"
        >
          {submitting ? "Creating…" : "Create RMA ticket"}
        </button>
      </div>
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  required = true,
}: {
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly type?: "text" | "number";
  readonly required?: boolean;
}) {
  return (
    <label className="block text-xs font-medium text-on-surface-variant">
      {label}
      <input
        type={type}
        value={value}
        required={required}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        className="mt-1.5 w-full rounded-lg border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface"
      />
    </label>
  );
}
