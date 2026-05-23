import { escapeHtml } from './ui.js';
import { renderMarkdownHtml } from './grading_feedback.js';

const TYPE_LABELS = {
    radio: '单选题',
    checkbox: '多选题',
    text: '填空题',
    textarea: '问答题',
};

function parseMaybeJson(value, fallback = {}) {
    if (!value) return fallback;
    if (typeof value === 'string') {
        try {
            return JSON.parse(value);
        } catch {
            return fallback;
        }
    }
    return value;
}

export function normalizeExamPaperPayload(input) {
    const raw = parseMaybeJson(input, {});
    const data = parseMaybeJson(raw.questions_json, raw);
    const pages = Array.isArray(data.pages) ? data.pages : [];
    return {
        ...data,
        pages: pages.map((page, pageIndex) => ({
            ...page,
            name: page?.name || page?.title || `第 ${pageIndex + 1} 部分`,
            description: page?.description || page?.desc || '',
            questions: Array.isArray(page?.questions) ? page.questions : [],
        })),
    };
}

export function getExamPaperSummary(input) {
    const paper = normalizeExamPaperPayload(input);
    const pageCount = paper.pages.length;
    const questionCount = paper.pages.reduce((total, page) => total + (page.questions || []).length, 0);
    const totalPoints = paper.pages.reduce((total, page) => {
        return total + (page.questions || []).reduce((sum, question) => {
            const value = Number(question?.points ?? question?.score ?? question?.point ?? 0);
            return sum + (Number.isFinite(value) ? value : 0);
        }, 0);
    }, 0);
    return { pageCount, questionCount, totalPoints };
}

function compactText(value, fallback = '') {
    const text = String(value ?? '').trim();
    return text || fallback;
}

function markdown(value, fallback = '') {
    const text = compactText(value, fallback);
    if (!text) return '';
    try {
        return renderMarkdownHtml(text);
    } catch {
        return escapeHtml(text).replace(/\n/g, '<br>');
    }
}

function normalizeOptions(options) {
    if (!Array.isArray(options)) return [];
    return options.map((option) => {
        if (option && typeof option === 'object') {
            return compactText(option.label ?? option.text ?? option.value, '（空选项）');
        }
        return compactText(option, '（空选项）');
    });
}

function normalizeAttachmentPolicy(question) {
    const raw = question?.attachment_requirements || question?.attachment_requirement || {};
    const required = Boolean(raw.required || raw.requires_attachment || raw.attachment_required);
    const minRaw = Number(raw.min_count ?? raw.min ?? (required ? 1 : 0));
    const maxRaw = Number(raw.max_count ?? raw.max ?? 0);
    const minCount = Number.isFinite(minRaw) ? Math.max(0, minRaw) : (required ? 1 : 0);
    const maxCount = Number.isFinite(maxRaw) && maxRaw > 0 ? maxRaw : null;
    const allowed = Array.isArray(raw.allowed_file_types)
        ? raw.allowed_file_types
        : String(raw.allowed_file_types || raw.file_types || '')
            .replace(/\r/g, '\n')
            .replace(/[;，、]/g, ',')
            .replace(/\n/g, ',')
            .split(',');
    const allowedFileTypes = allowed.map((item) => String(item || '').trim()).filter(Boolean);
    const enabled = Boolean(
        raw.enabled !== false
        && (required || minCount > 0 || maxCount || allowedFileTypes.length || raw.description || raw.allow_drawing)
    );
    return {
        enabled,
        required: required || minCount > 0,
        minCount,
        maxCount,
        allowedFileTypes,
        description: compactText(raw.description || raw.hint || ''),
        allowDrawing: Boolean(raw.allow_drawing || raw.drawing_allowed),
    };
}

function questionCopy(question) {
    return compactText(
        question?.text ?? question?.title ?? question?.question ?? question?.prompt,
        '（未填写题目）'
    );
}

function questionType(question) {
    return compactText(question?.type || question?.question_type || 'textarea').toLowerCase();
}

function renderAttachmentPolicy(question) {
    const policy = normalizeAttachmentPolicy(question);
    if (!policy.enabled) return '';
    const countText = policy.required
        ? `至少 ${Math.max(policy.minCount, 1)} 个附件`
        : '可选附件';
    const maxText = policy.maxCount ? `，最多 ${policy.maxCount} 个` : '';
    const typeText = policy.allowedFileTypes.length ? `；建议类型：${escapeHtml(policy.allowedFileTypes.join(', '))}` : '';
    const drawingText = policy.allowDrawing ? '；支持绘图作答' : '';
    return `
        <div class="exam-paper-preview__attachment">
            <div class="exam-paper-preview__attachment-head">
                <strong>本题附件要求</strong>
                <span>${policy.required ? '必交' : '可选'}</span>
            </div>
            <p>${escapeHtml(policy.description || `提交时${countText}${maxText}${typeText}${drawingText}。`)}</p>
            ${policy.description ? `<small>${escapeHtml(`${countText}${maxText}${typeText}${drawingText}`)}</small>` : ''}
        </div>
    `;
}

