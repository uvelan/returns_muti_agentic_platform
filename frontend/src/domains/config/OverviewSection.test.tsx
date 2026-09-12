/**
 * `/config/overview`: the release/head/releases cards (existing), plus
 * (CFG-4) the undecided-keys panel.
 *
 * A bare unit name is a `RETURN_PLATFORM` key; `AI_GATEWAY`/
 * `DEPENDENCY_SIMULATION` units are shown with their domain. CFG-3a's F11:
 * the panel must render `would_adopt` correctly whether it is empty (no
 * active release, `RETURN_PLATFORM`'s own shape) or full (the other two
 * domains' fallback-to-packaged shape) in the same response, without
 * treating either as wrong.
 *
 * RV round 1 F1 (BLOCKING): the panel used to infer "no active release"
 * from a unit's absence in `would_adopt` -- which is exactly the shape an
 * *undecided* key with a perfectly real active release has by construction
 * (`_carry_forward` keeps the release's own value; `_would_adopt` only
 * reports a key when the merge took the packaged one). On the live stack
 * this was six false sentences next to six enabled buttons. Fixed to derive
 * "no active release" from `headRevision` instead; the fixture below is the
 * live stack's own shape (six undecided keys, `RETURN_PLATFORM`'s
 * `would_adopt` empty, an active release present via `head_revision`).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CapabilityContext } from "../../hooks/capabilityContext";
import { OverviewSection } from "./OverviewSection";

const mocks = vi.hoisted(() => ({
  runtime: vi.fn(),
  releases: vi.fn(),
  packagedDrift: vi.fn(),
  adoptPackaged: vi.fn(),
}));

vi.mock("../../api/configuration", () => ({
  configApi: {
    runtime: mocks.runtime,
    releases: mocks.releases,
    packagedDrift: mocks.packagedDrift,
    adoptPackaged: mocks.adoptPackaged,
  },
}));

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
  grants = ["config.runtime.read", "config.release.read", "config.release.write", "config.release.promote"];
  mocks.runtime.mockReset().mockResolvedValue({
    release_id: "rel-1",
    head_revision: 41,
    configuration: {},
  });
  mocks.releases.mockReset().mockResolvedValue([
    { releaseId: "rel-1", status: "RELEASED", createdAt: "2026-08-01T00:00:00Z", createdBy: "op", checksumSha256: "a" },
  ]);
  mocks.packagedDrift.mockReset().mockResolvedValue({
    // RETURN_PLATFORM: no active release to merge against -- empty would_adopt.
    RETURN_PLATFORM: { undecided: ["discovery"], would_adopt: [], filled_leaves: [] },
    // AI_GATEWAY/DEPENDENCY_SIMULATION: fall back to the packaged file itself
    // in that same state -- every unit reported in would_adopt.
    AI_GATEWAY: { undecided: [], would_adopt: ["tasks.T1", "other"], filled_leaves: [] },
    DEPENDENCY_SIMULATION: { undecided: [], would_adopt: ["dependencies.OMC"], filled_leaves: [] },
  });
  mocks.adoptPackaged.mockReset().mockResolvedValue({
    release_id: "adopt-1",
    status: "RELEASED",
    created_at: "2026-09-01T00:00:00Z",
    created_by: "operator",
    checksum_sha256: "abc",
    domains: {},
    head_revision: 42,
    undecided: { RETURN_PLATFORM: [], AI_GATEWAY: [], DEPENDENCY_SIMULATION: [] },
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("Overview screen -- undecided keys panel", () => {
  it("labels a RETURN_PLATFORM unit bare, and other domains' units with their domain (F5)", async () => {
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });
    expect(await screen.findByText("RETURN_PLATFORM: discovery")).toBeInTheDocument();
  });

  it("renders both would_adopt shapes in one response without treating either as wrong (F11)", async () => {
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

    // RETURN_PLATFORM's own would_adopt is empty in this state -- not shown
    // in the "would adopt" list at all, and that is correct, not a bug.
    await screen.findByText("RETURN_PLATFORM: discovery");
    expect(screen.queryByText(/RETURN_PLATFORM:.*would/)).not.toBeInTheDocument();

    // AI_GATEWAY/DEPENDENCY_SIMULATION report every packaged unit in the same
    // response -- both shapes rendered side by side, neither suppressed.
    expect(screen.getByText("AI_GATEWAY: tasks.T1")).toBeInTheDocument();
    expect(screen.getByText("AI_GATEWAY: other")).toBeInTheDocument();
    expect(screen.getByText("DEPENDENCY_SIMULATION: dependencies.OMC")).toBeInTheDocument();
  });

  it("states the vocabulary explicitly: units are per domain, and a bare name means RETURN_PLATFORM (F5)", async () => {
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });
    await screen.findByText("RETURN_PLATFORM: discovery");
    expect(screen.getByText(/a key with no domain prefix is a/i)).toBeInTheDocument();
  });

  it("adopts the packaged file for an undecided key, confirms first, and shows the success notice", async () => {
    const user = userEvent.setup();
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

    const button = await screen.findByRole("button", { name: "Take packaged file" });
    await user.click(button);

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => { expect(mocks.adoptPackaged).toHaveBeenCalledWith(["discovery"], 41); });
    expect(await screen.findByText(/Adopted discovery from the packaged file/)).toBeInTheDocument();
  });

  it("does not adopt when the confirm dialog is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

    const button = await screen.findByRole("button", { name: "Take packaged file" });
    await user.click(button);

    expect(mocks.adoptPackaged).not.toHaveBeenCalled();
  });

  it("requires config.release.write to see the panel at all", async () => {
    grants = ["config.runtime.read", "config.release.read"];
    render(<OverviewSection canReadReleases />, { wrapper: Wrapper });
    expect(await screen.findByText(/requires config.release.write/)).toBeInTheDocument();
    expect(mocks.packagedDrift).not.toHaveBeenCalled();
  });

  describe("F1 -- the live stack's own shape (six undecided, would_adopt empty, active release present)", () => {
    it("never says there is no active release when the runtime snapshot carries one", async () => {
      mocks.packagedDrift.mockResolvedValue({
        RETURN_PLATFORM: {
          undecided: [
            "agents",
            "clarification_policy",
            "policy_evaluation",
            "return_eligibility_policy",
            "return_policy",
            "support_ingress",
          ],
          would_adopt: [],
          filled_leaves: [],
        },
        AI_GATEWAY: { undecided: [], would_adopt: [], filled_leaves: [] },
        DEPENDENCY_SIMULATION: { undecided: [], would_adopt: [], filled_leaves: [] },
      });
      render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

      expect(await screen.findByText("RETURN_PLATFORM: agents")).toBeInTheDocument();
      // The sentence RV F1 found false on every one of these six rows.
      expect(screen.queryByText(/No active release to compare against yet/)).not.toBeInTheDocument();
      // What carry-forward actually did, and what the button's own
      // consequence is, said once per row -- six times, one per key.
      const kept = screen.getAllByText(/The release's own value is kept for now\./);
      expect(kept).toHaveLength(6);
      const consequence = screen.getAllByText(
        /Taking the packaged file for this whole key replaces every value the release holds for it\./,
      );
      expect(consequence).toHaveLength(6);
    });

    it("names the leaves the packaged file would still fill in, when there are any", async () => {
      mocks.packagedDrift.mockResolvedValue({
        RETURN_PLATFORM: {
          undecided: ["policy_evaluation", "support_ingress"],
          would_adopt: [],
          filled_leaves: ["policy_evaluation.disabled_reason", "support_ingress.queue"],
        },
        AI_GATEWAY: { undecided: [], would_adopt: [], filled_leaves: [] },
        DEPENDENCY_SIMULATION: { undecided: [], would_adopt: [], filled_leaves: [] },
      });
      render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

      await screen.findByText("RETURN_PLATFORM: policy_evaluation");
      expect(
        screen.getByText(
          /The release's own value is kept for now; the packaged file would still fill 1 leaf it leaves absent \(policy_evaluation\.disabled_reason\)\./,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          /The release's own value is kept for now; the packaged file would still fill 1 leaf it leaves absent \(support_ingress\.queue\)\./,
        ),
      ).toBeInTheDocument();
    });
  });

  // RV CFG-4 round 2, G2: F1's fix moved "is there an active release" from
  // `would_adopt` (which cannot answer it) to `headRevision` (which can) --
  // but every fixture up to this point, including F1's own two above, passes
  // a `head_revision`. The one branch the sentence F1 was originally about
  // (`!hasActiveRelease`) had gone untested by the fix that replaced it.
  describe("G2 -- no active release at all (head_revision absent)", () => {
    it("says there is no active release to compare against, and disables the button", async () => {
      mocks.runtime.mockResolvedValue({
        release_id: "unknown",
        head_revision: null,
        configuration: {},
      });
      mocks.packagedDrift.mockResolvedValue({
        RETURN_PLATFORM: { undecided: ["discovery"], would_adopt: [], filled_leaves: [] },
        AI_GATEWAY: { undecided: [], would_adopt: [], filled_leaves: [] },
        DEPENDENCY_SIMULATION: { undecided: [], would_adopt: [], filled_leaves: [] },
      });
      render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

      await screen.findByText("RETURN_PLATFORM: discovery");
      expect(
        screen.getByText(/No active release to compare against yet -- adopting is refused until one exists\./),
      ).toBeInTheDocument();
      const button = screen.getByRole("button", { name: "Take packaged file" });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title", "No head revision to lock against");
    });

    // RV round 1, F1 (BLOCKING): `disabled:opacity-40` on `text-on-surface-variant`
    // composited to 2.04:1 on the white panel background -- reproduced 4/4 by
    // the review, including on a warm server, and was the sweep's one real
    // failure the whole time (not CFG-4's F10 flake, which this drop had
    // wrongly blamed it on). An opacity utility applied to a *disabled*
    // element's own text is exactly the shape of that defect: it fades the
    // foreground colour the sighted-contrast floor was computed against,
    // silently, well below what the token was chosen to clear. Asserted
    // directly on the className rather than by computing contrast in the
    // test, since there is no contrast-checking utility in this repo (the
    // review's own probe used an external axe run) and a className check
    // fails loudly the moment the exact class that caused this returns.
    // RV round 1 (CFG-7), H2: a `className` check that pins only the exact
    // spelling that caused this defect is a proxy for the property it broke,
    // not the property itself -- `disabled:text-outline` (the token CFG-5b's
    // own contrast bug used elsewhere) would sail through a no-opacity-only
    // assertion. Widened to the family: no opacity utility of any kind, AND
    // a real non-text channel (`disabled:bg-*`/`disabled:border-*`) present,
    // so a future edit cannot silently reintroduce the fade by moving it
    // from `opacity` onto `text-primary/40` or a bare `disabled:text-*`.
    it("does not fade the disabled button's text with an opacity utility, and dims it by fill/border instead", async () => {
      mocks.runtime.mockResolvedValue({
        release_id: "unknown",
        head_revision: null,
        configuration: {},
      });
      mocks.packagedDrift.mockResolvedValue({
        RETURN_PLATFORM: { undecided: ["discovery"], would_adopt: [], filled_leaves: [] },
        AI_GATEWAY: { undecided: [], would_adopt: [], filled_leaves: [] },
        DEPENDENCY_SIMULATION: { undecided: [], would_adopt: [], filled_leaves: [] },
      });
      render(<OverviewSection canReadReleases />, { wrapper: Wrapper });

      const button = await screen.findByRole("button", { name: "Take packaged file" });
      expect(button).toBeDisabled();
      expect(button.className).not.toMatch(/(?:^|[\s:])opacity-\d/);
      expect(button.className).not.toMatch(/disabled:text-\S+/);
      expect(button.className).toMatch(/disabled:(?:bg|border)-\S+/);
    });
  });
});
