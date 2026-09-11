import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { configApi } from "../../api/configuration";
import { mergePatchOf } from "../../api/mergePatch";
import {
  defaultReleaseId,
  runPublishPipeline,
  type PublishStep,
} from "../../api/releasePublish";
import { PublishProgress } from "../../components/PublishProgress";
import { useCapabilities } from "../../hooks/capabilityContext";
import { DocumentEditor, type JsonObject } from "./DocumentEditor";

/**
 * Every business section of the running configuration, editable, grouped by
 * the part of the platform it governs.
 *
 * Until this tab existed the Configuration screen could publish two of the
 * ~26 sections a release carries -- the support template here, AI tasks and
 * providers in the AI Control Center -- and said of the rest "already served,
 * see the Runtime tab", which is a read. Switching policy evaluation on,
 * changing a return method's derivation, adding a shipment status, moving a
 * bay rule: each meant a hand-written PATCH against the release API. The
 * 2026-09-02 decision to enable eligibility evaluation was made exactly that
 * way, from a terminal.
 *
 * **The write path is the one the other editors use.** One `DocumentEditor`
 * over one section at a time -- the form is generated from the document and
 * the backend's own model says what is valid, in its own words -- and one
 * publish pipeline: open a DRAFT, patch the section on its domain, VALIDATED,
 * RELEASED. What this tab adds is the *patch*: a merge patch computed from the
 * loaded section and the edited one (`mergePatchOf`), so a removed entry is
 * sent as `null` and actually leaves the release. Sending the edited section
 * whole, as the first editors did, can only add and change.
 *
 * **Grouping is by ownership, not by file.** The sections are the fields of
 * `ReturnPlatformConfiguration`; the groups are who changes them and why. A
 * section the release does not carry is not offered -- the tab invents no
 * configuration -- and the three sections with a screen of their own say
 * where that screen is rather than offering a second write path to the same
 * field.
 *
 * Typed forms per section are the better end state for the sections whose
 * shape is stable; this is the honest first step, and the same step the
 * support template took.
 */

const RETURN_PLATFORM_DOMAIN_KEY = "RETURN_PLATFORM";
const DEPENDENCY_SIMULATION_DOMAIN_KEY = "DEPENDENCY_SIMULATION";

type Subject = {
  /** The section key on the domain document; the whole document when `null`. */
  key: string | null;
  domainKey: string;
  title: string;
  hint: string;
};

type Group = { id: string; title: string; blurb: string; subjects: readonly Subject[] };

function section(key: string, title: string, hint: string): Subject {
  return { key, domainKey: RETURN_PLATFORM_DOMAIN_KEY, title, hint };
}

/** Derived from the release's own sections -- see the module note. */
const BUSINESS_GROUPS: readonly Group[] = [
  {
    id: "discovery",
    title: "Order discovery",
    blurb: "How an order is found and confirmed from what an associate says.",
    subjects: [
      section("discovery", "Discovery", "Identification fields, aliases and search behaviour."),
      section("source_resolution", "Source resolution", "Which source paths a fact is read from."),
      section("clarification_policy", "Clarification policy", "What the copilot asks for, and when."),
      section("selection_vocabulary", "Selection vocabulary", "The words a selection is described with."),
    ],
  },
  {
    id: "policy",
    title: "Return policy",
    blurb: "What may be returned, how, and what the platform decides on its own.",
    subjects: [
      section("return_policy", "Return policy", "Method derivation, freight rules, requirements."),
      section("return_eligibility_policy", "Eligibility policy", "The rules a return is judged by."),
      section("policy_evaluation", "Policy evaluation", "Whether eligibility is evaluated at all."),
    ],
  },
  {
    id: "workflow",
    title: "Workflow and case timing",
    blurb: "Stages, waits, calendars and housekeeping of a return case.",
    subjects: [
      section("workflow", "Workflow", "Stage sequence and handlers."),
      section("return_case", "Return case", "Timeouts and waits a case is bounded by."),
      section("business_calendars", "Business calendars", "Working days the waits count in."),
      section("housekeeping", "Housekeeping", "What is retired, and when."),
    ],
  },
  {
    id: "support",
    title: "Support",
    blurb: "The handoff to Support and what comes back. The template has its own tab.",
    subjects: [
      section("support", "Support", "Queues, mirrors and outbox topics."),
      section("support_gate", "Support gate", "Which requests wait for a review."),
      section("support_ingress", "Support ingress", "How replies from Support are read."),
      section("support_resolver", "Support resolver", "How a reply is matched to its request."),
      section("context_assembly", "Context assembly", "What a Support request carries."),
    ],
  },
  {
    id: "fulfilment",
    title: "Fulfilment and warehouse",
    blurb: "Shipments, bays and the order management connection.",
    subjects: [
      section("shipment_tracking", "Shipment tracking", "The status ladder and its transitions."),
      section("bay", "Bay placement", "Reservation and capacity rules."),
      section("omc", "Order management", "Cancellation and display rules."),
    ],
  },
  {
    id: "platform",
    title: "Integrations and features",
    blurb: "Topic bindings and the copilot's own settings.",
    subjects: [
      section("integrations", "Integrations", "Outbox topics and what AI may not fabricate."),
      section("copilot", "Copilot", "Which agent the copilot runs and what it shows."),
    ],
  },
  {
    id: "simulation",
    title: "Dependency simulation",
    blurb: "How simulated external systems behave outside production.",
    subjects: [
      {
        key: null,
        domainKey: DEPENDENCY_SIMULATION_DOMAIN_KEY,
        title: "Dependency simulation",
        hint: "The whole simulation document: enabled, banner, AI narration, dependencies.",
      },
    ],
  },
];

