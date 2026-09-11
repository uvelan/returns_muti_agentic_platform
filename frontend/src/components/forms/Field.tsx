import type { ReactNode } from "react";

/**
 * The layout and accessibility wiring shared by every form primitive: a
 * visible label, an optional hint, an optional error, and the plumbing that
 * ties them to the control by id.
 *
 * `children` may be a plain node, but the useful form is a render function --
 * `Field` computes `id`, `aria-describedby` and `aria-invalid` from `htmlFor`,
 * `hint` and `error`, and hands them to the control so every primitive built
 * on `Field` gets CFG-3b's non-negotiable wiring (hint/error announced,
 * invalid state exposed) without repeating the id bookkeeping in each one.
 * A plain node is still accepted for the rare control that manages its own
 * id (`Toggle`, whose accessible name is the visible label text itself).
 */

export type FieldControlProps = {
  id: string;
  "aria-describedby": string | undefined;
  "aria-invalid": true | undefined;
};

export function Field({
  label,
  hint,
  error,
  required = false,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  htmlFor: string;
  children: ReactNode | ((control: FieldControlProps) => ReactNode);
}) {
  const hintId = hint !== undefined ? `${htmlFor}-hint` : undefined;
  const errorId = error !== undefined ? `${htmlFor}-error` : undefined;
  const describedBy = [hintId, errorId].filter((id): id is string => id !== undefined).join(" ");
  const control: FieldControlProps = {
    id: htmlFor,
    "aria-describedby": describedBy === "" ? undefined : describedBy,
    "aria-invalid": error !== undefined ? true : undefined,
  };

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="premium-kicker flex items-center gap-1">
        {label}
        {required ? (
          <>
            <span aria-hidden="true" className="text-error">*</span>
            <span className="sr-only">required</span>
          </>
        ) : null}
      </label>
      {typeof children === "function" ? children(control) : children}
      {hint !== undefined ? (
        <p id={hintId} className="text-[10px] text-outline">{hint}</p>
      ) : null}
      {error !== undefined ? (
        <p id={errorId} role="alert" className="text-xs text-error">{error}</p>
      ) : null}
    </div>
  );
}
