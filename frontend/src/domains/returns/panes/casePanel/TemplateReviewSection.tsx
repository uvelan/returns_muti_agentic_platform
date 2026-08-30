import { useCallback, useId, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Loader2,
  PenLine,
  RotateCcw,
  Send,
  Users,
} from "lucide-react";

import {
  asReviewConflict,
  casePanelApi,
  isRecoverable,
  type ReviewPanelView,
} from "../../../../api/casePanel";
import { COPILOT_TOKENS } from "../../copilotTokens";
import { applyEdits, fieldKey, useDraftEditor, type DraftSection } from "./useDraftEditor";

/**
 * One support request, as an associate reviews and sends it.
 *
 * **Everything a reviewer can do to a draft is here, and everything they can be
 * told about it.** The states are the aggregate's, not this file's invention:
 * `OPEN` is the only editable one, `APPROVING` is "approved by X, sending",
 * `DELIVERY_FAILED` and `HELD_FOR_OPERATIONS` offer recovery, and `SENT`,
 * `CANCELLED` and `ABANDONED` are terminal and still visible -- a review an
 * associate can no longer act on is frequently the one they most need to see.
 *
 * ---
 *
 * **Support-derived text is data, never markup** (dispatch condition 10). Every
 * value below reaches the DOM as a React text child, which escapes it. There is
 * no `dangerouslySetInnerHTML` on this surface and no markdown renderer, and
 * `TemplateReviewSection.test.tsx` asserts that a field value containing a tag
 * renders as the literal characters. That matters more here than almost
 * anywhere: these values come from Support, through an extractor, and they are
 * displayed to the person who decides what to send back.
 *
 * **The composed body is not rendered from `payload.text`.** The gate service
 * recomposes it from the sections for the same reason: after a field edit the
 * two differ, and reading the stored string would show the reviewer their
 * change on the panel and send Support the original.
 */

type Props = {
  readonly caseId: string;
  readonly review: ReviewPanelView;
  /** Re-read the panel after an action, so the state on screen is the store's. */
  readonly onChanged: () => void;
};

const STATE_WORDS: Record<string, string> = {
  OPEN: "Awaiting your review",
  APPROVING: "Sending",
  SENT: "Sent to Support",
  DELIVERY_FAILED: "Could not be sent",
  HELD_FOR_OPERATIONS: "Held for operations",
  CANCELLED: "Cancelled",
  ABANDONED: "Abandoned",
};

const STATE_ICONS: Record<string, typeof Send> = {
  OPEN: PenLine,
  APPROVING: Loader2,
  SENT: CheckCircle2,
  DELIVERY_FAILED: AlertTriangle,
  HELD_FOR_OPERATIONS: AlertTriangle,
  CANCELLED: CircleSlash,
  ABANDONED: CircleSlash,
};

function stateClass(state: string): string {
  const scale = COPILOT_TOKENS.review.state as Record<string, string>;
  return scale[state] ?? scale.CANCELLED;
}

/** Where a value came from, in the associate's words rather than the grammar's. */
function provenanceLabel(source: string, sourcePath: string): string {
  if (source === "case_fact") return `From case fact ${sourcePath}`;
  if (source === "return_record") return `From the RMA record (${sourcePath})`;
  if (source === "graph") return `From the knowledge graph (${sourcePath})`;
  if (source === "literal") return "Fixed text from the template";
  return `From ${source}`;
}

function provenanceChip(source: string): string {
  if (source === "case_fact") return "case fact";
  if (source === "return_record") return "RMA";
  if (source === "graph") return "graph";
  if (source === "literal") return "template";
  return source;
}

