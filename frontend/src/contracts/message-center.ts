import { z } from 'zod';

import { ApiSuccessStatusSchema, FlexibleRecordSchema } from './api-common';

export const MessageCenterSummaryResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  summary: FlexibleRecordSchema,
  latest_unread: FlexibleRecordSchema.nullish(),
}).passthrough();

export const MessageCenterItemsResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  items: z.array(FlexibleRecordSchema),
}).passthrough();

export const MessageCenterMarkReadResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  updated_count: z.number().int().nonnegative(),
  summary: FlexibleRecordSchema,
}).passthrough();

export const PrivateContactsResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  contacts: z.array(FlexibleRecordSchema),
}).passthrough();

export const PrivateConversationResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  conversation: FlexibleRecordSchema,
  summary: FlexibleRecordSchema,
}).passthrough();

export const PrivateAiReplyJobResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  job: FlexibleRecordSchema,
}).passthrough();

export type MessageCenterSummaryResponse = z.infer<typeof MessageCenterSummaryResponseSchema>;
export type MessageCenterItemsResponse = z.infer<typeof MessageCenterItemsResponseSchema>;
export type PrivateConversationResponse = z.infer<typeof PrivateConversationResponseSchema>;

