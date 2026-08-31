import { describe, expect, it } from "vitest";

import type { PanelSectionView } from "../../../../../api/casePanel";
import {
  SUPPORT_SECTION_IDS,
  SUPPORT_SECTION_ORDER,
  displayText,
  isDegraded,
  readBoolean,
  readCount,
  readDigestPayload,
  readObject,
  readParkedPayload,
  readRecordsPayload,
  readString,
} from "./supportPanelPayloads";

/**
 * The readers between an opaque payload and anything drawable.
 *
 * Two things are being proved here and they are different in kind. One is that
 * a payload from a server this bundle has never met cannot make the console
 * throw, drop a record, or invent one. The other is dispatch condition 10's
 * *layout* half: a support-derived value must not be able to draw itself as a
 * second labelled row. The markup half belongs to the renderers and is proved
 * against the DOM in `supportSections.test.tsx`; this file proves the value is
 * carried through unchanged apart from its whitespace, which is the half a DOM
 * test cannot distinguish from a value that was never rendered at all.
 *
 * Assertions are whole-object equalities wherever the shape allows it. A
 * "does not contain a newline" assertion passes just as happily against a
 * reader that returned `null`, and that is the failure mode this module is most
 * likely to develop.
 */

function section(payload: Record<string, unknown>, status = "ok"): PanelSectionView {
  return { section_id: SUPPORT_SECTION_IDS.records, payload, status, reason: null };
}

/** A tracking-shaped value that also tries to draw a row of its own. */
const FRAMED = "9Z41 \n\t RETURN LOCATION: dock four";
const FRAMED_FLAT = "9Z41 RETURN LOCATION: dock four";

describe("reading a string out of an opaque payload", () => {
  it("collapses every run of whitespace, in whichever field it arrives", () => {
    // Pinned as an exact string, not as an absence of `\n`. The value has to
    // survive -- an associate reads it down a phone -- so "no newline" and "no
    // value" must not both pass.
    expect(readString({ value: FRAMED }, "value")).toBe(FRAMED_FLAT);
  });

  it("has no second door that skips the collapse", () => {
    // The defect this replaced: the collapse applied at four chosen call sites
    // while `readString` returned raw text, so every field added afterwards was
    // uncollapsed by default. Proved by reading a field the module treats as a
    // closed backend enum -- exactly the kind nobody thinks to collapse.
    expect(readString({ artifactType: "RMA\nPOLICY: none" }, "artifactType")).toBe(
      "RMA POLICY: none",
    );
    expect(readString({ status: " BOUND \n " }, "status")).toBe("BOUND");
  });

  it("carries a script tag through as characters, changing nothing but the spacing", () => {
    // The reader must not sanitise, strip or encode: the escaping happens in
    // the DOM, and a reader that silently rewrote the value would hide from an
    // associate what Support actually sent.
    const hostile = "<img src=x onerror=alert(1)>";
    expect(readString({ value: hostile }, "value")).toBe(hostile);
  });

  it("is absent rather than empty for a blank, a number or a missing key", () => {
    expect(readString({ value: "   \n  " }, "value")).toBeNull();
    expect(readString({ value: 7 }, "value")).toBeNull();
    expect(readString({}, "value")).toBeNull();
    expect(readString(null, "value")).toBeNull();
    expect(readString([{ value: "x" }], "value")).toBeNull();
  });

  it("passes null through as null", () => {
    expect(displayText(null)).toBeNull();
    expect(displayText("  ")).toBeNull();
  });
});

