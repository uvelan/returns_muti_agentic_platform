import type { PanelSectionView } from "../../../../../api/casePanel";

/**
 * V2's half of the panel section seam: the ids, the payload shapes, and the
 * readers that turn an opaque JSON object into something safe to draw.
 *
 * `PanelSectionView.payload` is an **opaque JSON object** on the wire, by
 * design -- V1's DTO must not learn V2's shapes (`.plan/handoffs/V1-phase2.md`
 * sect. 2). The cost of that seam is paid exactly here: nothing downstream may
 * cast the payload, because a cast is a promise about bytes that arrived from a
 * server which may be older or newer than this bundle. So every field is read
 * through a narrowing function that returns a typed absence rather than
 * throwing, and a section whose payload is nonsense draws as an empty section
 * rather than taking the panel's reviews down with it.
 *
 * ## The rendering-side half of dispatch condition 10
 *
 * `artifact.value` and `evidence_span` are **support-derived**: they originate
 * in a message the platform did not write, and they reach text an associate
 * reads and acts on. V2 phase 1b bounded them *structurally* (256/128
 * characters). Bounds are not escaping, and the condition is explicit that the
 * rendering side must escape rather than interpret.
 *
 * Two things do that here, and they answer two different attacks:
 *
 * 1. **Every value reaches the DOM as a React text child.** There is no
 *    `dangerouslySetInnerHTML` on this surface and no markdown renderer, so
 *    `<img src=x onerror=...>` is five words on a screen and not an element.
 *    That is React's default and the test pins it anyway, because the way this
 *    breaks in future is somebody adding a renderer "just for the digest".
 *
 * 2. **`readString` collapses whitespace runs to a single space** -- every
 *    string this module reads, without exception and with no raw-string reader
 *    beside it. This is the one that is not free. Escaping stops a value
 *    becoming *markup*; it does
 *    nothing to stop a value becoming *layout*. A tracking number submitted as
 *
 *        1Z999 \n RETURN LOCATION: dock four
 *
 *    is, after escaping, still a value that draws itself across two lines --
 *    and the second line is shaped exactly like the labelled rows beside it. An
 *    associate reading a card cannot tell a line the platform wrote from a line
 *    Support's message drew. That is V1 phase 1's framing-injection finding
 *    (carry-forward condition 7) in its rendering form, so the newline dies
 *    here rather than being trusted to a bound elsewhere.
 *
 * Neither is a substitute for the other and both are tested by injection.
 */

/* -------------------------------------------------------------------------
 * Section ids
 * ---------------------------------------------------------------------- */

/**
 * The ids V2 contributes under, and the layout order the console draws them in.
 *
 * **Both registries key on these strings**, so they are declared once, here,
 * and imported by the renderers, the tests and the MSW fixtures. A section id
 * spelled twice is the drift the seam's `sectionId` matching cannot catch: the
 * backend contributes `support_thread_digest`, the console registers
 * `support-thread-digest`, and the result is a labelled placeholder that looks
 * like a deployment skew.
 *
 * Order is explicit for the registry's stated reason -- registration order is
 * import order. The chosen order is a reading order, not an arbitrary one:
 *
 * * `0` the announcer, which draws no visible section at all and only has an
 *   order because the registry requires one;
 * * `10` parked messages, first because it is the section that says the panel
 *   below it is **not** the whole story;
 * * `20` the records, which is what an associate came here to read;
 * * `30` the thread digest, which is the evidence behind them.
 */
export const SUPPORT_SECTION_IDS = {
  announcer: "support_announcements",
  parked: "support_parked_messages",
  records: "support_return_records",
  digest: "support_thread_digest",
} as const;

export const SUPPORT_SECTION_ORDER = {
  [SUPPORT_SECTION_IDS.announcer]: 0,
  [SUPPORT_SECTION_IDS.parked]: 10,
  [SUPPORT_SECTION_IDS.records]: 20,
  [SUPPORT_SECTION_IDS.digest]: 30,
} as const;

