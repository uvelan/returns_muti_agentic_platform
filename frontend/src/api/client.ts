import type { APIResponse, PageMeta, ResponseMeta } from "../contracts/api";


type ErrorDetails = {
  readonly message?: string;
  readonly correlationId?: string;
};


export class APIError extends Error {
  public readonly status: number;
  public readonly correlationId: string | undefined;
  /**
   * The refusal's own `detail` object, when it sent one.
   *
   * `message` is what a person is shown; this is what a caller can *branch* on.
   * The review endpoints put the review's state and the field that moved in
   * here, because a UI that only has "409" can offer nothing but "try again",
   * while one that has `{code: "ReviewStateError", state: "APPROVING"}` can say
   * "this review is already being sent" and hide the button.
   *
   * Deliberately `unknown`: every router on this platform raises its own detail
   * shape and typing it here would make this file the place they are all
   * written down. Callers narrow it.
   */
  public readonly detail: unknown;

  public constructor(
    message: string,
    status: number,
    correlationId?: string,
    options?: ErrorOptions & { readonly detail?: unknown },
  ) {
    super(message, options);

    this.name = "APIError";
    this.status = status;
    this.correlationId = correlationId;
    this.detail = options?.detail;
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

  const detail = payload.detail;
  if (typeof detail === "string") {
    return { message: detail };
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (!isRecord(item) || typeof item.msg !== "string") {
        return [];
      }
      const location = Array.isArray(item.loc)
        ? item.loc.filter((part): part is string | number => (
          typeof part === "string" || typeof part === "number"
        )).join(".")
        : "";
      return [`${location ? `${location}: ` : ""}${item.msg}`];
    });
    if (messages.length > 0) {
      return { message: messages.join(" ") };
    }
  }

  // `{code, message}` — the shape most of this platform's routers raise, and
  // the one that was falling through to "The API request failed with status
  // 502". An operator was shown a status code for a refusal that had already
  // explained itself: "this return is already closed", "release has not been
  // published", "no source binding for that dataset".
  if (isRecord(detail) && typeof detail.message === "string") {
    return { message: detail.message };
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


type EnvelopeShape =
  Record<string, unknown>
  & { readonly meta: Record<string, unknown> };


/**
 * Is this the platform envelope?
 *
 * `meta` alone decides it. Both `data` and `page` are optional in the contract —
 * the backend models them as `T | None = None`, so the generated schema marks
 * neither as required — and an envelope carrying no `data` is a legitimate
 * "nothing to report", not a malformed response.
 *
 * This still rejects the bare, non-enveloped body the check exists for: such a
 * body has no conforming `meta`, which is what fails it.
 */
function isApiResponseEnvelope(
  payload: unknown,
): payload is EnvelopeShape {
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
      // The raw detail, alongside the message extracted from it. See
      // `APIError.detail`.
      { detail: isRecord(payload) ? payload.detail : undefined },
    );
  }

  if (!isApiResponseEnvelope(payload)) {
    throw new APIError(
      "The server returned an invalid API response envelope.",
      response.status,
      headerCorrelationId,
    );
  }

  // `data` and `page` are optional on the wire but non-optional on
  // `APIResponse<T>`. Settle the absent case to `null` here, once, so the value
  // handed to callers matches the type they are given and a caller that checks
  // `data === null` sees the same thing as one that writes `data ?? fallback`.
  return {
    data: (payload.data ?? null) as T | null,
    page: (payload.page ?? null) as PageMeta | null,
    meta: payload.meta as unknown as ResponseMeta,
  };
}
