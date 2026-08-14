import { Component, useState, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Database, Fingerprint, Network, TableProperties } from "lucide-react";

import type { DraftShapeView, EntityShapeView } from "../../api/graphSchema";
import { buildFlowElements, type SchemaFlowNode } from "./schemaFlowModel";

function EntityNode({ data }: NodeProps<SchemaFlowNode>) {
  const entity = data.entity;
  const properties = entity === null ? [] : Object.entries(entity.properties);

  return (
    <div className={[
      "w-60 overflow-hidden rounded-2xl border bg-surface-container-lowest shadow-panel",
      data.referenced ? "border-dashed border-outline" : "border-outline-variant",
    ].join(" ")}
    >
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={false}
        className="!size-2 !border-primary !bg-inverse-primary"
      />
      <div className="flex items-center justify-between gap-3 border-b border-outline-variant/70 px-3 py-2.5">
        <span className="truncate text-sm font-semibold text-on-surface">{data.label}</span>
        <span className={[
          "rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide",
          data.referenced
            ? "bg-surface-container-high text-outline"
            : "bg-secondary-container text-on-secondary-container",
        ].join(" ")}
        >
          {data.referenced ? "Referenced" : entity?.sync_mode ?? "Entity"}
        </span>
      </div>

      {entity === null ? (
        <p className="px-3 py-3 text-xs leading-5 text-outline">
          Relationship endpoint; entity details are unavailable in this draft.
        </p>
      ) : (
        <>
          <div className="flex items-center gap-1.5 border-b border-outline-variant/60 px-3 py-2 text-[10px] text-outline">
            <Database size={11} aria-hidden="true" />
            <span className="truncate">{entity.source_dataset}</span>
          </div>
          <ul className="space-y-1 px-3 py-2.5">
            {properties.slice(0, 5).map(([name, property]) => (
              <li key={name} className="flex items-center justify-between gap-3 text-[10px]">
                <span className={entity.identifier_properties.includes(name)
                  ? "flex items-center gap-1 font-semibold text-primary"
                  : "text-on-surface-variant"}
                >
                  {entity.identifier_properties.includes(name) ? (
                    <Fingerprint size={10} aria-label="Identifier" />
                  ) : null}
                  {name}
                </span>
                <span className="font-mono text-outline">{property.type}</span>
              </li>
            ))}
            {properties.length > 5 ? (
              <li className="pt-1 text-[10px] font-medium text-primary">
                +{properties.length - 5} more properties
              </li>
            ) : null}
          </ul>
        </>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={false}
        className="!size-2 !border-primary !bg-inverse-primary"
      />
    </div>
  );
}

class SchemaCanvasBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? (
      <p role="alert" className="m-4 rounded-xl border border-outline-variant bg-surface-container-lowest p-4 text-sm text-on-surface-variant">
        The visual canvas is unavailable. Use Details for the complete schema.
      </p>
    ) : this.props.children;
  }
}

const NODE_TYPES = { schemaEntity: EntityNode };