/* -------------------------------------------------------------------------
 * Safe reading
 * ---------------------------------------------------------------------- */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Key casing on this seam, per **AMENDMENT-7**.
 *
 * Two conventions, at two different sources, and neither is tolerance:
 *
 * * the **`CasePanelView` DTO's own fields are snake_case** (`case_id`,
 *   `return_records`, `section_id`) -- V1's `_return_records` converts the
 *   store's camelCase to snake_case deliberately for the DTO;
 * * a section's **opaque `payload` is camelCase** (`returnRecordId`,
 *   `supportEventId`), mirroring the stored documents it carries.
 *
 * `readRecordsPayload` reads *both*: the panel's `return_records[]` in
 * snake_case and the contributed payload in camelCase, a few lines apart. That
 * is not inconsistency -- they are two different sources with two settled
 * conventions, and `supportPayloadCasing.test.ts` pins the exact key set each
 * one is read with.
 *
 * **An earlier version of this module read either spelling.** That is removed.
 * A tolerant reader turns a producer disagreement into a section that renders
 * correctly and tells nobody -- and where it does not render, an empty section
 * is indistinguishable from a case Support has said nothing about. Reading one
 * convention makes a wrong-cased payload *visible*: `mentionsSnakeCaseKeys`
 * below detects it and the section says so on the screen, rather than looking
 * like an absence.
 */
function isSnakeCase(key: string): boolean {
  return /_[a-z0-9]/.test(key);
}

/**
 * How many **object** levels down a contributed payload this looks.
 *
 * The readers descend two at their deepest: `records[]` is one, and
 * `records[].artifacts[]` is two. Every other descent -- `unbound[]`,
 * `messages[]`, `placement` -- stops at one. Four is that plus headroom, so a
 * payload that grows a level does not silently fall out of the check.
 *
 * **Arrays do not consume a level.** A list is not a level of naming -- the
 * keys live on the objects inside it -- and counting `records[]` and its
 * elements as two would make this budget the shape of the container rather than
 * the depth of the names. It is a cap rather than an open walk because this
 * reads a document from a producer the console does not control; termination is
 * guaranteed regardless, since JSON is a finite tree.
 */
const CASING_SCAN_DEPTH = 4;

/**
 * Whether a contributed payload was written in the DTO's convention by mistake.
 *
 * Looks at the payload's own keys and **recurses**, because the give-away is
 * rarely at the top: a payload can be entirely single-word at the root
 * (`records`, `messages`, `count`) and snake_case all the way down.
 *
 * **This stopped at one level and that was a real defect** (review V2p2-1, F1).
 * The readers descend two for `records[].artifacts[]`, so a payload whose
 * artifacts were snake_case was dropped correctly by the strict reader and
 * reported by nothing -- the card drew with zero artifacts, which reads exactly
 * like "Support has attached nothing to this return". That is the invisibility
 * this function exists to prevent, sitting one level below where it looked. And
 * it is the worst level to miss: the artifacts carry the type and the value.
 *
 * Deliberately **not** used to read anything. It exists so a section can report
 * "this contributor sent the wrong shape" instead of drawing an absence, which
 * is the whole reason accepting both spellings was the wrong fix.
 */
export function mentionsSnakeCaseKeys(payload: unknown, depth = CASING_SCAN_DEPTH): boolean {
  if (Array.isArray(payload)) {
    // Same depth: see `CASING_SCAN_DEPTH`.
    return payload.some((element) => mentionsSnakeCaseKeys(element, depth));
  }
  if (depth < 0) return false;
  if (!isRecord(payload)) return false;
  if (Object.keys(payload).some(isSnakeCase)) return true;
  return Object.values(payload).some((value) => mentionsSnakeCaseKeys(value, depth - 1));
}

