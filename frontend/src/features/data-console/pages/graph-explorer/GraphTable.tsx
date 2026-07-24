
import { Link } from "wouter";
import type { GraphNode, GraphRelationship } from "../../../../contracts/graphExplorer";

type Props = {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
};

export const GraphTable: React.FC<Props> = ({ nodes, relationships }) => {
  return (
    <div className="space-y-8" role="region" aria-label="Graph Data Table">
      <section>
        <h3 className="text-lg font-semibold mb-4">Nodes</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse border border-gray-200">
            <caption className="sr-only">Graph Nodes</caption>
            <thead>
              <tr className="bg-gray-100">
                <th className="border border-gray-200 p-2">ID</th>
                <th className="border border-gray-200 p-2">Labels</th>
                <th className="border border-gray-200 p-2">Properties</th>
                <th className="border border-gray-200 p-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr key={node.id} className="hover:bg-gray-50">
                  <td className="border border-gray-200 p-2 font-mono text-sm">{node.id}</td>
                  <td className="border border-gray-200 p-2">
                    <div className="flex gap-1 flex-wrap">
                      {node.labels.map(l => (
                        <span key={l} className="px-2 py-0.5 bg-blue-100 text-blue-800 text-xs rounded-full">
                          {l}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="border border-gray-200 p-2 text-sm text-gray-600 truncate max-w-xs">
                    {JSON.stringify(node.properties)}
                  </td>
                  <td className="border border-gray-200 p-2 text-sm">
                    <Link href={`/data-console/graph/nodes/${encodeURIComponent(node.id)}`} className="text-blue-600 hover:underline">
                      Inspect
                    </Link>
                  </td>
                </tr>
              ))}
              {nodes.length === 0 && (
                <tr>
                  <td colSpan={4} className="border border-gray-200 p-4 text-center text-gray-500">
                    No nodes available
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h3 className="text-lg font-semibold mb-4">Relationships</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse border border-gray-200">
            <caption className="sr-only">Graph Relationships</caption>
            <thead>
              <tr className="bg-gray-100">
                <th className="border border-gray-200 p-2">ID</th>
                <th className="border border-gray-200 p-2">Type</th>
                <th className="border border-gray-200 p-2">Start Node</th>
                <th className="border border-gray-200 p-2">End Node</th>
                <th className="border border-gray-200 p-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {relationships.map((rel) => (
                <tr key={rel.id} className="hover:bg-gray-50">
                  <td className="border border-gray-200 p-2 font-mono text-sm">{rel.id}</td>
                  <td className="border border-gray-200 p-2">
                    <span className="px-2 py-0.5 bg-purple-100 text-purple-800 text-xs rounded-full">
                      {rel.type}
                    </span>
                  </td>
                  <td className="border border-gray-200 p-2 font-mono text-sm">
                    <Link href={`/data-console/graph/nodes/${encodeURIComponent(rel.startNodeId)}`} className="text-blue-600 hover:underline">
                      {rel.startNodeId}
                    </Link>
                  </td>
                  <td className="border border-gray-200 p-2 font-mono text-sm">
                    <Link href={`/data-console/graph/nodes/${encodeURIComponent(rel.endNodeId)}`} className="text-blue-600 hover:underline">
                      {rel.endNodeId}
                    </Link>
                  </td>
                  <td className="border border-gray-200 p-2 text-sm">
                    <Link href={`/data-console/graph/relationships/${encodeURIComponent(rel.id)}`} className="text-blue-600 hover:underline">
                      Inspect
                    </Link>
                  </td>
                </tr>
              ))}
              {relationships.length === 0 && (
                <tr>
                  <td colSpan={5} className="border border-gray-200 p-4 text-center text-gray-500">
                    No relationships available
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};