export function TemplateReviewSection({ caseId, review, onChanged }: Props) {
  const editor = useDraftEditor(caseId, review);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [rawMode, setRawMode] = useState(false);
  const headingId = useId();
  const statusId = useId();

  const sections: readonly DraftSection[] = editor.payload.sections ?? [];
  const gaps = editor.payload.gaps ?? [];
  const editable = review.state === "OPEN";
  const blocked = gaps.length > 0 || review.conflict_present;

  const act = useCallback(
    async (run: () => Promise<unknown>) => {
      setBusy(true);
      setRefusal(null);
      try {
        await run();
        onChanged();
      } catch (error) {
        const conflict = asReviewConflict(error);
        setRefusal(
          conflict
            ? // The transition, not the status code. "This review is already
              // being sent" is actionable; "409" makes an associate press the
              // button again.
              conflict.state
              ? `${conflict.message} (now: ${STATE_WORDS[conflict.state] ?? conflict.state})`
              : conflict.message
            : "That could not be completed. Nothing was sent.",
        );
      } finally {
        setBusy(false);
      }
    },
    [onChanged],
  );

  const send = useCallback(() => {
    void act(async () => {
      // Any in-flight autosave lands first, so the version the approval names
      // is the version the store holds.
      await editor.flush();
      if (review.approval_hash === null) {
        throw new Error("This draft can no longer be approved.");
      }
      // **The hash is the panel's, never computed here.** The store's CAS
      // compares against its own canonical serialization, and a browser
      // deriving that would be a second implementation of a compare-and-set --
      // the two would disagree the first time either side changed how a payload
      // serializes, and every approval would answer 409 for a reason no
      // associate could act on. Echoing it costs the guarantee nothing: a draft
      // that moved since this panel read hashes differently and is still
      // refused.
      return casePanelApi.approve(caseId, review.review_id, {
        draft_version: review.draft_version,
        canonical_edit_version: review.canonical_edit_version,
        canonical_approved_payload_hash: review.approval_hash,
      });
    });
  }, [act, caseId, editor, review]);

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className={COPILOT_TOKENS.typography.subheading}>
          Message to Support
        </h3>
        <ReviewStateBadge state={review.state} />
      </header>

      {/*
        Status, saves and arrivals, announced politely and never focused. An
        associate mid-sentence must not be interrupted, and a support artifact
        landing while they type must not take the caret out of the field.
      */}
      <p
        id={statusId}
        role="status"
        aria-live="polite"
        className={COPILOT_TOKENS.review.liveRegion}
      >
        {editor.announcement}
      </p>

      {review.approved_by !== null && review.state === "APPROVING" ? (
        <p className={COPILOT_TOKENS.typography.caption}>
          Approved by {review.approved_by}. Sending to Support now.
        </p>
      ) : null}

      {gaps.length > 0 ? (
        <div className={COPILOT_TOKENS.review.gap}>
          <p className="font-semibold">
            {gaps.length === 1
              ? "One required detail is missing"
              : `${String(gaps.length)} required details are missing`}
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {gaps.map((gap, index) => (
              <li key={`${gap.field_id}-${String(index)}`}>
                {gap.field_id} — {gap.reason}
              </li>
            ))}
          </ul>
          <p className="mt-1">
            This message cannot be sent until the case knows these. Ask for a fresh draft once it
            does.
          </p>
        </div>
      ) : null}

      {review.conflict_present ? (
        <div className={COPILOT_TOKENS.review.conflict}>
          <p className="flex items-center gap-1.5 font-semibold">
            <Users aria-hidden="true" className="size-3.5" />
            Somebody else is editing this draft
          </p>
          <p className="mt-1">
            Their wording is not shown here — it is theirs until it is agreed. Settle on one version
            before sending.
          </p>
          <div className={COPILOT_TOKENS.review.action.bar}>
            <button
              type="button"
              className={COPILOT_TOKENS.review.action.secondary}
              aria-disabled={busy}
              onClick={() => {
                void act(() =>
                  casePanelApi.resolveEdit(caseId, review.review_id, {
                    canonical_payload: applyEdits(
                      editor.payload,
                      editor.values,
                      editor.bodyOverride,
                    ),
                    resolved_from_actor_edit_ids: [],
                  }),
                );
              }}
            >
              Keep this version
            </button>
          </div>
        </div>
      ) : null}

      {editor.supersededDraftVersion !== null ? (
        <div className={COPILOT_TOKENS.review.conflict}>
          <p className="font-semibold">The draft was rebuilt from newer case facts</p>
          <p className="mt-1">Your edits are still here. Take the new draft, or keep yours.</p>
          <div className={COPILOT_TOKENS.review.action.bar}>
            <button
              type="button"
              className={COPILOT_TOKENS.review.action.secondary}
              onClick={editor.discardEdits}
            >
              Take the new draft
            </button>
          </div>
        </div>
      ) : null}

      <dl className="divide-y divide-outline-variant/20">
        <div className={COPILOT_TOKENS.review.field.row}>
          <dt className={COPILOT_TOKENS.review.field.label}>Subject</dt>
          <dd className={COPILOT_TOKENS.review.field.value}>
            {editor.payload.subject ?? "Pending"}
          </dd>
        </div>
        {sections.map((section) => (
          <div key={section.section_id} className="py-2">
            <p className={COPILOT_TOKENS.section.kicker}>
              {section.title}
              {section.return_record_id !== null ? ` · ${section.return_record_id}` : ""}
            </p>
            {section.fields.map((field) => {
              const key = fieldKey(section.section_id, field.field_id);
              const value = editor.values[key] ?? field.value;
              const changed = value !== field.value;
              return (
                <div key={key} className={COPILOT_TOKENS.review.field.row}>
                  <dt className={COPILOT_TOKENS.review.field.label}>
                    <label htmlFor={`${statusId}-${key}`}>{field.label}</label>
                  </dt>
                  <dd>
                    {editable ? (
                      <textarea
                        id={`${statusId}-${key}`}
                        value={value}
                        rows={1}
                        aria-describedby={`${statusId}-${key}-src`}
                        className={`${COPILOT_TOKENS.review.field.input} ${
                          changed ? COPILOT_TOKENS.review.field.edited : ""
                        }`}
                        onChange={(event) => {
                          editor.setField(section.section_id, field.field_id, event.target.value);
                        }}
                      />
                    ) : (
                      <p className={COPILOT_TOKENS.review.field.value}>{value}</p>
                    )}
                    <p
                      id={`${statusId}-${key}-src`}
                      className="mt-1 flex flex-wrap items-center gap-1.5"
                    >
                      <span
                        className={COPILOT_TOKENS.review.provenance}
                        title={provenanceLabel(field.source, field.source_path)}
                      >
                        {provenanceChip(field.source)}
                      </span>
                      {field.applied_fallback ? (
                        <span className={COPILOT_TOKENS.review.provenance}>fallback</span>
                      ) : null}
                      {changed ? (
                        <span className={COPILOT_TOKENS.review.provenance}>
                          edited — was “{field.value}”
                        </span>
                      ) : null}
                      <span className="sr-only">
                        {provenanceLabel(field.source, field.source_path)}
                      </span>
                    </p>
                  </dd>
                </div>
              );
            })}
          </div>
        ))}
      </dl>

      {editable ? (
        <div className="space-y-2">
          <button
            type="button"
            className={COPILOT_TOKENS.review.action.secondary}
            aria-expanded={rawMode}
            aria-controls={`${statusId}-raw`}
            onClick={() => {
              setRawMode((open) => !open);
            }}
          >
            <PenLine aria-hidden="true" className="size-3.5" />
            {rawMode ? "Back to fields" : "Write the whole message instead"}
          </button>
          {rawMode ? (
            <div id={`${statusId}-raw`}>
              <label htmlFor={`${statusId}-raw-input`} className={COPILOT_TOKENS.typography.caption}>
                This replaces the whole message. Support sees exactly what you write.
              </label>
              <textarea
                id={`${statusId}-raw-input`}
                rows={10}
                value={editor.bodyOverride ?? ""}
                className={COPILOT_TOKENS.review.field.input}
                onChange={(event) => {
                  editor.setBodyOverride(event.target.value || null);
                }}
              />
            </div>
          ) : null}
        </div>
      ) : null}

      {refusal !== null ? (
        <p role="alert" className={COPILOT_TOKENS.review.gap}>
          {refusal}
        </p>
      ) : null}

      <ReviewActions
        busy={busy}
        blocked={blocked}
        editable={editable}
        review={review}
        onSend={send}
        onRevise={() => {
          void act(() => casePanelApi.revise(caseId, review.review_id));
        }}
        onCancel={() => {
          void act(() =>
            casePanelApi.cancel(caseId, review.review_id, "Cancelled by the branch associate."),
          );
        }}
        onRedraft={() => {
          void act(() => casePanelApi.redraft(caseId, review.review_id));
        }}
        onRetry={() => {
          void act(() => casePanelApi.retryDelivery(caseId, review.review_id));
        }}
        onAbandon={() => {
          void act(() =>
            casePanelApi.abandon(
              caseId,
              review.review_id,
              "Abandoned by the branch associate after a failed delivery.",
            ),
          );
        }}
      />
    </section>
  );
}

