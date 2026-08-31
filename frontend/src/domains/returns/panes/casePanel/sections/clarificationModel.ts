import type { CasePanelView, PanelSectionView } from "../../../../../api/casePanel";

/**
 * Reading the clarifications off the panel, and nothing else.
 *
 * Separated from the component because two different things are going on and
 * only one of them is React: **deciding what a clarification is** is a contract
 * question with a fact-shaped answer, and it wants tests that render nothing.
 *
 * The shape is the value of the `support_clarification_requested` fact, written
 * by `operations/return_support/message_classification.py`. Its keys are
 * camelCase because that is how facts are stored, and they are transcribed here
 * rather than generated because there is nothing to generate from and there is
 * not meant to be: a contributed section's payload is an opaque JSON object
 * precisely so V3's shape never enters V1's DTO (V1 phase 2 handoff, sect. 2),
 * so the document types it as a free-form object. This is *not* the transcribing
 * `api/caseClarifications.ts` used to do -- that shape was published by the
 * answer route and is generated now; this one never will be.
 *
 * ---
 *
 * ## Where clarifications come from — one vehicle, since AMENDMENT-6
 *
 * §9 used to name **two** vehicles that did not agree with each other: the
 * clarification reaches the console as a *panel section*, and `clarifications[]`
 * was also declared on the panel DTO, which V1 phase 2's handoff repeated in its
 * frozen-DTO table — "arrive through the section registry", of a **top-level
 * field**.
 *
 * Only one of those was ever buildable. `register_panel_section`'s contributor
 * Protocol returns `PanelSectionView | None` and has no way to write a top-level
 * DTO field, so `api/case_panel.py` set `clarifications=()` as a literal and no
 * slice could have changed that. The first draft of this file read **only**
 * `panel.clarifications` and would therefore have rendered nothing, ever, while
 * every test that handed it a fabricated panel stayed green — the
 * consumer-tested-against-a-synthetic-producer shape, exactly.
 *
 * This file then read both vehicles, de-duplicated, on the reasoning that
 * whichever one the integration pass wired, the section would draw. AMENDMENT-6
 * settled it the other way: the DTO field is **retired**, not filled, because a
 * second parallel path the seam cannot reach is the defect. So the field half is
 * gone and the section payload is the whole source.
 *
 * The de-duplication stayed. It is no longer about two vehicles disagreeing —
 * it is that one payload carrying an id twice must still draw one card, and
 * `readClarifications`' order guarantee is what keeps the list from shuffling.
 */

/** The section id V3 contributes under, on both sides of the seam. */
export const CLARIFICATIONS_SECTION_ID = "clarifications";

export type ResolutionChoiceKind = "MAP_OR_REJECT" | null;

export type CaseClarification = {
  readonly clarificationId: string;
  /**
   * **Verbatim.** Sect. 9 requires the question to reach the associate as
   * Support wrote it -- see `ClarificationsSection.tsx` on why this is escaped
   * at the DOM and never rewritten on the way.
   */
  readonly verbatimQuestion: string;
  readonly whyUnresolvable: string;
  readonly neededField: string;
  readonly resolutionAttempts: readonly string[];
  readonly choice: ResolutionChoiceKind;
  /** The loose artifact's own value. Support-derived, and the sharpest input. */
  readonly artifactValue: string | null;
  /** What the message named, rather than what it said. Also support-derived. */
  readonly evidenceSpan: string | null;
  readonly candidateRecordIds: readonly string[];
  readonly supportEventId: string | null;
};

