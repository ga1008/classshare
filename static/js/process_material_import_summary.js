import { escapeHtml } from './ui.js';

function asArray(value) {
    return Array.isArray(value) ? value.filter((item) => item != null && String(item).trim()) : [];
}

function qualityTone(key) {
    if (key === 'failed') return 'is-failed';
    if (key === 'needs_review') return 'is-warning';
    if (key === 'in_progress') return 'is-busy';
    return 'is-ready';
}

function actionForSummary(item, qualityKey) {
    if (!item?.id) return null;
    if (qualityKey === 'failed') {
        if (!item?.can_manage) return null;
        if (item.source_type === 'import') return 'import-again';
        if (item.source_type === 'classroom' && item.class_offering_id) return 'retry';
    }
    if (qualityKey === 'needs_review') return item.can_manage ? 'edit' : 'preview';
    if (qualityKey === 'ready') return 'preview';
    return null;
}

function actionTextForSummary(summary, item, qualityKey, action) {
    if (qualityKey === 'failed') {
        if (!item?.can_manage) return '来源教师需处理';
        if (action === 'retry') return '一键重试';
        if (action === 'import-again') return '重新上传文件';
        return summary.action_label || '请处理失败记录';
    }
    if (qualityKey === 'needs_review' && action === 'preview' && !item?.can_manage) {
        return '先预览核对';
    }
    return summary.action_label || '可预览导出';
}

function moreWarningTextForSummary(moreCount, action) {
    if (!moreCount) return '';
    const nextStep = action === 'edit' ? '请进入编辑器核对。' : '请先预览核对。';
    return `还有 ${String(moreCount)} 项，${nextStep}`;
}

export function renderProcessImportSummary(item) {
    const summary = item?.import_summary || {};
    if (!summary.visible) return '';
    const warnings = asArray(summary.warnings).slice(0, 3);
    const allWarnings = asArray(summary.all_warnings || summary.warnings);
    const moreCount = Math.max(0, Number(summary.more_warning_count || 0));
    const qualityKey = String(summary.quality_key || '');
    const sourceHeading = summary.source_heading || '导入来源';
    const sourceLabel = summary.source_file_label || '导入文件';
    const sourceTitle = summary.source_file_title || sourceLabel;
    const action = actionForSummary(item, qualityKey);
    const moreWarningText = moreWarningTextForSummary(moreCount, action);
    const warningList = warnings.length
        ? `<ul class="lp-import-summary__warnings">
            ${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}
            ${moreWarningText ? `<li class="lp-import-summary__more">${escapeHtml(moreWarningText)}</li>` : ''}
        </ul>`
        : '';
    const fullWarningDetails = allWarnings.length > warnings.length
        ? `<details class="lp-import-summary__details">
            <summary>查看全部 ${allWarnings.length} 项核对点</summary>
            <ol>
                ${allWarnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}
            </ol>
        </details>`
        : '';
    const actionText = actionTextForSummary(summary, item, qualityKey, action);
    const actionLabel = `${actionText}：${sourceTitle}`;
    const actionHtml = action
        ? `<button type="button" class="lp-import-summary__action" data-action="${escapeHtml(action)}" data-id="${escapeHtml(String(item.id))}" aria-label="${escapeHtml(actionLabel)}">${escapeHtml(actionText)}</button>`
        : `<small class="lp-import-summary__action">${escapeHtml(actionText)}</small>`;
    return `
        <div class="lp-import-summary" data-import-quality="${escapeHtml(qualityKey)}">
            <div class="lp-import-summary__head">
                <span>${escapeHtml(sourceHeading)}</span>
                <strong title="${escapeHtml(sourceTitle)}">${escapeHtml(sourceLabel)}</strong>
                <em class="lp-status ${qualityTone(qualityKey)}">${escapeHtml(summary.quality_label || '已解析')}</em>
            </div>
            ${warningList}
            ${fullWarningDetails}
            ${actionHtml}
        </div>`;
}
