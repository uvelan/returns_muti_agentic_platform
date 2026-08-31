/**
 * What a clarification is, decided without rendering anything.
 *
 * The test this file exists for is the first one: **the source**. The abandoned
 * first draft read `panel.clarifications`, which `api/case_panel.py` set to `()`
 * as a literal and which no registered contributor could write, so the section
 * would have drawn nothing on every real panel while a suite full of hand-built
 * panel objects stayed green. That is the consumer-tested-against-a-synthetic-
 * producer shape, and the only assertion that catches it is one that hands the
 * reader a **section** and no top-level field.
 *
 * AMENDMENT-6 has since retired that field from `CasePanelView` outright, so the
 * second test no longer checks the other vehicle — it checks that there **is**
 * no other vehicle, against a panel body that still carries the retired key.
 */

import { describe, expect, it } from "vitest";

import type { CasePanelView } from "../../../../../api/casePanel";
import {
  attemptWords,
  candidateRecords,
  neededFieldWords,
  readClarification,
  readClarifications,
} from "./clarificationModel";

describe("where a clarification comes from", () => {
  it("reads the contributed section payload — the only vehicle that can carry one", () => {
    // `register_panel_section`'s contributor returns `PanelSectionView | None`
    // and cannot write a top-level DTO field. A reader that only knew about
    // `panel.clarifications` would return [] here and nothing would render.
    const found = readClarifications(panel(), section([raw()]));
    expect(found.map((entry) => entry.clarificationId)).toEqual(["clar-1"]);
  });

  it("draws nothing from a top-level clarifications field, retired by AMENDMENT-6", () => {
    // This test used to be "still reads the DTO field, in case the integration
    // pass wires that instead". The integration pass went the other way:
    // AMENDMENT-6 retired `CasePanelView.clarifications` rather than teaching
    // the composer to fill it, because a contributor returns a
    // `PanelSectionView | None` and can never write a top-level field.
    //
    // So the assertion is inverted rather than dropped, and it is a real one.
    // The cast is the point: the field is gone from the generated type, so the
    // only way to produce this input is a server that has not been redeployed —
    // exactly the case where a re-added `...panel.clarifications` read would
    // start drawing cards again and nobody would notice. If someone restores
    // that read, this goes red.
    const stale = { ...panel(), clarifications: [raw()] } as unknown as CasePanelView;
    expect(readClarifications(stale, undefined)).toEqual([]);
    expect(readClarifications(stale, section([]))).toEqual([]);
  });

  it("draws one card when the section names the same clarification twice", () => {
    // The de-duplication predates the amendment, where it reconciled two
    // vehicles. It still has a job with one: a contributor that appends a
    // clarification it already listed must not double the card.
    const found = readClarifications(panel(), section([raw(), raw()]));
    expect(found.map((entry) => entry.clarificationId)).toEqual(["clar-1"]);
  });

  it("keeps the section's own order, and the first mention fixes an id's place", () => {
    const found = readClarifications(
      panel(),
      section([
        raw({ clarificationId: "clar-1" }),
        raw({ clarificationId: "clar-2" }),
        raw({ clarificationId: "clar-1" }),
        raw({ clarificationId: "clar-3" }),
      ]),
    );
    // `clar-1` keeps its first position rather than being moved to where the
    // repeat appeared: skipping a duplicate must not reorder the list, or the
    // cards would shuffle under an associate between two polls.
    expect(found.map((entry) => entry.clarificationId)).toEqual(["clar-1", "clar-2", "clar-3"]);
  });

  it("finds nothing when there is nothing, from either side", () => {
    expect(readClarifications(panel(), undefined)).toEqual([]);
    expect(readClarifications(panel(), section([]))).toEqual([]);
    expect(readClarifications(panel(), { section_id: "clarifications", status: "ok" } as never))
      .toEqual([]);
  });
});

describe("reading one entry", () => {
  it("reads every field the fact carries, exactly as it carries them", () => {
    // Pinned whole, as an equality. `message_classification.py` writes these ten
    // keys; a field silently dropped by a typo would still pass a
    // field-by-field spot check of the three the card happens to assert on.
    expect(readClarification(raw())).toEqual({
      clarificationId: "clar-1",
      verbatimQuestion:
        "Support gave a tracking number (1Z999AA10123456784) for a return this case does not hold. Map it to one of this case's returns, or reject it.",
      whyUnresolvable: "the named return reference is not on this case",
      neededField: "TRACKING_NUMBER",
      resolutionAttempts: ["UNMATCHED"],
      choice: "MAP_OR_REJECT",
      artifactValue: "1Z999AA10123456784",
      evidenceSpan: "RMA-99999",
      candidateRecordIds: ["rec-1", "rec-2"],
      supportEventId: "evt-12",
    });
  });

  it("skips an entry nobody could answer rather than drawing an empty card", () => {
    // The console half of the backend registry's promise: a contributor that
    // raises degrades its section and does not take the panel down.
    expect(readClarification(null)).toBeNull();
    expect(readClarification("clar-1")).toBeNull();
    expect(readClarification({ clarificationId: "clar-1" })).toBeNull();
    expect(readClarification({ verbatimQuestion: "which return?" })).toBeNull();
  });

  it("does not let a malformed entry hide the answerable ones beside it", () => {
    const found = readClarifications(panel(), section([{ nonsense: true }, raw(), null]));
    expect(found.map((entry) => entry.clarificationId)).toEqual(["clar-1"]);
  });

  it("treats a non-MAP_OR_REJECT choice as no choice at all", () => {
    // A card that offered radios because a future release wrote a choice kind
    // this build does not know would be offering a binding it cannot express.
    expect(readClarification(raw({ choice: "SOMETHING_NEW" }))?.choice).toBeNull();
    expect(readClarification(raw({ choice: undefined }))?.choice).toBeNull();
  });
});

