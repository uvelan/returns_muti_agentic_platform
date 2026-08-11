import type { ReturnSessionView, ReturnStatus } from "../../api/returnsDomain";

/**
 * Queue partitioning for the Return Business Copilot (Phase 18).
 *
 * Separate from the screen so the predicates are testable and so the .tsx
 * exports only components (React Fast Refresh).
 *
 * Queues are views over one session list, never separate products: the same
 * workspace renders whichever queue you came from. Membership is derived from
 * session state rather than stored, so a session appears in the right queue
 * the moment its state changes without anything having to reassign it.
 */

export type QueueId = "mine" | "support" | "warehouse" | "closed";

const CLOSED_STATUSES: ReadonlySet<ReturnStatus> = new Set<ReturnStatus>([
  "COMPLETED",
  "REJECTED",
  "CANCELLED",
  "FAILED",
]);

export function isClosed(session: ReturnSessionView): boolean {
  return CLOSED_STATUSES.has(session.status) || session.caseClosureStatus === "CLOSED";
}

export type QueueDefinition = {
  readonly id: QueueId;
  readonly label: string;
  readonly match: (session: ReturnSessionView) => boolean;
};

export const QUEUES: readonly QueueDefinition[] = [
  {
    id: "mine",
    label: "My Returns",
    match: (session) => !isClosed(session),
  },
  {
    id: "support",
    label: "Support",
    // Support involvement is a support ticket or a state that demands human
    // judgement -- not the support *role*, which would make the queue depend
    // on who is looking rather than on the return.
    match: (session) =>
      !isClosed(session)
      && (session.status === "WAITING_SUPPORT"
        || session.supportTicketReference !== null
        || session.status === "REVIEW_REQUIRED"),
  },
  {
    id: "warehouse",
    label: "Warehouse",
    match: (session) =>
      !isClosed(session)
      && (session.warehouseStatus !== "NOT_REQUIRED_OR_PENDING"
        || session.bayReference !== null),
  },
  {
    id: "closed",
    label: "Closed",
    match: isClosed,
  },
];
