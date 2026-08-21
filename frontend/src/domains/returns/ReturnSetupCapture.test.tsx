/**
 * Setting a return up: what was extracted, and what the associate then names.
 *
 * The operator's source document says Fergusonhome needs six things to open a
 * return -- model number / SKU, quantity and colour or finish, the reason
 * (pictures may be requested), the branch number, and the branch associate's
 * contact details -- with the last two **optional**. These tests are about the
 * two screens that collect them, and they assert behaviour rather than markup:
 * that only captured fields appear, that the pickers are the release's, that an
 * unpublished term is refused, that branch absence blocks nothing, and that the
 * evidence control cannot be mistaken for a working upload.
 *
 * Two failures are pinned here that a rendering test would miss:
 *
 * - The facts panel briefly admitted `GRAPH_FACT` statements, so it showed the
 *   agent narrating its own reasoning -- "Line 1 has no product, quantity or
 *   amount recorded against it" -- under a heading promising extracted facts.
 * - `ORDER_CONFIRMATION` drew the candidate table, which left the selection
 *   pane reachable only after a selection existed, and the only screen that can
 *   create one is the selection pane.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as CasesModuleNamespace from "../../api/cases";
import type * as OrderAgentModuleNamespace from "../../api/orderAgent";
import type * as OrderLinesModuleNamespace from "../../api/orderLines";
import type { CaseFactProjection } from "../../api/cases";
import type { CapturedFact } from "../../api/orderAgent";
import type { RuntimeConfig } from "../../api/runtimeConfig";
import { APIError } from "../../api/client";
import { RuntimeConfigContext } from "../../hooks/useRuntimeConfig";
import { ReturnCopilotPage } from "./ReturnCopilotPage";
import { ItemSelectionMode } from "./modes/ItemSelectionMode";
import { CandidateOrderMode } from "./modes/CandidateOrderMode";
import { SUPPRESSED_FACTS, extractedReturnFields } from "./extractedFields";
import {
  caseProjection,
  confirmedOrder,
  customer,
  orderLine,
  selectedItem,
} from "./fixtures/modeFixtures";

/** The shipped release's catalogue, trimmed. Never a list this file invents. */
const PUBLISHED_REASONS = ["SHIPPING_DAMAGE", "ORDERED_IN_ERROR", "MANUFACTURING_DEFECT"];
const PUBLISHED_CONDITIONS = ["NEW_IN_ORIGINAL_PACKAGING", "USED"];

/**
 * `clarification_policy.fields` by descending priority, as `runtime-config`
 * serves it -- the shipped release's own ranking, not an order chosen here.
 *
 * The head and the tail are the operator's complaint: the return reason ranks
 * *last* of eighteen, and the panel used to draw it fourth from the top because
 * a TypeScript array said so. `test_the_fact_ranking_is_the_clarification_policy_by_descending_priority`
 * pins this sequence against the loaded configuration on the serving side, so
 * a release that re-ranks the policy fails there rather than drifting past this
 * copy unnoticed.
 */
const PUBLISHED_FACT_ORDER = [
  "order_number",
  "customer_id",
  "tracking_number",
  "invoice_number",
  "customer_po_number",
  "email",
  "phone",
  "customer_name",
  "company_name",
  "zip_code",
  "product_sku",
  "product_description",
  "approximate_purchase_date",
  "shipping_address",
  "product_colour",
  "purchase_channel_hint",
  "product_presence",
  "return_reason",
];

const mocks = vi.hoisted(() => ({
  can: vi.fn(),
  readCase: vi.fn(),
  listCases: vi.fn(),
  sendTurn: vi.fn(),
  listConversations: vi.fn(),
  readTranscript: vi.fn(),
  readOrderLines: vi.fn(),
  replaceSelection: vi.fn(),
  historyByOrder: vi.fn(),
  historyByCustomer: vi.fn(),
}));

vi.mock("../../api/orderAgent", async (importOriginal) => ({
  ...(await importOriginal<typeof OrderAgentModuleNamespace>()),
  orderAgentApi: {
    sendTurn: mocks.sendTurn,
    listConversations: mocks.listConversations,
    readTranscript: mocks.readTranscript,
  },
}));

vi.mock("../../api/cases", async (importOriginal) => ({
  ...(await importOriginal<typeof CasesModuleNamespace>()),
  casesApi: { readProjection: mocks.readCase, list: mocks.listCases },
}));

vi.mock("../../api/orderLines", async (importOriginal) => ({
  ...(await importOriginal<typeof OrderLinesModuleNamespace>()),
  orderLinesApi: { read: mocks.readOrderLines, replaceSelection: mocks.replaceSelection },
}));

