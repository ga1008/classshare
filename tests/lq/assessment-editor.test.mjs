import { describe, it, expect } from 'vitest';
import { markdownKeyEdit, alignmentEdit, codeKeyEdit, parseCodeBlocks, detectCodeLanguage, highlightAssessmentCode } from '../../static/js/assessment_editor.js';

const edit = (value, change) => change ? value.slice(0, change.start) + change.text + value.slice(change.end) : value;

describe('assessment Markdown edits', () => {
    it('splits a numbered line at the caret and continues its number', () => {
        const value = '3. 前半段后半段';
        const change = markdownKeyEdit(value, 6, 6, 'Enter');
        expect(edit(value, change)).toBe('3. 前半段\n4. 后半段');
        expect(change.caret).toBe(10);
    });
    it('continues nested and unordered lists, with empty item exit', () => {
        expect(edit('    2) 子项', markdownKeyEdit('    2) 子项', 9, 9, 'Enter'))).toBe('    2) 子项\n    3) ');
        expect(edit('- 内容', markdownKeyEdit('- 内容', 4, 4, 'Enter'))).toBe('- 内容\n- ');
        expect(edit('4. ', markdownKeyEdit('4. ', 3, 3, 'Enter'))).toBe('');
        expect(edit('    2. ', markdownKeyEdit('    2. ', 7, 7, 'Enter'))).toBe('1. ');
    });
    it('leaves plain text, Shift+Enter and fenced code native', () => {
        expect(markdownKeyEdit('ordinary text', 13, 13, 'Enter')).toBeNull();
        expect(markdownKeyEdit('1. text', 7, 7, 'Enter', true)).toBeNull();
        const value = '```python\n1. text';
        expect(markdownKeyEdit(value, value.length, value.length, 'Enter')).toBeNull();
        expect(markdownKeyEdit('ordinary text', 2, 2, 'Tab')).toBeNull();
    });
    it('indents a multiline list without losing the selected range', () => {
        const value = '1. one\n2. two';
        const change = markdownKeyEdit(value, 0, value.length, 'Tab');
        expect(edit(value, change)).toBe('    1. one\n    2. two');
        const indented = edit(value, change);
        expect(edit(indented, markdownKeyEdit(indented, 0, indented.length, 'Tab', true))).toBe(value);
    });
    it('changes an existing paragraph alignment without nesting directives', () => {
        const value = ':::align right\n需要居中的内容\n:::';
        const start = value.indexOf('需要');
        const result = edit(value, alignmentEdit(value, start, start + 7, 'center'));
        expect(result).toBe(':::align center\n需要居中的内容\n:::');
    });
});

