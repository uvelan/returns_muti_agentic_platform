import { useState } from "react";
import { useLocation } from "wouter";
import { PageHeader } from "../../../components/PageHeader";
import { LoadingState } from "../../../components/LoadingState";
import { ErrorState } from "../../../components/ErrorState";
import { useSources } from "../../../api/sourceQueries";
import { FixtureNotice } from "../components/FixtureNotice";
import { SearchInput } from "../components/SearchInput";
import { FilterBar } from "../components/FilterBar";
import { DataTable, type ColumnDef } from "../components/DataTable";
import { EngineBadge } from "../components/EngineBadge";
import { type SourceItem } from "../../../contracts/sources";

export function SourcesPage() {
  const [, setLocation] = useLocation();
  const { data, isLoading, error } = useSources();
  const [search, setSearch] = useState("");
  const [engineFilter, setEngineFilter] = useState("ALL");
  const [capabilityFilter, setCapabilityFilter] = useState("ALL");

  if (isLoading) return <LoadingState message="Loading data sources..." />;
  if (error) {
    if (error instanceof Error && error.message.includes("CAPABILITY_ERROR")) {
      return (
        <ErrorState 
          title="Data Sources Unavailable" 
          message="The Data Sources capability requires Fixture Mode. Enable VITE_MOCK_MODE to view this page." 
        />
      );
    }
    return <ErrorState title="Failed to load Data Sources" message={error instanceof Error ? error.message : "Unknown error"} />;
  }

  let filteredSources = data ?? [];
  
  if (search) {
    const s = search.toLowerCase();
    filteredSources = filteredSources.filter(src => 
      src.name.toLowerCase().includes(s) || src.id.toLowerCase().includes(s)
    );
  }

  if (engineFilter !== "ALL") {
    filteredSources = filteredSources.filter(src => src.engine === engineFilter);
  }
  
  if (capabilityFilter !== "ALL") {
    filteredSources = filteredSources.filter(src => src.capability === capabilityFilter);
  }

  const columns: ColumnDef<SourceItem>[] = [
    {
      header: "Name",
      accessor: (src) => (
        <div>
          <div className="font-medium text-gray-900">{src.name}</div>
          <div className="text-gray-500 text-xs">{src.id}</div>
        </div>
      )
    },
    {
      header: "Engine",
      accessor: (src) => <EngineBadge engine={src.engine} />
    },
    {
      header: "Environment",
      accessor: (src) => <span className="text-gray-700">{src.environment}</span>
    },
    {
      header: "Ownership",
      accessor: (src) => <span className="text-gray-700">{src.ownership}</span>
    },
    {
      header: "Capability",
      accessor: (src) => (
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
          src.capability === 'WRITABLE' ? 'bg-orange-100 text-orange-800' : 'bg-gray-100 text-gray-800'
        }`}>
          {src.capability}
        </span>
      )
    },
    {
      header: "Health",
      accessor: (src) => (
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
          src.health === 'HEALTHY' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
        }`}>
          {src.health}
        </span>
      )
    }
  ];

  return (
    <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8 space-y-6">
      <PageHeader title="Data Sources" description="Manage and explore connected data source inventories." />
      <FixtureNotice />

      <div className="flex flex-col sm:flex-row gap-4 items-end">
        <div className="flex-1 w-full">
          <SearchInput value={search} onChange={setSearch} placeholder="Search sources by name or ID..." />
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
            { label: "Platform", value: "PLATFORM" }
          ]} 
        />
        <FilterBar 
          label="Capability" 
          value={capabilityFilter} 
          onChange={setCapabilityFilter} 
          options={[
            { label: "All Capabilities", value: "ALL" },
            { label: "Read Only", value: "READ_ONLY" },
            { label: "Writable", value: "WRITABLE" }
          ]} 
        />
      </div>

      <DataTable 
        data={filteredSources} 
        columns={columns} 
        keyExtractor={(src) => src.id} 
        onRowClick={(src) => { setLocation(`/data-console/sources/${src.id}`); }}
      />
    </div>
  );
}