vi.mock("../../api/returnHistory", () => ({
  returnHistoryApi: { byOrder: mocks.historyByOrder, byCustomer: mocks.historyByCustomer },
}));

vi.mock("../../hooks/capabilityContext", () => ({
  useCapabilities: () => ({ can: mocks.can, principal: { subject: "tester" } }),
}));

function runtimeConfig(): RuntimeConfig {
  return {
    releaseId: "release-under-test",
    environment: "test",
    apiBasePath: "/api",
    features: { orderDiscoveryCopilot: true },
    capabilities: { availableSourceTypes: [], availableModelProviders: [] },
    agents: { orderDiscovery: "order-discovery-agent" },
    selectionVocabulary: { reasons: PUBLISHED_REASONS, conditions: PUBLISHED_CONDITIONS },
    factCatalogue: { orderedFields: PUBLISHED_FACT_ORDER },
  };
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <RuntimeConfigContext.Provider value={runtimeConfig()}>
        {children}
      </RuntimeConfigContext.Provider>
    </QueryClientProvider>
  );
}

function captured(overrides: Partial<CapturedFact> & { name: string }): CapturedFact {
  return { status: "USABLE", acquisition: "STATED", ...overrides };
}

/** One entry of the case's own fact log, as the projection serves it. */
function caseFact(
  overrides: Partial<CaseFactProjection> & { factName: string },
): CaseFactProjection {
  return {
    factId: `fact-${overrides.factName}`,
    value: null,
    channel: "CHANNEL_A",
    acquisitionMethod: "STATED",
    agentId: "return-copilot",
    sourceSystem: null,
    observedAt: "2026-08-21T10:00:00Z",
    recordedAt: "2026-08-21T10:00:00Z",
    supersedesFactId: null,
    ...overrides,
  };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/returns");
  mocks.can.mockReturnValue(true);
  mocks.readCase.mockReset();
  mocks.listCases.mockReset().mockResolvedValue([]);
  mocks.sendTurn.mockReset();
  mocks.listConversations.mockReset().mockResolvedValue([]);
  mocks.readTranscript.mockReset();
  mocks.readOrderLines.mockReset();
  mocks.replaceSelection.mockReset();
  mocks.historyByOrder
    .mockReset()
    .mockResolvedValue({ orderReference: null, accountId: null, customerId: null, cases: [] });
  mocks.historyByCustomer
    .mockReset()
    .mockResolvedValue({ orderReference: null, accountId: null, customerId: null, cases: [] });
});

