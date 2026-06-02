import { describe, expect, it } from 'vitest';

import {
  answerHasDisplayContent,
  collectSubmissionJumpGroups,
  summarizeSubmissionJumpGroups,
  truncateJumpText,
} from '@/lib/submission-jump-nav';

describe('submission jump nav helpers', () => {
  it('groups exam answers by paper pages and carries unmatched answers', () => {
    const groups = collectSubmissionJumpGroups({
      examQuestions: {
        pages: [
          {
            name: '选择题',
            questions: [
              { id: 'q1', text: '第一题' },
              { id: 'q2', text: '第二题' },
            ],
          },
        ],
      },
      answers: [
        { question_id: 'q1', answer: 'A' },
        { question_id: 'extra', answer: '补充说明' },
      ],
    });

    expect(groups).toHaveLength(2);
    expect(groups[0].title).toBe('选择题');
    expect(groups[0].items.map((item) => item.answered)).toEqual([true, true]);
    expect(groups[1].title).toBe('其他内容');
    expect(groups[1].items[0]).toMatchObject({ id: 'extra', answered: true });
  });

  it('uses plain answer groups when no exam paper exists', () => {
    const groups = collectSubmissionJumpGroups({
      examQuestions: null,
      answers: {
        完整答案: { answer: '' },
        附件题: { attachments: [{ file_name: 'diagram.png' }] },
      },
    });

    expect(groups).toEqual([
      {
        title: '答题内容',
        items: [
          { index: 1, id: '完整答案', text: '完整答案', answered: false },
          { index: 2, id: '附件题', text: '附件题', answered: true },
        ],
      },
    ]);
    expect(summarizeSubmissionJumpGroups(groups)).toEqual({ answered: 1, total: 2 });
  });

  it('treats scalar text and attachments as display content', () => {
    expect(answerHasDisplayContent('hello')).toBe(true);
    expect(answerHasDisplayContent({ answer: '  ' })).toBe(false);
    expect(answerHasDisplayContent({ attachments: [{}] })).toBe(true);
  });

  it('truncates markdown-like labels for compact nav buttons', () => {
    expect(truncateJumpText('**这是一个很长很长很长很长很长很长很长很长的题目**', 10)).toBe('这是一个很长很长很长...');
  });
});
