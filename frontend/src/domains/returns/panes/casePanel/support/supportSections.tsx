import { useEffect, useRef, useState } from "react";

import { COPILOT_TOKENS, PENDING_LABEL } from "../../../copilotTokens";
import type { PanelSectionRendererProps } from "../panelSectionRegistry";
import {
  SUPPORT_SECTION_IDS,
  isDegraded,
  readDigestPayload,
  readParkedPayload,
  readRecordsPayload,
  type SupportArtifact,
  type SupportRecordCard,
} from "./supportPanelPayloads";

/**
 * What Support has said about this return, drawn into V1's panel.
 *
 * Four sections through `registerPanelSectionRenderer`, in reading order: the
 * announcer (invisible), parked messages, the return-record cards, the thread
 * digest. Nothing here reaches into `CasePanel.tsx`; the seam is the registry
 * and the only thing V2 asks of V1's files is that
 * `installSupportSections` is imported once.
 *
 * ## Condition 10, on the rendering side
 *
 * Every support-derived value in this file reaches the DOM as a **React text
 * child**. There is no `dangerouslySetInnerHTML` here and no markdown renderer,
 * so `<img src=x onerror=alert(1)>` is a row of characters and not an element --
 * and `supportSections.test.tsx` asserts *both* halves, the literal being
 * present and the element being absent, because either alone passes against a
 * value that was never rendered.
 *
 * The layout half is already done by the time a value arrives here:
 * `readString` collapses whitespace runs, so a value cannot draw itself a second
 * labelled row underneath the one the platform wrote. That is why every value in
 * this file goes through the readers and none is taken off a payload directly.
 *
 * ## Focus
 *
 * **Nothing in these sections takes focus, ever.** The panel polls every ten
 * seconds and an artifact can land between two keystrokes while an associate is
 * mid-sentence in a review draft two sections up. There is no `autoFocus`, no
 * `ref.focus()`, and no `tabIndex` above `-1` anywhere below. What arrives is
 * announced through one polite live region instead, which is the whole reason
 * the announcer section exists.
 */

/* -------------------------------------------------------------------------
 * Shared bits
 * ---------------------------------------------------------------------- */

/**
 * A small labelled chip. **The word is always inside it.**
 *
 * Tone is a second signal, never the only one: a chip distinguished by hue is
 * unreadable to a colour-blind associate and silent to a screen reader. The
 * `label` prefix is what an assistive technology reads out -- "Status: bound" --
 * because "bound" alone, announced between two other chips, names nothing.
 */
function Chip({
  tone,
  label,
  children,
}: {
  readonly tone: keyof typeof COPILOT_TOKENS.support.chipTone;
  readonly label: string;
  readonly children: string;
}) {
  return (
    <span className={`${COPILOT_TOKENS.support.chip} ${COPILOT_TOKENS.support.chipTone[tone]}`}>
      <span className="sr-only">{label}: </span>
      {children}
    </span>
  );
}

/** A `<dl>` row. `null` draws this domain's one word for "not been told". */
function Row({ term, value }: { readonly term: string; readonly value: string | null }) {
  return (
    <div className={COPILOT_TOKENS.support.row}>
      <dt className={COPILOT_TOKENS.support.term}>{term}</dt>
      <dd className={COPILOT_TOKENS.support.value}>{value ?? PENDING_LABEL}</dd>
    </div>
  );
}

/**
 * A section the server composed and could not fill.
 *
 * Not the same as an empty one, and that is the whole reason the backend
 * registry catches a raising contributor rather than failing the panel: "Support
 * has told us nothing about this return" and "we could not read what Support
 * told us" draw identically on a screen that shows neither, and only one of them
 * is a reason to go and ask somebody.
 */
function Degraded({ what }: { readonly what: string }) {
  return (
    <p className={COPILOT_TOKENS.support.notice}>
      {what} could not be loaded just now. This is a display problem, not a change to the
      return -- nothing Support sent has been lost, and the panel will show it on the next
      refresh.
    </p>
  );
}