describe("the extracted fields are the model's, not the agent's prose", () => {
  it("names only fields something actually captured", () => {
    const fields = extractedReturnFields({
      captured: [
        captured({ name: "product_sku", value: "R7010108781", label: "SKU or item number" }),
        captured({ name: "return_reason", value: "damaged" }),
      ],
      projection: null,
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields.map((field) => field.label)).toEqual(["Product sku", "Return reason"]);
    // No placeholder row for colour, quantity, order or branch, even though the
    // ranking names all four. A configured field is a field the release may ask
    // about; a row is a field somebody answered. A row per unfilled field would
    // turn the panel into a form and make "not captured" look like "captured as
    // blank".
    expect(fields).toHaveLength(2);
    expect(fields.some((field) => field.value.trim() === "")).toBe(false);
  });

  it("carries nothing at all before the conversation has established anything", () => {
    expect(
      extractedReturnFields({
        captured: [],
        projection: null,
        factOrder: PUBLISHED_FACT_ORDER,
      }),
    ).toEqual([]);
  });

  it("never exposes the internal customer number", () => {
    // The same value `CandidateOrderMode.SUPPRESSED_COLUMNS` withholds from the
    // table next door, by operator instruction.
    expect(SUPPRESSED_FACTS.has("customer_id")).toBe(true);
    // The served ranking names it -- `clarification_policy` ranks it second of
    // eighteen -- which is exactly why the suppression has to survive the move
    // to a configured order. It is a decision about the customer id, not about
    // which fields the panel admits.
    expect(PUBLISHED_FACT_ORDER).toContain("customer_id");

    const fields = extractedReturnFields({
      captured: [
        captured({ name: "customer_id", value: "471565" }),
        captured({ name: "product_colour", value: "brushed nickel" }),
      ],
      projection: null,
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields.map((field) => field.value)).toEqual(["brushed nickel"]);
  });

  it("shows a re-stated fact once, at its latest value", () => {
    // The turn carries the merged set, so the panel cannot accumulate a row per
    // mention however long the conversation runs.
    const fields = extractedReturnFields({
      captured: [captured({ name: "return_reason", value: "wrong item" })],
      projection: null,
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields).toHaveLength(1);
    expect(fields[0].value).toBe("wrong item");
  });

  it("marks a value the conversation still owes a question about", () => {
    const [field] = extractedReturnFields({
      captured: [captured({ name: "return_reason", value: "damaged", status: "CONFLICTING" })],
      projection: null,
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(field.unsettledBecause).toBe("CONFLICTING");
    expect(field.provenance).toBe("STATED");
  });

  it("separates the order the associate read out from the order the case confirmed", () => {
    const spoken = extractedReturnFields({
      captured: [captured({ name: "order_number", value: "SO-A9" })],
      projection: null,
      factOrder: PUBLISHED_FACT_ORDER,
    });
    expect(spoken[0]).toMatchObject({ value: "SO-A9", provenance: "STATED" });

    const recorded = extractedReturnFields({
      captured: [captured({ name: "order_number", value: "SO-A9" })],
      projection: caseProjection({ confirmedOrder: confirmedOrder() }),
      factOrder: PUBLISHED_FACT_ORDER,
    });
    // The confirmation is bound to a candidate the agent searched, so it wins
    // and it is recorded rather than merely said.
    expect(recorded[0]).toMatchObject({ value: "SO-A1", provenance: "RECORDED" });
  });

  it("takes quantity from the selection the platform recorded, never from speech", () => {
    // There is no `quantity` field in `clarification_policy.fields`, so a
    // spoken quantity has nowhere to be captured; the number becomes real when
    // the selection write records it against a line.
    const fields = extractedReturnFields({
      captured: [],
      projection: caseProjection({ selectedItems: [selectedItem({ quantity: 3 })] }),
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields.filter((field) => field.label === "Quantity")).toMatchObject([
      { label: "Quantity", value: "3", provenance: "RECORDED" },
    ]);
  });

  it("shows the reason and the condition the selection recorded", () => {
    // The panel's empty state promises a reason, and until this it could only
    // show one the associate had said out loud. `POST /selected-items` records
    // both against the return *item*, so a return set up in the item pane --
    // which is how one is set up -- displayed neither.
    const fields = extractedReturnFields({
      captured: [],
      projection: caseProjection({
        selectedItems: [selectedItem({ reason: "ORDERED_IN_ERROR", condition: "USED" })],
      }),
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Return reason",
          value: "ORDERED_IN_ERROR",
          provenance: "RECORDED",
        }),
        expect.objectContaining({
          label: "Product condition",
          value: "USED",
          provenance: "RECORDED",
        }),
      ]),
    );
  });

  it("prefers the recorded reason over the spoken one, and shows one row", () => {
    // They should agree. Where they cannot, the copy everything downstream
    // reads wins -- and two rows for one question would be worse than either.
    const fields = extractedReturnFields({
      captured: [captured({ name: "return_reason", value: "said something else" })],
      projection: caseProjection({
        selectedItems: [selectedItem({ reason: "ORDERED_IN_ERROR" })],
      }),
      factOrder: PUBLISHED_FACT_ORDER,
    });

    const reasons = fields.filter((field) => field.label === "Return reason");
    expect(reasons).toMatchObject([
      { value: "ORDERED_IN_ERROR", provenance: "RECORDED" },
    ]);
  });

  it("shows the branch associate the case recorded", () => {
    // Three case facts `POST /selected-items` writes, and no
    // `clarification_policy` field names any of them -- so the panel never
    // asked, and a contact collected for a carrier reached a database and not a
    // screen.
    const fields = extractedReturnFields({
      captured: [],
      projection: caseProjection({
        facts: [
          caseFact({ factName: "branch_associate_name", value: "Dana Reyes" }),
          caseFact({ factName: "branch_associate_phone", value: "555-0142" }),
        ],
      }),
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Branch associate",
          value: "Dana Reyes",
          provenance: "RECORDED",
        }),
        expect.objectContaining({
          label: "Branch associate phone",
          value: "555-0142",
          provenance: "RECORDED",
        }),
      ]),
    );
    // Absent is no row, never a blank one.
    expect(fields.some((field) => field.label === "Branch associate email")).toBe(false);
  });

  it("bounds the quantity rows and counts what it left out", () => {
    const many = Array.from({ length: 7 }, (_unused, index) =>
      selectedItem({
        returnItemId: `sel-${String(index)}`,
        orderLineReference: `L${String(index)}`,
        quantity: 1,
      }),
    );

    const rows = extractedReturnFields({
      captured: [],
      projection: caseProjection({ selectedItems: many }),
      factOrder: PUBLISHED_FACT_ORDER,
    });

    const quantities = rows.filter((row) => row.label.startsWith("Quantity"));
    expect(quantities).toHaveLength(5);
    // Last, and still attached to the lines it counts. The quantity rows move
    // through the ordering as one block for this reason: a counter sorted apart
    // from the rows it describes would report a bound over nothing in
    // particular.
    expect(quantities[4].value).toBe("3 further lines not shown");
    // The same bound, applied to every per-line value the selection carries.
    expect(
      rows.filter((row) => row.label.startsWith("Product condition")),
    ).toHaveLength(5);
  });

  it("leaves the branch out entirely when the case has none", () => {
    // Optional by operator instruction, and absent is never a guessed default:
    // an invented hub routes freight.
    const without = extractedReturnFields({
      captured: [],
      projection: caseProjection({ customer: customer({ branchReference: null }) }),
      factOrder: PUBLISHED_FACT_ORDER,
    });
    expect(without).toEqual([]);

    const withBranch = extractedReturnFields({
      captured: [],
      projection: caseProjection({ customer: customer() }),
      factOrder: PUBLISHED_FACT_ORDER,
    });
    expect(withBranch).toMatchObject([{ label: "Branch", value: "BR-01" }]);
  });
});

