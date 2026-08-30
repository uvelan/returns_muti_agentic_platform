import { useCallback, useId, useState } from "react";
import { HelpCircle, Link2, Send, Unlink } from "lucide-react";

import {
  MAP_CHOICE,
  MAX_ANSWER_CHARACTERS,
  REJECT_CHOICE,
  asClarificationRefusal,
  caseClarificationsApi,
  type ResolutionChoice,
} from "../../../../../api/caseClarifications";
import { COPILOT_TOKENS } from "../../../copilotTokens";
import type { PanelSectionRendererProps } from "../panelSectionRegistry";
import {
  CLARIFICATIONS_SECTION_ID,
  attemptWords,
  candidateRecords,
  neededFieldWords,
  readClarifications,
  type CandidateRecord,
  type CaseClarification,
} from "./clarificationModel";

/**
 * What Support is asking the associate (contracts.md sect. 9).
 *
 * The platform tried to answer Support itself and could not, so it is asking the
 * person holding the box. This section says **what was asked, why the platform
 * could not answer it, what it tried, and what it needs** -- and then offers the
 * one form that answers it.
 *
 * ---
 *
 * ## Support-derived values are data, never markup
 *
 * This is the **inbound** direction, and it is the sharper one. The outbound
 * path already neutralises framing before Channel B text is composed; nothing on
 * that path protects this screen, because the risk here is not a forged heading
 * in a message to Support but a forged *interface* in front of the associate.
 *
 * Three values on this surface are attacker-influenced: `verbatimQuestion`
 * (sect. 9 requires the question **verbatim**, so it cannot be rewritten),
 * `artifactValue`, and `evidenceSpan`. Every one of them reaches the DOM as a
 * React text child, which escapes it. There is no `dangerouslySetInnerHTML` on
 * this surface, no markdown renderer, and no `innerHTML` anywhere in this
 * module. `ClarificationsSection.test.tsx` feeds a script tag and an
 * `<img onerror>` through all three and asserts the rendered text as an
 * **equality** as well as the absence of the elements -- the absence assertion
 * alone would pass against a field that never rendered at all.
 *
 * **Escaped, not mangled.** "Verbatim" is a contract requirement, so the
 * associate has to see what Support actually wrote -- colons, RMA numbers, bay
 * names and all. Nothing here strips, rewrites, truncates or "cleans" the
 * question. Escaping is the browser's job and it does it losslessly; a
 * neutraliser here would be the console deciding an associate should not read
 * their own supplier's words, and V3's backend round 2 already established that
 * narrowness is the whole game -- ten realistic support sentences with colons,
 * RMAs and bays survived byte for byte, and they must survive here too.
 *
 * The one layout decision that follows: the question is **not** rendered
 * `whitespace-pre-wrap`. The DOM text is byte-identical either way -- it is what
 * a screen reader announces and what a copy-paste yields -- but honouring
 * newlines would let a message with embedded line breaks lay itself out as
 * though it were several structured statements, which is the "restructure the
 * view" half of the rule. It is quoted instead, so its boundary is unambiguous.
 */

export function ClarificationsSection({ section, panel, caseId }: PanelSectionRendererProps) {
  const clarifications = readClarifications(panel, section);
  const arrival = useArrivalAnnouncement(clarifications);

  if (section?.status === "degraded") {
    /*
     * The contributor raised and the composer degraded it (V1 phase 2 handoff,
     * §2). Said out loud rather than drawn as nothing: "Support has not asked
     * anything" and "we could not find out whether Support asked anything" are
     * different, and only one of them means an associate should check the
     * thread themselves.
     */
    return (
      <p role="status" className={COPILOT_TOKENS.typography.caption}>
        Whether Support is waiting on an answer could not be read just now. It will be retried on
        the next refresh.
      </p>
    );
  }

  if (clarifications.length === 0) {
    /*
     * Nothing asked, nothing said -- the panel's own rule for the quiet case. A
     * permanent "Support is not asking anything" would be furniture reporting an
     * absence on the great majority of returns.
     */
    return null;
  }

  return (
    <section aria-labelledby={`${CLARIFICATIONS_SECTION_ID}-heading`} className="space-y-3">
      <h3
        id={`${CLARIFICATIONS_SECTION_ID}-heading`}
        className={COPILOT_TOKENS.typography.subheading}
      >
        <HelpCircle aria-hidden="true" className="mr-1.5 inline size-3.5" />
        Support is asking you this
      </h3>

      {/*
        A clarification that lands mid-interaction is announced and **takes no
        focus** (WCAG 3.2.1, and the panel's own mid-edit rule). The associate
        may be halfway through typing a message to the same supplier; moving the
        caret out of that field to a question they have not decided to read yet
        is the interruption this region exists to avoid. `polite`, never
        `assertive`, for the same reason -- it waits for a pause.
      */}
      <p role="status" aria-live="polite" className={COPILOT_TOKENS.review.liveRegion}>
        {arrival}
      </p>

      {clarifications.map((clarification) => (
        <ClarificationCard
          key={clarification.clarificationId}
          caseId={caseId}
          clarification={clarification}
          candidates={candidateRecords(clarification, panel)}
        />
      ))}
    </section>
  );
}

