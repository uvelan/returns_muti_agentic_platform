import { useMemo, useState, type ReactNode, type SyntheticEvent } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronRight,
  Code2,
  Database,
  Eye,
  EyeOff,
  FileSearch,
  KeyRound,
  Loader2,
  Menu,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Table2,
  Trash2,
  X,
  Bot,
  Settings2,
  CircleHelp,
  Network,
  PackageCheck,
  Sparkles,
  ArrowLeftRight,
  LineChart,
  FileText,
  LayoutDashboard
} from "lucide-react";
import { Link, Redirect, Route, Router, Switch, useLocation, useParams } from "wouter";

import {
  useConfiguredDataSource,
  useConfiguredDataSources,
  useCreateDataSource,
  useDataPreview,
  useDataSourceSchema,
  useDeleteDataSource,
  useRevealDataSourceCredential,
  useUpdateDataSource,
  useValidateDataSource,
} from "../../api/dataSourceConfig";
import type {
  DataSourceConfiguration,
  DataSourceStatus,
  DataSourceType,
  DataSourceWrite,
} from "../../contracts/dataSourceConfig";
import { env } from "../../env";
import {
  AgentConfigurationEditorPage,
  AnalyticsPage,
  AuditGovernancePage,
  GraphCatalogPage,
  HelpPage,
  ImportExportPage,
  ModuleCatalogPage,
  PoliciesPage,
  ProposalReviewPage,
  RedesignWorkspacePage,
  ReleaseComparisonPage,
  ReleasesPage,
  SchemaDesignPage,
  StudioOverviewPage,
  SyncOperationsPage,
} from "../configuration-v2/StudioPages";

const panel = "rounded-2xl border border-[#bcc9c6] bg-white shadow-[0_1px_3px_rgba(0,0,0,0.05)]";
const input = "mt-1.5 h-10 w-full rounded-lg border border-[#bcc9c6] bg-white px-3 text-sm text-[#171d1c] outline-none transition focus:border-[#00685f] focus:ring-4 focus:ring-[#00685f]/10";
const primary = "inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-[#00685f] px-4 text-sm font-semibold text-white transition hover:bg-[#005049] disabled:cursor-not-allowed disabled:opacity-50";
const secondary = "inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-[#bcc9c6] bg-white px-4 text-sm font-semibold text-[#3d4947] transition hover:border-[#00685f] hover:text-[#00685f] disabled:cursor-not-allowed disabled:opacity-50";

const emptySource: DataSourceWrite = {
  name: "",
  description: "",
  sourceType: "MONGODB",
  accessMode: "READ_ONLY",
  host: null,
  port: null,
  uri: null,
  database: "",
  username: null,
  credentialVaultReference: "vault://secret/production/data-sources/automatic#credential",
  credentialKind: "DSN",
  sslEnabled: true,
};

function sourceWrite(source: DataSourceConfiguration): DataSourceWrite {
  return {
    name: source.name,
    description: source.description,
    sourceType: source.sourceType,
    accessMode: source.accessMode,
    host: source.host,
    port: source.port,
    uri: source.uri,
    database: source.database,
    username: source.username,
    credentialVaultReference: source.credentialVaultReference,
    credentialKind: source.credentialKind,
    sslEnabled: source.sslEnabled,
  };
}

