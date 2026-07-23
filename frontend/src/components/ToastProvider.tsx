import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import { X } from "lucide-react";

export type Toast = {
  id: string;
  title: string;
  description?: string;
  type?: "success" | "error" | "info";
};

type ToastContextType = {
  toast: (t: Omit<Toast, "id">) => void;
};

const ToastContext = createContext<ToastContextType | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = crypto.randomUUID();
    setToasts((prev) => [...prev, { ...t, id }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((toast) => toast.id !== id));
    }, 5000);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-0 right-0 z-50 p-4 space-y-4 max-w-sm w-full" aria-live="assertive">
        {toasts.map((t) => (
          <div key={t.id} className={`rounded-md p-4 shadow-lg ring-1 ring-black/5 flex items-start justify-between bg-white ${t.type === 'error' ? 'border-l-4 border-red-500' : ''}`}>
            <div>
              <p className="text-sm font-medium text-slate-900">{t.title}</p>
              {t.description && <p className="mt-1 text-sm text-slate-500">{t.description}</p>}
            </div>
            <button
              type="button"
              className="ml-4 inline-flex shrink-0 rounded-md text-slate-400 hover:text-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2"
              onClick={() => { setToasts(prev => prev.filter(x => x.id !== t.id)); }}
            >
              <span className="sr-only">Close</span>
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within ToastProvider");
  return context;
}