describe("reading a count, a flag and an object", () => {
  it("takes only a non-negative integer as a count", () => {
    expect(readCount({ n: 3 }, "n")).toBe(3);
    expect(readCount({ n: 0 }, "n")).toBe(0);
    // A coerced string is how "0" becomes 0 and "" becomes 0 -- a parked count
    // of nothing where the truth was a payload this console could not read.
    expect(readCount({ n: "3" }, "n")).toBeNull();
    expect(readCount({ n: -1 }, "n")).toBeNull();
    expect(readCount({ n: 1.5 }, "n")).toBeNull();
    expect(readCount({ n: Number.NaN }, "n")).toBeNull();
    expect(readCount({ n: Number.POSITIVE_INFINITY }, "n")).toBeNull();
  });

  it("takes only a real boolean as a flag", () => {
    expect(readBoolean({ f: false }, "f")).toBe(false);
    expect(readBoolean({ f: "false" }, "f")).toBeNull();
    expect(readBoolean({}, "f")).toBeNull();
  });

  it("reads a single-valued group whether it arrives as an object or a one-element list", () => {
    expect(readObject({ p: { bayId: "b" } }, "p")).toEqual({ bayId: "b" });
    expect(readObject({ p: [{ bayId: "b" }] }, "p")).toEqual({ bayId: "b" });
    // Two placements is not a placement this console can draw as one, and
    // picking the first would be inventing which bay the goods are in.
    expect(readObject({ p: [{ bayId: "b" }, { bayId: "c" }] }, "p")).toBeNull();
    expect(readObject({ p: "b" }, "p")).toBeNull();
  });
});

