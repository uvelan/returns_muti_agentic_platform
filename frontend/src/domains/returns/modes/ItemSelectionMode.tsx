import { useId, useState } from "react";
import {
  Camera,
  CheckSquare,
  ChevronRight,
  MapPin,
  ShieldCheck,
  Square,
  UploadCloud,
  UserRound,
} from "lucide-react";
import type { SelectedItemProjection } from "../../../api/cases";
import {
  selectableQuantity,
  type OrderLineView,
  type ReturnContactRequest,
  type SelectedItemRequest,
} from "../../../api/orderLines";

/**
 * Where the return is actually set up: which lines, how many, why, in what state.
 *
 * This pane used to open on two invented products -- a motor at a made-up price
 * and a flange -- against an invented order, shipping to a hardcoded Charlotte
 * hub, with a six-entry reason catalogue and a four-entry condition catalogue
 * nothing configured. All of it is gone, and the fabrication guard reads this
 * file back to keep it gone.
 *
 * **There are no prices on the wire that this pane will render.**
 * `OrderLineView` can carry a `unitPrice`, but the case carries no order total
 * and `SelectedItemProjection` carries no price at all, so a credit estimate
 * would be a figure multiplied out of one line and presented as the return's
 * value. It says there is nothing to compute instead.
 *
 * **The controls are live now, and that is the change.** Their docstring used
 * to say they were inert because no case-scoped selection write existed; it
 * does -- `POST /api/cases/{caseId}/selected-items` records quantity, reason
 * and condition per line, holds the quantity, and refuses a term the active
 * release does not publish with `422 SELECTION_TERM_NOT_PUBLISHED`. The
 * principle behind the disabling is kept exactly: **anything that cannot be
 * made real stays visibly unavailable.** The evidence attachment is that case
 * and is drawn as a declared placeholder rather than as a button.
 *
 * **The catalogues are the release's.** `reasons` and `conditions` arrive from
 * `/api/runtime-config`, which serves `selection_vocabulary` off the active
 * return configuration. An empty catalogue means the deployment has published
 * none -- the writer then refuses nothing -- and the control says so rather
 * than offering a list this file made up.
 *
 * **Bounded lists.** An order can have any number of lines and a matching
 * catalogue can grow past today's twelve entries, so nothing here maps over a
 * collection whose length is decided by data without a cap: lines are revealed
 * a page at a time with the remainder counted, and the two catalogues are
 * native `select` elements, which stay usable at any length.
 *
 * **The branch associate is collected here and is case-level.** Fergusonhome's
 * list of what it needs to set a return up ends with the associate's name,
 * email and phone, "needed for UPS label or Freight LTL" -- and until now they
 * had no source anywhere in the platform, so the facts panel correctly showed
 * nothing for them. One associate raises one return, so they are emphatically
 * not a per-line field: they travel as `contact`, a sibling of `items` on the
 * selection write, and land on the case fact log beside every other case-level
 * detail. `draft_support_request` reads that log, which is how they reach the
 * desk that raises the label.
 *
 * They are **optional**, by the same operator instruction that makes the branch
 * number optional, and for the same reason the branch has no default: an
 * invented hub routes freight, and an invented associate email addresses a
 * label to nobody. Shape is validated and existence is not -- an email that is
 * not an email is refused at entry; an absent one blocks nothing.
 *
 * **The reason opens on what the associate already said, when it is safe to.**
 * `return_reason` is a captured fact, and asking again for something stated in
 * the first sentence is the complaint this pane exists to answer. But a
 * captured reason is *free text* -- `clarification_policy.fields` gives
 * `return_reason` no `validation_pattern`, so the model may return "the pump
 * arrived cracked" -- while the select's options are published enum terms. The
 * join is therefore exact and nothing else: a captured value that *is* a
 * published term (ignoring case and surrounding space) is pre-selected, and
 * anything else is reported as heard with the select left unset. Mapping
 * "cracked" onto `SHIPPING_DAMAGE` would put a policy-bearing term on the case
 * that nobody stated, and `SHIPPING_DAMAGE` is a term the evaluator routes a
 * delivery claim on.
 */

