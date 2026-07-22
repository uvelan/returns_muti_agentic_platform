import { z } from "zod";

const sha256Schema = z.string().regex(/^[0-9a-f]{64}$/);
const timestampSchema = z.string().datetime({ offset: true });
const nonnegativeIntegerSchema = z.number().int().nonnegative();

const warningSchema = z.object({
  source: z.string().min(1),
  code: z.string().min(1),
  message: z.string().min(1),
}).strict();

export const responseMetaSchema = z.object({
  schema_version: z.string().min(1),
  request_id: z.string().min(1),
  generated_at: timestampSchema,
  freshness: z.enum(["LIVE", "CACHED", "STALE"]),
  partial: z.boolean(),
  warnings: z.array(warningSchema),
}).strict();

export const pageMetaSchema = z.object({
  next_cursor: z.string().min(1).max(512).nullable(),
  has_more: z.boolean(),
  page_size: z.number().int().min(1).max(100),
}).strict();

export const graphEvidenceSummarySchema = z.object({
  schema_version: z.string().min(1),
  evidence_type: z.literal("CUSTOMER_GRAPH_SANDBOX_RUN"),
  document_id: z.string().regex(/^CUSTOMER_GRAPH_SANDBOX:[0-9a-f-]{36}$/),
  report_digest: sha256Schema,
  document_digest: sha256Schema,
  sync_run_id: z.string().uuid(),
  executed_at: timestampSchema,
  executed_at_epoch_microseconds: nonnegativeIntegerSchema,
  source_document_id: z.string().min(1),
  source_hash: sha256Schema,
  configuration_digest: sha256Schema,
  execution_plan_digest: sha256Schema,
  command_batch_digest: sha256Schema,
  evidence_classification: z.literal("SANDBOX_VALIDATED"),
  expected_customer_count: nonnegativeIntegerSchema,
  expected_customer_account_count: nonnegativeIntegerSchema,
  expected_relationship_count: nonnegativeIntegerSchema,
  idempotent: z.boolean(),
}).strict().superRefine((value, context) => {
  if (value.document_id !== `CUSTOMER_GRAPH_SANDBOX:${value.sync_run_id}`) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: "Document and sync-run identities do not match.",
      path: ["document_id"],
    });
  }
});

const jsonValueSchema: z.ZodType<unknown> = z.lazy(() => z.union([
  z.string(), z.number().finite(), z.boolean(), z.null(),
  z.array(jsonValueSchema), z.record(jsonValueSchema),
]));

export const graphEvidenceFullSchema = z.object({
  summary: graphEvidenceSummarySchema,
  schema_evidence_digest: sha256Schema,
  first_write_evidence_digest: sha256Schema,
  second_write_evidence_digest: sha256Schema,
  first_readback_evidence_digest: sha256Schema,
  second_readback_evidence_digest: sha256Schema,
  idempotency_evidence_digest: sha256Schema,
  report_payload: z.record(jsonValueSchema),
}).strict();

export type GraphEvidenceSummary = z.infer<typeof graphEvidenceSummarySchema>;
export type GraphEvidenceFull = z.infer<typeof graphEvidenceFullSchema>;