describe('code block editing', () => {
    it('matches complete variable-length fences and preserves CRLF offsets', () => {
        const text = '说明\r\n````python\r\nprint("```")\r\n````\r\n末尾';
        const [block] = parseCodeBlocks(text);
        expect(block.code).toBe('print("```")');
        expect(block.language).toBe('python');
        expect(text.slice(block.start, block.end)).toBe('````python\r\nprint("```")\r\n````');
        expect(parseCodeBlocks('```js\nnot closed')).toEqual([]);
        expect(parseCodeBlocks('~~~js\ncode\n~~~')).toHaveLength(1);
    });
    it('creates paired brackets, wraps selections and skips an existing closer', () => {
        expect(edit('answer', codeKeyEdit('answer', 0, 6, '('))).toBe('(answer)');
        expect(edit('', codeKeyEdit('', 0, 0, '{'))).toBe('{}');
        expect(codeKeyEdit('()', 1, 1, ')')).toMatchObject({ moveOnly: true, caret: 2 });
        expect(edit('[]', codeKeyEdit('[]', 1, 1, 'Backspace'))).toBe('');
    });
    it('uses four-space stops and keeps a closing bracket at the outer indent', () => {
        expect(edit('x', codeKeyEdit('x', 1, 1, 'Tab'))).toBe('x   ');
        expect(edit('    if (ok) {}', codeKeyEdit('    if (ok) {}', 13, 13, 'Enter'))).toBe('    if (ok) {\n        \n    }');
        expect(edit('if valid:', codeKeyEdit('if valid:', 9, 9, 'Enter'))).toBe('if valid:\n    ');
    });
    it.each([
        ['def hello():\n    print("hi")', 'python'], ['const n = 1;', 'javascript'],
        ['SELECT name FROM students', 'sql'], ['#include <stdio.h>\nint main() {}', 'c'],
        ['<div>Hello</div>', 'html'], ['{"ok":true}', 'json'], ['hello world', 'text'],
    ])('detects %j as %s without external grammars', (code, language) => expect(detectCodeLanguage(code)).toBe(language));
    it('escapes all HTML, marks brackets, and uses a bounded fallback for huge input', () => {
        const html = highlightAssessmentCode('if (x) { "<script>alert(1)</script>" }', 'javascript', 3);
        expect(html).toContain('assessment-code-keyword');
        expect(html).toContain('assessment-code-match');
        expect(html).toContain('&lt;script&gt;');
        expect(html).not.toContain('<script>');
        expect(highlightAssessmentCode('<'.repeat(100001), 'html')).toBe('&lt;'.repeat(100001));
    });
    it('pairs syntax brackets across strings and comments without matching their literal contents', () => {
        const marked = character => `<mark class="assessment-code-match">${character}</mark>`;
        expect(highlightAssessmentCode('print(")")', 'python', 5)).toBe(`print${marked('(')}<span class="assessment-code-string">&quot;)&quot;</span>${marked(')')}`);
        expect(highlightAssessmentCode('print(")")', 'python', 9)).toBe(`print${marked('(')}<span class="assessment-code-string">&quot;)&quot;</span>${marked(')')}`);
        expect(highlightAssessmentCode('print(")")', 'python', 7)).not.toContain('assessment-code-match');
        expect(highlightAssessmentCode('call(/* ) */ value)', 'javascript', 4)).toBe(`call${marked('(')}<span class="assessment-code-comment">/* ) */</span> value${marked(')')}`);
        expect(highlightAssessmentCode('call(\n# ) comment\nvalue)', 'python', 4)).toBe(`call${marked('(')}\n<span class="assessment-code-comment"># ) comment</span>\nvalue${marked(')')}`);
        expect(highlightAssessmentCode('call([)]', 'javascript', 4).match(/assessment-code-match/g)).toHaveLength(1);
    });
    it('consumes incomplete strings and trailing escapes without losing or executing source', () => {
        const quote = String.fromCharCode(34), slash = String.fromCharCode(92);
        for (const value of [quote + '<script>' + slash, "'" + '<img onerror=1>' + slash, '`<svg>' + slash, quote + slash + quote + '<script>']) {
            const html = highlightAssessmentCode(value, 'javascript');
            expect(html).toMatch(/^<span class="assessment-code-string">/);
            expect(html).not.toContain('<script>');
            expect(html).not.toContain('<img');
            expect(html).not.toContain('<svg');
            const unwrapped = html.replace(/^<span class="assessment-code-string">|<\/span>$/g, '');
            expect(unwrapped).toBe(value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'));
        }
        expect(highlightAssessmentCode('/* <script> /* unfinished', 'c')).toBe('<span class="assessment-code-comment">/* &lt;script&gt; /* unfinished</span>');
    });
    it('keeps 90 KB adversarial unfinished comment and string drafts below the interaction budget', () => {
        const quote = String.fromCharCode(34), slash = String.fromCharCode(92);
        const cases = ['/* '.repeat(30000), quote + (slash + quote).repeat(44000) + slash, quote + slash.repeat(89999)];
        // Warm the code path, then take the median of three independent calls
        // so one scheduler/GC interruption does not make this a timing lottery.
        highlightAssessmentCode('/* warmup', 'javascript');
        for (const source of cases) {
            const times = [];
            for (let i = 0; i < 3; i++) {
                const start = performance.now();
                const html = highlightAssessmentCode(source, 'javascript');
                times.push(performance.now() - start);
                expect(html).toContain('assessment-code-');
            }
            expect(times.sort((a, b) => a - b)[1]).toBeLessThan(150);
        }
    });
});