describe("the return-records section", () => {
  const panelRecords = [
    { return_record_id: "rec-1", return_reference: "REF-ONE", status: "OPEN", return_method: "PARCEL" },
    { return_record_id: "rec-2", return_reference: null, status: "OPEN", return_method: null },
  ];

  it("draws a card for every record on the panel, including one Support has said nothing about", () => {
    const payload = readRecordsPayload(
      section({
        records: [
          {
            returnRecordId: "rec-1",
            artifacts: [{ artifactType: "TRACKING", value: FRAMED, status: "BOUND" }],
          },
        ],
      }),
      panelRecords,
    );

    // Whole shape, not a spot check: a record nobody has sent an artifact for
    // yet is precisely the record an associate is waiting on, and a reader that
    // dropped it would still satisfy every assertion about rec-1.
    expect(payload.records).toEqual([
      {
        returnRecordId: "rec-1",
        returnReference: "REF-ONE",
        status: "OPEN",
        returnMethod: "PARCEL",
        artifacts: [
          {
            artifactType: "TRACKING",
            label: "Tracking",
            value: FRAMED_FLAT,
            status: "BOUND",
            evidenceSpan: null,
            supportEventId: null,
          },
        ],
      },
      {
        returnRecordId: "rec-2",
        returnReference: null,
        status: "OPEN",
        returnMethod: null,
        artifacts: [],
      },
    ]);
  });

  it("still draws a contributed record the panel does not hold", () => {
    // The two reads disagree about what is on this case. Dropping the odd one
    // out would hide the disagreement; drawing it is how somebody finds out.
    const payload = readRecordsPayload(
      section({ records: [{ returnRecordId: "rec-9", status: "OPEN", artifacts: [] }] }),
      panelRecords,
    );
    expect(payload.records.map((card) => card.returnRecordId)).toEqual(["rec-1", "rec-2", "rec-9"]);
  });

  it("orders artifact rows by kind and keeps two of a kind in the order they were recorded", () => {
    const payload = readRecordsPayload(
      section({
        records: [
          {
            returnRecordId: "rec-1",
            artifacts: [
              { artifactType: "RETURN_LOCATION", value: "dock four" },
              { artifactType: "TRACKING", value: "first parcel" },
              { artifactType: "RMA", value: "the reference" },
              { artifactType: "TRACKING", value: "second parcel" },
              { artifactType: "SOMETHING_NEW", value: "from a newer server" },
            ],
          },
        ],
      }),
      [panelRecords[0]],
    );

    // Exact list. Two tracking numbers on one return are two packages, and
    // reordering them silently reassigns which is which.
    expect(payload.records[0].artifacts.map((a) => [a.label, a.value])).toEqual([
      ["RMA", "the reference"],
      ["Tracking", "first parcel"],
      ["Tracking", "second parcel"],
      ["Return to", "dock four"],
      // An unrecognised kind keeps its raw name so a server/bundle skew is
      // visible, rather than being dropped or title-cased into something that
      // looks official.
      ["SOMETHING_NEW", "from a newer server"],
    ]);
  });

  it("reads the case's bay placement when it arrives as the object it is documented as", () => {
    // The defect the resume found: placement is case-level and singular, and
    // the reader only accepted an array. An object payload lost the bay in
    // silence, and a case with no placement drew identically to one whose goods
    // are on a shelf somewhere.
    const payload = readRecordsPayload(
      section({
        records: [],
        placement: { facilityId: "north site", bayId: "the far aisle", reason: "oversize" },
      }),
      [],
    );
    expect(payload.placement).toEqual({
      facilityId: "north site",
      bayId: "the far aisle",
      reason: "oversize",
    });
  });

  it("has no placement when every part of it is absent", () => {
    // An empty struct is not a placement, and drawing "Placed at: Pending"
    // would assert that somebody looked.
    expect(readRecordsPayload(section({ placement: {} }), []).placement).toBeNull();
    expect(readRecordsPayload(section({}), []).placement).toBeNull();
  });

  it("orders the unbound artifacts by the same rank as the cards", () => {
    const payload = readRecordsPayload(
      section({
        unbound: [
          { artifactType: "LABEL", value: "a label", status: "UNMATCHED" },
          { artifactType: "RMA", value: "a reference", status: "AMBIGUOUS", evidenceSpan: "as\nsaid" },
        ],
      }),
      [],
    );
    expect(payload.unbound).toEqual([
      {
        artifactType: "RMA",
        label: "RMA",
        value: "a reference",
        status: "AMBIGUOUS",
        evidenceSpan: "as said",
        supportEventId: null,
      },
      {
        artifactType: "LABEL",
        label: "Label",
        value: "a label",
        status: "UNMATCHED",
        evidenceSpan: null,
        supportEventId: null,
      },
    ]);
  });

  it("survives a payload that is nonsense in every field", () => {
    const payload = readRecordsPayload(
      section({ records: "not a list", placement: 4, unbound: [1, "two"], framingPromptKey: [] }),
      [],
    );
    expect(payload).toEqual({ records: [], placement: null, unbound: [], framingPromptKey: null });
  });

  it("reads a contributed record card, in the payload convention", () => {
    const camel = readRecordsPayload(
      section({
        records: [
          {
            returnRecordId: "rec-9",
            returnReference: "a reference",
            artifacts: [{ artifactType: "TRACKING", value: "a parcel", status: "BOUND" }],
          },
        ],
        placement: { facilityId: "a site", bayId: "an aisle", reason: null },
        framingPromptKey: "support-multi-record-do-not-mix",
      }),
      [],
    );
    // camelCase throughout, per AMENDMENT-7 -- and pinned against the values
    // rather than against a second read of the same payload, because two reads
    // returning the same emptiness would agree perfectly.
    expect(camel.records).toEqual([
      {
        returnRecordId: "rec-9",
        returnReference: "a reference",
        status: null,
        returnMethod: null,
        artifacts: [
          {
            artifactType: "TRACKING",
            label: "Tracking",
            value: "a parcel",
            status: "BOUND",
            evidenceSpan: null,
            supportEventId: null,
          },
        ],
      },
    ]);
    expect(camel.placement).toEqual({ facilityId: "a site", bayId: "an aisle", reason: null });
    expect(camel.framingPromptKey).toBe("support-multi-record-do-not-mix");
  });

  it("carries the framing key and never a sentence", () => {
    const payload = readRecordsPayload(section({ framingPromptKey: "support-multi-record" }), []);
    expect(payload.framingPromptKey).toBe("support-multi-record");
  });
});

