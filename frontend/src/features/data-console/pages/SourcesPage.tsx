import { useState } from "react";
import { useLocation } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { useSources } from "../../../api/sourceQueries";
import { SearchInput } from "../components/SearchInput";
import { FilterBar } from "../components/FilterBar";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { EngineBadge } from "../components/EngineBadge";
import { type SourceItem } from "../../../contracts/sources";

function healthClass(health: SourceItem["health"]): string {
  if (health === "HEALTHY") return "bg-green-100 text-green-800";
  if (health === "DEGRADED") return "bg-amber-100 text-amber-800";
  if (health === "UNAVAILABLE") return "bg-red-100 text-red-800";
  return "bg-gray-100 text-gray-800";
}

export function SourcesPage() {
  const [, setLocation] = useLocation();
  const { data, isLoading, error } = useSources();
  const [search, setSearch] = useState("");
  const [engineFilter, setEngineFilter] = useState("ALL");
  const [capabilityFilter, setCapabilityFilter] = useState("ALL");

  if (isLoading) return <LoadingState message="Loading data sources..." />;
  if (error) {
    return (
      <ErrorState
        title="Failed to load data sources"
        message={error instanceof Error ? error.message : "Unknown error"}
      />
    );
  }

  let filteredSources = data ?? [];
  if (search) {
    const normalized = search.toLowerCase();
    filteredSources = filteredSources.filter(
      (source) =>
        source.name.toLowerCase().includes(normalized) ||
        source.id.toLowerCase().includes(normalized),
    );
  }
  if (engineFilter !== "ALL") {
    filteredSources = filteredSources.filter((source) => source.engine === engineFilter);
  }
  if (capabilityFilter !== "ALL") {
    filteredSources = filteredSources.filter(
      (source) => source.capability === capabilityFilter,
    );
  }

  const columns: ColumnDef<SourceItem>[] = [
    {
      header: "Name",
      accessor: (source) => (
        <div>
          <div className="font-medium text-gray-900">{source.name}</div>
          <div className="text-gray-500 text-xs">{source.id}</div>
        </div>
      ),
    },
    { header: "Engine", accessor: (source) => <EngineBadge engine={source.engine} /> },
    { header: "Environment", accessor: (source) => source.environment },
    { header: "Ownership", accessor: (source) => source.ownership },
    {
      header: "Capability",
      accessor: (source) => (
        <span className="inline-flex rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-800">
          {source.capability}
        </span>
      ),
    },
    {
      header: "Health",
      accessor: (source) => (
        <span
          className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${healthClass(source.health)}`}
        >
          {source.health}
        </span>
      ),
    },
  ];

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">
      <PageHeader
        title="Data Sources"
        description="Inspect authoritative sources, platform stores, and derived graph projections."
      />

      <div className="flex flex-col sm:flex-row gap-4 items-end">
        <div className="flex-1 w-full">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search sources by name or ID..."
          />
        </div>
        <FilterBar
          label="Engine"
          value={engineFilter}
          onChange={setEngineFilter}
          options={[
            { label: "All Engines", value: "ALL" },
            { label: "SQL Server", value: "SQL_SERVER" },
            { label: "MongoDB", value: "MONGODB" },
            { label: "Neo4j", value: "NEO4J" },
            { label: "Platform", value: "PLATFORM" },
          ]}
        />
        <FilterBar
          label="Capability"
          value={capabilityFilter}
          onChange={setCapabilityFilter}
          options={[
            { label: "All Capabilities", value: "ALL" },
            { label: "Read Only", value: "READ_ONLY" },
            { label: "Writable", value: "WRITABLE" },
          ]}
        />
      </div>

      <DataTable
        data={filteredSources}
        columns={columns}
        keyExtractor={(source) => source.id}
        onRowClick={(source) => { setLocation(`/data-console/sources/${source.id}`); }}
      />
    </div>
  );
}