/** Sections with a screen of their own; offered as a pointer, not an editor. */
const EDITED_ELSEWHERE: Readonly<Record<string, string>> = {
  agents: "Agents tab -- an edit there is proposed and approved, not published directly.",
  support_template: "Support Template tab, which also previews the draft against a case.",
  runtime_integrations: "AI Control Center -- Providers & Models.",
};

type Snapshot = {
  releaseId: string;
  headRevision: number | null;
  configuration: JsonObject;
  dependencySimulation: JsonObject | null;
};

function asObject(value: unknown): JsonObject | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function snapshotOf(snapshot: Readonly<Record<string, unknown>>): Snapshot {
  const releaseId = snapshot.release_id;
  const head = snapshot.head_revision;
  return {
    releaseId: typeof releaseId === "string" ? releaseId : "unknown",
    headRevision: typeof head === "number" ? head : null,
    configuration: asObject(snapshot.configuration) ?? {},
    dependencySimulation: asObject(snapshot.dependency_simulation_configuration),
  };
}

function subjectId(subject: Subject): string {
  return `${subject.domainKey}:${subject.key ?? "*"}`;
}

function loadedFor(active: Snapshot, subject: Subject): JsonObject | null {
  if (subject.domainKey === DEPENDENCY_SIMULATION_DOMAIN_KEY) return active.dependencySimulation;
  return subject.key === null ? null : asObject(active.configuration[subject.key]);
}

