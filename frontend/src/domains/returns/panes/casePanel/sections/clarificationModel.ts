import type { CasePanelView } from "../../../../../api/casePanel";

/**
 * Reading the clarifications off the panel, and nothing else.
 *
 * Separated from the component because two different things are going on and
 * only one of them is React: **deciding what a clarification is** is a contract
 * question with a fact-shaped answer, and it wants tests that do not render
 * anything.
 *
 * The shape is the value of the `support_clarification_requested` fact, written
 * by `operations/return_support/message_classification.py`. Its keys are
 * camelCase because that is how facts are stored, and they are transcribed here
 * rather than generated for the reason `api/caseClarifications.ts` gives: the
 * panel declares `clarifications` as an untyped object list precisely so V3's
 * shape never enters V1's DTO (V1-phase2 handoff, sect. 2).
 */

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
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/**
 * One raw entry, or `null` if it is not one.
 *
 * **A malformed entry is skipped, never thrown on.** The backend registry
 * catches a contributor that raises and degrades that section rather than
 * taking the panel down, because the reviews are what an associate is blocked
 * on (V1-phase2 handoff, sect. 2); the console half of that promise is this
 * function. An entry with no id and no question is not a clarification anybody
 * can answer, so drawing an empty card for it would be furniture.
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

export function readClarifications(panel: CasePanelView): readonly CaseClarification[] {
  return panel.clarifications
    .map((entry) => readClarification(entry))
    .filter((entry): entry is CaseClarification => entry !== null);
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
 * projection with the reference, status and method (V1-phase2 handoff, sect.
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
 * vocabularies: the artifact binder writes a `BindingStatus`
 * (`AMBIGUOUS`/`UNMATCHED`), and the resolution ladder writes the rungs it
 * climbed. Both end up in the same list, so both are translated here.
 *
 * **An unrecognised value falls through to itself**, rendered as data. The
 * alternative -- "unknown step" -- would hide a rung a later release added from
 * exactly the person who is being asked to compensate for it, and this is the
 * `Pending`-word discipline `ReturnCopilotFabrication.test.ts` enforces applied
 * to a vocabulary rather than a value: do not invent a word for something the
 * platform said in words of its own.
 */
const ATTEMPT_WORDS: Record<string, string> = {
  AMBIGUOUS: "matched more than one of this case's returns",
  UNMATCHED: "named a return this case does not hold",
  BOUND: "matched one of this case's returns",
  facts: "looked through what the case already knows",
  graph: "looked it up in the knowledge graph",
  tools: "asked the systems it is allowed to ask",
  clarification: "came here",
};

export function attemptWords(attempt: string): string {
  return ATTEMPT_WORDS[attempt] ?? attempt;
}

/**
 * The field the platform needs, in an associate's language.
 *
 * `neededField` is an `ArtifactType` on the binding path
 * (`TRACKING_NUMBER`, `SHIPPING_LABEL`, …) and a resolver field name on the
 * other. Same fall-through rule, same reason.
 */
export function neededFieldWords(neededField: string): string {
  return neededField.replaceAll("_", " ").toLowerCase();
}
