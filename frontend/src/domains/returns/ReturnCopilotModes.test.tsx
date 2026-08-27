/**
 * The eight panes, and the one thing that chooses between them.
 *
 * Mode used to be derived from a `ReturnSessionView` that is `null` for every
 * Copilot return, from `candidates.length`, and from `turn.response.status` --
 * a model answer read as a workflow transition. It comes off `CaseProjection.
 * stage` now, and these pin both halves: every stage draws its pane, and the
 * panes say only what the projection says.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CopilotStage } from "../../api/cases";
import { deriveCopilotMode, type CopilotLifecycleMode } from "./types";
import { ReturnCopilotShell } from "./panes/ReturnCopilotShell";
import { ProgressTruthPane } from "./panes/ProgressTruthPane";
import { DiscoveryMode } from "./modes/DiscoveryMode";
import { CandidateOrderMode } from "./modes/CandidateOrderMode";
import { ItemSelectionMode } from "./modes/ItemSelectionMode";
import { ReturnEvaluationMode } from "./modes/ReturnEvaluationMode";
import { AuthorizedRmaMode } from "./modes/AuthorizedRmaMode";
import { CarrierTransitMode } from "./modes/CarrierTransitMode";
import { WarehouseReceivingMode } from "./modes/WarehouseReceivingMode";
import { ReturnSettlementMode } from "./modes/ReturnSettlementMode";

import {
  SAMPLE_CANDIDATE_ROWS,
  approvedItem,
  artifact,
  caseProjection,
  confirmedOrder,
  customer,
  orderLine,
  policyEvaluation,
  returnRecord,
  rmaWithLabelAndNoTracking,
  selectedItem,
  settlement,
  shipment,
  support,
  warehouse,
} from "./fixtures/modeFixtures";

describe("Return Copilot 8-Mode Lifecycle Contract", () => {
  it("enforces the frozen post-sidebar 40fr / 24fr / 36fr desktop grid on ReturnCopilotShell", () => {
    const { container } = render(
      <ReturnCopilotShell
        conversationPane={<div data-testid="left">Chat</div>}
        progressTruthPane={<div data-testid="center">Progress</div>}
        businessObjectPane={<div data-testid="right">Business Object</div>}
      />,
    );

    // The grid is no longer the root: below `lg` the shell puts a tablist above
    // it, because three panes sharing one viewport height give each about 231
    // pixels on a phone. The invariant is that the grid exists and still
    // declares the frozen tracks, not that it is the outermost element.
    const root = container.querySelector<HTMLElement>(".grid");
    expect(root).toBeTruthy();
    expect(root?.className).toContain("grid");
    expect(root?.className).toContain("lg:grid-cols-[minmax(0,40fr)_minmax(0,24fr)_minmax(0,36fr)]");
    expect(screen.getByTestId("left")).toBeInTheDocument();
    expect(screen.getByTestId("center")).toBeInTheDocument();
    expect(screen.getByTestId("right")).toBeInTheDocument();
  });

  describe("the backend stage chooses the pane", () => {
    /**
     * Every stage, and the pane it draws. Written out rather than derived from
     * the mapping table, because a test that recomputed the map would agree
     * with any map at all.
     */
    const EXPECTED: readonly (readonly [CopilotStage, CopilotLifecycleMode])[] = [
      ["DISCOVERY", "DISCOVERY"],
      // A case exists only once an order has been confirmed, so this stage is
      // "confirmed, nothing selected yet" -- the moment the return's lines,
      // quantities and reason have to be captured. It used to draw the
      // candidate table, which left the selection pane reachable only after a
      // selection existed and made the only screen that can create one
      // unreachable.
      ["ORDER_CONFIRMATION", "ITEM_SELECTION"],
      ["ITEM_SELECTION", "ITEM_SELECTION"],
      ["RETURN_FACTS", "ITEM_SELECTION"],
      ["POLICY_EVALUATION", "RETURN_EVALUATION"],
      ["APPROVAL_REQUIRED", "RETURN_EVALUATION"],
      ["AWAITING_SUPPORT", "RETURN_EVALUATION"],
      ["AUTHORIZED_RMA", "AUTHORIZED_RMA"],
      ["CARRIER_TRANSIT", "CARRIER_TRANSIT"],
      ["WAREHOUSE_RECEIVING", "WAREHOUSE_RECEIVING"],
      ["RETURN_SETTLEMENT", "RETURN_SETTLEMENT"],
      ["COMPLETED", "RETURN_SETTLEMENT"],
    ];

    for (const [stage, mode] of EXPECTED) {
      it(`draws ${mode} for stage ${stage}`, () => {
        expect(deriveCopilotMode({ stage, candidates: [] })).toBe(mode);
      });
    }

    it("does not let a candidate list move a case off its stage", () => {
      // The defect this replaced: two search rows and a `sales_order_number`
      // were enough to walk an authorized RMA back to a candidate table.
      for (const [stage, mode] of EXPECTED) {
        if (stage === "DISCOVERY") continue;
        expect(deriveCopilotMode({ stage, candidates: SAMPLE_CANDIDATE_ROWS })).toBe(mode);
      }
    });

    it("shows the candidates before there is a case to be authoritative", () => {
      // The one place a candidate list decides anything: no case exists, so
      // there is no stage to obey and the search is all there is.
      expect(deriveCopilotMode({ stage: null, candidates: [] })).toBe("DISCOVERY");
      expect(deriveCopilotMode({ stage: null, candidates: SAMPLE_CANDIDATE_ROWS })).toBe(
        "CANDIDATE_ORDER",
      );
      expect(deriveCopilotMode({ stage: "DISCOVERY", candidates: SAMPLE_CANDIDATE_ROWS })).toBe(
        "CANDIDATE_ORDER",
      );
    });
  });

  describe("8 Right-Pane Business Object Modes Rendering & Visual Constraints", () => {
    it("Mode 1: renders DiscoveryMode with clear guidelines and search anchors", () => {
      render(<DiscoveryMode />);
      expect(screen.getByText("Discovery Guidance")).toBeInTheDocument();
      expect(screen.getByText("Helpful Search Anchors")).toBeInTheDocument();
    });

    it("Mode 2: renders CandidateOrderMode with candidates and history table", () => {
      render(
        <CandidateOrderMode
          candidates={SAMPLE_CANDIDATE_ROWS}
          returnHistory={null}
          returnHistoryPending={false}
          returnHistoryError={null}
        />,
      );
      expect(screen.getByText("SO-A1")).toBeInTheDocument();
      expect(screen.getByText("SO-A2")).toBeInTheDocument();
    });

    it("Mode 3: renders ItemSelectionMode from the confirmed order's lines", () => {
      render(
        <ItemSelectionMode
          orderReference="SO-A1"
          branchReference="BR-01"
          lines={[orderLine(), orderLine({ lineReference: "L2", sku: "PART-B" })]}
          items={[selectedItem()]}
        />,
      );
      expect(screen.getByText("PART-A")).toBeInTheDocument();
      expect(screen.getByText("PART-B")).toBeInTheDocument();
      // The case's own selection is what the controls open on.
      expect(screen.getByDisplayValue("1")).toBeInTheDocument();
      // No unit price exists on the contract, so no figure is drawn from one.
      expect(screen.getByText("No line prices on the case")).toBeInTheDocument();
    });

    it("Mode 3: names no order and no branch it has not been given", () => {
      render(<ItemSelectionMode items={[]} />);
      expect(screen.getByText(/Sales Order Pending/)).toBeInTheDocument();
      expect(screen.getByText(/No line of the confirmed order has been read/)).toBeInTheDocument();
      // Branch is optional by operator instruction; absent is stated, never
      // filled in with a hub that would route freight somewhere.
      expect(screen.getByText("Not recorded (optional)")).toBeInTheDocument();
    });

    it("Mode 4: renders ReturnEvaluationMode from the policy projection", () => {
      render(
        <ReturnEvaluationMode
          evaluation={policyEvaluation({
            conditions: ["RESTOCKING_FEE_APPLIES"],
            appliedRules: ["WITHIN_30_DAYS"],
          })}
        />,
      );
      expect(screen.getByText("Return Eligible · Policy Approved")).toBeInTheDocument();
      expect(screen.getByText("release-under-test · v3")).toBeInTheDocument();
      // The evaluator issues a rate with a source, never an amount, and the
      // case carries no line prices to apply one to.
      expect(screen.getByText("Applies · rate set by seller configuration")).toBeInTheDocument();
      expect(screen.getByText("Not computed by the policy engine")).toBeInTheDocument();
      expect(screen.getByText("WITHIN_30_DAYS")).toBeInTheDocument();
    });

    it("Mode 4: carries no decision for a claim Support verifies", () => {
      render(
        <ReturnEvaluationMode
          evaluation={policyEvaluation({
            route: "WARRANTY",
            originalDecision: null,
            effectiveDecision: null,
            policyId: null,
            policyVersion: null,
          })}
          awaiting={["WARRANTY_VERIFICATION"]}
          support={support({ queue: "WARRANTY_SUPPORT", status: "IN_PROGRESS" })}
        />,
      );
      expect(screen.getByText("Verification With Support")).toBeInTheDocument();
      expect(screen.getByText("WARRANTY_SUPPORT · IN_PROGRESS")).toBeInTheDocument();
      expect(screen.queryByText("Return Eligible · Policy Approved")).toBeNull();
    });

    it("Mode 4: says nothing at all before an evaluation has run", () => {
      render(<ReturnEvaluationMode evaluation={null} />);
      expect(screen.getByText("Policy Evaluation Pending")).toBeInTheDocument();
      expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
    });

    it("Mode 4: a suspended gate is skipped, not pending, and not approved", () => {
      // Two different absences wearing one shape. A deployment can turn the gate
      // off through `policy_evaluation.enabled`, and the evaluator then produces
      // no route and no decision -- exactly what a case that has not been
      // evaluated *yet* looks like. Reading the second as the first tells an
      // associate a verdict is coming when none is.
      render(
        <ReturnEvaluationMode
          evaluation={null}
          policyEvaluationState="SKIPPED_BY_CONFIGURATION"
          policySkipReason="Eligibility gate suspended by the operator."
        />,
      );

      expect(screen.getByText("Policy Evaluation Skipped")).toBeInTheDocument();
      expect(screen.getByText("SKIPPED")).toBeInTheDocument();
      expect(screen.getByText("No policy was applied to this return")).toBeInTheDocument();
      // The operator's own words reach the associate, as they reach Support.
      expect(screen.getByText("Eligibility gate suspended by the operator.")).toBeInTheDocument();
      // Neither of the two things it must never say.
      expect(screen.queryByText("Policy Evaluation Pending")).toBeNull();
      expect(screen.queryByText("Return Eligible · Policy Approved")).toBeNull();
      expect(screen.getAllByText("Not evaluated").length).toBeGreaterThan(0);
    });

    it("Mode 4: a real evaluation is never overridden by the state fact", () => {
      // Defensive: the fact exists to tell one absence from another, and a case
      // carrying a stale state fact beside a real decision must show the
      // decision.
      render(
        <ReturnEvaluationMode
          evaluation={policyEvaluation()}
          policyEvaluationState="SKIPPED_BY_CONFIGURATION"
          policySkipReason="stale"
        />,
      );

      expect(screen.getByText("Return Eligible · Policy Approved")).toBeInTheDocument();
      expect(screen.queryByText("Policy Evaluation Skipped")).toBeNull();
      expect(screen.queryByText("stale")).toBeNull();
    });

    it("Mode 4: a skipped gate with no recorded reason invents none", () => {
      render(
        <ReturnEvaluationMode evaluation={null} policyEvaluationState="SKIPPED_BY_CONFIGURATION" />,
      );

      expect(screen.getByText("Policy Evaluation Skipped")).toBeInTheDocument();
      expect(screen.getByText("No policy was applied to this return")).toBeInTheDocument();
      expect(screen.queryByText(/Suspended by configuration/i)).toBeNull();
    });

    it("Mode 4: offers no control that would issue the RMA", () => {
      // The button that stood here submitted the words "authorize rma" into the
      // discovery conversation. By the time this pane is drawn the workflow has
      // already taken the case through the policy gate onto the Support queue,
      // and the RMA is written when Support records its outcome -- so an
      // approved evaluation names who does it rather than offering a control
      // that asks nobody. Same rule that deleted the "evaluate policy" sibling.
      render(<ReturnEvaluationMode evaluation={policyEvaluation()} />);

      expect(screen.getByText("Return Eligible · Policy Approved")).toBeInTheDocument();
      expect(screen.getByText("Support issues the RMA and its shipping label")).toBeInTheDocument();
      expect(screen.queryAllByRole("button")).toEqual([]);
    });

    it("Mode 5: renders AuthorizedRmaMode from the return record", () => {
      render(
        <AuthorizedRmaMode
          returnRecords={[
            returnRecord({
              returnLocation: "DOCK-3",
              returnMethod: "PREPAID_PARCEL",
              shipments: [
                shipment({
                  carrier: "CARRIER-A",
                  serviceLevel: "GROUND",
                  trackingNumber: "1Z-TEST",
                  shipmentStatus: "IN_TRANSIT",
                }),
              ],
              artifacts: [artifact({ shipmentId: "SHP-1" })],
            }),
          ]}
        />,
      );
      expect(screen.getAllByText("RMA-OPS01-CD4364").length).toBeGreaterThan(0);
      expect(screen.getByText("DOCK-3")).toBeInTheDocument();
      expect(screen.getByText("CARRIER-A · GROUND")).toBeInTheDocument();
      expect(screen.getByText("1Z-TEST")).toBeInTheDocument();
      expect(screen.getByText("Print Shipping Label & BOL")).toBeInTheDocument();
    });

    it("Mode 6: renders CarrierTransitMode from the shipment's own status", () => {
      render(
        <CarrierTransitMode
          shipments={[
            shipment({ carrier: "CARRIER-A", trackingNumber: "1Z-TEST", shipmentStatus: "IN_TRANSIT" }),
          ]}
        />,
      );
      expect(screen.getByText("Fulfillment & Transit")).toBeInTheDocument();
      expect(screen.getByText("Awaiting handoff")).toBeInTheDocument();
      expect(screen.getByText(/In transit/)).toBeInTheDocument();
      expect(screen.getByText("Received")).toBeInTheDocument();
      expect(screen.getByText("1Z-TEST")).toBeInTheDocument();
    });

    it("Mode 6: says there is no package rather than drawing one", () => {
      render(<CarrierTransitMode shipments={[]} />);
      expect(screen.getByText(/No package has been tendered/)).toBeInTheDocument();
    });

    it("Mode 7: renders WarehouseReceivingMode, and a missing bay is not an error", () => {
      render(
        <WarehouseReceivingMode
          warehouse={warehouse({ facilityId: "WH-1969", bayReason: "PRE_ARRIVAL_NOT_ALLOWED" })}
        />,
      );
      expect(screen.getByText("Warehouse Receiving & Bay")).toBeInTheDocument();
      expect(screen.getByText("WH-1969")).toBeInTheDocument();
      // The reason placement gave, rendered as the explanation it is.
      expect(screen.getByText("Pre arrival not allowed")).toBeInTheDocument();
      expect(screen.getByText("Confirm Dock Physical Receipt")).toBeDisabled();
    });

    it("Mode 7: reports the receipt fields as pending, having no producer", () => {
      render(<WarehouseReceivingMode warehouse={null} />);
      expect(screen.getAllByText("Pending").length).toBeGreaterThanOrEqual(4);
    });

    it("Mode 8: renders ReturnSettlementMode without inventing a credit", () => {
      render(<ReturnSettlementMode settlement={settlement()} caseStatus="COMPLETED_EXTERNAL_SETTLEMENT" />);
      expect(screen.getByText("Return Completed · Settlement Not Integrated")).toBeInTheDocument();
      expect(screen.getByText("Settlement Ledger")).toBeInTheDocument();
      // The credit figure is not wanted on this screen for now (operator
      // instruction, 2026-08-15), so the line reports completion instead. A
      // settled amount still renders beside it when one exists -- see the next
      // test -- and the approximate note below the ledger covers it.
      expect(screen.getByText("Completed")).toBeInTheDocument();
      expect(screen.getByText("NOT_INTEGRATED")).toBeInTheDocument();
      // Four ledger lines and a credit memo, none of which any producer fills.
      expect(screen.getAllByText("Unavailable").length).toBe(6);
      expect(screen.getByText("Start New Return")).toBeInTheDocument();
    });

    it("Mode 8: renders a settled amount only when one exists, currency included", () => {
      render(
        <ReturnSettlementMode
          settlement={settlement({
            status: "SETTLED",
            creditMemoReference: "MEMO-1",
            settledAmount: { amount: "12", currency: "USD" },
          })}
        />,
      );
      expect(screen.getByText("12 USD")).toBeInTheDocument();
      expect(screen.getByText("MEMO-1")).toBeInTheDocument();
    });
  });
});

