import React, { useState, useCallback, useMemo } from "react";
import { useRoute, useLocation } from "wouter";
import { ReactFlow, Controls, Background, useNodesState, useEdgesState, Panel, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useGraphSearch } from "../../../../api/graphExplorerQueries";
import { GraphTable } from "./GraphTable";
import { NodeDetailPanel } from "./NodeDetailPanel";
import { RelationshipDetailPanel } from "./RelationshipDetailPanel";


export const GraphExplorerPage = () => {
  const [, setLocation] = useLocation();

  // Route matching
  const [matchNode, paramsNode] = useRoute("/data-console/graph/nodes/:nodeId");
  const [matchRel, paramsRel] = useRoute("/data-console/graph/relationships/:relationshipId");

  const activeNodeId = matchNode ? decodeURIComponent(paramsNode.nodeId) : null;
  const activeRelId = matchRel ? decodeURIComponent(paramsRel.relationshipId) : null;
  const hasInspector = Boolean(activeNodeId ?? activeRelId);

  // State for search and view mode
  // Note: if user directly loads a node URL but queryId is empty,
  // we initialize it automatically from the URL
  const [searchId, setSearchId] = useState(activeNodeId ?? "");
  const [queryId, setQueryId] = useState(activeNodeId ?? "");
  const [viewMode, setViewMode] = useState<"canvas" | "table">("canvas");
  const [expansionDepth, setExpansionDepth] = useState<number>(1);

  const { data: searchResult, isLoading, isError, error } = useGraphSearch(queryId, expansionDepth);

  const handleSearch = (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (searchId.trim()) {
      setQueryId(searchId.trim());
      // Navigate to the node details when searching for it
      setLocation(`/data-console/graph/nodes/${encodeURIComponent(searchId.trim())}`);
    }
  };

  const closeInspector = useCallback(() => {
    setLocation("/data-console/graph");
  }, [setLocation]);

  // Convert GraphNode/Relationship to ReactFlow format
  const initialNodes = useMemo(() => {
    if (!searchResult?.data.nodes) return [];
    return searchResult.data.nodes.map((n) => {
      // Deterministic pseudo-random position based on ID string
      const seed = Array.from(n.id).reduce((acc, char) => acc + char.charCodeAt(0), 0);
      return {
        id: n.id,
        position: { x: (seed * 13) % 500, y: (seed * 17) % 500 },
        data: { label: `${n.labels[0] ?? 'Node'}\n${n.id}` },
      style: {
        background: activeNodeId === n.id ? '#e0e7ff' : '#fff',
        border: activeNodeId === n.id ? '2px solid #4f46e5' : '1px solid #222',
        borderRadius: '8px',
        padding: '10px',
        fontSize: '12px',
        width: 150
      }
    };
    });
  }, [searchResult, activeNodeId]);

  const initialEdges = useMemo(() => {
    if (!searchResult?.data.relationships) return [];
    return searchResult.data.relationships.map(r => ({
      id: r.id,
      source: r.startNodeId,
      target: r.endNodeId,
      label: r.type,
      animated: activeRelId === r.id,
      style: { stroke: activeRelId === r.id ? '#4f46e5' : '#999', strokeWidth: activeRelId === r.id ? 3 : 1 }
    }));
  }, [searchResult, activeRelId]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  React.useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setLocation(`/data-console/graph/nodes/${encodeURIComponent(node.id)}`);
  }, [setLocation]);

  const onEdgeClick = useCallback((_: React.MouseEvent, edge: Edge) => {
    setLocation(`/data-console/graph/relationships/${encodeURIComponent(edge.id)}`);
  }, [setLocation]);

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Header / Search Bar */}
      <header className="flex-none p-4 border-b flex flex-wrap gap-4 items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Graph Explorer</h1>
          <p className="text-sm text-gray-500">Read-only exploration by exact ID</p>
        </div>

        <form onSubmit={handleSearch} className="flex gap-2 items-center flex-1 max-w-lg">
          <input
            type="text"
            value={searchId}
            onChange={(e) => { setSearchId(e.target.value); }}
            placeholder="Enter Exact Node ID..."
            className="flex-1 px-3 py-2 border rounded text-sm focus:ring-2 focus:ring-blue-500"
            aria-label="Search by exact ID"
          />
          <select
            value={expansionDepth}
            onChange={(e) => { setExpansionDepth(Number(e.target.value)); }}
            className="px-3 py-2 border rounded text-sm bg-white"
            aria-label="Expansion Depth"
          >
            <option value={1}>Depth: 1</option>
            <option value={2}>Depth: 2</option>
            <option value={3}>Depth: 3</option>
          </select>
          <button type="submit">Search</button>
        </form>

        <div className="flex gap-2">
          <button
            className={`px-3 py-1 border rounded ${viewMode === "canvas" ? "bg-blue-600 text-white" : "bg-white"}`}
            onClick={() => { setViewMode("canvas"); }}
            aria-pressed={viewMode === "canvas"}
          >
            Canvas
          </button>
          <button
            className={`px-3 py-1 border rounded ${viewMode === "table" ? "bg-blue-600 text-white" : "bg-white"}`}
            onClick={() => { setViewMode("table"); }}
            aria-pressed={viewMode === "table"}
          >
            Table
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <div className="flex-1 flex overflow-hidden relative">

        {/* Left Side: View (Canvas or Table) */}
        <div className={`flex-1 transition-all duration-300 ${hasInspector ? 'hidden md:block md:w-2/3' : 'w-full'}`}>
          {isLoading && (
            <div className="flex items-center justify-center h-full">
              <p className="text-gray-500">Loading graph...</p>
            </div>
          )}

          {isError && (
            <div className="flex items-center justify-center h-full p-4">
              <div className="bg-red-50 text-red-600 p-4 rounded border border-red-200 text-center max-w-md">
                <h3 className="font-bold mb-2">Search Failed</h3>
                <p>{error instanceof Error ? error.message : "Unknown error occurred"}</p>
              </div>
            </div>
          )}

          {!isLoading && !isError && searchResult && (
            <>
              {searchResult.meta.isTruncated && (
                <div className="absolute top-4 left-4 z-10 bg-yellow-100 text-yellow-800 px-3 py-1 text-xs rounded border border-yellow-300 shadow-sm" role="alert">
                  Warning: Results are truncated due to expansion limits.
                </div>
              )}

              {viewMode === "table" ? (
                <div className="h-full overflow-y-auto p-4">
                  <GraphTable
                    nodes={searchResult.data.nodes}
                    relationships={searchResult.data.relationships}
                  />
                </div>
              ) : (
                <div className="h-full w-full bg-gray-50">
                  <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onNodeClick={onNodeClick}
                    onEdgeClick={onEdgeClick}
                    fitView
                  >
                    <Background />
                    <Controls />
                    <Panel position="bottom-left" className="bg-white/80 p-2 text-xs rounded shadow backdrop-blur-sm">
                      <p>Nodes: {searchResult.data.nodes.length}</p>
                      <p>Rels: {searchResult.data.relationships.length}</p>
                    </Panel>
                  </ReactFlow>
                </div>
              )}
            </>
          )}

          {!isLoading && !isError && !searchResult && (
            <div className="flex items-center justify-center h-full">
              <p className="text-gray-500">Enter a Node ID to explore the graph.</p>
            </div>
          )}
        </div>

        {/* Right Side: Inspector Panel */}
        {hasInspector && (
          <aside className="w-full md:w-1/3 h-full border-l bg-white flex-shrink-0 z-20 shadow-[-4px_0_15px_rgba(0,0,0,0.05)]">
            {activeNodeId && (
              <NodeDetailPanel nodeId={activeNodeId} onClose={closeInspector} />
            )}
            {activeRelId && (
              <RelationshipDetailPanel relationshipId={activeRelId} onClose={closeInspector} />
            )}
          </aside>
        )}

      </div>
    </div>
  );
};
