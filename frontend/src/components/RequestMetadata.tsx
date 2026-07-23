import { Clock, CheckCircle2 } from "lucide-react";

type RequestMetadataProps = {
  requestId?: string;
  generatedAt?: string;
  freshness?: string;
};

export function RequestMetadata({ requestId, generatedAt, freshness }: RequestMetadataProps) {
  if (!requestId && !generatedAt) return null;

  return (
    <div className="mt-8 border-t border-slate-200 pt-6">
      <h3 className="text-sm font-medium text-slate-900 mb-4">Request Metadata</h3>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
        {requestId && (
          <div className="sm:col-span-1 border border-slate-200 rounded-lg p-4 bg-slate-50">
            <dt className="text-xs font-medium text-slate-500 flex items-center gap-1">
              <CheckCircle2 className="h-3.5 w-3.5" /> Request ID
            </dt>
            <dd className="mt-1 text-xs font-mono text-slate-900 break-all">{requestId}</dd>
          </div>
        )}
        {generatedAt && (
          <div className="sm:col-span-1 border border-slate-200 rounded-lg p-4 bg-slate-50">
            <dt className="text-xs font-medium text-slate-500 flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" /> Generated At
            </dt>
            <dd className="mt-1 text-xs text-slate-900">{new Date(generatedAt).toLocaleString()}</dd>
          </div>
        )}
        {freshness && (
          <div className="sm:col-span-1 border border-slate-200 rounded-lg p-4 bg-slate-50">
            <dt className="text-xs font-medium text-slate-500 flex items-center gap-1">
              <Clock className="h-3.5 w-3.5" /> Freshness
            </dt>
            <dd className="mt-1 text-xs font-mono text-slate-900 capitalize">{freshness}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}