export function ReviewStateBadge({ state }: { readonly state: string }) {
  const Icon = STATE_ICONS[state] ?? CircleSlash;
  return (
    <span className={`${COPILOT_TOKENS.typography.badge} ${stateClass(state)}`}>
      {/*
        The icon is decorative: the word beside it carries the state, so a
        screen reader is not told "circle slash cancelled" and a colour-blind
        associate is not asked to tell amber from red.
      */}
      <Icon aria-hidden="true" className="size-3.5" />
      {STATE_WORDS[state] ?? state}
    </span>
  );
}

type ActionProps = {
  readonly busy: boolean;
  readonly blocked: boolean;
  readonly editable: boolean;
  readonly review: ReviewPanelView;
  readonly onSend: () => void;
  readonly onRevise: () => void;
  readonly onCancel: () => void;
  readonly onRedraft: () => void;
  readonly onRetry: () => void;
  readonly onAbandon: () => void;
};

function ReviewActions({
  busy,
  blocked,
  editable,
  review,
  onSend,
  onRevise,
  onCancel,
  onRedraft,
  onRetry,
  onAbandon,
}: ActionProps) {
  if (isRecoverable(review)) {
    return (
      <div className={COPILOT_TOKENS.review.action.bar}>
        <button
          type="button"
          className={COPILOT_TOKENS.review.action.primary}
          aria-disabled={busy}
          onClick={onRetry}
        >
          <RotateCcw aria-hidden="true" className="size-3.5" />
          Try sending again
        </button>
        <button
          type="button"
          className={COPILOT_TOKENS.review.action.danger}
          aria-disabled={busy}
          onClick={onAbandon}
        >
          Stop trying
        </button>
      </div>
    );
  }

  if (!editable) return null;

  return (
    <div className={COPILOT_TOKENS.review.action.bar}>
      {/*
        `aria-disabled`, never `disabled`. A `disabled` button leaves the tab
        order entirely, so a keyboard associate tabbing to Send would find
        nothing there and have no way to discover *why* it cannot be pressed.
        This one is reachable, announced as disabled, and explains itself.
      */}
      <button
        type="button"
        className={COPILOT_TOKENS.review.action.primary}
        aria-disabled={busy || blocked}
        aria-describedby={blocked ? "send-blocked-reason" : undefined}
        onClick={() => {
          if (!busy && !blocked) onSend();
        }}
      >
        <Send aria-hidden="true" className="size-3.5" />
        Send to Support
      </button>
      {blocked ? (
        <span id="send-blocked-reason" className={COPILOT_TOKENS.typography.caption}>
          {review.conflict_present
            ? "Settle the other edit first."
            : "Fill the missing details first."}
        </span>
      ) : null}
      <button
        type="button"
        className={COPILOT_TOKENS.review.action.secondary}
        aria-disabled={busy}
        onClick={onRevise}
      >
        Ask for a fresh draft
      </button>
      <button
        type="button"
        className={COPILOT_TOKENS.review.action.secondary}
        aria-disabled={busy}
        onClick={onRedraft}
      >
        Start over
      </button>
      <button
        type="button"
        className={COPILOT_TOKENS.review.action.danger}
        aria-disabled={busy}
        onClick={onCancel}
      >
        Do not send
      </button>
    </div>
  );
}
