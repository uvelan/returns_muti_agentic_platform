import { Loader2 } from "lucide-react";

type LoadingStateProps = {
  message?: string;
};

export function LoadingState({ message = "Loading..." }: LoadingStateProps) {
  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center p-8 text-center" role="status">
      <Loader2 className="h-8 w-8 animate-spin text-slate-400" aria-hidden="true" />
      <p className="mt-4 text-sm font-medium text-slate-600">{message}</p>
      <span className="sr-only">Loading</span>
    </div>
  );
}