describe("the panel's order is the release's, not the client's", () => {
  /** Every conversational row the ordering tests below place. */
  function spokenFacts() {
    return [
      captured({ name: "return_reason", value: "damaged" }),
      captured({ name: "product_sku", value: "R7010108781" }),
      captured({ name: "customer_name", value: "Melgon" }),
      captured({ name: "order_number", value: "SO-A9" }),
    ];
  }

  it("lists the facts in the priority the release ranked them", () => {
    // The operator's complaint, in one assertion: the return reason came fourth
    // from the top while `clarification_policy` ranks it last of eighteen. The
    // panel had it there because a hand-written array in this repository put it
    // there, so no release could move it.
    const fields = extractedReturnFields({
      captured: spokenFacts(),
      projection: null,
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields.map((field) => field.value)).toEqual([
      "SO-A9", // order_number, ranked first
      "Melgon", // customer_name
      "R7010108781", // product_sku
      "damaged", // return_reason, ranked last
    ]);
  });

  it("reorders when the release reorders, with no client change", () => {
    // The whole point of serving the ranking. Same facts, a configuration that
    // wants the reason first, and the panel obeys.
    const reversed = [...PUBLISHED_FACT_ORDER].reverse();

    const fields = extractedReturnFields({
      captured: spokenFacts(),
      projection: null,
      factOrder: reversed,
    });

    expect(fields.map((field) => field.value)).toEqual([
      "damaged",
      "R7010108781",
      "Melgon",
      "SO-A9",
    ]);
  });

  it("places the confirmed order by rank rather than by hand", () => {
    // Confirmation changes what the row says and where the value came from. It
    // does not promote the row: `order_number` sits wherever the release ranks
    // it, which under a policy that ranks it last is last.
    const fields = extractedReturnFields({
      captured: [captured({ name: "product_sku", value: "R7010108781" })],
      projection: caseProjection({ confirmedOrder: confirmedOrder() }),
      factOrder: ["product_sku", "order_number"],
    });

    expect(fields).toMatchObject([
      { value: "R7010108781", provenance: "STATED" },
      { value: "SO-A1", provenance: "RECORDED" },
    ]);
  });

  it("still shows a captured fact the served ranking does not mention", () => {
    // The failure mode this replaces is the allowlist that dropped
    // `customer_name` and left an associate who opened with "find orders for
    // Melgon" watching an empty panel. A ranking is not an allowlist: a fact it
    // has never heard of goes to the end of the panel, never off it.
    const fields = extractedReturnFields({
      captured: [
        captured({ name: "return_reason", value: "damaged" }),
        captured({ name: "customer_name", value: "Melgon" }),
      ],
      projection: null,
      factOrder: ["return_reason"],
    });

    expect(fields.map((field) => field.value)).toEqual(["damaged", "Melgon"]);
  });

  it("falls back to alphabetical when the deployment states no order", () => {
    // A backend older than `factCatalogue`, or a process with no configuration
    // loaded. Alphabetical is computed from the labels on screen, so it asserts
    // nothing about which fact matters; a built-in sequence here would be the
    // deleted array one tier down, and would outrank a configured order for
    // every field it happened to name.
    const fields = extractedReturnFields({
      captured: spokenFacts(),
      projection: caseProjection({
        customer: customer(),
        selectedItems: [selectedItem({ quantity: 2 })],
      }),
      factOrder: [],
    });

    expect(fields.map((field) => field.label)).toEqual([
      "Branch",
      "Customer name",
      "Order number",
      "Product condition",
      "Product sku",
      "Quantity",
      "Return reason",
    ]);
  });

  it("puts the rows configuration cannot rank after the ones it can", () => {
    // Quantity and condition come from the selection write and branch from the
    // principal; no `clarification_policy` field names any of them, because
    // none is something an associate is ever asked for in discovery. They take
    // the documented alphabetical tail rather than a slot somebody picked.
    //
    // `return_reason` *is* ranked, and stays ranked whichever source supplied
    // it -- which is why it leads here even though the selection recorded it.
    const fields = extractedReturnFields({
      captured: [captured({ name: "return_reason", value: "damaged" })],
      projection: caseProjection({
        customer: customer(),
        selectedItems: [selectedItem({ quantity: 2 })],
      }),
      factOrder: PUBLISHED_FACT_ORDER,
    });

    expect(fields.map((field) => field.label)).toEqual([
      "Return reason",
      "Branch",
      "Product condition",
      "Quantity",
    ]);
  });
});

describe("the item-selection pane collects the return details", () => {
  it("offers the released catalogue and no list of its own", () => {
    render(
      <ItemSelectionMode
        orderReference="SO-A1"
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        conditions={PUBLISHED_CONDITIONS}
        items={[selectedItem()]}
      />,
    );

    const reason = screen.getByLabelText(/Return reason/);
    const offered = Array.from(reason.querySelectorAll("option")).map((option) => option.value);
    expect(offered).toEqual(["", ...PUBLISHED_REASONS]);
  });

  it("offers nothing when the release publishes no catalogue", () => {
    // Empty means "no catalogue is published", which the writer reads as
    // "refuse nothing". Substituting a list here would be the hardcoded
    // catalogue that was removed from this pane.
    render(<ItemSelectionMode lines={[orderLine()]} items={[selectedItem()]} />);

    expect(screen.getByLabelText(/Return reason/)).toBeDisabled();
    expect(screen.getByText(/publishes no reason catalogue/)).toBeInTheDocument();
  });

  it("submits quantity, reason and condition for the lines the associate chose", () => {
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        orderReference="SO-A1"
        lines={[orderLine(), orderLine({ lineReference: "L2", sku: "PART-B" })]}
        reasons={PUBLISHED_REASONS}
        conditions={PUBLISHED_CONDITIONS}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    fireEvent.click(screen.getByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "SHIPPING_DAMAGE" },
    });
    fireEvent.change(screen.getByLabelText(/Item condition/), {
      target: { value: "USED" },
    });
    fireEvent.click(screen.getByText("Submit return details"));

    expect(onSubmitSelection).toHaveBeenCalledWith(
      [
        {
          orderLineReference: "L1",
          quantity: 2,
          reason: "SHIPPING_DAMAGE",
          condition: "USED",
        },
      ],
      // No contact: the associate never touched those fields, which says
      // nothing about the branch associate rather than clearing them.
      null,
    );
  });

  it("invents no quantity for a line the associate has only ticked", () => {
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    fireEvent.click(screen.getByText("PART-A"));
    expect(screen.getByLabelText(/Return quantity/)).toHaveValue(null);
    fireEvent.click(screen.getByText("Submit return details"));
    expect(onSubmitSelection).not.toHaveBeenCalled();
  });

  it("refuses more than the line has left rather than sending it", () => {
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine({ returnableQuantity: 1 })]}
        reasons={PUBLISHED_REASONS}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    fireEvent.click(screen.getByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "SHIPPING_DAMAGE" },
    });

    expect(screen.getByText(/Between 1 and 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Submit return details"));
    expect(onSubmitSelection).not.toHaveBeenCalled();
  });

  it("does not block the submission on a branch", () => {
    // Branch number and branch associate details are optional by operator
    // instruction. A return with neither still goes.
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        branchReference={null}
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    fireEvent.click(screen.getByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "ORDERED_IN_ERROR" },
    });
    fireEvent.click(screen.getByText("Submit return details"));

    expect(onSubmitSelection).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Not recorded (optional)")).toBeInTheDocument();
  });

  it("cannot be mistaken for a working evidence upload", () => {
    render(<ItemSelectionMode lines={[orderLine()]} />);

    const attach = screen.getByText(/Attach photo/).closest("button");
    expect(attach).toBeDisabled();
    // Named a placeholder on the control itself, not only in a note beside it.
    expect(attach).toHaveTextContent(/placeholder/i);
    expect(screen.getByText(/No upload endpoint exists/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Attach Defect Photo$/ })).toBeNull();
  });

  it("draws no price, because the case carries none", () => {
    render(
      <ItemSelectionMode
        lines={[orderLine({ unitPrice: "129.95" })]}
        items={[selectedItem()]}
      />,
    );

    expect(screen.getByText("No line prices on the case")).toBeInTheDocument();
    expect(screen.queryByText(/129\.95/)).toBeNull();
  });

  it("does not block the submission on a branch associate", () => {
    // Fergusonhome's list marks the associate's name, email and phone optional,
    // exactly as it marks the branch number. A return with none still goes, and
    // nothing on screen fills one in.
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText(/Email address/)).toHaveValue("");
    expect(screen.getByLabelText("Phone number")).toHaveValue("");

    fireEvent.click(screen.getByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "ORDERED_IN_ERROR" },
    });
    fireEvent.click(screen.getByText("Submit return details"));

    expect(onSubmitSelection).toHaveBeenCalledWith(
      [{ orderLineReference: "L1", quantity: 1, reason: "ORDERED_IN_ERROR" }],
      null,
    );
  });

  it("refuses an email that is not an email, and lets an absent one through", () => {
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    fireEvent.click(screen.getByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "ORDERED_IN_ERROR" },
    });
    // A phone number typed into the email box: the failure that produces a
    // label request nobody can answer.
    fireEvent.change(screen.getByLabelText(/Email address/), {
      target: { value: "704-555-0134" },
    });

    expect(screen.getByText(/Not an address a label could be sent to/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Submit return details"));
    expect(onSubmitSelection).not.toHaveBeenCalled();

    // Emptied, not corrected. Shape is validated; existence is not.
    fireEvent.change(screen.getByLabelText(/Email address/), { target: { value: "" } });
    fireEvent.click(screen.getByText("Submit return details"));
    expect(onSubmitSelection).toHaveBeenCalledTimes(1);
  });

  it("sends the branch associate beside the lines, never inside one", () => {
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine(), orderLine({ lineReference: "L2", sku: "PART-B" })]}
        reasons={PUBLISHED_REASONS}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "D. Reyes" } });
    fireEvent.change(screen.getByLabelText(/Email address/), {
      target: { value: "d.reyes@branch.example" },
    });
    fireEvent.change(screen.getByLabelText("Phone number"), {
      target: { value: "704-555-0134" },
    });

    for (const sku of ["PART-A", "PART-B"]) {
      fireEvent.click(screen.getByText(sku));
    }
    for (const quantity of screen.getAllByLabelText(/Return quantity/)) {
      fireEvent.change(quantity, { target: { value: "1" } });
    }
    for (const reason of screen.getAllByLabelText(/Return reason/)) {
      fireEvent.change(reason, { target: { value: "ORDERED_IN_ERROR" } });
    }
    fireEvent.click(screen.getByText("Submit return details"));

    const [items, contact] = onSubmitSelection.mock.calls[0] as [
      readonly Record<string, unknown>[],
      Record<string, unknown> | null,
    ];
    // One associate raises one return. Two lines, one contact -- and no line
    // carries a copy of it, which is the shape that makes two associates on one
    // return unsayable.
    expect(items).toHaveLength(2);
    expect(items.every((item) => !("name" in item) && !("email" in item))).toBe(true);
    expect(contact).toEqual({
      name: "D. Reyes",
      email: "d.reyes@branch.example",
      phone: "704-555-0134",
    });
  });

  it("opens on the associate the case already recorded", () => {
    // Read off the case's fact log, not held in this component. An untouched
    // fieldset then makes no claim: re-submitting must not re-assert values the
    // pane merely read.
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        contact={{ name: "D. Reyes", email: null, phone: "704-555-0134" }}
        onSubmitSelection={onSubmitSelection}
      />,
    );

    expect(screen.getByLabelText("Name")).toHaveValue("D. Reyes");
    // Absent, and drawn absent. No default, and no placeholder standing in for
    // an address a carrier would try to use.
    expect(screen.getByLabelText(/Email address/)).toHaveValue("");

    fireEvent.click(screen.getByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "ORDERED_IN_ERROR" },
    });
    fireEvent.click(screen.getByText("Submit return details"));

    expect(onSubmitSelection.mock.calls[0][1]).toBeNull();
  });

  it("bounds the line list and says how many it is holding back", () => {
    const lines = Array.from({ length: 11 }, (_unused, index) =>
      orderLine({ lineReference: `L${String(index)}`, sku: `PART-${String(index)}` }),
    );
    render(<ItemSelectionMode lines={lines} />);

    expect(screen.getByText("Showing 8 of 11 lines")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Show 3 more · 3 not shown/));
    expect(screen.getByText("Showing 11 of 11 lines")).toBeInTheDocument();
  });
});

