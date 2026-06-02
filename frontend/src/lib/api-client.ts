import { z, type ZodType } from 'zod';

export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(message: string, status: number, payload: unknown = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

export type JsonBody = Record<string, unknown> | unknown[];
export type ApiRequestInit = Omit<RequestInit, 'body'> & {
  body?: BodyInit | JsonBody | null;
};

function isJsonBody(value: unknown): value is JsonBody {
  return (
    (Array.isArray(value) || (typeof value === 'object' && value !== null))
    && !(typeof FormData !== 'undefined' && value instanceof FormData)
    && !(typeof Blob !== 'undefined' && value instanceof Blob)
    && !(typeof ArrayBuffer !== 'undefined' && value instanceof ArrayBuffer)
    && !(ArrayBuffer.isView(value))
    && !(typeof URLSearchParams !== 'undefined' && value instanceof URLSearchParams)
    && !(typeof ReadableStream !== 'undefined' && value instanceof ReadableStream)
  );
}

function normalizeHeaders(headers: HeadersInit | undefined, hasBody: boolean): Headers {
  const normalized = new Headers(headers);
  if (hasBody && !normalized.has('Content-Type')) {
    normalized.set('Content-Type', 'application/json');
  }
  normalized.set('Accept', 'application/json');
  return normalized;
}

async function parseResponsePayload(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }

  const text = await response.text();
  return text || null;
}

function errorMessageFromPayload(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>;
    const detail = record.detail || record.message || record.error;
    if (typeof detail === 'string' && detail.trim()) {
      return detail.trim();
    }
  }
  if (typeof payload === 'string' && payload.trim()) {
    return payload.trim();
  }
  return fallback;
}

export async function apiRequest<T>(
  input: RequestInfo | URL,
  schema: ZodType<T>,
  init: ApiRequestInit = {},
): Promise<T> {
  const hasJsonBody = isJsonBody(init.body);
  const body: BodyInit | null | undefined = hasJsonBody
    ? JSON.stringify(init.body)
    : (init.body as BodyInit | null | undefined);
  const response = await fetch(input, {
    credentials: 'same-origin',
    ...init,
    body,
    headers: normalizeHeaders(init.headers, body !== undefined && !(body instanceof FormData)),
  });
  const payload = await parseResponsePayload(response);

  if (!response.ok) {
    throw new ApiError(
      errorMessageFromPayload(payload, `请求失败 (${response.status})`),
      response.status,
      payload,
    );
  }

  return schema.parse(payload);
}

export const EmptyResponseSchema = z.null();
