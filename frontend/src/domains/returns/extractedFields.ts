/**
 * What this return has actually captured, and in the order the release ranked.
 *
 * **This is a structured field list, not a transcript.** The panel it feeds
 * spent a day admitting `GRAPH_FACT` statements, which put the agent narrating
 * its own reasoning -- "Line 1 has no product, quantity or amount recorded
 * against it" -- under a heading promising extracted fields. A statement is
 * prose the agent uttered; an extracted field is a named value the model pulled
 * out of the associate's sentence and the fact catalogue accepted. Reading the
 * first as the second is the same defect as a fabricated value, because both
 * put something on screen that the platform never established.
 *
 * **The order is configuration, not a list written here.** This module used to
 * return a hand-written array -- SKU, quantity, colour, reason, order, branch,
 * then everything else -- which put the return reason near the top of the panel
 * while `clarification_policy` ranks it last of eighteen, and meant no release
 * could correct the panel without a frontend build. The sequence now comes from
 * `runtimeConfig.factCatalogue.orderedFields`, which is
 * `clarification_policy.fields[].priority` descending: the ranking of the very
 * list that decides which fact names a turn may capture at all, so it is keyed
 * in exactly the namespace `captured_facts` is written in.
 *
 * Two other configured signals look like candidates and are not. `anchor_weights`
 * is per anchor *type* and scores candidate matches -- there is no anchor for a
 * return reason and there never will be. `identification_fields[].clarification_priority`
 * is keyed by the discovery catalogue's own `field_id` (`sku`, `postal_code`,
 * `free_text`), a namespace that names neither `return_reason` nor `product_sku`
 * nor `tracking_number`, so most of this panel would go unranked under it.
 *
 * **Where a field's value comes from** is a separate question from where it
 * sorts. Most rows are the captured fact of the same name, preferring the
 * case's recorded copy. Three are not:
 *
 * | Field        | Source                                                        |
 * | ------------ | ------------------------------------------------------------- |
 * | Order number | `confirmedOrder.orderReference`, else captured `order_number` |
 * | Quantity     | `selectedItems[].quantity` -- the selection write, not speech |
 * | Branch       | `customer.branchReference` -- the case's, from the principal  |
 *
 * Quantity is the one that is **not** conversational. No `quantity` field
 * exists in `clarification_policy.fields`, so the model has nowhere to put one
 * it heard; the number becomes real when the associate names it against a line
 * in the item-selection pane and `POST /selected-items` records it. Showing a
 * spoken quantity here would mean parsing it out of prose, which is the thing
 * this module exists to stop. It follows that configuration does not rank it
 * either, and it takes the documented tail rather than a pinned position.
 *
 * Branch is likewise recorded rather than extracted: `confirm_case` writes the
 * principal's branch onto the case, and only when the principal is scoped to
 * exactly one. It is optional by operator instruction and absent is a perfectly
 * ordinary state -- never a guessed default, because an invented branch routes
 * freight.
 *
 * **No row for a field nobody has stated.** A placeholder row per unfilled
 * field would turn the panel into a form and make "not captured" look like
 * "captured as blank". The configured ranking names every field the release can
 * ask for, and naming one is emphatically not capturing it.
 */

import type { CapturedFact } from "../../api/orderAgent";
import type { CaseFactProjection, CaseProjection } from "../../api/cases";

/**
 * Where a value came from, which decides how much it may be trusted.
 *
 * `STATED` is the associate's own word, carried by the model. `RECORDED` is
 * something the platform wrote against the case -- a confirmation bound to a
 * candidate the agent actually searched, a quantity committed by the selection
 * write, the branch the principal is scoped to. Rendering the two identically
 * would quietly promote hearsay to record, which is the error class this whole
 * programme exists to remove.
 */
export type ExtractedFieldProvenance = "STATED" | "RECORDED";

export type ExtractedField = {
  readonly key: string;
  readonly label: string;
  readonly value: string;
  readonly provenance: ExtractedFieldProvenance;
  /**
   * The `FactStatus` that means the conversation still owes the associate a
   * question about this value -- conflicting, invalid, ambiguous, stale, or
   * awaiting read-back. `null` for a settled value.
   */
  readonly unsettledBecause: string | null;
};

