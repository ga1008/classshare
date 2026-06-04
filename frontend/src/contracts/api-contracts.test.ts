import { describe, expect, it } from 'vitest';

import { parseApiErrorPayload } from './api-common';
import { AssignmentDraftResponseSchema, AssignmentTimeStateResponseSchema } from './homework';
import { MaterialAiImportActiveResponseSchema, MaterialLibraryResponseSchema } from './materials';
import { MessageCenterItemsResponseSchema, MessageCenterSummaryResponseSchema } from './message-center';

describe('API contract schemas', () => {
  it('parses structured API errors with enumerable codes', () => {
    const parsed = parseApiErrorPayload({
      detail: { message: 'Too many messages', retry_after_seconds: 9 },
      code: 'rate_limited',
      error: {
        code: 'rate_limited',
        message: 'Too many messages',
        details: { retry_after_seconds: 9 },
        request_id: 'req-1',
      },
    });

    expect(parsed).toEqual({
      code: 'rate_limited',
      message: 'Too many messages',
      details: { retry_after_seconds: 9 },
      requestId: 'req-1',
    });
  });

  it('keeps message center response shapes explicit', () => {
    expect(MessageCenterSummaryResponseSchema.parse({
      status: 'success',
      summary: { unread_total: 2, private_unread_count: 1 },
      latest_unread: null,
    }).summary.unread_total).toBe(2);

    expect(MessageCenterItemsResponseSchema.parse({
      status: 'success',
      items: [{ id: 1, category: 'assignment', title: 'Homework' }],
    }).items).toHaveLength(1);
  });

  it('keeps homework response shapes explicit', () => {
    expect(AssignmentTimeStateResponseSchema.parse({
      status: 'success',
      server_now: '2026-06-04T08:00:00+08:00',
      assignments: [{ id: 10, status: 'published', effective_status: 'published' }],
    }).assignments[0].id).toBe(10);

    expect(AssignmentDraftResponseSchema.parse({
      exists: false,
      answers_json: '',
      current_page: 0,
      client_updated_at: '',
      server_updated_at: '',
      server_version: 0,
      files: [],
      files_by_question: {},
    }).exists).toBe(false);
  });

  it('keeps materials response shapes explicit', () => {
    expect(MaterialLibraryResponseSchema.parse({
      status: 'success',
      current_folder: null,
      breadcrumbs: [],
      items: [{ id: 1, name: 'README.md', node_type: 'file' }],
      stats: {},
      filters: {},
      facets: {},
      overview: {},
    }).items[0].name).toBe('README.md');

    expect(MaterialAiImportActiveResponseSchema.parse({
      status: 'success',
      tasks: [{ id: 7, parse_status: 'queued' }],
      poll_interval_ms: 3500,
    }).poll_interval_ms).toBe(3500);
  });
});