/**
 * What the case holds about the branch associate, off `CaseProjection.facts`.
 *
 * Three separate nullable strings rather than one nullable object: a return can
 * perfectly well name a person and no phone number, and an object that was
 * present or absent as a unit would have to invent a rule for the half-filled
 * case that is the common one.
 */
export type BranchAssociateContact = {
  readonly name: string | null;
  readonly email: string | null;
  readonly phone: string | null;
};

export type ItemSelectionModeProps = {
  orderReference?: string | null;
  /** The confirmed order's lines. Empty until `GET /order-lines` answers. */
  lines?: readonly OrderLineView[];
  /**
   * The lines the confirmation named, as `GET /order-lines` serves them.
   *
   * The pane opens on these: they are drawn, they are ticked, and the rest of
   * the order waits behind a control. Empty is an order-level confirmation --
   * a real one -- and then every line is drawn as before.
   *
   * Scope rather than filter. An associate who finds a second faulty item on
   * the same order raises it on this case, so the lines the confirmation did
   * not name stay reachable; they are just not what the screen opens on.
   */
  confirmedLineReferences?: readonly string[];
  linesPending?: boolean;
  linesError?: Error | null;
  /** What the case already holds, which is what the controls open on. */
  items?: readonly SelectedItemProjection[];
  /** The branch the case is anchored to. No default: an invented hub routes freight. */
  branchReference?: string | null;
  /** The branch associate the case has recorded. Every part optional. */
  contact?: BranchAssociateContact;
  /** The released reason catalogue. Empty means none is published. */
  reasons?: readonly string[];
  /** The released condition catalogue. Empty means none is published. */
  conditions?: readonly string[];
  /**
   * The `return_reason` the conversation captured, verbatim.
   *
   * Free text, and treated as such. It pre-selects a line's reason only when it
   * is exactly one of `reasons`; otherwise it is shown as what was heard and
   * the associate chooses.
   */
  capturedReason?: string | null;
  /**
   * `contact` is `null` when the associate never touched those fields, which is
   * a different statement from three empty strings: the first says nothing
   * about the branch associate and the second retracts what the case holds.
   */
  onSubmitSelection?: (
    items: readonly SelectedItemRequest[],
    contact: ReturnContactRequest | null,
  ) => void;
  submitting?: boolean;
  submitError?: Error | null;
};

const PENDING = "Pending";

/**
 * What an absent branch reads as. One of this domain's "the platform has not
 * said" words rather than a business value, and named so that the sentence it
 * appears in is not a quoted literal on the right of a `??` -- the exact
 * construction the fabrication guard bans, because that is the shape an
 * invented hub would arrive in.
 */
const BRANCH_ABSENT = "Not recorded (optional)";

/** How many order lines are drawn at once, and how many each reveal adds. */
const LINE_PAGE = 8;

type LineDraft = {
  /** Text, not a number: an empty box is "not stated yet", and `0` is not. */
  readonly quantity: string;
  readonly reason: string;
  readonly condition: string;
};

/**
 * A line the associate has not touched this session, as the case holds it.
 *
 * `null` means not selected. Absent from the draft map means "no opinion yet",
 * which is what lets the pane open on the case's own selection without an
 * effect that would fight the associate's typing on every poll.
 */
function committed(item: SelectedItemProjection | undefined): LineDraft | null {
  if (item === undefined) return null;
  return {
    quantity: item.quantity === null ? "" : String(item.quantity),
    reason: item.reason ?? "",
    condition: item.condition ?? "",
  };
}

function positiveQuantity(draft: LineDraft, line: OrderLineView): number | null {
  const parsed = Number(draft.quantity);
  if (!Number.isInteger(parsed) || parsed < 1) return null;
  return parsed > selectableQuantity(line) ? null : parsed;
}

/**
 * What has to be true for an address to be an address.
 *
 * The same weak shape `ReturnContactRequest` enforces on the server, and weak
 * on purpose: the strict forms of this check are wrong in the same direction,
 * refusing addresses a carrier portal accepts. It catches a phone number typed
 * into the email box, which is the failure that produces a label request nobody
 * can answer. Duplicated rather than derived because the server must refuse it
 * whatever the client does, and the client must refuse it before the associate
 * has walked away from the counter.
 */
const EMAIL_SHAPE = /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/;

