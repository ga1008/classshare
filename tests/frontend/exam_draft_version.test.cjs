/* Import production submission operations with isolated page-owned ports. */
const assert = require('node:assert/strict');
const { join } = require('node:path');
const { pathToFileURL } = require('node:url');
const { test, before } = require('node:test');
let createExamSubmissionController;
const importTouches = [];
before(async () => {
  const names = ['window', 'document', 'localStorage', 'fetch'];
  const descriptors = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  try {
    for (const name of names) Object.defineProperty(globalThis, name, {
      configurable: true,
      get() { importTouches.push(name); throw new Error(`Import touched ${name}`); },
    });
    ({ createExamSubmissionController } = await import(pathToFileURL(join(__dirname, '../../static/js/exam_take/submit.js')).href));
  } finally {
    for (const [name, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture(overrides = {}) {
  const calls = [], messages = [], applied = [], busy = [], removed = [], timers = [], cleared = [], effects = [];
  const context = {
    ASSIGNMENT_ID: '42', SUBMISSION_VERSION: 'opened-round-1', EXAM_STARTED_AT: '2026-09-10',
    SUBMISSION_EXISTS: false, isEditingResubmission: false, assignmentAcceptingSubmissions: true, submissionSucceeded: false,
    state: { pages: [{ questions: [{ id: 'q1', text: 'Question' }] }], answers: { q1: 'retained answer' }, serverQuestionFiles: {}, currentPageIdx: 0 },
    serverDraftConflict: false, serverDraftLoadDone: false, lastLocalSavedAt: '', lastServerDraftSignature: '',
    serverDraftInFlight: null, serverDraftSaveTimer: null, uploadManager: null, LOCAL_DRAFT_KEY: 'exam_42',
    SERVER_DRAFT_RESTORE_SYNC_DELAY_MS: 1,
    buildAnswersListForDraft: () => [{ question_id: 'q1', answer: context.state.answers.q1 }],
    setSaveStatus: (...args) => messages.push(args), showMessage: (...args) => messages.push(args),
    getApiFailureMessage: (error, fallback) => error.message || fallback,
    escapeStatusText: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    setTimeout: (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer; },
    clearTimeout: timer => cleared.push(timer),
    nowIso: () => '2026-09-20T12:00:00.000Z', warn() {},
    applyServerDraft: value => applied.push(value), applyServerDraftFiles: value => applied.push(value),
    applyServerFilesToManagers: () => applied.push('files-synced'),
    scheduleServerDraftSave: () => applied.push('scheduled'),
    apiFetch: async (url, options) => { calls.push({ url, options }); return { files_by_question: {} }; },
    window: {}, localStorage: { removeItem: key => removed.push(key) }, reload: () => effects.push('reload'), confirm: () => true,
    onSubmissionSucceeded: () => { context.submissionSucceeded = true; },
    answerHasContent: () => true, hasDrawing: () => false, hasQuestionFiles: () => false,
    validateQuestionAttachmentRequirements: () => null, setSubmitBusy: value => busy.push(value),
    clearQuestionDraftUploadTimers() {}, buildPendingQuestionDraftPayload: async () => ({}),
    saveServerDraft: async () => ({ files_by_question: {} }), markPendingQuestionDraftsClean() {},
    findUnsyncedQuestionDraft: () => null, validateFormDataFileLimits() {},
    scrollToQuestion: id => effects.push(['scroll', id]),
    getQuestionDisplayLabel: question => question.id,
    collectUploadIssueMessage: (value, fallback) => value.message || fallback,
    appendQuestionAttachmentFiles: () => [], appendDrawingFiles: async () => [],
    findQuestionById: id => context.state.pages.flatMap(page => page.questions || []).find(question => question.id === id),
    ...overrides,
  };
  const live = {};
  for (const name of ['isEditingResubmission', 'assignmentAcceptingSubmissions', 'submissionSucceeded', 'uploadManager', 'lastLocalSavedAt', 'serverDraftLoadDone', 'serverDraftConflict', 'lastServerDraftSignature', 'serverDraftInFlight', 'serverDraftSaveTimer']) {
    Object.defineProperty(live, name, { get: () => context[name], set: value => { context[name] = value; } });
  }
  const ports = {};
  for (const name of ['apiFetch', 'buildAnswersListForDraft', 'setSaveStatus', 'showMessage', 'getApiFailureMessage', 'escapeStatusText', 'setTimeout', 'clearTimeout', 'nowIso', 'warn', 'applyServerDraft', 'applyServerDraftFiles', 'applyServerFilesToManagers', 'scheduleServerDraftSave', 'answerHasContent', 'hasDrawing', 'hasQuestionFiles', 'validateQuestionAttachmentRequirements', 'setSubmitBusy', 'clearQuestionDraftUploadTimers', 'buildPendingQuestionDraftPayload', 'saveServerDraft', 'markPendingQuestionDraftsClean', 'findUnsyncedQuestionDraft', 'validateFormDataFileLimits', 'scrollToQuestion', 'getQuestionDisplayLabel', 'collectUploadIssueMessage', 'appendQuestionAttachmentFiles', 'appendDrawingFiles', 'findQuestionById', 'confirm', 'reload']) {
    ports[name] = (...args) => context[name](...args);
  }
  Object.assign(ports, {
    onSubmissionSucceeded: () => context.onSubmissionSucceeded(),
    createFormData: () => new FormData(),
    getBehaviorTracker: () => context.window.behaviorTracker,
    getOpenGroupPeerEval: () => context.window.openGroupPeerEval ? id => context.window.openGroupPeerEval(id) : null,
    removeLocalDraft: key => context.localStorage.removeItem(key),
  });
  const openedContext = {
    assignmentId: context.ASSIGNMENT_ID, submissionVersion: context.SUBMISSION_VERSION,
    examStartedAt: context.EXAM_STARTED_AT, submissionExists: context.SUBMISSION_EXISTS,
    localDraftKey: context.LOCAL_DRAFT_KEY, restoreSyncDelayMs: context.SERVER_DRAFT_RESTORE_SYNC_DELAY_MS,
  };
  Object.assign(context, createExamSubmissionController({ context: openedContext, state: context.state, live, ports }));
  return { context, openedContext, calls, messages, applied, busy, removed, timers, cleared, effects };
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
    assert.deepEqual(f.applied, ['files-synced']);
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
  assert.equal(f.context.submissionSucceeded, false);
});

test('successful final submission also carries the fixed page version', async () => {
  const f = fixture();
  await f.context.handleSubmission();
  assert.equal(f.calls.length, 1);
  assert.match(f.calls[0].url, /\/submit$/);
  assert.equal(f.calls[0].options.body.get('expected_submission_version'), 'opened-round-1');
});

test('production import touches no browser or network globals', () => {
  assert.equal(typeof createExamSubmissionController, 'function');
  assert.deepEqual(importTouches, []);
});

test('opened context stays fixed and separate controllers do not share conflict or signature state', async () => {
  const first = fixture({ serverDraftConflict: true });
  const second = fixture();
  second.openedContext.submissionVersion = 'mutated-after-open';
  second.openedContext.assignmentId = '99';
  await assert.rejects(first.context.performServerDraftSave(), error => error.status === 409);
  await second.context.performServerDraftSave();
  assert.equal(first.calls.length, 0);
  assert.equal(first.context.lastServerDraftSignature, '');
  assert.equal(second.context.serverDraftConflict, false);
  assert.equal(second.calls[0].url, '/api/assignments/42/draft');
  assert.equal(second.calls[0].options.body.get('expected_submission_version'), 'opened-round-1');
});

test('late draft GET compares the latest local timestamp and only restores files for newer local work', async () => {
  const response = deferred();
  const observed = [];
  const f = fixture({
    lastLocalSavedAt: '2026-09-20T10:00:00Z',
    apiFetch: () => response.promise,
    applyServerDraft: () => observed.push('answers'),
    applyServerDraftFiles: draft => observed.push(['files', draft.files_by_question]),
    scheduleServerDraftSave: delay => observed.push(['schedule', delay]),
  });
  const loading = f.context.loadServerDraft();
  assert.equal(f.context.serverDraftLoadDone, false);
  f.context.lastLocalSavedAt = '2026-09-20T12:00:00Z';
  f.context.state.answers = { q1: 'edited during GET' };
  const files = { q1: [{ file_name: 'server.txt' }] };
  response.resolve({ exists: true, submission_version: 'opened-round-1', server_updated_at: '2026-09-20T11:00:00Z', files_by_question: files });
  await loading;
  assert.deepEqual(observed, [['files', files], ['schedule', 1]]);
  assert.equal(f.context.serverDraftLoadDone, true);
  assert.equal(f.context.state.answers.q1, 'edited during GET');
});

test('load preserves restore, missing-draft scheduling and failure completion branches', async () => {
  const draft = { exists: true, server_updated_at: '2026-09-20T11:00:00Z', answers_json: '{}' };
  const restored = fixture({ apiFetch: async () => draft, lastLocalSavedAt: draft.server_updated_at });
  await restored.context.loadServerDraft();
  assert.deepEqual(restored.applied, [draft]);
  assert.equal(restored.context.serverDraftLoadDone, true);
  const missing = fixture({ apiFetch: async () => ({ exists: false }), lastLocalSavedAt: 'local-time' });
  await missing.context.loadServerDraft();
  assert.deepEqual(missing.applied, ['scheduled']);
  const failed = fixture({ apiFetch: async () => { throw new Error('offline'); } });
  await failed.context.loadServerDraft();
  assert.equal(failed.context.serverDraftLoadDone, true);
  assert.deepEqual(failed.applied, []);
  assert.equal(failed.context.state.answers.q1, 'retained answer');
});

test('resubmission and submission availability are live page guards', async () => {
  const f = fixture({ SUBMISSION_EXISTS: true });
  assert.equal(await f.context.performServerDraftSave(), null);
  await f.context.loadServerDraft();
  assert.equal(f.calls.length, 0);
  f.context.isEditingResubmission = true;
  await f.context.performServerDraftSave();
  assert.equal(f.calls.length, 1);
  f.context.assignmentAcceptingSubmissions = false;
  await f.context.handleSubmission();
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.busy, []);
});

test('only text-only duplicate signatures skip a write; repeated uploads and clears retain file effects', async () => {
  const f = fixture();
  await f.context.performServerDraftSave();
  await f.context.performServerDraftSave();
  assert.equal(f.calls.length, 1);
  const file = new File(['drawing'], 'answer.png', { type: 'image/png' });
  const uploadItems = [{ file, relative_path: 'exam_drawings/q1.png', kind: 'exam_drawing', question_id: 'q1', question: 'Question' }];
  const projections = [];
  f.context.buildAnswersListForDraft = (attachments, replaceIds) => {
    projections.push({ attachments, replaceIds });
    return [{ question_id: 'q1', answer: f.context.state.answers.q1, attachments }];
  };
  await f.context.performServerDraftSave({ uploadItems });
  await f.context.performServerDraftSave({ uploadItems });
  await f.context.performServerDraftSave({ replaceQuestionIds: ['q1'] });
  await f.context.performServerDraftSave({ replaceQuestionIds: ['q1'] });
  assert.equal(f.calls.length, 5);
  assert.equal(f.applied.filter(value => value === 'files-synced').length, 5);
  const form = f.calls[1].options.body;
  assert.equal(await form.get('files').text(), 'drawing');
  assert.deepEqual(JSON.parse(form.get('manifest')), [{ relative_path: 'exam_drawings/q1.png', content_type: 'image/png', kind: 'exam_drawing', question_id: 'q1', question: 'Question' }]);
  assert.equal(projections[0].attachments[0].kind, 'drawing');
  assert.equal(projections[0].attachments[0].file_size, file.size);
  assert.deepEqual(JSON.parse(f.calls[4].options.body.get('replace_question_ids')), ['q1']);
  assert.equal(f.calls[4].options.body.getAll('files').length, 0);
});

test('draft in-flight, server-file projection and status timer remain page-owned and complete once', async () => {
  const response = deferred();
  const f = fixture({ apiFetch: () => response.promise });
  let projections = 0;
  const files = { q1: [{ relative_path: 'q1/new.txt' }] };
  f.context.applyServerFilesToManagers = () => { projections++; assert.strictEqual(f.context.state.serverQuestionFiles, files); };
  const saving = f.context.performServerDraftSave();
  assert.strictEqual(f.context.serverDraftInFlight, response.promise);
  assert.equal(f.context.lastServerDraftSignature, '');
  response.resolve({ files_by_question: files });
  await saving;
  assert.equal(f.context.serverDraftInFlight, null);
  assert.equal(projections, 1);
  assert.notEqual(f.context.lastServerDraftSignature, '');
  assert.deepEqual(f.timers.map(timer => timer.delay), [2000]);
  f.timers[0].callback();
  assert.deepEqual(f.messages.at(-1), ['', '本地与服务器自动保存']);
});

test('failed draft escapes status text, retains the original error metadata and clears in-flight', async () => {
  const error = Object.assign(new Error('<unsafe>'), { status: 409, data: { code: 'new_round' } });
  const f = fixture({ apiFetch: async () => { throw error; } });
  await assert.rejects(f.context.performServerDraftSave(), actual => actual === error && actual.data.code === 'new_round');
  assert.deepEqual(f.messages.at(-1), ['error', '&lt;unsafe&gt;']);
  assert.equal(f.context.serverDraftInFlight, null);
  assert.equal(f.context.serverDraftConflict, true);
  assert.equal(f.context.lastServerDraftSignature, '');
  assert.deepEqual(f.applied, []);
  assert.deepEqual(f.timers, []);
});

test('submit waits for payload and the queue port, keeps the versions Map and reads current manager and answers', async () => {
  const payloadReady = deferred(), queueReady = deferred(), queueEntered = deferred();
  const versions = new Map([['q1', 4]]);
  const payload = { uploadItems: [], replaceQuestionIds: ['q1'], versions };
  const oldManager = { hasFiles: () => true, buildFormData() { assert.fail('stale upload manager'); } };
  const f = fixture({
    uploadManager: oldManager,
    serverDraftSaveTimer: 17,
    buildPendingQuestionDraftPayload: () => payloadReady.promise,
    saveServerDraft: value => { assert.strictEqual(value, payload); queueEntered.resolve(); return queueReady.promise; },
  });
  let clearCount = 0, cleaned = null, builds = 0;
  f.context.clearQuestionDraftUploadTimers = () => { clearCount++; };
  f.context.markPendingQuestionDraftsClean = value => { cleaned = value; };
  const submitting = f.context.handleSubmission();
  assert.deepEqual(f.cleared, [17]);
  assert.equal(clearCount, 1);
  assert.deepEqual(f.busy, [true]);
  assert.equal(f.calls.length, 0);
  payloadReady.resolve(payload);
  await queueEntered.promise;
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.removed, []);
  f.context.uploadManager = { buildFormData() { builds++; const form = new FormData(); form.append('footer', 'current-manager'); return form; } };
  f.context.state.answers = { q1: 'new answer while awaiting draft' };
  f.context.state.serverQuestionFiles = { q1: [{ kind: 'exam_drawing', file_name: 'drawing.png', relative_path: 'q1/drawing.png' }] };
  queueReady.resolve({ files_by_question: f.context.state.serverQuestionFiles });
  await submitting;
  assert.strictEqual(cleaned, payload);
  assert.strictEqual(cleaned.versions, versions);
  assert.equal(builds, 1);
  assert.equal(f.calls.length, 1);
  const form = f.calls[0].options.body;
  assert.equal(form.get('footer'), 'current-manager');
  assert.equal(form.get('use_server_draft'), '1');
  const answer = JSON.parse(form.get('answers_json')).answers[0];
  assert.equal(answer.answer, 'new answer while awaiting draft');
  assert.equal(answer.attachments[0].kind, 'drawing');
  assert.equal(answer.attachments[0].question, 'Question');
});

test('non-409 draft failure with server files stops final submission and preserves input', async () => {
  const f = fixture({ saveServerDraft: async () => { throw new Error('offline'); } });
  const files = [{ relative_path: 'q1/old.txt' }];
  f.context.state.serverQuestionFiles = { q1: files };
  await f.context.handleSubmission();
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.busy, [true, false]);
  assert.strictEqual(f.context.state.serverQuestionFiles.q1, files);
  assert.equal(f.context.state.answers.q1, 'retained answer');
  assert.deepEqual(f.removed, []);
});

test('non-409 failure without server files still submits local footer, question and drawing files', async () => {
  const drawingReady = deferred(), drawingEntered = deferred();
  const f = fixture({ saveServerDraft: async () => { throw new Error('offline'); } });
  const questionEntry = { question_id: 'q1', kind: 'file', relative_path: 'q1/answer.txt' };
  const drawingEntry = { question_id: 'q1', kind: 'drawing', relative_path: 'q1/drawing.png' };
  let sharedForm;
  f.context.uploadManager = {
    hasFiles: () => true,
    buildFormData() { sharedForm = new FormData(); sharedForm.append('files', new File(['footer'], 'footer.txt')); return sharedForm; },
  };
  f.context.appendQuestionAttachmentFiles = (form, questions) => {
    assert.strictEqual(form, sharedForm); assert.equal(questions[0].id, 'q1');
    form.append('files', new File(['answer'], 'answer.txt')); return [questionEntry];
  };
  f.context.appendDrawingFiles = async form => {
    drawingEntered.resolve(); await drawingReady.promise;
    assert.strictEqual(form, sharedForm); form.append('files', new File(['drawing'], 'drawing.png')); return [drawingEntry];
  };
  f.context.validateFormDataFileLimits = form => { assert.strictEqual(form, sharedForm); assert.equal(form.getAll('files').length, 3); };
  const submitting = f.context.handleSubmission();
  await drawingEntered.promise;
  assert.equal(f.calls.length, 0);
  drawingReady.resolve();
  await submitting;
  assert.equal(f.calls.length, 1);
  assert.strictEqual(f.calls[0].options.body, sharedForm);
  assert.equal(sharedForm.get('use_server_draft'), '0');
  assert.deepEqual(sharedForm.getAll('files').map(file => file.name), ['footer.txt', 'answer.txt', 'drawing.png']);
  assert.deepEqual(JSON.parse(sharedForm.get('answers_json')).answers[0].attachments, [questionEntry, drawingEntry]);
  assert.ok(f.messages.some(message => message[0].includes('继续使用当前页面内容')));
});

test('dropped and unsynced draft attachments block submit after forwarding the same clean payload', async () => {
  for (const mode of ['dropped', 'unsynced']) {
    const payload = { replaceQuestionIds: ['q1'], versions: new Map([['q1', 3]]) };
    let cleaned;
    const f = fixture({
      buildPendingQuestionDraftPayload: async () => payload,
      saveServerDraft: async () => ({ dropped_file_count: mode === 'dropped' ? 1 : 0 }),
      markPendingQuestionDraftsClean: value => { cleaned = value; },
      findUnsyncedQuestionDraft: () => mode === 'unsynced' ? { id: 'q1' } : null,
    });
    f.context.state.serverQuestionFiles = { q1: [{ file_name: 'retained.txt' }] };
    await f.context.handleSubmission();
    assert.strictEqual(cleaned, payload);
    assert.equal(f.calls.length, 0);
    assert.deepEqual(f.removed, []);
    assert.deepEqual(f.busy, [true, false]);
  }
});

test('post-draft validation failure scrolls to the question and releases busy without building final data', async () => {
  let checks = 0;
  const f = fixture({ validateQuestionAttachmentRequirements: () => ++checks === 1 ? null : { question: { id: 'q1' }, message: '请补齐附件' } });
  f.context.uploadManager = { hasFiles: () => false, buildFormData() { assert.fail('invalid form must not be built'); } };
  await f.context.handleSubmission();
  assert.equal(checks, 2);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.effects, [['scroll', 'q1']]);
  assert.deepEqual(f.busy, [true, false]);
  assert.deepEqual(f.removed, []);
});

