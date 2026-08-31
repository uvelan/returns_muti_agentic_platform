/**
 * The associate-facing wording for what Support has said.
 *
 * Its own module because two surfaces need the same sentences -- the panel's
 * record cards and the typed system entry in the Order Discovery transcript --
 * and a fan-out warning that is worded one way on the panel and another way in
 * the transcript is two claims about the same case on one screen.
 */

/**
 * The do-not-mix sentence, chosen by the key the release names.
 *
 * `support_ingress.multi_record_framing_prompt_key` carries the **key**, never
 * the wording -- the config comment is explicit that it "names which of it
 * applies". That split is deliberate and it is the right way round: the wording
 * is associate-facing copy with a tone and a reading level, and a release that
 * could set the text directly would be a way to put arbitrary words on this
 * screen.
 */
export const MULTI_RECORD_FRAMING: Readonly<Record<string, string>> = {
  "support-multi-record-do-not-mix":
    "Support answered about more than one return in a single message. Each card below is a separate return -- check the reference on the card before you use anything on it.",
};

/**
 * The same warning, for the transcript, where there are no cards to point at.
 *
 * Separate wording rather than the same string, because "the card below" names
 * nothing in a conversation. The *claim* is identical and that is what matters:
 * one message, more than one return, do not carry a value from one to another.
 */
export const MULTI_RECORD_FRAMING_IN_TRANSCRIPT: Readonly<Record<string, string>> = {
  "support-multi-record-do-not-mix":
    "This message from Support was about more than one return. This update is about one of them only -- check the reference before you act on it.",
};

const DEFAULT_KEY = "support-multi-record-do-not-mix";

/**
 * The framing for a key, falling back to the default.
 *
 * An unrecognised key falls back rather than drawing nothing. A console that
 * silently omitted the warning because a release named a framing it had not
 * shipped would fail in exactly the case the warning exists for -- and it would
 * fail invisibly, which is worse than wording it generically.
 */
export function framingFor(
  key: string | null,
  table: Readonly<Record<string, string>> = MULTI_RECORD_FRAMING,
): string {
  return (key === null ? undefined : table[key]) ?? table[DEFAULT_KEY];
}

/**
 * How an intent reads to somebody who did not write the taxonomy.
 *
 * The digest drew the raw value -- `rma_issued`, `information_request` -- which
 * is machine vocabulary on the screen of somebody holding a box on a phone
 * call. A closed map, and an **unrecognised intent keeps its raw name** rather
 * than being title-cased: the raw name is visibly a system value, so a skew
 * between this bundle and a newer taxonomy looks like what it is instead of
 * looking like a phrase the platform chose.
 */
export const INTENT_LABEL: Readonly<Record<string, string>> = {
  rma_issued: "Return authorised",
  rejection: "Return declined",
  label_provided: "Label sent",
  tracking_update: "Tracking sent",
  information_request: "They need more from us",
  other: "General reply",
};

export function intentLabel(intent: string): string {
  return INTENT_LABEL[intent] ?? intent;
}