/**
 * The do-not-mix sentence, chosen by the key the release names.
 *
 * `support_ingress.multi_record_framing_prompt_key` carries the **key**, never
 * the wording -- the config comment is explicit that it "names which of it
 * applies". So the release decides *which* framing an operator gets and the
 * console owns the sentence, which is the right split: the wording is
 * associate-facing copy with a reading level and a tone, and a release that
 * could set it directly would be a way to put arbitrary text on this screen.
 *
 * An unrecognised key falls to the default rather than drawing nothing. A
 * console that silently omitted the warning because a release named a framing it
 * had not shipped would be the one failure this section exists to prevent.
 */
const MULTI_RECORD_FRAMING: Readonly<Record<string, string>> = {
  "support-multi-record-do-not-mix":
    "Support answered about more than one return in a single message. Each card below is a separate return -- check the reference on the card before you use anything on it.",
};

const DEFAULT_FRAMING = MULTI_RECORD_FRAMING["support-multi-record-do-not-mix"];

function framingFor(key: string | null): string {
  return (key === null ? undefined : MULTI_RECORD_FRAMING[key]) ?? DEFAULT_FRAMING;
}

/* -------------------------------------------------------------------------
 * The return-record artifact cards
 * ---------------------------------------------------------------------- */

function ArtifactRows({ artifacts }: { readonly artifacts: readonly SupportArtifact[] }) {
  return (
    <dl className="mt-2">
      {artifacts.map((artifact, index) => (
        <Row
          // Two artifacts of one kind are two real things (two parcels on one
          // return), so the kind alone is not a key. The index is stable
          // because the reader's sort is stable.
          key={`${artifact.artifactType}:${String(index)}`}
          term={artifact.label}
          value={artifact.value}
        />
      ))}
    </dl>
  );
}

function RecordCard({ card }: { readonly card: SupportRecordCard }) {
  return (
    <li className={COPILOT_TOKENS.support.card}>
      <div className={COPILOT_TOKENS.support.cardHeader}>
        {/*
          A real `<h4>` under the section's `<h3>`, not a styled span. This is
          how somebody skimming a case with four returns on a screen reader gets
          from one to the next, and it is the reference that tells them which
          return they have landed on -- which is exactly what the do-not-mix
          warning above is about.
        */}
        <h4 className={COPILOT_TOKENS.support.reference}>
          {card.returnReference ?? PENDING_LABEL}
        </h4>
        {card.status === null ? null : (
          <Chip tone="neutral" label="Return status">
            {card.status}
          </Chip>
        )}
      </div>
      {card.artifacts.length === 0 ? (
        <p className={`${COPILOT_TOKENS.support.term} mt-1`}>
          Support has not sent anything for this return yet.
        </p>
      ) : (
        <ArtifactRows artifacts={card.artifacts} />
      )}
      {card.returnMethod === null ? null : (
        <dl className="mt-1">
          <Row term="Method" value={card.returnMethod} />
        </dl>
      )}
    </li>
  );
}