test('final failure releases busy but never clears local work or starts peer evaluation and reload', async () => {
  const f = fixture({ apiFetch: async () => { throw new Error('submit rejected'); } });
  f.context.window.openGroupPeerEval = () => assert.fail('peer evaluation after failed submit');
  await f.context.handleSubmission();
  assert.deepEqual(f.busy, [true, false]);
  assert.deepEqual(f.removed, []);
  assert.deepEqual(f.timers, []);
  assert.equal(f.context.state.answers.q1, 'retained answer');
  assert.equal(f.context.submissionSucceeded, false);
});

test('successful submission clears once, resolves the late peer-evaluation port and only then schedules reload', async () => {
  const posted = deferred(), posting = deferred(), peer = deferred(), peerEntered = deferred();
  const f = fixture({ apiFetch: (url, options) => { posting.resolve({ url, options }); return posted.promise; } });
  const attempts = [];
  f.context.window.behaviorTracker = { log(...args) { assert.strictEqual(this, f.context.window.behaviorTracker); attempts.push(args); } };
  const submitting = f.context.handleSubmission();
  const call = await posting.promise;
  assert.equal(call.options.body.get('started_at'), '2026-09-10');
  assert.equal(call.options.body.get('expected_submission_version'), 'opened-round-1');
  assert.equal(attempts[0][0], 'exam_submit_attempt');
  assert.deepEqual(f.removed, []);
  assert.equal(f.context.submissionSucceeded, false);
  f.context.window.openGroupPeerEval = function(id) {
    assert.strictEqual(this, f.context.window); assert.equal(id, '42');
    assert.deepEqual(f.removed, ['exam_42']); peerEntered.resolve(); return peer.promise;
  };
  posted.resolve({ is_late_submission: true });
  await peerEntered.promise;
  assert.equal(f.context.submissionSucceeded, true);
  assert.deepEqual(f.timers, []);
  peer.reject(new Error('optional peer dialog dismissed'));
  await submitting;
  assert.deepEqual(f.removed, ['exam_42']);
  assert.deepEqual(f.timers.map(timer => timer.delay), [1000]);
  assert.ok(f.messages.some(message => message[0].includes('补交规则')));
  f.timers[0].callback();
  assert.deepEqual(f.effects, ['reload']);
});

