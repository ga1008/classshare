/** Small, explicit Markdown editor for assessment authoring and answers.
 * The caller's textarea remains the form/draft source of truth. No page scan,
 * contenteditable, network request, global key handler, or extra editor package.
 */
import { createDomainButton, adoptDomainControl } from './lq/domain-controls.js';
import { openDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { bindDirtyGuard } from './lq/dirty-guard.js';

const mounted = new WeakMap();
const autosizers = new WeakMap();
const resizeOwners = new WeakMap();
const escapeHtml = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const pairs = { '(': ')', '[': ']', '{': '}' };
const languages = ['text', 'python', 'javascript', 'typescript', 'java', 'c', 'cpp', 'csharp', 'html', 'css', 'sql', 'json', 'bash'];
const languageNames = { text: '纯文本', python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript', java: 'Java', c: 'C', cpp: 'C++', csharp: 'C#', html: 'HTML', css: 'CSS', sql: 'SQL', json: 'JSON', bash: 'Shell' };
const languageAliases = { js: 'javascript', ts: 'typescript', py: 'python', 'c++': 'cpp', cs: 'csharp', sh: 'bash', shell: 'bash', xml: 'html', plaintext: 'text' };
const normalizeLanguage = value => languageAliases[String(value).toLowerCase()] || (languages.includes(String(value).toLowerCase()) ? String(value).toLowerCase() : 'text');
const codeKeywords = new Set('if else elif for while do break continue return yield def class import from as in is not and or try except finally raise with lambda pass async await const let var function new this typeof switch case default throw catch public private protected static void int float double char bool boolean string interface type extends implements package using namespace include select from where insert into update delete create table join on group by order limit true false null none'.split(' '));
const isDigit = char => char >= '0' && char <= '9';
const isWordStart = char => char >= 'A' && char <= 'Z' || char >= 'a' && char <= 'z' || char === '_';
const isHexDigit = char => isDigit(char) || char >= 'a' && char <= 'f' || char >= 'A' && char <= 'F';

function renderMarkdownHtml(value) {
    const text = String(value || '');
    const host = document.createElement('div');
    const runtime = window.MarkdownRuntime;
    if (runtime?.renderIntoElement) runtime.renderIntoElement(host, text, { emptyHtml: '', fallbackMode: 'lines', silent: true });
    else host.innerHTML = escapeHtml(text).replace(/\n/g, '<br>');
    return host.innerHTML;
}

/** Only complete fenced blocks are editable cards. Offsets always refer to the
 * actual source, including CRLF and fences longer than three backticks. */
export function parseCodeBlocks(value) {
    const text = String(value || '');
    const lines = [...text.matchAll(/[^\n]*(?:\n|$)/g)];
    const result = [];
    let open = null;
    for (const line of lines) {
        const raw = line[0].replace(/\r?\n$/, '');
        const fence = raw.match(/^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/);
        if (!fence) continue;
        if (!open) {
            if (fence[1][0] === '`' && fence[2].includes('`')) continue;
            open = { start: line.index, contentStart: line.index + line[0].length, fence: fence[1], language: fence[2].trim().split(/\s/)[0] || '' };
        } else if (fence[1][0] === open.fence[0] && fence[1].length >= open.fence.length && !fence[2].trim()) {
            result.push({ ...open, end: line.index + raw.length, code: text.slice(open.contentStart, line.index).replace(/\r?\n$/, '') });
            open = null;
        }
    }
    return result;
}

function insideFence(value, position) {
    let fence = null;
    for (const line of value.slice(0, position).split('\n')) {
        const match = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
        if (!match) continue;
        if (!fence) fence = match[1];
        else if (match[1][0] === fence[0] && match[1].length >= fence.length && !match[2].trim()) fence = null;
    }
    return !!fence;
}

/** Pure edit descriptions make list/code semantics testable independently of
 * browser key delivery. Null leaves the browser's native action untouched. */
export function markdownKeyEdit(value, start, end, key, shift = false) {
    if (insideFence(value, start)) return null;
    const lineStart = value.lastIndexOf('\n', start - 1) + 1;
    const lineEndIndex = value.indexOf('\n', end);
    const lineEnd = lineEndIndex < 0 ? value.length : lineEndIndex;
    const before = value.slice(lineStart, start);
    const match = before.match(/^(\s*)(\d+[.)]|[-+*])\s+(.*)$/);
    if (key === 'Enter' && !shift && match) {
        // Empty list items end the current nesting level. At the first level,
        // Enter removes the marker; normal prose continues on the same line.
        if (!match[3].trim() && !value.slice(end, lineEnd).trim()) {
            const indent = match[1].replace(/ {1,4}$/, '');
            const replacement = match[1].length ? `${indent}1. ` : '';
            return { start: lineStart, end: lineEnd, text: replacement, caret: lineStart + replacement.length };
        }
        const number = match[2].match(/^(\d+)([.)])$/);
        const marker = number ? `${Number(number[1]) + 1}${number[2]}` : match[2];
        const text = `\n${match[1]}${marker} `;
        return { start, end, text, caret: start + text.length };
    }
    if (key === 'Tab' && /^(\s*)(?:\d+[.)]|[-+*])\s/.test(value.slice(lineStart, lineEnd))) {
        const selected = value.slice(lineStart, lineEnd);
        const transformed = selected.split('\n').map(line => shift ? line.replace(/^ {1,4}/, '') : `    ${line}`).join('\n');
        const delta = transformed.length - selected.length;
        return { start: lineStart, end: lineEnd, text: transformed, caret: Math.max(lineStart, start + (shift ? Math.max(-4, delta) : 4)), selectionEnd: Math.max(lineStart, end + delta) };
    }
    return null;
}

