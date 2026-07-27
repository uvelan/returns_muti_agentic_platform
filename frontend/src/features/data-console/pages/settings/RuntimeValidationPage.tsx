import { type SyntheticEvent, useMemo, useState } from "react";
import { CheckCircle2, Database, KeyRound, ShieldCheck } from "lucide-react";

import {
  type AIValidationPayload,
  type DataSourceValidationPayload,
  type ValidationReceipt,
  useValidateAIConfiguration,
  useValidateDataSource,
  useValidationReceipts,
} from "../../../../api/runtimeValidation";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";

const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-teal-600 focus:ring-2 focus:ring-teal-100";
const labelClass = "block text-xs font-semibold uppercase tracking-wide text-slate-600";
const buttonClass =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50";

function ReceiptCard({ receipt }: { receipt: ValidationReceipt }) {
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            <h3 className="font-semibold text-slate-900">{receipt.subject_key}</h3>
          </div>
          <p className="mt-1 text-xs text-slate-500">{receipt.subject_type}</p>
        </div>
        <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-800">
          {receipt.status}
        </span>
      </div>
      <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-slate-500">Receipt</dt>
          <dd className="break-all font-mono text-slate-700">{receipt.receipt_id}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Vault version</dt>
          <dd className="font-mono text-slate-700">{receipt.secret_version ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Validated</dt>
          <dd className="text-slate-700">{new Date(receipt.verified_at).toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Publication window</dt>
          <dd className="text-slate-700">{new Date(receipt.valid_until).toLocaleString()}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-slate-500">Vault reference</dt>
          <dd className="break-all font-mono text-slate-700">{receipt.target_uri}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-slate-500">Configuration checksum</dt>
          <dd className="break-all font-mono text-slate-700">
            {receipt.configuration_checksum ?? "—"}
          </dd>
        </div>
      </dl>
      <div className="mt-3 flex flex-wrap gap-2">
        {receipt.tests.map((test) => (
          <span key={test} className="rounded bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-700">
            {test}
          </span>
        ))}
      </div>
    </article>
  );
}

export function RuntimeValidationPage() {
  const receipts = useValidationReceipts();
  const aiMutation = useValidateAIConfiguration();
  const sourceMutation = useValidateDataSource();
  const [lastReceipt, setLastReceipt] = useState<ValidationReceipt | null>(null);
  const [ai, setAI] = useState<AIValidationPayload>({
    provider: "GOOGLE",
    modelId: "",
    modelClass: "LIGHTWEIGHT",
    taskKey: "RETURN_PROGRESSIVE_DISAMBIGUATION_V1",
    apiKey: "",
    vaultReference: "vault://secret/production/ai/google/key-01#api_key",
  });
  const [source, setSource] = useState<DataSourceValidationPayload>({
    sourceKey: "",
    sourceType: "MONGODB",
    accessMode: "READ_ONLY",
    uri: "",
    database: "",
    requiredDatasets: [],
    credential: "",
    credentialKind: "DSN",
    vaultReference: "vault://secret/production/data-sources/source-key#dsn",
  });
  const [datasetText, setDatasetText] = useState("");

  const errorMessage = useMemo(() => {
    const error = aiMutation.error ?? sourceMutation.error;
    return error instanceof Error ? error.message : null;
  }, [aiMutation.error, sourceMutation.error]);

  const submitAI = (event: SyntheticEvent) => {
    event.preventDefault();
    setLastReceipt(null);
    aiMutation.mutate(ai, {
      onSuccess: (receipt) => {
        setLastReceipt(receipt);
        setAI((current) => ({ ...current, apiKey: "" }));
      },
    });
  };

  const submitSource = (event: SyntheticEvent) => {
    event.preventDefault();
    setLastReceipt(null);
    sourceMutation.mutate(
      {
        ...source,
        requiredDatasets: datasetText
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      },
      {
        onSuccess: (receipt) => {
          setLastReceipt(receipt);
          setSource((current) => ({ ...current, credential: "" }));
        },
      },
    );
  };

  return (
    <div className="max-w-7xl p-6">
      <PageHeader
        title="Runtime Configuration Validation"
        description="Validate AI credentials, model access, data-source connectivity, and datasets before Vault persistence and graph publication."
      />

      {errorMessage ? <div className="mb-5"><ErrorState message={errorMessage} /></div> : null}
      {lastReceipt ? (
        <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
          Validation passed. Bind receipt <span className="font-mono font-semibold">{lastReceipt.receipt_id}</span>, Vault reference <span className="font-mono font-semibold">{lastReceipt.target_uri}</span>, and checksum <span className="font-mono font-semibold">{lastReceipt.configuration_checksum}</span> to the graph configuration draft before publication.
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-2">
        <form onSubmit={submitAI} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-5 flex items-center gap-3">
            <div className="rounded-xl bg-teal-100 p-2 text-teal-800"><KeyRound className="h-5 w-5" /></div>
            <div><h2 className="font-semibold text-slate-900">AI key and model</h2><p className="text-sm text-slate-500">The key is staged only after model discovery and inference validation pass.</p></div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className={labelClass}>Provider<select className={`${inputClass} mt-1`} value={ai.provider} onChange={(event) => { setAI({ ...ai, provider: event.target.value as AIValidationPayload["provider"] }); }}><option>GOOGLE</option><option>NVIDIA</option><option>OPENAI</option><option>ANTHROPIC</option></select></label>
            <label className={labelClass}>Model class<select className={`${inputClass} mt-1`} value={ai.modelClass} onChange={(event) => { setAI({ ...ai, modelClass: event.target.value as AIValidationPayload["modelClass"] }); }}><option>LIGHTWEIGHT</option><option>STANDARD</option></select></label>
            <label className={`${labelClass} sm:col-span-2`}>Model ID<input className={`${inputClass} mt-1`} required value={ai.modelId} onChange={(event) => { setAI({ ...ai, modelId: event.target.value }); }} /></label>
            <label className={`${labelClass} sm:col-span-2`}>Task key<input className={`${inputClass} mt-1`} required value={ai.taskKey} onChange={(event) => { setAI({ ...ai, taskKey: event.target.value }); }} /></label>
            <label className={`${labelClass} sm:col-span-2`}>Vault reference<input className={`${inputClass} mt-1 font-mono`} required value={ai.vaultReference} onChange={(event) => { setAI({ ...ai, vaultReference: event.target.value }); }} /></label>
            <label className={`${labelClass} sm:col-span-2`}>API key<input type="password" autoComplete="new-password" className={`${inputClass} mt-1`} required value={ai.apiKey} onChange={(event) => { setAI({ ...ai, apiKey: event.target.value }); }} /></label>
          </div>
          <button className={`${buttonClass} mt-5`} disabled={aiMutation.isPending}>{aiMutation.isPending ? "Validating…" : <><ShieldCheck className="h-4 w-4" />Validate and stage</>}</button>
        </form>

        <form onSubmit={submitSource} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-5 flex items-center gap-3">
            <div className="rounded-xl bg-blue-100 p-2 text-blue-800"><Database className="h-5 w-5" /></div>
            <div><h2 className="font-semibold text-slate-900">Data source</h2><p className="text-sm text-slate-500">Connectivity, authentication, and declared datasets must pass before staging.</p></div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className={labelClass}>Source key<input className={`${inputClass} mt-1`} required value={source.sourceKey} onChange={(event) => { setSource({ ...source, sourceKey: event.target.value }); }} /></label>
            <label className={labelClass}>Type<select className={`${inputClass} mt-1`} value={source.sourceType} onChange={(event) => { const sourceType = event.target.value as DataSourceValidationPayload["sourceType"]; setSource({ ...source, sourceType, credentialKind: sourceType === "MONGODB" ? "DSN" : "PASSWORD" }); }}><option>MONGODB</option><option>NEO4J</option><option>SQLSERVER</option></select></label>
            <label className={labelClass}>Access<select className={`${inputClass} mt-1`} value={source.accessMode} onChange={(event) => { setSource({ ...source, accessMode: event.target.value as DataSourceValidationPayload["accessMode"] }); }}><option>READ_ONLY</option><option>READ_WRITE</option></select></label>
            <label className={labelClass}>Database<input className={`${inputClass} mt-1`} required value={source.database} onChange={(event) => { setSource({ ...source, database: event.target.value }); }} /></label>
            {source.sourceType === "NEO4J" ? <label className={`${labelClass} sm:col-span-2`}>URI<input className={`${inputClass} mt-1`} required value={source.uri ?? ""} onChange={(event) => { setSource({ ...source, uri: event.target.value }); }} /></label> : null}
            {source.sourceType !== "MONGODB" ? <><label className={labelClass}>Host{source.sourceType === "NEO4J" ? <span className="text-slate-400"> (from URI)</span> : null}<input className={`${inputClass} mt-1`} required={source.sourceType === "SQLSERVER"} value={source.host ?? ""} onChange={(event) => { setSource({ ...source, host: event.target.value }); }} /></label><label className={labelClass}>Port<input type="number" className={`${inputClass} mt-1`} required={source.sourceType === "SQLSERVER"} value={source.port ?? ""} onChange={(event) => { setSource({ ...source, port: event.target.value ? Number(event.target.value) : undefined }); }} /></label><label className={`${labelClass} sm:col-span-2`}>Username<input className={`${inputClass} mt-1`} required value={source.username ?? ""} onChange={(event) => { setSource({ ...source, username: event.target.value }); }} /></label></> : null}
            <label className={`${labelClass} sm:col-span-2`}>Required datasets or indexes<input className={`${inputClass} mt-1`} placeholder="customers, salesInv" value={datasetText} onChange={(event) => { setDatasetText(event.target.value); }} /></label>
            <label className={`${labelClass} sm:col-span-2`}>Vault reference<input className={`${inputClass} mt-1 font-mono`} required value={source.vaultReference} onChange={(event) => { setSource({ ...source, vaultReference: event.target.value }); }} /></label>
            <label className={`${labelClass} sm:col-span-2`}>{source.credentialKind === "DSN" ? "Credential DSN" : "Password"}<input type="password" autoComplete="new-password" className={`${inputClass} mt-1`} required value={source.credential} onChange={(event) => { setSource({ ...source, credential: event.target.value }); }} /></label>
          </div>
          <button className={`${buttonClass} mt-5`} disabled={sourceMutation.isPending}>{sourceMutation.isPending ? "Validating…" : <><ShieldCheck className="h-4 w-4" />Validate and stage</>}</button>
        </form>
      </div>

      <section className="mt-8">
        <div className="mb-4 flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-slate-600" /><h2 className="text-lg font-semibold text-slate-900">Validation receipts</h2></div>
        {receipts.isLoading ? <LoadingState message="Loading validation receipts…" /> : receipts.isError ? <ErrorState message={receipts.error.message} /> : !receipts.data || receipts.data.length === 0 ? <p className="rounded-xl border border-dashed border-slate-300 p-6 text-sm text-slate-500">No validation receipts exist.</p> : <div className="grid gap-4 lg:grid-cols-2">{receipts.data.map((receipt) => <ReceiptCard key={receipt.receipt_id} receipt={receipt} />)}</div>}
      </section>
    </div>
  );
}
