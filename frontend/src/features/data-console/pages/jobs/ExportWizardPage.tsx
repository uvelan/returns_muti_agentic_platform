import { useMemo, useState } from "react";
import { useLocation } from "wouter";
import { useSubmitExport } from "../../../../api/jobsQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { PageHeader } from "../../../../components/PageHeader";
import { PropertyList } from "../../components/PropertyList";

export function ExportWizardPage() {
  const [, setLocation] = useLocation();
  const [step, setStep] = useState(1);
  const [source, setSource] = useState("");
  const [format, setFormat] = useState<"CSV" | "JSON" | "JSONL">("CSV");
  const [fieldText, setFieldText] = useState("");
  const submitExport = useSubmitExport();
  const fields = useMemo(
    () => Array.from(new Set(fieldText.split(",").map((field) => field.trim()).filter(Boolean))),
    [fieldText]
  );

  function submit() {
    submitExport.mutate(
      { source: source.trim(), format, fields },
      { onSuccess: (job) => { setLocation(`/data-console/jobs/${job.id}`); } }
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <PageHeader title="New Data Export" description="Queue a bounded export and download the materialized server artifact after completion." />
      {submitExport.isError && <div className="mb-4"><ErrorState message={submitExport.error.message} /></div>}
      <div className="bg-white p-6 rounded border border-gray-200">
        <div className="mb-6 border-b border-gray-200 pb-4 flex space-x-4">
          {[1, 2, 3].map((number) => (
            <div key={number} className={`text-sm ${step === number ? "font-bold text-blue-600" : "text-gray-500"}`}>
              {number === 1 ? "1. Source & Format" : number === 2 ? "2. Field Selection" : "3. Review & Submit"}
            </div>
          ))}
        </div>

        {step === 1 && (
          <div className="space-y-4">
            <label className="block text-sm font-medium">
              Source workspace
              <input
                type="text"
                className="mt-1 w-full border border-gray-300 rounded px-3 py-2 text-sm"
                placeholder="Workspace name or identifier"
                value={source}
                onChange={(event) => { setSource(event.target.value); }}
              />
            </label>
            <label className="block text-sm font-medium">
              Export format
              <select
                className="mt-1 w-full border border-gray-300 rounded px-3 py-2 text-sm"
                value={format}
                onChange={(event) => { setFormat(event.target.value as typeof format); }}
              >
                <option value="CSV">CSV</option>
                <option value="JSON">JSON</option>
                <option value="JSONL">JSONL</option>
              </select>
            </label>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4">
            <label className="block text-sm font-medium">
              Fields, comma separated
              <input
                className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm"
                placeholder="Leave empty to export all permitted fields"
                value={fieldText}
                onChange={(event) => { setFieldText(event.target.value); }}
              />
            </label>
            <p className="text-xs text-orange-700">Only isolated workspace records are exportable through this screen.</p>
          </div>
        )}

        {step === 3 && (
          <PropertyList properties={[
            { label: "Source", value: source },
            { label: "Format", value: format },
            { label: "Fields", value: fields.length > 0 ? fields.join(", ") : "All fields" },
            { label: "Artifact limit", value: "10MB / 10,000 records" },
            { label: "Execution", value: "Durable queued worker with cancel/retry" },
          ]} />
        )}

        <div className="mt-8 flex justify-between">
          <button
            disabled={step === 1}
            onClick={() => { setStep((current) => current - 1); }}
            className="px-4 py-2 border border-gray-300 rounded text-sm disabled:opacity-50"
            type="button"
          >
            Back
          </button>
          {step < 3 ? (
            <button
              disabled={step === 1 && !source.trim()}
              onClick={() => { setStep((current) => current + 1); }}
              className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
              type="button"
            >
              Next
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={submitExport.isPending || !source.trim()}
              className="px-4 py-2 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
              type="button"
            >
              {submitExport.isPending ? "Queueing..." : "Queue export"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