/**
 * A string field, whitespace-collapsed, or `null` for anything that is not a
 * non-empty string.
 *
 * **The collapse is here rather than at the call sites, and that is the whole
 * point.** An earlier draft of this module read strings raw and then applied
 * `displayText` at four chosen call sites -- `value`, `evidence_span`, `sender`,
 * `preview`. Every other string it read reached the DOM uncollapsed: the
 * artifact *type* (drawn under its own raw name when it is one this bundle does
 * not recognise, which is deliberate and is exactly the unrecognised, therefore
 * unvalidated, case), the binding `status`, the `intent`. Those are chips and
 * labels beside the values, and a newline in one of them restructures the card
 * just as surely as a newline in a tracking number does.
 *
 * Choosing which fields are dangerous is a judgement that has to be re-made
 * correctly every time somebody adds a field. Collapsing in the one reader is a
 * judgement made once. There is no `readRawString`, deliberately: a second door
 * is how the first one stops being load-bearing.
 *
 * Ids and ISO instants go through it too and are unharmed -- neither has
 * meaningful internal whitespace, and one that arrives carrying some is not a
 * value this console should be drawing unchanged either.
 */
export function readString(source: unknown, key: string): string | null {
  if (!isRecord(source)) return null;
  const value = source[key];
  if (typeof value !== "string") return null;
  return displayText(value);
}

/** An object field, or `null`. Accepts a one-element array of one, see below. */
export function readObject(source: unknown, key: string): Record<string, unknown> | null {
  if (!isRecord(source)) return null;
  const value = source[key];
  if (isRecord(value)) return value;
  // A contributor that serialises a single-valued group as a one-element list
  // is a shape this reader can honour without ambiguity, and the alternative --
  // dropping it -- is the silent failure this function was written to fix. See
  // `readRecordsPayload`'s placement note.
  if (Array.isArray(value) && value.length === 1 && isRecord(value[0])) return value[0];
  return null;
}

/** A finite non-negative integer field, or `null`. Never `NaN`, never a coerced string. */
export function readCount(source: unknown, key: string): number | null {
  if (!isRecord(source)) return null;
  const value = source[key];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) return null;
  return value;
}

export function readBoolean(source: unknown, key: string): boolean | null {
  if (!isRecord(source)) return null;
  const value = source[key];
  return typeof value === "boolean" ? value : null;
}

/** An array of objects, skipping anything in it that is not one. */
export function readObjects(source: unknown, key: string): readonly Record<string, unknown>[] {
  if (!isRecord(source)) return [];
  const value = source[key];
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord);
}

/**
 * Support-derived text, as it is allowed to reach the screen.
 *
 * Collapses every run of whitespace -- newlines, tabs, the lot -- to one space
 * and trims. See the module docstring: this is the layout half of condition 10,
 * and it is deliberately applied to the *rendering* and not to the stored fact,
 * which keeps the value in full for the audit trail.
 *
 * `null` in, `null` out: an absent value is drawn as this domain's one word for
 * "the platform has not said", never as an empty row that reads as a blank.
 */
export function displayText(value: string | null): string | null {
  if (value === null) return null;
  const collapsed = value.replace(/\s+/g, " ").trim();
  return collapsed.length === 0 ? null : collapsed;
}

/* -------------------------------------------------------------------------
 * The shapes, after reading
 * ---------------------------------------------------------------------- */

/**
 * The artifact kinds ingress extracts (`operations/artifact_binding.py`
 * `ArtifactType`), and the word each is labelled with on screen.
 *
 * A closed map rather than a formatter over the raw enum value, for the reason
 * the taxonomy is closed on the backend too: an unrecognised type is drawn
 * under its own raw name so an operator can see a server/bundle skew, rather
 * than being dropped or title-cased into something that looks official.
 */
export const ARTIFACT_LABELS: Readonly<Record<string, string>> = {
  RMA: "RMA",
  TRACKING: "Tracking",
  LABEL: "Label",
  SHIPPING_INSTRUCTION: "Shipping instruction",
  RETURN_LOCATION: "Return to",
};