describe("the thread digest", () => {
  it("reads the section's messages and keeps the total apart from the count shown", () => {
    const payload = readDigestPayload(
      section({
        messages: [
          {
            supportEventId: "evt-1",
            senderDisplayName: "the   support  desk",
            status: "PROCESSED",
            intent: "rma_issued",
            preview: FRAMED,
            recordedAtIso: "2026-08-30T10:00:00Z",
          },
        ],
        total: 23,
      }),
    );
    expect(payload).toEqual({
      messages: [
        {
          supportEventId: "evt-1",
          sender: "the support desk",
          status: "PROCESSED",
          intent: "rma_issued",
          preview: FRAMED_FLAT,
          recordedAtIso: "2026-08-30T10:00:00Z",
        },
      ],
      // Not `messages.length`. The digest is capped and the thread is not, so
      // "showing 1 of 23" is information that 1 alone does not carry.
      total: 23,
    });
  });

  it("says nothing about the total rather than claiming the cap is it", () => {
    const payload = readDigestPayload(section({ messages: [{ supportEventId: "evt-1" }] }));
    expect(payload.total).toBeNull();
  });

  it("reads a contributed message, in the payload convention", () => {
    // AMENDMENT-7 settles this: the payload is camelCase, mirroring the stored
    // documents it carries, while the DTO around it stays snake_case. The reader
    // takes **one** convention -- an earlier version took either, which would
    // have drawn a wrong-cased payload perfectly and told nobody the producer
    // disagreed.
    const camel = readDigestPayload(
      section({
        messages: [
          {
            supportEventId: "evt-2",
            senderDisplayName: "a transport",
            recordedAtIso: "2026-08-30T10:00:00Z",
          },
        ],
      }),
    );
    // Pinned against the values. An assertion that only compared two reads of
    // two payloads would pass with both of them empty.
    expect(camel.messages).toEqual([
      {
        supportEventId: "evt-2",
        sender: "a transport",
        status: null,
        intent: null,
        preview: null,
        recordedAtIso: "2026-08-30T10:00:00Z",
      },
    ]);
  });

  it("reads nothing at all when no section carries a digest", () => {
    // `CasePanelView.support_digest` is retired (AMENDMENT-6) and was
    // unfillable before it was: a contributor returns a section, not a
    // top-level field. There is no second source to fall back to, and a
    // fallback to one that cannot have a value would only hide this one failing.
    expect(readDigestPayload(undefined)).toEqual({ messages: [], total: null });
  });

  it("drops a message with no event id, and only that one", () => {
    const payload = readDigestPayload(
      section({ messages: [{ preview: "orphan" }, { supportEventId: "evt-3" }] }),
    );
    expect(payload.messages.map((m) => m.supportEventId)).toEqual(["evt-3"]);
  });
});

describe("the parked-messages entry", () => {
  it("takes the count the contributor gave", () => {
    expect(readParkedPayload(section({ count: 4, nlEnabled: false, quota: 50 }))).toEqual({
      count: 4,
      nlEnabled: false,
      oldestParkedAtIso: null,
      quota: 50,
    });
  });

  it("counts nothing when no section says so, rather than reading a field nobody can fill", () => {
    // `CasePanelView.parked_messages` is retired (AMENDMENT-6) and was
    // unfillable before it was: hardcoded `0` in `api/case_panel.py`, with no
    // contributor able to write it. A `?? panel.parked_messages` fallback
    // resolved to zero on every real panel, so the entry would never have
    // appeared -- on exactly the deployments where an operator needs it.
    expect(readParkedPayload(undefined)).toEqual({
      count: 0,
      nlEnabled: null,
      oldestParkedAtIso: null,
      quota: null,
    });
  });

  it("reads a contributed parked entry, in the payload convention", () => {
    const camel = readParkedPayload(section({ count: 4, nlEnabled: false, quota: 50 }));
    expect(camel).toEqual({
      count: 4,
      nlEnabled: false,
      oldestParkedAtIso: null,
      quota: 50,
    });
  });

  it("asserts no cause when the contributor did not state one", () => {
    // "These are on file" is true either way. "Natural-language intake is off"
    // is a claim about a release this console has not read.
    expect(readParkedPayload(section({ count: 1 })).nlEnabled).toBeNull();
  });
});

describe("degradation, and the ids both registries key on", () => {
  it("tells a section that could not be read from one with nothing to say", () => {
    expect(isDegraded(undefined)).toBe(false);
    expect(isDegraded(section({}, "ok"))).toBe(false);
    expect(isDegraded(section({}, "degraded"))).toBe(true);
  });

  it("gives every id an order, and every order a different place", () => {
    const ids = Object.values(SUPPORT_SECTION_IDS);
    expect(ids).toEqual([
      "support_announcements",
      "support_parked_messages",
      "support_return_records",
      "support_thread_digest",
    ]);
    const orders = ids.map((id) => SUPPORT_SECTION_ORDER[id]);
    expect(orders).toEqual([0, 10, 20, 30]);
    expect(new Set(orders).size).toBe(orders.length);
  });
});
