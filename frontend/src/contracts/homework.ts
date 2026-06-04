import { z } from 'zod';

import { ApiSuccessStatusSchema, FlexibleRecordSchema } from './api-common';

export const AssignmentMutationResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  new_assignment_id: z.union([z.number(), z.string()]).nullish(),
  updated_assignment_id: z.union([z.number(), z.string()]).nullish(),
  deleted_assignment_id: z.union([z.number(), z.string()]).nullish(),
  assignment_status: z.string().nullish(),
  due_at: z.string().nullish(),
}).passthrough();

export const AssignmentTimeStateItemSchema = FlexibleRecordSchema.extend({
  id: z.union([z.number(), z.string()]),
  status: z.string().nullish(),
  effective_status: z.string().nullish(),
});

export const AssignmentTimeStateResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  server_now: z.string(),
  assignments: z.array(AssignmentTimeStateItemSchema),
}).passthrough();

export const AssignmentDraftResponseSchema = FlexibleRecordSchema.extend({
  exists: z.boolean(),
  answers_json: z.string(),
  current_page: z.number().int(),
  client_updated_at: z.string(),
  server_updated_at: z.string(),
  server_version: z.number().int().nonnegative(),
  files: z.array(FlexibleRecordSchema),
  files_by_question: z.record(z.string(), z.array(FlexibleRecordSchema)),
});

export const AssignmentSubmissionsResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  stats: FlexibleRecordSchema,
  submissions: z.array(FlexibleRecordSchema),
  assignment: FlexibleRecordSchema,
}).passthrough();

export const ExamPapersResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  papers: z.array(FlexibleRecordSchema),
}).passthrough();

export type AssignmentTimeStateResponse = z.infer<typeof AssignmentTimeStateResponseSchema>;
export type AssignmentDraftResponse = z.infer<typeof AssignmentDraftResponseSchema>;
export type AssignmentSubmissionsResponse = z.infer<typeof AssignmentSubmissionsResponseSchema>;