describe("the reason opens on what the associate already said", () => {
  function selectFirstLine(capturedReason: string | null) {
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        capturedReason={capturedReason}
        onSubmitSelection={onSubmitSelection}
      />,
    );
    fireEvent.click(screen.getByText("PART-A"));
    return { onSubmitSelection, reason: screen.getByLabelText(/Return reason/) };
  }

  it("pre-selects a captured reason the release publishes", () => {
    // The operator's complaint: the associate opened with the reason and was
    // asked for it again. `SHIPPING_DAMAGE` is one of the release's own terms,
    // so it is the associate's own word and nothing has been translated.
    const { reason } = selectFirstLine("SHIPPING_DAMAGE");

    expect(reason).toHaveValue("SHIPPING_DAMAGE");
    expect(screen.getByText(/Pre-selected from the conversation/)).toBeInTheDocument();
  });

  it("chooses nothing for a reason the release does not publish, and says what it heard", () => {
    // The defect class this programme exists to remove, in one test. "the pump
    // arrived cracked" is not a published term; mapping it onto
    // `SHIPPING_DAMAGE` would put a value the evaluator routes a delivery claim
    // on to the case, which nobody stated.
    const { reason, onSubmitSelection } = selectFirstLine("the pump arrived cracked");

    expect(reason).toHaveValue("");
    expect(screen.getByText(/the pump arrived cracked/)).toBeInTheDocument();
    expect(screen.getByText(/not one of the terms this release publishes/)).toBeInTheDocument();
    expect(screen.queryByText(/Pre-selected from the conversation/)).toBeNull();

    // And it is not merely unselected on screen: a published reason is required
    // while a catalogue exists, so the submit stays refused until the associate
    // makes the choice deliberately.
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.click(screen.getByText("Submit return details"));
    expect(onSubmitSelection).not.toHaveBeenCalled();
  });

  it("chooses nothing when the release publishes no catalogue at all", () => {
    const onSubmitSelection = vi.fn();
    render(
      <ItemSelectionMode
        lines={[orderLine()]}
        capturedReason="SHIPPING_DAMAGE"
        onSubmitSelection={onSubmitSelection}
      />,
    );
    fireEvent.click(screen.getByText("PART-A"));

    // Nothing to pre-select against, so nothing is pre-selected -- and no note
    // about an unpublished term either, which would be an odd thing to say to a
    // deployment that publishes none.
    expect(screen.getByLabelText(/Return reason/)).toHaveValue("");
    expect(screen.getByText(/publishes no reason catalogue/)).toBeInTheDocument();
    expect(screen.queryByText(/not one of the terms this release publishes/)).toBeNull();
  });

  it("lets the associate's own choice beat the pre-selection", () => {
    const { reason, onSubmitSelection } = selectFirstLine("SHIPPING_DAMAGE");
    fireEvent.change(reason, { target: { value: "ORDERED_IN_ERROR" } });
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });

    // A pre-filled value is a default, never a commitment: the note stops
    // claiming the conversation chose it the moment the associate does.
    expect(screen.queryByText(/Pre-selected from the conversation/)).toBeNull();
    fireEvent.click(screen.getByText("Submit return details"));
    expect(onSubmitSelection).toHaveBeenCalledWith(
      [{ orderLineReference: "L1", quantity: 1, reason: "ORDERED_IN_ERROR" }],
      null,
    );
  });

  it("does not let a later turn overwrite a choice the associate has made", () => {
    // The turn that re-states the reason arrives as a new `capturedReason`
    // while the associate is mid-selection. The draft already holds their
    // answer, so a re-statement changes nothing -- which is the whole reason
    // the prefill is written once, at the moment the line is ticked, rather
    // than derived on every render.
    const onSubmitSelection = vi.fn();
    const { rerender } = render(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        capturedReason="SHIPPING_DAMAGE"
        onSubmitSelection={onSubmitSelection}
      />,
    );

    fireEvent.click(screen.getByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "ORDERED_IN_ERROR" },
    });
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });

    rerender(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        capturedReason="MANUFACTURING_DEFECT"
        onSubmitSelection={onSubmitSelection}
      />,
    );

    expect(screen.getByLabelText(/Return reason/)).toHaveValue("ORDERED_IN_ERROR");
    fireEvent.click(screen.getByText("Submit return details"));
    expect(onSubmitSelection).toHaveBeenCalledWith(
      [{ orderLineReference: "L1", quantity: 1, reason: "ORDERED_IN_ERROR" }],
      null,
    );
  });

  it("leaves a line the case already recorded exactly as the case recorded it", () => {
    // A recorded selection is a record, not a default. The pane opens on it
    // whatever the conversation has since said.
    render(
      <ItemSelectionMode
        lines={[orderLine()]}
        reasons={PUBLISHED_REASONS}
        items={[selectedItem({ reason: "ORDERED_IN_ERROR" })]}
        capturedReason="SHIPPING_DAMAGE"
      />,
    );

    expect(screen.getByLabelText(/Return reason/)).toHaveValue("ORDERED_IN_ERROR");
  });
});

