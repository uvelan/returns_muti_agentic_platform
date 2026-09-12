import { useId } from "react";

/**
 * An on/off switch with a visible label -- not the icon-only kind, and not
 * one whose accessible name is the word "Yes" or "No" (the defect
 * `AgentsSection.a11y.test.tsx` documents in `DocumentEditor`'s boolean
 * leaf).
 *
 * `reasonField` covers the one shape the platform actually needs: a policy
 * like `policy_evaluation` that must carry a reason when it is switched to
 * the state the model requires one for. The reason input only appears, and
 * is only marked invalid, while that condition holds -- flipping the toggle
 * back out of the required state clears the requirement along with the
 * field.
 */
export function Toggle({
  label,
  hint,
  value,
  onChange,
  reasonField,
}: {
  label: string;
  hint?: string;
  value: boolean;
  onChange: (next: boolean) => void;
  reasonField?: {
    value: string;
    onChange: (next: string) => void;
    requiredWhen: "off" | "on";
  };
}) {
  const id = useId();
  const hintId = hint !== undefined ? `${id}-hint` : undefined;
  const reasonId = `${id}-reason`;
  const reasonRequired =
    reasonField !== undefined
    && ((reasonField.requiredWhen === "off" && !value) || (reasonField.requiredWhen === "on" && value));
  const reasonMissing = reasonRequired && reasonField.value.trim() === "";
  const reasonErrorId = reasonMissing ? `${reasonId}-error` : undefined;

  return (
    <div className="flex flex-col gap-1.5">
      {/*
        CFG-7 (found running the item-1 live acceptance loop against
        /config/agents -- a real Chromium click on this exact control,
        not a jsdom simulation, is what surfaced it): the input used
        `sr-only` (Tailwind's `clip: rect(0,0,0,0)` technique), which
        clips the input's own PAINTED area to nothing at a sub-pixel box
        pulled outside the switch by its `margin: -1px` -- so the browser's
        own hit-test at that box's center resolved to the `relative`
        parent span, not the input, and a direct click (mouse or
        Playwright) on the switch's own visible position never reached
        the checkbox at all; only the browser's native label-forwarding
        (clicking the TEXT, inside the `<label>`) worked. Replaced with an
        input sized and positioned to exactly cover the switch
        (`absolute inset-0`, `opacity-0` rather than clipped), which is
        directly hit-testable at the position the switch is drawn --
        `opacity: 0` hides it visually without removing it from hit-testing
        or the accessibility tree (unlike `display:none`/`visibility:hidden`
        /`sr-only`'s clip trick). The outer `<label>` stays as a second,
        independent way in: clicking the text still forwards natively.
      */}
      <label htmlFor={id} className="flex cursor-pointer items-center gap-2.5">
        <span className="relative inline-flex h-5 w-9 shrink-0">
          <input
            id={id}
            type="checkbox"
            className="peer absolute inset-0 z-10 m-0 size-full cursor-pointer appearance-none opacity-0"
            checked={value}
            onChange={(event) => { onChange(event.target.checked); }}
            aria-describedby={hintId}
          />
          <span
            aria-hidden="true"
            className="absolute inset-0 rounded-full bg-surface-container-highest transition-colors peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary peer-focus-visible:ring-offset-2"
          />
          <span
            aria-hidden="true"
            className="absolute left-0.5 top-0.5 size-4 rounded-full bg-surface-container-lowest shadow-sm transition-transform peer-checked:translate-x-4"
          />
        </span>
        <span className="text-sm text-on-surface">{label}</span>
      </label>
      {hint !== undefined ? <p id={hintId} className="text-[10px] text-outline">{hint}</p> : null}

      {reasonField !== undefined && reasonRequired ? (
        <div className="ml-11 flex flex-col gap-1">
          <label htmlFor={reasonId} className="premium-kicker">Reason</label>
          <input
            id={reasonId}
            type="text"
            required
            value={reasonField.value}
            onChange={(event) => { reasonField.onChange(event.target.value); }}
            aria-invalid={reasonMissing ? true : undefined}
            aria-describedby={reasonErrorId}
            className="premium-field py-1.5 text-xs"
          />
          {reasonMissing ? (
            <p id={reasonErrorId} role="alert" className="text-xs text-error">
              A reason is required while this is {reasonField.requiredWhen}.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
