import type { APIResponse } from "../contracts/api";


type ErrorDetails = {
  readonly message?: string;
  readonly correlationId?: string;
};


export class APIError extends Error {
  public readonly status: number;
  public readonly correlationId: string | undefined;

  public constructor(
    message: string,
    status: number,
    correlationId?: string,
    options?: ErrorOptions,
  ) {
    super(message, options);

    this.name = "APIError";
    this.status = status;
    this.correlationId = correlationId;
  }
}


function isRecord(
  value: unknown,
): value is Record<string, unknown> {
  return (
    typeof value === "object"
    && value !== null
    && !Array.isArray(value)
  );
}


function parseJsonBody(
  body: string,
): unknown {
  return JSON.parse(body) as unknown;
}


function extractErrorDetails(
  payload: unknown,
): ErrorDetails {
  if (!isRecord(payload)) {
    return {};
  }

  const meta = payload.meta;

  if (!isRecord(meta)) {
    return {};
  }

  const correlationId =
    typeof meta.request_id === "string"
      ? meta.request_id
      : undefined;

  const warnings = meta.warnings;

  if (!Array.isArray(warnings)) {
    return {
      correlationId,
    };
  }

  const firstWarning: unknown = warnings.at(0);

  if (!isRecord(firstWarning)) {
    return {
      correlationId,
    };
  }

  const message =
    typeof firstWarning.message === "string"
      ? firstWarning.message
      : undefined;

  return {
    correlationId,
    message,
  };
}


function isApiResponseEnvelope(
  payload: unknown,
): payload is APIResponse<unknown> {
  if (!isRecord(payload)) {
    return false;
  }

  const meta = payload.meta;

  if (!isRecord(meta)) {
    return false;
  }

  return (
    typeof meta.schema_version === "string"
    && typeof meta.request_id === "string"
    && typeof meta.generated_at === "string"
    && typeof meta.freshness === "string"
    && typeof meta.partial === "boolean"
    && Array.isArray(meta.warnings)
    && Object.hasOwn(payload, "data")
  );
}


async function readJsonBody(
  response: Response,
): Promise<unknown> {
  const body = await response.text();

  if (!body.trim()) {
    return undefined;
  }

  try {
    return parseJsonBody(body);
  } catch (error) {
    throw new APIError(
      "The server returned malformed JSON.",
      response.status,
      response.headers.get("X-Correlation-ID") ?? undefined,
      {
        cause: error,
      },
    );
  }
}


function createHeaders(
  requestHeaders: HeadersInit | undefined,
): Headers {
  const headers = new Headers(requestHeaders);

  if (!headers.has("Accept")) {
    headers.set(
      "Accept",
      "application/json",
    );
  }

  return headers;
}


function isAbortError(
  error: unknown,
): boolean {
  return (
    error instanceof DOMException
    && error.name === "AbortError"
  );
}


function isTimeoutError(
  error: unknown,
): boolean {
  return (
    error instanceof DOMException
    && error.name === "TimeoutError"
  );
}


/**
 * Execute a request against the Return Platform API.
 *
 * Callers use relative paths so development and production routing can
 * resolve the backend without exposing infrastructure addresses in the
 * browser bundle.
 */
export async function apiClient<T>(
  path: string,
  init: RequestInit = {},
): Promise<APIResponse<T>> {
  const headers = createHeaders(init.headers);

  let response: Response;

  try {
    response = await fetch(path, {
      ...init,
      headers,
    });
  } catch (error) {
    if (isTimeoutError(error)) {
      throw new APIError(
        "The API request timed out.",
        0,
        undefined,
        {
          cause: error,
        },
      );
    }

    if (isAbortError(error)) {
      throw new APIError(
        "The API request was cancelled.",
        0,
        undefined,
        {
          cause: error,
        },
      );
    }

    throw new APIError(
      "Unable to reach the Return Platform API.",
      0,
      undefined,
      {
        cause: error,
      },
    );
  }

  const headerCorrelationId =
    response.headers.get("X-Correlation-ID")
    ?? undefined;

  let payload: unknown;

  try {
    payload = await readJsonBody(response);
  } catch (error) {
    if (error instanceof APIError) {
      throw error;
    }

    throw new APIError(
      "Unable to read the API response.",
      response.status,
      headerCorrelationId,
      {
        cause: error,
      },
    );
  }

  if (!response.ok) {
    const errorDetails = extractErrorDetails(
      payload,
    );

    throw new APIError(
      errorDetails.message
        ?? (
          "The API request failed with status "
          + String(response.status)
          + "."
        ),
      response.status,
      errorDetails.correlationId
        ?? headerCorrelationId,
    );
  }

  if (!isApiResponseEnvelope(payload)) {
    throw new APIError(
      "The server returned an invalid API response envelope.",
      response.status,
      headerCorrelationId,
    );
  }

  return payload as APIResponse<T>;
}
