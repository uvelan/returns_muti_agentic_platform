import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import { AlertTriangle, RefreshCw, Search } from "lucide-react";

import {
  shipmentsApi,
  text,
  type CatalogStatus,
  type ShipmentDocument,
  type ShipmentStatusCatalog,
} from "../../api/shipments";

/**
 * The Shipment Status Console: drive a return shipment through its ladder by
 * hand, so Fulfillment can be exercised without a live carrier feed.
 *
 * Operations-class screen: dense, data-first, no chat aesthetic. Every status
 * code, label, transition and colour comes from the served catalog -- a status
 * literal in this file is a defect by the catalog's contract.
 */

/** Colour classes per catalog token. The token names are the contract. */
const TOKEN_CLASSES: Record<string, string> = {
  success: "bg-primary-container/20 text-primary border-primary/40",
  warning: "bg-secondary-container/40 text-on-secondary-container border-secondary/40",
  error: "bg-error-container/30 text-error border-error/40",
  progress: "bg-surface-container-high text-on-surface-variant border-outline-variant",
};

function chipClass(token: string): string {
  return TOKEN_CLASSES[token] ?? TOKEN_CLASSES.progress;
}

function statusOf(catalog: ShipmentStatusCatalog | undefined, mode: string, code: string) {
  return catalog?.statuses.find(
    (status) => status.ladder === mode && status.code === code,
  );
}

