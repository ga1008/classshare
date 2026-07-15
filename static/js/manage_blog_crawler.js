import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const canManage = Boolean(window.BLOG_CRAWLER_CAN_MANAGE);

const elements = {
    form: document.getElementById('bc-config-form'),
    refreshBtn: document.getElementById('bc-refresh-btn'),
    runBtn: document.getElementById('bc-run-btn'),
    cancelBtn: document.getElementById('bc-cancel-btn'),
    workerStatus: document.getElementById('bc-worker-status'),
    nextRun: document.getElementById('bc-next-run'),
    lastRun: document.getElementById('bc-last-run'),
    publishedCount: document.getElementById('bc-published-count'),
    keywords: document.getElementById('bc-keywords'),
    sources: document.getElementById('bc-sources'),
    sections: document.getElementById('bc-sections'),
    postList: document.getElementById('bc-post-list'),
    runTable: document.getElementById('bc-run-table'),
    addSectionBtn: document.getElementById('bc-add-section-btn'),
    cancelSectionBtn: document.getElementById('bc-cancel-section-btn'),
    sectionForm: document.getElementById('bc-section-form'),
    reportList: document.getElementById('bc-report-list'),
    refreshReportsBtn: document.getElementById('bc-refresh-reports-btn'),
};

let managedSections = [];
let editingSectionKey = '';

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formToPayload(form) {
    const data = new FormData(form);
    const checked = (name) => Boolean(form.querySelector(`[name="${name}"]`)?.checked);
    const numberValue = (name) => {
        const raw = String(data.get(name) || '').trim();
        return raw === '' ? null : Number(raw);
    };
    return {
        enabled: checked('enabled'),
        auto_publish: checked('auto_publish'),
        featured_posts: checked('featured_posts'),
        fetch_article_pages: checked('fetch_article_pages'),
        fetch_images: checked('fetch_images'),
        enable_global_search_sources: checked('enable_global_search_sources'),
        schedule_window_start: data.get('schedule_window_start'),
        schedule_window_end: data.get('schedule_window_end'),
        max_keywords: numberValue('max_keywords'),
        search_limit_per_keyword: numberValue('search_limit_per_keyword'),
        max_posts_per_run: numberValue('max_posts_per_run'),
        article_fetch_limit: numberValue('article_fetch_limit'),
        min_request_interval_seconds: numberValue('min_request_interval_seconds'),
        max_request_interval_seconds: numberValue('max_request_interval_seconds'),
        extra_keywords: data.get('extra_keywords') || '',
        source_templates: data.get('source_templates') || '',
        blocked_domains: data.get('blocked_domains') || '',
    };
}

function renderKeywords(items) {
    if (!elements.keywords) return;
    if (!Array.isArray(items) || items.length === 0) {
        elements.keywords.innerHTML = '<span class="bc-muted">暂无课程关键词</span>';
        return;
    }
    elements.keywords.innerHTML = items
        .map((item) => `<span class="bc-chip">${escapeHtml(item.section_name || '综合')} · ${escapeHtml(item.keyword || '')}</span>`)
        .join('');
}

function renderSections(items) {
    if (!elements.sections) return;
    if (!Array.isArray(items) || items.length === 0) {
        elements.sections.innerHTML = '<span class="bc-muted">暂无板块配置。</span>';
        return;
    }
    elements.sections.innerHTML = items.map((section) => {
        const accent = /^#[0-9a-f]{6}$/i.test(String(section.accent_color || '')) ? section.accent_color : '#2563eb';
        const note = section.is_career ? '就业信息优先采集' : '按板块关键词轮换采集';
        return `
            <article class="bc-section-card" style="--section-accent:${accent}">
                <strong>${escapeHtml(section.icon || '•')} ${escapeHtml(section.name || '')}</strong>
                <p>${escapeHtml(section.description || '')}</p>
                <div class="bc-section-card__actions">
                    <span class="bc-muted">${section.is_enabled === false ? '已停用 · ' : ''}${escapeHtml(note)}</span>
                    ${canManage ? `<button type="button" class="btn btn-ghost btn-sm" data-bc-edit-section="${escapeHtml(section.section_key || '')}">编辑</button>` : ''}
                </div>
            </article>
        `;
    }).join('');
}

async function loadManagedSections() {
    if (!canManage) return;
    const data = await apiFetch('/api/blog/sections/manage');
    managedSections = Array.isArray(data.sections) ? data.sections : [];
    renderSections(managedSections);
}

