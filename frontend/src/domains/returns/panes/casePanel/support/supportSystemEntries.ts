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

/** The one kind V2 appends. `message_classification.py::SUPPORT_UPDATE_ENTRY_KIND`. */
export const SUPPORT_UPDATE_ENTRY_KIND = "SUPPORT_UPDATE";

export type SupportSystemEntry = {
  readonly entryId: string;
  readonly kind: string;
  readonly returnReference: string | null;
  readonly intent: string | null;
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
    const payload = readObject(entry, "payload");
    const intent = readString(payload, "intent");
    const reference = readString(payload, "returnReference");
    const multiRecord = readObject(entry, "payload")?.multiRecord === true;
    return [
      {
        entryId,
        kind: readString(entry, "kind") ?? SUPPORT_UPDATE_ENTRY_KIND,
        returnReference: reference,
        intent,
        text: sentenceFor(intent, reference, multiRecord, readString(payload, "framingPromptKey")),
        recordedAtIso: readString(entry, "recordedAt"),
      },
    ];
  });
}

/**
 * What the entry is labelled in the transcript.
 *
 * Says who is speaking, because the entry's whole design problem is that
 * neither party is. "Support" alone would be a lie -- the platform wrote these
 * words, not Support -- and "System" tells an associate nothing about why it is
 * on their screen.
 */
export const SUPPORT_UPDATE_KICKER = "Update from the platform";
