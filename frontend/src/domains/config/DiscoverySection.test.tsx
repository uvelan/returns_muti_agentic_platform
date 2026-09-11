/**
 * `/config/discovery`: load the active release's discovery slice, edit a
 * typed field, Validate (errors mapped onto the field), Publish through the
 * single-call `/api/config/publish` with the merge patch under the right
 * domain and `expected_head_revision` from the loaded snapshot, and see the
 * success notice.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError } from "../../api/client";
import { CapabilityContext } from "../../hooks/capabilityContext";
import { DiscoverySection } from "./DiscoverySection";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  validateDomain: vi.fn(),
  publish: vi.fn(),
  activeDocument: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    validateDomain: mocks.validateDomain,
    publish: mocks.publish,
  },
}));

vi.mock("../../api/schemaReleases", () => ({
  schemaReleasesApi: { activeDocument: mocks.activeDocument },
}));

const DISCOVERY = {
  web_order_pattern: "^WEB-\\d{8}$",
  ambiguity_gap_millionths: 150_000,
  auto_confirmation_allowed: false,
  anchor_weights: {},
  conflict_penalty_millionths: 0,
  strong_anchors: ["exact_order_key"],
  anchor_extractors: [],
  free_text_fallback_anchor: "customer_description",
  conversation: {},
  progressive: {},
  identification_fields: [],
};

function configuration() {
  return {
    discovery: DISCOVERY,
    source_resolution: {},
    clarification_policy: { fields: [] },
    selection_vocabulary: { reasons: [], conditions: [] },
  };
}

let grants: string[] = [];

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <CapabilityContext.Provider
        value={{
          principal: undefined,
          isLoading: false,
          isUnauthenticated: false,
          error: null,
          can: (capability) => grants.includes(capability),
          canAny: (...capabilities) => capabilities.some((one) => grants.includes(one)),
        }}
      >
        {children}
      </CapabilityContext.Provider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  grants = ["config.runtime.read", "config.release.write", "config.release.promote"];
  mocks.runtime.mockReset().mockResolvedValue({
    release_id: "rel-1",
    head_revision: 41,
    configuration: configuration(),
  });
  mocks.activeDocument.mockReset().mockResolvedValue({
    configurationReleaseId: "rel-1",
    configurationChecksum: "abc",
    schemaVersion: "1",
    document: {},
    fromFile: false,
  });
  mocks.validateDomain.mockReset().mockResolvedValue({ valid: true, errors: [] });
  mocks.publish.mockReset().mockResolvedValue({
    release_id: "publish-1",
    status: "RELEASED",
    created_at: "2026-09-01T00:00:00Z",
    created_by: "operator",
    checksum_sha256: "abc",
    domains: {},
    head_revision: 42,
    audit_ids: ["a1", "a2", "a3", "a4", "a5"],
  });
});

describe("Discovery screen", () => {
  it("loads the active release's discovery slice", async () => {
    render(<DiscoverySection />, { wrapper: Wrapper });
    expect(await screen.findByRole("spinbutton", { name: /Ambiguity gap/ })).toHaveValue(150_000);
  });

  it("maps a Validate error onto the field it names", async () => {
    const user = userEvent.setup();
    mocks.validateDomain.mockResolvedValue({
      valid: false,
      errors: [{ path: "discovery.ambiguity_gap_millionths", message: "Too high.", type: "value_error" }],
    });
    render(<DiscoverySection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Ambiguity gap/ });
    await user.clear(field);
    await user.type(field, "900000");
    await user.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => { expect(mocks.validateDomain).toHaveBeenCalled(); });
    const [domainKey, body] = mocks.validateDomain.mock.calls[0] as [string, { patch: Record<string, unknown> }];
    expect(domainKey).toBe("RETURN_PLATFORM");
    expect((body.patch.discovery as Record<string, unknown>).ambiguity_gap_millionths).toBe(900_000);

    // Shows both inline (on the field) and in the page-level validation
    // summary -- see `TypedSectionScreen`'s note on why a hand-written typed
    // form duplicates rather than dedupes.
    expect(await screen.findAllByText("Too high.")).not.toHaveLength(0);
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("publishes the merge patch under discovery on RETURN_PLATFORM with the loaded head revision, and shows the success notice", async () => {
    const user = userEvent.setup();
    render(<DiscoverySection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Ambiguity gap/ });
    await user.clear(field);
    await user.type(field, "300000");

    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalled(); });
    const [options] = mocks.publish.mock.calls[0] as [
      { domainKey: string; patch: Record<string, unknown>; expectedHeadRevision: number },
    ];
    expect(options.domainKey).toBe("RETURN_PLATFORM");
    expect(options.expectedHeadRevision).toBe(41);
    expect((options.patch.discovery as Record<string, unknown>).ambiguity_gap_millionths).toBe(300_000);
    // Nothing else on the slice moved -- the merge patch names only the
    // changed field, not the whole discovery document.
    expect(options.patch.source_resolution).toBeUndefined();

    expect(await screen.findByText(/Release publish-1 is published/)).toBeInTheDocument();
  });

  it("disables Publish when config.release.promote is missing", async () => {
    grants = ["config.runtime.read", "config.release.write"];
    render(<DiscoverySection />, { wrapper: Wrapper });
    await screen.findByRole("spinbutton", { name: /Ambiguity gap/ });
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  // RV F2: `canWrite` used to gate only the Advanced/JSON branch. A
  // principal with `config.release.promote` but not `config.release.write`
  // saw every typed field enabled -- the backend would 403 the write, but
  // nothing on screen said so first.
  it("disables every typed field and shows the read-only notice when config.release.write is missing", async () => {
    grants = ["config.runtime.read", "config.release.promote"];
    render(<DiscoverySection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Ambiguity gap/ });
    expect(field).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Allow auto-confirmation" })).toBeDisabled();
    expect(screen.getByText(/Read-only access\. Editing this section requires config\.release\.write\./)).toBeInTheDocument();
  });

  // RV F9: no test anywhere covered a refused publish; `publish` was mocked
  // resolved in every existing test.
  it("shows the error and keeps the draft when Publish is refused", async () => {
    const user = userEvent.setup();
    mocks.publish.mockRejectedValue(
      new APIError("Domain patch refused: ambiguity_gap_millionths must be at most 1000000", 422),
    );
    render(<DiscoverySection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Ambiguity gap/ });
    await user.clear(field);
    await user.type(field, "900000");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    expect(await screen.findByText(/Domain patch refused/)).toBeInTheDocument();
    // The draft survives the refusal -- an operator's edit is not thrown
    // away by a rejection they still need to act on.
    expect(field).toHaveValue(900_000);
    expect(screen.queryByText(/is published/)).not.toBeInTheDocument();
  });

  // RV F5: a 409 used to leave `expectedHeadRevision` stale forever (no
  // `onError`, so nothing refreshed it) and the only refresh that existed
  // changed the editor's React `key`, silently discarding the draft.
  it("recovers from a revision conflict without losing the draft, and retries with the fresh head", async () => {
    const user = userEvent.setup();
    mocks.publish
      .mockRejectedValueOnce(new APIError("Configuration head revision changed from 41 to 44", 409))
      .mockResolvedValueOnce({
        release_id: "publish-2",
        status: "RELEASED",
        created_at: "2026-09-01T00:00:00Z",
        created_by: "operator",
        checksum_sha256: "abc",
        domains: {},
        head_revision: 45,
        audit_ids: ["a1", "a2", "a3", "a4", "a5"],
      });
    render(<DiscoverySection />, { wrapper: Wrapper });

    const field = await screen.findByRole("spinbutton", { name: /Ambiguity gap/ });
    // Queued *after* mount consumes the base (head 41) -- this is what the
    // `onError` handler's own direct `configApi.runtime()` call (not
    // through the shared query cache) picks up next.
    mocks.runtime.mockResolvedValueOnce({
      release_id: "rel-1",
      head_revision: 44,
      configuration: configuration(),
    });
    await user.clear(field);
    await user.type(field, "300000");
    await user.click(screen.getByRole("button", { name: "Publish" }));

    await waitFor(() => { expect(mocks.publish).toHaveBeenCalledTimes(1); });
    expect(await screen.findByText(/The release moved to head 44 while you were editing\. Review the diff and publish again\./)).toBeInTheDocument();
    // The draft is intact -- no remount, no reset.
    expect(field).toHaveValue(300_000);

    await user.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => { expect(mocks.publish).toHaveBeenCalledTimes(2); });
    const [secondCallOptions] = mocks.publish.mock.calls[1] as [{ expectedHeadRevision: number }];
    expect(secondCallOptions.expectedHeadRevision).toBe(44);
    expect(await screen.findByText(/Release publish-2 is published/)).toBeInTheDocument();
  });
});
