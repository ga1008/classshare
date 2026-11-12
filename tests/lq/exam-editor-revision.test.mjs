import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { it, expect } from 'vitest';

// Exercise the actual legacy template save functions; no parallel controller.
const template = readFileSync(new URL('../../templates/exam_editor.html', import.meta.url), 'utf8');
const saveSource = template.slice(template.indexOf('// === 保存 ==='), template.indexOf('function setPaperPreviewModalOpen'));

function setup(fetch, { examId = 'paper' } = {}) {
    const button = { disabled: false, attributes: {}, setAttribute(key, value) { this.attributes[key] = value; } };
    const elements = {
        'exam-title': { value: 'Draft kept' }, 'exam-desc': { value: 'Unsent explanation' },
        'exam-scope-level': { value: 'private' }, 'exam-allow-student-ai': { checked: false },
        'exam-save-button': button, 'exam-save-conflict': { hidden: true, textContent: '' },
    };
    const toasts = [], timers = [], history = [];
    const context = vm.createContext({ window: { location: { href: '' }, history: { state: null, replaceState: (...args) => history.push(args) } }, document: { getElementById: id => elements[id] }, fetch,
        showToast: (...args) => toasts.push(args), setTimeout: callback => timers.push(callback),
        getAllEditorQuestions: () => [], scoringIsComplete: () => true });
    vm.runInContext(`let examPaperId=${JSON.stringify(examId)}; let examRevision='${'a'.repeat(64)}'; let examSaveBusy=false;
        let examData={pages:[]}; let examConfig={}; let redirectAfterSaveToList=false;
        ${saveSource}`, context);
    return { window: context.window, elements, button, toasts, timers, history, evaluate: code => vm.runInContext(code, context) };
}

it('K9 editor sends SSR revision once while busy and adopts the successful response revision', async () => {
    const bodies = []; let resolve;
    const pending = new Promise(done => { resolve = done; });
    const f = setup(async (_url, options) => { bodies.push(JSON.parse(options.body)); if (bodies.length === 1) await pending;
        return { ok: true, json: async () => ({ status: 'success', revision: 'b'.repeat(64) }) }; });
    const first = f.window.saveExam();
    expect(f.button.disabled).toBe(true); expect(f.button.attributes['aria-busy']).toBe('true');
    await f.window.saveExam(); expect(bodies).toHaveLength(1);
    expect(bodies[0].expected_revision).toBe('a'.repeat(64));
    resolve(); await first;
    expect(f.button.disabled).toBe(false); expect(f.button.attributes['aria-busy']).toBe('false');
    await f.window.saveExam(); expect(bodies[1].expected_revision).toBe('b'.repeat(64));
    expect(f.elements['exam-title'].value).toBe('Draft kept'); expect(f.timers).toEqual([]);
});

it('K9 revision conflict stays visible, retains every draft and never automatically retries or rebases', async () => {
    const bodies = [];
    const f = setup(async (_url, options) => { bodies.push(JSON.parse(options.body)); return { ok: false, status: 409,
        json: async () => ({ detail: { code: 'revision_conflict', message: 'Stale' } }) }; });
    await f.window.saveExam(); await Promise.resolve();
    expect(bodies).toHaveLength(1); expect(f.elements['exam-save-conflict'].hidden).toBe(false);
    expect(f.elements['exam-save-conflict'].textContent).toContain('当前输入已保留');
    expect(f.elements['exam-title'].value).toBe('Draft kept'); expect(f.elements['exam-desc'].value).toBe('Unsent explanation');
    expect(f.button.disabled).toBe(false); expect(f.timers).toEqual([]);
    await f.window.saveExam(); expect(bodies[1].expected_revision).toBe('a'.repeat(64));
    expect(f.elements['exam-save-conflict'].hidden).toBe(false);
});

