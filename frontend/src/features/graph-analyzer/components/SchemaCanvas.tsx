import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { KeyRound, Lock, Search, Zap } from "lucide-react";
import type { GraphEntity, GraphSchema } from "../../../contracts/graphAnalyzer";

/**
 * The system graph canvas.
 *
 * Built on `@xyflow/react`, which the repository already depends on and which
 * the retired `SchemaFlow` used: it brings real panning, wheel zoom, fit-to-view
 * and a minimap. The hand-rolled predecessor scaled a `div` and offered no pan
 * at all, so any graph larger than the viewport had regions the user could not
 * reach.
 *
 * Progressive disclosure is deliberate. A node shows its name, change state and
 * a count summary; identifiers, indexes and constraints appear as compact marks
 * rather than as a full property list, because rendering every property on every
 * node is what makes a schema graph unreadable.
 */

type EntityNodeData = {
  [key: string]: unknown;
  readonly entity: GraphEntity;
  readonly dimmed: boolean;
  /** Selecting the entity. On the data because a node type takes no props. */
  readonly onActivate: (entityId: string) => void;
};

type EntityNode = Node<EntityNodeData, "analyzerEntity">;

const CHANGE_STYLES: Record<GraphEntity["change"], string> = {
  ADDED: "border-emerald-500/80",
  CHANGED: "border-amber-500/80",
  REMOVED: "border-red-600/80",
  UNCHANGED: "border-analyzer-outline-variant",
};

/**
 * One entity on the canvas, as a control rather than a decorated `<div>`.
 *
 * React Flow makes its node wrapper a tab stop, and its keydown handler
 * responds to arrow keys and Escape only -- there is no Enter. So an entity
 * could be focused and never selected, and the inspector beside the canvas was
 * unreachable without a mouse. The library also declares
 * `.react-flow__node:focus-visible { outline: none }`, which out-specifies the
 * app's global focus ring, so the focus was invisible as well as inert.
 *
 * `nodesFocusable={false}` on the canvas hands the tab stop to this button
 * instead: one stop per entity, with Enter and Space for free and a focus ring
 * that nothing overrides.
 */
function EntityNodeView({ data, selected }: NodeProps<EntityNode>) {
  const { entity, dimmed, onActivate } = data;
  const identifiers = entity.properties.filter((property) => property.identifier).length;
  const indexes = entity.properties.filter((property) => property.indexed).length;
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={() => { onActivate(entity.id); }}
      className={`block w-52 overflow-hidden rounded-xl border bg-analyzer-surface-container text-left shadow-xl transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 focus-visible:ring-offset-2 focus-visible:ring-offset-analyzer-surface ${CHANGE_STYLES[entity.change]} ${
        selected ? "ring-2 ring-analyzer-primary ring-offset-2 ring-offset-analyzer-surface" : ""
      } ${dimmed ? "opacity-25" : "opacity-100"}`}
    >
      {/* Connectable is off everywhere: relationships are edited in the inspector,
          never by dragging a wire, so a handle that accepts a drag would create a
          relationship no backend call was made for. */}
      <Handle type="target" position={Position.Left} isConnectable={false} className="!bg-emerald-700" />
      <div className="border-b border-analyzer-outline-variant bg-analyzer-primary-container/40 px-3 py-2">
        <p className="truncate text-sm font-semibold text-emerald-100">{entity.name}</p>
        <p className="text-[9px] uppercase tracking-wider text-analyzer-on-surface-variant">{entity.change}</p>
      </div>
      <div className="space-y-1.5 px-3 py-2">
        <p className="text-[11px] text-analyzer-on-surface-variant">{entity.properties.length} properties</p>
        <div className="flex flex-wrap gap-1.5 text-[10px]">
          {identifiers > 0 ? (
            <span className="inline-flex items-center gap-1 rounded bg-violet-950 px-1.5 py-0.5 text-violet-300">
              <KeyRound size={9} />
              {identifiers}
            </span>
          ) : null}
          {indexes > 0 ? (
            <span className="inline-flex items-center gap-1 rounded bg-sky-950 px-1.5 py-0.5 text-sky-300">
              <Zap size={9} />
              {indexes}
            </span>
          ) : null}
          {entity.constraints.length > 0 ? (
            <span className="inline-flex items-center gap-1 rounded bg-analyzer-primary-container px-1.5 py-0.5 text-analyzer-accent">
              <Lock size={9} />
              {entity.constraints.length}
            </span>
          ) : null}
        </div>
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} className="!bg-emerald-700" />
    </button>
  );
}

const NODE_TYPES = { analyzerEntity: EntityNodeView } as const;

