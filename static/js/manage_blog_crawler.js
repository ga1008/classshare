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
    postList: document.getElementById('bc-post-list'),
    runTable: document.getElementById('bc-run-table'),
};

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
        .map((item) => `<span class="bc-chip">${escapeHtml(item.keyword || '')}</span>`)
        .join('');
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
            <div class="bc-muted">${escapeHtml(post.keyword || '')} · ${escapeHtml(post.post_created_at || '')}</div>
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
