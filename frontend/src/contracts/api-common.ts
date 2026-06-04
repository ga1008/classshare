import { z } from 'zod';

export const ApiErrorCodeSchema = z.enum([
  'bad_request',
  'login_required',
  'permission_denied',
  'not_found',
  'validation_error',
  'conflict',
  'rate_limited',
  'payload_too_large',
  'unsupported_media_type',
  'service_unavailable',
  'upstream_error',
  'internal_error',
  'unknown_error',
]);

export type ApiErrorCode = z.infer<typeof ApiErrorCodeSchema>;

export const FlexibleRecordSchema = z.object({}).catchall(z.unknown());
export type FlexibleRecord = z.infer<typeof FlexibleRecordSchema>;

export const ApiErrorBodySchema = z.object({
  code: ApiErrorCodeSchema,
  message: z.string().min(1),
  details: z.record(z.string(), z.unknown()).optional(),
  request_id: z.string().nullable().optional(),
}).passthrough();

export const ApiErrorPayloadSchema = z.object({
  detail: z.unknown().optional(),
  code: ApiErrorCodeSchema.optional(),
  error: ApiErrorBodySchema.optional(),
}).passthrough();

export type ApiErrorPayload = z.infer<typeof ApiErrorPayloadSchema>;

export function messageFromUnknownDetail(detail: unknown): string | null {
  if (typeof detail === 'string' && detail.trim()) {
    return detail.trim();
  }
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>;
    for (const key of ['message', 'error', 'reason', 'detail']) {
      const value = record[key];
      if (typeof value === 'string' && value.trim()) {
        return value.trim();
      }
    }
  }
  return null;
}

export function parseApiErrorPayload(payload: unknown): {
  code?: ApiErrorCode;
  message?: string;
  details?: Record<string, unknown>;
  requestId?: string | null;
} {
  const parsed = ApiErrorPayloadSchema.safeParse(payload);
  if (!parsed.success) {
    return {
      message: messageFromUnknownDetail(payload) ?? undefined,
    };
  }

  const error = parsed.data.error;
  return {
    code: error?.code ?? parsed.data.code,
    message: error?.message ?? messageFromUnknownDetail(parsed.data.detail) ?? undefined,
    details: error?.details,
    requestId: error?.request_id ?? undefined,
  };
}

export const ApiSuccessStatusSchema = z.literal('success');
