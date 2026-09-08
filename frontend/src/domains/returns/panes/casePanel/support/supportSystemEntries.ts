import { MULTI_RECORD_FRAMING_IN_TRANSCRIPT, framingFor } from "./supportCopy";
import { readObject, readObjects, readString } from "./supportPanelPayloads";

/**
 * The typed system entry, on its way into the Order Discovery transcript (DR-3).
 *
 * ## What the entry is, and what it is not
 *
 * V2's relay appends `{entryId, kind, supportEventId, returnRecordId,
 * payload{intent, returnReference, clarificationIds[], multiRecord,
 * framingPromptKey}, recordedAt}` to `state["systemEntries"]` on the
 * conversation document -- **never** to `state["transcript"]`. That is not a
 * filing preference: `_transcript_of` zips the transcript against `turns`
 * positionally, so an entry appended there would either read as something the
 * associate said or break the zip and change what the history endpoint serves.
 *
 * The console side inherits the same rule. A system entry is **not a turn**: it
 * is neither party speaking, it carries no statements, and nothing about it may
 * make the pane treat it as one. `ConversationPane` draws it full-width and
 * centred for exactly that reason -- the associate's messages sit right and the
 * agent's sit left, and an entry borrowing either shape would put the platform's
 * words in somebody's mouth on a screen somebody screenshots.
 *
 * ## The gap this module is honest about
 *
 * **Nothing on the wire carries these entries today.** `GET
 * /api/v2/order-agent/conversations/{id}/transcript` serves `messages[]` (role
 * and text) and `lastResultTurn`, and no endpoint exposes `systemEntries`. So
 * `readSupportSystemEntries` returns an empty list against every response the
 * platform currently sends, and the transcript draws exactly as it does now.
 *
 * The reader is written against the shape the relay actually writes, and it
 * reads it *defensively out of an unknown* rather than by widening
 * `ConversationTranscript` with a field the API does not serve -- declaring a
 * field nothing fills is the same class of thing as a value nothing produced.
 * The missing half is one field on the transcript response, and it belongs to
 * whoever owns that endpoint. Recorded in the ledger and the delta report as a
 * carry-forward, not worked around here.
 */

/** The kind V2's classify path appends. `message_classification.py::SUPPORT_UPDATE_ENTRY_KIND`. */
export const SUPPORT_UPDATE_ENTRY_KIND = "SUPPORT_UPDATE";
/** The item pane recorded the associate's answer. `relay.py::SELECTION_RECORDED_ENTRY_KIND`. */
export const SELECTION_RECORDED_ENTRY_KIND = "SELECTION_RECORDED";
/** Support issued or updated an RMA. `relay.py::RETURN_RECORD_ISSUED_ENTRY_KIND`. */
export const RETURN_RECORD_ISSUED_ENTRY_KIND = "RETURN_RECORD_ISSUED";

export type SupportSystemEntry = {
  readonly entryId: string;
  readonly kind: string;
  readonly returnReference: string | null;
  readonly intent: string | null;
  /** What the entry is labelled in the transcript. Says who is speaking. */
  readonly kicker: string;
  /** The composed sentence. Platform-written; no support text is folded in. */
  readonly text: string;
  readonly recordedAtIso: string | null;
};

/**
 * How an intent reads to somebody who did not write the taxonomy.
 *
 * A closed map, and an unrecognised intent falls back to a sentence that claims
 * nothing about what Support said -- rather than being title-cased into
 * something that looks like a decision the platform made. The intent is a
 * model's reading; the entry must not present it as more than that.
 */
const INTENT_SENTENCE: Readonly<Record<string, string>> = {
  rma_issued: "Support has issued a return authorisation.",
  rejection: "Support has declined this return.",
  label_provided: "Support has sent a return label.",
  tracking_update: "Support has sent tracking details.",
  information_request: "Support has asked for something before they can continue.",
  other: "Support has replied about this return.",
};

const UNKNOWN_INTENT_SENTENCE = "Support has replied about this return.";

/**
 * The entry's sentence, built entirely from platform-owned copy.
 *
 * **No support-authored text is interpolated here**, and that is deliberate:
 * the transcript is a conversation, and a value dropped into it reads as
 * something somebody said. The `return_reference` is the one identifier that
 * appears, because an update that would not say which return it is about is
 * unusable on a case with more than one -- and it goes through `readString`,
 * so it is whitespace-collapsed like every other value this slice draws.
 */
function sentenceFor(
  intent: string | null,
  reference: string | null,
  multiRecord: boolean,
  framingPromptKey: string | null,
): string {
  const parts = [intent === null ? UNKNOWN_INTENT_SENTENCE : (INTENT_SENTENCE[intent] ?? UNKNOWN_INTENT_SENTENCE)];
  if (reference !== null) parts.push(`This is about ${reference}.`);
  // One entry per record on a fan-out (the relay appends one each), so the
  // do-not-mix warning belongs on every one of them -- an associate reading a
  // single entry has no way to see that there were others.
  if (multiRecord) parts.push(framingFor(framingPromptKey, MULTI_RECORD_FRAMING_IN_TRANSCRIPT));
  return parts.join(" ");
}

/**
 * Every system entry on a conversation payload, oldest first.
 *
 * Defensive to the same standard as the panel readers: an entry with no id is
 * dropped (it is the React key and the relay's derived idempotency handle, and
 * without it a redelivered update could draw twice), and anything that is not a
 * list of objects reads as no entries at all rather than throwing on a screen an
 * associate is mid-conversation in.
 */
