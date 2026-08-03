import { useMemo, useState, type ReactNode, type SyntheticEvent } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bot,
  Box,
  CheckCircle2,
  CircleHelp,
  Download,
  FileClock,
  GitCompareArrows,
  Network,
  PackageCheck,
  Play,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Upload,
  Workflow,
  X,
  Plus,
  Trash2,
} from "lucide-react";
import { Link } from "wouter";

import {
  useConfigurationModules,
  useCreateImportDrafts,
  useCreateModuleDraft,
  useCreateRelease,
  useCreateSchemaDesign,
  useImportConfiguration,
  useModuleAction,
  useOrderSync,
  useReleaseAction,
  useReleases,
  useSchemaDesignAction,
  useUpdateModulePayload,
} from "../../api/platformV2";
import type { ConfigurationModule, SchemaDesignContext } from "../../contracts/platformV2";

const card = "rounded-2xl border border-[#bcc9c6] bg-white shadow-[0_1px_3px_rgba(23,29,28,0.05)]";
const primary = "inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-[#00685f] px-4 text-sm font-semibold text-white hover:bg-[#005049] disabled:cursor-not-allowed disabled:opacity-50";
const secondary = "inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-[#bcc9c6] bg-white px-4 text-sm font-semibold text-[#3d4947] hover:border-[#00685f] hover:text-[#00685f] disabled:opacity-50";
const input = "h-10 w-full rounded-lg border border-[#bcc9c6] bg-white px-3 text-sm outline-none focus:border-[#00685f] focus:ring-4 focus:ring-[#00685f]/10";

function PageTitle({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="mb-6 flex flex-col justify-between gap-4 md:flex-row md:items-end"><div><h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1><p className="mt-1.5 max-w-3xl text-sm leading-6 text-[#3d4947]">{description}</p></div>{action}</div>;
}

function Status({ value }: { value: string }) {
  const ok = ["ACTIVE", "APPROVED", "VALIDATED", "COMPLETED", "RELEASED"].includes(value);
  const warning = ["DRAFT", "WAITING_FOR_ANSWER", "NARROWING_REQUIRED"].includes(value);
  return <span className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ${ok ? "bg-emerald-50 text-emerald-800" : warning ? "bg-amber-50 text-amber-800" : "bg-slate-100 text-slate-700"}`}>{value.replaceAll("_", " ")}</span>;
}

function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return <div role="alert" className="mb-5 flex gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"><AlertTriangle size={18} className="shrink-0" />{error instanceof Error ? error.message : "The request could not be completed."}</div>;
}

function Loading() {
  return <div className={`${card} flex min-h-52 items-center justify-center gap-2 text-sm text-[#3d4947]`}><RefreshCw className="animate-spin" size={18} />Loading configuration…</div>;
}

function Metric({ icon: Icon, label, value, detail }: { icon: typeof Activity; label: string; value: string | number; detail: string }) {
  return <article className={`${card} p-5`}><div className="flex items-start justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#6d7a77]">{label}</p><p className="mt-2 text-3xl font-semibold">{value}</p></div><span className="rounded-xl bg-[#e5f5f1] p-2.5 text-[#00685f]"><Icon size={20} /></span></div><p className="mt-3 text-xs text-[#6d7a77]">{detail}</p></article>;
}

export function StudioOverviewPage() {
  const modules = useConfigurationModules();
  const releases = useReleases();
  if (modules.isLoading || releases.isLoading) return <Loading />;
  const moduleList = modules.data ?? [];
  const releaseList = releases.data ?? [];
  const active = releaseList.find(item => item.status === "ACTIVE");
  return <><PageTitle title="Configuration Workspace" description="Manage the runtime graph, independent agent modules, releases, and synchronization from one governed workspace." action={<Link href="/modules" className={primary}><Settings2 size={17} />Manage modules</Link>} /><ErrorNotice error={modules.error ?? releases.error} /><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metric icon={Box} label="Modules" value={new Set(moduleList.map(item => item.moduleId)).size} detail={`${String(moduleList.filter(item => item.status === "DRAFT").length)} drafts need review`} /><Metric icon={PackageCheck} label="Active release" value={active?.releaseId ?? "None"} detail={active ? `${String(active.modules.length)} locked modules` : "Build and activate a release"} /><Metric icon={ShieldCheck} label="Validated" value={moduleList.filter(item => ["VALIDATED", "APPROVED", "RELEASED"].includes(item.status)).length} detail="Immutable versions ready for release" /><Metric icon={Network} label="Graph readiness" value={active ? "Ready" : "Pending"} detail="Schema and sync use the active release" /></div><div className="mt-6 grid gap-5 lg:grid-cols-[1.4fr_1fr]"><section className={`${card} overflow-hidden`}><div className="border-b border-[#bcc9c6] p-5"><h2 className="font-semibold">Configuration activity</h2><p className="mt-1 text-sm text-[#6d7a77]">Latest immutable module versions</p></div><div className="divide-y divide-[#e0e5e3]">{moduleList.slice(0, 6).map(module => <div key={`${module.moduleId}-${module.configurationVersion}`} className="flex items-center justify-between gap-4 p-4"><div className="min-w-0"><p className="truncate text-sm font-semibold">{module.moduleId}</p><p className="mt-1 text-xs text-[#6d7a77]">v{module.configurationVersion} · {module.owner}</p></div><Status value={module.status} /></div>)}</div></section><section className={`${card} p-5`}><h2 className="font-semibold">Quick actions</h2><div className="mt-4 space-y-2">{[["Design graph schema", "/schema-design", Sparkles], ["Run order sync", "/sync", Play], ["Build release", "/releases", PackageCheck], ["Import package", "/import-export", Upload]].map(([label, href, Icon]) => <Link key={String(href)} href={String(href)} className="flex items-center justify-between rounded-xl border border-[#dce3e0] p-3 text-sm font-semibold hover:border-[#00685f] hover:bg-[#f5faf8]"><span className="flex items-center gap-3"><Icon size={18} className="text-[#00685f]" />{String(label)}</span><ArrowRight size={16} /></Link>)}</div></section></div></>;
}

function nextPatchVersion(version: string): string {
  const [major = "1", minor = "0", patch = "0"] = version.split(".");
  return `${major}.${minor}.${String(Number(patch) + 1)}`;
}

function flattenObject(obj: any, prefix = ""): Record<string, string> {
  const result: Record<string, string> = {};
  if (obj === null || obj === undefined) return result;
  for (const [key, value] of Object.entries(obj)) {
    const newPrefix = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      Object.assign(result, flattenObject(value, newPrefix));
    } else {
      result[newPrefix] = Array.isArray(value) ? JSON.stringify(value) : String(value);
    }
  }
  return result;
}

function unflattenObject(flat: Record<string, string>): any {
  const result: any = {};
  for (const [key, value] of Object.entries(flat)) {
    const parts = key.split(".");
    let current = result;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!current[parts[i]]) current[parts[i]] = {};
      current = current[parts[i]];
    }
    const leaf = parts[parts.length - 1];
    let parsedValue: any = value;
    if (value === "true") parsedValue = true;
    else if (value === "false") parsedValue = false;
    else if (!isNaN(Number(value)) && value.trim() !== "") parsedValue = Number(value);
    else if (value.startsWith("[") && value.endsWith("]")) {
      try { parsedValue = JSON.parse(value); } catch {}
    }
    current[leaf] = parsedValue;
  }
  return result;
}

