import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';
import * as questions from '../../static/js/assessment_questions.js';
import { createExamSubmissionController } from '../../static/js/exam_take/submit.js';

const ordinary = readFileSync(new URL('../../templates/assignment_detail_student.html', import.meta.url), 'utf8');
const exam = readFileSync(new URL('../../templates/exam_take.html', import.meta.url), 'utf8');
function actualFunction(template, name) {
    const match = template.match(new RegExp(`^    (?:async )?function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?^    \\}`, 'm'));
    if (!match) throw new Error(`Missing real template function: ${name}`);
    return match[0];
}

function examFixture(data) {
    const paper = [{ id: 'q1', type: 'text', text: '历史题干', blanks: ['旧扩展一', '旧扩展二'] },
        { id: 'q2', type: 'text', text: '{{2}} / {{7}}', blanks: [{ id: '2' }, { id: '7' }] }];
    const elements = { dynamicArea: { innerHTML: '' } };
    const context = vm.createContext({ ...questions, SUBMISSION_DATA: data, SUBMISSION_RETURNED: true,
        CAN_RESUBMIT_SUBMISSION: true, CAN_WITHDRAW_SUBMISSION: false, RESUBMISSION_DUE_AT: null,
        APPEAL_STATE: null, SUBMISSION_FILES: [], SUBMISSION_FEEDBACK_MD: '', gradingFeedback: {},
        state: { pages: [{ name: '旧卷', questions: paper }], answers: {}, drawings: {} }, elements,
        document: { body: { classList: { add() {} } }, getElementById: () => null },
        findQuestionById: id => paper.find(q => q.id === id),
        findSubmissionFileForAttachment: () => ({ raw_url: '/synthetic-drawing.png' }),
        updateTopbarPageState() {}, updateTopbarNavButtons() {}, handleWithdrawSubmission() {}, startExamResubmission() {},
        renderSidebarForSubmitted() {}, renderSubmittedAttachments: attachments => `[attachments:${attachments.length}]`,
        findQuestionFeedback: () => null, renderQuestionFeedbackHtml: () => '',
        formatText: value => value, formatAnswerMarkdown: value => value, esc: value => String(value),
        applyServerDraftFiles() {}, ensureAnswerKeys() {}, console,
    });
    vm.runInContext(['storedQuestionAnswer', 'normalizeAnswerRecord', 'extractSubmittedAnswers',
        'extractSubmittedDrawings', 'renderSubmittedView', 'applyServerDraft'].map(name => actualFunction(exam, name)).join('\n'), context);
    return { context, elements, evaluate: code => vm.runInContext(code, context) };
}

function footerFixture(apiFetch = async () => ({})) {
    const file = { id: 9, question_id: '', kind: 'file', relative_path: 'legacy.txt', file_name: 'legacy.txt' };
    class UploadManager {
        constructor(options) { this.options = options; this.entries = []; }
        init() {}
        destroy() {}
        addServerEntries(files) { this.entries.push(...files.map(remote => ({ relativePath: remote.relative_path, remote }))); }
        applyRemoteFiles() {}
        removeEntries(predicate) { this.entries = this.entries.filter(entry => !predicate(entry)); }
    }
    const context = vm.createContext({ state: { serverQuestionFiles: { '': [file] } },
        uploadManager: null, pendingFooterFileDeletes: new Set(), failedFooterFileDeletes: new Set(),
        pendingDraftReplaceQuestionIds: new Set(), questionUploadManagers: new Map(),
        serverDraftWriteChain: Promise.resolve(), serverDraftConflict: false,
        SubmissionUploadManager: UploadManager, ASSIGNMENT_ID: 'synthetic', SUBMISSION_VERSION: 'round-2',
        ALLOWED_FILE_TYPES: [], MAX_UPLOAD_BYTES: 1000, MAX_PER_FILE_BYTES: 1000, MAX_FILE_COUNT: 5,
        createDuplicateImageGuard() {}, collectKnownImageHashes: () => [], showMessage() {},
        apiFetch, URLSearchParams, File: class {}, window: {}, document: { getElementById: () => null } });
    vm.runInContext(['getServerQuestionFileEntries', 'isDrawingFile', 'getServerQuestionRegularFiles',
        'localRelativePathForServerFile', 'seedManagerWithServerFiles', 'rawServerQuestionRegularFiles',
        'applyServerFilesToManagers', 'mountUploadManager', 'syncRemovedFooterDraftFiles',
        'validateFormDataFileLimits'].map(name => actualFunction(exam, name)).join('\n'), context);
    return { context, file, evaluate: code => vm.runInContext(code, context) };
}