describe("an RMA with a label and no tracking", () => {
  /** The live shape the audit found: `RMA-OPS01-CD4364`, label present, tracking null. */
  it("renders the label as an active artifact and the tracking as pending", () => {
    render(<AuthorizedRmaMode returnRecords={[rmaWithLabelAndNoTracking()]} />);

    expect(screen.getAllByText("RMA-OPS01-CD4364").length).toBeGreaterThan(0);
    // No package exists, so there is no tracking -- and saying so is the whole
    // point. The pane used to print `TRK-98421049281` here.
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
    expect(screen.queryByText(/^TRK-/)).toBeNull();
    // The one label it does have, and it is the active one.
    expect(screen.getByText("LBL-OPS01.pdf")).toBeInTheDocument();
  });

  it("keeps polling-visible truth in the progress pane too", () => {
    render(
      <ProgressTruthPane
        candidates={[]}
        fields={[]}
        projection={caseProjection({
          stage: "AUTHORIZED_RMA",
          awaiting: ["TRACKING"],
          returnRecords: [rmaWithLabelAndNoTracking()],
        })}
      />,
    );

    expect(screen.getByText("RMA-OPS01-CD4364")).toBeInTheDocument();
    expect(screen.getByText("Tracking")).toBeInTheDocument();
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
    expect(screen.getByText("LBL-OPS01.pdf")).toBeInTheDocument();
  });

  it("names the customer on the rail rather than showing their internal id", () => {
    // This read `customerReference ?? displayName`, so a case that knew the
    // customer was Melgon Heating drew `CUST-9012` -- an internal id, on the
    // rail an associate reads while talking to that customer. The agent is
    // forbidden from showing a customer id in as many words.
    render(
      <ProgressTruthPane
        candidates={[]}
        fields={[]}
        projection={caseProjection({
          stage: "ITEM_SELECTION",
          customer: customer(),
          confirmedOrder: confirmedOrder(),
        })}
      />,
    );

    expect(screen.getByText("Melgon Heating")).toBeInTheDocument();
    expect(screen.queryByText("CUST-9012")).not.toBeInTheDocument();
  });

  it("falls back to the reference for a customer whose name is not resolved yet", () => {
    // Not a regression of the above: an id is something true to show, and a
    // blank chip on a case that has resolved a customer says less.
    render(
      <ProgressTruthPane
        candidates={[]}
        fields={[]}
        projection={caseProjection({
          stage: "ITEM_SELECTION",
          customer: customer({ displayName: null }),
          confirmedOrder: confirmedOrder(),
        })}
      />,
    );

    expect(screen.getByText("CUST-9012")).toBeInTheDocument();
  });
});