describe("the candidates an associate picks between", () => {
  it("joins the ids to the records, in the order the clarification named them", () => {
    expect(candidateRecords(mustRead(raw()), panel())).toEqual([
      {
        returnRecordId: "rec-1",
        returnReference: "RMA-88120",
        status: "OPEN",
        returnMethod: "PARCEL",
      },
      {
        returnRecordId: "rec-2",
        returnReference: "RMA-88121",
        status: "OPEN",
        returnMethod: "PALLET",
      },
    ]);
  });

  it("still offers a candidate the panel has no record for", () => {
    // Dropping it would silently shorten the list of things the artifact could
    // belong to, and a shorter list is one an associate answers confidently and
    // wrongly.
    const clarification = mustRead(raw({ candidateRecordIds: ["rec-1", "rec-ghost"] }));
    expect(candidateRecords(clarification, panel()).map((c) => c.returnRecordId)).toEqual([
      "rec-1",
      "rec-ghost",
    ]);
  });
});

describe("saying what was tried, in words", () => {
  it("speaks both producers' vocabularies, using their literal strings", () => {
    // `BindingStatus` from the artifact binder; `RUNG_*` from
    // `resolution_state.py`. The first draft invented `facts` and `tools`.
    expect(attemptWords("UNMATCHED")).toBe("named a return this case does not hold");
    expect(attemptWords("AMBIGUOUS")).toBe("matched more than one of this case's returns");
    expect(attemptWords("BOUND")).toBe("matched one of this case's returns");
    expect(attemptWords("case_facts")).toBe("looked through what the case already knows");
    expect(attemptWords("graph")).toBe("looked it up in the knowledge graph");
    expect(attemptWords("registered_tool")).toBe("asked a system it is allowed to ask");
  });

  it("passes an unrecognised step through rather than calling it unknown", () => {
    expect(attemptWords("A_RUNG_ADDED_NEXT_YEAR")).toBe("A_RUNG_ADDED_NEXT_YEAR");
  });

  it("reads an artifact type as words without a table to keep in step", () => {
    expect(neededFieldWords("TRACKING_NUMBER")).toBe("tracking number");
    expect(neededFieldWords("SHIPPING_LABEL")).toBe("shipping label");
    expect(neededFieldWords("A_TYPE_ADDED_NEXT_YEAR")).toBe("a type added next year");
  });
});

/* ---------------------------------------------------------------------------
 * Fixtures
 * ------------------------------------------------------------------------ */

/** Exactly the value `message_classification.py` writes for the fact. */
function raw(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    clarificationId: "clar-1",
    verbatimQuestion:
      "Support gave a tracking number (1Z999AA10123456784) for a return this case does not hold. Map it to one of this case's returns, or reject it.",
    whyUnresolvable: "the named return reference is not on this case",
    neededField: "TRACKING_NUMBER",
    resolutionAttempts: ["UNMATCHED"],
    supportEventId: "evt-12",
    artifactValue: "1Z999AA10123456784",
    evidenceSpan: "RMA-99999",
    candidateRecordIds: ["rec-1", "rec-2"],
    choice: "MAP_OR_REJECT",
    ...overrides,
  };
}

/**
 * `readClarification` returning `null` here would be a fixture bug, not a
 * finding -- but a bare `!` hides which of the two it was when it happens.
 */
function mustRead(entry: Record<string, unknown>) {
  const clarification = readClarification(entry);
  if (clarification === null) throw new Error("fixture is not a readable clarification");
  return clarification;
}

function section(clarifications: unknown[]) {
  return {
    section_id: "clarifications",
    status: "ok",
    reason: null,
    payload: { clarifications },
  } as never;
}

/**
 * A panel with no clarifications on it, because since AMENDMENT-6 there is no
 * top-level field for them to sit in. The retirement guard above builds its
 * stale-server input by spreading this and adding the retired key back.
 */
function panel(): CasePanelView {
  return {
    return_records: [
      {
        return_record_id: "rec-1",
        return_reference: "RMA-88120",
        status: "OPEN",
        return_method: "PARCEL",
      },
      {
        return_record_id: "rec-2",
        return_reference: "RMA-88121",
        status: "OPEN",
        return_method: "PALLET",
      },
    ],
  } as never;
}
