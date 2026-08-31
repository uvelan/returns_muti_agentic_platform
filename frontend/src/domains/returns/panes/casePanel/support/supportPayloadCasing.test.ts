import { describe, expect, it } from "vitest";

import type { PanelSectionView } from "../../../../../api/casePanel";
import {
  mentionsSnakeCaseKeys,
  readDigestPayload,
  readParkedPayload,
  readRecordsPayload,
} from "./supportPanelPayloads";

/**
 * The key casing on this seam, pinned key-for-key. **AMENDMENT-7.**
 *
 * The rule: the `CasePanelView` DTO's own fields are **snake_case**
 * (`case_id`, `return_records`, `section_id`), and a section's opaque
 * **`payload` is camelCase** (`returnRecordId`, `supportEventId`), mirroring the
 * stored documents it carries. `payload` is an opaque `dict`, so no schema
 * governs it and nothing on the wire will ever complain -- which is precisely
 * why a convention here needs a test rather than a paragraph.
 *
 * ## Why this test observes rather than restates
 *
 * The obvious version lists the expected keys and compares them to a list
 * written beside the reader. That is two copies of one intention agreeing with
 * each other, and it goes green the moment somebody edits both -- or edits
 * neither and changes the reader.
 *
 * So the payload handed to each reader is a **recording proxy**: it answers like
 * a normal object and remembers every property actually asked for. The
 * assertion is then on what the reader *did*, and the expected set is exact --
 * a key added, removed or re-spelled fails here before it fails on a screen.
 *
 * ## Why the dual-read had to go
 *
 * An earlier version of `supportPanelPayloads.ts` accepted either spelling. It
 * would have drawn a wrong-cased payload perfectly and told nobody the producer
 * disagreed -- and in the places where it did not draw, an empty section is
 * indistinguishable from a case Support has said nothing about. Reading one
 * convention is what makes the disagreement observable; `mentionsSnakeCaseKeys`
 * is what turns it into something an operator can see.
 */

/** camelCase: starts lower, no underscores. */
const CAMEL = /^[a-z][A-Za-z0-9]*$/;
/** snake_case: lower, underscore-separated. */
const SNAKE = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/;

/**
 * A value that answers like the real thing and remembers what was asked of it.
 *
 * Only plain objects are proxied. Arrays pass through so `Array.isArray` and
 * `.filter` keep working, and their elements are proxied on the way out -- which
 * is how the keys inside `records[]` and `messages[]` get recorded too.
 */
function recording(value: unknown, seen: Set<string>): unknown {
  if (Array.isArray(value)) return value.map((element) => recording(element, seen));
  if (typeof value !== "object" || value === null) return value;
  return new Proxy(value as Record<string, unknown>, {
    get(target, key, receiver) {
      const value: unknown = Reflect.get(target, key, receiver);
      if (typeof key !== "string") return value;
      seen.add(key);
      return recording(value, seen);
    },
    // `key in proxy` is a read too -- `readObject` uses it, and a reader that
    // probed for a key without getting it would otherwise go unrecorded.
    has(target, key) {
      if (typeof key === "string") seen.add(key);
      return Reflect.has(target, key);
    },
  });
}

function section(payload: Record<string, unknown>, seen: Set<string>): PanelSectionView {
  return {
    section_id: "support_return_records",
    payload: recording(payload, seen) as Record<string, unknown>,
    status: "ok",
    reason: null,
  };
}

/** A payload carrying every field the records section reads. */
const RECORDS_PAYLOAD = {
  records: [
    {
      returnRecordId: "rec-9",
      returnReference: "a reference",
      status: "OPEN",
      returnMethod: "PARCEL",
      artifacts: [
        {
          artifactType: "TRACKING",
          value: "a parcel",
          status: "BOUND",
          evidenceSpan: "as written",
          supportEventId: "evt-1",
        },
      ],
    },
  ],
  placement: { facilityId: "a site", bayId: "an aisle", reason: "oversize" },
  unbound: [{ artifactType: "LABEL", value: "a label", status: "UNMATCHED" }],
  framingPromptKey: "support-multi-record-do-not-mix",
};

