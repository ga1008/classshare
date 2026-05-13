import { escapeHtml } from './ui.js';

const QUESTION_HEADING_RE = /^#{1,6}\s*(?:第\s*)?(\d+)\s*(?:题|问|小题)?(?:\s*[：:.\-、]\s*(.*))?$/i;
const QUESTION_ALT_HEADING_RE = /^#{1,6}\s*(?:q|question)\s*\.?\s*(\d+)(?:\s*[：:.\-、]\s*(.*))?$/i;
const QUESTION_BULLET_RE = /^\s*(?:[-*+]|\d+[.)、])\s*(?:第\s*)?(\d+)\s*(?:题|问|小题)\s*[：:]\s*(.+)$/i;

function stripMarkdown(text) {
    return String(text || '')
        .replace(/```[\s\S]*?```/g, ' ')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
        .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
        .replace(/[#>*_~]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function normalizeQuestionId(value) {
    const text = String(value || '').trim().toLowerCase();
    if (!text) return '';
    const numberMatch = text.match(/(?:^|[^0-9])(\d+)(?:[^0-9]|$)/);
    if (numberMatch) return `q${Number(numberMatch[1])}`;
    return text.replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '');
}

function readQuestionHeading(line) {
    const trimmed = String(line || '').trim();
    const match = trimmed.match(QUESTION_HEADING_RE) || trimmed.match(QUESTION_ALT_HEADING_RE);
    if (!match) return null;
    const index = Number(match[1]);
    if (!Number.isFinite(index) || index <= 0) return null;
    return {
        index,
        id: `q${index}`,
        title: match[2] || `第 ${index} 题`,
    };
}

function pushQuestionSection(sections, current) {
    if (!current) return;
    const markdown = current.lines.join('\n').trim();
    if (!markdown) return;
    sections.push({
        index: current.index,
        id: current.id,
        title: current.title,
        markdown,
        plain: stripMarkdown(markdown),
    });
}

export function parseGradingFeedback(markdown) {
    const raw = String(markdown || '').trim();
    if (!raw) {
        return {
            raw: '',
            overviewMd: '',
            overviewPlain: '',
            questions: [],
            byIndex: new Map(),
            byId: new Map(),
            hasQuestionFeedback: false,
        };
    }

    const lines = raw.split(/\r?\n/);
    const overviewLines = [];
    const sections = [];
    let current = null;

    lines.forEach((line) => {
        const heading = readQuestionHeading(line);
        if (heading) {
            pushQuestionSection(sections, current);
            current = { ...heading, lines: [] };
            if (heading.title && !/^第\s*\d+\s*题?$/.test(heading.title)) {
                current.lines.push(`**${heading.title}**`);
            }
            return;
        }

        const bullet = String(line || '').match(QUESTION_BULLET_RE);
        if (bullet && !current) {
            const index = Number(bullet[1]);
            sections.push({
                index,
                id: `q${index}`,
                title: `第 ${index} 题`,
                markdown: `- ${bullet[2].trim()}`,
                plain: stripMarkdown(bullet[2]),
            });
            return;
        }

        if (current) {
            current.lines.push(line);
        } else {
            overviewLines.push(line);
        }
    });
    pushQuestionSection(sections, current);

    const byIndex = new Map();
    const byId = new Map();
    sections.forEach((section) => {
        byIndex.set(Number(section.index), section);
        byId.set(section.id, section);
        byId.set(normalizeQuestionId(section.id), section);
    });

    const cleanedOverviewLines = overviewLines.filter((line) => {
        return !/^#{1,6}\s*(逐题反馈|分题反馈|题目反馈|question feedback|per-question feedback|question-level feedback|items)\s*$/i.test(String(line || '').trim());
    });
    const overviewMd = (sections.length ? cleanedOverviewLines.join('\n') : raw).trim();
    return {
        raw,
        overviewMd,
        overviewPlain: stripMarkdown(overviewMd),
        questions: sections,
        byIndex,
        byId,
        hasQuestionFeedback: sections.length > 0,
    };
}

export function findQuestionFeedback(model, { index, questionId, questionText } = {}) {
    if (!model || !model.hasQuestionFeedback) return null;
    const numericIndex = Number(index);
    if (Number.isFinite(numericIndex) && model.byIndex.has(numericIndex)) {
        return model.byIndex.get(numericIndex);
    }
    const candidates = [
        questionId,
        normalizeQuestionId(questionId),
        questionText,
        normalizeQuestionId(questionText),
    ].filter(Boolean);
    for (const key of candidates) {
        if (model.byId.has(key)) return model.byId.get(key);
    }
    return null;
}

export function renderMarkdownHtml(markdown) {
    const text = String(markdown || '').trim();
    if (!text) return '<p class="text-muted">暂无内容</p>';
    const host = document.createElement('div');
    const runtime = window.MarkdownRuntime;
    if (runtime && typeof runtime.renderIntoElement === 'function') {
        runtime.renderIntoElement(host, text, {
            emptyHtml: '<p class="text-muted">暂无内容</p>',
            fallbackMode: 'lines',
            silent: true,
        });
        return host.innerHTML;
    }
    return escapeHtml(text).replace(/\n/g, '<br>');
}

export function renderQuestionFeedbackHtml(feedback, emptyText = '') {
    if (!feedback?.markdown) {
        return emptyText
            ? `<div class="grading-question-feedback is-empty">${escapeHtml(emptyText)}</div>`
            : '';
    }
    return `
        <div class="grading-question-feedback">
            <div class="grading-question-feedback__label">错误点与改进建议</div>
            <div class="md-content grading-question-feedback__body">${renderMarkdownHtml(feedback.markdown)}</div>
        </div>
    `;
}

export function feedbackPreview(markdown, maxLength = 120) {
    const text = stripMarkdown(markdown);
    if (!text) return '';
    return text.length > maxLength ? `${text.slice(0, maxLength).trim()}...` : text;
}
