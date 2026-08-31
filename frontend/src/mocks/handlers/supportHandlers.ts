import { HttpResponse, delay, http } from "msw";

/**
 * Support ingress and V2's panel sections, for `npm run dev:mock` and tests.
 *
 * **Stateful, for the same reason the panel's set is.** The one thing an
 * operator does on this surface is watch a message arrive and see what the panel
 * then says about it, and a handler that answered identically forever would let
 * a developer look at a screenshot and nothing else. So a POST to the ingress
 * route mutates a module-level store, and the panel sections compose from it.
 *
 * The dedupe and the parked lifecycle are modelled rather than faked, because
 * both are things the console has to *draw* differently: a duplicate is not an
 * error and a parked message is not a failure, and a mock that answered `202
 * ACCEPTED` to everything would let a console ship that could not tell the three
 * apart.
 *
 * Excluded from the production bundle by the mock-mode gate in `main.tsx`;
 * `scripts/check-bundle.js` fails the build if a mock artifact leaks.
 */

const CASE_ID = "case-mock-2026";
const WORK_ITEM_ID = "wi-mock-2026";

/**
 * The natural-language door.
 *
 * A **switch, not a constant**, and it earns that twice over. A `const true`
 * narrows to a literal, and the compiler then agrees that a shut door is
 * unreachable -- so the parked branch stops being code anybody can reach or
 * test. And parking is a state the console has to draw *differently*: sect. 5
 * parks rather than refusing, and the panel is where an operator finds that
 * out, so `dev:mock` and the specs both need to be able to shut it.
 */
let nlEnabled = true;

/** Shut or open the door, and see what the panel then says. */
export function setSupportNlEnabled(enabled: boolean): void {
  nlEnabled = enabled;
}
const PER_CASE_QUOTA = 50;

type Inbound = {
  readonly externalMessageId: string;
  readonly supportEventId: string;
  readonly sender: string;
  readonly bodyText: string;
  readonly status: "PROCESSED" | "PARKED";
  readonly intent: string | null;
  readonly recordedAtIso: string;
};

type Store = {
  inbound: Inbound[];
  /** Artifacts the platform has filed against a return, keyed by record. */
  artifacts: Map<string, { artifactType: string; value: string; status: string }[]>;
  /** Artifacts it could not file. */
  unbound: { artifactType: string; value: string; status: string; evidenceSpan: string }[];
};

function freshStore(): Store {
  return {
    inbound: [
      {
        externalMessageId: "ext-seed-1",
        supportEventId: "evt-seed-1",
        sender: "the support desk",
        bodyText: "Authorised. The reference and the parcel details are below.",
        status: "PROCESSED",
        intent: "rma_issued",
        recordedAtIso: new Date(Date.now() - 20 * 60_000).toISOString(),
      },
    ],
    artifacts: new Map([
      [
        "rec-mock-1",
        [
          { artifactType: "TRACKING", value: "the parcel Support gave us", status: "BOUND" },
          { artifactType: "RETURN_LOCATION", value: "the north dock", status: "BOUND" },
        ],
      ],
    ]),
    unbound: [
      {
        artifactType: "LABEL",
        value: "a label reference nobody can place",
        status: "UNMATCHED",
        evidenceSpan: "label attached, see below",
      },
    ],
  };
}

let store = freshStore();

/** Test seam. `dev:mock` never calls it; a test that wants a clean case does. */
export function resetSupportMocks(): void {
  store = freshStore();
  nlEnabled = true;
}

function parkedCount(): number {
  return store.inbound.filter((message) => message.status === "PARKED").length;
}

/**
 * V2's contributed sections, composed from the same store the ingress writes.
 *
 * Exported rather than served by a second panel handler: MSW takes the first
 * matching handler, so a second `GET .../panel` would shadow the panel's own and
 * silently take V1's reviews off the screen. One panel, composed from both
 * slices, is what the backend registry does too.
 */
