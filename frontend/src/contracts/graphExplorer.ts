import { z } from "zod";

export const GraphNodeSchema = z.object({
  id: z.string(),
  labels: z.array(z.string()),
  properties: z.record(z.unknown()), // Unknown payload validated/redacted at render time
  truncated: z.boolean().optional(), // Metadata indicating properties were truncated
  provenance: z.object({
    source_id: z.string().optional(),
    document_id: z.string().optional(),
  }).nullish(),
  ownership: z.object({
    owner: z.string().optional(),
  }).nullish()
});
export type GraphNode = z.infer<typeof GraphNodeSchema>;

export const GraphRelationshipSchema = z.object({
  id: z.string(),
  type: z.string(),
  startNodeId: z.string(),
  endNodeId: z.string(),
  properties: z.record(z.unknown()),
  truncated: z.boolean().optional()
});
export type GraphRelationship = z.infer<typeof GraphRelationshipSchema>;

export const GraphExpansionLimitSchema = z.object({
  maxNodes: z.number(),
  maxRelationships: z.number(),
  maxDepth: z.number(),
  expansionLimit: z.number()
});
export type GraphExpansionLimit = z.infer<typeof GraphExpansionLimitSchema>;

export const GraphSearchResultSchema = z.object({
  data: z.object({
    nodes: z.array(GraphNodeSchema),
    relationships: z.array(GraphRelationshipSchema)
  }),
  meta: z.object({
    schema_version: z.string(),
    request_id: z.string(),
    generated_at: z.string(),
    limits: GraphExpansionLimitSchema.optional(),
    isPartial: z.boolean().optional(),
    isTruncated: z.boolean().optional(),
    warnings: z.array(z.any()).optional()
  })
});
export type GraphSearchResult = z.infer<typeof GraphSearchResultSchema>;