function StatusChip({
  catalog,
  mode,
  code,
}: {
  catalog: ShipmentStatusCatalog | undefined;
  mode: string;
  code: string;
}) {
  const entry = statusOf(catalog, mode, code);
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${chipClass(entry?.color_token ?? "progress")}`}
    >
      {entry?.label ?? code}
    </span>
  );
}

/**
 * The full ladder for the shipment's mode: completed rungs solid, the current
 * rung marked, the rest ghosted. Exception side-states fork off the rail
 * rather than sitting inline -- they never advance the ordinal.
 */
function StageRail({
  catalog,
  mode,
  current,
}: {
  catalog: ShipmentStatusCatalog;
  mode: string;
  current: string;
}) {
  const rungs = catalog.statuses
    .filter((status) => status.ladder === mode && !status.exception_state)
    .sort((a, b) => a.ordinal - b.ordinal);
  const forks = catalog.statuses.filter(
    (status) => status.ladder === mode && status.exception_state,
  );
  const currentEntry = statusOf(catalog, mode, current);
  const currentOrdinal = currentEntry?.ordinal ?? -1;
  const onFork = currentEntry?.exception_state === true;
  return (
    <div>
      <ol className="flex flex-wrap items-center gap-1" aria-label="Shipment stages">
        {rungs.map((rung, index) => {
          const reached = rung.ordinal < currentOrdinal || (!onFork && rung.code === current);
          const isCurrent = !onFork && rung.code === current;
          return (
            <li key={rung.code} className="flex items-center gap-1">
              {index > 0 && (
                <span
                  aria-hidden="true"
                  className={`h-px w-4 ${reached ? "bg-primary" : "bg-outline-variant"}`}
                />
              )}
              <span
                aria-current={isCurrent ? "step" : undefined}
                className={`rounded-md border px-2 py-1 text-xs ${
                  isCurrent
                    ? "border-primary bg-primary text-on-primary font-semibold"
                    : reached
                      ? "border-primary/40 bg-primary-container/20 text-primary"
                      : "border-outline-variant text-outline"
                }`}
              >
                {rung.label}
              </span>
            </li>
          );
        })}
      </ol>
      {forks.length > 0 && (
        <div className="mt-2 flex items-center gap-2 pl-6" aria-label="Exception side-states">
          <AlertTriangle size={12} aria-hidden="true" className="text-outline" />
          {forks.map((fork) => (
            <span
              key={fork.code}
              className={`rounded-md border px-2 py-0.5 text-xs ${
                onFork && fork.code === current
                  ? chipClass(fork.color_token) + " font-semibold"
                  : "border-dashed border-outline-variant text-outline"
              }`}
            >
              {fork.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function formatWhen(value: unknown): string {
  if (typeof value !== "string" || value === "") return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function ShipmentConsolePage() {
  const queries = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const [caseFilter, setCaseFilter] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const catalog = useQuery({ queryKey: ["shipments", "catalog"], queryFn: shipmentsApi.catalog });
  const list = useQuery({
    queryKey: ["shipments", "list", statusFilter, caseFilter, search],
    queryFn: () =>
      shipmentsApi.list({
        status: statusFilter || undefined,
        case: caseFilter || undefined,
        search: search || undefined,
      }),
    refetchInterval: 15_000,
  });
  const selected = useQuery({
    queryKey: ["shipments", "detail", selectedId],
    queryFn: () => shipmentsApi.get(selectedId ?? ""),
    enabled: selectedId !== null,
  });

  // ---- update panel state ---------------------------------------------------
  const [nextStatus, setNextStatus] = useState("");
  const [location, setLocation] = useState("");
  const [note, setNote] = useState("");
  const [eventAt, setEventAt] = useState("");
  const [allowAny, setAllowAny] = useState(false);
  const [updateError, setUpdateError] = useState<string | null>(null);

  const doc = selected.data;
  const mode = doc ? (text(doc, "mode") ?? "parcel") : "parcel";
  const current = doc ? (text(doc, "current_status") ?? "") : "";
  const currentEntry = statusOf(catalog.data, mode, current);
  const isTerminal = currentEntry?.terminal === true;

  const allowedNext = useMemo(() => {
    if (!catalog.data || !doc) return [];
    if (allowAny) {
      return catalog.data.statuses.filter((status) => status.ladder === mode);
    }
    const allowed = new Set(currentEntry?.allowed_next ?? []);
    return catalog.data.statuses.filter(
      (status) => status.ladder === mode && allowed.has(status.code),
    );
  }, [catalog.data, doc, mode, currentEntry, allowAny]);

  const update = useMutation({
    mutationFn: async () => {
      if (!doc) throw new Error("No shipment selected.");
      const shipmentId = text(doc, "shipment_id");
      if (!shipmentId) throw new Error("The selected shipment carries no id.");
      return shipmentsApi.appendEvent(shipmentId, {
        status: nextStatus,
        location: location || undefined,
        note: note || undefined,
        eventAt: eventAt ? new Date(eventAt).toISOString() : undefined,
        override: allowAny || undefined,
        overrideReason: allowAny ? note || "console override" : undefined,
      });
    },
    onMutate: () => {
      setUpdateError(null);
    },
    onSuccess: (updated) => {
      queries.setQueryData(["shipments", "detail", selectedId], updated);
      void queries.invalidateQueries({ queryKey: ["shipments", "list"] });
      setNextStatus("");
      setLocation("");
      setNote("");
      setEventAt("");
    },
    onError: (caught: Error) => {
      // State what failed and what to do -- the transition rule, not a toast.
      setUpdateError(caught.message);
      void queries.invalidateQueries({ queryKey: ["shipments", "detail", selectedId] });
    },
  });

  const selectRow = useCallback((row: ShipmentDocument) => {
    setSelectedId(text(row, "shipment_id"));
    setNextStatus("");
    setUpdateError(null);
  }, []);

  const events = ((doc?.events as ShipmentDocument[] | undefined) ?? [])
    .slice()
    .reverse();

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold text-on-surface">Shipment Status Console</h1>
          <p className="text-xs text-on-surface-variant">
            Drive a return shipment through its ladder. Every status and transition comes from
            the released catalog.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void list.refetch()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-outline-control px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:text-on-surface"
        >
          <RefreshCw size={13} aria-hidden="true" /> Refresh
        </button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(320px,2fr)_3fr]">
        {/* LEFT: active return shipments */}
        <section
          aria-label="Return shipments"
          className="flex min-h-0 flex-col rounded-xl border border-outline-variant/40 bg-surface-container-lowest"
        >
          <div className="flex flex-wrap gap-2 border-b border-outline-variant/30 p-2">
            <label className="relative flex-1">
              <Search
                size={13}
                aria-hidden="true"
                className="absolute left-2 top-1/2 -translate-y-1/2 text-outline"
              />
              <input
                type="search"
                value={search}
                onChange={(event) => { setSearch(event.target.value); }}
                placeholder="Tracking, PRO, BOL, RMA or case id"
                aria-label="Search shipments"
                className="w-full rounded-lg border border-outline-control bg-surface py-1.5 pl-7 pr-2 text-xs text-on-surface"
              />
            </label>
            <select
              value={statusFilter}
              onChange={(event) => { setStatusFilter(event.target.value); }}
              aria-label="Filter by status"
              className="rounded-lg border border-outline-control bg-surface px-2 py-1.5 text-xs text-on-surface"
            >
              <option value="">All statuses</option>
              {(catalog.data?.statuses ?? []).map((status: CatalogStatus) => (
                <option key={`${status.ladder}:${status.code}`} value={status.code}>
                  {status.label} ({status.ladder})
                </option>
              ))}
            </select>
            <input
              value={caseFilter}
              onChange={(event) => { setCaseFilter(event.target.value); }}
              placeholder="Case id"
              aria-label="Filter by case"
              className="w-28 rounded-lg border border-outline-control bg-surface px-2 py-1.5 text-xs text-on-surface"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {list.isLoading ? (
              <p className="p-4 text-xs text-outline">Loading shipments…</p>
            ) : list.error ? (
              <p className="p-4 text-xs text-error">
                The shipment list could not be loaded: {list.error.message}. Retry with the
                Refresh button; if it persists, the shipment store or its release
                configuration is down.
              </p>
            ) : (list.data ?? []).length === 0 ? (
              <p className="p-4 text-xs text-outline">
                No return shipments match. A shipment appears here once Support&apos;s reply
                seeds it.
              </p>
            ) : (
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-surface-container-low text-outline">
                  <tr>
                    <th className="px-2 py-1.5 font-medium">Tracking / PRO</th>
                    <th className="px-2 py-1.5 font-medium">RMA</th>
                    <th className="px-2 py-1.5 font-medium">Case</th>
                    <th className="px-2 py-1.5 font-medium">Carrier</th>
                    <th className="px-2 py-1.5 font-medium">Mode</th>
                    <th className="px-2 py-1.5 font-medium">Status</th>
                    <th className="px-2 py-1.5 font-medium">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {(list.data ?? []).map((row) => {
                    const id = text(row, "shipment_id");
                    const isSelected = id !== null && id === selectedId;
                    return (
                      <tr
                        key={id ?? text(row, "tracking_reference") ?? "row"}
                        onClick={() => { selectRow(row); }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") selectRow(row);
                        }}
                        tabIndex={0}
                        aria-selected={isSelected}
                        className={`cursor-pointer border-t border-outline-variant/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${
                          isSelected ? "bg-primary-container/15" : "hover:bg-surface-container-low/60"
                        }`}
                      >
                        <td className="px-2 py-1.5 font-medium text-on-surface">
                          {text(row, "tracking_reference") ?? text(row, "pro_number") ?? "—"}
                        </td>
                        <td className="px-2 py-1.5">{text(row, "rma_reference") ?? "—"}</td>
                        <td className="max-w-[9rem] truncate px-2 py-1.5">
                          {text(row, "case_id") ?? "—"}
                        </td>
                        <td className="px-2 py-1.5">{text(row, "carrier") ?? "—"}</td>
                        <td className="px-2 py-1.5 uppercase">{text(row, "mode") ?? "—"}</td>
                        <td className="px-2 py-1.5">
                          <StatusChip
                            catalog={catalog.data}
                            mode={text(row, "mode") ?? "parcel"}
                            code={text(row, "current_status") ?? ""}
                          />
                        </td>
                        <td className="px-2 py-1.5 text-outline">
                          {formatWhen(row.updated_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </section>

        {/* RIGHT: selected shipment detail */}
        <section
          aria-label="Shipment detail"
          className="flex min-h-0 flex-col gap-3 overflow-auto rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-3"
        >
          {!doc ? (
            <p className="text-xs text-outline">Select a shipment to see its ladder and events.</p>
          ) : (
            <>
              <header className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold text-on-surface">
                    {text(doc, "tracking_reference") ?? "—"}
                    <span className="ml-2 text-xs font-normal uppercase text-outline">
                      {mode}
                    </span>
                  </h2>
                  <dl className="mt-1 grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs text-on-surface-variant md:grid-cols-3">
                    <div>
                      <dt className="inline text-outline">RMA </dt>
                      <dd className="inline">{text(doc, "rma_reference") ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="inline text-outline">Case </dt>
                      <dd className="inline">
                        {text(doc, "case_id") ? (
                          <Link
                            href={`/operations?case=${text(doc, "case_id") ?? ""}`}
                            className="text-primary underline-offset-2 hover:underline"
                          >
                            {text(doc, "case_id")}
                          </Link>
                        ) : (
                          "—"
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt className="inline text-outline">Record </dt>
                      <dd className="inline">{text(doc, "return_record_id") ?? "—"}</dd>
                    </div>
                    {text(doc, "bol_reference") && (
                      <div>
                        <dt className="inline text-outline">BOL </dt>
                        <dd className="inline">{text(doc, "bol_reference")}</dd>
                      </div>
                    )}
                    {text(doc, "label_reference") && (
                      <div>
                        <dt className="inline text-outline">Label </dt>
                        <dd className="inline">{text(doc, "label_reference")}</dd>
                      </div>
                    )}
                    {(text(doc, "destination_warehouse") ?? text(doc, "destination_bay")) && (
                      <div>
                        <dt className="inline text-outline">Destination </dt>
                        <dd className="inline">
                          {[text(doc, "destination_warehouse"), text(doc, "destination_bay")]
                            .filter(Boolean)
                            .join(" / ")}
                        </dd>
                      </div>
                    )}
                  </dl>
                </div>
                <StatusChip catalog={catalog.data} mode={mode} code={current} />
              </header>

              {catalog.data && <StageRail catalog={catalog.data} mode={mode} current={current} />}

              {isTerminal && (
                <div
                  role="status"
                  className="rounded-lg border border-primary/40 bg-primary-container/15 px-3 py-2 text-xs text-on-surface"
                >
                  This shipment reached the terminal state{" "}
                  <strong>{currentEntry.label}</strong>. Updates are disabled. To reopen it,
                  switch on “allow any status”, choose a status, and record the reason in the
                  note — the override is written onto the event.
                </div>
              )}

              {/* update panel */}
              <form
                aria-label="Update status"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (nextStatus) update.mutate();
                }}
                className="grid grid-cols-1 gap-2 rounded-lg border border-outline-variant/40 bg-surface-container-low/50 p-2 md:grid-cols-2"
              >
                <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                  Status
                  <select
                    value={nextStatus}
                    onChange={(event) => { setNextStatus(event.target.value); }}
                    disabled={(isTerminal && !allowAny) || update.isPending}
                    className="rounded-lg border border-outline-control bg-surface px-2 py-1.5 text-xs text-on-surface disabled:opacity-50"
                  >
                    <option value="">Choose next status…</option>
                    {allowedNext.map((status) => (
                      <option key={status.code} value={status.code}>
                        {status.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                  Location
                  <input
                    value={location}
                    onChange={(event) => { setLocation(event.target.value); }}
                    placeholder="Facility, terminal or city"
                    className="rounded-lg border border-outline-control bg-surface px-2 py-1.5 text-xs text-on-surface"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                  Event time (blank = now)
                  <input
                    type="datetime-local"
                    value={eventAt}
                    onChange={(event) => { setEventAt(event.target.value); }}
                    className="rounded-lg border border-outline-control bg-surface px-2 py-1.5 text-xs text-on-surface"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                  Note
                  <input
                    value={note}
                    onChange={(event) => { setNote(event.target.value); }}
                    placeholder="Free text carried on the event"
                    className="rounded-lg border border-outline-control bg-surface px-2 py-1.5 text-xs text-on-surface"
                  />
                </label>
                <label className="flex items-center gap-2 text-xs text-on-surface-variant">
                  <input
                    type="checkbox"
                    checked={allowAny}
                    onChange={(event) => { setAllowAny(event.target.checked); }}
                  />
                  Allow any status (testing override — logged on the event)
                </label>
                <div className="flex items-end justify-end">
                  <button
                    type="submit"
                    disabled={!nextStatus || update.isPending || (isTerminal && !allowAny)}
                    className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary disabled:opacity-40"
                  >
                    {update.isPending ? "Updating…" : "Update status"}
                  </button>
                </div>
                {updateError && (
                  <p role="alert" className="col-span-full text-xs text-error">
                    The update was refused: {updateError} Pick one of the allowed statuses, or
                    switch on the override to record an out-of-ladder move.
                  </p>
                )}
              </form>

              {/* event log */}
              <section aria-label="Event log" className="min-h-0">
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-outline">
                  Event log · newest first
                </h3>
                {events.length === 0 ? (
                  <p className="text-xs text-outline">
                    No events yet — the shipment is where Support&apos;s reply seeded it.
                  </p>
                ) : (
                  <ol className="divide-y divide-outline-variant/20 text-xs">
                    {events.map((event, index) => (
                      <li key={text(event, "event_id") ?? index} className="flex flex-wrap gap-x-4 gap-y-0.5 py-1.5">
                        <StatusChip
                          catalog={catalog.data}
                          mode={mode}
                          code={text(event, "status") ?? ""}
                        />
                        <span className="text-on-surface-variant">
                          {formatWhen(event.event_at)}
                        </span>
                        {text(event, "location") && (
                          <span className="text-on-surface-variant">@ {text(event, "location")}</span>
                        )}
                        <span className="text-outline">by {text(event, "actor") ?? "—"}</span>
                        {event.override === true && (
                          <span className="font-medium text-error">
                            override{text(event, "override_reason") !== null && `: ${text(event, "override_reason") ?? ""}`}
                          </span>
                        )}
                        {text(event, "note") && (
                          <span className="w-full text-on-surface-variant">{text(event, "note")}</span>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
