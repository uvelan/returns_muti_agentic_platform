import { Link } from "wouter";
import { ArrowUpRight, ShieldCheck, Sparkles } from "lucide-react";

import { useCapabilities } from "../hooks/capabilityContext";
import { DOMAINS } from "./registry";

/**
 * The platform front door.
 *
 * Until now `/` redirected straight to `/returns`, which made the copilot look
 * like the whole product and left the other four domains reachable only from
 * the rail. That is fine for someone who already knows the platform and poor
 * for everyone else, so this is a launcher: one card per domain, saying what
 * the domain is *for*.
 *
 * **Cards are filtered by capability, exactly like the rail.** A domain you
 * cannot read is absent rather than shown-and-disabled -- an entry that only
 * ever refuses you is noise. Hiding stays presentation: the backend refuses
 * regardless, and `Forbidden` still exists for anyone who types the URL.
 *
 * **A domain with no backend says so on its face.** The alternative is a card
 * that looks identical to four working ones and dead-ends, which is how a
 * placeholder gets mistaken for a defect.
 */
export function PlatformLanding() {
  const { can, principal } = useCapabilities();
  const visible = DOMAINS.filter((domain) => can(domain.requires));

  return (
    <section aria-labelledby="landing-heading" className="mx-auto max-w-7xl">
      <header className="relative overflow-hidden rounded-3xl bg-rail-surface px-5 py-7 text-rail-on-surface shadow-2xl shadow-primary/10 sm:px-10 sm:py-9">
        <div className="absolute -right-24 -top-28 size-80 rounded-full bg-primary/25 blur-3xl" aria-hidden="true" />
        <div className="absolute bottom-0 right-64 size-44 rounded-full bg-inverse-primary/10 blur-2xl" aria-hidden="true" />
        {/* The second track was a hard `22rem`, which at 320 needs 352px for
            itself and overflowed the viewport by 13px with the padding. It
            stacks below `lg` and keeps its fixed width above. */}
        <div className="relative grid grid-cols-1 items-end gap-8 lg:grid-cols-[minmax(0,1fr)_22rem] lg:gap-12">
          <div>
            <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-inverse-primary">
              <Sparkles size={13} aria-hidden="true" />
              Returns intelligence workspace
            </span>
            <h1 id="landing-heading" className="mt-5 max-w-3xl text-4xl font-semibold tracking-tight text-white">
              Returns Platform
            </h1>
            <p className="mt-3 max-w-2xl text-base leading-7 text-rail-on-surface/70">
              Move from customer intent to resolution with governed AI, connected operations,
              and clear human decision points.
            </p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.06] p-4 backdrop-blur">
            <p className="flex items-center gap-2 text-xs font-semibold text-inverse-primary">
              <ShieldCheck size={15} aria-hidden="true" />
              Secure operator session
            </p>
            <p className="mt-3 truncate text-sm font-medium text-white" title={principal?.subject}>
              {principal?.subject ?? "Authenticated operator"}
            </p>
            <p className="mt-1 text-xs text-rail-on-surface/60">
              {visible.length} of {DOMAINS.length} workspaces available through your capabilities.
            </p>
          </div>
        </div>
      </header>

      <div className="mb-4 mt-9 flex items-end justify-between">
        <div>
          <p className="premium-kicker">Your access</p>
          <h2 className="mt-1 text-xl font-semibold tracking-tight text-on-surface">
            Choose a workspace
          </h2>
        </div>
        <p className="text-xs text-on-surface-variant">
          Each workspace keeps actions capability-gated and auditable.
        </p>
      </div>

      {visible.length === 0 ? (
        <div className="premium-panel flex min-h-48 items-center justify-center px-8 text-sm text-on-surface-variant">
          Your account has not been granted access to any platform domain. Ask an
          administrator to review your roles.
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {visible.map((domain) => {
            const Icon = domain.icon;
            const pending = domain.status !== undefined;
            return (
              <li key={domain.path}>
                <Link
                  href={domain.path}
                  className="group flex h-full min-h-64 flex-col rounded-2xl border border-outline-variant/80 bg-surface-container-lowest p-5 shadow-sm transition duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-panel"
                >
                  <span className="flex items-start justify-between gap-3">
                    <span className="flex size-11 items-center justify-center rounded-xl bg-secondary-container text-primary ring-1 ring-inset ring-primary/10">
                      <Icon size={20} aria-hidden="true" />
                    </span>
                    {pending ? (
                      <span className="rounded-full bg-tertiary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-on-tertiary-container">
                        {domain.status}
                      </span>
                    ) : (
                      <ArrowUpRight size={17} className="text-outline transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary" aria-hidden="true" />
                    )}
                  </span>
                  <span className="mt-5 block text-base font-semibold text-on-surface transition group-hover:text-primary">
                    {domain.name}
                  </span>
                  <span className="mt-2 block text-sm leading-6 text-on-surface-variant">
                    {domain.purpose}
                  </span>
                  <span className="mt-auto border-t border-outline-variant/70 pt-4 text-xs leading-5 text-outline">
                    {domain.description}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
