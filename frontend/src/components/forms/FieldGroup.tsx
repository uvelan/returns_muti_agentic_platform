import { useId, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

/**
 * A named section of a form: a kicker, a title, an optional description, and
 * the fields it frames. `premium-panel` is the same container every other
 * screen uses, so a form built from `FieldGroup`s reads like the rest of the
 * product rather than like a second component system next to it.
 *
 * `collapsible` renders a disclosure button wired the standard way --
 * `aria-expanded` on the button, `aria-controls` naming the region it
 * governs -- rather than hiding content with no way for a screen reader user
 * to know there was more to find.
 */
export function FieldGroup({
  kicker,
  title,
  description,
  children,
  collapsible = false,
  defaultOpen = true,
}: {
  kicker: string;
  title: string;
  description?: string;
  children: ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const contentId = useId();
  const expanded = !collapsible || open;

  return (
    <section className="premium-panel flex flex-col gap-3 p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="premium-kicker">{kicker}</p>
          <h3 className="mt-0.5 text-sm font-semibold text-on-surface">{title}</h3>
          {description !== undefined ? (
            <p className="mt-1 text-xs text-on-surface-variant">{description}</p>
          ) : null}
        </div>
        {collapsible ? (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={contentId}
            onClick={() => { setOpen((value) => !value); }}
            className="flex shrink-0 items-center gap-1 rounded-lg border border-outline-control bg-surface-container-lowest px-2.5 py-1.5 text-[11px] font-medium text-on-surface-variant transition hover:border-primary hover:text-primary"
          >
            {open ? "Collapse" : "Expand"}
            <ChevronDown
              size={13}
              aria-hidden="true"
              className={`transition-transform ${open ? "rotate-180" : ""}`}
            />
          </button>
        ) : null}
      </header>
      <div id={contentId} hidden={!expanded} className="flex flex-col gap-3">
        {children}
      </div>
    </section>
  );
}