function renderReports(items) {
    if (!elements.reportList) return;
    if (!Array.isArray(items) || !items.length) {
        elements.reportList.innerHTML = '<p class="bc-muted">当前没有待处理反馈。</p>';
        return;
    }
    elements.reportList.innerHTML = items.map((item) => `
        <article class="bc-post">
            <strong>${escapeHtml(item.target_title || `${item.target_type} #${item.target_id}`)}</strong>
            <div class="bc-muted">${escapeHtml(item.reason_code || '')} · ${escapeHtml(item.reporter_identity || '')} · ${escapeHtml(item.created_at || '')}</div>
            ${item.details ? `<p>${escapeHtml(item.details)}</p>` : ''}
            <div class="flex gap-2">
                ${item.target_url ? `<a class="btn btn-ghost btn-sm" href="${escapeHtml(item.target_url)}" target="_blank" rel="noopener noreferrer">打开内容核验</a>` : ''}
                <button type="button" class="btn btn-primary btn-sm" data-bc-resolve-report="${item.id}" data-status="resolved">标记已核验</button>
                <button type="button" class="btn btn-ghost btn-sm" data-bc-resolve-report="${item.id}" data-status="dismissed">驳回</button>
            </div>
        </article>
    `).join('');
}

async function loadManagedReports() {
    if (!canManage) return;
    const data = await apiFetch('/api/blog/reports/manage');
    renderReports(data.reports || []);
}

async function handleReportResolution(button) {
    button.disabled = true;
    try {
        await apiFetch(`/api/blog/reports/${encodeURIComponent(button.dataset.bcResolveReport)}/resolve`, {
            method: 'POST',
            body: { status: button.dataset.status || 'resolved' },
        });
        showMessage('内容反馈已处理。', 'success');
        await loadManagedReports();
    } catch (error) {
        showMessage(error.message || '反馈处理失败。', 'error');
    } finally {
        button.disabled = false;
    }
}

function openSectionEditor(section = null) {
    if (!elements.sectionForm) return;
    editingSectionKey = section?.section_key || '';
    elements.sectionForm.hidden = false;
    const field = (name) => elements.sectionForm.elements.namedItem(name);
    field('section_key').value = section?.section_key || '';
    field('section_key').readOnly = Boolean(section);
    field('name').value = section?.name || '';
    field('short_name').value = section?.short_name || '';
    field('description').value = section?.description || '';
    field('icon').value = section?.icon || '•';
    field('accent_color').value = /^#[0-9a-f]{6}$/i.test(section?.accent_color || '') ? section.accent_color : '#2563eb';
    field('sort_order').value = section?.sort_order ?? 100;
    field('source_keywords').value = (section?.source_keywords || []).join('\n');
    field('source_templates').value = (section?.source_templates || []).length
        ? JSON.stringify(section.source_templates, null, 2)
        : '';
    field('is_enabled').checked = section?.is_enabled ?? true;
    field('allow_user_posts').checked = section?.allow_user_posts ?? true;
    field('is_career').checked = Boolean(section?.is_career);
    elements.sectionForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
    field('name').focus();
}

function closeSectionEditor() {
    if (!elements.sectionForm) return;
    elements.sectionForm.hidden = true;
    editingSectionKey = '';
    elements.sectionForm.reset();
}

function sectionFormPayload() {
    const form = elements.sectionForm;
    const field = (name) => form.elements.namedItem(name);
    let sourceTemplates = [];
    const sourceText = String(field('source_templates').value || '').trim();
    if (sourceText) {
        sourceTemplates = JSON.parse(sourceText);
        if (!Array.isArray(sourceTemplates)) throw new Error('专属信息源必须是 JSON 数组。');
    }
    return {
        section_key: String(field('section_key').value || '').trim(),
        name: String(field('name').value || '').trim(),
        short_name: String(field('short_name').value || '').trim(),
        description: String(field('description').value || '').trim(),
        icon: String(field('icon').value || '•').trim(),
        accent_color: field('accent_color').value,
        sort_order: Number(field('sort_order').value || 100),
        source_keywords: String(field('source_keywords').value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        source_templates: sourceTemplates,
        is_enabled: field('is_enabled').checked,
        allow_user_posts: field('allow_user_posts').checked,
        is_career: field('is_career').checked,
    };
}

async function saveSection(event) {
    event.preventDefault();
    if (!canManage || !elements.sectionForm) return;
    const submit = elements.sectionForm.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    try {
        const payload = sectionFormPayload();
        const url = editingSectionKey
            ? `/api/blog/sections/${encodeURIComponent(editingSectionKey)}`
            : '/api/blog/sections';
        await apiFetch(url, { method: editingSectionKey ? 'PUT' : 'POST', body: payload });
        showMessage(editingSectionKey ? '板块配置已更新。' : '新板块已创建。', 'success');
        closeSectionEditor();
        await refreshDashboard();
        await loadManagedSections();
    } catch (error) {
        showMessage(error.message || '板块保存失败。', 'error');
    } finally {
        if (submit) submit.disabled = false;
    }
}

function renderSources(items) {
    if (!elements.sources) return;
    if (!Array.isArray(items) || items.length === 0) {
        elements.sources.innerHTML = '<span class="bc-muted">暂无可用信息源</span>';
        return;
    }
    elements.sources.innerHTML = items
        .map((item) => `<span class="bc-chip">${escapeHtml(item.name || '')}</span>`)
        .join('');
}

function renderPosts(items) {
    if (!elements.postList) return;
    if (!Array.isArray(items) || items.length === 0) {
        elements.postList.innerHTML = '<p class="bc-muted">暂无 AI 管家发布记录。</p>';
        return;
    }
    elements.postList.innerHTML = items.map((post) => `
        <article class="bc-post">
            <strong>${escapeHtml(post.post_title || '')}</strong>
            <div class="bc-muted">${escapeHtml(post.section_key || 'general')} · ${escapeHtml(post.keyword || '')} · ${escapeHtml(post.post_created_at || '')}</div>
            <a href="/blog?post=${encodeURIComponent(post.post_id || '')}" target="_blank" rel="noopener noreferrer">打开博客</a>
        </article>
    `).join('');
}

function renderRuns(items) {
    if (!elements.runTable) return;
    if (!Array.isArray(items) || items.length === 0) {
        elements.runTable.innerHTML = '<tr><td colspan="7" class="bc-muted">暂无运行记录。</td></tr>';
        return;
    }
    elements.runTable.innerHTML = items.map((run) => `
        <tr>
            <td>${escapeHtml(run.id || '')}</td>
            <td>${escapeHtml(run.status || '')}</td>
            <td>${escapeHtml(run.new_candidate_count || 0)} / ${escapeHtml(run.candidate_count || 0)}</td>
            <td>${escapeHtml(run.duplicate_count || 0)}</td>
            <td>${escapeHtml(run.published_count || 0)}</td>
            <td>${escapeHtml(run.started_at || run.scheduled_for || run.created_at || '')}</td>
            <td>${escapeHtml(run.finished_at || '-')}</td>
        </tr>
    `).join('');
}

function renderDashboard(dashboard) {
    const config = dashboard?.config || {};
    if (elements.workerStatus) {
        const staleSuffix = dashboard?.worker_stale ? '（未连接）' : '';
        elements.workerStatus.textContent = `${config.worker_status || '未连接'}${staleSuffix}`;
    }
    if (elements.nextRun) elements.nextRun.textContent = config.next_run_at || '待生成';
    if (elements.lastRun) elements.lastRun.textContent = config.last_run_at || '暂无';
    if (elements.publishedCount) elements.publishedCount.textContent = String(dashboard?.published_count || 0);
    renderKeywords(dashboard?.keywords || []);
    renderSources(dashboard?.sources || []);
    renderSections(dashboard?.sections || []);
    renderPosts(dashboard?.recent_posts || []);
    renderRuns(dashboard?.recent_runs || []);
}

async function refreshDashboard() {
    const data = await apiFetch('/api/manage/system/blog-crawler/status');
    renderDashboard(data.dashboard || {});
    return data.dashboard;
}

async function handleSave(event) {
    event.preventDefault();
    if (!canManage || !elements.form) return;
    const submitBtn = elements.form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    try {
        await apiFetch('/api/manage/system/blog-crawler/config', {
            method: 'POST',
            body: formToPayload(elements.form),
        });
        showMessage('AI 博客管家设置已保存。', 'success');
        await refreshDashboard();
    } catch (error) {
        showMessage(error.message || '保存失败。', 'error');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function handleRun() {
    if (!canManage || !elements.runBtn) return;
    elements.runBtn.disabled = true;
    try {
        const result = await apiFetch('/api/manage/system/blog-crawler/run', { method: 'POST' });
        showMessage(result.message || '已加入执行队列。', 'success');
        await refreshDashboard();
    } catch (error) {
        showMessage(error.message || '启动失败。', 'error');
    } finally {
        elements.runBtn.disabled = false;
    }
}

async function handleCancel() {
    if (!canManage || !elements.cancelBtn) return;
    elements.cancelBtn.disabled = true;
    try {
        const result = await apiFetch('/api/manage/system/blog-crawler/cancel-pending', { method: 'POST' });
        showMessage(result.message || '已取消待执行任务。', 'success');
        await refreshDashboard();
    } catch (error) {
        showMessage(error.message || '取消失败。', 'error');
    } finally {
        elements.cancelBtn.disabled = false;
    }
}

elements.form?.addEventListener('submit', handleSave);
elements.refreshBtn?.addEventListener('click', () => {
    refreshDashboard().catch((error) => showMessage(error.message || '刷新失败。', 'error'));
});
elements.runBtn?.addEventListener('click', handleRun);
elements.cancelBtn?.addEventListener('click', handleCancel);
elements.addSectionBtn?.addEventListener('click', () => openSectionEditor());
elements.cancelSectionBtn?.addEventListener('click', closeSectionEditor);
elements.sectionForm?.addEventListener('submit', saveSection);
elements.sections?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-bc-edit-section]');
    if (!button) return;
    const section = managedSections.find((item) => item.section_key === button.dataset.bcEditSection);
    if (section) openSectionEditor(section);
});
elements.reportList?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-bc-resolve-report]');
    if (button) handleReportResolution(button);
});
elements.refreshReportsBtn?.addEventListener('click', () => {
    loadManagedReports().catch((error) => showMessage(error.message || '反馈加载失败。', 'error'));
});

loadManagedSections().catch((error) => showMessage(error.message || '板块配置加载失败。', 'error'));
loadManagedReports().catch((error) => showMessage(error.message || '反馈加载失败。', 'error'));