/** The three boxes as typed. Text, not `null`s: an empty box is a cleared box. */
type ContactDraft = {
  readonly name: string;
  readonly email: string;
  readonly phone: string;
};

/** What the case holds, as the boxes would show it. */
function heldContact(contact: BranchAssociateContact | undefined): ContactDraft {
  return {
    name: contact?.name ?? "",
    email: contact?.email ?? "",
    phone: contact?.phone ?? "",
  };
}

function anyStated(contact: ContactDraft): boolean {
  return contact.name !== "" || contact.email !== "" || contact.phone !== "";
}

/**
 * A non-empty, trimmed string, or `null`.
 *
 * Named rather than inlined so the "nothing was said" case is one value that
 * every reader below tests the same way.
 */
function spoken(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/**
 * The captured reason **as a published term**, or `null`.
 *
 * The one join between what the model heard and what the release publishes, and
 * the place this file is most able to do damage. `return_reason` carries no
 * validation pattern in `clarification_policy.fields`, so the captured value is
 * whatever the associate said; `reasons` are `ReturnReason` members, the
 * evaluator's own closed vocabulary, on which `SHIPPING_DAMAGE` and
 * `MANUFACTURER_WARRANTY_ISSUE` route the case down entirely different paths.
 *
 * So the match is **exact** -- identical after trimming and case-folding -- and
 * nothing else. No synonym table, no separator normalisation, no nearest
 * neighbour. Anything looser is a mapping this file invented, and a mapping
 * this file invented would put a term on the case that no one stated while
 * looking exactly like a term someone did.
 *
 * An ambiguous catalogue -- two published terms that fold together -- resolves
 * to `null` for the same reason: choosing between them is not this file's
 * choice to make.
 */
function publishedTerm(heard: string | null, reasons: readonly string[]): string | null {
  const said = spoken(heard);
  if (said === null) return null;
  const folded = said.toLowerCase();
  const matched = reasons.filter((reason) => reason.trim().toLowerCase() === folded);
  return matched.length === 1 ? matched[0] : null;
}

export function ItemSelectionMode({
  orderReference = null,
  lines = [],
  confirmedLineReferences = [],
  linesPending = false,
  linesError = null,
  items = [],
  branchReference = null,
  contact,
  reasons = [],
  conditions = [],
  capturedReason = null,
  onSubmitSelection,
  submitting = false,
  submitError = null,
}: ItemSelectionModeProps) {
  const [draft, setDraft] = useState<Readonly<Record<string, LineDraft | null>>>({});
  const [visible, setVisible] = useState<number>(LINE_PAGE);
  /** Whether the associate has asked for the lines the confirmation did not name. */
  const [showEveryLine, setShowEveryLine] = useState<boolean>(false);
  /**
   * The contact boxes, once the associate has touched them.
   *
   * `null` means untouched, exactly as an absent key in `draft` does for a
   * line, and for the same reason: the pane opens on what the case holds and a
   * ten-second poll must not fight the associate's typing. It is also what the
   * submit sends, so an untouched fieldset makes no claim at all rather than
   * re-asserting values the case already has.
   */
  const [contactDraft, setContactDraft] = useState<ContactDraft | null>(null);

  /** What the conversation stated, verbatim, or nothing. */
  const heardReason = spoken(capturedReason);
  /** That same value only if the release publishes it. Never a guess. */
  const preselectedReason = publishedTerm(capturedReason, reasons);

  const confirmedScope = new Set(confirmedLineReferences);
  const held = new Map(items.map((item) => [item.orderLineReference, item]));
  function entry(reference: string): LineDraft | null {
    if (reference in draft) return draft[reference];
    const recorded = committed(held.get(reference));
    if (recorded !== null) return recorded;
    // A line the confirmation named opens ticked, in the same shape ticking it
    // by hand produces -- an empty quantity, which keeps the submit refused
    // until the associate states one. Derived rather than seeded into `draft`
    // so unticking it still works: writing the key is what makes the
    // associate's choice win over this default.
    return confirmedScope.has(reference)
      ? { quantity: "", reason: preselectedReason ?? "", condition: "" }
      : null;
  }
  function write(reference: string, next: LineDraft | null) {
    setDraft((previous) => ({ ...previous, [reference]: next }));
  }

  const chosen = lines.flatMap((line) => {
    const current = entry(line.lineReference);
    return current === null ? [] : [{ line, current }];
  });

  // A published catalogue is a question the associate can answer, so it is one
  // they must: the reason is what Fergusonhome needs to set the return up, and
  // what `case_policy_facts` reads as `return_reason`. Where no catalogue is
  // published there is nothing to pick, and requiring an answer nobody can give
  // would take the pane offline over a key the release does not carry.
  const reasonRequired = reasons.length > 0;
  const incomplete = chosen.filter(
    ({ line, current }) =>
      positiveQuantity(current, line) === null || (reasonRequired && current.reason === ""),
  );

  const recordedContact = heldContact(contact);
  const contactShown = contactDraft ?? recordedContact;
  // Shape, never existence. An absent email is the ordinary state of an
  // optional field; one that cannot receive a label is a typo the associate is
  // still standing there to fix, and letting it through produces a Support
  // request addressed to nothing.
  const emailRefused = contactShown.email !== "" && !EMAIL_SHAPE.test(contactShown.email);
  const emailErrorId = useId();

  const submittable =
    onSubmitSelection !== undefined &&
    !submitting &&
    chosen.length > 0 &&
    incomplete.length === 0 &&
    !emailRefused;

  function submit() {
    if (!submittable) return;
    onSubmitSelection(
      chosen.flatMap(({ line, current }) => {
        const quantity = positiveQuantity(current, line);
        if (quantity === null) return [];
        return [
          {
            orderLineReference: line.lineReference,
            quantity,
            // Absent rather than empty: the writer distinguishes "no reason
            // given" from a blank string, and a blank would be a published-term
            // check against nothing.
            ...(current.reason === "" ? {} : { reason: current.reason }),
            ...(current.condition === "" ? {} : { condition: current.condition }),
          },
        ];
      }),
      // The draft, not what is on screen. An untouched fieldset says nothing
      // about the branch associate; re-sending the case's own values would be
      // this screen asserting a contact it merely read.
      contactDraft,
    );
  }

  /**
   * The lines this pane opens on: the confirmed ones, until asked for the rest.
   *
   * `confirmedScope` empty means the confirmation named no line, and then the
   * order's lines are the scope -- the behaviour every case had before the
   * references were served.
   */
  const scoped =
    confirmedScope.size === 0 || showEveryLine
      ? lines
      : lines.filter((line) => confirmedScope.has(line.lineReference));
  const shown = scoped.slice(0, visible);
  const hidden = scoped.length - shown.length;
  /** Lines of this order the confirmation did not name, and which are not drawn. */
  const withheld = lines.length - scoped.length;

  return (
    <div className="flex flex-col gap-4">
      {/* 1. Header & branch routing context */}
      <div className="flex flex-col gap-2 rounded-xl border border-outline-variant/30 bg-surface-container-low p-3.5 shadow-xs">
        <div className="flex items-center justify-between">
          <div>
            <span className="text-[11px] font-bold uppercase tracking-wider text-primary">
              Return Line Item Scope
            </span>
            <h3 className="text-sm font-bold text-on-surface">
              Sales Order {orderReference ?? PENDING}
            </h3>
          </div>
          <span className="rounded-full bg-secondary-container px-2.5 py-0.5 text-xs font-semibold text-primary">
            {String(chosen.length)} Selected
          </span>
        </div>

        <div className="flex items-center gap-1.5 border-t border-outline-variant/20 pt-2 text-xs text-outline">
          <MapPin size={13} className="text-secondary shrink-0" />
          {/* Optional by operator instruction. A case whose principal covers
              several branches has none, and the return is not blocked on it. */}
          <span className="truncate">
            Branch:{" "}
            <strong className="text-on-surface">
              {branchReference ?? BRANCH_ABSENT}
            </strong>
          </span>
        </div>
      </div>

      {/* 2. The branch associate. Case-level, like the branch above it. */}
      <div className="flex flex-col gap-2.5 rounded-xl border border-outline-variant/30 bg-surface-container-low p-3.5 shadow-xs">
        <div className="flex items-baseline justify-between gap-2">
          <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-outline">
            <UserRound size={13} className="text-secondary shrink-0" />
            <span>Branch associate contact</span>
          </span>
          <span className="shrink-0 text-[11px] text-outline">Optional</span>
        </div>

        <p className="text-[11px] leading-relaxed text-outline">
          {/* Why it is asked for, in the words of the requirement, so an
              associate can tell whether it matters for this return. */}
          Who a carrier can reach about this return. Needed to raise a UPS label or a Freight LTL
          bill of lading; nothing here is filled in for you, and a return with none still goes.
        </p>

        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-outline">Name</span>
            <input
              type="text"
              autoComplete="off"
              maxLength={200}
              value={contactShown.name}
              onChange={(event) => {
                setContactDraft({ ...contactShown, name: event.target.value });
              }}
              className="rounded-lg border border-outline-control bg-surface px-2 py-1 text-xs font-medium text-on-surface"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-outline">Email address</span>
            <input
              type="email"
              inputMode="email"
              autoComplete="off"
              maxLength={320}
              aria-invalid={emailRefused}
              // The refusal was on screen and not attached to anything, so a
              // screen reader said "invalid" and never said why. Described-by
              // rather than a live region on purpose: this recomputes on every
              // keystroke, and an alert would interrupt on each one while
              // someone is still halfway through typing the address.
              aria-describedby={emailRefused ? emailErrorId : undefined}
              value={contactShown.email}
              onChange={(event) => {
                setContactDraft({ ...contactShown, email: event.target.value });
              }}
              className="rounded-lg border border-outline-control bg-surface px-2 py-1 text-xs font-medium text-on-surface aria-[invalid=true]:border-error"
            />
            {emailRefused ? (
              <span id={emailErrorId} className="text-[10px] text-error">
                Not an address a label could be sent to. Correct it, or leave it empty.
              </span>
            ) : null}
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-semibold text-outline">Phone number</span>
            <input
              type="tel"
              inputMode="tel"
              autoComplete="off"
              maxLength={64}
              value={contactShown.phone}
              onChange={(event) => {
                setContactDraft({ ...contactShown, phone: event.target.value });
              }}
              className="rounded-lg border border-outline-control bg-surface px-2 py-1 text-xs font-medium text-on-surface"
            />
            {/* No format is imposed. Branch numbers in the source are written
                half a dozen ways and a pattern invented here would refuse the
                ones a carrier can actually dial. */}
          </label>
        </div>

        {contactDraft === null && !anyStated(recordedContact) ? (
          <span className="text-[11px] text-outline">
            Not recorded (optional). Submitting without it does not hold the return up.
          </span>
        ) : null}
      </div>

      {/* 3. The order's lines, with quantity, reason and condition */}
      <div className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-outline">
            Line Item Details &amp; Condition
          </span>
          {lines.length > 0 ? (
            <span className="shrink-0 text-[11px] text-outline">
              Showing {String(shown.length)} of {String(scoped.length)}{" "}
              {withheld > 0 ? "confirmed lines" : "lines"}
            </span>
          ) : null}
        </div>

        {linesError !== null ? (
          <p className="rounded-xl border border-error/40 bg-error-container/30 p-3 text-xs text-error">
            {linesError.message}
          </p>
        ) : null}

        {linesError === null && lines.length === 0 ? (
          <p className="rounded-xl border border-dashed border-outline-variant/50 bg-surface-container-low/40 p-4 text-center text-xs text-on-surface-variant">
            {linesPending
              ? "Reading the confirmed order's lines."
              : "No line of the confirmed order has been read yet."}
          </p>
        ) : null}

        {shown.map((line) => {
          const current = entry(line.lineReference);
          const selected = current !== null;
          const available = selectableQuantity(line);
          const quantityRefused =
            current !== null && current.quantity !== "" && positiveQuantity(current, line) === null;

          return (
            <div
              key={line.lineReference}
              className={[
                "flex flex-col gap-3 rounded-xl border bg-surface-container-lowest p-4 shadow-sm",
                selected ? "border-2 border-primary/50" : "border-outline-variant/30",
              ].join(" ")}
            >
              {/* Line & product */}
              <div className="flex items-start justify-between gap-3">
                <button
                  type="button"
                  aria-pressed={selected}
                  onClick={() => {
                    write(
                      line.lineReference,
                      selected
                        ? null
                        : {
                            // No invented quantity: an empty box is the honest
                            // opening state, and the submit stays refused until
                            // it is filled.
                            quantity: "",
                            // The one place the prefill is applied, and it is a
                            // *default* rather than a commitment. Writing it
                            // into the draft at the moment the line is ticked
                            // is what makes the associate's later choice win:
                            // once the key exists the draft is the answer, so a
                            // re-statement in chat on a later turn cannot reach
                            // back and overwrite it. A line the case already
                            // holds is not touched at all -- that is a recorded
                            // selection, and the pane opens on the record.
                            reason: preselectedReason ?? "",
                            condition: "",
                          },
                    );
                  }}
                  className="flex min-w-0 items-start gap-2.5 text-left"
                >
                  <span className="mt-0.5 text-primary">
                    {selected ? <CheckSquare size={19} /> : <Square size={19} />}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-bold text-on-surface">
                      {line.sku ?? line.productReference ?? line.lineReference}
                    </span>
                    <span className="block font-mono text-xs font-semibold text-primary">
                      Line {line.lineReference}
                    </span>
                    {line.description === null ? null : (
                      <span className="block truncate text-[11px] text-outline">
                        {line.description}
                      </span>
                    )}
                  </span>
                </button>
                <div className="shrink-0 text-right">
                  {/* No price is drawn from the line: the case carries no order
                      total, so a figure here would have no basis. */}
                  <span className="text-xs font-semibold text-outline">
                    {String(available)} returnable
                  </span>
                  <span className="block text-[10px] text-outline">
                    {line.orderedQuantity === null
                      ? "Ordered quantity not on the source line"
                      : `${String(line.orderedQuantity)} ordered`}
                  </span>
                </div>
              </div>

              {selected ? (
                <>
                  <div className="grid grid-cols-2 gap-3 border-t border-outline-variant/20 pt-3">
                    <label className="flex flex-col gap-1">
                      <span className="text-[11px] font-semibold text-outline">
                        Return quantity
                      </span>
                      <input
                        type="number"
                        min={1}
                        max={available}
                        step={1}
                        inputMode="numeric"
                        value={current.quantity}
                        onChange={(event) => {
                          write(line.lineReference, { ...current, quantity: event.target.value });
                        }}
                        className="rounded-lg border border-outline-control bg-surface px-2 py-1 text-xs font-bold text-on-surface"
                      />
                      {quantityRefused ? (
                        <span className="text-[10px] text-error">
                          Between 1 and {String(available)}.
                        </span>
                      ) : null}
                    </label>

                    <label className="flex flex-col gap-1">
                      <span className="text-[11px] font-semibold text-outline">
                        Item condition
                      </span>
                      <select
                        value={current.condition}
                        disabled={conditions.length === 0}
                        onChange={(event) => {
                          write(line.lineReference, { ...current, condition: event.target.value });
                        }}
                        className="rounded-lg border border-outline-control bg-surface px-2 py-1 text-xs font-medium text-on-surface disabled:opacity-40"
                      >
                        <option value="">Not stated</option>
                        {conditions.map((condition) => (
                          <option key={condition} value={condition}>
                            {condition}
                          </option>
                        ))}
                      </select>
                      {conditions.length === 0 ? (
                        <span className="text-[10px] text-outline">
                          This release publishes no condition catalogue.
                        </span>
                      ) : null}
                    </label>
                  </div>

                  <label className="flex flex-col gap-1.5 rounded-lg bg-surface-container-low p-2.5">
                    <span className="text-xs font-semibold text-outline">
                      Return reason{reasonRequired ? " (required)" : ""}
                    </span>
                    <select
                      value={current.reason}
                      disabled={reasons.length === 0}
                      onChange={(event) => {
                        write(line.lineReference, { ...current, reason: event.target.value });
                      }}
                      className="rounded-lg border border-outline-control bg-surface px-2 py-1 text-xs font-medium text-on-surface disabled:opacity-40"
                    >
                      <option value="">Not stated</option>
                      {reasons.map((reason) => (
                        <option key={reason} value={reason}>
                          {reason}
                        </option>
                      ))}
                    </select>
                    {reasons.length === 0 ? (
                      <span className="text-[10px] text-outline">
                        This release publishes no reason catalogue, so no reason can be recorded
                        against the line.
                      </span>
                    ) : null}
                    {reasons.length > 0 &&
                    heardReason !== null &&
                    preselectedReason === null ? (
                      // Heard, and not published. Reported rather than mapped:
                      // the associate can see what the model took from the
                      // conversation and pick the term that matches, and no
                      // policy-bearing value arrives on the case that nobody
                      // stated.
                      <span className="text-[10px] text-outline">
                        The conversation stated &quot;{heardReason}&quot;, which is not one of the
                        terms this release publishes. Nothing has been chosen for you.
                      </span>
                    ) : null}
                    {preselectedReason !== null && current.reason === preselectedReason ? (
                      <span className="text-[10px] text-outline">
                        Pre-selected from the conversation. Change it if it is not what the
                        associate meant.
                      </span>
                    ) : null}
                  </label>
                </>
              ) : null}
            </div>
          );
        })}

        {hidden > 0 ? (
          <button
            type="button"
            onClick={() => {
              setVisible((count) => count + LINE_PAGE);
            }}
            className="rounded-lg border border-outline-control bg-surface px-3 py-1.5 text-xs font-semibold text-on-surface"
          >
            Show {String(Math.min(LINE_PAGE, hidden))} more · {String(hidden)} not shown
          </button>
        ) : null}

        {withheld > 0 ? (
          <button
            type="button"
            onClick={() => {
              setShowEveryLine(true);
            }}
            className="rounded-lg border border-dashed border-outline-control bg-surface px-3 py-1.5 text-xs font-semibold text-on-surface"
          >
            Show all {String(lines.length)} lines on this order · {String(withheld)} not named on
            the confirmation
          </button>
        ) : null}
      </div>

      {/* 4. Evidence: a declared placeholder, not a control */}
      <div className="flex flex-col gap-2 rounded-xl border border-dashed border-outline-variant/50 bg-surface-container-low/40 p-3.5">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-outline">
          <Camera size={13} className="text-secondary" />
          <span>Evidence &amp; photo attachments</span>
        </span>

        <div className="flex items-center gap-2">
          <button
            type="button"
            // Placeholder, and drawn as one. There is no case-scoped evidence
            // upload anywhere in the platform -- no route, no store, no
            // artifact type for it -- and the control that used to sit here
            // reported a photo attached and attached nothing.
            disabled
            aria-describedby="evidence-placeholder-note"
            className="flex items-center gap-1.5 rounded-lg border border-dashed border-outline-variant/60 bg-surface-container px-3 py-1.5 text-xs font-semibold text-outline disabled:opacity-60"
          >
            <UploadCloud size={14} />
            <span>Attach photo — placeholder</span>
          </button>
        </div>
        <p id="evidence-placeholder-note" className="text-[11px] leading-relaxed text-outline">
          Placeholder only. No upload endpoint exists, so nothing can be attached from here yet;
          pictures the branch requests must travel with the Support work item.
        </p>
      </div>

      {/* 5. Credit statement & the write */}
      <div className="flex flex-col gap-2 rounded-xl border border-primary/20 bg-secondary-container/30 p-3.5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <span className="block text-[11px] font-semibold text-outline">
              Estimated credit / refund
            </span>
            <span className="text-xs font-bold text-outline">No line prices on the case</span>
          </div>

          <button
            type="button"
            disabled={!submittable}
            onClick={submit}
            className="flex items-center gap-1.5 rounded-lg bg-primary-container px-4 py-2 text-xs font-bold text-white shadow-sm transition hover:bg-primary-container/90 disabled:opacity-40"
          >
            <ShieldCheck size={15} />
            <span>{submitting ? "Recording selection…" : "Submit return details"}</span>
            <ChevronRight size={14} />
          </button>
        </div>

        {submitError !== null ? (
          <p className="rounded-lg border border-error/40 bg-error-container/30 p-2 text-xs text-error">
            {submitError.message}
          </p>
        ) : null}

        <p className="text-[11px] leading-relaxed text-outline">
          {/* The button does not run the evaluator, and must not claim to. The
              case workflow evaluates policy and opens the Support work item on
              its own; this records what the return is for. */}
          Recording the selection holds the quantity against this order. Policy evaluation and the
          Support hand-off are run by the case workflow, and this screen follows the case.
        </p>
      </div>
    </div>
  );
}