export function supportPanelSections(): readonly {
  section_id: string;
  payload: Record<string, unknown>;
  status: string;
  reason: string | null;
}[] {
  return [
    {
      section_id: "support_parked_messages",
      // **camelCase, per AMENDMENT-7.** The DTO's own fields are snake_case;
      // a section's opaque payload mirrors the stored documents it carries.
      payload: {
        count: parkedCount(),
        nlEnabled: nlEnabled,
        quota: PER_CASE_QUOTA,
        oldestParkedAtIso:
          store.inbound.find((message) => message.status === "PARKED")?.recordedAtIso ?? null,
      },
      status: "ok",
      reason: null,
    },
    {
      section_id: "support_return_records",
      payload: {
        records: [...store.artifacts].map(([returnRecordId, artifacts]) => ({
          returnRecordId: returnRecordId,
          artifacts,
        })),
        // A single object, which is the shape the reader takes and the shape the
        // case projection produces: one facility and one bay per case.
        placement: {
          facilityId: "the northern site",
          bayId: "the far aisle",
          reason: "oversize goods",
        },
        unbound: store.unbound,
        framingPromptKey: "support-multi-record-do-not-mix",
      },
      status: "ok",
      reason: null,
    },
    {
      section_id: "support_thread_digest",
      payload: {
        messages: store.inbound.map((message) => ({
          supportEventId: message.supportEventId,
          senderDisplayName: message.sender,
          status: message.status,
          intent: message.intent,
          preview: message.bodyText,
          recordedAtIso: message.recordedAtIso,
        })),
        total: store.inbound.length,
      },
      status: "ok",
      reason: null,
    },
  ];
}

function envelope<T>(data: T, requestId: string) {
  return {
    data,
    meta: {
      schema_version: "1.0",
      request_id: `mock-${requestId}`,
      generated_at: new Date().toISOString(),
      freshness: "LIVE",
      partial: false,
      warnings: [],
    },
  };
}

export const supportHandlers = [
  /**
   * `POST .../work-items/{id}/inbound-messages` -- AMENDMENT-3's path.
   *
   * **202, never 201**, and the receipt carries no intent: nothing has been
   * acted on when the response is written, and the classification is a model's
   * answer accepted asynchronously under S2's analysis record. A field here
   * carrying it would be this handler claiming an analysis it did not wait for.
   *
   * A shut door answers `202 PARKED`, not a 5xx: an operator's switch does not
   * belong inside a transport's retry budget.
   */
  http.post(
    "/api/v1/return-support/work-items/:workItemId/inbound-messages",
    async ({ params, request }) => {
      await delay(30);
      if (String(params.workItemId) !== WORK_ITEM_ID) {
        // 404, never 403: confirming that another tenant's work item exists is
        // the disclosure the backend refuses to make.
        return HttpResponse.json(
          {
            detail: {
              code: "WORK_ITEM_NOT_FOUND",
              message: "That work item does not exist.",
              retryable: false,
            },
          },
          { status: 404 },
        );
      }

      const body = (await request.json()) as {
        external_message_id: string;
        body_text: string;
        sender: string;
        sender_display_name?: string | null;
      };

      const held = store.inbound.find(
        (message) => message.externalMessageId === body.external_message_id,
      );
      if (held) {
        // A redelivery is not an error and not a second message. Same receipt,
        // same event id, `DUPLICATE`.
        return HttpResponse.json(
          envelope(
            {
              caseId: CASE_ID,
              supportEventId: held.supportEventId,
              disposition: "DUPLICATE",
              outboxCommandId: null,
              parkedCount: parkedCount(),
            },
            "inbound-message",
          ),
          { status: 202 },
        );
      }

      const supportEventId = `evt-mock-${String(store.inbound.length + 1)}`;
      store.inbound.push({
        externalMessageId: body.external_message_id,
        supportEventId,
        sender: body.sender_display_name ?? body.sender,
        bodyText: body.body_text,
        status: nlEnabled ? "PROCESSED" : "PARKED",
        intent: nlEnabled ? "other" : null,
        recordedAtIso: new Date().toISOString(),
      });

      return HttpResponse.json(
        envelope(
          {
            caseId: CASE_ID,
            supportEventId,
            disposition: nlEnabled ? "ACCEPTED" : "PARKED",
            outboxCommandId: nlEnabled ? `cmd-mock-${supportEventId}` : null,
            parkedCount: parkedCount(),
          },
          "inbound-message",
        ),
        { status: 202 },
      );
    },
  ),
];
