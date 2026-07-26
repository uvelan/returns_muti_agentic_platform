
import { useGraphNode, useGraphNeighborhood } from "../../../../api/graphExplorerQueries";
import { Link } from "wouter";


type Props = {
  nodeId: string;
  onClose: () => void;
};

export const NodeDetailPanel: React.FC<Props> = ({ nodeId, onClose }) => {
  const { data: node, isLoading, isError, error } = useGraphNode(nodeId);
  const { data: neighborhood, isLoading: isLoadingNeighborhood } = useGraphNeighborhood(nodeId, 1);

  if (isLoading) {
    return <div className="p-4" aria-busy="true">Loading node details...</div>;
  }

  if (isError || !node) {
    return (
      <div className="p-4 text-red-600" role="alert">
        <h3 className="font-semibold">Error loading node</h3>
        <p className="text-sm">{error instanceof Error ? error.message : "Unknown error"}</p>
        <button className="mt-4 px-3 py-1 border rounded bg-white text-gray-700 hover:bg-gray-50" onClick={onClose}>Close</button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex justify-between items-center p-4 border-b">
        <h2 className="text-lg font-bold truncate" title={node.id}>Node: {node.id}</h2>
        <button className="text-gray-500 hover:text-gray-700 font-bold" onClick={onClose} aria-label="Close inspector">X</button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Labels</h3>
          <div className="flex flex-wrap gap-2">
            {node.labels.map(label => (
              <span key={label} className="bg-blue-100 text-blue-800 text-xs px-2 py-1 rounded-md font-medium">
                {label}
              </span>
            ))}
          </div>
        </section>

        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Properties</h3>
          {node.truncated && (
            <div className="bg-yellow-50 text-yellow-800 p-2 text-xs rounded mb-2" role="alert">
              Properties have been truncated.
            </div>
          )}
          <dl className="grid grid-cols-1 gap-2 text-sm">
            {Object.entries(node.properties).map(([key, value]) => (
              <div key={key} className="bg-gray-50 p-2 rounded border border-gray-100">
                <dt className="font-semibold text-gray-700">{key}</dt>
                <dd className="mt-1 break-all text-gray-600 font-mono text-xs">
                  {JSON.stringify(value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Provenance</h3>
          {node.provenance ? (
            <dl className="text-sm space-y-1">
              {node.provenance.source_id && (
                <div className="flex gap-2">
                  <dt className="font-semibold">Source:</dt>
                  <dd>
                    <Link href={`/data-console/sources/${node.provenance.source_id}`} className="text-blue-600 hover:underline">
                      {node.provenance.source_id}
                    </Link>
                  </dd>
                </div>
              )}
              {node.provenance.document_id && (
                <div className="flex gap-2">
                  <dt className="font-semibold">Document:</dt>
                  <dd>
                    {/* Link to Graph Evidence if applicable */}
                    <Link href={`/data-console/graph-evidence`} className="text-blue-600 hover:underline">
                      {node.provenance.document_id}
                    </Link>
                  </dd>
                </div>
              )}
            </dl>
          ) : (
            <p className="text-sm text-gray-500 font-style-italic">No provenance available</p>
          )}
        </section>

        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Ownership</h3>
          <p className="text-sm">
            {node.ownership?.owner ? (
              <span className="font-medium text-gray-800">{node.ownership.owner}</span>
            ) : (
              <span className="text-gray-500 italic">Unowned</span>
            )}
          </p>
        </section>

        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Neighborhood (Depth 1)</h3>
          {isLoadingNeighborhood ? (
            <p className="text-sm text-gray-500">Loading neighborhood...</p>
          ) : neighborhood?.data.relationships.length ? (
            <ul className="text-sm space-y-2">
              {neighborhood.data.relationships.map(rel => {
                const isStart = rel.startNodeId === node.id;
                const otherNodeId = isStart ? rel.endNodeId : rel.startNodeId;
                return (
                  <li key={rel.id} className="flex flex-col gap-1 bg-gray-50 p-2 rounded">
                    <span className="text-xs font-semibold text-purple-700 uppercase">{rel.type}</span>
                    <div className="flex justify-between items-center">
                      <span className="text-gray-600">{isStart ? "Outgoing to:" : "Incoming from:"}</span>
                      <Link href={`/data-console/graph/nodes/${encodeURIComponent(otherNodeId)}`} className="text-blue-600 hover:underline truncate max-w-[150px]">
                        {otherNodeId}
                      </Link>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-gray-500">No relationships</p>
          )}
        </section>
      </div>
    </div>
  );
};
