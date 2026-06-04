import { z } from 'zod';

import { ApiSuccessStatusSchema, FlexibleRecordSchema } from './api-common';

export const MaterialLibraryResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  current_folder: FlexibleRecordSchema.nullish(),
  breadcrumbs: z.array(FlexibleRecordSchema),
  items: z.array(FlexibleRecordSchema),
  stats: FlexibleRecordSchema,
  filters: FlexibleRecordSchema,
  facets: FlexibleRecordSchema,
  overview: FlexibleRecordSchema,
}).passthrough();

export const MaterialDetailResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  material: FlexibleRecordSchema,
}).passthrough();

export const MaterialAiImportActiveResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  tasks: z.array(FlexibleRecordSchema),
  poll_interval_ms: z.number().int().positive(),
}).passthrough();

export const MaterialAiImportStatusResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  task: FlexibleRecordSchema,
}).passthrough();

export const ClassroomMaterialsResponseSchema = z.object({
  status: ApiSuccessStatusSchema,
  current_folder: FlexibleRecordSchema.nullish(),
  breadcrumbs: z.array(FlexibleRecordSchema),
  items: z.array(FlexibleRecordSchema),
}).passthrough();

export type MaterialLibraryResponse = z.infer<typeof MaterialLibraryResponseSchema>;
export type MaterialDetailResponse = z.infer<typeof MaterialDetailResponseSchema>;
export type MaterialAiImportActiveResponse = z.infer<typeof MaterialAiImportActiveResponseSchema>;
export type ClassroomMaterialsResponse = z.infer<typeof ClassroomMaterialsResponseSchema>;

