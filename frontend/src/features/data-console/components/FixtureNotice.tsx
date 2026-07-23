import { AlertTriangle } from "lucide-react";

export function FixtureNotice() {
  return (
    <div className="bg-yellow-100 border-l-4 border-yellow-500 text-yellow-800 p-4 mb-4 flex items-center" role="alert" aria-live="polite">
      <AlertTriangle className="h-5 w-5 mr-2" />
      <div>
        <span className="font-bold">FIXTURE — NON-DURABLE:</span> This data is powered by development fixtures. Changes are simulated and will not be persisted to the backend.
      </div>
    </div>
  );
}
