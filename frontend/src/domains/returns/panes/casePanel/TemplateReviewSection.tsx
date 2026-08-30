import { useCallback, useEffect, useId, useRef, useState } from "react";
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
  /**
   * Which irreversible action is waiting for a second press.
   *
   * **Three of the five actions cannot be taken back**, and one click is not
   * enough for any of them: sending posts a message on a supplier's support
   * desk, cancelling parks the case terminally, and abandoning closes a
   * delivery for good with an audited actor. The other two -- rebuild and
   * discard-and-start-again -- change only what is on this screen, and asking
   * twice for those would be the confirmation habit that makes people stop
   * reading confirmations.
   *
   * Inline rather than `window.confirm`, which phase 1 registered as a
   * follow-up for the config publish and which cannot be styled, cannot be
   * read by a screen reader as part of this section, and blocks the tab.
   */
  const [confirming, setConfirming] = useState<ConfirmKind | null>(null);
  /**
   * Which action's button should take focus on the next action bar.
   *
   * WCAG 2.4.3. A keyboard associate who backs out of a confirmation must land
   * back on the control they pressed, not on `<body>` -- which in a long draft
   * means having lost their place entirely.
   */
  const [restoreFocusTo, setRestoreFocusTo] = useState<ConfirmKind | null>(null);
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
            This message cannot be sent until the case knows these. Rebuild the draft once it
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

      {/*
        One `<dl>` per section, each under its own real heading, rather than one
        `<dl>` wrapping headings and nested groups. `<dl>` may contain only
        `<dt>`, `<dd>` and `<div>` wrappers around them -- a `<p>` inside one is
        invalid and screen readers announce the list's structure wrongly. The
        headings are `<h4>`, not styled paragraphs: a fake heading is invisible
        to heading navigation, which is how somebody using a screen reader skims
        a long draft.
      */}
      <dl className="divide-y divide-outline-variant/20">
        <div className={COPILOT_TOKENS.review.field.row}>
          <dt className={COPILOT_TOKENS.review.field.label}>Subject</dt>
          <dd className={COPILOT_TOKENS.review.field.value}>
            {editor.payload.subject ?? "Pending"}
          </dd>
        </div>
      </dl>
      {sections.map((section) => (
          <div key={section.section_id} className="py-2">
            <h4 className={COPILOT_TOKENS.section.kicker}>
              {section.title}
              {section.return_record_id !== null ? ` · ${section.return_record_id}` : ""}
            </h4>
            <dl className="divide-y divide-outline-variant/20">
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
            </dl>
          </div>
        ))}

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

      {confirming === null ? (
        <ReviewActions
          busy={busy}
          blocked={blocked}
          editable={editable}
          review={review}
          focusOnMount={restoreFocusTo}
          onSend={() => {
            setConfirming("send");
          }}
          onRevise={() => {
            void act(() => casePanelApi.revise(caseId, review.review_id));
          }}
          onCancel={() => {
            setConfirming("cancel");
          }}
          onRedraft={() => {
            void act(() => casePanelApi.redraft(caseId, review.review_id));
          }}
          onRetry={() => {
            void act(() => casePanelApi.retryDelivery(caseId, review.review_id));
          }}
          onAbandon={() => {
            setConfirming("abandon");
          }}
        />
      ) : (
        <ConfirmAction
          kind={confirming}
          busy={busy}
          onCancel={() => {
            setConfirming(null);
            setRestoreFocusTo(confirming);
          }}
          onConfirm={() => {
            setConfirming(null);
            setRestoreFocusTo(null);
            if (confirming === "send") {
              send();
              return;
            }
            if (confirming === "cancel") {
              void act(() =>
                casePanelApi.cancel(caseId, review.review_id, "Cancelled by the branch associate."),
              );
              return;
            }
            void act(() =>
              casePanelApi.abandon(
                caseId,
                review.review_id,
                "Abandoned by the branch associate after a failed delivery.",
              ),
            );
          }}
        />
      )}
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
  /** The action whose button takes focus when this bar mounts. See 2.4.3. */
  readonly focusOnMount: ConfirmKind | null;
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
  focusOnMount,
  onSend,
  onRevise,
  onCancel,
  onRedraft,
  onRetry,
  onAbandon,
}: ActionProps) {
  const sendRef = useRef<HTMLButtonElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const abandonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (focusOnMount === "send") sendRef.current?.focus();
    if (focusOnMount === "cancel") cancelRef.current?.focus();
    if (focusOnMount === "abandon") abandonRef.current?.focus();
  }, [focusOnMount]);

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
          ref={abandonRef}
          type="button"
          className={COPILOT_TOKENS.review.action.danger}
          aria-disabled={busy}
          onClick={onAbandon}
        >
          Stop trying to send
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
        ref={sendRef}
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
        Rebuild from the latest facts
      </button>
      <button
        type="button"
        className={COPILOT_TOKENS.review.action.secondary}
        aria-disabled={busy}
        onClick={onRedraft}
      >
        Discard and start again
      </button>
      <button
        ref={cancelRef}
        type="button"
        className={COPILOT_TOKENS.review.action.danger}
        aria-disabled={busy}
        onClick={onCancel}
      >
        Cancel this request
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Confirming the three that cannot be taken back
 * ---------------------------------------------------------------------- */

