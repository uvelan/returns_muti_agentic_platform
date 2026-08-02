import { useMemo, useState } from "react";
import { Bot, CheckCircle, GitBranch, Layers3, Save, Settings2, ShieldCheck } from "lucide-react";

import { useActiveSnapshot, useConfigurationReleaseDetail, useConfigurationReleases, useSaveDomainMutation } from "../../../../api/configurationQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";
import { StructuredConfigurationEditor } from "./StructuredConfigurationEditor";
import { fieldLabel } from "./configurationEditorUtils";

type ConfigurationRecord = Record<string, unknown>;
type ModuleView = { id: string; label: string; kind: "AGENT" | "SHARED"; path: string[] };

function isRecord(value: unknown): value is ConfigurationRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function modulesForDomain(domainKey: string, payload: ConfigurationRecord): ModuleView[] {
  const modules: ModuleView[] = [];
  const agents = payload.agents;
  if (domainKey === "RETURN_PLATFORM" && isRecord(agents)) {
    for (const [key, agent] of Object.entries(agents)) {
      const name = isRecord(agent) && typeof agent.name === "string" ? agent.name : fieldLabel(key);
      modules.push({ id: `agent.${key}`, label: name, kind: "AGENT", path: ["agents", key] });
    }
  }
  for (const key of Object.keys(payload)) {
    if (key !== "agents") modules.push({ id: `${domainKey.toLowerCase()}.${key}`, label: fieldLabel(key), kind: "SHARED", path: [key] });
  }
  if (modules.length === 0) modules.push({ id: domainKey.toLowerCase(), label: fieldLabel(domainKey), kind: "SHARED", path: [] });
  return modules;
}

function valueAtPath(value: unknown, path: string[]): unknown {
  let current = value;
  for (const key of path) {
    if (!isRecord(current)) return undefined;
    current = current[key];
  }
  return current;
}

function setValueAtPath(root: ConfigurationRecord, path: string[], value: unknown): ConfigurationRecord {
  if (path.length === 0) return isRecord(value) ? value : root;
  const [head, ...tail] = path;
  const current = isRecord(root[head]) ? root[head] : {};
  return { ...root, [head]: tail.length === 0 ? value : setValueAtPath(current, tail, value) };
}