export function BusinessSection() {
  const { can } = useCapabilities();
  const queryClient = useQueryClient();
  const runtime = useQuery({ queryKey: ["config", "runtime"], queryFn: configApi.runtime });
  const [selected, setSelected] = useState<string>(subjectId(BUSINESS_GROUPS[0].subjects[0]));
  const [steps, setSteps] = useState<readonly PublishStep[]>([]);
  // Held outside the editor for the same reason the support template holds
  // it: publishing changes the release id, the editor remounts, and a
  // confirmation rendered inside it would vanish with it.
  const [published, setPublished] = useState<string | null>(null);
  const [, setDirty] = useState(false);

  if (runtime.isPending) return <p className="text-sm text-on-surface-variant">Loading...</p>;
  if (runtime.error !== null) {
    return (
      <p role="alert" className="text-sm text-error">
        {runtime.error.message}
      </p>
    );
  }

  const active = snapshotOf(runtime.data);
  const canPublish = can("config.release.promote");
  const subject =
    BUSINESS_GROUPS.flatMap((group) => group.subjects).find((one) => subjectId(one) === selected)
    ?? BUSINESS_GROUPS[0].subjects[0];
  const loaded = loadedFor(active, subject);
  const elsewhere = Object.entries(EDITED_ELSEWHERE).filter(([key]) => key in active.configuration);

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h2 className="text-base font-semibold text-on-surface">Business configuration</h2>
        <p className="mt-1 max-w-3xl text-sm text-on-surface-variant">
          Every section of release {active.releaseId}, one at a time. A publish opens a draft from
          the active release, patches only the section shown, validates it against the platform's
          own model and releases it; processes adopt it without a restart.
        </p>
      </header>

      {published !== null ? (
        <p role="status" className="rounded-xl border border-primary/20 bg-secondary-container px-4 py-3 text-sm text-on-secondary-container">
          Release {published} is published. Cases opened from now on pin it; cases already running
          keep the configuration they started with.
        </p>
      ) : null}

      <nav aria-label="Configuration sections" className="flex flex-col gap-3">
        {BUSINESS_GROUPS.map((group) => {
          const offered = group.subjects.filter((one) => loadedFor(active, one) !== null);
          if (offered.length === 0) return null;
          return (
            <div key={group.id} className="flex flex-col gap-1">
              <p className="premium-kicker">{group.title}</p>
              <p className="text-xs text-on-surface-variant">{group.blurb}</p>
              <div role="group" aria-label={group.title} className="flex flex-wrap gap-1.5">
                {offered.map((one) => {
                  const id = subjectId(one);
                  const isSelected = id === selected;
                  return (
                    <button
                      key={id}
                      type="button"
                      aria-pressed={isSelected}
                      onClick={() => { setSelected(id); setSteps([]); }}
                      className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${
                        isSelected
                          ? "border-primary bg-primary text-on-primary"
                          : "border-outline-control bg-surface-container-lowest text-on-surface-variant hover:border-primary hover:text-primary"
                      }`}
                    >
                      {one.title}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      {elsewhere.length > 0 ? (
        <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-xs text-on-surface-variant">
          Edited on their own screens:{" "}
          {elsewhere.map(([key, where], index) => (
            <span key={key}>
              {index > 0 ? "; " : ""}
              <code>{key}</code> — {where}
            </span>
          ))}
          .
        </p>
      ) : null}

      {loaded === null ? (
        <p className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-2 text-sm text-on-surface-variant">
          Release {active.releaseId} carries no <code>{subject.key ?? subject.domainKey}</code>{" "}
          section, so there is nothing to edit here.
        </p>
      ) : (
        <DocumentEditor
          key={`${active.releaseId}:${subjectId(subject)}`}
          kicker={subject.title}
          subtitle={`${subject.domainKey}${subject.key === null ? "" : ` · ${subject.key}`} — ${subject.hint}`}
          badges={
            <span className="rounded-full bg-secondary-container px-2 py-0.5 text-on-secondary-container">
              Release {active.releaseId}
            </span>
          }
          loaded={loaded}
          canWrite={canPublish}
          jsonLabel={`${subject.title} JSON`}
          submitLabel="Publish release"
          submittingLabel="Publishing..."
          submitTitle="Publishing a configuration release requires config.release.promote"
          readOnlyNotice="Read-only access. Publishing a configuration release requires config.release.promote."
          notObjectMessage={`${subject.title} must be an object; the platform's model decides which keys it may carry.`}
          confirmSubmit={`Publish ${subject.title} as a new configuration release? Cases opened afterwards pin it.`}
          notice={<PublishProgress steps={steps} />}
          onDirtyChange={setDirty}
          onSubmit={async (document: JsonObject) => {
            const patch = mergePatchOf(loaded, document);
            const changed =
              typeof patch === "object" && patch !== null && !Array.isArray(patch)
                ? Object.keys(patch).length > 0
                : true;
            if (!changed) throw new Error("Nothing changed, so there is nothing to publish.");
            const releaseId = defaultReleaseId(`business-${subject.key ?? "simulation"}`);
            setPublished(null);
            await runPublishPipeline({
              releaseId,
              domainKey: subject.domainKey,
              patch: (subject.key === null ? patch : { [subject.key]: patch }) as Record<
                string,
                unknown
              >,
              headRevision: active.headRevision,
              onSteps: setSteps,
            });
            setPublished(releaseId);
            await queryClient.invalidateQueries({ queryKey: ["config"] });
            return releaseId;
          }}
        />
      )}
    </div>
  );
}