describe("a return in two packages", () => {
  function twoPackages() {
    return returnRecord({
      returnMethod: "PREPAID_PARCEL",
      returnLocation: "DOCK-3",
      shipments: [
        shipment({ shipmentId: "SHP-1", trackingNumber: "1Z-ONE", carrier: "CARRIER-A" }),
        shipment({ shipmentId: "SHP-2", trackingNumber: "1Z-TWO", carrier: "CARRIER-A" }),
      ],
      artifacts: [
        artifact({ artifactId: "art-1", shipmentId: "SHP-1", fileName: "package-one.pdf" }),
        artifact({ artifactId: "art-2", shipmentId: "SHP-2", fileName: "package-two.pdf" }),
      ],
    });
  }

  it("attributes each label and each tracking number to its own package", () => {
    render(<AuthorizedRmaMode returnRecords={[twoPackages()]} />);

    const first = screen.getByText("Tracking Number · SHP-1").parentElement;
    const second = screen.getByText("Tracking Number · SHP-2").parentElement;
    expect(first).toHaveTextContent("1Z-ONE");
    expect(first).not.toHaveTextContent("1Z-TWO");
    expect(second).toHaveTextContent("1Z-TWO");

    const firstLabel = screen.getByText("Label · SHP-1").parentElement;
    expect(firstLabel).toHaveTextContent("package-one.pdf");
    expect(firstLabel).not.toHaveTextContent("package-two.pdf");
  });

  it("resolves the label action to the active artifact, never to artifacts[0]", () => {
    // A replaced label stays on the record for audit. Printing it would send
    // the parcel to the address it was replaced for.
    const onPrintLabel = vi.fn();
    render(
      <AuthorizedRmaMode
        onPrintLabel={onPrintLabel}
        returnRecords={[
          returnRecord({
            artifacts: [
              artifact({
                artifactId: "art-old",
                fileName: "superseded.pdf",
                active: false,
                supersededBy: "art-new",
              }),
              artifact({ artifactId: "art-new", fileName: "current.pdf" }),
            ],
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByText("Print Shipping Label & BOL"));
    expect(onPrintLabel).toHaveBeenCalledTimes(1);
    expect(onPrintLabel.mock.calls[0][0]).toMatchObject({ artifactId: "art-new" });
  });

  it("cannot print a label the platform has not issued", () => {
    render(
      <AuthorizedRmaMode onPrintLabel={vi.fn()} returnRecords={[returnRecord()]} />,
    );
    expect(screen.getByText("Print Shipping Label & BOL").closest("button")).toBeDisabled();
    expect(screen.getByText(/No label document has been issued/)).toBeInTheDocument();
  });
});

describe("the progress pane reads the case and nothing else", () => {
  it("walks the milestones as the projection fills in", () => {
    render(
      <ProgressTruthPane
        candidates={[{ sales_order_number: "SO-A1" }]}
        fields={[]}
        projection={caseProjection({
          stage: "CARRIER_TRANSIT",
          customer: customer(),
          confirmedOrder: confirmedOrder(),
          returnRecords: [
            returnRecord({
              returnMethod: "PREPAID_PARCEL",
              approvedItems: [approvedItem({ orderLineReference: "L1" })],
              shipments: [
                shipment({ shipmentStatus: "IN_TRANSIT", trackingNumber: "1Z-ONE" }),
              ],
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText("SO-A1")).toBeInTheDocument();
    // The customer, by the name an associate would say out loud. This asserted
    // `CUST-9012` and so pinned the defect: the rail drew the internal id in
    // preference to the name sitting beside it.
    expect(screen.getByText("Melgon Heating")).toBeInTheDocument();
    expect(screen.getByText("IN_TRANSIT")).toBeInTheDocument();
    expect(screen.getByText("PREPAID_PARCEL")).toBeInTheDocument();
    expect(screen.getByText("Covers L1")).toBeInTheDocument();
    expect(screen.getByText("5/7 milestones")).toBeInTheDocument();
  });

  it("draws the extracted fields it is given, and says so when there are none", () => {
    const view = render(
      <ProgressTruthPane candidates={[]} fields={[]} projection={null} />,
    );
    expect(screen.getByText("No return details captured yet")).toBeInTheDocument();
    view.unmount();

    render(
      <ProgressTruthPane
        candidates={[]}
        fields={[
          {
            key: "reason",
            label: "Reason for return",
            value: "SHIPPING_DAMAGE",
            provenance: "STATED",
            unsettledBecause: null,
          },
          {
            key: "colour",
            label: "Colour / finish",
            value: "Brushed nickel",
            provenance: "STATED",
            unsettledBecause: "AMBIGUOUS",
          },
        ]}
        projection={caseProjection()}
      />,
    );

    expect(screen.getByText("Reason for return")).toBeInTheDocument();
    expect(screen.getByText("SHIPPING_DAMAGE")).toBeInTheDocument();
    // A value the conversation still owes a question about is not drawn as
    // settled -- the re-ask reason is on the row.
    expect(screen.getByText("AMBIGUOUS")).toBeInTheDocument();
  });

  it("does not treat a bay recommendation as a receipt", () => {
    // Placement runs before the goods exist. A recommendation lighting the
    // receiving milestone would report goods booked in that nobody has seen.
    render(
      <ProgressTruthPane
        candidates={[]}
        fields={[]}
        projection={caseProjection({
          stage: "AUTHORIZED_RMA",
          confirmedOrder: confirmedOrder(),
          warehouse: warehouse({ facilityId: "WH-1969", bayReason: "NO_ELIGIBLE_BAY" }),
        })}
      />,
    );

    // Three: the order was identified, it was selected, and the case exists.
    // The bay is not a fourth.
    expect(screen.getByText("3/7 milestones")).toBeInTheDocument();
    expect(screen.getByLabelText("Reached warehouse: not reached")).toBeInTheDocument();
    expect(screen.queryByText("NO_ELIGIBLE_BAY")).toBeNull();
  });
});

/**
 * Which milestones light, and on what.
 *
 * The live defect: an order found, confirmed and visible on screen, with
 * "Orders identified" and "Order selected" both unticked and the header reading
 * "Ready". These pin each step to the evidence that is allowed to light it --
 * the search the platform ran, and the case it raised -- and pin the two states
 * that must stay dark: an order nobody has confirmed, and a case with no
 * confirmed order on it.
 */
describe("what evidences each milestone", () => {
  it("lights the search and nothing after it while no case exists", () => {
    // A search is real progress and the only thing that has happened. Lighting
    // "Order selected" from a single candidate would be reading the screen
    // rather than the platform.
    render(
      <ProgressTruthPane
        candidates={[{ sales_order_number: "SO-A1" }]}
        fields={[]}
        projection={null}
      />,
    );

    expect(screen.getByLabelText("Orders identified: reached")).toBeInTheDocument();
    expect(screen.getByLabelText("Order selected: not reached")).toBeInTheDocument();
    expect(screen.getByLabelText("Case created: not reached")).toBeInTheDocument();
    expect(screen.getByText("1/7 milestones")).toBeInTheDocument();
  });

  it("lights the order, the selection and the case as soon as one is confirmed", () => {
    // No candidates at all: this is the shape of a resumed or reloaded return,
    // where the search happened in a conversation that has ended and the case
    // is the only record of it. Every one of these three is off the case.
    render(
      <ProgressTruthPane
        candidates={[]}
        fields={[]}
        projection={caseProjection({
          stage: "ORDER_CONFIRMATION",
          customer: customer(),
          confirmedOrder: confirmedOrder(),
        })}
      />,
    );

    expect(screen.getByLabelText("Orders identified: reached")).toBeInTheDocument();
    expect(screen.getByLabelText("Order selected: reached")).toBeInTheDocument();
    // The case is created by the confirmation itself. This step used to wait
    // for an RMA or a Support work item -- two stages further on -- so a case
    // being actively worked reported its own creation as pending.
    expect(screen.getByLabelText("Case created: reached")).toBeInTheDocument();
    // The step in progress is now the RMA, not the shipment. That is the point
    // of adding it: a confirmed case with no RMA was shown as working towards a
    // shipment, when what it is actually waiting for is Support to issue one.
    expect(screen.getByLabelText("RMA issued: in progress")).toBeInTheDocument();
    expect(screen.getByLabelText("Shipment in progress: not reached")).toBeInTheDocument();
    expect(screen.getByText("3/7 milestones")).toBeInTheDocument();
    expect(screen.getByText("SO-A1")).toBeInTheDocument();
  });

  it("does not tick a step underneath one that is lit", () => {
    // `done` was `index <= furthest`, so the furthest evidenced step back-filled
    // every step below it. A case carrying no confirmed order would have ticked
    // "Order selected" on the strength of existing.
    render(<ProgressTruthPane candidates={[]} fields={[]} projection={caseProjection()} />);

    expect(screen.getByLabelText("Case created: reached")).toBeInTheDocument();
    expect(screen.getByLabelText("Order selected: not reached")).toBeInTheDocument();
    expect(screen.getByLabelText("Orders identified: not reached")).toBeInTheDocument();
    expect(screen.getByText("1/7 milestones")).toBeInTheDocument();
  });

  it("says Ready only when the platform has recorded nothing", () => {
    render(<ProgressTruthPane candidates={[]} fields={[]} projection={null} />);

    expect(screen.getByText("Ready")).toBeInTheDocument();
    for (const milestone of [
      "Orders identified",
      "Order selected",
      "Case created",
      "Shipment in progress",
      "Reached warehouse",
      "Completed",
    ]) {
      expect(screen.getByLabelText(`${milestone}: not reached`)).toBeInTheDocument();
    }
  });
});