/**
 * "A new question arrived", derived during render rather than in an effect.
 *
 * The panel polls every ten seconds, so a clarification can appear between two
 * keystrokes. An effect would paint the new card first and announce it on the
 * following commit, which for a screen-reader user is the announcement arriving
 * after the thing it announces. Deriving it during render is React's own
 * documented adjust-state-on-props-change pattern and is what `useDraftEditor`
 * does for the same reason.
 *
 * The **first** render announces nothing: a card that was already there when the
 * pane opened has not arrived, and announcing the whole list on mount is how a
 * status region becomes something people learn to ignore.
 *
 * **A `Set`, not a delimiter-joined string.** The first draft of this hook
 * compared `ids.join(sep)` against a stored string, and the separator it shipped
 * with was a literal NUL byte -- which made the file binary to git and would
 * have made it unreviewable. A set needs no separator, so it has none to get
 * wrong: this run has already spent three attempts on a guarantee that rested on
 * a separator rather than on the property it claimed.
 */
function useArrivalAnnouncement(clarifications: readonly CaseClarification[]): string {
  const [seen, setSeen] = useState<ReadonlySet<string>>(
    () => new Set(clarifications.map((entry) => entry.clarificationId)),
  );
  const [announcement, setAnnouncement] = useState("");

  const arrived = clarifications.filter((entry) => !seen.has(entry.clarificationId));
  if (arrived.length > 0) {
    setSeen(new Set([...seen, ...arrived.map((entry) => entry.clarificationId)]));
    setAnnouncement(
      arrived.length === 1
        ? "Support is asking you something new. It is below."
        : `Support is asking you ${String(arrived.length)} new things. They are below.`,
    );
  }

  return announcement;
}

/* -------------------------------------------------------------------------
 * One question
 * ---------------------------------------------------------------------- */

