import { z } from "zod";

import {
  graphEvidenceFullSchema,
  graphEvidenceSummarySchema,
  pageMetaSchema,
  responseMetaSchema,
} from "../contracts/graphEvidence";
import { APIError, apiClient } from "./client";

export const GRAPH_EVIDENCE_PAGE_SIZE = 25;
const REQUEST_TIMEOUT_MS = 8_000;
const BASE_PATH = "/data-console/v1/graph-evidence";

const summaryEnvelopeSchema = z.object({
  data: graphEvidenceSummarySchema,
  page: z.null(),
  meta: responseMetaSchema,
}).strict();
const listEnvelopeSchema = z.object({
  data: z.array(graphEvidenceSummarySchema),
  page: pageMetaSchema,
  meta: responseMetaSchema,
}).strict();
const fullEnvelopeSchema = z.object({
  data: graphEvidenceFullSchema,
  page: z.null(),
  meta: responseMetaSchema,
}).strict();

function requestSignal(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  return signal === undefined ? timeout : AbortSignal.any([signal, timeout]);
}

async function getAndParse<T>(
  path: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
): Promise<T> {
  const response = await apiClient<unknown>(path, { method: "GET", signal: requestSignal(signal) });
  const parsed = schema.safeParse(response);
  if (!parsed.success) {
    throw new APIError(
      "The server returned graph evidence that violates the expected contract.",
      502,
      response.meta.request_id,
      { cause: parsed.error },
    );
  }
  return parsed.data;
}

export function listGraphEvidence(cursor?: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ page_size: String(GRAPH_EVIDENCE_PAGE_SIZE) });
  if (cursor !== undefined) query.set("cursor", cursor);
  return getAndParse(`${BASE_PATH}?${query.toString()}`, listEnvelopeSchema, signal);
}

export function getLatestGraphEvidence(signal?: AbortSignal) {
  return getAndParse(`${BASE_PATH}/validation/latest`, summaryEnvelopeSchema, signal);
}

export function getGraphEvidenceByDocumentId(documentId: string, signal?: AbortSignal) {
  return getAndParse(`${BASE_PATH}/documents/${encodeURIComponent(documentId)}`, summaryEnvelopeSchema, signal);
}

export function getGraphEvidenceBySyncRunId(syncRunId: string, signal?: AbortSignal) {
  return getAndParse(`${BASE_PATH}/sync-runs/${encodeURIComponent(syncRunId)}`, summaryEnvelopeSchema, signal);
}

export function getGraphEvidenceByReportDigest(reportDigest: string, signal?: AbortSignal) {
  return getAndParse(`${BASE_PATH}/reports/${encodeURIComponent(reportDigest)}`, summaryEnvelopeSchema, signal);
}

export function getFullGraphEvidence(documentId: string, signal?: AbortSignal) {
  return getAndParse(`${BASE_PATH}/documents/${encodeURIComponent(documentId)}/full`, fullEnvelopeSchema, signal);
}