export function SchemaFlow({ shape }: { shape: DraftShapeView }) {
  const [view, setView] = useState<"graph" | "details">("graph");
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const { nodes, edges } = buildFlowElements(shape);
  const selected = selectedLabel === null
    ? null
    : nodes.find((node) => node.id === selectedLabel) ?? null;

  return (
    <div className="mt-4 overflow-hidden rounded-2xl border border-outline-variant/80 bg-surface-container-low">
      <div className="flex items-center justify-between gap-4 border-b border-outline-variant/80 bg-surface-container-lowest px-4 py-3">
        <div>
          <p className="premium-kicker">Draft topology</p>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            {Object.keys(shape.entities).length} entities | {shape.relationships.length} relationships
          </p>
        </div>
        <div className="flex overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
          <button
            type="button"
            aria-pressed={view === "graph"}
            onClick={() => { setView("graph"); }}
            className={[
              "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium",
              view === "graph" ? "bg-primary text-on-primary" : "text-on-surface-variant",
            ].join(" ")}
          >
            <Network size={13} aria-hidden="true" />
            Graph
          </button>
          <button
            type="button"
            aria-pressed={view === "details"}
            onClick={() => { setView("details"); }}
            className={[
              "flex items-center gap-1.5 border-l border-outline-variant px-3 py-1.5 text-xs font-medium",
              view === "details" ? "bg-primary text-on-primary" : "text-on-surface-variant",
            ].join(" ")}
          >
            <TableProperties size={13} aria-hidden="true" />
            Details
          </button>
        </div>
      </div>

      {view === "graph" ? (
        <div role="region" className="relative h-[34rem] min-h-[30rem]" aria-label="Schema relationship graph">
          {typeof ResizeObserver === "undefined" ? (
            <p
              role="alert"
              className="m-4 rounded-xl border border-outline-variant bg-surface-container-lowest p-4 text-sm text-on-surface-variant"
            >
              The visual canvas is unavailable. Use Details for the complete schema.
            </p>
          ) : (
            <SchemaCanvasBoundary>
              <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            fitView
            fitViewOptions={{ padding: 0.22 }}
            minZoom={0.25}
            maxZoom={1.8}
            nodesConnectable={false}
            edgesReconnectable={false}
            deleteKeyCode={null}
            onNodeClick={(_event, node) => { setSelectedLabel(node.id); }}
            onPaneClick={() => { setSelectedLabel(null); }}
            proOptions={{ hideAttribution: true }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={18}
              size={1}
              color="#bec9c6"
            />
            <MiniMap
              pannable
              zoomable
              nodeColor={(node) => node.data.referenced === true ? "#e0e3e1" : "#c5e6e1"}
              maskColor="rgb(247 250 248 / 0.72)"
              className="!rounded-xl !border !border-outline-variant !bg-surface-container-lowest"
            />
            <Controls className="!overflow-hidden !rounded-xl !border-outline-variant !shadow-sm" />
              </ReactFlow>
            </SchemaCanvasBoundary>
          )}

          {selected === null ? null : (
            <EntityInspector
              label={selected.data.label}
              entity={selected.data.entity}
              onClose={() => { setSelectedLabel(null); }}
            />
          )}
        </div>
      ) : null}

      <SchemaInventory
        shape={shape}
        className={view === "details"
          ? "grid grid-cols-[minmax(0,1fr)_18rem] gap-4 p-4"
          : "sr-only"}
      />
    </div>
  );
}

function EntityInspector({
  label,
  entity,
  onClose,
}: {
  label: string;
  entity: EntityShapeView | null;
  onClose: () => void;
}) {
  return (
    <aside className="absolute right-4 top-4 z-10 w-72 rounded-2xl border border-outline-variant bg-surface-container-lowest p-4 shadow-float">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="premium-kicker">Entity inspector</p>
          <h3 className="mt-1 text-base font-semibold text-on-surface">{label}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-outline-variant px-2 py-1 text-xs text-on-surface-variant"
        >
          Close
        </button>
      </div>
      {entity === null ? (
        <p className="mt-3 text-sm leading-6 text-on-surface-variant">
          This endpoint is referenced by a relationship but is not defined in the current shape.
        </p>
      ) : (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg bg-surface-container-low p-2">
              <dt className="text-outline">Dataset</dt>
              <dd className="mt-1 truncate font-medium text-on-surface">{entity.source_dataset}</dd>
            </div>
            <div className="rounded-lg bg-surface-container-low p-2">
              <dt className="text-outline">Sync</dt>
              <dd className="mt-1 font-medium text-on-surface">{entity.sync_mode}</dd>
            </div>
            <div className="rounded-lg bg-surface-container-low p-2">
              <dt className="text-outline">Ownership</dt>
              <dd className="mt-1 font-medium text-on-surface">{entity.ownership}</dd>
            </div>
            <div className="rounded-lg bg-surface-container-low p-2">
              <dt className="text-outline">Properties</dt>
              <dd className="mt-1 font-medium text-on-surface">{Object.keys(entity.properties).length}</dd>
            </div>
          </dl>
          <ul className="mt-3 max-h-64 space-y-1 overflow-y-auto">
            {Object.entries(entity.properties).map(([name, property]) => (
              <li key={name} className="rounded-lg border border-outline-variant/70 px-2.5 py-2 text-xs">
                <div className="flex justify-between gap-2">
                  <span className="font-medium text-on-surface">{name}</span>
                  <span className="font-mono text-outline">{property.type}</span>
                </div>
                <p className="mt-1 truncate text-[10px] text-outline">
                  {property.source_field}
                  {property.transformation === "NONE" ? "" : ` | ${property.transformation}`}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}

function SchemaInventory({
  shape,
  className,
}: {
  shape: DraftShapeView;
  className: string;
}) {
  const entities = Object.entries(shape.entities);
  return (
    <div className={className}>
      <div>
        <h3 className="premium-kicker">Entities and properties</h3>
        <ul className="mt-2 grid grid-cols-2 gap-3">
          {entities.map(([label, entity]) => (
            <li key={label} className="rounded-xl border border-outline-variant bg-surface-container-lowest p-3">
              <p className="text-sm font-semibold text-on-surface">{label}</p>
              <p className="mt-0.5 text-xs text-outline">
                from {entity.source_dataset}
              </p>
              <ul className="mt-2 flex flex-col gap-1">
                {Object.entries(entity.properties).map(([name, property]) => (
                  <li key={name} className="flex justify-between gap-2 text-xs">
                    <span className={entity.identifier_properties.includes(name)
                      ? "font-semibold text-primary"
                      : "text-on-surface-variant"}
                    >
                      {name}{entity.identifier_properties.includes(name) ? " (id)" : ""}
                    </span>
                    <span className="font-mono text-outline">{property.type}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="premium-kicker">Relationships</h3>
        {shape.relationships.length === 0 ? (
          <p className="mt-2 text-sm text-on-surface-variant">No relationships defined.</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {shape.relationships.map((edge, index) => (
              <li
                key={`${edge.from_label}-${edge.relationship_type}-${edge.to_label}-${String(index)}`}
                className="rounded-xl border border-outline-variant bg-surface-container-lowest p-3 text-xs text-on-surface-variant"
              >
                <span className="font-semibold text-on-surface">{edge.from_label}</span>
                <span aria-hidden="true"> &rarr; </span>
                <span className="sr-only"> to </span>
                <span className="font-semibold text-on-surface">{edge.to_label}</span>
                <span className="mt-1 block font-mono text-primary">{edge.relationship_type}</span>
                <span className="mt-1 block text-outline">{edge.cardinality}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
