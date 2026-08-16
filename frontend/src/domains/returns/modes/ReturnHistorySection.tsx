import type { ReturnHistory, ReturnHistoryCase } from "../../../api/returnHistory";

export type ReturnHistorySectionProps = {
  history: ReturnHistory | null;
  pending: boolean;
  error: Error | null;
};

export function ReturnHistorySection({
  history,
  pending,
  error,
}: ReturnHistorySectionProps) {
  if (error !== null) {
    return (
      <section className="mt-3 border-t border-outline-variant pt-3">
        <h3 className="px-2 text-xs font-semibold text-on-surface">Return history</h3>
        <p className="px-2 pt-1 text-xs text-error">
          Earlier returns could not be read. {error.message}
        </p>
      </section>
    );
  }

  if (pending) {
    return (
      <section className="mt-3 border-t border-outline-variant pt-3">
        <h3 className="px-2 text-xs font-semibold text-on-surface">Return history</h3>
        <p className="px-2 pt-1 text-xs text-outline">Checking earlier returns...</p>
      </section>
    );
  }

  if (history === null) return null;

  return (
    <section className="mt-3 border-t border-outline-variant pt-3">
      <h3 className="px-2 text-xs font-semibold text-on-surface">
        Return history ({String(history.cases.length)})
      </h3>
      {history.cases.length === 0 ? (
        <p className="px-2 pt-1 text-xs text-outline">No earlier returns recorded for this customer.</p>
      ) : (
        <ol className="mt-2 flex flex-col gap-2">
          {history.cases.map((historyCase) => (
            <ReturnHistoryCaseCard
              key={historyCase.caseId}
              historyCase={historyCase}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function ReturnHistoryCaseCard({
  historyCase,
}: {
  historyCase: ReturnHistoryCase;
}) {
  return (
    <li className="rounded border border-outline-variant bg-surface-container-low p-2 text-xs">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium text-on-surface">
          {historyCase.confirmedOrderReference ?? historyCase.caseId}
        </span>
        <span className="text-outline">{historyCase.status}</span>
      </div>

      {historyCase.returnRecords.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-1">
          {historyCase.returnRecords.map((record) => (
            <li key={record.returnRecordId} className="text-outline">
              <span className="font-medium text-on-surface">
                {record.returnReference ?? "RMA pending"}
              </span>{" "}
              ({record.status})
              {record.items.length > 0 ? (
                <span>
                  {" "}
                  -- <span>Covers {record.items.map((i) => i.orderLineReference).join(", ")}</span>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {historyCase.unassignedItems.length > 0 ? (
        <p className="mt-1 text-outline">
          Unassigned:{" "}
          {historyCase.unassignedItems
            .map((i) => i.orderLineReference)
            .join(", ")}
        </p>
      ) : null}

      {historyCase.placements.length > 0 ? (
        <p className="mt-1 text-outline">
          Staged in {historyCase.placements.map((p) => p.bayId ?? p.warehouseId).join(", ")}
        </p>
      ) : null}
    </li>
  );
}