describe("what the readers ask a contributed payload for", () => {
  it("asks the records section for exactly these keys, all camelCase", () => {
    const seen = new Set<string>();
    // A panel record too, so the join runs and the artifacts are reached.
    readRecordsPayload(section(RECORDS_PAYLOAD, seen), [
      { return_record_id: "rec-1", return_reference: "REF", status: "OPEN", return_method: "PARCEL" },
    ]);
    expect([...seen].sort()).toEqual([
      "artifactType",
      "artifacts",
      "bayId",
      "evidenceSpan",
      "facilityId",
      "framingPromptKey",
      "placement",
      "reason",
      "records",
      "returnMethod",
      "returnRecordId",
      "returnReference",
      "status",
      "supportEventId",
      "unbound",
      "value",
    ]);
    expect([...seen].filter((key) => !CAMEL.test(key))).toEqual([]);
  });

  it("asks the digest section for exactly these keys, all camelCase", () => {
    const seen = new Set<string>();
    readDigestPayload(
      section(
        {
          // Two messages, because `senderDisplayName ?? sender` short-circuits:
          // with only the first, `sender` is never asked for and the set below
          // would silently stop pinning it.
          messages: [
            {
              supportEventId: "evt-1",
              senderDisplayName: "the desk",
              status: "PROCESSED",
              intent: "other",
              preview: "hello",
              recordedAtIso: "2026-08-30T10:00:00Z",
            },
            { supportEventId: "evt-2", sender: "a transport" },
          ],
          total: 3,
        },
        seen,
      ),
    );
    expect([...seen].sort()).toEqual([
      "intent",
      "messages",
      "preview",
      "recordedAtIso",
      "sender",
      "senderDisplayName",
      "status",
      "supportEventId",
      "total",
    ]);
    expect([...seen].filter((key) => !CAMEL.test(key))).toEqual([]);
  });

  it("asks the parked section for exactly these keys, all camelCase", () => {
    const seen = new Set<string>();
    readParkedPayload(
      section({ count: 2, nlEnabled: false, oldestParkedAtIso: "2026-08-30T10:00:00Z", quota: 50 }, seen),
    );
    expect([...seen].sort()).toEqual(["count", "nlEnabled", "oldestParkedAtIso", "quota"]);
    expect([...seen].filter((key) => !CAMEL.test(key))).toEqual([]);
  });

  it("asks the panel's own projection for exactly these keys, all snake_case", () => {
    // The other half of AMENDMENT-7, and the reason it needs stating: the DTO
    // read and the payload read sit a few lines apart in `readRecordsPayload`
    // and look almost identical. They are two sources with two conventions.
    const seen = new Set<string>();
    readRecordsPayload(undefined, [
      recording(
        { return_record_id: "rec-1", return_reference: "REF", status: "OPEN", return_method: "PARCEL" },
        seen,
      ) as Record<string, unknown>,
    ]);
    expect([...seen].sort()).toEqual([
      "return_method",
      "return_record_id",
      "return_reference",
      "status",
    ]);
    // `status` is one word, so it satisfies both patterns -- which is exactly
    // why this asserts the whole set rather than "every key has an underscore".
    expect([...seen].filter((key) => !SNAKE.test(key))).toEqual([]);
  });

  it("records what a reader really touched -- the proxy is not inventing agreement", () => {
    // Without this every assertion above is green against a proxy that records
    // nothing, or one that records the fixture's own keys regardless of what was
    // read. Both halves: a key present but never read is absent from the set,
    // and a key the reader asks for that the payload lacks is present in it.
    const seen = new Set<string>();
    readParkedPayload(section({ count: 1, neverRead: true }, seen));
    expect(seen.has("neverRead")).toBe(false);
    expect(seen.has("nlEnabled")).toBe(true);
  });
});