/** The order artifact rows are drawn in. Types outside it follow, in payload order. */
export const ARTIFACT_ROW_ORDER: readonly string[] = [
  "RMA",
  "TRACKING",
  "LABEL",
  "SHIPPING_INSTRUCTION",
  "RETURN_LOCATION",
];

export type SupportArtifact = {
  readonly artifactType: string;
  readonly label: string;
  /** Already whitespace-collapsed. `null` when the payload carried nothing. */
  readonly value: string | null;
  /** `BOUND` | `AMBIGUOUS` | `UNMATCHED`, or `null` when the payload is silent. */
  readonly status: string | null;
  /** What the message named, not what it said. Whitespace-collapsed. */
  readonly evidenceSpan: string | null;
  readonly supportEventId: string | null;
};

export type SupportRecordCard = {
  readonly returnRecordId: string;
  /** The RMA Support issued, or `null` while the platform has not been told one. */
  readonly returnReference: string | null;
  readonly status: string | null;
  readonly returnMethod: string | null;
  readonly artifacts: readonly SupportArtifact[];
};

export type SupportBayPlacement = {
  readonly facilityId: string | null;
  readonly bayId: string | null;
  readonly reason: string | null;
};

export type SupportRecordsPayload = {
  readonly records: readonly SupportRecordCard[];
  /**
   * Placement is **case-level, not per record** (`case_projection/assembly.py`
   * projects one `facilityId`/`bayId`/`bayReason` per case from the bay facts),
   * so it is drawn once under the cards rather than repeated inside each one.
   * Repeating it would read as one placement per RMA, which is a claim the
   * platform does not make.
   */
  readonly placement: SupportBayPlacement | null;
  /** Artifacts that named a record this case does not hold, or named none. */
  readonly unbound: readonly SupportArtifact[];
  /**
   * `support_ingress.multi_record_framing_prompt_key`, as configured.
   *
   * The **key**, never the wording: the config comment is explicit that it
   * "names which of it applies" rather than carrying the text. The console owns
   * the associate-facing sentence -- see `MULTI_RECORD_FRAMING`.
   */
  readonly framingPromptKey: string | null;
};

function readArtifact(source: Record<string, unknown>): SupportArtifact | null {
  const artifactType = readString(source, "artifactType");
  if (artifactType === null) return null;
  return {
    artifactType,
    label: ARTIFACT_LABELS[artifactType] ?? artifactType,
    value: readString(source, "value"),
    status: readString(source, "status"),
    evidenceSpan: readString(source, "evidenceSpan"),
    supportEventId: readString(source, "supportEventId"),
  };
}

function orderArtifacts(artifacts: readonly SupportArtifact[]): readonly SupportArtifact[] {
  const rank = (artifact: SupportArtifact) => {
    const index = ARTIFACT_ROW_ORDER.indexOf(artifact.artifactType);
    return index === -1 ? ARTIFACT_ROW_ORDER.length : index;
  };
  // A stable sort, so two artifacts of one type keep the order the platform
  // recorded them in -- two tracking numbers on one RMA are two packages, and
  // reordering them silently reassigns which is which.
  return [...artifacts]
    .map((artifact, index) => ({ artifact, index }))
    .sort((left, right) => rank(left.artifact) - rank(right.artifact) || left.index - right.index)
    .map((entry) => entry.artifact);
}

/**
 * The records section's payload, joined onto the panel's own narrow projection.
 *
 * **Both sources, deliberately.** `CasePanelView.return_records[]` is V1's and
 * carries identity, status and method for every record on the case; the section
 * payload is V2's and carries what Support has since said about them. A card
 * built from the section alone would silently lose a record nobody has sent an
 * artifact for yet, which is exactly the record an associate is waiting on.
 *
 * The join is on `return_record_id`. A section record naming an id the panel
 * does not hold is still drawn -- dropping it would hide a disagreement between
 * two reads of the same case.
 */