describe("the candidate table is a page, and says so", () => {
  it("reports the search's own total rather than the rows it was handed", () => {
    render(
      <CandidateOrderMode
        candidates={[{ sales_order_number: "SO-A1" }]}
        totalFound={47}
        returnHistory={null}
        returnHistoryPending={false}
        returnHistoryError={null}
      />,
    );

    expect(screen.getByText("Showing 1 of 47 matched")).toBeInTheDocument();
    expect(screen.getByText(/46 further matches were found/)).toBeInTheDocument();
  });

  it("caps the rows it draws and counts the remainder", () => {
    const rows = Array.from({ length: 14 }, (_unused, index) => ({
      sales_order_number: `SO-${String(index)}`,
    }));
    render(
      <CandidateOrderMode
        candidates={rows}
        totalFound={14}
        returnHistory={null}
        returnHistoryPending={false}
        returnHistoryError={null}
      />,
    );

    expect(screen.getAllByText("Select")).toHaveLength(10);
    fireEvent.click(screen.getByText(/Show 4 more · 4 not shown/));
    expect(screen.getAllByText("Select")).toHaveLength(14);
  });
});

describe("the copilot walks confirmation into capture", () => {
  function openOnConfirmedCase() {
    window.history.replaceState(null, "", "/returns?caseId=case-1");
    mocks.readCase.mockResolvedValue(
      caseProjection({
        caseId: "case-1",
        stage: "ORDER_CONFIRMATION",
        confirmedOrder: confirmedOrder(),
        customer: customer({ branchReference: null }),
      }),
    );
    mocks.readOrderLines.mockResolvedValue({
      caseId: "case-1",
      orderReference: "SO-A1",
      lines: [orderLine()],
    });
  }

  it("opens the capture pane on a confirmed order with nothing selected", async () => {
    openOnConfirmedCase();
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    // The deadlock this replaced: the stage drew the candidate table, and the
    // only screen that can create a selection is the one it was hiding.
    expect(await screen.findByText("Return Line Item Scope")).toBeInTheDocument();
    expect(await screen.findByText("PART-A")).toBeInTheDocument();
    expect(screen.queryByText("Select Matching Order")).toBeNull();
  });

  it("records the selection against the case", async () => {
    openOnConfirmedCase();
    mocks.replaceSelection.mockResolvedValue({
      caseId: "case-1",
      revision: 5,
      changed: true,
      items: [],
      lines: [],
    });
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.click(await screen.findByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "MANUFACTURING_DEFECT" },
    });
    fireEvent.click(screen.getByText("Submit return details"));

    await waitFor(() => {
      expect(mocks.replaceSelection).toHaveBeenCalledWith(
        "case-1",
        [{ orderLineReference: "L1", quantity: 1, reason: "MANUFACTURING_DEFECT" }],
        null,
      );
    });
  });

  it("shows the refusal when the release does not publish the term", async () => {
    // The writer's own words. A console that swallowed this would leave the
    // associate looking at a control that did nothing.
    openOnConfirmedCase();
    mocks.replaceSelection.mockRejectedValue(
      new APIError(
        "The active return configuration does not publish reason(s) SHIPPING_DAMAGE.",
        422,
      ),
    );
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.click(await screen.findByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "SHIPPING_DAMAGE" },
    });
    fireEvent.click(screen.getByText("Submit return details"));

    expect(await screen.findByText(/does not publish reason\(s\) SHIPPING_DAMAGE/)).toBeInTheDocument();
  });

  it("never posts the words 'evaluate policy' at a discovery agent", async () => {
    // The hook this replaced submitted that sentence into the conversation, so
    // a button labelled "Evaluate Policy & Submit" asked an order-discovery
    // agent to run an evaluator it has no access to. `ReturnCaseWorkflow` runs
    // the policy gate and opens the Support work item; this screen records the
    // selection and follows the case.
    openOnConfirmedCase();
    mocks.replaceSelection.mockResolvedValue({
      caseId: "case-1",
      revision: 5,
      changed: true,
      items: [],
      lines: [],
    });
    render(<ReturnCopilotPage />, { wrapper: Wrapper });

    fireEvent.click(await screen.findByText("PART-A"));
    fireEvent.change(screen.getByLabelText(/Return quantity/), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText(/Return reason/), {
      target: { value: "ORDERED_IN_ERROR" },
    });
    fireEvent.click(screen.getByText("Submit return details"));

    await waitFor(() => {
      expect(mocks.replaceSelection).toHaveBeenCalledTimes(1);
    });
    expect(mocks.sendTurn).not.toHaveBeenCalled();
  });
});