export function SupportRecordsSection({ section, panel }: PanelSectionRendererProps) {
  if (isDegraded(section)) return <Degraded what="What Support has sent about these returns" />;

  const payload = readRecordsPayload(section, panel.return_records);
  if (payload.records.length === 0 && payload.unbound.length === 0) return null;

  const multiRecord = payload.records.length > 1;

  return (
    <div className="space-y-2">
      <h3 id="support-return-records" className={COPILOT_TOKENS.typography.subheading}>
        What Support has sent
      </h3>

      {multiRecord ? (
        <p className={COPILOT_TOKENS.support.warning}>
          {/*
            Not `role="alert"`. This is a standing property of the case, true for
            as long as the fan-out is on it -- an alert would re-interrupt on
            every ten-second poll. It sits above the cards it is about, which is
            where somebody reading downward meets it before the first reference.
          */}
          {framingFor(payload.framingPromptKey)}
        </p>
      ) : null}

      <ul className="space-y-2">
        {payload.records.map((card) => (
          <RecordCard key={card.returnRecordId} card={card} />
        ))}
      </ul>

      {payload.placement === null ? null : (
        <div className={COPILOT_TOKENS.support.card}>
          {/*
            Drawn once, under the cards, never inside one. Placement is
            case-level -- one facility and one bay per case -- and repeating it
            in each card would read as one placement per return, which is a
            claim the platform does not make.
          */}
          <h4 className={COPILOT_TOKENS.support.reference}>Where the goods go</h4>
          <dl className="mt-2">
            <Row term="Facility" value={payload.placement.facilityId} />
            <Row term="Bay" value={payload.placement.bayId} />
            {payload.placement.reason === null ? null : (
              <Row term="Why" value={payload.placement.reason} />
            )}
          </dl>
        </div>
      )}

      {payload.unbound.length === 0 ? null : (
        <div className={COPILOT_TOKENS.support.notice}>
          <h4 className="mb-1 font-semibold">Sent, but not filed against a return</h4>
          <p className="mb-2">
            Support sent these and the platform could not tell which return they belong to.
            Nothing has been applied. Somebody has to say which is which before they can be
            used.
          </p>
          <ul className="space-y-1">
            {payload.unbound.map((artifact, index) => (
              <li key={`${artifact.artifactType}:${String(index)}`}>
                <span className={COPILOT_TOKENS.support.term}>{artifact.label}: </span>
                <span className={COPILOT_TOKENS.support.value}>
                  {artifact.value ?? PENDING_LABEL}
                </span>
                {artifact.status === null ? null : (
                  <>
                    {" "}
                    <Chip tone="attention" label="Why it is unfiled">
                      {artifact.status}
                    </Chip>
                  </>
                )}
                {artifact.evidenceSpan === null ? null : (
                  <p className={COPILOT_TOKENS.support.term}>
                    From their message: {artifact.evidenceSpan}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * The thread digest
 * ---------------------------------------------------------------------- */

export function SupportDigestSection({ section, panel }: PanelSectionRendererProps) {
  if (isDegraded(section)) return <Degraded what="The messages from Support" />;

  const payload = readDigestPayload(section, panel.support_digest);
  if (payload.messages.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 id="support-thread-digest" className={COPILOT_TOKENS.typography.subheading}>
        Messages from Support
      </h3>
      <ul className="space-y-2">
        {payload.messages.map((message) => (
          <li key={message.supportEventId} className={COPILOT_TOKENS.support.digestRow}>
            <div className={COPILOT_TOKENS.support.cardHeader}>
              <span className={COPILOT_TOKENS.support.reference}>
                {message.sender ?? PENDING_LABEL}
              </span>
              {message.status === null ? null : (
                <Chip tone={message.status === "PARKED" ? "parked" : "neutral"} label="Message state">
                  {message.status}
                </Chip>
              )}
            </div>
            {message.preview === null ? null : (
              <p className={COPILOT_TOKENS.support.value}>{message.preview}</p>
            )}
            {message.intent === null ? null : (
              <p className={COPILOT_TOKENS.support.term}>Read as: {message.intent}</p>
            )}
          </li>
        ))}
      </ul>
      {payload.total === null ? null : (
        <p className={COPILOT_TOKENS.support.term}>
          {/*
            Only said when the contributor said it. The digest is capped and the
            thread is not, so a count derived from what is drawn would tell an
            associate the list is complete when it is not.
          */}
          Showing {String(payload.messages.length)} of {String(payload.total)}.
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Parked messages
 * ---------------------------------------------------------------------- */

export function SupportParkedSection({ section, panel }: PanelSectionRendererProps) {
  if (isDegraded(section)) return <Degraded what="Whether Support has messages waiting" />;

  const payload = readParkedPayload(section, panel.parked_messages);
  if (payload.count === 0) return null;

  const plural = payload.count === 1 ? "message" : "messages";

  return (
    <div className={COPILOT_TOKENS.support.notice}>
      <h3 className="mb-1 font-semibold">
        {String(payload.count)} {plural} from Support {payload.count === 1 ? "is" : "are"} on
        file and not yet read
      </h3>
      <p>
        {/*
          The panel is where an operator learns this. A parked message is not a
          failure and is not lost: sect. 5 parks rather than refuses, so nothing
          has bounced back to Support and nothing needs re-sending.
        */}
        {payload.nlEnabled === false
          ? "Reading messages in their own words is switched off for this platform, so these are being kept as they arrive. They will be read in the order they came in when it is switched on again."
          : "These are being kept until the platform can read them. They will be read in the order they came in."}{" "}
        Nothing has been lost and Support does not need to send them again.
      </p>
      {payload.quota === null ? null : (
        <p className="mt-1">
          This return can hold {String(payload.quota)}. Past that, operations are told.
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * The announcer
 * ---------------------------------------------------------------------- */

/**
 * What has just arrived, said once, to whoever is not looking at the screen.
 *
 * **This section draws nothing visible**, and the reason is worth stating: the
 * artifacts and the parked count are already on the screen. A visible line
 * repeating them would be the same fact twice for a sighted associate, and its
 * absence would be the fact once for everybody else.
 *
 * `polite`, never `assertive` (contracts sect. 9's own rule for this panel, and
 * V1's for `review.liveRegion`): an associate composing a message to a supplier
 * is not interrupted by a tracking number arriving. And the region **never takes
 * focus** -- the caret stays in whatever field it was in. That is the mid-edit
 * rule from `.plan/handoffs/V1-phase2.md` sect. 6, applied to arriving content
 * rather than to a re-seeded draft.
 *
 * The announcement is derived from a *signature* of what is on the panel, and
 * the first render is deliberately silent: a screen reader landing on a case
 * would otherwise be told that everything already on it has "just arrived".
 */
function arrivalSignature(props: PanelSectionRendererProps): string {
  // **The other sections' payloads, not this one's.** `props.section` is the
  // announcer's own contributed section, which carries nothing; a signature
  // built from it would be constant, the announcer would be permanently silent,
  // and every rendering test would still pass because the region is there and
  // empty is a legal value for it. The panel carries all the sections, so they
  // are looked up by the ids both registries key on.
  const find = (id: string) => props.panel.sections.find((entry) => entry.section_id === id);
  const records = readRecordsPayload(
    find(SUPPORT_SECTION_IDS.records),
    props.panel.return_records,
  );
  const parked = readParkedPayload(find(SUPPORT_SECTION_IDS.parked), props.panel.parked_messages);
  const artifacts = records.records.reduce((total, card) => total + card.artifacts.length, 0);
  return `${String(artifacts)}|${String(records.unbound.length)}|${String(parked.count)}`;
}

export function SupportAnnouncerSection(props: PanelSectionRendererProps) {
  const signature = arrivalSignature(props);
  const previous = useRef<string | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const before = previous.current;
    previous.current = signature;
    // First sight of this case: nothing has *arrived*, it was already here.
    if (before === null || before === signature) return;
    const [artifacts, unbound, parked] = signature.split("|").map(Number);
    const [wasArtifacts, wasUnbound, wasParked] = before.split("|").map(Number);
    const parts: string[] = [];
    if (artifacts + unbound > wasArtifacts + wasUnbound) {
      parts.push("Support has sent something new about this return.");
    }
    if (parked > wasParked) {
      parts.push("A message from Support is on file and not yet read.");
    }
    // Nothing to say about a count that went down -- an artifact being filed
    // against a return is not news an associate needs read aloud mid-sentence.
    setMessage(parts.join(" "));
  }, [signature]);

  return (
    <p role="status" aria-live="polite" className={COPILOT_TOKENS.support.announcer}>
      {message}
    </p>
  );
}