export function SchemaCanvas({
  schema,
  selectedId,
  onSelect,
}: {
  readonly schema: GraphSchema;
  readonly selectedId: string | null;
  readonly onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();

  const matches = useMemo(
    () =>
      new Set(
        normalized.length === 0
          ? []
          : schema.entities
              .filter((entity) => entity.name.toLowerCase().includes(normalized))
              .map((entity) => entity.id),
      ),
    [schema.entities, normalized],
  );

  const nodes = useMemo<EntityNode[]>(
    () =>
      schema.entities.map((entity) => ({
        id: entity.id,
        type: "analyzerEntity" as const,
        // `x`/`y` are percentages of the canvas from the backend; React Flow works
        // in absolute units, so they are scaled once here rather than persisted.
        position: { x: entity.x * 9, y: entity.y * 6 },
        data: { entity, dimmed: normalized.length > 0 && !matches.has(entity.id), onActivate: onSelect },
        selected: entity.id === selectedId,
      })),
    [schema.entities, matches, normalized, selectedId, onSelect],
  );

  const edges = useMemo<Edge[]>(
    () =>
      schema.relationships.map((relationship) => {
        // Direction is a property of the relationship, not of the drawing: an
        // INBOUND relationship points at its declared `from` entity, so the arrow
        // has to be reversed rather than the two ids swapped in the model.
        const forward = relationship.direction === "OUTBOUND";
        return {
          id: relationship.id,
          source: forward ? relationship.fromEntityId : relationship.toEntityId,
          target: forward ? relationship.toEntityId : relationship.fromEntityId,
          label: relationship.name,
          selected: relationship.id === selectedId,
          markerEnd: { type: MarkerType.ArrowClosed, color: "#34d399" },
          style: {
            stroke: relationship.id === selectedId ? "#34d399" : "#245443",
            strokeWidth: relationship.id === selectedId ? 2.5 : 1.5,
          },
          labelStyle: { fill: "#94a3b8", fontSize: 10 },
          labelBgStyle: { fill: "#0a1714" },
        } satisfies Edge;
      }),
    [schema.relationships, selectedId],
  );

  return (
    <div className="relative h-[560px] overflow-hidden rounded-xl border border-analyzer-outline-variant bg-analyzer-surface">
      {typeof ResizeObserver === "undefined" ? (
        <p role="alert" className="m-4 rounded-lg border border-analyzer-outline p-4 text-sm text-analyzer-on-surface-variant">
          The visual canvas is unavailable in this environment. Use the Changes view for the
          complete schema.
        </p>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.2}
          maxZoom={1.8}
          nodesConnectable={false}
          // The tab stop belongs to the button inside each node. Left on, React
          // Flow adds its own wrapper stop that responds to arrows and Escape
          // but not Enter -- focusable and unselectable, twice per entity.
          nodesFocusable={false}
          edgesReconnectable={false}
          deleteKeyCode={null}
          onNodeClick={(_event, node) => {
            onSelect(node.id);
          }}
          onEdgeClick={(_event, edge) => {
            onSelect(edge.id);
          }}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#1c3a31" />
          <MiniMap
            pannable
            zoomable
            nodeColor="#134e3a"
            maskColor="rgb(5 12 10 / 0.75)"
            className="!rounded-lg !border !border-analyzer-outline-variant !bg-analyzer-surface-sunken"
          />
          <Controls className="!overflow-hidden !rounded-lg !border-analyzer-outline-variant" />
          <CanvasSearch
            query={query}
            onQuery={setQuery}
            matchCount={matches.size}
            focusId={selectedId}
          />
        </ReactFlow>
      )}
    </div>
  );
}

/**
 * Lives inside `ReactFlow` so it can use `useReactFlow` to move the viewport --
 * selecting a validation issue or a diff row has to bring that object on screen,
 * which is only possible from within the provider the canvas creates.
 */
function CanvasSearch({
  query,
  onQuery,
  matchCount,
  focusId,
}: {
  readonly query: string;
  readonly onQuery: (value: string) => void;
  readonly matchCount: number;
  readonly focusId: string | null;
}) {
  const flow = useReactFlow();

  const focusNode = useCallback(
    (id: string) => {
      const node = flow.getNode(id);
      if (node === undefined) return;
      void flow.fitView({ nodes: [{ id }], duration: 400, maxZoom: 1.2, padding: 0.4 });
    },
    [flow],
  );

  useEffect(() => {
    if (focusId !== null) focusNode(focusId);
  }, [focusId, focusNode]);

  return (
    <div className="absolute left-3 top-3 z-10 flex items-center gap-1 rounded-lg border border-analyzer-outline-variant bg-analyzer-surface-container/95 p-1.5 shadow-xl">
      <Search size={14} className="ml-1 text-analyzer-on-surface-variant" />
      <input
        value={query}
        onChange={(event) => {
          onQuery(event.target.value);
        }}
        placeholder="Find entity"
        aria-label="Find entity"
        className="w-36 bg-transparent px-1 text-xs text-white outline-none placeholder:text-analyzer-on-surface-variant"
      />
      {query.trim().length > 0 ? (
        <span className="pr-1 text-[10px] text-analyzer-on-surface-variant">{matchCount} match</span>
      ) : null}
    </div>
  );
}