function text(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function strings(value: unknown): readonly string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/**
 * One raw entry, or `null` if it is not one.
 *
 * **A malformed entry is skipped, never thrown on.** The backend registry
 * catches a contributor that raises and degrades that section rather than taking
 * the panel down, because the reviews are what an associate is blocked on (V1
 * phase 2 handoff, sect. 2); the console half of that promise is this function.
 * An entry with no id and no question is not a clarification anybody can answer,
 * so drawing an empty card for it would be furniture.
 */
export function readClarification(raw: unknown): CaseClarification | null {
  if (typeof raw !== "object" || raw === null) return null;
  const held = raw as Record<string, unknown>;
  const clarificationId = text(held.clarificationId);
  const verbatimQuestion = text(held.verbatimQuestion);
  if (clarificationId === null || verbatimQuestion === null) return null;
  return {
    clarificationId,
    verbatimQuestion,
    whyUnresolvable: text(held.whyUnresolvable) ?? "",
    neededField: text(held.neededField) ?? "",
    resolutionAttempts: strings(held.resolutionAttempts),
    choice: held.choice === "MAP_OR_REJECT" ? "MAP_OR_REJECT" : null,
    artifactValue: text(held.artifactValue),
    evidenceSpan: text(held.evidenceSpan),
    candidateRecordIds: strings(held.candidateRecordIds),
    supportEventId: text(held.supportEventId),
  };
}

/**
 * Every clarification the contributed section carries, in the order it carries
 * them.
 *
 * First writer of an id wins, so a payload that names the same clarification
 * twice draws one card — and the order does not shuffle, because skipping a
 * repeat never reorders what is already in the list.
 *
 * `panel` stays in the signature deliberately, unread. It is what lets
 * `clarificationModel.test.ts`'s retirement guard hand this function a panel
 * that still carries a top-level `clarifications` key — an older release, or a
 * server that has not been redeployed — and assert that **nothing** is drawn
 * from it. Drop the parameter and that assertion becomes unwritable, which
 * would leave AMENDMENT-6 with no watcher on the console side.
 */
export function readClarifications(
  panel: CasePanelView,
  section: PanelSectionView | undefined,
): readonly CaseClarification[] {
  void panel;
  const payload = section?.payload as { clarifications?: unknown } | undefined;
  const held: unknown = payload?.clarifications;
  const fromSection: readonly unknown[] = Array.isArray(held) ? (held as readonly unknown[]) : [];
  const seen = new Set<string>();
  const found: CaseClarification[] = [];
  for (const raw of fromSection) {
    const clarification = readClarification(raw);
    if (clarification === null || seen.has(clarification.clarificationId)) continue;
    seen.add(clarification.clarificationId);
    found.push(clarification);
  }
  return found;
}

/* -------------------------------------------------------------------------
 * The candidates
 * ---------------------------------------------------------------------- */

export type CandidateRecord = {
  readonly returnRecordId: string;
  /** The RMA Support issued. This is what an associate actually recognises. */
  readonly returnReference: string;
  readonly status: string;
  readonly returnMethod: string;
};

/**
 * The case's records, in the order the clarification named them.
 *
 * The clarification carries **ids**; the panel carries the narrow record
 * projection with the reference, status and method (V1 phase 2 handoff, sect.
 * 2's `return_records[]` row). Joining them here is why the renderer takes the
 * whole panel: an id is not a thing anybody at a counter can recognise, and
 * asking somebody to pick between two opaque uuids is asking them to guess.
 *
 * A candidate id with **no** record on the panel is still offered, labelled by
 * its id. Dropping it would silently shorten the list of things the artifact
 * could belong to, and a shorter list is one an associate is likelier to answer
 * confidently and wrongly.
 */
export function candidateRecords(
  clarification: CaseClarification,
  panel: CasePanelView,
): readonly CandidateRecord[] {
  const byId = new Map<string, CandidateRecord>();
  for (const raw of panel.return_records) {
    const held = raw as Record<string, unknown>;
    const returnRecordId = text(held.return_record_id);
    if (returnRecordId === null) continue;
    byId.set(returnRecordId, {
      returnRecordId,
      returnReference: text(held.return_reference) ?? "",
      status: text(held.status) ?? "",
      returnMethod: text(held.return_method) ?? "",
    });
  }
  return clarification.candidateRecordIds.map(
    (id) =>
      byId.get(id) ?? { returnRecordId: id, returnReference: "", status: "", returnMethod: "" },
  );
}

/* -------------------------------------------------------------------------
 * Saying what was tried, in words
 * ---------------------------------------------------------------------- */

/**
 * The ladder rungs and binding statuses, in an associate's language.
 *
 * Two producers write `resolutionAttempts` and they speak different
 * vocabularies. The artifact binder writes a `BindingStatus` — today's only
 * producer, `message_classification.py`, writes exactly `[decision.status.value]`.
 * The resolution ladder writes the rungs it climbed, whose constants are
 * `RUNG_FACTS = "case_facts"`, `RUNG_GRAPH = "graph"` and
 * `RUNG_TOOL = "registered_tool"` in `resolution_state.py`.
 *
 * **Both tables are the literal backend strings.** The first draft of this file
 * invented `facts`, `tools` and `clarification`, none of which any producer
 * writes. Because an unrecognised value falls through to itself — deliberately —
 * that mistake would have shown raw enum values to associates forever without
 * producing a single wrong word anybody could report.
 *
 * The fall-through stays. It hides a rung a later release adds from exactly the
 * person being asked to compensate for it, which is the `Pending`-word
 * discipline `ReturnCopilotFabrication.test.ts` enforces applied to a vocabulary
 * rather than to a value: do not invent a word for something the platform said
 * in words of its own.
 */
const ATTEMPT_WORDS: Record<string, string> = {
  AMBIGUOUS: "matched more than one of this case's returns",
  UNMATCHED: "named a return this case does not hold",
  BOUND: "matched one of this case's returns",
  case_facts: "looked through what the case already knows",
  graph: "looked it up in the knowledge graph",
  registered_tool: "asked a system it is allowed to ask",
};

export function attemptWords(attempt: string): string {
  return ATTEMPT_WORDS[attempt] ?? attempt;
}

/**
 * The field the platform needs, in an associate's language.
 *
 * `neededField` is an `ArtifactType` on the binding path (`TRACKING_NUMBER`,
 * `SHIPPING_LABEL`, …) and a resolver field name on the other. Same
 * fall-through rule, same reason — and the transformation is mechanical rather
 * than a lookup table, so a type added next release reads correctly without this
 * file being edited.
 */
export function neededFieldWords(neededField: string): string {
  return neededField.replaceAll("_", " ").toLowerCase();
}
