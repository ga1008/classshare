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

function compactText(text, maxLength) {
    const plain = stripMarkdown(text);
    if (!plain) return '';
    return plain.length > maxLength ? `${plain.slice(0, maxLength).trim()}...` : plain;
}

function normalizeQuestionId(value) {
    const text = String(value || '').trim().toLowerCase();
    if (!text) return '';
    const chineseQuestionMatch = text.match(/第\s*(\d+)\s*(?:题|问|小题)/);
    if (chineseQuestionMatch) return `q${Number(chineseQuestionMatch[1])}`;
    const plainQuestionMatch = text.match(/^(?:question|q)?\s*(\d+)$/i);
    if (plainQuestionMatch) return `q${Number(plainQuestionMatch[1])}`;
    return text.replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '');
}

function parseScoreValue(text) {
    const raw = String(text || '').trim();
    const numbers = raw.match(/\d+(?:\.\d+)?/g) || [];
    const score = numbers.length ? Number(numbers[0]) : null;
    const maxScore = numbers.length > 1 ? Number(numbers[1]) : null;
    return {
        text: raw,
        score: Number.isFinite(score) ? score : null,
        maxScore: Number.isFinite(maxScore) ? maxScore : null,
    };
}

function parseLabeledFeedbackLine(line) {
    const normalized = String(line || '')
        .trim()
        .replace(/^\s*(?:[-*+]|\d+[.)、])\s*/, '')
        .replace(/\*\*/g, '')
        .trim();
    const match = normalized.match(/^(本题得分|得分|score|扣分点描述|扣分点|失分点|评价|评语|evaluation)\s*[：:]\s*(.*)$/i);
    if (!match) return null;
    const label = match[1].toLowerCase();
    const value = match[2].trim();
    if (label === '本题得分' || label === '得分' || label === 'score') {
        return { key: 'score', value };
    }
    if (label === '评价' || label === '评语' || label === 'evaluation') {
        return { key: 'evaluation', value };
    }
    return { key: 'deductionPoints', value };
}

function parseQuestionDetails(markdown) {
    const details = {
        scoreText: '',
        score: null,
        maxScore: null,
        deductionPoints: '',
        evaluation: '',
    };
    let hasStructuredFields = false;
    String(markdown || '').split(/\r?\n/).forEach((line) => {
        const parsed = parseLabeledFeedbackLine(line);
        if (!parsed) return;
        hasStructuredFields = true;
        if (parsed.key === 'score') {
            const score = parseScoreValue(parsed.value);
            details.scoreText = score.text;
            details.score = score.score;
            details.maxScore = score.maxScore;
            return;
        }
        if (parsed.key === 'deductionPoints') {
            details.deductionPoints = compactText(parsed.value || '无', 80) || '无';
            return;
        }
        if (parsed.key === 'evaluation') {
            details.evaluation = compactText(parsed.value, 20);
        }
    });

    if (!hasStructuredFields) return null;
    if (!details.deductionPoints) details.deductionPoints = '无';
    if (!details.evaluation) details.evaluation = '继续稳步完善';
    const noDeduction = /^(无|没有|未发现|暂无)$/.test(details.deductionPoints);
    let scoreState = 'partial';
    if (Number(details.score) === 0) {
        scoreState = 'zero';
    } else if (noDeduction || (
        Number.isFinite(details.score)
        && Number.isFinite(details.maxScore)
        && details.score >= details.maxScore
    )) {
        scoreState = 'full';
    }
    details.noDeduction = noDeduction;
    details.scoreState = scoreState;
    return details;
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
        details: parseQuestionDetails(markdown),
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
                details: null,
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
    if (feedback.details) {
        const details = feedback.details;
        const scoreText = details.scoreText || (
            details.score != null
                ? `${details.score}${details.maxScore != null ? `/${details.maxScore}` : ''}`
                : '-'
        );
        const deductionClass = details.noDeduction ? 'is-none' : 'has-deduction';
        return `
            <div class="grading-question-feedback is-structured" tabindex="0">
                <div class="grading-question-feedback__label">本题批改</div>
                <div class="grading-feedback-grid">
                    <div class="grading-feedback-field">
                        <span class="grading-feedback-field__name">本题得分</span>
                        <strong class="grading-feedback-score is-${details.scoreState}">${escapeHtml(scoreText)}</strong>
                    </div>
                    <div class="grading-feedback-field grading-feedback-field--wide">
                        <span class="grading-feedback-field__name">扣分点</span>
                        <strong class="grading-feedback-deduction ${deductionClass}" title="${escapeHtml(details.deductionPoints)}">${escapeHtml(details.deductionPoints)}</strong>
                    </div>
                    <div class="grading-feedback-field grading-feedback-field--wide">
                        <span class="grading-feedback-field__name">评价</span>
                        <span class="grading-feedback-evaluation" title="${escapeHtml(details.evaluation)}">${escapeHtml(details.evaluation)}</span>
                    </div>
                </div>
            </div>
        `;
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