function formatDate(value: string | null): string {
  if (!value) return "Not yet";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function sourceTypeLabel(type: DataSourceType): string {
  return { MONGODB: "MongoDB", SQLSERVER: "SQL Server", NEO4J: "Neo4j" }[type];
}

function statusStyle(status: DataSourceStatus): string {
  if (status === "VALID") return "bg-emerald-50 text-emerald-700 ring-emerald-200";
  if (status === "INVALID") return "bg-red-50 text-red-700 ring-red-200";
  if (status === "VALIDATING") return "bg-blue-50 text-blue-700 ring-blue-200";
  return "bg-stone-100 text-stone-600 ring-stone-200";
}

function StatusPill({ status }: { readonly status: DataSourceStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${statusStyle(status)}`}>
      <span className={`size-1.5 rounded-full ${status === "VALID" ? "bg-emerald-500" : status === "INVALID" ? "bg-red-500" : "bg-stone-400"}`} />
      {status.replace("_", " ").toLowerCase().replace(/^./, value => value.toUpperCase())}
    </span>
  );
}

function DataSourceShell({ children }: { readonly children: ReactNode }) {
  const [location] = useLocation();
  const navigation = [
    ["/", "Overview", LayoutDashboard],
    ["/modules", "Module Config", Bot],
    ["/data-sources", "Data Sources", Database],
    ["/graph-schema", "Graph Schema", Network],
    ["/schema-design", "Schema Design Agent", Sparkles],
    ["/sync", "Order Sync", RefreshCw],
    ["/releases", "Releases", PackageCheck],
    ["/import-export", "Import / Export", ArrowLeftRight],
    ["/governance", "Governance", ShieldCheck],
    ["/analytics", "Analytics", LineChart],
    ["/policies", "Policies", FileText],
    ["/help", "Help", CircleHelp],
  ] as const;
  const currentLabel = navigation.find(([href]) => href === "/" ? location === "/" : location.startsWith(href))?.[1] ?? "Configuration";
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="min-h-screen bg-[#f5faf8] text-[#171d1c] lg:flex">
      <aside className={`border-b border-[#bcc9c6] bg-[#00201d] text-white lg:sticky lg:top-0 lg:h-screen lg:shrink-0 lg:border-b-0 lg:border-r transition-all duration-300 ${collapsed ? "lg:w-[72px]" : "lg:w-64"}`}>
        <div className={`flex h-16 items-center border-b border-white/10 ${collapsed ? "justify-center px-0" : "gap-3 px-5"}`}>
          <span className="flex shrink-0 size-9 items-center justify-center rounded-xl bg-[#008378]">
            <Settings2 size={19} />
          </span>
          {!collapsed && (
            <div className="min-w-0">
              <div className="truncate font-semibold tracking-tight">Returns Configuration</div>
              <div className="truncate text-xs text-[#89f5e7]/70">Connection workspace</div>
            </div>
          )}
        </div>
        <nav className={`flex gap-2 overflow-x-auto p-3 lg:block lg:max-h-[calc(100vh-8rem)] lg:space-y-1 lg:overflow-y-auto ${collapsed ? "lg:p-3" : "lg:p-4"} [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-white/10 [&::-webkit-scrollbar-thumb]:rounded-full hover:[&::-webkit-scrollbar-thumb]:bg-white/20 [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.1)_transparent]`} aria-label="Configuration navigation">
          {navigation.map(([href, label, Icon]) => {
            const active = href === "/" ? location === "/" : location.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`flex shrink-0 items-center rounded-lg px-3 py-2.5 text-sm font-medium ${active ? "bg-[#008378] text-white" : "text-[#d6e0dd] hover:bg-white/10"} ${collapsed ? "justify-center" : "gap-3"}`}
                title={collapsed ? label : undefined}
              >
                <Icon size={18} className="shrink-0" /> {!collapsed && <span className="truncate">{label}</span>}
              </Link>
            );
          })}
        </nav>
        {!collapsed && (
          <div className="hidden border-t border-white/10 p-4 text-xs leading-5 text-[#d6e0dd]/75 lg:absolute lg:inset-x-0 lg:bottom-0 lg:block">
            Credentials are stored in Vault and never displayed after validation.
          </div>
        )}
      </aside>
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-[#bcc9c6] bg-white/95 px-4 backdrop-blur sm:px-6 lg:px-8">
          <div className="flex items-center gap-2 text-sm text-[#6d7a77]">
            <button onClick={() => setCollapsed(c => !c)} className="cursor-pointer text-[#171d1c] hover:text-[#00685f] p-1 -ml-1 rounded-md hover:bg-[#e4e9e7] hidden lg:block" title="Toggle sidebar">
              <Menu size={18} />
            </button>
            <Menu size={18} className="lg:hidden" />
            <span>Configuration</span><ChevronRight size={15} /><strong className="text-[#171d1c]">{currentLabel}</strong>
          </div>
          <a href="/v1/associate/returns" className="text-sm font-semibold text-[#00685f] hover:underline">
            Return to Assistant
          </a>
        </header>
        <main className="mx-auto w-full max-w-[1440px] p-4 sm:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}

function PageTitle({ title, description, actions }: {
  readonly title: string;
  readonly description: string;
  readonly actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-[#171d1c] sm:text-3xl">{title}</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-6 text-[#3d4947]">{description}</p>
      </div>
      {actions ? <div className="flex shrink-0 gap-2">{actions}</div> : null}
    </div>
  );
}

function ErrorBanner({ error }: { readonly error: unknown }) {
  return (
    <div className="mb-5 flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      <AlertTriangle className="mt-0.5 shrink-0" size={18} />
      <span>{error instanceof Error ? error.message : "Something went wrong."}</span>
    </div>
  );
}

function LoadingCard({ label }: { readonly label: string }) {
  return <div className={`${panel} flex min-h-48 items-center justify-center gap-3 text-sm text-[#3d4947]`}><Loader2 className="animate-spin text-[#00685f]" size={20} />{label}</div>;
}

function SourceForm({ source, onClose }: {
  readonly source?: DataSourceConfiguration;
  readonly onClose: () => void;
}) {
  const [form, setForm] = useState<DataSourceWrite>(source ? sourceWrite(source) : emptySource);
  const [credentialVisible, setCredentialVisible] = useState(false);
  const create = useCreateDataSource();
  const update = useUpdateDataSource(source?.id ?? "");
  const reveal = useRevealDataSourceCredential(source?.id ?? "");
  const mutation = source ? update : create;

  function set<K extends keyof DataSourceWrite>(key: K, value: DataSourceWrite[K]) {
    setForm(current => ({ ...current, [key]: value }));
  }

  function changeType(type: DataSourceType) {
    setForm(current => ({
      ...current,
      sourceType: type,
      credentialKind: type === "MONGODB" ? "DSN" : "PASSWORD",
      host: type === "SQLSERVER" ? current.host : null,
      port: type === "SQLSERVER" ? current.port ?? 1433 : null,
      uri: type === "NEO4J" ? current.uri : null,
      username: type === "MONGODB" ? null : current.username,
    }));
  }

  async function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    await mutation.mutateAsync(form);
    onClose();
  }

  async function toggleCredentialVisibility() {
    if (credentialVisible) {
      setCredentialVisible(false);
      return;
    }
    if (source && !form.credential) {
      const savedCredential = await reveal.mutateAsync();
      set("credential", savedCredential);
    }
    setCredentialVisible(true);
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-[#00201d]/35" role="presentation" onMouseDown={onClose}>
      <section className="h-full w-full max-w-xl overflow-y-auto bg-[#f5faf8] shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="source-form-title" onMouseDown={event => { event.stopPropagation(); }}>
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[#bcc9c6] bg-white px-6 py-4">
          <div>
            <h2 id="source-form-title" className="text-xl font-semibold">{source ? "Edit data source" : "Add data source"}</h2>
            <p className="mt-1 text-xs text-[#6d7a77]">Enter the connection details. Credentials are saved securely.</p>
          </div>
          <button type="button" className="rounded-lg p-2 text-[#6d7a77] hover:bg-stone-100" onClick={onClose} aria-label="Close"><X size={20} /></button>
        </div>
        <form className="space-y-5 p-6" onSubmit={event => { void submit(event); }}>
          {mutation.error || reveal.error ? <ErrorBanner error={mutation.error ?? reveal.error} /> : null}
          <div>
            <label className="text-sm font-semibold">Source type</label>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {(["MONGODB", "SQLSERVER", "NEO4J"] as const).map(type => (
                <button key={type} type="button" onClick={() => { changeType(type); }} className={`rounded-xl border p-3 text-sm font-semibold transition ${form.sourceType === type ? "border-[#00685f] bg-[#e5f5f1] text-[#00685f]" : "border-[#bcc9c6] bg-white text-[#3d4947]"}`}>
                  {sourceTypeLabel(type)}
                </button>
              ))}
            </div>
          </div>
          <label className="block text-sm font-semibold">Display name<input required className={input} value={form.name} onChange={event => { set("name", event.target.value); }} placeholder="Inventory database" /></label>
          <label className="block text-sm font-semibold">Description<textarea className={`${input} min-h-20 py-2`} value={form.description} onChange={event => { set("description", event.target.value); }} placeholder="What this connection is used for" /></label>
          {form.sourceType === "SQLSERVER" ? (
            <div className="grid gap-4 sm:grid-cols-[1fr_120px]">
              <label className="block text-sm font-semibold">Host<input required className={input} value={form.host ?? ""} onChange={event => { set("host", event.target.value); }} placeholder="db.internal.example" /></label>
              <label className="block text-sm font-semibold">Port<input required type="number" className={input} value={form.port ?? 1433} onChange={event => { set("port", Number(event.target.value)); }} /></label>
            </div>
          ) : null}
          {form.sourceType === "NEO4J" ? <label className="block text-sm font-semibold">URI<input required className={input} value={form.uri ?? ""} onChange={event => { set("uri", event.target.value); }} placeholder="neo4j://graph.internal:7687" /></label> : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-semibold">Database<input required className={input} value={form.database} onChange={event => { set("database", event.target.value); }} /></label>
            {form.sourceType !== "MONGODB" ? <label className="block text-sm font-semibold">Username<input required className={input} value={form.username ?? ""} onChange={event => { set("username", event.target.value); }} /></label> : null}
          </div>
          <label className="block text-sm font-semibold">
            {form.sourceType === "MONGODB" ? "Connection string" : "Password"}
            <span className="relative block">
            <input
              required={!source}
              type={credentialVisible ? "text" : "password"}
              autoComplete="new-password"
              className={input + " pr-11 font-mono text-xs"}
              value={form.credential ?? ""}
              onChange={event => { set("credential", event.target.value); }}
              placeholder={source
                ? "Leave blank to keep the saved " + (form.sourceType === "MONGODB" ? "connection string" : "password")
                : form.sourceType === "MONGODB"
                  ? "mongodb://username:password@host:27017/database"
                  : "Enter password"}
            />
              {env.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED ? <button
                type="button"
                className="absolute right-1.5 top-[0.6rem] flex size-9 items-center justify-center rounded-md text-[#6d7a77] transition hover:bg-[#f0f5f2] hover:text-[#00685f] focus:outline-none focus:ring-2 focus:ring-[#00685f]/30"
                onClick={() => { void toggleCredentialVisibility(); }}
                disabled={reveal.isPending}
                aria-label={credentialVisible ? "Hide credential" : "Show credential"}
                title={credentialVisible ? "Hide credential" : "Show credential"}
              >
                {reveal.isPending ? <Loader2 className="animate-spin" size={18} /> : credentialVisible ? <EyeOff size={18} /> : <Eye size={18} />}
              </button> : null}
            </span>
            <span className="mt-1.5 block text-xs font-normal text-[#6d7a77]">
              {env.VITE_DATA_SOURCE_CREDENTIAL_REVEAL_ENABLED
                ? <>Saved securely by the backend. Use the eye icon to {credentialVisible ? "hide" : "show"} it.</>
                : "Saved securely by the backend."}
            </span>
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-semibold">Access<select className={input} value={form.accessMode} onChange={event => { set("accessMode", event.target.value as DataSourceWrite["accessMode"]); }}><option value="READ_ONLY">Read only</option><option value="READ_WRITE">Read and write</option></select></label>
            <label className="flex items-center gap-3 self-end rounded-lg border border-[#bcc9c6] bg-white px-3 py-2.5 text-sm font-semibold"><input type="checkbox" checked={form.sslEnabled} onChange={event => { set("sslEnabled", event.target.checked); }} className="size-4 accent-[#00685f]" />Use SSL/TLS</label>
          </div>
          <div className="flex justify-end gap-3 border-t border-[#bcc9c6] pt-5"><button type="button" className={secondary} onClick={onClose}>Cancel</button><button className={primary} disabled={mutation.isPending}>{mutation.isPending ? <Loader2 className="animate-spin" size={17} /> : <Check size={17} />}{source ? "Save changes" : "Add source"}</button></div>
        </form>
      </section>
    </div>
  );
}

export function DataSourcesPage() {
  const [, navigate] = useLocation();
  const query = useConfiguredDataSources();
  const remove = useDeleteDataSource();
  const [search, setSearch] = useState("");
  const [type, setType] = useState("ALL");
  const [status, setStatus] = useState("ALL");
  const [editor, setEditor] = useState<DataSourceConfiguration | "new" | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const sources = useMemo(() => (query.data ?? []).filter(source => {
    const term = search.trim().toLowerCase();
    return (!term || source.name.toLowerCase().includes(term) || source.database.toLowerCase().includes(term))
      && (type === "ALL" || source.sourceType === type)
      && (status === "ALL" || source.status === status);
  }), [query.data, search, type, status]);

  return (
    <>
      <PageTitle title="Data Sources" description="Create and manage the connections used by Returns Assistant." actions={<button className={primary} onClick={() => { setEditor("new"); }}><Plus size={17} />Add data source</button>} />
      {query.error ? <ErrorBanner error={query.error} /> : null}
      <section className={panel}>
        <div className="flex flex-col gap-3 border-b border-[#bcc9c6] p-4 sm:flex-row sm:items-center">
          <label className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[#6d7a77]" size={17} /><input className="h-10 w-full rounded-lg border border-[#bcc9c6] bg-[#f5faf8] pl-10 pr-3 text-sm outline-none focus:border-[#00685f]" value={search} onChange={event => { setSearch(event.target.value); }} placeholder="Search data sources" /></label>
          <select className="h-10 rounded-lg border border-[#bcc9c6] bg-white px-3 text-sm" value={type} onChange={event => { setType(event.target.value); }}><option value="ALL">All types</option><option value="MONGODB">MongoDB</option><option value="SQLSERVER">SQL Server</option><option value="NEO4J">Neo4j</option></select>
          <select className="h-10 rounded-lg border border-[#bcc9c6] bg-white px-3 text-sm" value={status} onChange={event => { setStatus(event.target.value); }}><option value="ALL">All statuses</option><option value="VALID">Valid</option><option value="INVALID">Invalid</option><option value="NOT_VALIDATED">Not validated</option></select>
          <button className="rounded-lg border border-[#bcc9c6] p-2.5 text-[#3d4947] hover:text-[#00685f]" onClick={() => { void query.refetch(); }} aria-label="Refresh"><RefreshCw size={17} /></button>
        </div>
        {query.isLoading ? <div className="p-5"><LoadingCard label="Loading data sources…" /></div> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-sm">
              <thead className="bg-[#f0f5f2] text-xs uppercase tracking-[0.05em] text-[#6d7a77]"><tr>{["Source", "Type", "Connection", "Database", "Status", "Last validated", ""].map(label => <th key={label} className="px-4 py-3 font-semibold">{label}</th>)}</tr></thead>
              <tbody className="divide-y divide-[#d6dbd9]">
                {sources.map(source => (
                  <tr key={source.id} className="transition hover:bg-[#f5faf8]">
                    <td className="px-4 py-3"><button className="text-left" onClick={() => { navigate(`/data-sources/${source.id}/validate`); }}><span className="block font-semibold text-[#171d1c]">{source.name}</span><span className="text-xs text-[#6d7a77]">{source.description || source.id}</span></button></td>
                    <td className="px-4 py-3"><span className="rounded-md bg-[#e4e9e7] px-2 py-1 text-xs font-semibold text-[#3d4947]">{sourceTypeLabel(source.sourceType)}</span></td>
                    <td className="max-w-56 truncate px-4 py-3 font-mono text-xs text-[#3d4947]">{source.host ?? source.uri ?? "Saved connection string"}{source.port ? `:${String(source.port)}` : ""}</td>
                    <td className="px-4 py-3 text-[#3d4947]">{source.database}</td>
                    <td className="px-4 py-3"><StatusPill status={source.status} /></td>
                    <td className="px-4 py-3 text-xs text-[#6d7a77]">{formatDate(source.lastValidatedAt)}</td>
                    <td className="relative px-4 py-3 text-right"><button className="rounded-lg p-2 text-[#6d7a77] hover:bg-[#e4e9e7]" aria-label={`Actions for ${source.name}`} onClick={() => { setMenu(menu === source.id ? null : source.id); }}><MoreHorizontal size={18} /></button>{menu === source.id ? <div className="absolute right-4 top-12 z-10 w-44 rounded-xl border border-[#bcc9c6] bg-white p-1.5 text-left shadow-xl"><button className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-[#f0f5f2]" onClick={() => { navigate(`/data-sources/${source.id}/validate`); }}><ShieldCheck size={16} />Validate</button><button className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-[#f0f5f2]" onClick={() => { navigate(`/data-sources/${source.id}/schema`); }}><FileSearch size={16} />Explore schema</button><><button className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-[#f0f5f2]" onClick={() => { setEditor(source); setMenu(null); }}><Pencil size={16} />Edit</button><button className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-700 hover:bg-red-50" onClick={() => { if (window.confirm(`Delete ${source.name}?`)) void remove.mutateAsync(source.id); }}><Trash2 size={16} />Delete</button></></div> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {sources.length === 0 ? <div className="p-12 text-center text-sm text-[#6d7a77]">No data sources match these filters.</div> : null}
          </div>
        )}
      </section>
      {editor ? <SourceForm source={editor === "new" ? undefined : editor} onClose={() => { setEditor(null); }} /> : null}
    </>
  );
}

function SourceBreadcrumb({ source, current }: { readonly source: DataSourceConfiguration; readonly current: string }) {
  return <nav className="mb-5 flex items-center gap-2 text-sm text-[#6d7a77]"><Link href="/data-sources" className="hover:text-[#00685f]">Data Sources</Link><ChevronRight size={15} /><span>{source.name}</span><ChevronRight size={15} /><strong className="text-[#171d1c]">{current}</strong></nav>;
}

function SourceSummary({ source }: { readonly source: DataSourceConfiguration }) {
  return <section className={`${panel} p-5`}><div className="flex flex-col justify-between gap-4 sm:flex-row"><div><div className="flex items-center gap-3"><span className="flex size-10 items-center justify-center rounded-xl bg-[#e5f5f1] text-[#00685f]"><Database size={20} /></span><div><h2 className="text-lg font-semibold">{source.name}</h2><p className="text-sm text-[#6d7a77]">{sourceTypeLabel(source.sourceType)} connection</p></div></div></div><StatusPill status={source.status} /></div><dl className="mt-5 grid grid-cols-2 gap-4 border-t border-[#d6dbd9] pt-4 text-sm md:grid-cols-4"><div><dt className="text-xs font-semibold uppercase tracking-wide text-[#6d7a77]">Connection</dt><dd className="mt-1 truncate">{source.host ?? source.uri ?? "Saved connection string"}</dd></div><div><dt className="text-xs font-semibold uppercase tracking-wide text-[#6d7a77]">Database</dt><dd className="mt-1">{source.database}</dd></div><div><dt className="text-xs font-semibold uppercase tracking-wide text-[#6d7a77]">Access</dt><dd className="mt-1">{source.accessMode === "READ_ONLY" ? "Read only" : "Read and write"}</dd></div><div><dt className="text-xs font-semibold uppercase tracking-wide text-[#6d7a77]">TLS</dt><dd className="mt-1">{source.sslEnabled ? "Enabled" : "Not configured"}</dd></div></dl></section>;
}

export function ValidateConnectionPage() {
  const params = useParams<{ sourceId: string }>();
  const [, navigate] = useLocation();
  const sourceId = params.sourceId;
  const query = useConfiguredDataSource(sourceId);
  const validate = useValidateDataSource(sourceId);
  if (query.isLoading) return <LoadingCard label="Loading connection…" />;
  if (query.error || !query.data) return <ErrorBanner error={query.error ?? new Error("Data source not found.")} />;
  const source = query.data;
  return <><SourceBreadcrumb source={source} current="Validate connection" /><PageTitle title="Validate Connection" description="Check connectivity, credentials, and schema access before using this source." /><div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]"><div className="space-y-5"><SourceSummary source={source}/><section className={panel}><div className="flex items-center justify-between border-b border-[#bcc9c6] px-5 py-4"><h3 className="font-semibold">Validation checks</h3>{validate.data ? <StatusPill status={validate.data.status} /> : null}</div><div className="divide-y divide-[#d6dbd9]">{validate.data?.checks.map(check => <div key={check.name} className="flex gap-3 p-4"><span className={`flex size-8 shrink-0 items-center justify-center rounded-full ${check.status === "PASSED" ? "bg-emerald-50 text-emerald-600" : "bg-red-50 text-red-600"}`}>{check.status === "PASSED" ? <Check size={16}/> : <X size={16}/>}</span><div><div className="font-semibold">{check.name}</div><div className="mt-0.5 text-sm text-[#6d7a77]">{check.message}</div></div></div>) ?? <div className="p-8 text-center text-sm text-[#6d7a77]">Run validation to check this connection.</div>}</div></section>{validate.error ? <ErrorBanner error={validate.error}/> : null}</div><aside className={`${panel} h-fit p-5`}><h3 className="font-semibold">Next step</h3><p className="mt-2 text-sm leading-6 text-[#3d4947]">After the connection passes, inspect available tables, collections, and fields.</p><button className={`${primary} mt-5 w-full`} disabled={validate.isPending} onClick={() => { void validate.mutateAsync(undefined); }}>{validate.isPending ? <Loader2 className="animate-spin" size={17}/> : <ShieldCheck size={17}/>}Validate connection</button><button className={`${secondary} mt-3 w-full`} disabled={validate.data?.status !== "VALID" && source.status !== "VALID"} onClick={() => { navigate(`/data-sources/${source.id}/schema`); }}>Open Schema Explorer<ChevronRight size={17}/></button><div className="mt-5 rounded-xl bg-[#f0f5f2] p-4 text-xs leading-5 text-[#3d4947]"><KeyRound className="mb-2 text-[#00685f]" size={18}/>The saved credential is loaded securely by the backend and never returned to the browser.</div></aside></div></>;
}

export function SchemaExplorerPage() {
  const params = useParams<{ sourceId: string }>();
  const [, navigate] = useLocation();
  const sourceId = params.sourceId;
  const source = useConfiguredDataSource(sourceId);
  const schema = useDataSourceSchema(sourceId);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("");
  if (source.isLoading || schema.isLoading) return <LoadingCard label="Loading schema…"/>;
  if (source.error || schema.error || !source.data || !schema.data) return <ErrorBanner error={source.error ?? schema.error ?? new Error("Schema is unavailable.")}/>;
  const datasets = schema.data.datasets.filter(item => item.name.toLowerCase().includes(search.toLowerCase()));
  const selected = datasets.find(item => item.id === selectedId) ?? datasets.at(0);
  return <><SourceBreadcrumb source={source.data} current="Schema Explorer"/><PageTitle title="Schema Explorer" description="Browse the datasets and fields available through this connection." actions={<button className={secondary} onClick={() => { void schema.refetch(); }}><RefreshCw size={17}/>Refresh</button>}/><div className="grid min-h-[620px] gap-5 lg:grid-cols-[320px_minmax(0,1fr)]"><aside className={`${panel} overflow-hidden`}><div className="border-b border-[#bcc9c6] p-4"><label className="relative"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#6d7a77]"/><input className="h-9 w-full rounded-lg border border-[#bcc9c6] bg-[#f5faf8] pl-9 pr-3 text-sm outline-none focus:border-[#00685f]" value={search} onChange={event => { setSearch(event.target.value); }} placeholder="Find a dataset"/></label></div><div className="max-h-[560px] overflow-y-auto p-2">{datasets.map(dataset => <button key={dataset.id} className={`mb-1 flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left ${selected?.id === dataset.id ? "bg-[#e5f5f1] text-[#00685f]" : "hover:bg-[#f0f5f2]"}`} onClick={() => { setSelectedId(dataset.id); }}><span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white ring-1 ring-[#d6dbd9]">{dataset.kind === "TABLE" ? <Table2 size={16}/> : <Database size={16}/>}</span><span className="min-w-0"><span className="block truncate text-sm font-semibold">{dataset.namespace ? `${dataset.namespace}.` : ""}{dataset.name}</span><span className="text-xs opacity-70">{dataset.fields.length} fields · {dataset.kind.toLowerCase()}</span></span></button>)}{datasets.length === 0 ? <p className="p-6 text-center text-sm text-[#6d7a77]">No datasets found.</p> : null}</div></aside><section className={`${panel} overflow-hidden`}>{selected ? <><div className="flex flex-col justify-between gap-3 border-b border-[#bcc9c6] p-5 sm:flex-row sm:items-center"><div><div className="text-xs font-semibold uppercase tracking-wide text-[#00685f]">{selected.kind}</div><h2 className="mt-1 text-xl font-semibold">{selected.namespace ? `${selected.namespace}.` : ""}{selected.name}</h2><p className="mt-1 text-sm text-[#6d7a77]">{selected.description}</p></div>{selected.kind !== "NODE" ? <button className={primary} onClick={() => { navigate(`/data-sources/${sourceId}/data/${encodeURIComponent(selected.id)}`); }}><Eye size={17}/>View data</button> : null}</div><div className="overflow-x-auto"><table className="w-full min-w-[680px] text-left text-sm"><thead className="bg-[#f0f5f2] text-xs uppercase tracking-wide text-[#6d7a77]"><tr><th className="px-5 py-3">Field</th><th className="px-5 py-3">Type</th><th className="px-5 py-3">Required</th><th className="px-5 py-3">Key</th><th className="px-5 py-3">Description</th></tr></thead><tbody className="divide-y divide-[#d6dbd9]">{selected.fields.map(field => <tr key={field.name}><td className="px-5 py-3 font-mono text-xs font-semibold">{field.name}</td><td className="px-5 py-3"><span className="rounded bg-[#e4e9e7] px-2 py-1 text-xs">{field.type}</span></td><td className="px-5 py-3">{field.required ? "Yes" : "No"}</td><td className="px-5 py-3">{field.key ? <KeyRound size={16} className="text-[#00685f]"/> : "—"}</td><td className="px-5 py-3 text-[#6d7a77]">{field.description}</td></tr>)}</tbody></table></div></> : <div className="flex h-full items-center justify-center p-12 text-sm text-[#6d7a77]">Select a dataset to inspect its fields.</div>}</section></div></>;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  if (typeof value === "symbol") return value.description ?? "Symbol";
  if (typeof value === "function") return "[Function " + (value.name || "anonymous") + "]";
  return JSON.stringify(value);
}

export function DataViewerPage() {
  const params = useParams<{ sourceId: string; datasetId: string }>();
  const sourceId = params.sourceId;
  const datasetId = decodeURIComponent(params.datasetId);
  const source = useConfiguredDataSource(sourceId);
  const schema = useDataSourceSchema(sourceId);
  const preview = useDataPreview(sourceId, datasetId);
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState<"TABLE" | "JSON">("TABLE");
  if (source.isLoading || schema.isLoading || preview.isLoading) return <LoadingCard label="Loading data preview…"/>;
  if (source.error || schema.error || preview.error || !source.data || !preview.data) return <ErrorBanner error={source.error ?? schema.error ?? preview.error ?? new Error("Data preview is unavailable.")}/>;
  const previewData = preview.data;
  const dataset = schema.data?.datasets.find(item => item.id === datasetId);
  const rows = previewData.rows.filter(row => !search || JSON.stringify(row).toLowerCase().includes(search.toLowerCase()));
  return <><SourceBreadcrumb source={source.data} current="Data Viewer"/><div className="mb-5 flex items-center gap-3"><Link href={`/data-sources/${sourceId}/schema`} className="rounded-lg border border-[#bcc9c6] bg-white p-2 text-[#3d4947] hover:text-[#00685f]" aria-label="Back to schema"><ArrowLeft size={18}/></Link><div><h1 className="text-2xl font-semibold tracking-tight">{dataset ? `${dataset.namespace ? `${dataset.namespace}.` : ""}${dataset.name}` : datasetId}</h1><p className="mt-1 text-sm text-[#6d7a77]">Read-only preview · up to 25 rows · sensitive values redacted</p></div></div><section className={panel}><div className="flex flex-col gap-3 border-b border-[#bcc9c6] p-4 sm:flex-row sm:items-center sm:justify-between"><label className="relative max-w-md flex-1"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#6d7a77]"/><input className="h-9 w-full rounded-lg border border-[#bcc9c6] bg-[#f5faf8] pl-9 pr-3 text-sm outline-none focus:border-[#00685f]" value={search} onChange={event => { setSearch(event.target.value); }} placeholder="Filter visible rows"/></label><div className="flex gap-2"><button className={mode === "TABLE" ? primary : secondary} onClick={() => { setMode("TABLE"); }}><Table2 size={16}/>Table</button><button className={mode === "JSON" ? primary : secondary} onClick={() => { setMode("JSON"); }}><Code2 size={16}/>JSON</button><button className={secondary} onClick={() => { void preview.refetch(); }}><RefreshCw size={16}/></button></div></div>{mode === "TABLE" ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-sm"><thead className="bg-[#f0f5f2] text-xs uppercase tracking-wide text-[#6d7a77]"><tr>{previewData.columns.map(column => <th key={column} className="whitespace-nowrap px-4 py-3 font-semibold">{column}</th>)}</tr></thead><tbody className="divide-y divide-[#d6dbd9]">{rows.map((row, index) => <tr key={index} className="hover:bg-[#f5faf8]">{previewData.columns.map(column => <td key={column} className="max-w-72 truncate px-4 py-3 font-mono text-xs" title={displayValue(row[column])}>{displayValue(row[column])}</td>)}</tr>)}</tbody></table></div> : <pre className="max-h-[640px] overflow-auto bg-[#00201d] p-5 text-xs leading-6 text-[#c7fff6]">{JSON.stringify(rows, null, 2)}</pre>}{rows.length === 0 ? <div className="p-10 text-center text-sm text-[#6d7a77]">No rows match this filter.</div> : null}<div className="flex items-center justify-between border-t border-[#bcc9c6] px-4 py-3 text-xs text-[#6d7a77]"><span>{rows.length} rows shown</span><span className="flex items-center gap-1.5"><ShieldCheck size={15} className="text-[#00685f]"/>Read only</span></div></section></>;
}

export function DataSourceConfigApp() {
  return (
    <Router base="/v2/config">
      <DataSourceShell>
        <Switch>
          <Route path="/data-sources/:sourceId/data/:datasetId" component={DataViewerPage}/>
          <Route path="/data-sources/:sourceId/validate" component={ValidateConnectionPage}/>
          <Route path="/data-sources/:sourceId/schema" component={SchemaExplorerPage}/>
          <Route path="/data-sources" component={DataSourcesPage}/>
          <Route path="/modules/:moduleId" component={AgentConfigurationEditorPage}/>
          <Route path="/modules" component={ModuleCatalogPage}/>
          <Route path="/graph-schema" component={GraphCatalogPage}/>
          <Route path="/schema-design/proposals" component={ProposalReviewPage}/>
          <Route path="/schema-design/redesign" component={RedesignWorkspacePage}/>
          <Route path="/schema-design" component={SchemaDesignPage}/>
          <Route path="/sync" component={SyncOperationsPage}/>
          <Route path="/releases/compare" component={ReleaseComparisonPage}/>
          <Route path="/releases" component={ReleasesPage}/>
          <Route path="/import-export" component={ImportExportPage}/>
          <Route path="/governance" component={AuditGovernancePage}/>
          <Route path="/analytics" component={AnalyticsPage}/>
          <Route path="/policies" component={PoliciesPage}/>
          <Route path="/help" component={HelpPage}/>
          <Route path="/" component={StudioOverviewPage}/>
          <Route><Redirect to="/" replace/></Route>
        </Switch>
      </DataSourceShell>
    </Router>
  );
}
