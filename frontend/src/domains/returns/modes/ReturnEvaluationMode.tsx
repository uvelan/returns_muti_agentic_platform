import { AlertTriangle, CheckCircle2, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import type {
  AwaitingDimension,
  PolicyEvaluationProjection,
  SupportProjection,
} from "../../../api/cases";

/**
 * What the deterministic evaluator decided, and on whose authority.
 *
 * The pane used to open with a `DEFAULT_EVALUATION` -- approved, policy code
 * `POL-STD-30D`, refund `149.99` -- rendered under the heading "Authoritative
 * Policy Engine" before any evaluation had run. Nothing on it was computed by
 * anything.
 *
 * **The refund figure.** The evaluator emits a restocking-fee *rate* in basis
 * points with a `rate_source`, never a currency amount: it is pure and holds no
 * line prices. `PolicyEvaluationProjection` carries neither the rate nor any
 * price, and `SelectedItemProjection` and `ApprovedItemProjection` carry
 * quantities without unit prices -- so there is nothing on the wire from which a
 * figure could be computed. What is knowable is *whether a fee applies*, which
 * `PolicyCondition` states outright, and that is what this pane says. A number
 * here would have no source, and a number with no source is the thing this
 * whole programme exists to delete.
 *
 * **There is no "issue the RMA" control here, and there cannot be one.** The one
 * that stood in this pane was labelled "View RMA & Shipping Label" and submitted
 * the literal words `authorize rma` into the discovery conversation. Nothing
 * about that could work. `ReturnCaseWorkflow.run` drives itself from the
 * confirmation -- bay, then `_policy_cleared`, then `_open_support` -- so by the
 * time this pane is on screen the case is already with Support, and the RMA is
 * created when Support records its outcome: `POST
 * /api/support/work-items/{id}/actions`, which reaches the case through the
 * workflow's `support_response` signal and the `record_support_outcome`
 * activity. The order-discovery agent this copilot talks to has no reach into
 * any of that; the sentence would have been answered as conversation, and an
 * associate would have been told an RMA was coming by a screen that had asked
 * nobody for one. Its sibling `onEvaluate`, which posted "evaluate policy", was
 * deleted for exactly this reason -- this is the same defect wearing a different
 * verb. What replaces it is a statement of where the decision is; the copilot
 * moves to the RMA pane on its own when the case carries a record, because
 * `stage` puts it there and no click is involved.
 */

export type ReturnEvaluationModeProps = {
  evaluation?: PolicyEvaluationProjection | null;
  /**
   * `policy_evaluation_state` off the case, for the one state that has no
   * `PolicyEvaluationProjection` and is not pending either.
   *
   * A deployment can suspend the gate through `policy_evaluation.enabled`. The
   * evaluator then never runs, so there is no route and no decision to project
   * -- and this pane read that absence as "Pending", which tells an associate a
   * verdict is on its way when none is coming. It is reported as skipped
   * instead, with the operator's stated reason, which is what the Support
   * handoff says about the same case.
   */
  policyEvaluationState?: string | null;
  policySkipReason?: string | null;
  /** What the platform is still waiting for. Drives the verification hand-off text. */
  awaiting?: readonly AwaitingDimension[];
  support?: SupportProjection | null;
  onApproveOverride?: () => void;
  onRequestException?: () => void;
};

const PENDING = "Pending";

/** The gate did not run, and will not. Distinct from "has not run yet". */
const SKIPPED_BY_CONFIGURATION = "SKIPPED_BY_CONFIGURATION";
const NOT_EVALUATED = "Not evaluated";

/** Applicability, which is knowable. Never a rate the contract does not carry. */
function restockingFee(
  evaluation: PolicyEvaluationProjection | null,
  skipped: boolean,
): string {
  const conditions = evaluation?.conditions ?? [];
  if (conditions.includes("RESTOCKING_FEE_WAIVED")) return "Waived";
  if (conditions.includes("RESTOCKING_FEE_APPLIES")) {
    return "Applies · rate set by seller configuration";
  }
  return skipped ? NOT_EVALUATED : PENDING;
}

/** The applied policy, as the release that decided it. */
function appliedPolicy(
  evaluation: PolicyEvaluationProjection | null,
  skipped: boolean,
): string {
  const policyId = evaluation?.policyId ?? null;
  const version = evaluation?.policyVersion ?? null;
  if (policyId === null) return skipped ? NOT_EVALUATED : PENDING;
  return version === null ? policyId : `${policyId} · ${version}`;
}

function headline(
  evaluation: PolicyEvaluationProjection | null,
  skipped: boolean,
): { title: string; badge: string; tone: "approved" | "review" | "rejected" } {
  if (skipped) {
    // Not approved and not pending. The gate was switched off, so no rule was
    // applied to this return -- which a human downstream has to know, because it
    // is the check they would otherwise assume had happened.
    return { title: "Policy Evaluation Skipped", badge: "SKIPPED", tone: "review" };
  }
  if (evaluation === null) {
    return { title: "Policy Evaluation Pending", badge: PENDING, tone: "review" };
  }
  if (evaluation.route !== "STANDARD_RETURN") {
    // A warranty or delivery claim carries no decision by contract: Support
    // verifies it, and an "approved" badge here would approve the claim the
    // verification exists to test.
    return {
      title: "Verification With Support",
      badge: evaluation.route,
      tone: "review",
    };
  }
  const decision = evaluation.effectiveDecision;
  if (decision === "APPROVE") {
    return { title: "Return Eligible · Policy Approved", badge: decision, tone: "approved" };
  }
  if (decision === "REJECT") {
    return { title: "Return Ineligible", badge: decision, tone: "rejected" };
  }
  if (decision === "REVIEW_REQUIRED") {
    return {
      title: "Policy Exception Review Required",
      badge: decision,
      tone: "review",
    };
  }
  return { title: "Policy Evaluation Pending", badge: PENDING, tone: "review" };
}

export function ReturnEvaluationMode({
  evaluation = null,
  policyEvaluationState = null,
  policySkipReason = null,
  awaiting = [],
  support = null,
  onApproveOverride,
  onRequestException,
}: ReturnEvaluationModeProps) {
  // An evaluation that *did* run wins over the state fact. The fact is how the
  // one state with no projection is told apart from a case whose evaluation has
  // simply not happened yet; it must never override a real decision.
  const skipped = evaluation === null && policyEvaluationState === SKIPPED_BY_CONFIGURATION;
  const { title, badge, tone } = headline(evaluation, skipped);
  const isApproved = tone === "approved";
  const isRejected = tone === "rejected";
  const overridable =
    evaluation?.route === "STANDARD_RETURN" &&
    evaluation.effectiveDecision === "REVIEW_REQUIRED";
  const awaitingVerification =
    awaiting.includes("WARRANTY_VERIFICATION") ||
    awaiting.includes("DELIVERY_CLAIM_VERIFICATION");
  const reasons = [...(evaluation?.reasonCodes ?? []), ...(evaluation?.appliedRules ?? [])];

  return (
    <div className="flex flex-col gap-4">
      {/* 1. Policy Header Banner */}
      <div className="flex flex-col gap-3 rounded-xl border border-outline-variant/30 bg-surface-container-low p-4 shadow-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            {isApproved ? (
              <span className="flex size-8 items-center justify-center rounded-full bg-emerald-600 text-white shadow-xs">
                <CheckCircle2 size={18} />
              </span>
            ) : isRejected ? (
              <span className="flex size-8 items-center justify-center rounded-full bg-error text-white shadow-xs">
                <XCircle size={18} />
              </span>
            ) : (
              <span className="flex size-8 items-center justify-center rounded-full bg-amber-500 text-white shadow-xs">
                <AlertTriangle size={18} />
              </span>
            )}
            <div>
              <h3 className="text-sm font-bold text-on-surface">{title}</h3>
              {/*
                Whose authority. On a skipped gate there is none, so the line
                says what is true of the case rather than naming an engine that
                did not run.
              */}
              <span className="text-xs text-outline font-medium">
                {skipped ? "No policy was applied to this return" : "Authoritative Policy Engine"}
              </span>
              {/*
                The operator's own words, rendered only when they exist. A
                default here would be a reason nobody gave -- which is the
                fabrication `ReturnCopilotFabrication.test.ts` refuses, and it
                was right to catch the first attempt at this line.
              */}
              {skipped && policySkipReason ? (
                <p className="mt-0.5 text-xs text-outline">{policySkipReason}</p>
              ) : null}
            </div>
          </div>

          <span
            className={[
              "rounded-full px-2.5 py-0.5 text-xs font-bold uppercase",
              isApproved
                ? "bg-secondary-container text-primary"
                : isRejected
                  ? "bg-error-container text-error"
                  : "bg-amber-100 text-amber-900",
            ].join(" ")}
          >
            {badge}
          </span>
        </div>
      </div>

      {/* 2. Policy Evaluation Metrics Grid */}
      <div className="flex flex-col gap-3 rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-4 shadow-xs">
        <span className="text-xs font-semibold uppercase tracking-wider text-outline">
          Policy Evaluation Rules & Calculations
        </span>

        <dl className="flex flex-col gap-2.5 text-xs">
          <div className="flex justify-between border-b border-outline-variant/15 pb-2">
            <dt className="text-outline">Applied Policy Code</dt>
            <dd className="font-mono font-bold text-on-surface">{appliedPolicy(evaluation, skipped)}</dd>
          </div>

          <div className="flex justify-between border-b border-outline-variant/15 pb-2">
            <dt className="text-outline">Restocking Fee</dt>
            <dd className="font-semibold text-on-surface">{restockingFee(evaluation, skipped)}</dd>
          </div>

          <div className="flex justify-between border-b border-outline-variant/15 pb-2">
            <dt className="text-outline">Authorized Refund / Credit</dt>
            <dd className="font-semibold text-on-surface text-right max-w-[60%]">
              {/* The evaluator issues a rate and a source, never an amount, and
                  the case carries no line prices to apply one to. */}
              Not computed by the policy engine
            </dd>
          </div>

          {reasons.length > 0 ? (
            <div className="flex flex-col gap-1 pt-1">
              <dt className="text-outline">Policy Notes</dt>
              <dd className="rounded-lg bg-surface-container-low p-2.5 text-on-surface leading-relaxed text-xs">
                {reasons.join(" · ")}
              </dd>
            </div>
          ) : null}

          {evaluation?.override != null ? (
            <div className="flex flex-col gap-1 pt-1">
              <dt className="text-outline">Supervisor Override</dt>
              <dd className="rounded-lg bg-surface-container-low p-2.5 text-on-surface leading-relaxed text-xs">
                {evaluation.override.overrideDecision} · {evaluation.override.reasonCode} ·{" "}
                {evaluation.override.actor}
              </dd>
            </div>
          ) : null}
        </dl>
      </div>

      {/* 3. Where the answer is being held: Support, or a supervisor. */}
      {awaitingVerification || support !== null ? (
        <div className="flex flex-col gap-2 rounded-xl border border-amber-300 bg-amber-50/50 p-3.5 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-amber-800">
            <ShieldAlert size={15} />
            <span>Support Verification In Progress</span>
          </div>
          <p className="text-on-surface-variant leading-relaxed">
            {support === null
              ? "Support has been asked to verify this claim."
              : `${support.queue ?? "Support"} · ${support.status ?? PENDING}`}
          </p>
        </div>
      ) : overridable ? (
        <div className="flex flex-col gap-2 rounded-xl border border-amber-300 bg-amber-50/50 p-3.5 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-amber-800">
            <ShieldAlert size={15} />
            <span>Supervisor Override Available</span>
          </div>
          <p className="text-on-surface-variant leading-relaxed">
            The evaluator returned REVIEW_REQUIRED. A supervisor may record an override, which is
            appended to the case and never replaces the original decision.
          </p>
          <div className="flex gap-2 mt-1">
            {onApproveOverride ? (
              <button
                type="button"
                onClick={onApproveOverride}
                className="flex-1 rounded-lg bg-primary-container py-2 text-xs font-bold text-white shadow-xs hover:bg-primary-container/90"
              >
                Authorize Override
              </button>
            ) : null}
            {onRequestException ? (
              <button
                type="button"
                onClick={onRequestException}
                className="flex-1 rounded-lg border border-outline-control bg-surface py-2 text-xs font-medium text-on-surface"
              >
                Escalate Ticket
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* 4. What happens next, and who does it. Deliberately not a control. */}
      {isApproved ? (
        <div className="flex items-start gap-3 rounded-xl border border-primary/20 bg-secondary-container/40 p-4">
          <ShieldCheck size={16} className="mt-0.5 shrink-0 text-primary" />
          <div className="flex flex-col gap-1">
            <span className="block text-[11px] font-semibold text-outline">
              Next Lifecycle Action
            </span>
            <span className="text-xs font-bold text-primary">
              Support issues the RMA and its shipping label
            </span>
            <p className="text-[11px] leading-relaxed text-on-surface-variant">
              The return workflow has already carried this case past the policy gate and onto the
              Support queue; the RMA is created when Support records its outcome against the work
              item. Nothing on this screen can issue one, so there is no button here — this pane
              becomes the RMA manifest by itself as soon as the case carries a record.
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
