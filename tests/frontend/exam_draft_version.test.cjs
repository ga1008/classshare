/* Execute production exam-page functions with isolated browser/API doubles. */
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = readFileSync(join(__dirname, '../../templates/exam_take.html'), 'utf8');
const functions = ['performServerDraftSave', 'loadServerDraft', 'handleSubmission'].map(name => {
  const match = source.match(new RegExp(`^    async function ${name}\\([^]*?^    }`, 'm'));
  assert.ok(match, name);
  return match[0];
}).join('\n');

function fixture(overrides = {}) {
  const calls = [], messages = [], applied = [];
  const context = vm.createContext({
    FormData, Blob, Date, JSON, Map, Set, Error, console: { warn() {} },
    ASSIGNMENT_ID: '42', SUBMISSION_VERSION: 'opened-round-1', EXAM_STARTED_AT: '2026-09-10',
    SUBMISSION_EXISTS: false, isEditingResubmission: false, assignmentAcceptingSubmissions: true,
    state: { pages: [{ questions: [{ id: 'q1', text: 'Question' }] }], answers: { q1: 'retained answer' }, serverQuestionFiles: {}, currentPageIdx: 0 },
    serverDraftConflict: false, serverDraftLoadDone: false, lastLocalSavedAt: '', lastServerDraftSignature: '',
    serverDraftInFlight: null, serverDraftSaveTimer: null, uploadManager: null, LOCAL_DRAFT_KEY: 'exam_42',
    SERVER_DRAFT_RESTORE_SYNC_DELAY_MS: 1,
    buildAnswersListForDraft: () => [{ question_id: 'q1', answer: 'retained answer' }],
    setSaveStatus: (...args) => messages.push(args), showMessage: (...args) => messages.push(args),
    getApiFailureMessage: (error, fallback) => error.message || fallback,
    escapeStatusText: String, setTimeout() {}, clearTimeout() {},
    applyServerDraft: value => applied.push(value), applyServerDraftFiles: value => applied.push(value),
    scheduleServerDraftSave: () => applied.push('scheduled'),
    apiFetch: async (url, options) => { calls.push({ url, options }); return { files_by_question: {} }; },
    window: {}, localStorage: { removeItem() {} }, confirm: () => true,
    answerHasContent: () => true, hasDrawing: () => false, hasQuestionFiles: () => false,
    validateQuestionAttachmentRequirements: () => null, setSubmitBusy() {},
    clearQuestionDraftUploadTimers() {}, buildPendingQuestionDraftPayload: async () => ({}),
    saveServerDraft: async () => ({ files_by_question: {} }), markPendingQuestionDraftsClean() {},
    findUnsyncedQuestionDraft: () => null, validateFormDataFileLimits() {},
    ...overrides,
  });
  vm.runInContext(functions, context);
  return { context, calls, messages, applied };
}

test('text, attachment upload and clear all carry the opened page version', async () => {
  for (const options of [{}, { replaceQuestionIds: ['q1'] }, { uploadItems: [{
    file: Object.assign(new Blob(['answer'], { type: 'text/plain' }), { name: 'answer.txt' }),
    relative_path: 'answer.txt', question_id: 'q1',
  }] }]) {
    const f = fixture();
    await f.context.performServerDraftSave(options);
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0].options.body.get('expected_submission_version'), 'opened-round-1');
  }
});

test('a new-round draft response is never applied to an old page', async () => {
  const f = fixture({ apiFetch: async () => ({ exists: true, submission_version: 'new-round-2', answers_json: '{}' }) });
  await f.context.loadServerDraft();
  assert.deepEqual(f.applied, []);
  assert.equal(f.context.serverDraftConflict, true);
  assert.equal(f.context.state.answers.q1, 'retained answer');
});

test('409 remains a conflict and stops subsequent draft writes without discarding answers', async () => {
  let writes = 0;
  const f = fixture({ apiFetch: async () => { writes++; throw Object.assign(new Error('new round'), { status: 409 }); } });
  await assert.rejects(() => f.context.performServerDraftSave(), error => error.status === 409);
  await assert.rejects(() => f.context.performServerDraftSave(), error => error.status === 409);
  assert.equal(writes, 1);
  assert.equal(f.context.state.answers.q1, 'retained answer');
});

test('final submission never falls back after a draft round conflict', async () => {
  const f = fixture({ saveServerDraft: async () => { throw Object.assign(new Error('new round'), { status: 409 }); } });
  await f.context.handleSubmission();
  assert.equal(f.calls.length, 0);
  assert.ok(f.messages.some(args => String(args[0]).includes('new round')));
  assert.equal(f.context.state.answers.q1, 'retained answer');
});

test('successful final submission also carries the fixed page version', async () => {
  const f = fixture();
  await f.context.handleSubmission();
  assert.equal(f.calls.length, 1);
  assert.match(f.calls[0].url, /\/submit$/);
  assert.equal(f.calls[0].options.body.get('expected_submission_version'), 'opened-round-1');
});
