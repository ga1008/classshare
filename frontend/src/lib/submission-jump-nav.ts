export type SubmissionJumpItem = {
  index: number;
  id: string;
  text: string;
  answered: boolean;
};

export type SubmissionJumpGroup = {
  title: string;
  items: SubmissionJumpItem[];
};

export type SubmissionJumpPayload = {
  answers: unknown;
  examQuestions: unknown;
};

export const SUBMISSION_JUMP_MANAGED_ATTR = 'submissionJumpManaged';

function stripMarkdownText(value: unknown) {
  return String(value || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/[#>*_~]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function truncateJumpText(value: unknown, maxLength = 34) {
  const text = stripMarkdownText(value);
  return text.length > maxLength ? `${text.slice(0, maxLength).trim()}...` : text;
}

function extractAnswerText(value: unknown): string | null {
  if (value == null || value === '') {
    return null;
  }
  if (typeof value !== 'object') {
    return String(value);
  }
  const record = value as Record<string, unknown>;
  const scalar = record.answer ?? record.content ?? record.text;
  if (scalar != null) {
    return String(scalar);
  }
  if (Array.isArray(record.attachments) && record.attachments.length) {
    return '';
  }
  return JSON.stringify(value);
}

function extractAnswerAttachments(value: unknown): unknown[] {
  if (!value || typeof value !== 'object') {
    return [];
  }
  const attachments = (value as Record<string, unknown>).attachments;
  return Array.isArray(attachments) ? attachments : [];
}

export function answerHasDisplayContent(value: unknown) {
  const text = extractAnswerText(value);
  return Boolean((text && text.trim() !== '') || extractAnswerAttachments(value).length);
}

function getAnswer(answers: unknown, questionId: unknown, questionIndex: number) {
  if (!answers) {
    return null;
  }
  if (Array.isArray(answers)) {
    const id = String(questionId || '');
    const item = answers.find((answer) => {
      if (!answer || typeof answer !== 'object') {
        return false;
      }
      const record = answer as Record<string, unknown>;
      return String(record.question_id || '') === id || String(record.question || '') === id;
    });
    if (item) {
      return item;
    }
    if (questionIndex > 0 && questionIndex <= answers.length) {
      return answers[questionIndex - 1];
    }
    return null;
  }
  if (typeof answers === 'object') {
    const record = answers as Record<string, unknown>;
    const id = String(questionId || '');
    if (id && record[id] !== undefined) {
      return record[id];
    }
    if (record[String(questionIndex)] !== undefined) {
      return record[String(questionIndex)];
    }
  }
  return null;
}

function getExtraAnswers(answers: unknown, matchedIds: Set<string>): Array<[string, unknown]> {
  if (!answers || typeof answers !== 'object') {
    return [];
  }
  if (Array.isArray(answers)) {
    return answers
      .filter((answer) => {
        if (!answer || typeof answer !== 'object') {
          return true;
        }
        const record = answer as Record<string, unknown>;
        return !matchedIds.has(String(record.question_id || '')) && !matchedIds.has(String(record.question || ''));
      })
      .map((answer, index) => {
        if (!answer || typeof answer !== 'object') {
          return [`extra-${index + 1}`, answer];
        }
        const record = answer as Record<string, unknown>;
        return [String(record.question_id || record.question || `extra-${index + 1}`), answer];
      });
  }

  return Object.entries(answers as Record<string, unknown>)
    .filter(([key]) => !matchedIds.has(key) && !matchedIds.has(String(key)));
}

type ExamQuestion = {
  id?: string | number;
  text?: string;
};

type ExamPage = {
  name?: string;
  questions?: ExamQuestion[];
};

function normalizeExamPages(examQuestions: unknown): ExamPage[] {
  if (!examQuestions || typeof examQuestions !== 'object') {
    return [];
  }
  const pages = (examQuestions as Record<string, unknown>).pages;
  if (!Array.isArray(pages)) {
    return [];
  }
  return pages.map((page) => {
    const record = page && typeof page === 'object' ? (page as Record<string, unknown>) : {};
    return {
      name: typeof record.name === 'string' ? record.name : '',
      questions: Array.isArray(record.questions)
        ? record.questions.map((question) => {
          const questionRecord = question && typeof question === 'object'
            ? (question as Record<string, unknown>)
            : {};
          return {
            id: typeof questionRecord.id === 'string' || typeof questionRecord.id === 'number'
              ? questionRecord.id
              : '',
            text: typeof questionRecord.text === 'string' ? questionRecord.text : '',
          };
        })
        : [],
    };
  });
}

function collectExamJumpGroups(answers: unknown, examQuestions: unknown): SubmissionJumpGroup[] {
  const pages = normalizeExamPages(examQuestions);
  if (!pages.length) {
    return [];
  }

  const groups: SubmissionJumpGroup[] = [];
  const matchedIds = new Set<string>();
  let questionIndex = 0;

  pages.forEach((page, pageIndex) => {
    const items = (page.questions || []).map((question) => {
      questionIndex += 1;
      const id = String(question.id || '');
      if (id) {
        matchedIds.add(id);
      }
      return {
        index: questionIndex,
        id,
        text: question.text || `第 ${questionIndex} 题`,
        answered: answerHasDisplayContent(getAnswer(answers, id, questionIndex)),
      };
    });
    if (items.length) {
      groups.push({
        title: page.name || `第 ${pageIndex + 1} 大题`,
        items,
      });
    }
  });

  const extras = getExtraAnswers(answers, matchedIds);
  if (extras.length) {
    groups.push({
      title: '其他内容',
      items: extras.map(([key, value], extraIndex) => ({
        index: questionIndex + extraIndex + 1,
        id: key,
        text: key,
        answered: answerHasDisplayContent(value),
      })),
    });
  }

  return groups;
}

function collectPlainJumpGroups(answers: unknown): SubmissionJumpGroup[] {
  const items: SubmissionJumpItem[] = [];
  if (Array.isArray(answers)) {
    answers.forEach((item, index) => {
      const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {};
      items.push({
        index: index + 1,
        id: String(record.question_id || record.question || `q${index + 1}`),
        text: String(record.question || record.question_id || `第 ${index + 1} 题`),
        answered: answerHasDisplayContent(item),
      });
    });
  } else if (answers && typeof answers === 'object') {
    Object.entries(answers as Record<string, unknown>).forEach(([key, value], index) => {
      items.push({
        index: index + 1,
        id: key,
        text: key,
        answered: answerHasDisplayContent(value),
      });
    });
  }

  return items.length ? [{ title: '答题内容', items }] : [];
}

export function collectSubmissionJumpGroups(payload: SubmissionJumpPayload): SubmissionJumpGroup[] {
  const examGroups = collectExamJumpGroups(payload.answers, payload.examQuestions);
  return examGroups.length ? examGroups : collectPlainJumpGroups(payload.answers);
}

export function summarizeSubmissionJumpGroups(groups: SubmissionJumpGroup[]) {
  return groups.reduce(
    (summary, group) => ({
      total: summary.total + group.items.length,
      answered: summary.answered + group.items.filter((item) => item.answered).length,
    }),
    { answered: 0, total: 0 },
  );
}
