import { useEffect, useRef, useState } from "react";
import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "wouter";

import {
  newConversationId,
  orderAgentApi,
  type AgentTurnResult,
  type CapturedFact,
  type ConversationTranscript,
} from "../../api/orderAgent";
import {
  caseLifecycle,
  caseRefetchInterval,
  caseRetry,
  casesApi,
  keepNewerRevision,
  projectedFactString,
  type CaseProjection,
  type ConfirmedOrderProjection,
} from "../../api/cases";
import {
  orderLinesApi,
  type OrderLines,
  type ReturnContactRequest,
  type SelectedItemRequest,
} from "../../api/orderLines";
import {
  capturedFactOrder,
  selectionVocabulary,
  type RuntimeConfig,
} from "../../api/runtimeConfig";
import { returnHistoryApi } from "../../api/returnHistory";
import { useCapabilities } from "../../hooks/capabilityContext";
import { useRuntimeConfig } from "../../hooks/useRuntimeConfig";
import { DomainRail, RailFact, RailNote, RailSection } from "../DomainRail";

import { ReturnCopilotShell } from "./panes/ReturnCopilotShell";
import { ConversationPane, type ChatHistoryEntry } from "./panes/ConversationPane";
import { ProgressTruthPane } from "./panes/ProgressTruthPane";
import { BusinessObjectPane } from "./panes/BusinessObjectPane";
import { extractedReturnFields } from "./extractedFields";
import { caseRecords, caseShipments, deriveCopilotMode } from "./types";

import { DiscoveryMode } from "./modes/DiscoveryMode";
import { CandidateOrderMode } from "./modes/CandidateOrderMode";
import {
  ItemSelectionMode,
  type BranchAssociateContact,
} from "./modes/ItemSelectionMode";
import { ReturnEvaluationMode } from "./modes/ReturnEvaluationMode";
import { AuthorizedRmaMode } from "./modes/AuthorizedRmaMode";
import { CarrierTransitMode } from "./modes/CarrierTransitMode";
import { WarehouseReceivingMode } from "./modes/WarehouseReceivingMode";
import { ReturnSettlementMode } from "./modes/ReturnSettlementMode";

/**
 * What the model has extracted from the conversation, for the
 * "Extracted & Verified Facts" panel.
 *
 * **Captured facts, never statements, and that is the correction.** An earlier
 * pass here filtered `history` for `GRAPH_FACT` and `USER_PROVIDED_FACT`
 * statements and fed the panel their `text`. Statements are prose the agent
 * uttered -- on a live run the panel read "Line 1 has no product, quantity or
 * amount recorded against it", which is narration of the agent's own reasoning
 * and exposes internal working under a heading promising extracted fields.
 *
 * `AgentTurnResult.captured_facts` is the honest source: named values the model
 * pulled out of the associate's sentence, accepted by the configured fact
 * catalogue, merged across the whole conversation and flushed into the case
 * fact log at confirmation. The turn carries the merged set, so the latest turn
 * is the whole picture and nothing accumulates per mention.
 */
function capturedFacts(turn: AgentTurnResult | null): readonly CapturedFact[] {
  return turn?.captured_facts ?? [];
}

/**
 * The branch associate the case has recorded, off the fact log.
 *
 * The three names the selection write appends. Read from `CaseProjection.facts`
 * rather than kept in client state, for the reason the bay recommendation is:
 * the case is the record, and a second copy in the browser is the copy that can
 * disagree with it. Absent is entirely ordinary -- the fields are optional --
 * so every part is independently nullable and nothing is defaulted.
 */
function branchAssociate(projection: CaseProjection | null): BranchAssociateContact {
  const facts = projection?.facts ?? null;
  return {
    name: projectedFactString(facts, "branch_associate_name"),
    email: projectedFactString(facts, "branch_associate_email"),
    phone: projectedFactString(facts, "branch_associate_phone"),
  };
}

