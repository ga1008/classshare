import { describe, it, expect } from 'vitest';
import { questionBlanks, blankAnswerValues, answerHasText, answerIsComplete, answerDisplayText, questionTextForDisplay, normalizeQuestionType } from '../../static/js/assessment_questions.js';

describe('assessment question answer contract', () => {
    const question = { type: 'text', text: '先 {{2}} 再 {{7}}', blanks: [{id:'2'}, {id:'7'}] };
    it('preserves empty slots and maps explicit identifiers', () => {
        expect(blankAnswerValues(question, ['second', 'first'], ['7', '2'])).toEqual(['first', 'second']);
        expect(blankAnswerValues(question, ['', 'value'])).toEqual(['', 'value']);
        expect(answerHasText(['', ''])).toBe(false);
        expect(answerIsComplete(question, ['', 'value'])).toBe(false);
        expect(answerIsComplete(question, ['first', 'second'])).toBe(true);
    });
    it('does not guess legacy punctuation as separate answers', () => {
        expect(questionBlanks({type:'text', answer:'x,y'})).toEqual([]);
        expect(answerDisplayText({type:'text'}, 'x,y')).toBe('x,y');
        expect(blankAnswerValues(question, 'x,y')).toEqual(['x,y', '']);
    });
    it('labels numbered blanks without leaking reference answers', () => {
        expect(questionTextForDisplay(question)).toBe('先 （第 2 空） 再 （第 7 空）');
        expect(answerDisplayText(question, ['', 'B'])).toBe('第 2 空：（未作答）\n\n第 7 空：B');
        expect(normalizeQuestionType('true_false')).toBe('boolean');
        expect(normalizeQuestionType('programming')).toBe('code');
    });
});