/**
 * Never rendered, by operator instruction (2026-08-15): the internal ERP
 * customer number is not for the screen.
 *
 * **This panel has no allowlist and must not grow one.** An allowlist is the
 * right shape for `selection_vocabulary`, where refusing a term the release has
 * not published is the correct answer; it is the wrong shape here, where the
 * panel's whole job is to report what the model extracted. The old one admitted
 * four fact names and so left an associate who opened with "find orders for
 * Melgon" watching an empty panel, because `customer_name` was captured and
 * filtered out.
 *
 * This set is the one thing that does withhold a value, and it is a decision
 * about the customer id rather than about the panel -- which is why
 * `CandidateOrderMode`'s `SUPPRESSED_COLUMNS` withholds the same value in the
 * pane next door. `clarification_policy` ranks `customer_id` second of
 * eighteen, so the ranking served to the client names it; suppression is
 * applied after the ranking is read, and neither one knows about the other.
 */
export const SUPPRESSED_FACTS: ReadonlySet<string> = new Set(["customer_id"]);

/** Everything other than `USABLE` is a value the agent may still have to re-ask. */
const SETTLED = "USABLE";

/**
 * The one configured fact whose value has a source outside the fact log: the
 * order the case confirmed. It still sorts by its configured rank like any
 * other.
 */
const ORDER_FACT = "order_number";

/**
 * A captured value as one line of text, or `null` when there is nothing to show.
 *
 * Arrays are joined because an identification field configured `multiple: true`
 * arrives as a list -- an associate who read out two SKUs stated two SKUs, and
 * showing the first would silently drop the second.
 */
function text(value: unknown): string | null {
  if (typeof value === "string") return value.trim() === "" ? null : value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (Array.isArray(value)) {
    const parts = value.flatMap((entry) => {
      const rendered = text(entry);
      return rendered === null ? [] : [rendered];
    });
    return parts.length === 0 ? null : parts.join(", ");
  }
  return null;
}

/**
 * A configured fact name as a person would read it: `customer_name` → "Customer
 * name".
 *
 * Derived rather than mapped, deliberately, and now for every conversational
 * row rather than only the ones the old array did not mention. The fact
 * catalogue is operator configuration and can publish a name this build has
 * never heard of, so a lookup table would silently drop the eighteenth field
 * the way the old allowlist dropped `customer_name` -- and a table that named
 * some fields and not others would leave the panel with two wording regimes for
 * one list. A derived label is occasionally clumsier and never absent.
 */
