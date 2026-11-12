import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';
import { normalizeQuestionType } from '../../static/js/assessment_questions.js';

const template = readFileSync(new URL('../../templates/exam_editor.html', import.meta.url), 'utf8');
const source = template.slice(template.indexOf('        function optionAnswerValue('), template.indexOf('        function splitCheckboxAnswer('))
    + template.slice(template.indexOf('        function blankMarker('), template.indexOf('        // === 预览 ==='));

function setup(questions, confirmed = true) {
    const data = { pages: [{ questions }] };
    const window = { renderQuestionEditor() {}, renderPreview() {}, renderPageList() {} };
    const context = vm.createContext({ window, confirm: () => confirmed, confirmAssessmentAction: async () => confirmed, normalizeQuestionType,
        setTimeout() {}, queuePreview() {}, document: { querySelector: () => null },
        isChoiceQuestion: q => ['radio', 'checkbox', 'boolean'].includes(normalizeQuestionType(q.type)),
        answerHasValueForQuestion: q => Boolean(String(q.answer || '').trim()),
        getAllEditorQuestions: () => data.pages.flatMap(page => page.questions.map(q => ({ q }))),
    });
    context.data = data;
    vm.runInContext(`let examData=data, currentPageIdx=0; const removedBlankAnswers=new WeakMap(); ${source}`, context);
    return { context, window, data, evaluate: code => vm.runInContext(code, context) };
}

describe('teacher numbered blank and type edits preserve answer ownership', () => {
    it('retains punctuation and line breaks per blank, deduplicates repeated markers, and restores a temporarily removed marker', () => {
        const q = { id: 'q1', type: 'text', text: '{{1}} + {{3}}', blanks: [{ id: '1', answer: 'a,b\nc' }, { id: '3', answer: 'zero' }], answer: ['a,b\nc', 'zero'] };
        const f = setup([q]);
        f.window.updateQText(0, '{{ 3 }} + {{1}} and {{3}}');
        expect(q.blanks.map(b => b.id)).toEqual(['3', '1']);
        expect(q.answer).toEqual(['zero', 'a,b\nc']);
        f.window.updateQText(0, '{{3}} + {');
        expect(q.answer).toEqual(['zero']);
        f.window.updateQText(0, '{{3}} + {{1}}');
        expect(q.answer).toEqual(['zero', 'a,b\nc']);
        f.window.updateBlankAnswer(0, 1, 'new, unchanged\nseparator');
        expect(q.blanks[0].answer).toBe('zero');
        expect(q.answer[1]).toBe('new, unchanged\nseparator');
    });

    it('does not infer or rewrite legacy answers on text edits; explicit conversion preserves the entire original answer', async () => {
        const q = { id: 'old', type: 'text', text: 'Legacy {{2}} notation', answer: 'A, B；C\nD' };
        const f = setup([q]);
        f.window.updateQText(0, 'Legacy {{2}} changed');
        expect(q).not.toHaveProperty('blanks');
        expect(q.answer).toBe('A, B；C\nD');
        await f.window.enableNumberedBlanks(0);
        expect(q.blanks.find(b => b.id === '1').answer).toBe('A, B；C\nD');
        expect(q.text).toContain('{{1}}');
    });

    it('allocates unique IDs after deleting an earlier question and initializes new boolean and blank types', () => {
        const f = setup([{ id: 'p1_q2', type: 'textarea' }]);
        f.window.addQuestion('boolean');
        f.window.addQuestion('text');
        const questions = f.data.pages[0].questions;
        expect(new Set(questions.map(q => q.id)).size).toBe(3);
        expect(questions[1]).toMatchObject({ type: 'boolean', options: ['正确', '错误'] });
        expect(questions[2]).toMatchObject({ type: 'text', text: '{{1}}', blanks: [{ id: '1', answer: '' }], answer: [''] });
    });

    it('changes subjective labels without dropping existing answer text or attachment requirements', () => {
        const q = { id: 'subjective', type: 'textarea', answer: '1. Explain\n2. Prove', attachment_requirements: { required: true, min_count: 1 } };
        const f = setup([q]);
        f.window.changeQType(0, 'analysis');
        f.window.changeQType(0, 'code');
        f.window.changeQType(0, 'comprehensive');
        expect(q).toMatchObject({ type: 'comprehensive', answer: '1. Explain\n2. Prove', attachment_requirements: { required: true, min_count: 1 } });
    });

    it('canceling a shape change or numbered blank conversion leaves legacy data unchanged', async () => {
        const q = { id: 'legacy', type: 'text', text: 'Legacy', answer: 'A, B' };
        const f = setup([q], false);
        const before = JSON.stringify(q);
        await f.window.enableNumberedBlanks(0);
        expect(JSON.stringify(q)).toBe(before);
        const choice = { id: 'choice', type: 'checkbox', options: ['A', 'B'], answer: ['A', 'B'] };
        f.data.pages[0].questions.push(choice);
        await f.window.changeQType(1, 'textarea');
        expect(choice).toEqual({ id: 'choice', type: 'checkbox', options: ['A', 'B'], answer: ['A', 'B'] });
    });

    it('remaps positional choice answers when earlier options are removed and clears a deleted correct option', () => {
        const q = { id: 'choices', type: 'radio', options: ['first', 'second', 'third'], answer: 'C' };
        const f = setup([q]);
        f.window.removeOption(0, 0);
        expect(q).toMatchObject({ options: ['second', 'third'], answer: 'B' });
        f.window.removeOption(0, 1);
        expect(q.answer).toBe('');
        const empty = { id: 'empty', type: 'checkbox', options: ['', '', ''] };
        f.data.pages[0].questions.push(empty);
        f.window.removeOption(1, 0);
        expect(empty.answer).toEqual([]);
    });

    it('preserves an answer stored as exact option text while editing that option', () => {
        const q = { id: 'choice-text', type: 'boolean', options: ['正确', '错误'], answer: '正确' };
        const f = setup([q]);
        f.window.updateOption(0, 0, '**正确**');
        expect(q.answer).toBe('**正确**');
    });
});