it('K9 failed response or network error releases busy and leaves the old token for a deliberate retry', async () => {
    const bodies = [];
    const f = setup(async (_url, options) => { bodies.push(JSON.parse(options.body));
        if (bodies.length === 1) throw new Error('Response lost');
        return { ok: false, status: 409, json: async () => ({ detail: '已有学生提交或草稿' }) };
    });
    await f.window.saveExam(); expect(f.button.disabled).toBe(false);
    await f.window.saveExam(); expect(bodies[1].expected_revision).toBe('a'.repeat(64));
    expect(f.toasts.at(-1)[0]).toContain('已有学生提交或草稿');
    expect(f.elements['exam-title'].value).toBe('Draft kept');
    expect(f.elements['exam-save-conflict'].hidden).toBe(true); expect(f.timers).toEqual([]);
});

it('K9 creation adopts its identity immediately and later saves update that paper without a second POST', async () => {
    const requests = []; let resolve;
    const pending = new Promise(done => { resolve = done; });
    const f = setup(async (url, options) => { requests.push({ url, method: options.method, body: JSON.parse(options.body) });
        if (requests.length === 1) await pending;
        return { ok: true, json: async () => ({ status: 'success', paper_id: 'new-paper', revision: 'b'.repeat(64) }) };
    }, { examId: '' });
    const first = f.window.saveExam(); await f.window.saveExam();
    expect(requests).toHaveLength(1); expect(requests[0].url).toBe('/api/exam-papers');
    expect(requests[0].method).toBe('POST'); expect(requests[0].body).not.toHaveProperty('expected_revision');
    resolve(); await first;
    expect(f.button.disabled).toBe(false); expect(f.timers).toHaveLength(0);
    expect(f.history).toEqual([[null, '', '/exam/new-paper/edit']]);
    await f.window.saveExam();
    expect(requests[1]).toMatchObject({ method: 'PUT', url: '/api/exam-papers/new-paper', body: { expected_revision: 'b'.repeat(64) } });
});

it('K9 edits typed during creation remain unsaved and editable, then persist through PUT using the created revision', async () => {
    const requests = []; let resolve;
    const pending = new Promise(done => { resolve = done; });
    const f = setup(async (url, options) => { requests.push({ url, method: options.method, body: JSON.parse(options.body) });
        if (requests.length === 1) await pending;
        return { ok: true, json: async () => ({ status: 'success', paper_id: 'created-once', revision: 'b'.repeat(64) }) };
    }, { examId: '' });
    const first = f.window.saveExam();
    f.elements['exam-title'].value = 'Typed while POST was pending';
    f.elements['exam-desc'].value = 'Additional edits remain local';
    resolve(); await first;
    expect(f.elements['exam-title'].value).toBe('Typed while POST was pending');
    expect(f.button.disabled).toBe(false); expect(f.button.attributes['aria-busy']).toBe('false');
    expect(f.evaluate('editorSnapshot() === savedEditorSnapshot')).toBe(false);
    expect(f.timers).toEqual([]); expect(f.window.location.href).toBe('');
    await f.window.saveExam();
    expect(requests.map(request => request.method)).toEqual(['POST', 'PUT']);
    expect(requests[1]).toMatchObject({ url: '/api/exam-papers/created-once', body: {
        title: 'Typed while POST was pending', description: 'Additional edits remain local', expected_revision: 'b'.repeat(64),
    } });
    expect(f.evaluate('editorSnapshot() === savedEditorSnapshot')).toBe(true);
});

it('K9 a delayed list redirect never discards edits made after the save response and leaves save available', async () => {
    const f = setup(async () => ({ ok: true, json: async () => ({ status: 'success', revision: 'b'.repeat(64) }) }));
    f.evaluate('redirectAfterSaveToList = true');
    await f.window.saveExam();
    expect(f.timers).toHaveLength(1); expect(f.button.disabled).toBe(false);
    f.elements['exam-desc'].value = 'Keep me on the editor';
    f.timers[0]();
    expect(f.window.location.href).toBe(''); expect(f.button.disabled).toBe(false);
    expect(f.evaluate('editorSnapshot() === savedEditorSnapshot')).toBe(false);
});
