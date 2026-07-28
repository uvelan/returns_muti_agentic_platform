import { useState } from "react";
import { AlertTriangle, CheckCircle, GitBranch, Layers, Network, Plus, Save, Tag } from "lucide-react";

import {
  useActiveSnapshot,
  useConfigurationReleaseDetail,
  useConfigurationReleases,
  useCreateReleaseMutation,
  usePromoteReleaseMutation,
  useSaveDomainMutation,
} from "../../../../api/configurationQueries";
import { ErrorState } from "../../../../components/ErrorState";
import { LoadingState } from "../../../../components/LoadingState";
import { PageHeader } from "../../../../components/PageHeader";

export function ConfigurationStudioPage() {
  const { data: snapshot, isLoading: snapLoading, isError: snapError, error: snapErr } = useActiveSnapshot();
  const { data: releases = [], isLoading: relLoading } = useConfigurationReleases();
  
  const [selectedReleaseId, setSelectedReleaseId] = useState<string | null>(null);
  const [newReleaseId, setNewReleaseId] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const selectedDomainKey = "RETURN_PLATFORM";
  const [domainJsonText, setDomainJsonText] = useState<string>("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const snapshotReleaseId = releases.some((release) => release.release_id === snapshot?.release_id)
    ? snapshot?.release_id
    : null;
  const activeReleaseId = selectedReleaseId ?? snapshotReleaseId ?? (releases.length > 0 ? releases[0].release_id : null);
  const { data: detail, isLoading: detailLoading } = useConfigurationReleaseDetail(activeReleaseId);

  const createMutation = useCreateReleaseMutation();
  const saveMutation = useSaveDomainMutation();
  const promoteMutation = usePromoteReleaseMutation();

  if (snapLoading || relLoading) {
    return <LoadingState message="Loading Graph Configuration Studio..." />;
  }
  if (snapError || !snapshot) {
    return (
      <ErrorState
        title="Configuration Studio Unavailable"
        message={snapErr instanceof Error ? snapErr.message : "Failed to load active snapshot"}
      />
    );
  }

  const handleCreateRelease = (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!newReleaseId.trim()) return;
    createMutation.mutate(
      { releaseId: newReleaseId.trim(), fromActive: true },
      {
        onSuccess: (newRel) => {
          if (newRel) setSelectedReleaseId(newRel.release_id);
          setNewReleaseId("");
          setIsCreating(false);
        },
      }
    );
  };

  const currentDomainConfig = detail?.domains?.[selectedDomainKey] ?? {};
  const currentJsonString = JSON.stringify(currentDomainConfig, null, 2);

  const handleDomainJsonChange = (val: string) => {
    setDomainJsonText(val);
    setJsonError(null);
    setSaveSuccess(false);
  };

  const handleSaveDomain = () => {
    if (!activeReleaseId) return;
    let parsed: Record<string, unknown>;
    try {
      const textToParse = domainJsonText || currentJsonString;
      parsed = JSON.parse(textToParse) as Record<string, unknown>;
    } catch {
      setJsonError("Invalid JSON syntax. Please fix formatting before saving.");
      return;
    }
    setJsonError(null);
    saveMutation.mutate(
      {
        releaseId: activeReleaseId,
        domainKey: selectedDomainKey,
        payload: parsed,
      },
      {
        onSuccess: () => {
          setSaveSuccess(true);
          setTimeout(() => { setSaveSuccess(false); }, 3000);
        },
      }
    );
  };

  const handlePromote = (targetStatus: "VALIDATED" | "RELEASED" | "ARCHIVED") => {
    if (!activeReleaseId) return;
    promoteMutation.mutate({
      releaseId: activeReleaseId,
      status: targetStatus,
      expectedHeadRevision: targetStatus === "RELEASED" ? snapshot.head_revision : undefined,
    });
  };

  const isEditable = detail?.status === "DRAFT";

  return (
    <div className="max-w-7xl p-6">
      <PageHeader
        title="Graph Configuration Studio"
        description="Manage validated Neo4j configuration releases, runtime policies, data sources, and AI routing references."
      />

      {/* Active Snapshot Status Banner */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div
              className={`flex h-10 w-10 items-center justify-center rounded-full ${
                snapshot.source === "NEO4J_CONFIGURATION_GRAPH" ? "bg-green-100 text-green-600" : "bg-amber-100 text-amber-600"
              }`}
            >
              {snapshot.source === "NEO4J_CONFIGURATION_GRAPH" ? <Network className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5" />}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-gray-900">Active Runtime Snapshot</span>
                <span
                  className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                    snapshot.source === "NEO4J_CONFIGURATION_GRAPH"
                      ? "bg-green-100 text-green-800"
                      : "bg-amber-100 text-amber-800"
                  }`}
                >
                  {snapshot.source === "NEO4J_CONFIGURATION_GRAPH" ? "Neo4j Graph Active" : "Version-Controlled Recovery Snapshot"}
                </span>
              </div>
              <p className="text-xs text-gray-500">
                Release: <span className="font-mono font-medium text-gray-700">{snapshot.release_id}</span> • Head Revision:{" "}
                <span className="font-mono text-gray-600">{snapshot.head_revision}</span> • SHA-256 Checksum:{" "}
                <span className="font-mono text-gray-600">{snapshot.checksum_sha256.slice(0, 16)}...</span>
              </p>
            </div>
          </div>

          <div className="text-right">
            <span className="text-xs text-gray-400">Loaded At</span>
            <div className="text-xs font-medium text-gray-600">{new Date(snapshot.loaded_at).toLocaleString()}</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
        {/* Left Sidebar: Releases List */}
        <div className="space-y-4 lg:col-span-1">
          <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                <GitBranch className="h-4 w-4 text-gray-500" />
                Releases
              </h2>
              <button
                type="button"
                onClick={() => { setIsCreating(!isCreating); }}
                className="inline-flex items-center gap-1 rounded bg-blue-600 px-2 py-1 text-xs font-semibold text-white hover:bg-blue-700"
              >
                <Plus className="h-3 w-3" /> New
              </button>
            </div>

            {isCreating && (
              <form onSubmit={handleCreateRelease} className="mb-4 rounded bg-gray-50 p-3 border border-gray-200">
                <label className="block text-xs font-medium text-gray-700 mb-1">New Release ID</label>
                <input
                  type="text"
                  placeholder="e.g. rel-od-v2"
                  value={newReleaseId}
                  onChange={(e) => { setNewReleaseId(e.target.value); }}
                  className="w-full rounded border border-gray-300 px-2 py-1 text-xs mb-2 focus:border-blue-500 focus:outline-none"
                  required
                />
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => { setIsCreating(false); }}
                    className="px-2 py-1 text-xs text-gray-600 hover:text-gray-900"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={createMutation.isPending}
                    className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {createMutation.isPending ? "Creating..." : "Create Draft"}
                  </button>
                </div>
              </form>
            )}

            <div className="space-y-2 max-h-[500px] overflow-y-auto">
              {releases.length === 0 ? (
                <p className="text-xs text-gray-500 italic">No graph releases found. Click New to create a draft.</p>
              ) : (
                releases.map((rel) => {
                  const isSelected = rel.release_id === activeReleaseId;
                  let badgeColor = "bg-gray-100 text-gray-800";
                  if (rel.status === "VALIDATED") badgeColor = "bg-blue-100 text-blue-800";
                  else if (rel.status === "DRAFT") badgeColor = "bg-amber-100 text-amber-800";
                  else if (rel.status === "RELEASED") badgeColor = "bg-green-100 text-green-800 border border-green-300";
                  else if (rel.status === "SUPERSEDED") badgeColor = "bg-purple-100 text-purple-800";

                  return (
                    <div
                      key={rel.release_id}
                      onClick={() => {
                        setSelectedReleaseId(rel.release_id);
                        setDomainJsonText("");
                        setJsonError(null);
                        setSaveSuccess(false);
                      }}
                      className={`cursor-pointer rounded-lg border p-3 transition-colors ${
                        isSelected
                          ? "border-blue-500 bg-blue-50/50"
                          : "border-gray-200 bg-white hover:border-gray-300"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-mono text-xs font-bold text-gray-900">{rel.release_id}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${badgeColor}`}>
                          {rel.status}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-[11px] text-gray-500">
                        <span>By {rel.created_by}</span>
                        <span>{new Date(rel.created_at).toLocaleDateString()}</span>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* Right Main Area: Domain Editor */}
        <div className="space-y-6 lg:col-span-3">
          {detailLoading || !detail ? (
            <LoadingState message="Loading release details..." />
          ) : (
            <>
              {/* Release Detail Card */}
              <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-4 border-b border-gray-100 pb-4 mb-4">
                  <div>
                    <h3 className="text-base font-bold text-gray-900 flex items-center gap-2">
                      <Tag className="h-4 w-4 text-blue-600" />
                      Release Studio: <span className="font-mono text-blue-600">{detail.release_id}</span>
                    </h3>
                    <p className="text-xs text-gray-500 mt-0.5">
                      Checksum: <span className="font-mono text-gray-700">{detail.checksum_sha256 || "Uncalculated"}</span> • Status:{" "}
                      <span className="font-semibold text-gray-800">{detail.status}</span>
                    </p>
                  </div>

                  <div className="flex items-center gap-2">
                    {detail.status === "DRAFT" && (
                      <button
                        type="button"
                        onClick={() => { handlePromote("VALIDATED"); }}
                        disabled={promoteMutation.isPending}
                        className="inline-flex items-center gap-1 rounded bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                      >
                        <CheckCircle className="h-3.5 w-3.5" /> Validate Release
                      </button>
                    )}
                    {detail.status === "VALIDATED" && (
                      <button
                        type="button"
                        onClick={() => { handlePromote("RELEASED"); }}
                        disabled={promoteMutation.isPending}
                        className="inline-flex items-center gap-1 rounded bg-green-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-700 disabled:opacity-50"
                      >
                        <CheckCircle className="h-3.5 w-3.5" /> Publish as Active
                      </button>
                    )}
                    {(detail.status === "DRAFT" || detail.status === "VALIDATED" || detail.status === "SUPERSEDED") && (
                      <button
                        type="button"
                        onClick={() => { handlePromote("ARCHIVED"); }}
                        disabled={promoteMutation.isPending}
                        className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                      >
                        Archive
                      </button>
                    )}
                  </div>
                </div>

                <div className="mb-4 border-b border-gray-200">
                  <div className="inline-flex border-b-2 border-blue-600 px-4 py-2 text-xs font-semibold text-blue-600">
                    RETURN_PLATFORM
                  </div>
                </div>

                {/* Domain Config Editor */}
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
                      <Layers className="h-3.5 w-3.5 text-gray-500" />
                      Domain Payload JSON ({selectedDomainKey})
                    </span>
                    {!isEditable && (
                      <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded">
                        Read-Only (Release is {detail.status})
                      </span>
                    )}
                  </div>

                  {jsonError && (
                    <div className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-700 flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      <span>{jsonError}</span>
                    </div>
                  )}

                  {saveSuccess && (
                    <div className="rounded border border-green-200 bg-green-50 p-3 text-xs text-green-700 flex items-center gap-2">
                      <CheckCircle className="h-4 w-4 shrink-0" />
                      <span>Domain configuration successfully updated and checksum recomputed!</span>
                    </div>
                  )}

                  <textarea
                    value={domainJsonText || currentJsonString}
                    onChange={(e) => { handleDomainJsonChange(e.target.value); }}
                    disabled={!isEditable || saveMutation.isPending}
                    rows={12}
                    className="w-full font-mono text-xs rounded border border-gray-300 bg-gray-50 p-3 focus:border-blue-500 focus:bg-white focus:outline-none disabled:bg-gray-100 disabled:text-gray-500"
                  />

                  {isEditable && (
                    <div className="flex justify-end gap-3 pt-2">
                      <button
                        type="button"
                        onClick={() => { setDomainJsonText(currentJsonString); }}
                        disabled={saveMutation.isPending}
                        className="rounded border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                      >
                        Reset Changes
                      </button>
                      <button
                        type="button"
                        onClick={handleSaveDomain}
                        disabled={saveMutation.isPending}
                        className="inline-flex items-center gap-1.5 rounded bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        <Save className="h-3.5 w-3.5" />
                        {saveMutation.isPending ? "Saving..." : "Save Domain Config"}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