export function alignmentEdit(value, selectionStart, selectionEnd, alignment) {
    if (!['left', 'center', 'right'].includes(alignment)) return null;
    let start = value.lastIndexOf('\n', selectionStart - 1) + 1;
    const next = value.indexOf('\n', selectionEnd);
    let end = next < 0 ? value.length : next;
    let content = value.slice(start, end) || '内容';
    const before = value.slice(0, start);
    const opens = [...before.matchAll(/^:::align (?:left|center|right)[ \t]*\r?$/gm)];
    const open = opens.at(-1);
    if (open && !insideFence(value, open.index)) {
        const bodyStart = open.index + open[0].length + 1;
        const closing = value.slice(bodyStart).match(/^:::[ \t]*\r?$/m);
        const bodyEnd = closing ? bodyStart + closing.index : -1;
        // Re-aligning the same paragraph changes its existing wrapper instead
        // of accumulating nested pseudo-Markdown directives in the answer.
        if (bodyEnd >= end) {
            start = open.index; end = bodyEnd + closing[0].length;
            content = value.slice(bodyStart, bodyEnd).replace(/\r?\n$/, '');
        }
    }
    const prefix = `:::align ${alignment}\n`;
    return { start, end, text: `${prefix}${content}\n:::`, caret: start + prefix.length, selectionEnd: start + prefix.length + content.length };
}