test('absent or broken optional peer-evaluation hooks keep the existing successful reload path', async () => {
  for (const hook of [undefined, null, false, {}]) {
    const f = fixture({ window: { openGroupPeerEval: hook } });
    await f.context.handleSubmission();
    assert.deepEqual(f.removed, ['exam_42']);
    assert.deepEqual(f.busy, [true]);
    assert.deepEqual(f.timers.map(timer => timer.delay), [1000]);
  }
});

test('accepted final submission marks the page owner once and prevents old queued draft work or duplicate final writes', async () => {
  const f = fixture();
  let accepted = 0;
  f.context.onSubmissionSucceeded = () => {
    accepted++;
    assert.equal(f.calls.at(-1).url, '/api/assignments/42/submit');
    f.context.submissionSucceeded = true;
  };
  await f.context.performServerDraftSave();
  const oldStatusTimer = f.timers[0];
  await f.context.handleSubmission();
  assert.equal(accepted, 1);
  assert.equal(f.calls.length, 2);
  const statusCount = f.messages.length;
  oldStatusTimer.callback();
  assert.equal(f.messages.length, statusCount);
  assert.equal(await f.context.performServerDraftSave({ replaceQuestionIds: ['q1'] }), null);
  await f.context.handleSubmission();
  assert.equal(f.calls.length, 2);
  assert.equal(accepted, 1);
  assert.deepEqual(f.removed, ['exam_42']);
});

test('a rejected final request leaves the owner unsucceeded and an explicit retry can succeed', async () => {
  for (const status of [409, 503]) {
    const f = fixture();
    let attempts = 0;
    f.context.apiFetch = async () => {
      if (++attempts === 1) throw Object.assign(new Error('not accepted'), { status });
      return {};
    };
    await f.context.handleSubmission();
    assert.equal(f.context.submissionSucceeded, false);
    assert.deepEqual(f.removed, []);
    assert.equal(f.context.state.answers.q1, 'retained answer');
    await f.context.handleSubmission();
    assert.equal(attempts, 2);
    assert.equal(f.context.submissionSucceeded, true);
    assert.deepEqual(f.removed, ['exam_42']);
  }
});
