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
      <div className="flex items-center gap-2.5">
        <span className="relative inline-flex h-5 w-9 shrink-0">
          <input
            id={id}
            type="checkbox"
            className="peer sr-only"
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
        <label htmlFor={id} className="cursor-pointer text-sm text-on-surface">
          {label}
        </label>
      </div>
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
