import { useCapabilities } from "../hooks/capabilityContext";
import type { DomainDefinition } from "./registry";

/**
 * A domain's shell-stage landing (Phase 17).
 *
 * Phase 17 delivers navigation and RBAC; Phases 18-21 deliver the screens.
 * This states that plainly rather than rendering a mock workspace, because a
 * convincing-looking screen backed by nothing is indistinguishable from a
 * broken one and invites someone to build on top of it.
 *
 * It does render one real thing: the capabilities you actually hold in this
 * domain, read from `/api/principal`. That makes the RBAC wiring observable
 * end-to-end -- change a role, reload, and the list changes -- which is what
 * this phase is accountable for.
 */
export function DomainLanding({ domain }: { domain: DomainDefinition }) {
  const { principal } = useCapabilities();
  const prefix = `${domain.path.slice(1).replace(/-/g, "_")}.`;
  const held = (principal?.capabilities ?? []).filter((capability) =>
    capability.startsWith(prefix),
  );

  return (
    <section aria-labelledby="domain-heading" className="mx-auto max-w-3xl">
      <h1 id="domain-heading" className="text-2xl font-semibold text-slate-900">
        {domain.name}
      </h1>
      <p className="mt-2 text-sm text-slate-600">{domain.description}</p>

      <div className="mt-6 rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-900">Your access in this domain</h2>
        {held.length > 0 ? (
          <ul className="mt-3 flex flex-col gap-1">
            {held.map((capability) => (
              <li key={capability} className="font-mono text-xs text-slate-700">
                {capability}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-600">
            You hold no capabilities in this domain.
          </p>
        )}
      </div>

      <p className="mt-6 text-sm text-slate-500">
        This domain&apos;s workspace is built in Phase {domain.screenPhase}. The shell,
        routing, and access control are in place; the screen is not.
      </p>
    </section>
  );
}
