
import { useGraphRelationship } from "../../../../api/graphExplorerQueries";
import { Link } from "wouter";


type Props = {
  relationshipId: string;
  onClose: () => void;
};

export const RelationshipDetailPanel: React.FC<Props> = ({ relationshipId, onClose }) => {
  const { data: relationship, isLoading, isError, error } = useGraphRelationship(relationshipId);

  if (isLoading) {
    return <div className="p-4" aria-busy="true">Loading relationship details...</div>;
  }

  if (isError || !relationship) {
    return (
      <div className="p-4 text-red-600" role="alert">
        <h3 className="font-semibold">Error loading relationship</h3>
        <p className="text-sm">{error instanceof Error ? error.message : "Unknown error"}</p>
        <button className="mt-4 px-3 py-1 border rounded bg-white text-gray-700 hover:bg-gray-50" onClick={onClose}>Close</button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex justify-between items-center p-4 border-b">
        <h2 className="text-lg font-bold truncate" title={relationship.id}>Rel: {relationship.id}</h2>
        <button className="text-gray-500 hover:text-gray-700 font-bold" onClick={onClose} aria-label="Close inspector">X</button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Type</h3>
          <span className="bg-purple-100 text-purple-800 text-xs px-2 py-1 rounded-md font-medium">
            {relationship.type}
          </span>
        </section>

        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Connections</h3>
          <div className="flex flex-col gap-2">
            <div className="bg-gray-50 p-2 rounded border border-gray-200">
              <span className="text-xs font-semibold text-gray-500 block mb-1">From Node:</span>
              <Link href={`/data-console/graph/nodes/${encodeURIComponent(relationship.startNodeId)}`} className="text-blue-600 hover:underline break-all text-sm font-mono">
                {relationship.startNodeId}
              </Link>
            </div>
            <div className="bg-gray-50 p-2 rounded border border-gray-200">
              <span className="text-xs font-semibold text-gray-500 block mb-1">To Node:</span>
              <Link href={`/data-console/graph/nodes/${encodeURIComponent(relationship.endNodeId)}`} className="text-blue-600 hover:underline break-all text-sm font-mono">
                {relationship.endNodeId}
              </Link>
            </div>
          </div>
        </section>

        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">Properties</h3>
          {relationship.truncated && (
            <div className="bg-yellow-50 text-yellow-800 p-2 text-xs rounded mb-2" role="alert">
              Properties have been truncated.
            </div>
          )}
          {Object.keys(relationship.properties).length === 0 ? (
            <p className="text-sm text-gray-500 italic">No properties</p>
          ) : (
            <dl className="grid grid-cols-1 gap-2 text-sm">
              {Object.entries(relationship.properties).map(([key, value]) => (
                <div key={key} className="bg-gray-50 p-2 rounded border border-gray-100">
                  <dt className="font-semibold text-gray-700">{key}</dt>
                  <dd className="mt-1 break-all text-gray-600 font-mono text-xs">
                    {JSON.stringify(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </section>
      </div>
    </div>
  );
};
