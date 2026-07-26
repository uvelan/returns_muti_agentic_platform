import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GraphEvidencePage } from "./GraphEvidencePage";

const documentId = "CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7";
const syncRunId = "d084d10c-5bdf-4002-befb-8ccb9948f9e7";
const reportDigest = "75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8";
const summary = {
  schema_version: "1.0", evidence_type: "CUSTOMER_GRAPH_SANDBOX_RUN", document_id: documentId,
  report_digest: reportDigest, document_digest: "6".repeat(64), sync_run_id: syncRunId,
  executed_at: "2026-07-22T11:13:47.868047Z", executed_at_epoch_microseconds: 1784718827868047,
  source_document_id: "P100", source_hash: "a".repeat(64), configuration_digest: "b".repeat(64),
  execution_plan_digest: "c".repeat(64), command_batch_digest: "d".repeat(64),
  evidence_classification: "SANDBOX_VALIDATED", expected_customer_count: 1,
  expected_customer_account_count: 2, expected_relationship_count: 2, idempotent: true,
};
const meta = {
  schema_version: "1.0", request_id: "request-123", generated_at: "2026-07-22T13:10:26.930514Z",
  freshness: "LIVE", partial: false, warnings: [],
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

type ServerOptions = { readonly empty?: boolean; readonly fail?: boolean; readonly forbidFull?: boolean; readonly paginated?: boolean };

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function installServer(options: ServerOptions = {}) {
  const mock = vi.fn<typeof fetch>((input) => {
    const path = requestPath(input);
    if (options.fail) return Promise.resolve(json({ data: null, page: null, meta: { ...meta, partial: true, warnings: [] } }, 503));
    if (path.endsWith("/validation/latest")) {
      return Promise.resolve(options.empty ? json({ data: null, page: null, meta }, 404) : json({ data: summary, page: null, meta }));
    }
    if (path.endsWith("/full")) {
      if (options.forbidFull) return Promise.resolve(json({ data: null, page: null, meta: { ...meta, partial: true, warnings: [{ source: "graph-evidence", code: "FORBIDDEN", message: "Not authorized." }] } }, 403));
      return Promise.resolve(json({ data: { summary, schema_evidence_digest: "e".repeat(64), first_write_evidence_digest: "1".repeat(64), second_write_evidence_digest: "2".repeat(64), first_readback_evidence_digest: "3".repeat(64), second_readback_evidence_digest: "4".repeat(64), idempotency_evidence_digest: "5".repeat(64), report_payload: { process_exit_code: 0, evidence_classification: "SANDBOX_VALIDATED" } }, page: null, meta }));
    }
    if (path.includes("?page_size=")) {
      const secondPage = path.includes("cursor=next-cursor");
      const data = options.empty ? [] : [{ ...summary, source_document_id: secondPage ? "P200" : "P100" }];
      return Promise.resolve(json({ data, page: { next_cursor: options.paginated && !secondPage ? "next-cursor" : null, has_more: Boolean(options.paginated && !secondPage), page_size: 25 }, meta }));
    }
    return Promise.resolve(json({ data: summary, page: null, meta: { ...meta, request_id: "lookup-request" } }));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><GraphEvidencePage /></QueryClientProvider>);
}

describe("GraphEvidencePage", () => {
  it("renders latest validation, history, and request IDs", async () => {
    installServer(); renderPage();
    expect(await screen.findByText("SANDBOX_VALIDATED")).toBeInTheDocument();
    expect(screen.getByText("P100")).toBeInTheDocument();
    expect(screen.getByText(/Latest request ID: request-123/)).toBeInTheDocument();
    expect(screen.getByText(/History request ID: request-123/)).toBeInTheDocument();
  });

  it("renders the empty state", async () => {
    installServer({ empty: true }); renderPage();
    expect(await screen.findByText("No Customer graph evidence has been recorded.")).toBeInTheDocument();
    expect(screen.getByText("No latest validation evidence is available.")).toBeInTheDocument();
  });

  it("renders a hard backend error and retry control", async () => {
    installServer({ fail: true }); renderPage();
    expect(await screen.findByText("Graph evidence is unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("performs exact lookup and opens summary inspection", async () => {
    const mock = installServer(); renderPage(); const user = userEvent.setup();
    await screen.findByText("Immutable evidence history");
    await user.selectOptions(screen.getByLabelText("Identifier type"), "sync-run");
    await user.type(screen.getByLabelText("Exact identifier"), syncRunId);
    await user.click(screen.getByRole("button", { name: "Look up" }));
    expect(await screen.findByText("Inspection")).toBeInTheDocument();
    expect(screen.getByText(/Lookup request ID: lookup-request/)).toBeInTheDocument();
    expect(mock.mock.calls.some(([input]) => requestPath(input).includes(`/sync-runs/${syncRunId}`))).toBe(true);
  });

  it("renders admin full evidence", async () => {
    installServer(); renderPage(); const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Inspect" }));
    await user.click(screen.getByRole("button", { name: "Inspect admin evidence" }));
    expect(await screen.findByText("Validated full report payload")).toBeInTheDocument();
    expect(screen.getByText(/process_exit_code/)).toBeInTheDocument();
  });

  it("keeps summary visible when viewer full access is denied", async () => {
    installServer({ forbidFull: true }); renderPage(); const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Inspect" }));
    await user.click(screen.getByRole("button", { name: "Inspect admin evidence" }));
    expect(await screen.findByText(/requires the console_admin role/)).toBeInTheDocument();
    expect(screen.getAllByText("Document ID").length).toBeGreaterThanOrEqual(2);
  });

  it("navigates forward and back with seek cursors", async () => {
    const mock = installServer({ paginated: true }); renderPage(); const user = userEvent.setup();
    const next = await screen.findByRole("button", { name: "Next" });
    await user.click(next);
    await waitFor(() => { expect(mock.mock.calls.some(([input]) => requestPath(input).includes("cursor=next-cursor"))).toBe(true); });
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await waitFor(() => { expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled(); });
  });

  it("manually refreshes latest and history", async () => {
    const mock = installServer(); renderPage(); const user = userEvent.setup();
    await screen.findByText("Immutable evidence history");
    const before = mock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => { expect(mock.mock.calls.length).toBeGreaterThanOrEqual(before + 2); });
  });
});
