/**
 * Learning-document library: A4 card grid.
 *
 *   renderMaterialCards(items, ctx) -> html
 *
 * Each card shows the document kind (colour strip + icon), title, summary or
 * structure, classroom coverage, the Git last-sync state with a one-click
 * update button, and the primary actions. The host wires clicks through
 * `data-action` exactly like the legacy row list (`explore`, `git-update`,
 * `open-external`, `assign-card`, `card-menu`, `lessondoc-edit`, …).
 */
import { escapeHtml, formatDate, formatSize } from './ui.js';
import { hasInlineMaterialPreview } from './materials_common.js';
import { getMaterialKind, kindIcon, providerLabel } from './materials_kinds.js';
import { describeLastSync, describeSyncResult, renderSyncProgress } from './materials_sync_report.js';

function highlight(text, keyword) {
    const source = String(text || '');
    const needle = String(keyword || '').trim();
    if (!needle) return escapeHtml(source);
    const pattern = new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig');
    return source.split(pattern).map((segment, index) => (index % 2 ? `<mark>${escapeHtml(segment)}</mark>` : escapeHtml(segment))).join('');
}

function structureLine(item, kind) {
    if (item.node_type === 'folder') {
        const parts = [];
        if (item.subtree_folder_count != null) parts.push(`${item.subtree_folder_count} 文件夹`);
        if (item.subtree_file_count != null) parts.push(`${item.subtree_file_count} 文件`);
        if (!parts.length) parts.push(`${item.child_count || 0} 个子项`);
        if (item.lessondoc_pack) parts.push(`${item.lessondoc_pack.ready_count} / ${item.lessondoc_pack.total_count} 课就绪`);
        if (item.is_html_package && item.html_package_lesson_count) parts.push(`${item.html_package_lesson_count} 个课次页`);
        return parts.join(' · ');
    }
    return `${kind.label} · ${formatSize(item.file_size || 0)}`;
}

function searchMatches(item, keyword) {
    const matches = Array.isArray(item.search_matches) ? item.search_matches : [];
    if (!matches.length && !item.search_match_count) return '';
    const count = Number(item.search_match_count || matches.length);
    return `
        <span class="mcard__matches">
            <span class="mcard__matches-head">${count} 处命中</span>
            ${matches.slice(0, 4).map((match) => `<span class="mcard__match" title="${escapeHtml(match.material_path || '')}">${kindIcon(match.node_type === 'folder' ? 'folder' : 'file', 12)}${highlight(match.name, keyword)}</span>`).join('')}
            ${count > Math.min(matches.length, 4) + 1 ? '<span class="mcard__match is-more">…</span>' : ''}
        </span>`;
}

function coverageChips(item) {
    const courses = Array.isArray(item.assigned_course_names) ? item.assigned_course_names.filter(Boolean) : [];
    const classes = Array.isArray(item.assigned_class_names) ? item.assigned_class_names.filter(Boolean) : [];
    const chips = [];
    if (item.assignment_count) chips.push(`<span class="lq-chip lq-chip--sm">${kindIcon('users', 12)} ${escapeHtml(String(item.assignment_count))} 个课堂</span>`);
    if (courses.length) chips.push(`<span class="lq-chip lq-chip--sm" title="${escapeHtml(courses.join(' / '))}">${escapeHtml(courses.slice(0, 2).join(' / '))}${courses.length > 2 ? ` +${courses.length - 2}` : ''}</span>`);
    else if (classes.length) chips.push(`<span class="lq-chip lq-chip--sm">${escapeHtml(classes.slice(0, 2).join(' / '))}</span>`);
    if (item.scope_label && item.scope_level !== 'private') chips.push(`<span class="lq-chip lq-chip--sm">${escapeHtml(item.scope_label)}</span>`);
    if (item.can_manage === false) chips.push('<span class="lq-chip lq-chip--sm">共享材料</span>');
    return chips.join('');
}

