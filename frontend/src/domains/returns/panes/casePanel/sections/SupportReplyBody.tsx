import { BadgeCheck } from "lucide-react";

import { COPILOT_TOKENS } from "../../../copilotTokens";
import type { DraftEditor } from "../useDraftEditor";
import {
  confidencePercent,
  EMPTY_REPLY_NOTICE,
  rungWords,
  type SupportReplyDraft,
} from "./supportReplyDraft";

/**
 * The body of a `SUPPORT_REPLY` review — the thing the template renderer could
 * not draw.
 *
 * Everything around it is V1's: the state badge, the live region, the conflict
 * and superseded banners, the action bar, the confirmations, the approval hash
 * and its CAS. Item 6 asks for reply reviews to render *through* V1's review
 * components, and this is the one part that could not be shared, because the
 * two payload shapes have no field in common.
 *
 * ---
 *
 * ## The reply text is data, never markup
 *
 * `messageText` is `compose_reply`'s output: a platform-composed frame around
 * the resolver's answer, and the resolver answered a question Support wrote.
 * Support does not author the frame, but its words reach this string, so this is
 * the same inbound-to-associate exposure the clarification card has and it gets
 * the same treatment — a React text child, escaped by the renderer, with no
 * `dangerouslySetInnerHTML`, no markdown pass and no `innerHTML` on this
 * surface. `SupportReplyReview.test.tsx` pins the **whole** rendered text as an
 * equality rather than asserting a tag is absent.
 *
 * `whitespace-pre-wrap` **is** used here, unlike the clarification quote, and
 * the difference is deliberate. This text is a message about to be sent: its
 * paragraph breaks are the sender's own and hiding them would show the associate
 * something other than what leaves the building. The framing risk the
 * clarification quote guards against does not apply, because this string is not
 * a question presented as somebody else's speech — it is the draft under review,
 * and every character of it is up for editing.
 */

export function SupportReplyBody({
  draft,
  editor,
  editable,
  fieldId,
}: {
  readonly draft: SupportReplyDraft;
  readonly editor: DraftEditor;
  readonly editable: boolean;
  /** The section's `useId`, so label/control association is unique per review. */
  readonly fieldId: string;
}) {
  const confidence = confidencePercent(draft.confidenceMillionths);
  /*
   * The edited text, or the composed one. Seeding from `messageText` rather
   * than from `""` is the fix: the template section's raw-write box seeds from
   * `bodyOverride ?? ""`, which for a reply is an empty box beside no other
   * copy of the message at all.
   */
  const value = editor.bodyOverride ?? draft.messageText;

  return (
    <div className="space-y-3">
      <div>
        <label htmlFor={`${fieldId}-reply`} className={COPILOT_TOKENS.review.field.label}>
          The reply
        </label>
        {editable ? (
          <textarea
            id={`${fieldId}-reply`}
            value={value}
            rows={6}
            aria-describedby={`${fieldId}-reply-hint`}
            className={`${COPILOT_TOKENS.review.field.input} mt-1 ${
              editor.bodyOverride === null ? "" : COPILOT_TOKENS.review.field.edited
            }`}
            onChange={(event) => {
              /*
               * Emptied back to the composed text, not to `null`: `null` means
               * "no override" and would silently restore the draft under the
               * associate's cursor. Clearing the box is a decision to send
               * nothing, and the send path refuses that on its own.
               */
              editor.setBodyOverride(event.target.value);
            }}
          />
        ) : (
          <p
            id={`${fieldId}-reply`}
            className={`${COPILOT_TOKENS.review.field.value} mt-1 whitespace-pre-wrap`}
          >
            {draft.messageText === "" ? EMPTY_REPLY_NOTICE : draft.messageText}
          </p>
        )}
        <p id={`${fieldId}-reply-hint`} className={`${COPILOT_TOKENS.typography.caption} mt-1`}>
          Support sees exactly this. Nothing else on this screen is sent.
        </p>
      </div>

      {/*
        Provenance, on the same footing as a template field's. An associate
        deciding whether to send an answer needs to know which rung produced it:
        a graph read and a confirmed case fact are different amounts of trust,
        and §8 makes that part of the contract rather than a nicety.
      */}
      <p className="flex flex-wrap items-center gap-1.5">
        <span className={COPILOT_TOKENS.review.provenance}>
          {draft.resolvedByRung === "" ? "source not recorded" : rungWords(draft.resolvedByRung)}
        </span>
        {confidence === null ? null : (
          <span className={COPILOT_TOKENS.review.provenance}>{confidence} confident</span>
        )}
        {draft.citedFactIds.length > 0 ? (
          <span className={COPILOT_TOKENS.review.provenance}>
            {draft.citedFactIds.length === 1
              ? "1 case fact cited"
              : `${String(draft.citedFactIds.length)} case facts cited`}
          </span>
        ) : null}
        {draft.disclosesAgent ? (
          <span className={COPILOT_TOKENS.review.provenance}>
            <BadgeCheck aria-hidden="true" className="size-3" />
            says it is from the platform
          </span>
        ) : null}
      </p>

      {/*
        Not a decoration and not a nag. The disclosure line is a configured
        `agent_disclosure` string; a reply that lost it — because somebody
        rewrote the box above and deleted it — goes out as though a person at
        this company wrote it. Saying so at the point of editing is the only
        place it helps.
      */}
      {draft.disclosesAgent && editable && editor.bodyOverride !== null ? (
        /*
          `conflict` (tertiary), not `gap` (error). `gap` is documented as "a
          required detail the case cannot answer", and this is not that: the
          reply is sendable, and this is a caution about one line of it. Dressing
          a caution as an error is how people learn to send past errors.
        */
        <p className={COPILOT_TOKENS.review.conflict}>
          Keep the line saying this reply came from the platform. Support is told when an answer
          was not hand-written.
        </p>
      ) : null}
    </div>
  );
}