export function codeKeyEdit(value, start, end, key, shift = false) {
    const lineStart = value.lastIndexOf('\n', start - 1) + 1;
    const line = value.slice(lineStart, start);
    if (key === 'Tab') {
        if (start === end && !shift) {
            const text = ' '.repeat(4 - (start - lineStart) % 4);
            return { start, end, text, caret: start + text.length };
        }
        const nextLine = value.indexOf('\n', end);
        const lineEnd = nextLine < 0 ? value.length : nextLine;
        const text = value.slice(lineStart, lineEnd).split('\n').map(item => shift ? item.replace(/^ {1,4}/, '') : `    ${item}`).join('\n');
        const delta = text.length - (lineEnd - lineStart);
        return { start: lineStart, end: lineEnd, text, caret: Math.max(lineStart, start + (shift ? Math.max(-4, delta) : 4)), selectionEnd: Math.max(lineStart, end + delta) };
    }
    if (key === 'Enter') {
        const indent = line.match(/^\s*/)[0];
        const more = /[({[,:]\s*$/.test(line) ? '    ' : '';
        const after = value.slice(end);
        const closing = pairs[line.trimEnd().slice(-1)];
        const paired = more && closing && closing === after[0];
        const text = `\n${indent}${more}${paired ? `\n${indent}` : ''}`;
        return { start, end, text, caret: start + 1 + indent.length + more.length };
    }
    if (pairs[key]) {
        const selected = value.slice(start, end);
        return { start, end, text: `${key}${selected}${pairs[key]}`, caret: start + 1, selectionEnd: start + 1 + selected.length };
    }
    if (Object.values(pairs).includes(key) && start === end && value[start] === key) return { start, end, text: '', caret: start + 1, moveOnly: true };
    if (key === 'Backspace' && start === end && start > 0 && pairs[value[start - 1]] === value[start]) return { start: start - 1, end: start + 1, text: '', caret: start - 1 };
    return null;
}

export function detectCodeLanguage(code) {
    const text = String(code || '').slice(0, 20000).trim();
    if (/^(?:\{|\[)/.test(text)) { try { JSON.parse(text); return 'json'; } catch { /* not JSON */ } }
    if (/<(?:!doctype|html|div|script|body|span|p)\b/i.test(text)) return 'html';
    if (/(?:^|\n)\s*(?:from\s+\w+\s+import|def\s+\w+\s*\(|class\s+\w+\s*[:(]|import\s+\w+\s*$)|\b(?:print|range|len)\s*\(|(?:^|\n)\s*(?:for|if|while)\b[^\n]+:\s*(?:#.*)?$/m.test(text)) return 'python';
    if (/\b(?:SELECT\s+.+\s+FROM|INSERT\s+INTO|CREATE\s+TABLE|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b/is.test(text)) return 'sql';
    if (/#include\s*[<"]|\b(?:printf|scanf)\s*\(/.test(text)) return /\b(?:std::|cout|cin|namespace)\b/.test(text) ? 'cpp' : 'c';
    if (/\b(?:using\s+System|Console\.Write|namespace\s+\w+\s*\{)/.test(text)) return 'csharp';
    if (/\b(?:public\s+(?:static\s+)?(?:class|void)|System\.out\.|import\s+java\.)/.test(text)) return 'java';
    if (/\b(?:interface\s+\w+|type\s+\w+\s*=)|:\s*(?:string|number|boolean)\b/.test(text)) return 'typescript';
    if (/\b(?:const|let|function|var)\s+|=>|console\.log\s*\(/.test(text)) return 'javascript';
    if (/(?:^|\n)\s*[^{}\n]+\{\s*[\w-]+\s*:\s*[^{};]+[;}]/.test(text)) return 'css';
    if (/^#!.*\b(?:bash|sh)\b|(?:^|\n)\s*(?:echo|export|sudo)\s/.test(text)) return 'bash';
    return 'text';
}

function bracketPair(code, cursor) {
    const at = /[()[\]{}]/.test(code[cursor] || '') ? cursor : cursor - 1;
    if (at < 0 || !/[()[\]{}]/.test(code[at] || '')) return [];
    const openings = [];
    let targetSeen = false;
    for (const { index, end, kind } of scanCodeTokens(code)) {
        // The same lexer owns both coloring and structural matching. Brackets
        // in strings (including escaped quotes) and comments are never syntax.
        if (kind || end !== index + 1 || !/[()[\]{}]/.test(code[index])) continue;
        if (index === at) targetSeen = true;
        if (pairs[code[index]]) openings.push(index);
        else {
            const opening = openings.at(-1);
            if (opening !== undefined && pairs[code[opening]] === code[index]) {
                openings.pop();
                if (index === at || opening === at) return [at, index === at ? opening : index];
            } else {
                if (targetSeen) return [at];
                openings.length = 0;
            }
        }
    }
    return targetSeen ? [at] : [];
}

function* scanCodeTokens(text) {
    let offset = 0;
    while (offset < text.length) {
        const index = offset, char = text[offset];
        let kind = '';
        if (char === '"' || char === "'" || char === '`') {
            kind = 'string'; offset++;
            while (offset < text.length) {
                const current = text[offset++];
                // A trailing backslash remains part of an unfinished string;
                // consume an escaped character only when one actually exists.
                if (current === '\\') { if (offset < text.length) offset++; }
                else if (current === char) break;
            }
        } else if (char === '/' && text[offset + 1] === '*') {
            kind = 'comment';
            const closing = text.indexOf('*/', offset + 2);
            // Consume an unfinished comment once. A regex that restarts at each
            // later /* can scan a 90 KB draft quadratically on every keystroke.
            offset = closing < 0 ? text.length : closing + 2;
        } else if (char === '#' || char === '/' && text[offset + 1] === '/') {
            kind = 'comment';
            const newline = text.indexOf('\n', offset + 1);
            offset = newline < 0 ? text.length : newline;
        } else if (isDigit(char)) {
            kind = 'number'; offset++;
            if (char === '0' && /[xX]/.test(text[offset] || '') && isHexDigit(text[offset + 1])) {
                offset++;
                while (offset < text.length && isHexDigit(text[offset])) offset++;
            } else {
                while (offset < text.length && isDigit(text[offset])) offset++;
                if (text[offset] === '.' && isDigit(text[offset + 1])) {
                    offset++;
                    while (offset < text.length && isDigit(text[offset])) offset++;
                }
            }
        } else if (isWordStart(char)) {
            kind = 'word';
            offset++;
            while (offset < text.length && (isWordStart(text[offset]) || isDigit(text[offset]))) offset++;
        } else offset++;
        yield { index, end: offset, kind };
    }
}

/** Linear token scan. Tokens are escaped before markup, never interpreted as
 * HTML. Intentionally basic syntax assistance, not a language service. */
export function highlightAssessmentCode(source, language = 'text', cursor = -1) {
    const text = String(source || '');
    if (text.length > 100000) return escapeHtml(text);
    const matching = cursor >= 0 ? bracketPair(text, cursor) : [];
    const fragments = [];
    for (const token of scanCodeTokens(text)) {
        const { index, end } = token;
        const value = text.slice(index, end);
        const kind = token.kind === 'word' ? language !== 'text' && codeKeywords.has(value.toLowerCase()) ? 'keyword' : '' : token.kind;
        const content = escapeHtml(value);
        fragments.push(matching.includes(index) && value.length === 1 ? `<mark class="assessment-code-match">${content}</mark>` : kind ? `<span class="assessment-code-${kind}">${content}</span>` : content);
    }
    return fragments.join('');
}

export function renderAssessmentMarkdown(value) {
    const text = String(value || '');
    const lines = text.split('\n');
    let html = '', pending = [], alignment = null, fence = null;
    const flush = () => {
        if (!pending.length) return;
        const rendered = renderMarkdownHtml(pending.join('\n'));
        html += alignment ? `<div class="assessment-align-${alignment}">${rendered}</div>` : rendered;
        pending = [];
    };
    for (const line of lines) {
        const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
        if (marker) {
            if (!fence) fence = marker[1];
            else if (marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
            pending.push(line); continue;
        }
        const align = !fence && line.match(/^:::align (left|center|right)\s*$/);
        if (align && !alignment) { flush(); alignment = align[1]; }
        else if (!fence && alignment && /^:::\s*$/.test(line)) { flush(); alignment = null; }
        else pending.push(line);
    }
    flush();
    const host = document.createElement('div');
    host.innerHTML = html;
    for (const code of host.querySelectorAll('pre > code')) {
        const requested = [...code.classList].find(name => name.startsWith('language-'))?.slice(9);
        const language = requested ? normalizeLanguage(requested) : detectCodeLanguage(code.textContent);
        code.innerHTML = highlightAssessmentCode(code.textContent, language);
    }
    return host.innerHTML;
}

/** Bounded grow behavior deliberately owns only explicitly attached nodes.
 * One resize listener per document; no observers or idle animation loops. */
export function attachAutosize(textarea, { minRows = 2, maxRows = 16 } = {}) {
    if (autosizers.has(textarea)) return autosizers.get(textarea);
    if (textarea?.tagName !== 'TEXTAREA') throw new TypeError('Assessment autosize requires a textarea');
    minRows = Math.max(2, Number(minRows) || 2); maxRows = Math.max(16, minRows, Number(maxRows) || 16);
    const doc = textarea.ownerDocument, win = doc.defaultView;
    const previous = { rows: textarea.getAttribute('rows'), style: {} };
    for (const key of ['height', 'min-height', 'max-height', 'overflow-y', 'padding-top', 'padding-bottom', 'resize']) previous.style[key] = [textarea.style.getPropertyValue(key), textarea.style.getPropertyPriority(key)];
    textarea.rows = minRows; textarea.classList.add('assessment-autosize');
    let destroyed = false, frame = 0;
    function measure() {
        frame = 0;
        if (destroyed || !textarea.getClientRects().length) return;
        const css = win.getComputedStyle(textarea), line = parseFloat(css.lineHeight) || parseFloat(css.fontSize) * 1.6 || 24;
        const border = (parseFloat(css.borderTopWidth) || 0) + (parseFloat(css.borderBottomWidth) || 0);
        const padding = 10;
        const min = minRows * line + padding * 2 + border, max = maxRows * line + padding * 2 + border;
        const scrollTop = textarea.scrollTop;
        textarea.style.minHeight = '0px'; textarea.style.maxHeight = `${max}px`;
        textarea.style.paddingTop = textarea.style.paddingBottom = `${padding}px`;
        textarea.style.height = '0px';
        const natural = textarea.scrollHeight + border;
        if (minRows === 2 && natural <= line + padding * 2 + border + 1) textarea.style.paddingTop = textarea.style.paddingBottom = `${padding + line / 2}px`;
        textarea.style.minHeight = `${min}px`;
        textarea.style.height = `${Math.min(max, Math.max(min, natural))}px`;
        textarea.style.overflowY = natural > max + 1 ? 'auto' : 'hidden';
        textarea.scrollTop = scrollTop;
    }
    const refresh = () => { if (!destroyed && !frame) frame = win.requestAnimationFrame(measure); };
    let owner = resizeOwners.get(win);
    if (!owner) {
        const controls = new Set(), onResize = () => controls.forEach(item => item());
        owner = { controls, onResize }; resizeOwners.set(win, owner); win.addEventListener('resize', onResize);
    }
    owner.controls.add(refresh); textarea.addEventListener('input', refresh); textarea.addEventListener('change', refresh);
    const api = { refresh, destroy() {
        if (destroyed) return;
        destroyed = true; if (frame) win.cancelAnimationFrame(frame);
        textarea.removeEventListener('input', refresh); textarea.removeEventListener('change', refresh);
        owner.controls.delete(refresh);
        if (!owner.controls.size) { win.removeEventListener('resize', owner.onResize); resizeOwners.delete(win); }
        if (previous.rows === null) textarea.removeAttribute('rows'); else textarea.setAttribute('rows', previous.rows);
        for (const [key, [value, priority]] of Object.entries(previous.style)) value ? textarea.style.setProperty(key, value, priority) : textarea.style.removeProperty(key);
        textarea.classList.remove('assessment-autosize'); autosizers.delete(textarea);
    } };
    autosizers.set(textarea, api); refresh(); return api;
}

function applyEdit(textarea, edit) {
    if (!edit || textarea.disabled || textarea.readOnly) return false;
    textarea.focus({ preventScroll: true });
    if (edit.moveOnly) { textarea.setSelectionRange(edit.caret, edit.caret); return true; }
    textarea.setSelectionRange(edit.start, edit.end);
    const before = textarea.value;
    const expected = before.slice(0, edit.start) + edit.text + before.slice(edit.end);
    if (textarea.maxLength >= 0 && expected.length > textarea.maxLength) return false;
    let nativeEvent = false;
    const noticed = () => { nativeEvent = true; };
    textarea.addEventListener('input', noticed);
    try {
        // execCommand remains the only broadly supported way of extending a
        // textarea's native undo stack; setRangeText is the non-support fallback.
        if (textarea.ownerDocument.activeElement === textarea && !textarea.closest('[inert]')) {
            try { textarea.ownerDocument.execCommand('insertText', false, edit.text); } catch { /* fallback below */ }
        }
        if (textarea.value !== expected) textarea.setRangeText(edit.text, edit.start, edit.end, 'end');
    } finally { textarea.removeEventListener('input', noticed); }
    textarea.setSelectionRange(edit.caret, edit.selectionEnd ?? edit.caret);
    if (!nativeEvent && textarea.value !== before) textarea.dispatchEvent(new textarea.ownerDocument.defaultView.Event('input', { bubbles: true }));
    return true;
}

function fencedCode(code, language) {
    const longest = Math.max(2, ...[...code.matchAll(/`+/g)].map(match => match[0].length));
    const fence = '`'.repeat(longest + 1);
    return `${fence}${language === 'text' ? '' : language}\n${code}\n${fence}`;
}

export function mountAssessmentEditor(textarea, { format = true, code = false, label = '答案编辑器' } = {}) {
    if (mounted.has(textarea)) return mounted.get(textarea);
    if (textarea?.tagName !== 'TEXTAREA' || !textarea.parentNode) throw new TypeError('Assessment editor requires a mounted textarea');
    const doc = textarea.ownerDocument, win = doc.defaultView, disposers = [];
    let disposed = false, composing = false, compositionTimer = 0, renderFrame = 0, dialog = null, previewMode = false;
    let renderedValue = null, renderedState = null;
    const originalParent = textarea.parentNode, originalNext = textarea.nextSibling;
    const shell = doc.createElement('div'); shell.className = 'assessment-editor'; shell.setAttribute('role', 'group'); shell.setAttribute('aria-label', label);
    const toolbar = doc.createElement('div'); toolbar.className = 'assessment-editor__toolbar lq-domain-toolbar'; toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', '格式工具');
    const body = doc.createElement('div'); body.className = 'assessment-editor__body';
    const cards = doc.createElement('div'); cards.className = 'assessment-editor__codes';
    const preview = doc.createElement('div'); preview.className = 'assessment-editor__preview md-content'; preview.hidden = true;
    const hint = doc.createElement('small'); hint.className = 'assessment-editor__hint'; hint.textContent = '支持 Markdown；编号行按 Enter 续号，Tab / Shift+Tab 调整层级。';
    originalParent.insertBefore(shell, textarea); shell.append(toolbar, body); body.append(textarea, cards, preview); if (format) shell.append(hint);
    const autosize = attachAutosize(textarea);
    const listen = (node, event, callback, options) => { node.addEventListener(event, callback, options); disposers.push(() => node.removeEventListener(event, callback, options)); };
    const controls = [];
    const addButton = (labelText, action, extra = {}) => {
        const button = createDomainButton({ label: labelText, variant: 'ghost', size: 'sm', attrs: { title: extra.title || labelText, 'aria-label': labelText, ...extra.attrs } }, doc);
        // Keep the textarea's selection while a pointer operates its toolbar.
        listen(button, 'mousedown', event => event.preventDefault());
        listen(button, 'click', () => { if (!textarea.disabled && !textarea.readOnly) action(button); });
        toolbar.append(button); controls.push(button); return button;
    };
    const selectionEdit = (prefix, suffix = prefix, placeholder = '') => {
        const start = textarea.selectionStart, end = textarea.selectionEnd, selected = textarea.value.slice(start, end) || placeholder;
        applyEdit(textarea, { start, end, text: prefix + selected + suffix, caret: start + prefix.length, selectionEnd: start + prefix.length + selected.length });
    };
    if (format) {
        addButton('加粗', () => selectionEdit('**', '**', '加粗内容'), { title: '加粗（Ctrl / ⌘ + B）' });
        for (const [alignment, title] of [['left', '左对齐'], ['center', '居中'], ['right', '右对齐']]) addButton(title, () => {
            applyEdit(textarea, alignmentEdit(textarea.value, textarea.selectionStart, textarea.selectionEnd, alignment));
        });
        addButton('自动编号', () => {
            const start = textarea.value.lastIndexOf('\n', textarea.selectionStart - 1) + 1;
            const next = textarea.value.indexOf('\n', textarea.selectionEnd), end = next < 0 ? textarea.value.length : next;
            const text = textarea.value.slice(start, end).split('\n').map((line, index) => `${index + 1}. ${line.replace(/^\s*\d+[.)]\s+/, '')}`).join('\n');
            applyEdit(textarea, { start, end, text, caret: start + text.length });
        });
        for (const [title, shift] of [['增加层级', false], ['减少层级', true]]) addButton(title, () => {
            const edit = markdownKeyEdit(textarea.value, textarea.selectionStart, textarea.selectionEnd, 'Tab', shift);
            if (edit) applyEdit(textarea, edit);
        });
    }
    if (code) addButton('插入代码块', trigger => editCode(null, trigger));
    const previewButton = addButton('预览', () => {
        previewMode = !previewMode;
        textarea.hidden = previewMode; preview.hidden = !previewMode; cards.hidden = previewMode;
        previewButton.textContent = previewMode ? '继续编辑' : '预览'; previewButton.setAttribute('aria-label', previewButton.textContent);
        previewButton.setAttribute('aria-pressed', String(previewMode)); refresh();
        if (!previewMode) textarea.focus();
    });
    previewButton.setAttribute('aria-pressed', 'false');
    function renderCards() {
        renderFrame = 0;
        if (disposed) return;
        const state = `${previewMode}:${textarea.disabled}:${textarea.readOnly}`;
        // Blur also delivers change after input; rebuilding an unchanged card
        // between pointerdown and click would swallow the edit action.
        if (renderedValue === textarea.value && renderedState === state) return;
        renderedValue = textarea.value; renderedState = state;
        const blocks = code ? parseCodeBlocks(textarea.value) : [];
        cards.replaceChildren();
        for (const [index, block] of blocks.entries()) {
            const language = block.language ? normalizeLanguage(block.language) : detectCodeLanguage(block.code);
            const title = `${languageNames[language]} · ${block.code.split('\n').length} 行 · 编辑代码块 ${index + 1}`;
            const button = createDomainButton({ label: '', variant: 'soft', size: 'sm', className: 'assessment-editor__code-card', attrs: { 'aria-label': title, 'data-lq-shape': 'surface', 'data-code-block-index': String(index) } }, doc);
            const caption = doc.createElement('span'); caption.textContent = title;
            const snippet = doc.createElement('pre'); snippet.innerHTML = highlightAssessmentCode(block.code.split('\n').slice(0, 3).join('\n'), language);
            button.append(caption, snippet);
            button.disabled = textarea.disabled || textarea.readOnly;
            button.addEventListener('click', () => editCode(block, button)); cards.append(button);
        }
        if (previewMode) {
            preview.innerHTML = renderAssessmentMarkdown(textarea.value);
            if (code) for (const [index, blockNode] of [...preview.querySelectorAll('pre')].entries()) {
                // Only convert matching complete fences; indented Markdown code
                // remains regular content and cannot target an unrelated block.
                const content = blockNode.querySelector('code')?.textContent?.replace(/\n$/, '');
                const block = blocks.find(item => item.code === content && !item.used);
                if (!block) continue;
                block.used = true;
                const button = createDomainButton({ label: '', variant: 'soft', attrs: { 'aria-label': `编辑代码块 ${index + 1}`, 'data-lq-shape': 'surface' }, className: 'assessment-editor__code-card' }, doc);
                button.disabled = textarea.disabled || textarea.readOnly;
                button.append(blockNode.cloneNode(true)); button.addEventListener('click', () => editCode(block, button)); blockNode.replaceWith(button);
            }
        }
    }
    function refresh() {
        if (disposed) return;
        controls.forEach(button => { button.disabled = textarea.disabled || textarea.readOnly; });
        autosize.refresh();
        if (!renderFrame) renderFrame = win.requestAnimationFrame(renderCards);
    }
    function editCode(block, trigger, recovered = null) {
        if (disposed || dialog || (!recovered && (textarea.disabled || textarea.readOnly))) return;
        const originalValue = textarea.value;
        const range = block ? { start: block.start, end: block.end } : { start: textarea.selectionStart, end: textarea.selectionEnd };
        const initialCode = recovered?.code ?? block?.code ?? textarea.value.slice(range.start, range.end);
        const wrap = doc.createElement('div'); wrap.className = 'assessment-code-dialog';
        const languageLabel = doc.createElement('label'); languageLabel.textContent = '代码语言';
        const select = adoptDomainControl(doc.createElement('select'));
        select.dataset.lqDropdown = '';
        select.setAttribute('aria-label', '代码语言');
        for (const [value, text] of [['auto', '自动识别'], ...languages.map(key => [key, languageNames[key]])]) { const option = doc.createElement('option'); option.value = value; option.textContent = text; select.append(option); }
        select.value = recovered?.language ?? (block?.language ? normalizeLanguage(block.language) : 'auto'); languageLabel.append(select);
        const initialLanguage = select.value;
        const status = doc.createElement('small'); status.className = 'assessment-code-dialog__status'; status.setAttribute('aria-live', 'polite');
        const codeLabel = doc.createElement('label'); codeLabel.textContent = '代码内容';
        const surface = doc.createElement('div'); surface.className = 'assessment-code-surface';
        const input = adoptDomainControl(doc.createElement('textarea')); input.classList.add('assessment-code-input'); input.setAttribute('aria-label', '代码内容'); input.spellcheck = false; input.autocapitalize = 'off'; input.autocomplete = 'off'; input.wrap = 'off'; input.value = initialCode;
        const overlay = doc.createElement('div'); overlay.className = 'assessment-code-overlay'; overlay.setAttribute('aria-hidden', 'true');
        const highlighted = doc.createElement('pre'); overlay.append(highlighted); surface.append(input, overlay); codeLabel.append(surface);
        const help = doc.createElement('small'); help.textContent = 'Tab 缩进四个空格；括号自动闭合，光标附近的配对括号会突出显示。';
        const error = doc.createElement('p'); error.className = 'lq-alert'; error.dataset.lqComponent = 'alert'; error.setAttribute('role', 'alert'); error.hidden = true;
        if (recovered?.error) { error.hidden = false; error.textContent = recovered.error; }
        wrap.append(languageLabel, status, codeLabel, help, error);
        const footer = doc.createElement('div'); footer.className = 'assessment-code-dialog__actions';
        const cancel = createDomainButton({ label: '取消', variant: 'ghost' }, doc);
        const confirm = createDomainButton({ label: block ? '更新代码块' : '插入代码块', variant: 'prominent' }, doc);
        footer.append(cancel, confirm);
        const grow = attachAutosize(input);
        let saved = false, dirtyClose = false, typing = false, typingTimer = 0, highlightFrame = 0, pendingEdit = null;
        const syncScroll = () => {
            highlighted.style.paddingTop = input.style.paddingTop;
            highlighted.style.paddingBottom = input.style.paddingBottom;
            highlighted.style.transform = `translate(${-input.scrollLeft}px, ${-input.scrollTop}px)`;
        };
        const update = () => {
            highlightFrame = 0;
            const language = select.value === 'auto' ? detectCodeLanguage(input.value) : select.value;
            status.textContent = select.value === 'auto' ? `识别为：${languageNames[language]}（可手动调整）` : `使用：${languageNames[language]}`;
            highlighted.innerHTML = `${highlightAssessmentCode(input.value, language, input.selectionStart)}\n`;
            syncScroll();
            confirm.disabled = !input.value.trim() || textarea.disabled || textarea.readOnly || saved;
        };
        const schedule = () => { if (!highlightFrame) highlightFrame = win.requestAnimationFrame(update); };
        input.addEventListener('input', schedule); input.addEventListener('keyup', schedule); input.addEventListener('click', schedule); input.addEventListener('scroll', syncScroll);
        input.addEventListener('compositionstart', () => { typing = true; win.clearTimeout(typingTimer); });
        input.addEventListener('compositionend', () => { typingTimer = win.setTimeout(() => { typing = false; }, 0); });
        input.addEventListener('keydown', event => {
            if (event.isComposing || typing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey) return;
            const edit = codeKeyEdit(input.value, input.selectionStart, input.selectionEnd, event.key, event.shiftKey);
            if (edit) { event.preventDefault(); applyEdit(input, edit); schedule(); }
        });
        select.addEventListener('change', schedule);
        let dropdowns = [], draftGuard = null;
        const hasDraftChanges = () => input.value !== initialCode || select.value !== initialLanguage;
        const cleanup = () => { draftGuard?.destroy(); dropdowns.forEach(owner => owner.destroy()); grow.destroy(); win.clearTimeout(typingTimer); if (highlightFrame) win.cancelAnimationFrame(highlightFrame); dialog = null; };
        dialog = openDialog({ title: block ? '编辑代码块' : '插入代码块', body: wrap, footer, size: 'lg' }, {
            trigger, initialFocus: input, closeOnOutside: false, returnFocus: () => previewMode ? previewButton : textarea,
            beforeClose: reason => {
                if (saved || reason === 'cancel' || !hasDraftChanges() || dirtyClose) return true;
                // Escape and the shared close button must not silently discard
                // an unfinished code draft. Cancel is the explicit discard path.
                error.hidden = false; error.textContent = '代码尚未插入。请确认插入，或点击“取消”放弃本次修改。'; return false;
            }, onClose: () => {
                cleanup();
                if (!pendingEdit || disposed) return;
                const wasHidden = textarea.hidden; textarea.hidden = false;
                const committed = textarea.value === originalValue && applyEdit(textarea, pendingEdit);
                textarea.hidden = wasHidden;
                if (committed) { refresh(); if (previewMode) previewButton.focus(); }
                else editCode(null, previewButton, { code: input.value, language: select.value, error: '正文或作答状态已经变化，代码尚未写入。代码已保留，请复制保存，或确认当前位置后重新插入。' });
            }, onDestroy: cleanup,
        }, doc);
        cancel.addEventListener('click', () => { dirtyClose = true; void getLayerSystem(doc).close(dialog, 'cancel'); });
        confirm.addEventListener('click', () => {
            if (disposed || saved || !input.value.trim() || textarea.disabled || textarea.readOnly) return;
            if (textarea.value !== originalValue) { error.hidden = false; error.textContent = '正文在编辑代码期间发生变化。代码已保留，请复制后取消，再重新插入。'; return; }
            const language = select.value === 'auto' ? detectCodeLanguage(input.value) : select.value;
            const before = textarea.value.slice(0, range.start), after = textarea.value.slice(range.end);
            const text = `${before && !before.endsWith('\n') ? '\n\n' : ''}${fencedCode(input.value, language)}${after && !after.startsWith('\n') ? '\n\n' : '\n'}`;
            if (textarea.maxLength >= 0 && before.length + text.length + after.length > textarea.maxLength) { error.hidden = false; error.textContent = '代码超过当前输入框的长度限制，内容已保留。'; return; }
            // Background remains inert until the shared layer completes its
            // exit. Commit afterwards so native undo targets the real source.
            pendingEdit = { ...range, text, caret: range.start + text.length };
            saved = true; confirm.disabled = true; void getLayerSystem(doc).close(dialog, 'confirm');
        });
        // The unconfirmed code has not reached the owner's persisted textarea.
        // Shared DirtyGuard installs beforeunload only while this local draft
        // differs, and preserves other owners when this dialog is destroyed.
        draftGuard = bindDirtyGuard(wrap, { isDirty: hasDraftChanges });
        dropdowns = enhanceDropdowns(wrap); grow.refresh(); schedule();
    }
    listen(textarea, 'compositionstart', () => { composing = true; win.clearTimeout(compositionTimer); });
    listen(textarea, 'compositionend', () => { compositionTimer = win.setTimeout(() => { composing = false; }, 0); });
    listen(textarea, 'keydown', event => {
        if (composing || event.isComposing || event.keyCode === 229 || textarea.disabled || textarea.readOnly) return;
        if (format && (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'b') { event.preventDefault(); selectionEdit('**', '**', '加粗内容'); return; }
        if (!format || event.ctrlKey || event.metaKey || event.altKey) return;
        const edit = markdownKeyEdit(textarea.value, textarea.selectionStart, textarea.selectionEnd, event.key, event.shiftKey);
        if (edit) { event.preventDefault(); applyEdit(textarea, edit); }
    });
    listen(textarea, 'input', refresh); listen(textarea, 'change', refresh);
    const api = { refresh, destroy() {
        if (disposed) return;
        disposed = true; if (dialog) dialog.destroy(); if (renderFrame) win.cancelAnimationFrame(renderFrame); win.clearTimeout(compositionTimer);
        disposers.forEach(dispose => dispose()); autosize.destroy(); textarea.hidden = false;
        if (shell.contains(textarea)) {
            const parent = shell.parentNode || originalParent;
            parent.insertBefore(textarea, shell.parentNode ? shell : originalNext?.parentNode === parent ? originalNext : null);
        }
        shell.remove(); mounted.delete(textarea);
    } };
    mounted.set(textarea, api); refresh(); return api;
}