function gitBlock(item, ctx) {
    if (!item.is_git_repository) return '';
    const last = describeLastSync(item);
    const sync = ctx.syncStates?.get(Number(item.id)) || null;
    const running = Boolean(sync?.running);
    const canManage = item.can_manage !== false;
    const done = sync?.result ? describeSyncResult(sync.result) : null;
    const resultTone = done ? done.tone : last.tone;
    const resultText = done ? done.text : last.text;
    return `
        <div class="mcard__git" data-git-block>
            <div class="mcard__git-row">
                <span class="mcard__git-meta" title="${escapeHtml(item.git_remote_url || '')}">${kindIcon('git', 13)} ${escapeHtml(providerLabel(item))} · ${escapeHtml(item.git_default_branch || item.git_head_branch || '未识别分支')}</span>
                ${canManage ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--prominent mcard__git-btn" data-action="git-update" ${running ? 'disabled aria-busy="true"' : ''}>${kindIcon('refresh', 13)} ${running ? '更新中…' : '一键更新'}</button>` : ''}
            </div>
            <span class="lq-chip lq-chip--sm lq-chip--status mcard__git-last" data-lq-status data-lq-tone-level="${escapeHtml(resultTone)}" title="${escapeHtml(done ? '进入浏览可查看完整同步报告' : last.title)}">${escapeHtml(resultText)}</span>
            <div class="mcard__git-progress" data-git-progress hidden></div>
        </div>`;
}

function primaryActions(item, ctx) {
    if (ctx.classroom) {
        const actions = [];
        if (item.node_type === 'folder') {
            actions.push(`<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass" data-action="explore">${kindIcon('tree', 13)} 打开文件夹</button>`);
        }
        if (item.preview_url) {
            const label = item.is_renderable ? (item.render_label || '渲染') : '预览';
            actions.push(`<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" data-action="open-external" href="${escapeHtml(item.preview_url)}" target="_blank" rel="noopener">${kindIcon('external', 13)} ${escapeHtml(label)}</a>`);
        }
        if (item.document_viewer_url && item.document_viewer_url !== item.preview_url) {
            actions.push(`<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" href="${escapeHtml(item.document_viewer_url)}" target="_blank" rel="noopener">阅读文档</a>`);
        }
        if (item.node_type === 'file' && item.download_url && item.download_allowed !== false) {
            actions.push(`<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--ghost" data-action="download" href="${escapeHtml(item.download_url)}">下载</a>`);
        } else if (item.node_type === 'file' && item.download_allowed === false) {
            actions.push(`<span class="mcard__structure" title="${escapeHtml(item.download_blocked_reason || '')}">已限制下载</span>`);
        }
        if (ctx.canViewDetails && item.can_manage) {
            actions.push('<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost" data-action="details">详情</button>');
        }
        return actions.join('');
    }
    const buttons = [];
    buttons.push(`<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass" data-action="explore">${kindIcon('tree', 13)} 浏览</button>`);
    if (item.preview_url) {
        const label = item.is_renderable ? (item.render_label || '渲染') : '预览';
        buttons.push(`<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass" data-action="open-external">${kindIcon('external', 13)} ${escapeHtml(label)}</button>`);
    }
    if (item.lessondoc_pack && item.can_manage !== false) {
        buttons.push(`<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" data-action="lessondoc-edit" href="/materials/lessondoc-editor/${Number(item.lessondoc_pack.pack_id)}?lesson=0&return_to=${encodeURIComponent(`${location.pathname}${location.search}`)}">${kindIcon('pencil', 13)} 编辑</a>`);
    }
    if (ctx.canAssign && item.can_publish_to_classroom !== false) {
        buttons.push(`<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost" data-action="assign-card">${kindIcon('users', 13)} 分配</button>`);
    }
    buttons.push(`<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost lq-btn--icon" data-action="card-menu" aria-label="更多操作" aria-haspopup="menu">${kindIcon('more', 15)}</button>`);
    return buttons.join('');
}