export function readRecordsPayload(
  section: PanelSectionView | undefined,
  panelRecords: readonly Record<string, unknown>[],
): SupportRecordsPayload {
  const payload = section?.payload;
  const contributed = new Map<string, Record<string, unknown>>();
  for (const entry of readObjects(payload, "records")) {
    const id = readString(entry, "returnRecordId");
    if (id !== null) contributed.set(id, entry);
  }

  const cards: SupportRecordCard[] = [];
  const seen = new Set<string>();

  // **The panel's own projection, in the DTO's snake_case** (AMENDMENT-7).
  // The contributed payload a few lines down is camelCase. Two sources, two
  // settled conventions, pinned key-for-key in `supportPayloadCasing.test.ts`.
  for (const record of panelRecords) {
    const id = readString(record, "return_record_id");
    if (id === null) continue;
    seen.add(id);
    const fromSection = contributed.get(id);
    cards.push({
      returnRecordId: id,
      returnReference: readString(record, "return_reference"),
      status: readString(record, "status"),
      returnMethod: readString(record, "return_method"),
      artifacts: orderArtifacts(
        readObjects(fromSection, "artifacts").flatMap((raw) => {
          const artifact = readArtifact(raw);
          return artifact === null ? [] : [artifact];
        }),
      ),
    });
  }

  // A record the section names and the panel does not hold. Contributed
  // payload, so camelCase -- the near-identical block above reads the DTO.
  for (const [id, entry] of contributed) {
    if (seen.has(id)) continue;
    cards.push({
      returnRecordId: id,
      returnReference: readString(entry, "returnReference"),
      status: readString(entry, "status"),
      returnMethod: readString(entry, "returnMethod"),
      artifacts: orderArtifacts(
        readObjects(entry, "artifacts").flatMap((raw) => {
          const artifact = readArtifact(raw);
          return artifact === null ? [] : [artifact];
        }),
      ),
    });
  }

  // **Read as an object, not as a list.** The abandoned draft of this module
  // documented placement as case-level and singular -- one `facilityId` /
  // `bayId` / reason per case, projected from the bay facts -- and then read it
  // with `readObjects(payload, "placement")[0]`, which only ever sees an
  // *array*. A contributor emitting the object the docstring describes would
  // have had the bay silently dropped, and the panel would have drawn a case
  // with no placement exactly like a case whose goods have not been put
  // anywhere. `readObject` takes the object and still honours a one-element
  // list, so neither serialisation loses the bay.
  const placementSource = readObject(payload, "placement");
  const placement =
    placementSource === null
      ? null
      : {
          facilityId: readString(placementSource, "facilityId"),
          bayId: readString(placementSource, "bayId"),
          reason: readString(placementSource, "reason"),
        };

  return {
    records: cards,
    placement:
      placement !== null &&
      (placement.facilityId !== null || placement.bayId !== null || placement.reason !== null)
        ? placement
        : null,
    // Ordered by the same rank as the cards'. The abandoned draft left these in
    // payload order, so the one list an associate reads *against* the cards --
    // "Support sent these and we could not file them" -- was sorted differently
    // from the cards beside it, and comparing the two meant re-finding each row.
    unbound: orderArtifacts(
      readObjects(payload, "unbound").flatMap((raw) => {
        const artifact = readArtifact(raw);
        return artifact === null ? [] : [artifact];
      }),
    ),
    framingPromptKey: readString(payload, "framingPromptKey"),
  };
}

/* -------------------------------------------------------------------------
 * The digest
 * ---------------------------------------------------------------------- */

export type SupportDigestEntry = {
  readonly supportEventId: string;
  /** What to call the sender. Whitespace-collapsed -- it is Support-supplied. */
  readonly sender: string | null;
  readonly status: string | null;
  readonly intent: string | null;
  /** The message, as the digest carries it. Whitespace-collapsed. */
  readonly preview: string | null;
  readonly recordedAtIso: string | null;
};