type ConfirmKind = "send" | "cancel" | "abandon";

/**
 * What each confirmation says, and the shape is the same every time: **what
 * will happen**, then **what it costs**, then two buttons labelled with the
 * actions rather than with "OK" and "Cancel".
 *
 * "Are you sure?" is the version of this that teaches people to click through
 * it. Naming the consequence -- "Support will see this message", "this return
 * will stop waiting" -- is what makes the second press a decision rather than a
 * reflex.
 */
const CONFIRMATIONS: Record<
  ConfirmKind,
  { readonly question: string; readonly consequence: string; readonly confirm: string }
> = {
  send: {
    question: "Send this message to Support?",
    consequence:
      "Support will see it as it is written above. A message cannot be recalled once it has been sent.",
    confirm: "Send it",
  },
  cancel: {
    question: "Cancel this request?",
    consequence:
      "Support will not be asked, and this return will stop waiting for an answer. This cannot be undone.",
    confirm: "Cancel the request",
  },
  abandon: {
    question: "Stop trying to send this message?",
    consequence:
      "The platform will make no further attempts. Your name and your reason are recorded against the decision.",
    confirm: "Stop trying",
  },
};

function ConfirmAction({
  kind,
  busy,
  onConfirm,
  onCancel,
}: {
  readonly kind: ConfirmKind;
  readonly busy: boolean;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}) {
  const copy = CONFIRMATIONS[kind];
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  /*
   * Focus moves here, and this is the one place on this surface where taking
   * focus is right: the associate asked for it by pressing a button, and the
   * alternative is a prompt a keyboard user has to hunt for. It is *not* the
   * rule for arriving content, which never moves focus -- see `useDraftEditor`.
   *
   * Giving focus **back** is not this component's job, and the first attempt at
   * making it so was wrong in an instructive way: it captured
   * `document.activeElement` and restored it on unmount, which never fires
   * because the action bar unmounts with the prompt and the node it captured is
   * gone by then. The restore therefore lives in the parent, which knows *which
   * action* opened this and can focus that button on the bar it renders next.
   */
  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  return (
    <div
      role="group"
      aria-label={copy.question}
      className={`${COPILOT_TOKENS.review.conflict} mt-3`}
      onKeyDown={(event) => {
        // Escape backs out, which is what a person expects from anything that
        // took their focus -- and it is the fastest way out for somebody who
        // pressed the wrong button.
        if (event.key === "Escape") {
          event.stopPropagation();
          onCancel();
        }
      }}
    >
      <p className="font-semibold">{copy.question}</p>
      <p className="mt-1">{copy.consequence}</p>
      <div className={COPILOT_TOKENS.review.action.bar}>
        <button
          ref={confirmRef}
          type="button"
          className={
            kind === "send"
              ? COPILOT_TOKENS.review.action.primary
              : COPILOT_TOKENS.review.action.danger
          }
          aria-disabled={busy}
          onClick={() => {
            if (!busy) onConfirm();
          }}
        >
          {copy.confirm}
        </button>
        {/* "Keep editing", not "Cancel" -- on the cancel confirmation, a button
            labelled "Cancel" would be asking whether to cancel the cancel. */}
        <button type="button" className={COPILOT_TOKENS.review.action.secondary} onClick={onCancel}>
          Keep editing
        </button>
      </div>
    </div>
  );
}
