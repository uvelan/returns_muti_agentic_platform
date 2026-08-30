import type { ReviewPanelView } from "../../../../../api/casePanel";

/**
 * A `SUPPORT_REPLY` review's draft, read off the payload the gate wrote.
 *
 * ---
 *
 * **Why this file exists at all.** `TemplateReviewSection` draws
 * `payload.subject` and iterates `payload.sections[]`, because that is the shape
 * `render_configured_template` produces for a `TEMPLATE` review. A
 * `SUPPORT_REPLY` review's payload has neither field: `reply_gating.py` writes
 * `messageText`, `disclosesAgent`, `supportEventId`, `intent`,
 * `confidenceMillionths`, `resolvedByRung`, `citedFactIds`, `consumedFactIds`
 * and `contextHash`.
 *
 * Rendered through the template shape, a gated reply therefore showed a subject
 * of "Pending", no body, and a Send button. The associate was being asked to
 * approve a message to a supplier that the screen did not contain. Sect. 6 makes
 * the review the gate; a gate nobody can see through is not a gate.
 *
 * Kept out of the component for `clarificationModel.ts`'s reason: **what a reply
 * draft is** is a contract question with a fact-shaped answer, and it wants
 * tests that render nothing.
 *
 * Transcribed rather than generated, like the other V3 payload shapes: the
 * review's `draft` is an opaque object on the wire precisely so V3's shape never
 * enters V1's DTO (V1 phase 2 handoff, sect. 2).
 */

export const SUPPORT_REPLY_KIND = "SUPPORT_REPLY";

export type SupportReplyDraft = {
  /**
   * What would be sent to Support, verbatim.
   *
   * **Support-derived in part** -- `compose_reply` interpolates the resolver's
   * answer and the verbatim question into a platform-composed frame. It reaches
   * the DOM as a React text child, and `SupportReplyBody.tsx` says why that is
   * the whole of the defence.
   */
  readonly messageText: string;
  /** Whether the composed text carries the configured agent-disclosure line. */
  readonly disclosesAgent: boolean;
  readonly intent: string;
  /** Which rung of the ladder answered. `resolution_state.py`'s vocabulary. */
  readonly resolvedByRung: string;
  /** Millionths, as the resolver records them. `null` when it did not say. */
  readonly confidenceMillionths: number | null;
  readonly citedFactIds: readonly string[];
  readonly supportEventId: string;
};

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): readonly string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/**
 * The **kind** decides, never the shape.
 *
 * A template review whose payload happened to carry a `messageText` key must
 * still draw as a template; sniffing the payload would make a future template
 * field silently change how every template renders.
 */
export function isSupportReply(review: ReviewPanelView): boolean {
  return review.review_kind === SUPPORT_REPLY_KIND;
}

/**
 * The draft, or `null` when this review is not a reply.
 *
 * A reply whose `messageText` is empty still returns a draft rather than
 * `null` -- the component then says so in words. Returning `null` there would
 * fall the review back to the template renderer, which is the exact silence this
 * file was written to end.
 */
export function readSupportReplyDraft(review: ReviewPanelView): SupportReplyDraft | null {
  if (!isSupportReply(review)) return null;
  const payload = review.draft as Record<string, unknown>;
  const confidence = payload.confidenceMillionths;
  return {
    messageText: text(payload.messageText),
    disclosesAgent: payload.disclosesAgent === true,
    intent: text(payload.intent),
    resolvedByRung: text(payload.resolvedByRung),
    confidenceMillionths: typeof confidence === "number" ? confidence : null,
    citedFactIds: strings(payload.citedFactIds),
    supportEventId: text(payload.supportEventId),
  };
}

/**
 * The rung that answered, in an associate's language.
 *
 * The three keys are `RUNG_FACTS`, `RUNG_GRAPH` and `RUNG_TOOL` in
 * `operations/return_support/resolution_state.py` -- the literal strings, not a
 * paraphrase of them. The first draft of this file (and of
 * `clarificationModel.ts`) invented `facts` and `tools`, which match nothing the
 * backend writes and would have fallen through to the raw value forever without
 * anybody noticing, because the fall-through is silent by design.
 *
 * **An unrecognised rung falls through to itself.** A later release adding a
 * rung must not have it hidden behind "unknown step" from the person being asked
 * to trust the answer -- the same discipline `ReturnCopilotFabrication.test.ts`
 * enforces on values, applied to a vocabulary.
 */
const RUNG_WORDS: Record<string, string> = {
  case_facts: "from what this case already knows",
  graph: "from the knowledge graph",
  registered_tool: "from a system it is allowed to ask",
};

export function rungWords(rung: string): string {
  return RUNG_WORDS[rung] ?? rung;
}

/**
 * Confidence as a percentage, or `null` when the resolver did not record one.
 *
 * Shown rather than hidden, and shown as the resolver's own number rather than
 * as a word: "high confidence" is this console inventing a threshold, and the
 * thresholds are `fact_confidence_millionths` / `graph_confidence_millionths` in
 * the release, not here.
 */
export function confidencePercent(millionths: number | null): string | null {
  if (millionths === null) return null;
  return `${String(Math.round(millionths / 10_000))}%`;
}