function ClarificationCard({
  caseId,
  clarification,
  candidates,
}: {
  readonly caseId: string;
  readonly clarification: CaseClarification;
  readonly candidates: readonly CandidateRecord[];
}) {
  const fieldId = useId();
  const mapOrReject = clarification.choice === "MAP_OR_REJECT";

  const [answerText, setAnswerText] = useState("");
  const [choice, setChoice] = useState<ResolutionChoice | null>(null);
  const [recordId, setRecordId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setRefusal(null);

    /*
     * The four refusals below mirror the server's, and none of them replaces
     * it. Client-side validation is for the person; the endpoint still checks
     * every one of these, and it is the endpoint's answer that is
     * authoritative. Checking here only means the associate is told at the
     * counter instead of after a round trip.
     */
    if (answerText.trim().length === 0) {
      setRefusal("Write your answer first. Support sees exactly what you write here.");
      return;
    }
    if (answerText.length > MAX_ANSWER_CHARACTERS) {
      setRefusal(
        `That is longer than ${String(MAX_ANSWER_CHARACTERS)} characters. Shorten it — it is refused rather than cut, because the part that gets cut is often the part naming the return.`,
      );
      return;
    }
    if (mapOrReject && choice === null) {
      setRefusal("Choose which return this belongs to, or say it belongs to none of them.");
      return;
    }
    /*
     * **The one that is not a convenience.** A `map` with no record is refused
     * server-side as `CLARIFICATION_MAP_WITHOUT_RECORD`, because "map this to
     * nothing" is not a decision anybody can have meant and a later step
     * inventing a record for it is the create-from-a-loose-artifact behaviour
     * sect. 4 forbids. The form never offers a free-text record either: the
     * only bindable records are the ones the case holds, and a box an associate
     * could type an RMA into is a box they can type the wrong RMA into.
     */
    if (choice === MAP_CHOICE && recordId === null) {
      setRefusal("Pick the return it belongs to.");
      return;
    }

    setBusy(true);
    try {
      const accepted = await caseClarificationsApi.answer(caseId, clarification.clarificationId, {
        answerText: answerText.trim(),
        resolutionChoice: mapOrReject ? choice : null,
        returnRecordId: choice === MAP_CHOICE ? recordId : null,
      });
      setReceipt(
        accepted.duplicate
          ? "This was already answered. The answer on file stands."
          : "Your answer is recorded. Support will be told.",
      );
    } catch (error) {
      const refused = asClarificationRefusal(error);
      setRefusal(
        refused === null
          ? "That could not be recorded. Nothing was sent to Support."
          : refused.message,
      );
    } finally {
      setBusy(false);
    }
  }, [answerText, caseId, choice, clarification.clarificationId, mapOrReject, recordId]);

  return (
    <article className={`${COPILOT_TOKENS.review.clarification} space-y-2`}>
      {/*
        The question, quoted. `<blockquote>` rather than a styled paragraph
        because the boundary between what Support wrote and what this console
        wrote is the whole point: everything outside the quotation marks is
        ours, everything inside is theirs, and an associate deciding whether to
        trust a sentence needs to be able to tell which is which without reading
        carefully.

        Rendered as a React text child. See this module's header.
      */}
      <blockquote className={COPILOT_TOKENS.typography.body}>
        “{clarification.verbatimQuestion}”
      </blockquote>

      <dl className="space-y-1">
        <div className={COPILOT_TOKENS.review.field.row}>
          <dt className={COPILOT_TOKENS.review.field.label}>Why we are asking</dt>
          <dd className={COPILOT_TOKENS.review.field.value}>
            {clarification.whyUnresolvable === ""
              ? "Unavailable"
              : clarification.whyUnresolvable}
          </dd>
        </div>
        <div className={COPILOT_TOKENS.review.field.row}>
          <dt className={COPILOT_TOKENS.review.field.label}>What we need</dt>
          <dd className={COPILOT_TOKENS.review.field.value}>
            {clarification.neededField === ""
              ? "Unavailable"
              : neededFieldWords(clarification.neededField)}
          </dd>
        </div>
        {clarification.resolutionAttempts.length > 0 ? (
          <div className={COPILOT_TOKENS.review.field.row}>
            <dt className={COPILOT_TOKENS.review.field.label}>What we tried</dt>
            <dd className="flex flex-wrap gap-1.5">
              {clarification.resolutionAttempts.map((attempt, index) => (
                <span
                  key={`${attempt}-${String(index)}`}
                  className={COPILOT_TOKENS.review.provenance}
                >
                  {attemptWords(attempt)}
                </span>
              ))}
            </dd>
          </div>
        ) : null}
      </dl>

      {mapOrReject ? <ArtifactEvidence clarification={clarification} /> : null}

      {receipt !== null ? (
        /*
          The receipt says what committed and no more. There is deliberately no
          "Support has seen it": the relay happens in an activity after the
          signal lands, and a line here claiming it would be this screen
          reporting work it did not wait for.
        */
        <p role="status" className={COPILOT_TOKENS.typography.caption}>
          {receipt}
        </p>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {mapOrReject ? (
            <MapOrReject
              fieldId={fieldId}
              candidates={candidates}
              choice={choice}
              recordId={recordId}
              onChoice={(next) => {
                setChoice(next);
                if (next === REJECT_CHOICE) setRecordId(null);
              }}
              onRecord={(next) => {
                setChoice(MAP_CHOICE);
                setRecordId(next);
              }}
            />
          ) : null}

          <div>
            <label htmlFor={`${fieldId}-answer`} className={COPILOT_TOKENS.review.field.label}>
              {mapOrReject ? "How do you know?" : "Your answer"}
            </label>
            <textarea
              id={`${fieldId}-answer`}
              name="answerText"
              value={answerText}
              rows={3}
              maxLength={MAX_ANSWER_CHARACTERS}
              aria-describedby={`${fieldId}-answer-hint`}
              className={`${COPILOT_TOKENS.review.field.input} mt-1`}
              onChange={(event) => {
                setAnswerText(event.target.value);
                // Clear on input, check on submit: nobody should be told they
                // are wrong while they are still typing the thing.
                setRefusal(null);
              }}
            />
            <p id={`${fieldId}-answer-hint`} className={COPILOT_TOKENS.typography.caption}>
              Support sees this. Write it as you would say it to them.
            </p>
          </div>

          {refusal !== null ? (
            <p role="alert" className={COPILOT_TOKENS.review.gap}>
              {refusal}
            </p>
          ) : null}

          <div className={COPILOT_TOKENS.review.action.bar}>
            {/*
              `aria-disabled`, never `disabled`. A disabled button leaves the tab
              order, so a keyboard associate tabbing here would find nothing and
              no way to learn why. This one is reachable, announced as disabled
              while the answer is in flight, and it is `submit`, so the form is
              completable without a mouse from the first field.
            */}
            <button
              type="submit"
              className={COPILOT_TOKENS.review.action.primary}
              aria-disabled={busy}
            >
              <Send aria-hidden="true" className="size-3.5" />
              Send this to Support
            </button>
          </div>
        </form>
      )}
    </article>
  );
}