/**
 * The return reason the conversation captured, verbatim and unmapped.
 *
 * The case's recorded copy wins over the turn's, which is the rule
 * `extractedFields.conversationalField` already applies and for the same
 * reason: `confirm_case` flushes captured facts into the fact log the moment a
 * case exists, so once there is one it is the authority and the conversation's
 * copy covers the turns before it.
 *
 * **Handed on unchanged.** `return_reason` carries no validation pattern in
 * `clarification_policy.fields`, so this is free text and may be anything the
 * associate said. Deciding whether it is a term the release publishes belongs
 * to the pane that owns the catalogue, and it is an exact match or nothing --
 * a translation performed here would be a mapping invented in the client, one
 * layer further from where anybody would look for it.
 */
function statedReturnReason(
  turn: AgentTurnResult | null,
  projection: CaseProjection | null,
): string | null {
  const recorded = projectedFactString(projection?.facts ?? null, "return_reason");
  if (recorded !== null) return recorded;
  const heard = capturedFacts(turn).find((fact) => fact.name === "return_reason");
  return heard === undefined ? null : text(heard.value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

/**
 * What clicking a candidate should say in the conversation, or `null` when the
 * row identifies nothing the agent could act on.
 *
 * Candidate rows are not one shape. Searching an order number returns rows with
 * `sales_order_number`; searching a customer name returns rows with only
 * `account_id`, `customer_id` and `customer_name`. Confirming an order is the
 * stronger statement, so it wins whenever the row can support it.
 *
 * A customer row confirms the *customer*, which is the honest first step of a
 * narrowing that has not reached an order yet -- and it is what lets the flow
 * go name, then product, then order, rather than jumping to an order the
 * associate has not chosen. `account_id` is included because a customer id is
 * only unique within an account.
 *
 * Returning `null` rather than a vague sentence matters: an empty submission
 * would put a meaningless turn on the record and cost a reasoning round trip.
 */
function confirmationFor(chosen: Record<string, unknown>): string | null {
  // Kept deliberately plain and minimal. This is a *button press* rendered as
  // text so the agent can act on it -- an intent marker, not dialogue. Warm,
  // natural conversation is the model's job, written under the tone rules in
  // `ORDER_AGENT_REASONING_V1`'s system prompt; hardcoding friendly phrasing
  // here would put conversation content in the client, where no release can
  // change it and no prompt version governs it.
  const order = text(chosen.sales_order_number);
  if (order !== null) return `Confirm order ${order}.`;

  // `customer_id` is deliberately not read here. By operator instruction
  // (2026-08-15) the internal ERP customer number must not be exposed, and what
  // is submitted goes into a stored, replayable transcript -- a more durable
  // exposure than a table cell. The account narrows the name sufficiently, and
  // the agent still holds the candidate set to resolve the exact row.
  const name = text(chosen.customer_name);
  if (name === null) return null;

  const account = text(chosen.account_id);
  return account !== null
    ? `Confirm the customer ${name} on account ${account}.`
    : `Confirm the customer ${name}.`;
}

/**
 * The rows a turn's searches produced, and how many the graph actually matched.
 *
 * **The rows are a page, and the total says so.** `order_search` serves five
 * candidates at a time out of at most twenty-five cached, and reports
 * `total_found` on the same evidence record. A table headed by
 * `candidates.length` would announce five matches for a search that found four
 * hundred, contradicting the agent's own rules in the pane beside it. Where
 * several evidence records carry a total -- a turn that paged -- the largest is
 * the search's, since each page reports the same figure.
 */
type TurnCandidates = {
  readonly rows: Record<string, unknown>[];
  readonly totalFound: number | null;
};

function turnCandidates(turn: AgentTurnResult): TurnCandidates | null {
  const searches = turn.query_evidence.flatMap((evidence) =>
    isRecord(evidence.result) && Array.isArray(evidence.result.candidates)
      ? [evidence.result]
      : [],
  );
  if (searches.length === 0) return null;
  const totals = searches.flatMap((result) =>
    typeof result.total_found === "number" && Number.isFinite(result.total_found)
      ? [result.total_found]
      : [],
  );
  return {
    rows: searches
      .flatMap((result) => result.candidates as unknown[])
      .flatMap((candidate: unknown) =>
        isRecord(candidate) && isRecord(candidate.data) ? [candidate.data] : [],
      ),
    totalFound: totals.length === 0 ? null : Math.max(...totals),
  };
}

function resolvedOrderReference(candidates: readonly Record<string, unknown>[]): string | null {
  if (candidates.length !== 1) return null;
  const value = candidates[0].sales_order_number;
  return typeof value === "string" ? value : null;
}

/**
 * The confirmed order, rendered in the candidate pane's own shape.
 *
 * A resumed return has no search behind it: the associate did the search in a
 * conversation that ended days ago, and `query_evidence` is not replayed. The
 * candidate list therefore came back empty and the order the case was raised
 * for -- which the platform knows perfectly well -- was nowhere on screen.
 *
 * Every value here is the case's. Nothing is defaulted, and a field the
 * projection does not carry is simply not a column.
 */
function confirmedOrderRow(
  order: ConfirmedOrderProjection | null,
): Record<string, unknown>[] {
  if (order === null) return [];
  const row: Record<string, unknown> = { sales_order_number: order.orderReference };
  if (order.orderSource !== null) row.order_source = order.orderSource;
  if (order.sourceWebOrderNumber !== null) row.web_order_number = order.sourceWebOrderNumber;
  if (order.trilogieOrderNumber !== null) row.trilogie_order_number = order.trilogieOrderNumber;
  if (order.confirmedAt !== null) row.confirmed_at = order.confirmedAt;
  return [row];
}

type HistoryAnchor =
  | { orderReference: string; customerId: null; accountId: null }
  | { orderReference: null; customerId: string; accountId: string };

function historySearchKey(
  candidates: readonly Record<string, unknown>[],
): HistoryAnchor | null {
  if (candidates.length !== 1) return null;
  const candidate = candidates[0];
  const order =
    typeof candidate.sales_order_number === "string" &&
    candidate.sales_order_number.length > 0
      ? candidate.sales_order_number
      : null;
  const customer =
    typeof candidate.customer_id === "string" && candidate.customer_id.length > 0
      ? candidate.customer_id
      : null;
  const account =
    typeof candidate.account_id === "string" && candidate.account_id.length > 0
      ? candidate.account_id
      : null;

  if (customer !== null && account !== null) {
    return { orderReference: null, customerId: customer, accountId: account };
  }
  if (order !== null) {
    return { orderReference: order, customerId: null, accountId: null };
  }
  return null;
}

/**
 * Which agent a turn is addressed to, and why it can be missing.
 *
 * The page used to send the literal `"order_discovery"` while the active schema
 * keys the policy `order-discovery-agent`, so every turn came back
 * `422 ORDER_AGENT_OUT_OF_SCOPE`. The id is now served by
 * `/api/runtime-config`, and the two ways it can be absent are different
 * situations that deserve different sentences: the bootstrap fetch has not
 * landed (ordinary, and momentary), or it landed and the deployment has not
 * configured the mapping (a configuration defect an operator must fix).
 *
 * Neither one falls back to a literal. A second guessed id would fail exactly
 * the same way as the first, several minutes later, in a conversation an
 * associate had already started.
 */
function agentBinding(
  runtimeConfig: RuntimeConfig | null,
): { agentId: string; error: null } | { agentId: null; error: Error } {
  const configured = runtimeConfig?.agents?.orderDiscovery ?? null;
  if (configured !== null) {
    return { agentId: configured, error: null };
  }
  return {
    agentId: null,
    error: new Error(
      runtimeConfig === null
        ? "Runtime configuration has not loaded yet, so the copilot cannot address an agent."
        : "This deployment has no copilot.order_discovery_agent_id configured, so no message " +
          "can be sent. Set it in the return configuration release.",
    ),
  };
}

/**
 * The two identifiers that survive a reload, and the only things that do.
 *
 * **IDs only, in the URL, and never business state in `localStorage`.** A cached
 * RMA or policy decision is a copy of something the platform owns, and a copy
 * outlives the fact -- an associate reloading after a cancellation would be
 * shown the return as it was. Two ids cost one transcript read and one case
 * read to rebuild everything from its source, and the URL makes the return
 * shareable and back-button-able for free.
 */
const CONVERSATION_PARAM = "conversationId";
const CASE_PARAM = "caseId";

/**
 * A stored transcript replayed as plain speech.
 *
 * `restored` rather than `agent`: the record keeps role and text, not the typed
 * statements, so a replayed turn must not be able to claim it cited evidence it
 * no longer carries.
 */
function restoredHistory(transcript: ConversationTranscript): ChatHistoryEntry[] {
  return transcript.messages.map((message, index) => ({
    role: "restored" as const,
    id: `${transcript.conversationId}-${String(index)}`,
    author: message.role,
    text: message.text,
  }));
}

export function ReturnCopilotPage() {
  const { can } = useCapabilities();
  const runtimeConfig = useRuntimeConfig();
  const { agentId, error: agentConfigurationError } = agentBinding(runtimeConfig);
  // The released reason and condition catalogues. Empty means the deployment
  // has published none, which the pane says rather than substituting a list.
  const vocabulary = selectionVocabulary(runtimeConfig);
  // The release's own ranking of the conversation's facts. Empty means the
  // deployment stated none, which the facts panel handles by falling back to a
  // documented alphabetical order rather than to a list written in the client.
  const factOrder = capturedFactOrder(runtimeConfig);
  const queries = useQueryClient();
  const [routeParams, setRouteParams] = useSearchParams();
  const routeConversationId = routeParams.get(CONVERSATION_PARAM);
  const routeCaseId = routeParams.get(CASE_PARAM);
  // Minted, not chosen: a conversation the associate has not spoken into yet
  // has no server-side transcript, so there is nothing to put in the URL until
  // the first turn lands. Until then this is the id that turn will be sent
  // under.
  const [mintedConversationId, setMintedConversationId] = useState<string>(() =>
    newConversationId(),
  );
  const conversationId = routeConversationId ?? mintedConversationId;
  const [history, setHistory] = useState<readonly ChatHistoryEntry[]>([]);
  const [draft, setDraft] = useState<string>("");
  const [showHistory, setShowHistory] = useState<boolean>(false);
  const [turn, setTurn] = useState<AgentTurnResult | null>(null);
  const [candidates, setCandidates] = useState<readonly Record<string, unknown>[]>([]);
  /** What the search matched, as opposed to what it handed over. `null` if unreported. */
  const [candidateTotal, setCandidateTotal] = useState<number | null>(null);
  const versionRef = useRef<number>(0);
  /**
   * The conversation whose transcript this mount has already dealt with.
   *
   * Set both by the rehydration effect and by every writer of the URL, because
   * they are the same claim: "the history on screen is this conversation's".
   * Without the second half, the first turn writes its id to the URL, the
   * effect sees a conversation id appear and re-reads the transcript over the
   * live exchange -- replacing typed statements with replayed plain text a
   * second after they arrived.
   */
  const restoredConversation = useRef<string | null>(null);

  /** Write identity to the URL, replacing rather than pushing: resuming a return
   * is not a navigation the back button should have to walk through. */
  function rememberIdentity(next: { conversationId?: string; caseId?: string | null }) {
    if (next.conversationId !== undefined) {
      restoredConversation.current = next.conversationId;
    }
    setRouteParams(
      (previous) => {
        const updated = new URLSearchParams(previous);
        if (next.conversationId !== undefined) {
          updated.set(CONVERSATION_PARAM, next.conversationId);
        }
        if (next.caseId !== undefined) {
          if (next.caseId === null) updated.delete(CASE_PARAM);
          else updated.set(CASE_PARAM, next.caseId);
        }
        return updated;
      },
      { replace: true },
    );
  }

  // The turn that just confirmed knows the case first; the URL knows it across
  // a reload. Nothing else does, and no third copy is kept in state.
  const caseId = turn?.case_id ?? routeCaseId;
  const caseRead = useQuery({
    queryKey: ["cases", caseId],
    // **The read.** One `GET /api/cases/{caseId}` carries enough authoritative
    // state to drive the whole screen -- stage included -- with no
    // `ReturnSession` read behind it, which is what the deleted
    // `returnsApi.list()` scan used to attempt in the browser.
    queryFn: caseId === null ? skipToken : () => casesApi.readProjection(caseId),
    // Business truth, not array length. `caseRefetchInterval` carries the
    // reasoning; the short version is that an RMA with no label is a case still
    // being worked, and the shipped predicate stopped on it.
    refetchInterval: (query) => caseRefetchInterval(query.state.data, query.state.error),
    // A late response carrying a lower revision is discarded before it becomes
    // query data, so it cannot walk the screen backwards.
    structuralSharing: keepNewerRevision,
    // A counter terminal loses its network for a minute; the case is re-read on
    // the way back. **The agent turn is not re-issued** -- it is a mutation, and
    // mutations are never refetched. Reconnecting must not spend a model call
    // or append a turn nobody asked for.
    refetchOnReconnect: true,
    // Bounded, and never against a refusal the backend has already explained.
    retry: caseRetry,
  });

  const projection = caseRead.data ?? null;
  // Structural rather than a cast: a cache holding a body written before the
  // route swap, or a decode that failed, must read as "no lifecycle" instead of
  // asserting a stage that is not there.
  const lifecycle = caseLifecycle(caseRead.data);

  const historyAnchor = historySearchKey(candidates);
  const returnHistory = useQuery({
    queryKey: [
      "return-history",
      historyAnchor?.orderReference ?? null,
      historyAnchor?.accountId ?? null,
      historyAnchor?.customerId ?? null,
    ],
    queryFn:
      historyAnchor === null
        ? skipToken
        : () =>
            historyAnchor.customerId === null
              ? returnHistoryApi.byOrder(historyAnchor.orderReference)
              : returnHistoryApi.byCustomer(
                  historyAnchor.accountId,
                  historyAnchor.customerId,
                ),
  });

  /**
   * The confirmed order's lines, which is what the associate selects from.
   *
   * Case-scoped, because that is the only shape the platform serves: there is
   * deliberately no `/api/orders/{reference}/lines`, since a naked order number
   * is not an authorization boundary. Nothing is asked for until there is a
   * case, and a case with no confirmed order answers `409 ORDER_NOT_CONFIRMED`
   * rather than an empty order.
   *
   * Not polled. Availability moves when somebody else selects the same line,
   * and the write re-evaluates it inside its own transaction and refuses with
   * the recomputed figures -- so a poll would spend requests to pre-empt a
   * refusal the writer already explains.
   */
  // Explicitly typed: `caseRetry` declares its error parameter `unknown`, which
  // would otherwise widen the query's error type and leave the pane unable to
  // read a `message` off the refusal the backend already wrote.
  const orderLines = useQuery<OrderLines>({
    queryKey: ["order-lines", caseId],
    queryFn: caseId === null ? skipToken : () => orderLinesApi.read(caseId),
    retry: caseRetry,
  });

  /**
   * Record the whole selection against the case (plan sect. 12.4).
   *
   * **This is the write whose absence disabled the item-selection pane.** Its
   * docstring said the controls were inert because no case-scoped selection
   * write existed; `POST /api/cases/{caseId}/selected-items` is it, and it
   * validates reason and condition against the released `selection_vocabulary`,
   * refusing an unpublished term with `422 SELECTION_TERM_NOT_PUBLISHED`.
   *
   * On success both the case and the line availability are re-read rather than
   * patched from the response. The response does carry them, but the case is
   * the thing that moves this whole screen -- stage included -- and a
   * client-side patch of the projection is exactly the copy that can disagree
   * with the platform.
   */
  const selectItems = useMutation({
    mutationFn: (submission: {
      readonly items: readonly SelectedItemRequest[];
      readonly contact: ReturnContactRequest | null;
    }) => {
      if (caseId === null) {
        return Promise.reject(
          new Error("No case has been raised yet, so no selection can be recorded."),
        );
      }
      return orderLinesApi.replaceSelection(caseId, submission.items, submission.contact);
    },
    onSuccess: async () => {
      await Promise.all([
        queries.invalidateQueries({ queryKey: ["cases", caseId] }),
        queries.invalidateQueries({ queryKey: ["order-lines", caseId] }),
      ]);
    },
  });

  const send = useMutation({
    mutationFn: (message: string) => {
      // Fail closed. `submit` already refuses, so reaching here means a caller
      // bypassed it -- and sending a guessed id would spend a real turn to
      // learn what this branch already knows.
      if (agentId === null) return Promise.reject(agentConfigurationError);
      return orderAgentApi.sendTurn({
        conversationId,
        expectedConversationVersion: versionRef.current,
        message,
        agentId,
      });
    },
    onSuccess: (result) => {
      versionRef.current = result.conversation_version;
      setTurn(result);
      // Only now does the conversation exist server-side, so only now is there
      // a transcript for a reload to come back to.
      rememberIdentity({
        conversationId,
        ...(result.case_id ? { caseId: result.case_id } : {}),
      });
      // **No state transition is read off the turn.** `response.status` and
      // `business_capability` used to move this screen -- an `APPROVED` or an
      // `ISSUED` from the model advanced the lifecycle and minted a policy
      // decision out of nothing. They are conversational metadata; the case
      // says what happened.
      const found = turnCandidates(result);
      if (found !== null) {
        setCandidates(found.rows);
        setCandidateTotal(found.totalFound);
      }
      setHistory((previous) => [
        ...previous,
        {
          role: "agent",
          id: result.client_turn_id,
          statements: result.response.statements,
          status: result.response.status,
        },
      ]);
    },
  });

  /**
   * Resume a conversation, and the return it raised.
   *
   * **The lookup is for the rows that do not know.** The open-cases list knows
   * the case id -- it draws each row with it -- and hands it over, so resuming
   * an open return costs one transcript read and no case-discovery round trip
   * at all. The conversation-history rows are past searches, most of which
   * raised nothing, and there `casesApi.list(conversationId)` is the only way
   * to find out; a conversation raises at most one case, which is what makes
   * `.at(0)` a read of the whole answer rather than a pick from several.
   *
   * The difference is not only a request. When the id was thrown away and
   * re-derived, a lookup that came back empty -- a case the principal cannot
   * see, a list read that failed -- resolved to `null` and *cleared* the case
   * from the URL, so a return the platform had open lost its case id on the way
   * back in and the whole screen fell back to discovery.
   */
  const open = useMutation({
    mutationFn: async ({ id, caseId: known }: { id: string; caseId?: string }) => {
      const transcript = await orderAgentApi.readTranscript(id);
      if (known !== undefined) {
        return { transcript, raisedCase: { caseId: known } };
      }
      const caseList = await casesApi.list(id);
      return {
        transcript,
        raisedCase: caseList.at(0) ?? null,
      };
    },
    onSuccess: ({ transcript, raisedCase }) => {
      setShowHistory(false);
      setHistory(restoredHistory(transcript));
      setTurn(null);
      // `setCandidates([])` used to live here, and the search results of the
      // previous conversation used to survive here. Neither is right: the
      // resumed return's order comes off the case, through `confirmedOrderRow`
      // below, so the stale list is dropped and the authoritative one takes
      // its place.
      setCandidates([]);
      setCandidateTotal(null);
      rememberIdentity({
        conversationId: transcript.conversationId,
        caseId: raisedCase === null ? null : raisedCase.caseId,
      });
      versionRef.current = transcript.conversationVersion;
    },
  });

  /**
   * Rebuild the conversation half of a reload.
   *
   * The case half needs nothing: `routeCaseId` feeds the case query directly,
   * which is the point of putting it in the URL. Re-deriving it from
   * `casesApi.list(conversationId)` -- what the history path does, because
   * there the id is genuinely unknown -- would be a second round trip to
   * re-learn something the address bar already says, and would clear the case
   * on any read that came back empty.
   */
  const restore = useMutation({
    mutationFn: (id: string) => orderAgentApi.readTranscript(id),
    onSuccess: (transcript) => {
      setHistory(restoredHistory(transcript));
      versionRef.current = transcript.conversationVersion;
    },
  });

  const restoreTranscript = restore.mutate;
  useEffect(() => {
    // Once per conversation named in the URL. The transcript is immutable
    // history and the mutation would otherwise refire on every state change.
    if (routeConversationId === null) return;
    if (restoredConversation.current === routeConversationId) return;
    restoredConversation.current = routeConversationId;
    restoreTranscript(routeConversationId);
  }, [routeConversationId, restoreTranscript]);

  const conversations = useQuery({
    queryKey: ["order-agent", "conversations"],
    queryFn: () => orderAgentApi.listConversations(),
    enabled: showHistory,
  });

  const cases = useQuery({
    queryKey: ["cases", "list"],
    queryFn: () => casesApi.list(),
    enabled: showHistory && can("returns.session.read"),
  });

  if (!can("returns.session.read")) {
    return (
      <div className="flex h-64 items-center justify-center">
        <p className="text-sm text-outline">
          You do not have access to the returns domain.
        </p>
      </div>
    );
  }

  function submit(message: string) {
    const trimmed = message.trim();
    if (trimmed.length === 0 || send.isPending || agentId === null) return;
    setHistory((previous) => [
      ...previous,
      { role: "associate", id: `me-${String(previous.length)}`, text: trimmed },
    ]);
    setDraft("");
    send.mutate(trimmed);
  }

  function resetToFreshReturn() {
    setShowHistory(false);
    // The one place a new id is minted. The agent's memory is scoped to the
    // conversation, so reusing one would let the previous customer's details
    // inform this customer's search.
    setMintedConversationId(newConversationId());
    // Identity out of the URL as well, or a reload would resume the return the
    // associate has just walked away from.
    restoredConversation.current = null;
    setRouteParams(
      (previous) => {
        const updated = new URLSearchParams(previous);
        updated.delete(CONVERSATION_PARAM);
        updated.delete(CASE_PARAM);
        return updated;
      },
      { replace: true },
    );
    setHistory([]);
    setDraft("");
    setTurn(null);
    setCandidates([]);
    setCandidateTotal(null);
    versionRef.current = 0;
  }

  // The case's own order wins over a search that may be several conversations
  // old; the search list is what the associate is looking at *before* there is
  // a case to be authoritative.
  const confirmedOrder = projection?.confirmedOrder ?? null;
  const shownCandidates =
    confirmedOrder === null ? candidates : confirmedOrderRow(confirmedOrder);
  const confirmedOrderReference =
    confirmedOrder?.orderReference ?? resolvedOrderReference(candidates);

  // **Mode comes from the backend stage and nothing else** once a case exists.
  // `stage` is a pure projection with frozen precedence and monotonicity tests
  // behind it; the screen's job is to draw it, not to re-derive it.
  const activeMode = deriveCopilotMode({
    stage: lifecycle?.stage ?? null,
    candidates: shownCandidates,
  });

  return (
    <>
      <DomainRail>
        <RailSection title="Return Copilot">
          <RailFact label="Mode" value={activeMode} />
          <RailFact label="Stage" value={lifecycle?.stage ?? null} />
          <RailFact label="Case" value={caseId} />
          <RailFact label="Order" value={confirmedOrderReference} />
          <RailNote>
            Post-sidebar 40fr / 24fr / 36fr unified 8-mode lifecycle copilot.
          </RailNote>
        </RailSection>
      </DomainRail>

      <ReturnCopilotShell
        conversationPane={
          <ConversationPane
            history={history}
            draft={draft}
            onDraftChange={setDraft}
            onSubmit={submit}
            onReset={resetToFreshReturn}
            isPending={send.isPending}
            // No agent id, no composer -- but no spinner either. Folding this
            // into `isPending` disabled the box correctly and simultaneously
            // claimed a search was running, next to an alert saying nothing
            // could be sent. `disabled` takes the composer away and says
            // nothing about what the agent is doing.
            disabled={agentId === null}
            error={agentConfigurationError ?? send.error}
            conversations={conversations.data ?? []}
            openCases={cases.data ?? []}
            onOpen={(id, knownCaseId) => {
              open.mutate({ id, ...(knownCaseId === undefined ? {} : { caseId: knownCaseId }) });
            }}
            showHistory={showHistory}
            onToggleHistory={() => {
              setShowHistory((openState) => !openState);
            }}
          />
        }
        progressTruthPane={
          <ProgressTruthPane
            candidates={shownCandidates}
            fields={extractedReturnFields({
              captured: capturedFacts(turn),
              projection,
              factOrder,
            })}
            projection={projection}
            caseId={caseId}
          />
        }
        businessObjectPane={
          <BusinessObjectPane activeMode={activeMode} candidateCount={shownCandidates.length}>
            {activeMode === "DISCOVERY" && <DiscoveryMode turn={turn} />}
            {activeMode === "CANDIDATE_ORDER" && (
              <CandidateOrderMode
                candidates={shownCandidates}
                // The search's own count, so the header cannot announce a page
                // as the whole match set. Absent once the case's confirmed
                // order is what is on screen -- one row is the whole of it.
                totalFound={confirmedOrder === null ? candidateTotal : null}
                returnHistory={returnHistory.data ?? null}
                returnHistoryPending={historyAnchor !== null && returnHistory.isPending}
                returnHistoryError={returnHistory.error}
                onSelectCandidate={(chosen) => {
                  // Only the agent can confirm an order -- confirmation is what
                  // raises the case. The button says which one, in the
                  // conversation, rather than mutating a client-side copy of
                  // the return into the next mode.
                  //
                  // **Candidate shape depends on what was searched.** An order
                  // anchor yields rows carrying `sales_order_number`; a name or
                  // other customer anchor yields customer rows that carry no
                  // order at all. This used to read `sales_order_number` and
                  // silently do nothing when it was absent -- so on a name
                  // search the button rendered enabled and was inert, with no
                  // error and no feedback. Confirming the customer is the real
                  // first step of that narrowing, so say that instead.
                  const confirmation = confirmationFor(chosen);
                  if (confirmation !== null) {
                    submit(confirmation);
                  }
                }}
              />
            )}
            {activeMode === "ITEM_SELECTION" && (
              <ItemSelectionMode
                orderReference={confirmedOrderReference}
                lines={orderLines.data?.lines ?? []}
                linesPending={caseId !== null && orderLines.isPending}
                linesError={orderLines.error}
                items={projection?.selectedItems ?? []}
                // Optional, and absent rather than defaulted: `confirm_case`
                // records a branch only when the principal is scoped to exactly
                // one, and an invented hub routes freight.
                branchReference={projection?.customer?.branchReference ?? null}
                // Optional in the same way and for the same reason: Fergusonhome
                // marks the associate's details optional, and an invented email
                // addresses a UPS label to nobody. Read off the case's fact log,
                // so the pane opens on what was recorded rather than on a copy
                // this page kept.
                contact={branchAssociate(projection)}
                reasons={vocabulary.reasons}
                conditions={vocabulary.conditions}
                // Free text, handed over unmapped. The pane pre-selects it only
                // if it is exactly one of the published terms above.
                capturedReason={statedReturnReason(turn, projection)}
                submitting={selectItems.isPending}
                submitError={selectItems.error}
                onSubmitSelection={(items, contact) => {
                  // The write, and nothing more. Policy evaluation belongs to
                  // `ReturnCaseWorkflow`, which runs it on the case and opens
                  // the Support work item itself; the earlier hook here posted
                  // the words "evaluate policy" into the conversation, which
                  // asked a discovery agent to do something it cannot do.
                  selectItems.mutate({ items, contact });
                }}
              />
            )}
            {activeMode === "RETURN_EVALUATION" && (
              <ReturnEvaluationMode
                evaluation={projection?.policyEvaluation ?? null}
                awaiting={projection?.awaiting ?? []}
                support={projection?.support ?? null}
                // No `onIssueRma`. It submitted the words "authorize rma" into
                // the discovery conversation, and nothing downstream of that
                // sentence issues an RMA: `ReturnCaseWorkflow.run` has already
                // taken the case through the policy gate to `_open_support` by
                // the time this pane is drawn, and the RMA is written by
                // `record_support_outcome` when Support answers the work item.
                // Same defect as the `onEvaluate` that posted "evaluate policy"
                // and was deleted this morning -- a control that asks a
                // discovery agent to do something it cannot reach.
              />
            )}
            {activeMode === "AUTHORIZED_RMA" && (
              <AuthorizedRmaMode returnRecords={caseRecords(projection)} />
            )}
            {activeMode === "CARRIER_TRANSIT" && (
              <CarrierTransitMode shipments={caseShipments(projection)} />
            )}
            {activeMode === "WAREHOUSE_RECEIVING" && (
              <WarehouseReceivingMode warehouse={projection?.warehouse ?? null} />
            )}
            {activeMode === "RETURN_SETTLEMENT" && (
              <ReturnSettlementMode
                settlement={projection?.settlement ?? null}
                caseStatus={projection?.status ?? null}
                onStartNewReturn={resetToFreshReturn}
              />
            )}
          </BusinessObjectPane>
        }
      />
    </>
  );
}
