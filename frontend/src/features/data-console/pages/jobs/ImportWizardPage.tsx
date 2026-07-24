import { useMemo, useState, type ChangeEvent } from "react";
import { useLocation } from "wouter";
import { useSubmitImport } from "../../../../api/jobsQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { PageHeader } from "../../../../components/PageHeader";
import { PropertyList } from "../../components/PropertyList";

const MAX_IMPORT_BYTES = 10 * 1024 * 1024;

function parseFieldMapping(value: string): Record<string, string> {
  if (!value.trim()) return {};
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("Field mapping must be a JSON object.");
  }
  return Object.fromEntries(
    Object.entries(parsed).map(([source, target]) => {
      if (typeof target !== "string" || !source.trim() || !target.trim()) {
        throw new Error("Every field mapping key and value must be a non-empty string.");
      }
      return [source, target];
    })
  );
}

export function ImportWizardPage() {
  const [, setLocation] = useLocation();
  const [step, setStep] = useState(1);
  const [target, setTarget] = useState("");
  const [format, setFormat] = useState<"CSV" | "JSON" | "JSONL">("CSV");
  const [duplicatePolicy, setDuplicatePolicy] = useState<"SKIP" | "OVERWRITE" | "FAIL">("SKIP");
  const [fileName, setFileName] = useState("");
  const [content, setContent] = useState("");
  const [mappingText, setMappingText] = useState("{}");
  const [localError, setLocalError] = useState("");
  const submitImport = useSubmitImport();

  const byteSize = useMemo(() => new TextEncoder().encode(content).byteLength, [content]);

  async function selectFile(event: ChangeEvent<HTMLInputElement>) {
    setLocalError("");
    const file = event.target.files?.[0];
    if (!file) {
      setFileName("");
      setContent("");
      return;
    }
    if (file.size > MAX_IMPORT_BYTES) {
      setLocalError("The selected file exceeds the 10MB import limit.");
      event.target.value = "";
      return;
    }
    try {
      const text = await file.text();
      setFileName(file.name);
      setContent(text);
    } catch {
      setLocalError("The selected file could not be read.");
      event.target.value = "";
    }
  }

  function submit() {
    setLocalError("");
    try {
      const fieldMapping = parseFieldMapping(mappingText);
      submitImport.mutate(
        { target: target.trim(), format, duplicatePolicy, fieldMapping, content },
        { onSuccess: (job) => { setLocation(`/data-console/jobs/${job.id}`); } }
      );
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "Invalid field mapping.");
    }
  }

  const canContinue = step !== 1 || (Boolean(target.trim()) && Boolean(content) && byteSize <= MAX_IMPORT_BYTES);

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <PageHeader title="New Data Import" description="Queue a bounded CSV, JSON, or JSONL import into a sandbox workspace." />
      {(localError || submitImport.isError) && (
        <div className="mb-4">
          <ErrorState message={localError || submitImport.error?.message || "Import submission failed."} />
        </div>
      )}
      <div className="bg-white p-6 rounded border border-gray-200">
        <div className="mb-6 border-b border-gray-200 pb-4 flex space-x-4">
          {[1, 2, 3].map((number) => (
            <div key={number} className={`text-sm ${step === number ? "font-bold text-blue-600" : "text-gray-500"}`}>
              {number === 1 ? "1. Target & File" : number === 2 ? "2. Mapping & Policy" : "3. Review & Submit"}
            </div>
          ))}
        </div>

        {step === 1 && (
          <div className="space-y-4">
            <label className="block text-sm font-medium">
              Target workspace
              <input
                type="text"
                className="mt-1 w-full border border-gray-300 rounded px-3 py-2 text-sm"
                placeholder="Workspace name or identifier"
                value={target}
                onChange={(event) => { setTarget(event.target.value); }}
              />
            </label>
            <label className="block text-sm font-medium">
              File format
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
            <label className="block text-sm font-medium">
              Import file
              <input
                type="file"
                accept=".csv,.json,.jsonl,text/csv,application/json,application/x-ndjson"
                className="mt-2 block text-sm"
                onChange={(event) => { void selectFile(event); }}
              />
            </label>
            <p className="text-xs text-gray-500">Maximum 10MB and 10,000 records. The backend validates the actual decoded content.</p>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4">
            <label className="block text-sm font-medium">
              Duplicate policy
              <select
                className="mt-1 w-full border border-gray-300 rounded px-3 py-2 text-sm"
                value={duplicatePolicy}
                onChange={(event) => { setDuplicatePolicy(event.target.value as typeof duplicatePolicy); }}
              >
                <option value="SKIP">Skip duplicates</option>
                <option value="OVERWRITE">Overwrite existing records</option>
                <option value="FAIL">Fail before mutation</option>
              </select>
            </label>
            <label className="block text-sm font-medium">
              Field mapping JSON
              <textarea
                className="mt-1 min-h-32 w-full rounded border border-gray-300 px-3 py-2 font-mono text-sm"
                value={mappingText}
                onChange={(event) => { setMappingText(event.target.value); }}
                spellCheck={false}
              />
            </label>
          </div>
        )}

        {step === 3 && (
          <PropertyList properties={[
            { label: "Target", value: target },
            { label: "File", value: fileName },
            { label: "Format", value: format },
            { label: "Duplicate policy", value: duplicatePolicy },
            { label: "Payload size", value: `${byteSize.toLocaleString()} bytes` },
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
              disabled={!canContinue}
              onClick={() => { setStep((current) => current + 1); }}
              className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
              type="button"
            >
              Next
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={submitImport.isPending || !content || !target.trim()}
              className="px-4 py-2 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
              type="button"
            >
              {submitImport.isPending ? "Queueing..." : "Queue import"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