describe("a payload sent in the wrong convention", () => {
  it("reads as nothing -- the reader does not quietly translate it", () => {
    // **The guard on the dual-read staying gone.** Everything else in this file
    // watches which keys are *asked for*, and a tolerant reader asks for the
    // camelCase key first and finds it -- so the observed sets are identical
    // with tolerance and without, and reinstating it left all 169 tests green.
    // The only assertion that separates the two is a behavioural one: hand the
    // reader the other convention and require that it yields nothing.
    //
    // Whole-value equalities, not "does not contain": a reader that returned a
    // record with every field null would pass a containment check.
    expect(
      readRecordsPayload(
        section(
          {
            records: [
              {
                return_record_id: "rec-9",
                return_reference: "a reference",
                artifacts: [{ artifact_type: "TRACKING", value: "a parcel" }],
              },
            ],
            placement: { facility_id: "a site", bay_id: "an aisle" },
            unbound: [{ artifact_type: "LABEL", value: "a label" }],
            framing_prompt_key: "support-multi-record-do-not-mix",
          },
          new Set<string>(),
        ),
        [],
      ),
    ).toEqual({ records: [], placement: null, unbound: [], framingPromptKey: null });

    expect(
      readDigestPayload(
        section(
          { messages: [{ support_event_id: "evt-1", preview: "hello" }], total: 3 },
          new Set<string>(),
        ),
      ),
    ).toEqual({ messages: [], total: 3 });

    expect(
      readParkedPayload(
        section({ count: 2, nl_enabled: false, oldest_parked_at_iso: "x" }, new Set<string>()),
      ),
    ).toEqual({ count: 2, nlEnabled: null, oldestParkedAtIso: null, quota: null });
  });

  it("is found at every depth the readers descend to", () => {
    // **Review V2p2-1, F1.** This scanned the root and one level; the readers
    // descend two, for `records[].artifacts[]`. Probed against the shipped
    // detector, the ladder read:
    //
    //     depth0 (nl_enabled at root)            : detected
    //     depth1 (return_record_id in records[]) : detected
    //     depth2 (artifact_type in artifacts[])  : NOT detected
    //     readerProducedArtifacts                : []
    //
    // So the strict reader dropped the artifact -- correctly -- and nothing
    // said so, and the card drew with zero artifacts, which reads exactly like
    // "Support has attached nothing to this return". The whole ladder is pinned
    // here, not just the level that was broken: a fix that moved the blind spot
    // from two to three would pass a test that only checked two.
    const depth0 = { nl_enabled: false };
    const depth1 = { records: [{ return_record_id: "rec-1", artifacts: [] }] };
    const depth2 = {
      records: [
        { returnRecordId: "rec-1", artifacts: [{ artifact_type: "TRACKING", value: "a parcel" }] },
      ],
    };
    expect([depth0, depth1, depth2].map((payload) => mentionsSnakeCaseKeys(payload))).toEqual([
      true,
      true,
      true,
    ]);

    // And the half that makes it matter: the reader really does produce nothing
    // from the depth-2 payload, so without the notice the card is an absence.
    const read = readRecordsPayload(section(depth2, new Set<string>()), []);
    expect(read.records[0]?.artifacts).toEqual([]);
  });

  it("is reported rather than drawn as an absence", () => {
    // The whole argument for dropping the dual-read. A tolerant reader would
    // have rendered this perfectly; a strict silent one renders nothing, which
    // reads exactly like a case Support has said nothing about.
    expect(
      mentionsSnakeCaseKeys({
        records: [{ return_record_id: "rec-1", artifacts: [] }],
      }),
    ).toBe(true);
    // One level down is where the give-away lives: the top level of a payload
    // can be entirely single-word and still be snake_case underneath.
    expect(mentionsSnakeCaseKeys({ messages: [{ support_event_id: "evt-1" }] })).toBe(true);
    expect(mentionsSnakeCaseKeys({ placement: { bay_id: "an aisle" } })).toBe(true);
    expect(mentionsSnakeCaseKeys({ nl_enabled: false })).toBe(true);
  });

  it("says nothing about a payload that is correct, or empty, or absent", () => {
    // A false positive here would put a "your release is broken" notice on a
    // working panel, which is worse than the thing it is warning about.
    expect(mentionsSnakeCaseKeys(RECORDS_PAYLOAD)).toBe(false);
    expect(mentionsSnakeCaseKeys({ messages: [{ supportEventId: "evt-1" }], total: 3 })).toBe(false);
    expect(mentionsSnakeCaseKeys({})).toBe(false);
    expect(mentionsSnakeCaseKeys(undefined)).toBe(false);
    // A value that merely *contains* an underscore is not a key that does.
    expect(mentionsSnakeCaseKeys({ framingPromptKey: "support-multi-record-do-not-mix" })).toBe(
      false,
    );
    expect(mentionsSnakeCaseKeys({ records: [{ returnRecordId: "rec_1" }] })).toBe(false);
    // A correct payload at the depth the detector now reaches. Widening a
    // detector is exactly how false positives arrive, and a "your release is
    // broken" notice on a working panel is worse than the thing it warns about.
    expect(mentionsSnakeCaseKeys(RECORDS_PAYLOAD)).toBe(false);
    expect(
      mentionsSnakeCaseKeys({
        records: [
          {
            returnRecordId: "rec-1",
            artifacts: [{ artifactType: "TRACKING", value: "a parcel", evidenceSpan: "as_written" }],
          },
        ],
      }),
    ).toBe(false);
  });
});
