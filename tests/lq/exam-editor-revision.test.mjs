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
    const toasts = [], timers = [];
    const context = vm.createContext({ window: { location: { href: '' } }, document: { getElementById: id => elements[id] }, fetch,
        showToast: (...args) => toasts.push(args), setTimeout: callback => timers.push(callback),
        getAllEditorQuestions: () => [], scoringIsComplete: () => true });
    vm.runInContext(`const EXAM_ID=${JSON.stringify(examId)}; let examRevision='${'a'.repeat(64)}'; let examSaveBusy=false;
        let examData={pages:[]}; let examConfig={}; let redirectAfterSaveToList=false;
        ${saveSource}`, context);
    return { window: context.window, elements, button, toasts, timers };
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

it('K9 creation keeps the original POST contract and prevents duplicate creation during the redirect delay', async () => {
    const requests = [];
    const f = setup(async (url, options) => { requests.push({ url, method: options.method, body: JSON.parse(options.body) });
        return { ok: true, json: async () => ({ status: 'success', paper_id: 'new-paper' }) };
    }, { examId: '' });
    await f.window.saveExam(); await f.window.saveExam();
    expect(requests).toHaveLength(1); expect(requests[0].url).toBe('/api/exam-papers');
    expect(requests[0].method).toBe('POST'); expect(requests[0].body).not.toHaveProperty('expected_revision');
    expect(f.button.disabled).toBe(true); expect(f.timers).toHaveLength(1);
    f.timers[0](); expect(f.window.location.href).toBe('/exam/new-paper/edit');
});
