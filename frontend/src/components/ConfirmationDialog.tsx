import { useEffect, useRef } from "react";
import { AlertTriangle, X } from "lucide-react";

type ConfirmationDialogProps = {
  isOpen: boolean;
  title: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void;
  onCancel: () => void;
  isDestructive?: boolean;
};

export function ConfirmationDialog({
  isOpen,
  title,
  description,
  confirmText = "Confirm",
  cancelText = "Cancel",
  onConfirm,
  onCancel,
  isDestructive = false
}: ConfirmationDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (isOpen) {
      dialog.showModal();
    } else {
      dialog.close();
    }
  }, [isOpen]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    const handleCancel = (e: Event) => {
      e.preventDefault();
      onCancel();
    };
    dialog.addEventListener("cancel", handleCancel);
    return () => { dialog.removeEventListener("cancel", handleCancel); };
  }, [onCancel]);

  return (
    <dialog
      ref={dialogRef}
      className="backdrop:bg-slate-900/50 rounded-xl p-0 shadow-2xl mx-auto mt-24 max-w-md w-full"
      aria-labelledby="dialog-title"
      aria-describedby="dialog-description"
    >
      <div className="bg-white p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-start space-x-4">
            <div className={`shrink-0 rounded-full p-2 ${isDestructive ? 'bg-red-100 text-red-600' : 'bg-blue-100 text-blue-600'}`}>
              <AlertTriangle className="h-6 w-6" aria-hidden="true" />
            </div>
            <div>
              <h2 id="dialog-title" className="text-lg font-semibold text-slate-900">{title}</h2>
              <p id="dialog-description" className="mt-2 text-sm text-slate-500">{description}</p>
            </div>
          </div>
          <button
            type="button"
            className="rounded-md text-slate-400 hover:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500"
            onClick={() => { onCancel(); }}
          >
            <span className="sr-only">Close</span>
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
            onClick={() => { onCancel(); }}
          >
            {cancelText}
          </button>
          <button
            type="button"
            className={`rounded-md px-3 py-2 text-sm font-semibold text-white shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
              isDestructive
                ? 'bg-red-600 hover:bg-red-500 focus-visible:outline-red-600'
                : 'bg-blue-600 hover:bg-blue-500 focus-visible:outline-blue-600'
            }`}
            onClick={() => { onConfirm(); }}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </dialog>
  );
}