export type SupportDigestPayload = {
  readonly messages: readonly SupportDigestEntry[];
  /**
   * How many messages the thread holds in total, when the payload says.
   *
   * Kept separate from `messages.length` rather than derived, because they are
   * genuinely different numbers: the digest is capped and the thread is not, so
   * "showing 5 of 23" is information and `messages.length` alone is not.
   * `null` when the contributor did not say, and the footer then says nothing
   * rather than claiming the cap is the total.
   */
  readonly total: number | null;
};

/**
 * The digest, **from the contributed section and nowhere else** (AMENDMENT-6).
 *
 * This read `CasePanelView.support_digest` as a fallback until the amendment.
 * That field could never be filled by anybody: a registered contributor returns
 * a `PanelSectionView | None` into `sections[]`, it cannot write a top-level
 * field, and `api/case_panel.py` hardcodes `support_digest=()`. So the fallback
 * was a branch that ran on every real panel and produced nothing, while every
 * test built on a hand-written `CasePanelView` stayed green -- the same defect
 * V3 measured on `clarifications`, where restricting the reader to the dead
 * field failed 3 tests of 14 and left 11 passing.
 *
 * It is gone rather than kept "just in case". A fallback to a source that cannot
 * have a value is not resilience; it is a second path that hides the first one
 * failing.
 */
export function readDigestPayload(section: PanelSectionView | undefined): SupportDigestPayload {
  return {
    messages: readObjects(section?.payload, "messages").flatMap((entry) => {
      const supportEventId = readString(entry, "supportEventId");
      if (supportEventId === null) return [];
      return [
        {
          supportEventId,
          sender:
            readString(entry, "senderDisplayName") ?? readString(entry, "sender"),
          status: readString(entry, "status"),
          intent: readString(entry, "intent"),
          preview: readString(entry, "preview"),
          recordedAtIso: readString(entry, "recordedAtIso"),
        },
      ];
    }),
    total: readCount(section?.payload, "total"),
  };
}

/* -------------------------------------------------------------------------
 * Parked messages
 * ---------------------------------------------------------------------- */

export type SupportParkedPayload = {
  readonly count: number;
  /**
   * Whether the natural-language door is open.
   *
   * `null` when the contributor did not say, and the copy then describes the
   * count without asserting a cause -- "these are on file" is true either way,
   * while "natural-language intake is switched off" is a claim about a release
   * this console has not read.
   */
  readonly nlEnabled: boolean | null;
  readonly oldestParkedAtIso: string | null;
  /** `support_ingress.parking.per_case_quota`, when the contributor says. */
  readonly quota: number | null;
};

/**
 * The parked count, **from the contributed section and nowhere else**.
 *
 * Same reasoning and the same amendment as the digest above:
 * `CasePanelView.parked_messages` is hardcoded `0` and no contributor can change
 * it, so a `?? panel.parked_messages` fallback would resolve to zero on every
 * real panel -- which is to say, the parked entry would never appear, on exactly
 * the deployments where an operator most needs it.
 */
export function readParkedPayload(section: PanelSectionView | undefined): SupportParkedPayload {
  return {
    count: readCount(section?.payload, "count") ?? 0,
    nlEnabled: readBoolean(section?.payload, "nlEnabled"),
    oldestParkedAtIso: readString(section?.payload, "oldestParkedAtIso"),
    quota: readCount(section?.payload, "quota"),
  };
}

/* -------------------------------------------------------------------------
 * Degradation
 * ---------------------------------------------------------------------- */

/**
 * Whether a contributed section came back degraded.
 *
 * A degraded section is **not** an empty one, and the difference is the whole
 * reason the backend registry catches a raising contributor instead of letting
 * it fail the panel: "Support has told us nothing about this return" and "we
 * could not read what Support told us" look identical on a screen that draws
 * neither, and only one of them is a reason to go and ask somebody.
 */
export function isDegraded(section: PanelSectionView | undefined): boolean {
  return section !== undefined && section.status !== "ok";
}
