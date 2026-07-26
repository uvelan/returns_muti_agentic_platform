import { Info } from "lucide-react";

type PartialWarningBannerProps = {
  message?: string;
};

export function PartialWarningBanner({ message = "Some data may be incomplete or missing." }: PartialWarningBannerProps) {
  return (
    <div className="rounded-md bg-amber-50 p-4 mb-6 border border-amber-200">
      <div className="flex">
        <div className="shrink-0">
          <Info className="h-5 w-5 text-amber-400" aria-hidden="true" />
        </div>
        <div className="ml-3">
          <h3 className="text-sm font-medium text-amber-800">Partial Data</h3>
          <div className="mt-2 text-sm text-amber-700">
            <p>{message}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