export function renderMaterialCard(item, ctx = {}) {
    const kind = getMaterialKind(item);
    const id = Number(item.id);
    const selected = ctx.selectedIds?.has(id);
    const active = Number(ctx.activeId) === id;
    const generated = Number(ctx.recentGeneratedId) === id;
    const summary = String(item.summary || '').trim();
    const activity = item.latest_activity_at || item.updated_at || item.created_at;
    const bodyContents = `
        <span class="mcard__title" title="${escapeHtml(item.name || '')}">${highlight(item.name, ctx.keyword)}</span>
        <span class="mcard__structure">${escapeHtml(structureLine(item, kind))}</span>
        ${summary ? `<span class="mcard__summary">${escapeHtml(summary)}</span>` : `<span class="mcard__summary is-muted">${escapeHtml(item.material_path || '')}</span>`}
        ${searchMatches(item, ctx.keyword)}
        ${generated ? '<span class="materials-generated-badge">刚刚生成</span>' : ''}`;
    const bodyClass = 'mcard__body lq-btn lq-btn--ghost lq-domain-choice';
    const bodyAttrs = `class="${bodyClass}" data-lq-component="choice" data-lq-shape="surface"`;
    const bodyUrl = ctx.classroom && item.node_type !== 'folder' && (item.preview_url || (item.download_allowed !== false && item.download_url));
    const bodyLabel = escapeHtml(item.name || '材料');
    let body;
    if (ctx.classroom && hasInlineMaterialPreview(item)) {
        body = `<button type="button" ${bodyAttrs} data-action="preview" aria-label="预览 ${bodyLabel}">${bodyContents}</button>`;
    } else if (bodyUrl) {
        body = `<a ${bodyAttrs} href="${escapeHtml(bodyUrl)}"${item.preview_url ? ' target="_blank" rel="noopener"' : ''} aria-label="打开 ${bodyLabel}">${bodyContents}</a>`;
    } else if (ctx.classroom && item.node_type !== 'folder') {
        body = `<div class="mcard__body">${bodyContents}</div>`;
    } else {
        body = `<button type="button" ${bodyAttrs} data-action="explore" aria-label="${ctx.classroom ? '打开' : '浏览'} ${bodyLabel}">${bodyContents}</button>`;
    }
    return `
        <article class="mcard${ctx.classroom ? ' mcard--classroom' : ''}${selected ? ' is-selected' : ''}${active ? ' is-active' : ''}${generated ? ' is-generated' : ''}" data-id="${id}" data-kind="${escapeHtml(kind.kind)}"${ctx.classroom ? ` data-material-download-allowed="${item.download_allowed === false ? 'false' : 'true'}"` : ''} style="--kind-color:${escapeHtml(kind.color)}" aria-label="${bodyLabel}">
            <div class="mcard__paper lq-surface" data-lq-component="surface" ${selected || active ? 'data-lq-selected="true"' : ''}>
                <header class="mcard__head">
                    <span class="mcard__kind">${kindIcon(kind.icon, 16)}<span>${escapeHtml(kind.label)}</span></span>
                    <label class="mcard__check" title="选择">
                        <input data-lq-component="checkbox" class="lq-checkbox" type="checkbox" aria-label="${ctx.classroom ? '选择材料：' : '选择 '}${bodyLabel}" data-role="select-item" data-id="${id}" ${selected ? 'checked' : ''}>
                    </label>
                </header>
                ${body}
                ${ctx.classroom ? '' : `<div class="mcard__coverage">${coverageChips(item)}</div>
                ${gitBlock(item, ctx)}
                <footer class="mcard__foot">
                    <span class="mcard__time" title="最近活动">${kindIcon('clock', 12)} ${escapeHtml(formatDate(activity) || '暂无')}</span>
                    <span class="mcard__time" title="创建时间">建 ${escapeHtml((formatDate(item.created_at) || '').slice(0, 10) || '—')}</span>
                </footer>`}
            </div>
            <div class="mcard__actions">${primaryActions(item, ctx)}</div>
        </article>
    `;
}

export function renderMaterialCards(items, ctx = {}) {
    if (!items.length) return '';
    return `<div class="mcard-grid" data-materials-cards>${items.map((item) => renderMaterialCard(item, ctx)).join('')}</div>`;
}

/** Update a single card's git progress strip without re-rendering the grid. */
export function paintCardSyncProgress(listBody, materialId, progress) {
    const card = listBody?.querySelector(`.mcard[data-id="${Number(materialId)}"]`);
    if (!card) return;
    const running = Boolean(progress && progress.status === 'running');
    // Only the live phase strip lives in the card; the finished report belongs to the explorer.
    renderSyncProgress(card.querySelector('[data-git-progress]'), running ? progress : null);
    const button = card.querySelector('[data-action="git-update"]');
    if (button) {
        button.disabled = running;
        button.setAttribute('aria-busy', String(running));
        button.innerHTML = `${kindIcon('refresh', 13)} ${running ? '更新中…' : '一键更新'}`;
    }
}