function factLabel(name: string): string {
  const words = name.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function factByName(
  facts: readonly CaseFactProjection[] | null,
  name: string,
): CaseFactProjection | null {
  return facts?.find((fact) => fact.factName === name) ?? null;
}

/**
 * One thing the panel places as a unit.
 *
 * A unit rather than a bare row because the quantity lines and the "N further
 * lines not shown" counter under them are one thing: the counter states what
 * the rows above it left out, so sorting rows individually would let it drift
 * away from the lines it counts.
 *
 * `factName` is the configured name the ranking is looked up by. `null` says no
 * configured fact names this row -- true of the quantity and branch rows, and
 * the reason they take the documented tail instead of a hand-picked slot.
 */
type FactBlock = {
  readonly factName: string | null;
  readonly label: string;
  readonly rows: readonly ExtractedField[];
};

function block(field: ExtractedField | null, factName: string | null): FactBlock | null {
  return field === null ? null : { factName, label: field.label, rows: [field] };
}

/**
 * One conversational field, preferring what the case recorded over what the
 * conversation is still holding.
 *
 * The case fact log is the durable copy -- `confirm_case` flushes the
 * conversation's captured facts into it the moment a case exists -- so once
 * there is a case it is the authority, and the conversation's copy fills the
 * several turns before there is one. They should agree; where they cannot, the
 * one everything downstream reads wins.
 */
function conversationalField(
  name: string,
  captured: readonly CapturedFact[],
  facts: readonly CaseFactProjection[] | null,
): ExtractedField | null {
  if (SUPPRESSED_FACTS.has(name)) return null;
  const label = factLabel(name);

  const recorded = factByName(facts, name);
  const recordedValue = recorded === null ? null : text(recorded.value);
  if (recordedValue !== null) {
    return {
      key: `fact-${name}`,
      label,
      value: recordedValue,
      // Still the associate's word. The case recording it makes it durable and
      // auditable, not evidenced -- nothing verified the colour of a pump
      // against a source system.
      provenance: "STATED",
      unsettledBecause: null,
    };
  }

  const heard = captured.find((fact) => fact.name === name) ?? null;
  if (heard === null) return null;
  const value = text(heard.value);
  if (value === null) return null;
  return {
    key: `fact-${name}`,
    label,
    value,
    provenance: "STATED",
    unsettledBecause: heard.status === SETTLED ? null : heard.status,
  };
}

/**
 * The order the return is being raised against.
 *
 * The confirmed reference wins and is `RECORDED`: confirmation is bound to a
 * candidate the agent actually searched, through `CandidateSet.validate_selection`,
 * and it is what raised the case. A spoken order number before that is the
 * associate reading a number off a screen, which is `STATED` and may be wrong.
 *
 * Either way the row is `order_number`'s, so it sorts at `order_number`'s
 * configured rank. Confirming an order changes what the row says and where the
 * value came from; it does not promote the row up the panel.
 */
function orderField(
  captured: readonly CapturedFact[],
  projection: CaseProjection | null,
): ExtractedField | null {
  const confirmed = text(projection?.confirmedOrder?.orderReference);
  if (confirmed !== null) {
    return {
      key: `fact-${ORDER_FACT}`,
      label: factLabel(ORDER_FACT),
      value: confirmed,
      provenance: "RECORDED",
      unsettledBecause: null,
    };
  }
  return conversationalField(ORDER_FACT, captured, null);
}

/**
 * How many quantity rows the panel will draw before it starts counting instead.
 *
 * The selection is the one part of this list whose length is decided by data --
 * the writer accepts up to two hundred lines -- and a panel that grew a row per
 * line would push every other captured field off the screen. Everything past
 * the bound is reported as a count, never silently dropped.
 */
const MAX_QUANTITY_ROWS = 4;

/**
 * What the case is holding against each line, once a selection exists.
 *
 * One row per line when there are several, because a return of two of line 1
 * and one of line 2 is not a return of three of anything, and a summed figure
 * would be a number the case does not carry.
 */
function quantityBlock(projection: CaseProjection | null): FactBlock | null {
  const items = projection?.selectedItems ?? [];
  const named = items.filter((item) => item.quantity !== null);
  const shown = named.slice(0, MAX_QUANTITY_ROWS);
  const hidden = named.length - shown.length;

  const rows: ExtractedField[] = shown.map((item) => ({
    key: `quantity-${item.returnItemId}`,
    label: named.length === 1 ? "Quantity" : `Quantity · line ${item.orderLineReference}`,
    value: String(item.quantity),
    provenance: "RECORDED" as const,
    unsettledBecause: null,
  }));

  if (hidden > 0) {
    rows.push({
      key: "quantity-remainder",
      label: "Quantity",
      // A count of what is not on screen, which is the difference between a
      // bound and quietly losing data. The lines themselves are all listed in
      // the item-selection pane.
      value: `${String(hidden)} further line${hidden === 1 ? "" : "s"} not shown`,
      provenance: "RECORDED",
      unsettledBecause: null,
    });
  }
  if (rows.length === 0) return null;
  // One block: the rows keep the order the bound gave them -- lines, then the
  // count of what was left off -- while the block as a whole is placed by the
  // same rule as everything else.
  return { factName: null, label: "Quantity", rows };
}

/**
 * The branch, when the case has one.
 *
 * Optional by operator instruction, and absent whenever the principal is scoped
 * to more than one branch -- `confirm_case` refuses to pick for them. There is
 * no row at all in that case, and deliberately no default.
 */
function branchBlock(projection: CaseProjection | null): FactBlock | null {
  const branch = text(projection?.customer?.branchReference);
  if (branch === null) return null;
  return block(
    {
      key: "branch",
      label: "Branch",
      value: branch,
      provenance: "RECORDED",
      unsettledBecause: null,
    },
    // No `clarification_policy` field names a branch, because the branch is
    // never something the associate is asked for -- it is read off the
    // principal. Unranked, and placed by the tail rule rather than by hand.
    null,
  );
}

/**
 * The blocks in the order the release ranked them.
 *
 * `factOrder` is `clarification_policy.fields[].priority` descending, served on
 * `GET /api/runtime-config` and read with `capturedFactOrder`. A block whose
 * fact name appears in it sorts at that position. Everything else follows, in
 * alphabetical order of its label.
 *
 * **The alphabetical tail is not a second, hidden ranking.** It is computed
 * from the labels in front of the reader, so it states no opinion about which
 * fact matters more than which; it is identical in every deployment and stable
 * across releases. A built-in list would be the array this module just deleted
 * moved one tier down -- it would encode one operator's preference in the
 * client, and worse, it could silently outrank the configured ranking for a
 * field it happened to mention. Alphabetical order cannot do that, because it
 * is only ever applied to fields the configured ranking did not mention: the
 * quantity and branch rows, which no configured fact names, and a captured fact
 * from a release newer than the served ranking, which appears at the end rather
 * than vanishing.
 *
 * With an empty `factOrder` -- no configuration loaded, or a backend older than
 * the field -- every block is unmentioned, so the whole panel is alphabetical.
 * That is a visibly different panel rather than a plausible-looking wrong one,
 * which is the honest failure for a client that has not been told the order.
 */
function inConfiguredOrder(
  blocks: readonly FactBlock[],
  factOrder: readonly string[],
): readonly ExtractedField[] {
  const rankOf = new Map<string, number>();
  factOrder.forEach((name, index) => {
    if (!rankOf.has(name)) rankOf.set(name, index);
  });
  // Strictly past every real rank, so a ranked block always precedes an
  // unranked one and the two groups can share one comparison.
  const unranked = factOrder.length;

  return blocks
    .map((entry, arrival) => ({
      entry,
      arrival,
      rank: entry.factName === null ? unranked : (rankOf.get(entry.factName) ?? unranked),
    }))
    .sort(
      (left, right) =>
        left.rank - right.rank ||
        left.entry.label.localeCompare(right.entry.label, "en") ||
        // Never reached by today's blocks, whose labels are distinct. Present so
        // the sort is total and the panel cannot reshuffle between renders.
        left.arrival - right.arrival,
    )
    .flatMap((placed) => placed.entry.rows);
}

/**
 * The return-setup fields this conversation and case have actually captured.
 *
 * Fields with no value are simply absent, and the order is the release's, not
 * this module's.
 */
export function extractedReturnFields(input: {
  readonly captured: readonly CapturedFact[];
  readonly projection: CaseProjection | null;
  /**
   * The configured ranking, from `capturedFactOrder(useRuntimeConfig())`.
   *
   * Required rather than defaulted: a caller who forgot it would silently get
   * the alphabetical fallback and no indication that the release had an opinion
   * it was ignoring.
   */
  readonly factOrder: readonly string[];
}): readonly ExtractedField[] {
  const { captured, projection, factOrder } = input;
  const facts = projection?.facts ?? null;

  // Every name the release ranks, plus every name this conversation captured.
  //
  // Neither alone is enough. The captured set misses a fact the case recorded
  // whose conversational copy this turn is not carrying; the configured set
  // misses a fact from a release newer than the served ranking, and dropping
  // that one is precisely the allowlist failure that hid `customer_name`.
  //
  // The union is still bounded and still cannot admit an unowned value.
  // `captured_facts` carries only names the fact catalogue accepted, merged
  // latest-per-name across the conversation, so it cannot grow with turns;
  // `factOrder` is that same catalogue. The case's own fact log is read only at
  // these names, which is what keeps the workflow's own facts -- the bay
  // recommendation and its confidence -- out of a panel about what the
  // associate said.
  const names = new Set<string>([...factOrder, ...captured.map((fact) => fact.name)]);

  const blocks = [
    ...[...names].map((name) =>
      name === ORDER_FACT
        ? block(orderField(captured, projection), ORDER_FACT)
        : block(conversationalField(name, captured, facts), name),
    ),
    quantityBlock(projection),
    branchBlock(projection),
  ].flatMap((entry) => (entry === null ? [] : [entry]));

  return inConfiguredOrder(blocks, factOrder);
}