export function ConfigurationStudioV2Page() {
  const snapshotQuery = useActiveSnapshot();
  const releasesQuery = useConfigurationReleases();
  const [selectedReleaseId, setSelectedReleaseId] = useState<string | null>(null);
  const [selectedDomainKey, setSelectedDomainKey] = useState("RETURN_PLATFORM");
  const [selectedModuleId, setSelectedModuleId] = useState<string | null>(null);
  const [draftPayload, setDraftPayload] = useState<ConfigurationRecord | null>(null);
  const [saveComplete, setSaveComplete] = useState(false);

  const releases = releasesQuery.data ?? [];
  const snapshotReleaseId = releases.some((release) => release.release_id === snapshotQuery.data?.release_id) ? snapshotQuery.data?.release_id ?? null : null;
  const activeReleaseId = selectedReleaseId ?? snapshotReleaseId ?? (releases.length > 0 ? releases[0].release_id : null);
  const detailQuery = useConfigurationReleaseDetail(activeReleaseId);
  const saveMutation = useSaveDomainMutation();
  const detail = detailQuery.data;
  const domainKeys = Object.keys(detail?.domains ?? {}).sort((left, right) => left === "RETURN_PLATFORM" ? -1 : right === "RETURN_PLATFORM" ? 1 : left.localeCompare(right));
  const currentDomainPayload = isRecord(detail?.domains?.[selectedDomainKey]) ? detail.domains[selectedDomainKey] : {};
  const effectivePayload = draftPayload ?? currentDomainPayload;
  const modules = useMemo(() => modulesForDomain(selectedDomainKey, effectivePayload), [effectivePayload, selectedDomainKey]);
  const selectedModule = modules.find((module) => module.id === selectedModuleId) ?? modules[0];
  const selectedValue = valueAtPath(effectivePayload, selectedModule.path);
  const editable = detail?.status === "DRAFT";

  if (snapshotQuery.isLoading || releasesQuery.isLoading) return <LoadingState message="Loading Configuration Studio V2..." />;
  if (snapshotQuery.isError || !snapshotQuery.data) return <ErrorState title="Configuration Studio unavailable" message="The active configuration snapshot could not be loaded." />;

  const resetEditor = () => {
    setDraftPayload(null);
    setSelectedModuleId(null);
    setSaveComplete(false);
  };

  const handleSave = () => {
    if (!activeReleaseId) return;
    saveMutation.mutate({ releaseId: activeReleaseId, domainKey: selectedDomainKey, payload: effectivePayload }, { onSuccess: () => { setSaveComplete(true); } });
  };

  return (
    <div className="max-w-7xl p-6">
      <PageHeader title="Configuration Studio V2" description="Manage agent-owned modules and shared platform configuration with typed, nested controls." />
      <div className="mb-6 grid gap-3 md:grid-cols-3">
        <Summary icon={<Layers3 className="h-5 w-5" />} tone="blue" label="Runtime release" value={snapshotQuery.data.release_id} mono />
        <Summary icon={<ShieldCheck className="h-5 w-5" />} tone="emerald" label="Editor mode" value="Typed and schema-ready" />
        <Summary icon={<Bot className="h-5 w-5" />} tone="violet" label="Agent boundary" value="One owned module per agent" />
      </div>
      <div className="grid gap-6 lg:grid-cols-4">
        <aside className="lg:col-span-1">
          <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-900"><GitBranch className="h-4 w-4 text-gray-500" /> Releases</h2>
            <div className="space-y-2">
              {releases.map((release) => (
                <button key={release.release_id} type="button" onClick={() => { setSelectedReleaseId(release.release_id); resetEditor(); }} className={`w-full rounded-lg border p-3 text-left ${release.release_id === activeReleaseId ? "border-blue-500 bg-blue-50" : "border-gray-200 hover:border-gray-300"}`}>
                  <span className="block font-mono text-xs font-semibold text-gray-900">{release.release_id}</span>
                  <span className="mt-1 block text-[11px] text-gray-500">{release.status}</span>
                </button>
              ))}
            </div>
          </section>
        </aside>
        <main className="space-y-4 lg:col-span-3">
          {detailQuery.isLoading || !detail ? <LoadingState message="Loading release modules..." /> : (
            <>
              <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
                <div className="flex gap-1 overflow-x-auto border-b border-gray-200">
                  {domainKeys.map((key) => (
                    <button key={key} type="button" onClick={() => { setSelectedDomainKey(key); resetEditor(); }} className={`whitespace-nowrap border-b-2 px-4 py-2 text-xs font-semibold ${key === selectedDomainKey ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}>{key}</button>
                  ))}
                </div>
              </section>
              <div className="grid gap-4 xl:grid-cols-[230px_minmax(0,1fr)]">
                <section className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
                  <h2 className="mb-3 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-gray-500"><Settings2 className="h-4 w-4" /> Modules</h2>
                  <div className="space-y-1.5">
                    {modules.map((module) => (
                      <button key={module.id} type="button" onClick={() => { setSelectedModuleId(module.id); setSaveComplete(false); }} className={`w-full rounded-md border px-3 py-2.5 text-left ${selectedModule.id === module.id ? "border-blue-300 bg-blue-50" : "border-transparent hover:bg-gray-50"}`}>
                        <span className="block text-xs font-semibold text-gray-800">{module.label}</span>
                        <span className={`mt-1 inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold ${module.kind === "AGENT" ? "bg-violet-100 text-violet-700" : "bg-gray-100 text-gray-600"}`}>{module.kind}</span>
                      </button>
                    ))}
                  </div>
                </section>
                <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
                  <header className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
                    <div><h2 className="text-base font-semibold text-gray-900">{selectedModule.label}</h2><p className="mt-0.5 font-mono text-[11px] text-gray-500">{selectedModule.id}</p></div>
                    <div className="flex items-center gap-2">
                      {!editable && <span className="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-700">Read only: {detail.status}</span>}
                      {saveComplete && <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700"><CheckCircle className="h-4 w-4" /> Saved</span>}
                      {editable && <button type="button" onClick={handleSave} disabled={saveMutation.isPending} className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"><Save className="h-3.5 w-3.5" /> {saveMutation.isPending ? "Saving..." : "Save module"}</button>}
                    </div>
                  </header>
                  <div className="p-5">
                    <StructuredConfigurationEditor value={selectedValue} disabled={!editable} path={selectedModule.path} onChange={(next) => { setDraftPayload(setValueAtPath(effectivePayload, selectedModule.path, next)); setSaveComplete(false); }} />
                  </div>
                </section>
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function Summary({ icon, tone, label, value, mono = false }: { icon: React.ReactNode; tone: "blue" | "emerald" | "violet"; label: string; value: string; mono?: boolean }) {
  const tones = { blue: "bg-blue-50 text-blue-600", emerald: "bg-emerald-50 text-emerald-600", violet: "bg-violet-50 text-violet-600" };
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"><div className="flex items-center gap-3"><span className={`rounded-lg p-2 ${tones[tone]}`}>{icon}</span><div><p className="text-xs text-gray-500">{label}</p><p className={`${mono ? "font-mono" : ""} text-sm font-semibold text-gray-900`}>{value}</p></div></div></div>
  );
}

