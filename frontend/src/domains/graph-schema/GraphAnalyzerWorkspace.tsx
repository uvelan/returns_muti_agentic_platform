import { useLocation } from "wouter";

import { GraphAnalyzerProvider } from "../../features/graph-analyzer/GraphAnalyzerContext";
import { GraphAnalyzerPage } from "../../features/graph-analyzer/pages/GraphAnalyzerPage";
import { SchemaWorkspacePage } from "../../features/graph-analyzer/pages/SchemaWorkspacePage";
import { SyncWorkspacePage } from "../../features/graph-analyzer/pages/SyncWorkspacePage";

function WorkspacePage() {
  const [location] = useLocation();

  if (location.startsWith("/graph-schema/sync")) {
    return <SyncWorkspacePage />;
  }
  if (location.startsWith("/graph-schema/schema")) {
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