export function ModuleCatalogPage() {
  const query = useConfigurationModules();
  const [search, setSearch] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const draft = useCreateModuleDraft();
  const validate = useModuleAction("validate");
  const submit = useModuleAction("submit");
  const approve = useModuleAction("approve");
  const updatePayload = useUpdateModulePayload();

  // Editor states
  const [editorMode, setEditorMode] = useState<"JSON" | "KV">("KV");
  const [jsonText, setJsonText] = useState("");
  const [kvData, setKvData] = useState<Record<string, string>>({});

  if (query.isLoading) return <Loading />;
  const modules = query.data ?? [];
  const filtered = modules.filter(item => {
    const displayName = item.payload?.name || item.moduleId;
    return `${displayName} ${item.moduleId} ${item.moduleType} ${item.owner}`.toLowerCase().includes(search.toLowerCase());
  });
  const selected = modules.find(item => `${item.moduleId}:${item.configurationVersion}` === selectedKey);
  const error = query.error ?? draft.error ?? validate.error ?? submit.error ?? approve.error ?? updatePayload.error;

  const handleCardClick = (module: ConfigurationModule) => {
    setSelectedKey(`${module.moduleId}:${module.configurationVersion}`);
    setJsonText(JSON.stringify(module.payload, null, 2));
    setKvData(flattenObject(module.payload));
  };

  const closeDialog = () => {
    setSelectedKey("");
  };

  const handleSave = () => {
    if (!selected) return;
    let newPayload;
    if (editorMode === "JSON") {
      try {
        newPayload = JSON.parse(jsonText);
      } catch (err) {
        alert("Invalid JSON");
        return;
      }
    } else {
      newPayload = unflattenObject(kvData);
    }
    updatePayload.mutate({
      moduleId: selected.moduleId,
      version: selected.configurationVersion,
      expectedRevision: selected.revision,
      payload: newPayload,
    }, {
      onSuccess: () => {
        // Just refresh the selected state? Actually invalidation triggers refetch.
        // I can just close or leave it open. Let's leave it open.
        // We'll rely on the refetch to give us the new version.
        // Wait, a draft mutation keeps the version, but increments revision.
        // Our selectedKey is based on version. So it's fine.
      }
    });
  };

  return (
    <>
      <PageTitle title="Module Catalog" description="Each agent and platform capability owns an independent, versioned configuration module." />
      <ErrorNotice error={error} />

      <div className="mb-6">
        <label className="relative block max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[#6d7a77]" size={16} />
          <input aria-label="Search modules" className={`${input} pl-9`} value={search} onChange={event => { setSearch(event.target.value); }} placeholder="Search modules" />
        </label>
      </div>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
        {filtered.map(module => {
          const key = `${module.moduleId}:${module.configurationVersion}`;
          const p = module.payload as Record<string, unknown> | null | undefined;
          const displayName = typeof p?.name === "string" && p.name.trim() !== "" ? p.name : module.moduleId;
          return (
            <button
              type="button"
              key={key}
              onClick={() => handleCardClick(module)}
              className={`${card} flex min-h-[140px] flex-col justify-between p-5 text-left transition-shadow hover:shadow-md`}
            >
              <div>
                <h3 className="font-semibold text-lg text-[#101828] truncate">{displayName}</h3>
                <p className="mt-1 text-sm text-[#6d7a77] truncate">{module.moduleType} · v{module.configurationVersion}</p>
              </div>
              <div className="mt-4 flex items-center justify-between">
                <span className="text-xs text-[#6d7a77]">Rev {module.revision}</span>
                <Status value={module.status} />
              </div>
            </button>
          );
        })}
        {filtered.length === 0 && (
          <div className="col-span-full p-12 text-center text-sm text-[#6d7a77]">No modules match this search.</div>
        )}
      </div>

      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
          <div className={`${card} flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden bg-white shadow-2xl`}>
            {/* Header */}
            <div className="flex items-start justify-between border-b border-[#bcc9c6] bg-[#f5faf8] p-5">
              <div>
                <div className="flex items-center gap-3">
                  <h2 className="text-xl font-semibold">
                    {typeof (selected.payload as Record<string, unknown> | null)?.name === "string" && (selected.payload as Record<string, unknown> | null)?.name
                      ? String((selected.payload as Record<string, unknown> | null)?.name)
                      : selected.moduleId}
                  </h2>
                  <Status value={selected.status} />
                </div>
                <p className="mt-1 text-sm text-[#6d7a77]">
                  {selected.moduleId} · v{selected.configurationVersion} · Rev {selected.revision}
                </p>
              </div>
              <button onClick={closeDialog} className="rounded-lg p-2 text-[#6d7a77] hover:bg-[#e4e9e7]"><X size={20} /></button>
            </div>

            {/* Actions */}
            <div className="flex flex-wrap gap-2 border-b border-[#dce3e0] p-4">
              {selected.status !== "DRAFT" && selected.status !== "QUARANTINED" ? (
                <button className={secondary} disabled={draft.isPending} onClick={() => { draft.mutate({ moduleId: selected.moduleId, fromVersion: selected.configurationVersion, configurationVersion: nextPatchVersion(selected.configurationVersion) }); }}>New draft</button>
              ) : (
                <>
                  <button className={secondary} disabled={validate.isPending} onClick={() => { validate.mutate({ moduleId: selected.moduleId, version: selected.configurationVersion }); }}>Validate</button>
                  <button className={primary} disabled={submit.isPending} onClick={() => { submit.mutate({ moduleId: selected.moduleId, version: selected.configurationVersion }); }}>Submit</button>
                </>
              )}
              {selected.status === "VALIDATED" ? (
                <button className={primary} disabled={approve.isPending} onClick={() => { approve.mutate({ moduleId: selected.moduleId, version: selected.configurationVersion }); }}>Approve</button>
              ) : null}
            </div>

            {/* Editor body */}
            <div className="flex flex-1 flex-col overflow-hidden p-5">
              <div className="mb-4 flex items-center justify-between">
                <h3 className="text-sm font-semibold">Configuration Payload</h3>
                <div className="flex overflow-hidden rounded-lg border border-[#bcc9c6]">
                  <button onClick={() => setEditorMode("KV")} className={`px-3 py-1 text-xs font-medium ${editorMode === "KV" ? "bg-[#00685f] text-white" : "bg-white text-[#3d4947] hover:bg-[#f5faf8]"}`}>Key-Value</button>
                  <button onClick={() => setEditorMode("JSON")} className={`px-3 py-1 text-xs font-medium ${editorMode === "JSON" ? "bg-[#00685f] text-white" : "bg-white text-[#3d4947] hover:bg-[#f5faf8]"}`}>JSON</button>
                </div>
              </div>

              <div className="flex-1 overflow-auto rounded-xl border border-[#bcc9c6] bg-[#fdfdfd]">
                {editorMode === "JSON" ? (
                  <textarea
                    className="h-full w-full resize-none bg-transparent p-4 font-mono text-sm outline-none"
                    value={jsonText}
                    onChange={(e) => setJsonText(e.target.value)}
                    readOnly={selected.status !== "DRAFT" && selected.status !== "QUARANTINED"}
                  />
                ) : (
                  <div className="p-4 space-y-3">
                    {Object.entries(kvData).map(([k, v]) => (
                      <div key={k} className="flex gap-2 items-start">
                        <input
                          className={`${input} flex-1 font-mono text-sm`}
                          value={k}
                          onChange={(e) => {
                            const newKv = { ...kvData };
                            delete newKv[k];
                            newKv[e.target.value] = v;
                            setKvData(newKv);
                          }}
                          readOnly={selected.status !== "DRAFT" && selected.status !== "QUARANTINED"}
                          placeholder="path.to.key"
                        />
                        <input
                          className={`${input} flex-1 font-mono text-sm`}
                          value={v}
                          onChange={(e) => setKvData({ ...kvData, [k]: e.target.value })}
                          readOnly={selected.status !== "DRAFT" && selected.status !== "QUARANTINED"}
                          placeholder="value"
                        />
                        {(selected.status === "DRAFT" || selected.status === "QUARANTINED") && (
                          <button
                            type="button"
                            className="shrink-0 p-2 text-red-500 hover:bg-red-50 rounded-lg"
                            onClick={() => {
                              const newKv = { ...kvData };
                              delete newKv[k];
                              setKvData(newKv);
                            }}
                          >
                            <Trash2 size={16} />
                          </button>
                        )}
                      </div>
                    ))}
                    {(selected.status === "DRAFT" || selected.status === "QUARANTINED") && (
                      <button
                        type="button"
                        className={`${secondary} mt-2 text-xs`}
                        onClick={() => {
                          setKvData({ ...kvData, [`new.key.${Date.now()}`]: "" });
                        }}
                      >
                        <Plus size={14} /> Add Field
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Footer */}
            {(selected.status === "DRAFT" || selected.status === "QUARANTINED") && (
              <div className="flex justify-end border-t border-[#bcc9c6] p-4 bg-[#f5faf8]">
                <button
                  className={primary}
                  onClick={handleSave}
                  disabled={updatePayload.isPending}
                >
                  {updatePayload.isPending ? "Saving..." : "Save Payload"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

export function GraphCatalogPage() {
  const query = useConfigurationModules();
  if (query.isLoading) return <Loading />;
  const graphModules = (query.data ?? []).filter(item => item.moduleType.includes("GRAPH") || item.moduleId.includes("graph"));
  const entities = [
    ["Order", "fullOrderId", "Authoritative order identity and minimal context"],
    ["Order Line", "fullOrderLineId", "Every immutable line under a full order"],
    ["Customer", "customerId", "Customer identity only when required by the flow"],
    ["Shipment", "trackingNumber", "Tracking evidence used for order resolution"],
  ];
  return <><PageTitle title="Graph Schema Catalog" description="Review the minimum operational graph used by agents. Source records stay in their systems of record and sync on demand." action={<Link href="/schema-design" className={primary}><Sparkles size={17} />Redesign schema</Link>} /><ErrorNotice error={query.error} /><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{entities.map(([name, identity, detail]) => <article key={name} className={`${card} p-5`}><span className="flex size-10 items-center justify-center rounded-xl bg-[#e5f5f1] text-[#00685f]"><Network size={20} /></span><h2 className="mt-4 font-semibold">{name}</h2><p className="mt-2 font-mono text-xs text-[#00685f]">{identity}</p><p className="mt-3 text-sm leading-6 text-[#6d7a77]">{detail}</p></article>)}</div><section className={`${card} mt-5 p-5`}><h2 className="font-semibold">Configured schema modules</h2><div className="mt-4 space-y-3">{graphModules.length ? graphModules.map(item => <div key={`${item.moduleId}-${item.configurationVersion}`} className="flex items-center justify-between rounded-xl border border-[#dce3e0] p-4"><div><p className="text-sm font-semibold">{item.moduleId}</p><p className="mt-1 text-xs text-[#6d7a77]">Version {item.configurationVersion} · {item.owner}</p></div><Status value={item.status} /></div>) : <p className="rounded-xl border border-dashed border-[#bcc9c6] p-8 text-center text-sm text-[#6d7a77]">No graph schema module has been configured yet. Start with the Schema Design Agent.</p>}</div></section></>;
}

export function SchemaDesignPage() {
  const modules = useConfigurationModules();
  const create = useCreateSchemaDesign();
  const [context, setContext] = useState<SchemaDesignContext | null>(null);
  const [capability, setCapability] = useState("Discover and synchronize complete orders from strong anchors");
  const [dataset, setDataset] = useState("salesInv");
  const [sourceId, setSourceId] = useState("orders-primary");
  const [identityPath, setIdentityPath] = useState("_id");
  const [answer, setAnswer] = useState("");
  const action = useSchemaDesignAction(context?.requestId ?? "pending");
  const moduleIds = useMemo(() => Array.from(new Set((modules.data ?? []).map(item => item.moduleId))).slice(0, 8), [modules.data]);
  async function start(event: SyntheticEvent) {
    event.preventDefault();
    const value = await create.mutateAsync({ selectedModules: moduleIds, requestedCapabilities: [capability], sourceStructures: [{ sourceId, dataset, fields: [{ path: identityPath, dataType: "string", nullable: false, key: true, sensitive: false }], identityPaths: [identityPath], candidateJoins: [], fingerprint: `${sourceId}:${dataset}` }] });
    setContext(value);
  }
  async function advance() {
    if (!context) return;
    const value = context.currentQuestion && answer.trim()
      ? await action.mutateAsync({ action: "answers", body: { questionId: context.currentQuestion.questionId, value: answer.trim() } })
      : await action.mutateAsync({ action: "next-question" });
    setContext(value);
    setAnswer("");
  }
  return <><PageTitle title="Schema Design Agent" description="Describe the capability and source structure. The independent agent asks only evidence-driven questions needed to produce a configurable graph proposal." /><ErrorNotice error={modules.error ?? create.error ?? action.error} /><div className="grid gap-5 xl:grid-cols-[390px_minmax(0,1fr)]"><form className={`${card} space-y-4 p-5`} onSubmit={event => { void start(event); }}><h2 className="font-semibold">Design context</h2><label className="block text-sm font-semibold">Requested capability<textarea className={`${input} mt-1.5 min-h-24 py-2`} value={capability} onChange={event => { setCapability(event.target.value); }} /></label><label className="block text-sm font-semibold">Source ID<input className={`${input} mt-1.5`} value={sourceId} onChange={event => { setSourceId(event.target.value); }} /></label><label className="block text-sm font-semibold">Dataset or collection<input className={`${input} mt-1.5`} value={dataset} onChange={event => { setDataset(event.target.value); }} /></label><label className="block text-sm font-semibold">Identity field path<input className={`${input} mt-1.5 font-mono`} value={identityPath} onChange={event => { setIdentityPath(event.target.value); }} /></label><div><p className="text-sm font-semibold">Selected modules</p><div className="mt-2 flex flex-wrap gap-2">{moduleIds.map(id => <span key={id} className="rounded-md bg-[#e5f5f1] px-2 py-1 text-xs font-semibold text-[#00685f]">{id}</span>)}</div></div><button className={`${primary} w-full`} disabled={create.isPending || !capability.trim()}><Sparkles size={17} />Analyze schema</button></form><section className={`${card} flex min-h-[620px] flex-col overflow-hidden`}><div className="flex items-center justify-between border-b border-[#bcc9c6] p-4"><div className="flex items-center gap-3"><span className="rounded-xl bg-[#00685f] p-2 text-white"><Bot size={18} /></span><div><h2 className="text-sm font-semibold">Schema Design Agent</h2><p className="text-xs text-[#6d7a77]">Context-only configuration session</p></div></div>{context ? <Status value={context.status} /> : null}</div><div className="flex-1 space-y-4 overflow-y-auto p-5">{!context ? <div className="flex h-full flex-col items-center justify-center text-center"><Network size={38} className="text-[#00685f]" /><h3 className="mt-4 text-lg font-semibold">Ready to inspect configured structure</h3><p className="mt-2 max-w-lg text-sm leading-6 text-[#6d7a77]">The agent will infer identities, relationships, and minimal graph projections. It will ask an associate or administrator only when source evidence is ambiguous.</p></div> : <><div className="rounded-xl bg-[#e5f5f1] p-4 text-sm leading-6"><p className="font-semibold">Analysis started</p><p className="mt-1 text-[#3d4947]">Request {context.requestId} is using context version {String(context.contextVersion)}.</p></div>{context.currentQuestion ? <div className="rounded-xl border border-[#bcc9c6] p-4"><p className="text-xs font-semibold uppercase tracking-wide text-[#00685f]">Question for {context.currentQuestion.requiredOwner}</p><p className="mt-2 font-semibold">{context.currentQuestion.prompt}</p><p className="mt-2 text-sm text-[#6d7a77]">{context.currentQuestion.reason}</p>{context.currentQuestion.evidence.length ? <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-[#3d4947]">{context.currentQuestion.evidence.map(item => <li key={item}>{item}</li>)}</ul> : null}</div> : null}{context.commands.length ? <div><h3 className="mb-3 text-sm font-semibold">Proposed schema commands</h3><div className="space-y-3">{context.commands.map((command, index) => <article key={`${command.moduleId}-${String(index)}`} className="rounded-xl border border-[#dce3e0] p-4"><div className="flex justify-between gap-3"><p className="text-sm font-semibold">{command.operation} · {command.moduleId}</p><span className="text-xs text-[#6d7a77]">{command.changeClassification}</span></div><p className="mt-2 font-mono text-xs text-[#00685f]">{command.path.join(".")}</p><p className="mt-2 text-sm text-[#3d4947]">{command.reason}</p></article>)}</div></div> : null}</>}</div>{context && context.status !== "REVIEW_READY" ? <div className="border-t border-[#bcc9c6] p-4"><div className="flex gap-2"><input className={input} value={answer} onChange={event => { setAnswer(event.target.value); }} placeholder={context.currentQuestion ? "Answer this configuration question" : "Ask the agent to continue"} aria-label="Schema design answer" /><button type="button" className={primary} disabled={action.isPending} onClick={() => { void advance(); }}><Send size={17} />Continue</button></div></div> : null}</section></div></>;
}

export function SyncOperationsPage() {
  const releases = useReleases();
  const sync = useOrderSync();
  const [mode, setMode] = useState<"partial" | "full">("full");
  const [releaseId, setReleaseId] = useState("");
  const [fullOrderId, setFullOrderId] = useState("");
  const [anchorType, setAnchorType] = useState("TRACKING_NUMBER");
  const [anchorValue, setAnchorValue] = useState("");
  const selectedRelease = [releaseId, releases.data?.find(item => item.status === "ACTIVE")?.releaseId, releases.data?.at(0)?.releaseId].find(Boolean) ?? "";
  function submit(event: SyntheticEvent) { event.preventDefault(); sync.mutate({ mode, releaseId: selectedRelease, fullOrderId, anchorType, anchorValue }); }
  return <><PageTitle title="Sync Operations" description="Full sync accepts exactly one validated full order ID and synchronizes all its lines. Partial sync resolves one or more order IDs from a strong anchor before syncing each complete order." /><ErrorNotice error={releases.error ?? sync.error} /><div className="grid gap-5 lg:grid-cols-[420px_minmax(0,1fr)]"><form className={`${card} space-y-5 p-5`} onSubmit={submit}><h2 className="font-semibold">Trigger manual sync</h2><div className="grid grid-cols-2 gap-2"><button type="button" className={mode === "full" ? primary : secondary} onClick={() => { setMode("full"); }}>Full order</button><button type="button" className={mode === "partial" ? primary : secondary} onClick={() => { setMode("partial"); }}>Strong anchor</button></div><label className="block text-sm font-semibold">Release<select className={`${input} mt-1.5`} value={selectedRelease} onChange={event => { setReleaseId(event.target.value); }}>{releases.data?.map(release => <option key={release.releaseId} value={release.releaseId}>{release.releaseId} · {release.status}</option>)}</select></label>{mode === "full" ? <label className="block text-sm font-semibold">Full order ID<input required className={`${input} mt-1.5 font-mono`} value={fullOrderId} onChange={event => { setFullOrderId(event.target.value); }} placeholder="ACCOUNT*ORDERNUMBER" /><span className="mt-1 block text-xs font-normal text-[#6d7a77]">Line IDs are not accepted here; every line is discovered by the order ID.</span></label> : <><label className="block text-sm font-semibold">Strong anchor<select className={`${input} mt-1.5`} value={anchorType} onChange={event => { setAnchorType(event.target.value); }}><option value="ORDER_REFERENCE">Order reference</option><option value="TRACKING_NUMBER">Tracking number</option><option value="INVOICE_NUMBER">Invoice number</option><option value="DELIVERY_TICKET">Delivery ticket</option><option value="CUSTOMER_PO">Customer PO</option></select></label><label className="block text-sm font-semibold">Anchor value<input required className={`${input} mt-1.5`} value={anchorValue} onChange={event => { setAnchorValue(event.target.value); }} /></label></>}<button className={`${primary} w-full`} disabled={sync.isPending || !selectedRelease}><Play size={17} />Run {mode} sync</button></form><section className={`${card} p-5`}><h2 className="font-semibold">Latest result</h2>{sync.data ? <div className="mt-5 space-y-5"><div className="flex items-center justify-between rounded-xl bg-[#f5faf8] p-4"><div><p className="text-xs text-[#6d7a77]">Request</p><p className="mt-1 font-mono text-sm">{sync.data.requestId}</p></div><Status value={sync.data.status} /></div><p className="text-sm leading-6 text-[#3d4947]">{sync.data.message}</p><div className="grid gap-3 sm:grid-cols-3"><Metric icon={Box} label="Orders" value={sync.data.fullOrderIds.length} detail="Resolved full order IDs" /><Metric icon={Search} label="Records read" value={sync.data.recordsRead} detail="Authoritative source records" /><Metric icon={Network} label="Graph writes" value={sync.data.graphWrites} detail="Minimal projections written" /></div>{sync.data.fullOrderIds.length ? <div><h3 className="text-sm font-semibold">Resolved order IDs</h3><div className="mt-2 flex flex-wrap gap-2">{sync.data.fullOrderIds.map(id => <span key={id} className="rounded-md bg-[#e5f5f1] px-2 py-1 font-mono text-xs text-[#00685f]">{id}</span>)}</div></div> : null}</div> : <div className="flex min-h-[420px] flex-col items-center justify-center text-center"><Workflow size={40} className="text-[#00685f]" /><h3 className="mt-4 font-semibold">No sync started</h3><p className="mt-2 max-w-md text-sm leading-6 text-[#6d7a77]">Choose an active release and provide either a full order ID or one strong anchor.</p></div>}</section></div></>;
}

export function ReleasesPage() {
  const modules = useConfigurationModules();
  const releases = useReleases();
  const create = useCreateRelease();
  const resolve = useReleaseAction("resolve");
  const validate = useReleaseAction("validate");
  const transition = useReleaseAction("transition");
  const activate = useReleaseAction("activate");
  const [releaseId, setReleaseId] = useState(`release-${new Date().toISOString().slice(0, 10)}`);
  const latestApproved = useMemo(() => { const map = new Map<string, ConfigurationModule>(); for (const item of modules.data ?? []) if (["APPROVED", "RELEASED"].includes(item.status) && !map.has(item.moduleId)) map.set(item.moduleId, item); return [...map.values()]; }, [modules.data]);
  const error = modules.error ?? releases.error ?? create.error ?? resolve.error ?? validate.error ?? transition.error ?? activate.error;
  function createRelease(event: SyntheticEvent) { event.preventDefault(); create.mutate({ releaseId, modules: latestApproved.map(item => ({ moduleId: item.moduleId, version: item.configurationVersion, checksum: item.checksum })) }); }
  return <><PageTitle title="Release Builder" description="Lock approved module versions into a reproducible configuration release, validate dependencies, and activate atomically." /><ErrorNotice error={error} /><div className="grid gap-5 xl:grid-cols-[1.1fr_1fr]"><section className={`${card} p-5`}><h2 className="font-semibold">Staged modules</h2><p className="mt-1 text-sm text-[#6d7a77]">Latest approved version per independent module</p><div className="mt-4 max-h-[420px] divide-y divide-[#dce3e0] overflow-auto">{latestApproved.map(item => <div key={item.moduleId} className="flex items-center justify-between gap-4 py-3"><div><p className="text-sm font-semibold">{item.moduleId}</p><p className="mt-1 text-xs text-[#6d7a77]">v{item.configurationVersion} · {item.owner}</p></div><CheckCircle2 size={18} className="text-emerald-600" /></div>)}</div><form className="mt-5 flex flex-col gap-2 border-t border-[#bcc9c6] pt-5 sm:flex-row" onSubmit={createRelease}><label className="sr-only" htmlFor="release-id">Release ID</label><input id="release-id" className={input} value={releaseId} onChange={event => { setReleaseId(event.target.value); }} /><button className={primary} disabled={create.isPending || latestApproved.length === 0}><PackageCheck size={17} />Create release</button></form></section><section className={`${card} overflow-hidden`}><div className="border-b border-[#bcc9c6] p-5"><h2 className="font-semibold">Release history</h2></div><div className="max-h-[560px] divide-y divide-[#dce3e0] overflow-auto">{releases.data?.map(release => <article key={release.releaseId} className="p-4"><div className="flex items-start justify-between gap-4"><div><p className="font-semibold">{release.releaseId}</p><p className="mt-1 text-xs text-[#6d7a77]">{release.modules.length} modules · {new Date(release.createdAt).toLocaleString()}</p></div><Status value={release.status} /></div><div className="mt-3 flex flex-wrap gap-2">{release.status === "DRAFT" ? <button className={secondary} onClick={() => { resolve.mutate({ releaseId: release.releaseId }); }}>Resolve dependencies</button> : null}{release.status === "DEPENDENCIES_RESOLVED" ? <button className={secondary} onClick={() => { validate.mutate({ releaseId: release.releaseId }); }}>Validate</button> : null}{release.status === "VALIDATED" ? <button className={primary} onClick={() => { transition.mutate({ releaseId: release.releaseId, status: "APPROVED" }); }}>Approve</button> : null}{release.status === "APPROVED" ? <button className={primary} onClick={() => { transition.mutate({ releaseId: release.releaseId, status: "MIGRATION_READY" }); }}>Prepare migration</button> : null}{release.status === "MIGRATION_READY" ? <button className={primary} onClick={() => { activate.mutate({ releaseId: release.releaseId }); }}>Activate</button> : null}<a className={secondary} href={`/api/v2/configuration/releases/${encodeURIComponent(release.releaseId)}/download`}><Download size={16} />Download</a></div></article>)}</div></section></div></>;
}

export function ImportExportPage() {
  const importer = useImportConfiguration();
  const createDrafts = useCreateImportDrafts();
  const releases = useReleases();
  const [format, setFormat] = useState<"JSON" | "YAML">("YAML");
  const [content, setContent] = useState("");
  async function readFile(file?: File) { if (!file) return; setContent(await file.text()); setFormat(file.name.toLowerCase().endsWith(".json") ? "JSON" : "YAML"); }
  const importRecord = createDrafts.data ?? importer.data;
  return <><PageTitle title="Import / Export Center" description="Quarantine and validate uploaded configuration packages before creating drafts. Export releases as portable, immutable manifests." /><ErrorNotice error={importer.error ?? createDrafts.error ?? releases.error} /><div className="grid gap-5 lg:grid-cols-2"><section className={`${card} p-5`}><h2 className="font-semibold">Upload configuration package</h2><label className="mt-4 flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-[#90a09c] bg-[#f5faf8] text-center"><Upload size={24} className="text-[#00685f]" /><span className="mt-2 text-sm font-semibold">Choose JSON or YAML</span><span className="mt-1 text-xs text-[#6d7a77]">The server validates content in quarantine</span><input className="sr-only" type="file" accept=".json,.yaml,.yml,application/json,text/yaml" onChange={event => { void readFile(event.target.files?.[0]); }} /></label><div className="mt-4 flex gap-2"><button type="button" className={format === "YAML" ? primary : secondary} onClick={() => { setFormat("YAML"); }}>YAML</button><button type="button" className={format === "JSON" ? primary : secondary} onClick={() => { setFormat("JSON"); }}>JSON</button></div><textarea aria-label="Configuration package content" className="mt-3 min-h-48 w-full rounded-xl border border-[#bcc9c6] bg-[#00201d] p-4 font-mono text-xs leading-6 text-[#c7fff6] outline-none focus:border-[#008378]" value={content} onChange={event => { setContent(event.target.value); }} placeholder="Paste configuration package content" /><button className={`${primary} mt-3 w-full`} disabled={importer.isPending || content.trim().length < 2} onClick={() => { importer.mutate({ format, content }); }}>Validate import</button>{importRecord ? <div className="mt-4 rounded-xl bg-[#f5faf8] p-4"><div className="flex justify-between"><p className="text-sm font-semibold">{importRecord.importId}</p><Status value={importRecord.status} /></div><p className="mt-2 text-xs text-[#6d7a77]">{importRecord.modules.length} modules / {importRecord.issues.length} issues</p>{importRecord.status === "VALIDATED" ? <button type="button" className={`${primary} mt-3`} disabled={createDrafts.isPending} onClick={() => { createDrafts.mutate(importRecord.importId); }}>Create module drafts</button> : null}</div> : null}</section><section className={`${card} overflow-hidden`}><div className="border-b border-[#bcc9c6] p-5"><h2 className="font-semibold">Available exports</h2><p className="mt-1 text-sm text-[#6d7a77]">Release manifests include exact checksums and dependency locks.</p></div><div className="divide-y divide-[#dce3e0]">{releases.data?.map(release => <div key={release.releaseId} className="flex items-center justify-between gap-4 p-4"><div><p className="text-sm font-semibold">{release.releaseId}</p><p className="mt-1 text-xs text-[#6d7a77]">{release.modules.length} modules · {release.status}</p></div><a className={secondary} href={`/api/v2/configuration/releases/${encodeURIComponent(release.releaseId)}/download`}><Download size={16} />Export</a></div>)}</div></section></div></>;
}

export function AuditGovernancePage() {
  const modules = useConfigurationModules();
  const releases = useReleases();
  const events = [...(modules.data ?? []).map(item => ({ at: item.createdAt, actor: item.createdBy, action: `${item.status} module`, target: `${item.moduleId} v${item.configurationVersion}` })), ...(releases.data ?? []).map(item => ({ at: item.createdAt, actor: item.createdBy, action: `${item.status} release`, target: item.releaseId }))].sort((a, b) => b.at.localeCompare(a.at));
  return <><PageTitle title="Governance Log" description="A consolidated, read-only view of immutable module and release activity available from the current V2 control-plane APIs." /><ErrorNotice error={modules.error ?? releases.error} /><section className={`${card} overflow-hidden`}><div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-[#f5faf8] text-xs uppercase tracking-wide text-[#6d7a77]"><tr><th className="px-5 py-3">Time</th><th className="px-5 py-3">Actor</th><th className="px-5 py-3">Action</th><th className="px-5 py-3">Target</th></tr></thead><tbody className="divide-y divide-[#dce3e0]">{events.map((event, index) => <tr key={`${event.at}-${String(index)}`}><td className="px-5 py-3 text-[#6d7a77]">{new Date(event.at).toLocaleString()}</td><td className="px-5 py-3 font-semibold">{event.actor}</td><td className="px-5 py-3">{event.action}</td><td className="px-5 py-3 font-mono text-xs">{event.target}</td></tr>)}</tbody></table></div>{events.length === 0 ? <p className="p-12 text-center text-sm text-[#6d7a77]">No configuration activity is available.</p> : null}</section></>;
}

export function AnalyticsPage() {
  const modules = useConfigurationModules();
  const releases = useReleases();
  const list = modules.data ?? [];
  return <><PageTitle title="Platform Analytics" description="Operational configuration health derived from the live module and release catalog." /><ErrorNotice error={modules.error ?? releases.error} /><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metric icon={Box} label="Versioned modules" value={list.length} detail="Across all statuses" /><Metric icon={ShieldCheck} label="Approval rate" value={`${String(list.length ? Math.round(list.filter(item => ["APPROVED", "RELEASED"].includes(item.status)).length / list.length * 100) : 0)}%`} detail="Approved or released versions" /><Metric icon={PackageCheck} label="Releases" value={releases.data?.length ?? 0} detail="Immutable release manifests" /><Metric icon={Activity} label="Active" value={releases.data?.filter(item => item.status === "ACTIVE").length ?? 0} detail="Runtime release" /></div><section className={`${card} mt-5 p-5`}><h2 className="font-semibold">Module status distribution</h2><div className="mt-5 space-y-4">{["DRAFT", "VALIDATED", "APPROVED", "RELEASED"].map(status => { const count = list.filter(item => item.status === status).length; const width = list.length ? `${String(Math.max(2, count / list.length * 100))}%` : "2%"; return <div key={status}><div className="mb-1 flex justify-between text-xs"><span className="font-semibold">{status}</span><span>{count}</span></div><div className="h-3 overflow-hidden rounded bg-[#e4e9e7]"><div className="h-full rounded bg-[#00685f]" style={{ width }} /></div></div>; })}</div></section></>;
}

export function PoliciesPage() {
  return <><PageTitle title="Settings & Global Policies" description="Global governance is intentionally separate from agent-owned modules. This screen reflects the Stitch policy design and remains read-only until a policy API is available." /><div className="mb-5 flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><AlertTriangle size={18} className="shrink-0" />Policy persistence is not exposed by the current V2 backend. Values below document the proposed safe defaults.</div><div className="grid gap-5 lg:grid-cols-2"><section className={`${card} p-5`}><h2 className="font-semibold">Global thresholds</h2><div className="mt-5 space-y-5"><label className="block text-sm font-semibold">Maximum discovery candidates<input className={`${input} mt-1.5`} value="20" readOnly /></label><label className="block text-sm font-semibold">Configuration approval rule<input className={`${input} mt-1.5`} value="Owner approval required" readOnly /></label><label className="block text-sm font-semibold">On-demand sync timeout<input className={`${input} mt-1.5`} value="30 seconds" readOnly /></label></div></section><section className={`${card} p-5`}><h2 className="font-semibold">Security posture</h2><div className="mt-5 space-y-3">{["All source credentials resolved through Vault", "Graph writes require an active release", "Agent configuration is versioned and immutable", "Order sync is constrained by authorization scope"].map(item => <div key={item} className="flex gap-3 rounded-xl bg-[#f5faf8] p-3 text-sm"><ShieldCheck size={18} className="shrink-0 text-[#00685f]" />{item}</div>)}</div></section></div></>;
}

export function HelpPage() {
  const topics = [["Getting started", "Connect a source, inspect its schema, and create agent-owned modules."], ["Order synchronization", "Use a full order ID for complete sync or a strong anchor for partial resolution."], ["Schema Design Agent", "Provide a capability and source structure; answer only questions supported by evidence."], ["Release governance", "Validate and approve modules before locking them into an active release."], ["Security and access", "Credentials remain in Vault and authorization scopes constrain sync candidates."], ["API reference", "V2 endpoints are served under /api/v2 with standard response envelopes."]];
  return <><PageTitle title="Help & Documentation" description="Practical guidance for administrators, associates, sales representatives, and customer care teams." /><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{topics.map(([title, detail]) => <article key={title} className={`${card} p-5`}><CircleHelp size={22} className="text-[#00685f]" /><h2 className="mt-4 font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-[#6d7a77]">{detail}</p></article>)}</div></>;
}

export function RedesignWorkspacePage() {
  return <><PageTitle title="Redesign Workspace" description="Explore proposed graph changes visually, then use the Schema Design Agent to create evidence-backed configuration commands." action={<Link href="/schema-design" className={primary}><Sparkles size={17} />Open design agent</Link>} /><div className="grid min-h-[600px] gap-5 lg:grid-cols-[260px_1fr_300px]"><aside className={`${card} p-4`}><h2 className="text-sm font-semibold">Entity properties</h2><div className="mt-4 space-y-2">{["Order", "Order Line", "Customer", "Shipment"].map((item, index) => <button key={item} className={`w-full rounded-lg p-3 text-left text-sm font-semibold ${index === 0 ? "bg-[#e5f5f1] text-[#00685f]" : "hover:bg-[#f5faf8]"}`}>{item}</button>)}</div></aside><section className={`${card} relative overflow-hidden bg-[#f1f5f3] p-6`}><div className="absolute inset-0 opacity-30" style={{ backgroundImage: "radial-gradient(#6e7977 1px, transparent 1px)", backgroundSize: "20px 20px" }} /><div className="relative flex h-full items-center justify-center"><div className="grid gap-16 md:grid-cols-2">{[["Order", "fullOrderId"], ["Order Line", "fullOrderLineId"], ["Customer", "customerId"], ["Shipment", "trackingNumber"]].map(([name, key]) => <div key={name} className="w-44 rounded-xl border-2 border-[#00685f] bg-white p-4 shadow-sm"><Network size={18} className="text-[#00685f]" /><p className="mt-3 font-semibold">{name}</p><p className="mt-1 font-mono text-xs text-[#6d7a77]">{key}</p></div>)}</div></div></section><aside className={`${card} p-4`}><h2 className="text-sm font-semibold">Design rules</h2><div className="mt-4 space-y-3 text-sm text-[#3d4947]"><p className="rounded-xl bg-[#f5faf8] p-3">Store only data required to complete the return flow.</p><p className="rounded-xl bg-[#f5faf8] p-3">Use fullOrderId as the canonical Order identity.</p><p className="rounded-xl bg-[#f5faf8] p-3">Resolve strong anchors on demand, then sync complete orders.</p><p className="rounded-xl bg-[#f5faf8] p-3">Keep source/table mappings configurable.</p></div></aside></div></>;
}

export function ProposalReviewPage() {
  return <><PageTitle title="Proposal Review" description="Review schema changes as explicit commands with evidence, ownership, and impact before they become module drafts." /><div className={`${card} p-8 text-center`}><GitCompareArrows size={40} className="mx-auto text-[#00685f]" /><h2 className="mt-4 text-lg font-semibold">Proposals are created by the Schema Design Agent</h2><p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-[#6d7a77]">Start a schema design session to generate commands. The backend currently returns proposals within that request context and does not expose a cross-session proposal catalog.</p><Link href="/schema-design" className={`${primary} mt-5`}><Sparkles size={17} />Start schema design</Link></div></>;
}

export function ReleaseComparisonPage() {
  const releases = useReleases();
  const left = releases.data?.at(1);
  const right = releases.data?.at(0);
  return <><PageTitle title="Release Comparison & Approval" description="Compare immutable module locks before approval or activation." /><ErrorNotice error={releases.error} /><div className="grid gap-5 lg:grid-cols-2">{[left, right].map((release, index) => <section key={release?.releaseId ?? index} className={`${card} overflow-hidden`}><div className="border-b border-[#bcc9c6] p-5"><p className="text-xs font-semibold uppercase tracking-wide text-[#6d7a77]">{index === 0 ? "Previous" : "Candidate"}</p><h2 className="mt-1 font-semibold">{release?.releaseId ?? "No release"}</h2></div><div className="divide-y divide-[#dce3e0]">{release?.modules.map(module => <div key={module.moduleId} className="flex justify-between gap-4 p-4 text-sm"><span className="font-semibold">{module.moduleId}</span><span className="font-mono text-xs">{module.version}</span></div>)}</div></section>)}</div></>;
}

export function AgentConfigurationEditorPage() {
  return <ModuleCatalogPage />;
}

export function OperationalPlaceholderPage() {
  return <><PageTitle title="Configuration operation" description="This workspace is available through the connected Configuration Studio routes." /><div className={`${card} p-8 text-center`}><FileClock size={36} className="mx-auto text-[#00685f]" /><p className="mt-3 text-sm text-[#6d7a77]">Choose a module from the navigation to continue.</p></div></>;
}
