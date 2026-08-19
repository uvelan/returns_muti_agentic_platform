import { AlertTriangle } from "lucide-react";

export function ConfirmationDialog({
  isOpen,
  title,
  description,
  confirmText,
  isDestructive = false,
  onCancel,
  onConfirm,
}: {
  readonly isOpen: boolean;
  readonly title: string;
  readonly description: string;
  readonly confirmText: string;
  readonly isDestructive?: boolean;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
}) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="analyzer-confirm-title"
        className="w-full max-w-md rounded-2xl border border-emerald-900 bg-[#0a1714] p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-amber-950 text-amber-300">
            <AlertTriangle size={19} />
          </span>
          <div>
            <h2 id="analyzer-confirm-title" className="font-semibold text-white">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-slate-400">{description}</p>
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-lg border border-emerald-900 px-3 py-2 text-sm text-slate-300 hover:bg-white/5">
            Cancel
          </button>
          <button type="button" onClick={onConfirm} className={`rounded-lg px-3 py-2 text-sm font-semibold ${isDestructive ? "bg-red-500 text-white hover:bg-red-400" : "bg-emerald-400 text-emerald-950 hover:bg-emerald-300"}`}>
            {confirmText}
          </button>
        </div>
      </section>
    </div>
  );
}