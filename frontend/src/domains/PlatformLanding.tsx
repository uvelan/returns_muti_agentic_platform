import { Link } from "wouter";

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
    <section aria-labelledby="landing-heading" className="mx-auto max-w-5xl">
      <header>
        <h1 id="landing-heading" className="text-3xl font-semibold text-on-surface">
          Returns Platform
        </h1>
        <p className="mt-2 text-sm text-on-surface-variant">
          {principal ? (
            <>
              Signed in as <span className="font-medium text-on-surface">{principal.subject}</span>.
              You have access to {visible.length} of {DOMAINS.length} domains.
            </>
          ) : (
            "Choose a domain to begin."
          )}
        </p>
      </header>

      {visible.length === 0 ? (
        <p className="mt-8 text-sm text-on-surface-variant">
          Your account has not been granted access to any platform domain. Ask an
          administrator to review your roles.
        </p>
      ) : (
        <ul className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((domain) => {
            const Icon = domain.icon;
            const pending = domain.status !== undefined;
            return (
              <li key={domain.path}>
                <Link
                  href={domain.path}
                  className="group flex h-full flex-col rounded-xl border border-outline-variant bg-surface-container-lowest p-5 transition hover:border-primary hover:shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                >
                  <span className="flex items-start justify-between gap-3">
                    <span className="flex size-10 items-center justify-center rounded-lg bg-secondary-container text-primary">
                      <Icon size={20} />
                    </span>
                    {pending ? (
                      <span className="rounded-full bg-tertiary-container px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-on-tertiary-container">
                        {domain.status}
                      </span>
                    ) : null}
                  </span>

                  <span className="mt-4 block text-base font-semibold text-on-surface group-hover:text-primary">
                    {domain.name}
                  </span>
                  <span className="mt-1 block text-sm text-on-surface-variant">
                    {domain.purpose}
                  </span>
                  <span className="mt-3 block text-xs text-outline">{domain.description}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
