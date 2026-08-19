import type { GraphSchema } from "../../contracts/graphAnalyzer";

export type SchemaChange = {
  readonly id: string;
  readonly type: string;
  readonly label: string;
};

export function schemaChanges(existing: GraphSchema | null, proposed: GraphSchema): readonly SchemaChange[] {
  const changes: SchemaChange[] = proposed.entities
    .filter((entity) => entity.change !== "UNCHANGED")
    .map((entity) => ({ id: entity.id, type: entity.change, label: `Entity ${entity.name}` }));
  changes.push(...proposed.relationships
    .filter((relationship) => relationship.change !== "UNCHANGED")
    .map((relationship) => ({ id: relationship.id, type: relationship.change, label: `Relationship ${relationship.name}` })));
  for (const entity of existing?.entities ?? []) {
    if (!proposed.entities.some((candidate) => candidate.id === entity.id)) changes.push({ id: entity.id, type: "REMOVED", label: `Entity ${entity.name}` });
  }
  for (const relationship of existing?.relationships ?? []) {
    if (!proposed.relationships.some((candidate) => candidate.id === relationship.id)) changes.push({ id: relationship.id, type: "REMOVED", label: `Relationship ${relationship.name}` });
  }
  return changes;
}