export function readSupportSystemEntries(source: unknown): readonly SupportSystemEntry[] {
  return readObjects(source, "systemEntries").flatMap((entry) => {
    const entryId = readString(entry, "entryId");
    if (entryId === null) return [];
    const kind = readString(entry, "kind") ?? SUPPORT_UPDATE_ENTRY_KIND;
    const payload = readObject(entry, "payload");
    const reference = readString(payload, "returnReference");
    const recordedAtIso = readString(entry, "recordedAt");
    if (kind === SELECTION_RECORDED_ENTRY_KIND) {
      return [
        {
          entryId,
          kind,
          returnReference: null,
          intent: null,
          kicker: RECORDED_FROM_PANE_KICKER,
          text: selectionSentence(payload),
          recordedAtIso,
        },
      ];
    }
    if (kind === RETURN_RECORD_ISSUED_ENTRY_KIND) {
      return [
        {
          entryId,
          kind,
          returnReference: reference,
          intent: null,
          kicker: SUPPORT_UPDATE_KICKER,
          text: returnRecordSentence(payload, reference),
          recordedAtIso,
        },
      ];
    }
    const intent = readString(payload, "intent");
    const multiRecord = payload?.multiRecord === true;
    return [
      {
        entryId,
        kind,
        returnReference: reference,
        intent,
        kicker: SUPPORT_UPDATE_KICKER,
        text: sentenceFor(intent, reference, multiRecord, readString(payload, "framingPromptKey")),
        recordedAtIso,
      },
    ];
  });
}

/**
 * The pane's answer, said back. Line, quantity and reason are the associate's
 * own selection and the released vocabulary's words; the description is the
 * catalogue's. All go through `readString`/`readNumber` so they are collapsed
 * and typed before they reach the sentence.
 */
function selectionSentence(payload: Record<string, unknown> | null): string {
  // An item with neither a line nor a description is not described: the
  // sentence says less rather than standing in a placeholder for it.
  const lines = readObjects(payload, "items").flatMap((item) => {
    const line = readString(item, "orderLineReference");
    const description = readString(item, "description");
    if (line === null && description === null) return [];
    const quantity = readNumber(item, "quantity");
    const reason = readString(item, "reason");
    const words: string[] = [];
    if (quantity !== null) words.push(`${String(quantity)} ×`);
    if (description !== null) words.push(description);
    if (line !== null) words.push(description === null ? `line ${line}` : `(line ${line})`);
    if (reason !== null) words.push(`, ${reasonWords(reason)}`);
    return [words.join(" ").replace(" ,", ",")];
  });
  const details = readObject(payload, "returnDetails");
  const method = readString(details, "returnMethod");
  const parts = ["Recorded from the item pane."];
  if (lines.length > 0) parts[0] = `Recorded from the item pane: ${lines.join("; ")}.`;
  if (method !== null) parts.push(`Return method ${methodWords(method)}.`);
  return parts.join(" ");
}

/**
 * The RMA, said back. Every value is an identifier Support issued -- the
 * reference, the carrier, the label and tracking references -- and each is the
 * thing a carrier desk or a warehouse ticket is keyed by, which is why they
 * appear where Support's *prose* never does.
 */
function returnRecordSentence(
  payload: Record<string, unknown> | null,
  reference: string | null,
): string {
  const method = readString(payload, "returnMethod");
  const carrier = readString(payload, "carrier");
  const tracking = readString(payload, "trackingReference");
  const label = readString(payload, "labelReference");
  const location = readString(payload, "returnLocation");
  const lines = readStrings(payload, "orderLineReferences");
  const parts = ["Support has issued a return authorisation."];
  if (reference !== null) parts.push(`RMA ${reference}.`);
  const how: string[] = [];
  if (method !== null) how.push(methodWords(method));
  if (carrier !== null) how.push(`via ${carrier}`);
  if (how.length > 0) parts.push(`${capitalise(how.join(" "))}.`);
  if (lines.length > 0) parts.push(`Covers line ${lines.join(", ")}.`);
  if (label !== null) parts.push(`Label ${label}.`);
  if (tracking !== null) parts.push(`Tracking ${tracking}.`);
  if (location !== null) parts.push(`Return to ${location}.`);
  return parts.join(" ");
}

function readNumber(source: unknown, key: string): number | null {
  if (typeof source !== "object" || source === null) return null;
  const value = (source as Record<string, unknown>)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readStrings(source: unknown, key: string): readonly string[] {
  if (typeof source !== "object" || source === null) return [];
  const value = (source as Record<string, unknown>)[key];
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => (typeof item === "string" && item.trim() ? [item.trim()] : []));
}

/** A released vocabulary code, in words: `SHIPPING_DAMAGE` reads "shipping damage". */
function reasonWords(code: string): string {
  return code.toLowerCase().replaceAll("_", " ");
}

/** A return method code, in words: `PREPAID_PARCEL` reads "prepaid parcel". */
function methodWords(code: string): string {
  return code.toLowerCase().replaceAll("_", " ");
}

function capitalise(text: string): string {
  return text.length === 0 ? text : text[0].toUpperCase() + text.slice(1);
}

/** What a pane-recorded selection is labelled. The associate said it, in the pane. */
export const RECORDED_FROM_PANE_KICKER = "Recorded from the item pane";

/**
 * What the entry is labelled in the transcript.
 *
 * Says who is speaking, because the entry's whole design problem is that
 * neither party is. "Support" alone would be a lie -- the platform wrote these
 * words, not Support -- and "System" tells an associate nothing about why it is
 * on their screen.
 */
export const SUPPORT_UPDATE_KICKER = "Update from the platform";