function renderQuestionInput(question, index) {
    const type = questionType(question);
    const options = normalizeOptions(question?.options);
    if (type === 'radio' || type === 'checkbox') {
        if (!options.length) {
            return '<div class="exam-paper-preview__empty-control">暂无选项</div>';
        }
        const inputType = type === 'radio' ? 'radio' : 'checkbox';
        return `
            <div class="exam-paper-preview__options">
                ${options.map((option, optionIndex) => `
                    <label class="exam-paper-preview__option">
                        <input type="${inputType}" disabled tabindex="-1" name="preview-${index}-${type}" value="${escapeHtml(option)}">
                        <div>${markdown(option, '（空选项）')}</div>
                    </label>
                `).join('')}
            </div>
        `;
    }
    if (type === 'text') {
        return `
            <input class="exam-paper-preview__input" type="text" disabled
                placeholder="${escapeHtml(question?.placeholder || '请输入答案')}">
        `;
    }
    return `
        <textarea class="exam-paper-preview__input exam-paper-preview__textarea" disabled rows="5"
            placeholder="${escapeHtml(question?.placeholder || '请在此作答...')}"></textarea>
    `;
}

function renderQuestion(question, questionIndex) {
    const type = questionType(question);
    const points = Number(question?.points ?? question?.score ?? question?.point);
    const pointText = Number.isFinite(points) && points > 0 ? String(points).replace(/\.0+$/, '') : '';
    const displayId = compactText(question?.id, String(questionIndex + 1));
    return `
        <article class="exam-paper-preview__question">
            <div class="exam-paper-preview__question-head">
                <span class="exam-paper-preview__question-number">${escapeHtml(displayId)}</span>
                <div class="exam-paper-preview__question-main">
                    <div class="exam-paper-preview__question-meta">
                        <span>${TYPE_LABELS[type] || '问答题'}</span>
                        ${pointText ? `<span>${escapeHtml(pointText)} 分</span>` : ''}
                        ${question?.allow_ai || question?.allow_student_ai || question?.ai_allowed ? '<span>允许 AI 辅助</span>' : ''}
                    </div>
                    <div class="exam-paper-preview__question-copy md-content">
                        ${markdown(questionCopy(question))}
                    </div>
                </div>
            </div>
            ${renderQuestionInput(question, questionIndex)}
            ${renderAttachmentPolicy(question)}
        </article>
    `;
}

function renderPage(page, pageIndex, questionOffset) {
    const questions = Array.isArray(page.questions) ? page.questions : [];
    return `
        <section class="exam-paper-preview__page" id="preview-page-${pageIndex + 1}">
            <div class="exam-paper-preview__page-head">
                <div>
                    <h3>${escapeHtml(page.name || `第 ${pageIndex + 1} 部分`)}</h3>
                    ${page.description ? `<p>${escapeHtml(page.description)}</p>` : ''}
                </div>
                <span>${questions.length} 题</span>
            </div>
            <div class="exam-paper-preview__question-list">
                ${questions.length
                    ? questions.map((question, index) => renderQuestion(question, questionOffset + index)).join('')
                    : '<div class="exam-paper-preview__empty-control">这一部分暂无题目</div>'}
            </div>
        </section>
    `;
}

export function renderExamPaperPreview(target, options = {}) {
    const container = typeof target === 'string' ? document.querySelector(target) : target;
    if (!container) return { pageCount: 0, questionCount: 0, totalPoints: 0 };

    const paper = normalizeExamPaperPayload(options.paper || options.questions || {});
    const summary = getExamPaperSummary(paper);
    const title = compactText(options.title || paper.title, '试卷预览');
    const description = compactText(options.description || paper.description || '');
    const badgeText = compactText(options.badgeText, '学生视角试卷预览');
    let questionOffset = 0;

    const pageNav = paper.pages.length > 1 ? `
        <div class="exam-paper-preview__page-nav" aria-label="试卷部分">
            ${paper.pages.map((page, index) => `
                <a href="#preview-page-${index + 1}">${escapeHtml(page.name || `第 ${index + 1} 部分`)}</a>
            `).join('')}
        </div>
    ` : '';

    const pagesHtml = paper.pages.map((page, index) => {
        const html = renderPage(page, index, questionOffset);
        questionOffset += Array.isArray(page.questions) ? page.questions.length : 0;
        return html;
    }).join('');

    container.innerHTML = `
        <section class="exam-paper-preview" data-exam-paper-preview>
            <header class="exam-paper-preview__hero">
                <div>
                    <span class="exam-paper-preview__badge">${escapeHtml(badgeText)}</span>
                    <h2>${escapeHtml(title)}</h2>
                    ${description ? `<p>${escapeHtml(description)}</p>` : ''}
                </div>
                <div class="exam-paper-preview__summary" aria-label="试卷概览">
                    <span><strong>${summary.pageCount}</strong><small>部分</small></span>
                    <span><strong>${summary.questionCount}</strong><small>题目</small></span>
                    <span><strong>${summary.totalPoints || '-'}</strong><small>总分</small></span>
                </div>
            </header>
            ${pageNav}
            <div class="exam-paper-preview__pages">
                ${pagesHtml || `<div class="exam-paper-preview__empty">${escapeHtml(options.emptyText || '暂无题目')}</div>`}
            </div>
        </section>
    `;
    return summary;
}
