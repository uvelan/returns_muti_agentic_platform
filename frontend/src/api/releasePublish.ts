import { configApi } from "./configuration";

/**
 * Publishing one behaviour-domain edit, as one call.
 *
 * The platform has a single release lifecycle -- open a DRAFT, patch one
 * domain, promote to VALIDATED, promote to RELEASED -- and every screen that
 * edits configuration rides it. This used to live inside the AI Control Center,
 * where it was already shared by two editors on that page; the support template
 * editor is the third, and a copy in another domain is how two screens come to
 * publish differently.
 *
 * Nothing here decides anything: each step is a call the backend validates and
 * may refuse, and a refusal stops the run at the step that earned it so the
 * operator can see how far the change got.
 */

export type PublishStep = { name: string; state: "PENDING" | "RUNNING" | "DONE" | "FAILED" };

export async function runPublishPipeline(options: {
  releaseId: string;
  domainKey: string;
  patch: Readonly<Record<string, unknown>>;
  headRevision: number | null;
  onSteps: (steps: readonly PublishStep[]) => void;
}): Promise<void> {
  const plan: PublishStep[] = [
    { name: `Create draft release ${options.releaseId}`, state: "PENDING" },
    { name: `Patch ${options.domainKey} domain`, state: "PENDING" },
    { name: "Promote to VALIDATED", state: "PENDING" },
    { name: "Promote to RELEASED", state: "PENDING" },
  ];
  const mark = (index: number, state: PublishStep["state"]) => {
    plan[index] = { ...plan[index], state };
    options.onSteps([...plan]);
  };
  options.onSteps([...plan]);
  const step = async (index: number, act: () => Promise<unknown>) => {
    mark(index, "RUNNING");
    try {
      await act();
    } catch (caught) {
      mark(index, "FAILED");
      throw caught;
    }
    mark(index, "DONE");
  };
  await step(0, () => configApi.createRelease(options.releaseId));
  await step(1, () => configApi.patchDomain(options.releaseId, options.domainKey, options.patch));
  await step(2, () => configApi.promote(options.releaseId, "VALIDATED"));
  await step(3, () =>
    configApi.promote(options.releaseId, "RELEASED", options.headRevision ?? undefined),
  );
}

/** A release id an operator can recognise later: what changed, and when. */
export function defaultReleaseId(prefix: string): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${prefix}-${String(now.getFullYear())}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}
