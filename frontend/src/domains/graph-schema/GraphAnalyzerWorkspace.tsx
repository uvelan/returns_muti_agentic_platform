import { GraphAnalyzerProvider } from "../../features/graph-analyzer/GraphAnalyzerContext";
import { DataSourcesWorkspacePage } from "../../features/graph-analyzer/pages/DataSourcesWorkspacePage";
import { GraphAnalyzerPage } from "../../features/graph-analyzer/pages/GraphAnalyzerPage";
import { SchemaWorkspacePage } from "../../features/graph-analyzer/pages/SchemaWorkspacePage";
import { SyncWorkspacePage } from "../../features/graph-analyzer/pages/SyncWorkspacePage";
import { requireDomain, type GRAPH_SCHEMA_SECTIONS } from "../registry";
import { useDomainSection } from "../useDomainSection";

/** Derived from the registry, so a section renamed there is an error here. */
type AnalyzerSection = (typeof GRAPH_SCHEMA_SECTIONS)[number];

/**
 * Which analyzer workspace is on screen.
 *
 * The section comes from the URL and the rail sets it, so this screen holds no
 * navigation state of its own -- the same arrangement Configuration and Returns
 * Support use. It previously matched the pathname itself and rendered its own
 * tab strip, which meant the rail showed nothing for this domain and the
 * platform had two navigations stacked one above the other.
 */
function WorkspacePage() {
  const section = useDomainSection(requireDomain("/graph-schema")) as AnalyzerSection;

  if (section === "Data Sources") {
    return <DataSourcesWorkspacePage />;
  }
  if (section === "Sync") {
    return <SyncWorkspacePage />;
  }
  if (section === "Schema") {
    return <SchemaWorkspacePage />;
  }
  return <GraphAnalyzerPage />;
}

export function GraphAnalyzerWorkspace() {
  return (
    <GraphAnalyzerProvider>
      <WorkspacePage />
    </GraphAnalyzerProvider>
  );
}