describe('legacy answer readers retain valid work across malformed peers', () => {
    it('reads dictionary values without flattening arrays or losing blank IDs and attachments', () => {
        const original = { q1: 'A, B；C\nD', q2: { answer: ['second', 'first'], blank_ids: ['7', '2'],
            question_id: 'wrong-inner-id', attachments: [{ kind: 'drawing', relative_path: 'exam_drawings/a.png' }] }, q3: 0, q4: null };
        const before = JSON.stringify(original);
        for (const raw of [original, { answers: original }, JSON.stringify({ answers: original })]) {
            const records = questions.parseAnswerRecords(raw);
            expect(records.map(item => item.question_id)).toEqual(['q1', 'q2', 'q3', 'q4']);
            expect(records[0].answer).toBe('A, B；C\nD');
            expect(records[1]).toMatchObject({ question_id: 'q2', question: 'q2', answer: ['second', 'first'],
                blank_ids: ['7', '2'], attachments: original.q2.attachments });
            expect(records[2].answer).toBe(0);
            expect(records[3].answer).toBeNull();
        }
        expect(JSON.stringify(original)).toBe(before);
    });

    it('filters invalid peers, accepts direct lists and fails closed on invalid containers', () => {
        const valid = { question_id: 'q1', content: 'survives' };
        expect(questions.parseAnswerRecords([null, 7, 'invalid', [], {}, { unknown: true }, valid])).toEqual([valid]);
        for (const value of [null, 'broken JSON', 7, { answers: null }, { answers: 'invalid' }]) {
            expect(questions.parseAnswerRecords(value)).toEqual([]);
        }
    });

    it('ordinary real restore functions recover dictionaries by label and preserve punctuation', () => {
        const fields = [{ dataset: { question: '原题 A' }, value: '' }, { dataset: { question: '原题 B' }, value: '' }];
        let refreshed = 0;
        const context = vm.createContext({ ...questions, document: { querySelectorAll: () => fields },
            assignmentAnswerEditors: new Map(fields.map(field => [field, { refresh() { refreshed++; } }])) });
        vm.runInContext(['ordinaryAnswerText', 'parseDraftAnswers', 'applyDraftAnswers'].map(name => actualFunction(ordinary, name)).join('\n'), context);
        const restored = vm.runInContext(`applyDraftAnswers(parseDraftAnswers({answers:{'原题 B':{text:'第二答'},'原题 A':'逗号, 分号；\\n原文'}}))`, context);
        expect(restored).toBe(2);
        expect(fields.map(field => field.value)).toEqual(['逗号, 分号；\n原文', '第二答']);
        expect(refreshed).toBe(2);
    });

    it('ordinary real restore keeps supported plain text, JSON scalars and positional answers editable', () => {
        const fields = [{ dataset: { question: '当前第一题' }, value: '' }, { dataset: { question: '当前第二题' }, value: '' }];
        const context = vm.createContext({ ...questions, document: { querySelectorAll: () => fields }, assignmentAnswerEditors: new Map() });
        vm.runInContext(['ordinaryAnswerText', 'parseDraftAnswers', 'applyDraftAnswers'].map(name => actualFunction(ordinary, name)).join('\n'), context);
        for (const [raw, expected] of [['旧纯文本，保留\n换行', '旧纯文本，保留\n换行'],
            [JSON.stringify('旧 JSON 字符串'), '旧 JSON 字符串'], ['0', '0'], ['false', 'false'],
            [{ answers: '旧包装标量' }, '旧包装标量']]) {
            context.raw = raw;
            fields.forEach(field => { field.value = ''; });
            expect(vm.runInContext('applyDraftAnswers(parseDraftAnswers(raw))', context)).toBe(1);
            expect(fields.map(field => field.value)).toEqual([expected, '']);
        }
        for (const raw of [['第一答', '第二答'], { answers: ['第一答', '第二答'] },
            JSON.stringify({ answers: ['第一答', '第二答'] })]) {
            context.raw = raw;
            fields.forEach(field => { field.value = ''; });
            vm.runInContext('applyDraftAnswers(parseDraftAnswers(raw))', context);
            expect(fields.map(field => field.value)).toEqual(['第一答', '第二答']);
        }
        context.raw = { answers: [null, { question: '当前第二题', answer: '有效答案' }] };
        fields.forEach(field => { field.value = ''; });
        vm.runInContext('applyDraftAnswers(parseDraftAnswers(raw))', context);
        expect(fields.map(field => field.value)).toEqual(['', '有效答案']);
        expect(vm.runInContext('parseDraftAnswers({answers:null})', context)).toEqual([]);
    });

    it('ordinary mixed scalar/record restoration reserves exact matches before allocating unused answers', () => {
        const fields = [{ dataset: { question: '题一' }, value: '' }, { dataset: { question: '题二' }, value: '' }];
        const context = vm.createContext({ ...questions, document: { querySelectorAll: () => fields }, assignmentAnswerEditors: new Map() });
        vm.runInContext(['ordinaryAnswerText', 'parseDraftAnswers', 'applyDraftAnswers'].map(name => actualFunction(ordinary, name)).join('\n'), context);
        for (const answers of [['标量原文', { question: '题一', answer: '匹配原文' }],
            [{ question: '题一', answer: '匹配原文' }, '标量原文']]) {
            context.raw = { answers };
            fields.forEach(field => { field.value = ''; });
            expect(vm.runInContext('applyDraftAnswers(parseDraftAnswers(raw))', context)).toBe(2);
            expect(fields.map(field => field.value)).toEqual(['匹配原文', '标量原文']);
        }
        context.raw = { answers: ['标量原文', { question: '题二', answer: '匹配原文' }] };
        fields.forEach(field => { field.value = ''; });
        vm.runInContext('applyDraftAnswers(parseDraftAnswers(raw))', context);
        expect(fields.map(field => field.value)).toEqual(['标量原文', '匹配原文']);
    });

    it('ordinary real submitted rendering keeps valid peers beside null and scalar entries', () => {
        const container = { innerHTML: '' };
        const context = vm.createContext({ document: { getElementById: () => container }, gradingFeedback: {},
            findQuestionFeedback: () => null, renderQuestionFeedbackHtml: () => '', updateQuestionFeedbackNote() {},
            renderAssessmentMarkdown: value => String(value), escapeHtml: value => String(value) });
        vm.runInContext(['ordinaryAnswerText', 'renderSubmittedAnswers'].map(name => actualFunction(ordinary, name)).join('\n'), context);
        vm.runInContext(`renderSubmittedAnswers({answers:[null,'旧字符串',{question:'题目',answer:'有效答案'}]})`, context);
        expect(container.innerHTML).toContain('（未作答）');
        expect(container.innerHTML).toContain('旧字符串');
        expect(container.innerHTML).toContain('有效答案');
        expect(container.innerHTML).not.toContain('[object Object]');
    });

    it('exam real submitted rendering, redo and draft recovery retain valid answers and drawings after nulls', () => {
        const drawing = { kind: 'drawing', relative_path: 'exam_drawings/a.png', file_name: 'a.png' };
        const data = { answers: [null, 7, 'invalid', [], {},
            { question_id: 'q1', answer: '完整旧答案，不能拆分', attachments: [null, 7, drawing] },
            { question_id: 'q2', answer: ['second', 'first'], blank_ids: ['7', '2'] }] };
        const f = examFixture(data);
        expect(() => f.evaluate('renderSubmittedView()')).not.toThrow();
        expect(f.elements.dynamicArea.innerHTML).toContain('完整旧答案，不能拆分');
        expect(f.elements.dynamicArea.innerHTML).toContain('重新作答并提交');
        expect(f.evaluate('extractSubmittedAnswers()')).toEqual({ q1: '完整旧答案，不能拆分', q2: ['first', 'second'] });
        expect(f.evaluate('extractSubmittedDrawings()').q1).toMatchObject({ sourceUrl: '/synthetic-drawing.png', relativePath: 'exam_drawings/a.png' });
        f.context.draft = { answers_json: JSON.stringify(data) };
        f.evaluate('applyServerDraft(draft)');
        expect(f.context.state.answers).toEqual({ q1: '完整旧答案，不能拆分', q2: ['first', 'second'] });
    });

    it('exam dictionary keys own identity and keep array blank answers through redo', () => {
        const f = examFixture({ answers: { q1: 'old', q2: { question_id: 'wrong', answer: ['B', 'A'], blank_ids: ['7', '2'] } } });
        expect(f.evaluate('extractSubmittedAnswers()')).toEqual({ q1: 'old', q2: ['A', 'B'] });
        expect(() => f.evaluate('renderSubmittedView()')).not.toThrow();
        expect(f.elements.dynamicArea.innerHTML).toContain('第 2 空：A');
        expect(f.elements.dynamicArea.innerHTML).toContain('第 7 空：B');
    });

    it('real exam redo restores this round local work before production controller compares server timestamps', async () => {
        for (const serverNewer of [false, true]) {
            const f = examFixture({ answers: [{ question_id: 'q1', answer: '原提交答案' }] });
            const keys = [], files = [], scheduled = [];
            const local = { answers: { q1: '本代较新本地答案' }, drawings: { q1: { dataUrl: 'local-drawing' } },
                currentPage: 1, updatedAt: '2026-11-15T10:05:00' };
            f.context.state.pages.push({ name: '第二页', questions: [] });
            Object.assign(f.context, { resubmissionStarting: false, isEditingResubmission: false,
                uploadManager: null, lastLocalSavedAt: '', LOCAL_DRAFT_KEY: 'student:exam:round-2',
                localStorage: { getItem(key) { keys.push(key); return JSON.stringify(local); } },
                restoreExamEditingShell() {}, renderCurrentPage() {}, saveToLocal() {} });
            f.context.document.body.classList.remove = () => {};
            f.context.applyServerDraftFiles = draft => { files.push(draft.files_by_question); };
            vm.runInContext(['loadFromLocal', 'startExamResubmission'].map(name => actualFunction(exam, name)).join('\n'), f.context);
            const live = {};
            for (const name of ['isEditingResubmission', 'lastLocalSavedAt', 'serverDraftLoadDone', 'serverDraftConflict']) {
                Object.defineProperty(live, name, { get: () => f.context[name], set: value => { f.context[name] = value; } });
            }
            const controller = createExamSubmissionController({
                context: { assignmentId: 'synthetic', submissionVersion: 'round-2', submissionExists: true, restoreSyncDelayMs: 100 },
                state: f.context.state, live, ports: {
                    apiFetch: async () => ({ exists: true, submission_version: 'round-2', current_page: 0,
                        server_updated_at: serverNewer ? '2026-11-15T10:06:00' : '2026-11-15T10:00:00.001',
                        answers_json: JSON.stringify({ answers: [{ question_id: 'q1', answer: '服务器答案' }] }),
                        files_by_question: { '': [{ id: 9, file_name: '原附件.txt' }] } }),
                    applyServerDraft: draft => { f.context.draft = draft; f.evaluate('applyServerDraft(draft)'); },
                    applyServerDraftFiles: draft => f.context.applyServerDraftFiles(draft),
                    scheduleServerDraftSave: delay => scheduled.push(delay), showMessage() {}, setSaveStatus() {}, warn() {},
                },
            });
            f.context.loadServerDraft = controller.loadServerDraft;
            await f.evaluate('startExamResubmission()');
            expect(keys).toEqual(['student:exam:round-2']);
            expect(f.context.state.answers.q1).toBe(serverNewer ? '服务器答案' : '本代较新本地答案');
            expect(f.context.state.currentPageIdx).toBe(serverNewer ? 0 : 1);
            if (!serverNewer) expect(f.context.state.drawings.q1).toEqual({ dataUrl: 'local-drawing' });
            expect(files).toEqual([{ '': [{ id: 9, file_name: '原附件.txt' }] }]);
            expect(scheduled).toEqual(serverNewer ? [] : [100]);
            expect(f.context.resubmissionStarting).toBe(false);
        }
    });

    it('exam real footer mount restores generic server files once and preserves edits across remounts', async () => {
        const requests = [];
        const f = footerFixture(async (url, options) => { requests.push({ url, options }); return {}; });
        f.evaluate('mountUploadManager(); mountUploadManager()');
        expect(f.context.uploadManager.entries.map(entry => entry.relativePath)).toEqual(['legacy.txt']);
        f.context.uploadManager.entries.push({ relativePath: 'new.txt', file: {} });
        f.evaluate('mountUploadManager()');
        expect(f.context.uploadManager.entries.map(entry => entry.relativePath)).toEqual(['legacy.txt', 'new.txt']);
        f.evaluate("uploadManager.entries = uploadManager.entries.filter(entry => !entry.remote); uploadManager.options.onChange({count:1})");
        await f.context.serverDraftWriteChain;
        expect(requests).toHaveLength(1);
        expect(requests[0]).toMatchObject({ url: '/api/assignments/synthetic/draft-files/9?expected_submission_version=round-2', options: { method: 'DELETE' } });
        expect(f.context.state.serverQuestionFiles['']).toEqual([]);
        f.evaluate('mountUploadManager()');
        expect(f.context.uploadManager.entries.map(entry => entry.relativePath)).toEqual(['new.txt']);
    });

    it('exam footer deletion failure restores the chip, blocks submission and remains retryable', async () => {
        let failed = true;
        const f = footerFixture(async () => { if (failed) throw Object.assign(new Error('offline'), { status: 502 }); return {}; });
        f.evaluate('mountUploadManager(); uploadManager.entries = []; uploadManager.options.onChange({count:0})');
        await f.context.serverDraftWriteChain;
        expect(f.context.uploadManager.entries.map(entry => entry.relativePath)).toEqual(['legacy.txt']);
        expect(() => f.evaluate('validateFormDataFileLimits({getAll:()=>[]})')).toThrow('附件删除尚未完成');
        failed = false;
        f.evaluate('uploadManager.entries = []; uploadManager.options.onChange({count:0})');
        await f.context.serverDraftWriteChain;
        expect(f.context.failedFooterFileDeletes.size).toBe(0);
        expect(f.context.state.serverQuestionFiles['']).toEqual([]);
        expect(() => f.evaluate('validateFormDataFileLimits({getAll:()=>[]})')).not.toThrow();
    });

    it('exam clear before the footer mounts does not rehydrate pending removals; conflicts retain files', async () => {
        const f = footerFixture();
        f.evaluate('syncRemovedFooterDraftFiles(); mountUploadManager()');
        expect(f.context.uploadManager.entries).toEqual([]);
        await f.context.serverDraftWriteChain;
        expect(f.context.state.serverQuestionFiles['']).toEqual([]);
        const conflict = footerFixture(async () => { throw Object.assign(new Error('stale round'), { status: 409 }); });
        conflict.evaluate('mountUploadManager(); uploadManager.entries = []; syncRemovedFooterDraftFiles()');
        await conflict.context.serverDraftWriteChain;
        expect(conflict.context.serverDraftConflict).toBe(true);
        expect(conflict.context.uploadManager.entries.map(entry => entry.relativePath)).toEqual(['legacy.txt']);
        expect(conflict.context.state.serverQuestionFiles['']).toHaveLength(1);
    });

    it('a later authoritative draft settles a committed DELETE whose response was lost', async () => {
        const f = footerFixture(async () => { throw Object.assign(new Error('response lost'), { status: 503 }); });
        f.evaluate('mountUploadManager(); uploadManager.entries = []; syncRemovedFooterDraftFiles()');
        await f.context.serverDraftWriteChain;
        expect(f.context.failedFooterFileDeletes.size).toBe(1);
        expect(f.context.uploadManager.entries).toHaveLength(1);
        f.context.uploadManager.entries.push({ file: {}, relativePath: 'new-local.txt' });
        f.evaluate("state.serverQuestionFiles = {}; applyServerFilesToManagers()");
        expect(f.context.failedFooterFileDeletes.size).toBe(0);
        expect(f.context.uploadManager.entries.map(entry => entry.relativePath)).toEqual(['new-local.txt']);
        expect(() => f.evaluate('validateFormDataFileLimits({getAll:()=>[]})')).not.toThrow();
    });

    it('numbered blanks require one wholly valid shape and do not partially enable old extensions', () => {
        for (const blanks of [['旧第一空', '旧第二空'], [{ id: '1' }, { label: 'old' }],
            [{ id: '1' }, { id: ' 1 ' }], [{ id: '0' }], [{ id: '-1' }], [{ id: '1.2' }], [null], []]) {
            const question = { type: 'text', blanks };
            expect(questions.questionBlanks(question)).toEqual([]);
            expect(questions.answerDisplayText(question, '完整旧答案')).toBe('完整旧答案');
        }
        const question = { type: 'text', blanks: [{ id: 2, answer: 'A' }, { id: ' 7 ', answer: 'B' }] };
        expect(questions.questionBlanks(question).map(blank => blank.id)).toEqual(['2', '7']);
        expect(question.blanks[1].id).toBe(' 7 ');
    });
});
