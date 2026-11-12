// Shared, data-only question contract. Legacy values remain unchanged on disk.
export const QUESTION_TYPE_LABELS = Object.freeze({
    radio: '单选题', checkbox: '多选题', boolean: '判断题', text: '填空题',
    textarea: '简答题', analysis: '分析题', code: '程序题', comprehensive: '综合题',
});
const ALIASES = {
    single: 'radio', single_choice: 'radio', choice: 'radio', '单选': 'radio', '单选题': 'radio', '选择题': 'radio',
    multiple: 'checkbox', multiple_choice: 'checkbox', multi: 'checkbox', multiple_select: 'checkbox', multi_choice: 'checkbox', '多选': 'checkbox', '多选题': 'checkbox',
    true_false: 'boolean', truefalse: 'boolean', bool: 'boolean', judge: 'boolean', judgement: 'boolean', judgment: 'boolean', '判断': 'boolean', '判断题': 'boolean',
    blank: 'text', fill_blank: 'text', fill: 'text', completion: 'text', '填空': 'text', '填空题': 'text',
    essay: 'textarea', short_answer: 'textarea', 'short-answer': 'textarea', shortanswer: 'textarea',
    qa: 'textarea', question_answer: 'textarea', subjective: 'textarea', open: 'textarea', '简答': 'textarea', '简答题': 'textarea', '问答': 'textarea', '问答题': 'textarea', '主观题': 'textarea', '论述题': 'textarea',
    analyze: 'analysis', analyse: 'analysis', case_analysis: 'analysis', '分析': 'analysis', '分析题': 'analysis', programming: 'code', program: 'code', coding: 'code', '程序': 'code', '程序题': 'code', '编程': 'code', '编程题': 'code',
    composite: 'comprehensive', integrated: 'comprehensive', '综合': 'comprehensive', '综合题': 'comprehensive',
};
export function normalizeQuestionType(type) {
    const value = String(type || 'textarea').trim().toLowerCase().replace(/[- ]/g, '_');
    return ALIASES[value] || value;
}
export function questionBlanks(question) {
    return normalizeQuestionType(question?.type) === 'text' && Array.isArray(question?.blanks)
        ? question.blanks.filter(blank => blank && blank.id != null) : [];
}
export function blankAnswerValues(question, value, blankIds) {
    const blanks = questionBlanks(question);
    if (!blanks.length) return [];
    if (Array.isArray(value)) {
        const positions = Array.isArray(blankIds) ? new Map(blankIds.map((id, index) => [String(id), index])) : null;
        return blanks.map((blank, index) => {
            const position = positions ? positions.get(String(blank.id)) ?? -1 : index;
            return String(position >= 0 ? value[position] ?? '' : '');
        });
    }
    // Do not split legacy prose on commas, punctuation, or whitespace.
    return blanks.map((_, index) => index === 0 ? String(value ?? '') : '');
}
export function answerHasText(value) {
    return Array.isArray(value) ? value.some(answerHasText) : Boolean(String(value ?? '').trim());
}
export function answerIsComplete(question, value) {
    const blanks = questionBlanks(question);
    return blanks.length ? blankAnswerValues(question, value).every(answerHasText) : answerHasText(value);
}
export function answerDisplayText(question, value, blankIds) {
    const blanks = questionBlanks(question);
    if (blanks.length) return blankAnswerValues(question, value, blankIds)
        .map((answer, index) => `第 ${blanks[index].id} 空：${answer || '（未作答）'}`).join('\n\n');
    return Array.isArray(value) ? value.map(String).join('\n\n') : String(value ?? '');
}
export function questionTextForDisplay(question) {
    const ids = new Set(questionBlanks(question).map(blank => String(blank.id)));
    return String(question?.text ?? question?.title ?? question?.question ?? question?.prompt ?? '').replace(/\{\{\s*([1-9]\d*)\s*\}\}/g, (match, id) => ids.has(id) ? `（第 ${id} 空）` : match);
}