/* -------------------------------------------------------------------------
 * The unmatched artifact
 * ---------------------------------------------------------------------- */

/**
 * What Support sent, and where in their message it came from.
 *
 * Both values are support-derived. Both are React text children.
 *
 * The evidence span is shown rather than summarised because "Support mentioned a
 * tracking number" and "Support wrote RMA-4471, and this case has no RMA-4471"
 * are different amounts of help, and only the second lets an associate notice
 * that Support has the wrong case entirely.
 */
function ArtifactEvidence({ clarification }: { readonly clarification: CaseClarification }) {
  return (
    <dl className="space-y-1">
      <div className={COPILOT_TOKENS.review.field.row}>
        <dt className={COPILOT_TOKENS.review.field.label}>Support sent</dt>
        <dd className={COPILOT_TOKENS.review.field.value}>
          {clarification.artifactValue ?? "Unavailable"}
        </dd>
      </div>
      <div className={COPILOT_TOKENS.review.field.row}>
        <dt className={COPILOT_TOKENS.review.field.label}>They called it</dt>
        <dd className={COPILOT_TOKENS.review.field.value}>
          {clarification.evidenceSpan ?? "Unavailable"}
        </dd>
      </div>
    </dl>
  );
}

/**
 * Bind it to one of this case's returns, or say it belongs to none of them.
 *
 * **Radios over a select**, and only ever over the case's own records. Visible
 * radios below six options is the usual rule, and the reason bites harder than
 * usual here: the choice is which physical package a label or a tracking number
 * belongs to, and a collapsed list is one an associate can commit to without
 * having read the alternatives. If a case ever holds enough returns for this to
 * be unwieldy, that is a case where reading all of them matters more, not less.
 *
 * **There is no free-text record box, and there will not be one.** A loose
 * artifact never creates a record (sect. 4), so a typed RMA could only ever be
 * one the case already holds -- which the radios already offer -- or a wrong
 * one, silently accepted.
 */
function MapOrReject({
  fieldId,
  candidates,
  choice,
  recordId,
  onChoice,
  onRecord,
}: {
  readonly fieldId: string;
  readonly candidates: readonly CandidateRecord[];
  readonly choice: ResolutionChoice | null;
  readonly recordId: string | null;
  readonly onChoice: (choice: ResolutionChoice) => void;
  readonly onRecord: (recordId: string) => void;
}) {
  return (
    <fieldset className="space-y-1.5">
      <legend className={COPILOT_TOKENS.review.field.label}>
        <Link2 aria-hidden="true" className="mr-1 inline size-3.5" />
        Which return is this for?
      </legend>

      {/*
        The do-not-mix framing, and it is contract text rather than a caption. A
        case with two RMAs is two physical packages going to two places, and the
        single failure this whole clarification exists to prevent is a label or a
        tracking number being attached to the wrong one. Naming the consequence
        is what makes the choice a decision instead of a click.
      */}
      <p className={COPILOT_TOKENS.typography.caption}>
        These are separate returns going separate ways. Attach this to the wrong one and the
        package goes to the wrong place.
      </p>

      {candidates.map((candidate) => {
        const id = `${fieldId}-record-${candidate.returnRecordId}`;
        return (
          <div key={candidate.returnRecordId} className="flex items-start gap-2">
            <input
              type="radio"
              id={id}
              name={`${fieldId}-resolution`}
              className="mt-1 accent-primary"
              checked={choice === MAP_CHOICE && recordId === candidate.returnRecordId}
              onChange={() => {
                onRecord(candidate.returnRecordId);
              }}
            />
            <label htmlFor={id} className={COPILOT_TOKENS.review.field.value}>
              {candidate.returnReference === ""
                ? candidate.returnRecordId
                : candidate.returnReference}
              {candidate.returnMethod === "" ? "" : ` · ${candidate.returnMethod}`}
              {candidate.status === "" ? "" : ` · ${candidate.status}`}
            </label>
          </div>
        );
      })}

      <div className="flex items-start gap-2">
        <input
          type="radio"
          id={`${fieldId}-reject`}
          name={`${fieldId}-resolution`}
          className="mt-1 accent-primary"
          checked={choice === REJECT_CHOICE}
          onChange={() => {
            onChoice(REJECT_CHOICE);
          }}
        />
        <label htmlFor={`${fieldId}-reject`} className={COPILOT_TOKENS.review.field.value}>
          <Unlink aria-hidden="true" className="mr-1 inline size-3.5" />
          None of these — it is not for any return on this case
        </label>
      </div>
    </fieldset>
  );
}
