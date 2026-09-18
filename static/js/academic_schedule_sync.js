/** Shared teacher-only academic timetable sync. No schedule facts are persisted in the browser. */
const activeRequests = new Map();
const validTerms = ['1', '2', '3'];

export function normalizeAcademicSyncTerm(value = {}) {
    const year = String(value.year || '').trim();
    const term = String(value.term || '').trim();
    if (!year && !term) return { year: '', term: '' };
    const match = /^(\d{4})-(\d{4})$/.exec(year);
    if (!match || Number(match[2]) !== Number(match[1]) + 1 || !validTerms.includes(term)) {
        throw new Error('请选择有效学年（如 2026-2027）及第一、第二或夏季学期。');
    }
    return { year, term };
}

export function syncAcademicSchedule(term, fetcher = fetch) {
    const target = normalizeAcademicSyncTerm(term);
    const key = `${target.year}|${target.term}`;
    if (activeRequests.has(key)) return activeRequests.get(key);
    const task = (async () => {
        const response = await fetcher('/api/manage/academic/course-schedule/academic-sync', {
            method: 'POST', credentials: 'same-origin', cache: 'no-store',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(target),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'success' || !data.overview) {
            const error = new Error(data.detail || data.message || '教务同步未完成，仍保留上次有效课表。');
            error.status = data.status || 'failed';
            throw error;
        }
        return data;
    })().finally(() => { if (activeRequests.get(key) === task) activeRequests.delete(key); });
    activeRequests.set(key, task);
    return task;
}

function ensureStyle() {
    if (document.getElementById('academic-schedule-sync-style')) return;
    const style = document.createElement('style'); style.id = 'academic-schedule-sync-style';
    style.textContent = `.cs-sync-dialog{width:min(460px,calc(100vw - 32px));max-height:90dvh;overflow:auto;border:1px solid #cbd5e1;border-radius:16px;padding:24px;color:#0f172a;background:#fff;box-shadow:0 24px 90px #0f172a33}.cs-sync-dialog::backdrop{background:#0f172a66}.cs-sync-dialog h3{margin:0 0 10px}.cs-sync-dialog p,.cs-sync-dialog label{font-size:14px;line-height:1.7}.cs-sync-dialog form{display:grid;gap:14px}.cs-sync-dialog label{display:grid;gap:5px}.cs-sync-dialog input,.cs-sync-dialog select{width:100%;min-width:0;padding:9px;border:1px solid #cbd5e1;border-radius:8px;background:#fff}.cs-sync-term-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}.cs-sync-dialog footer{display:flex;gap:8px;justify-content:flex-end}.cs-sync-error{color:#b91c1c}.cs-sync-feedback{font-size:12px;line-height:1.6;overflow-wrap:anywhere}.cs-sync-feedback:empty{display:none}.ls-course-search{flex-wrap:wrap}.ls-course-search [data-dashboard-search]{flex:1 1 180px}.ls-course-search [data-academic-schedule-sync]{white-space:nowrap}.ls-course-search .cs-sync-feedback{flex-basis:100%}`;
    document.head.appendChild(style);
}

export function createAcademicScheduleSync({ button, getTerm, getContext, onStart, onSuccess, onError, onMessage } = {}) {
    if (!button) return null;
    ensureStyle();
    let busy = false;
    const feedback = document.createElement('span'); feedback.className = 'cs-sync-feedback'; feedback.setAttribute('role', 'status');
    button.insertAdjacentElement('afterend', feedback);
    const dialog = document.createElement('dialog'); dialog.className = 'cs-sync-dialog';
    dialog.innerHTML = `<form><h3>同步教务课表与申请</h3><p>读取同一学期的正式课表和调停课申请。待审申请只显示预测位置，不改变真实课次。</p><label>同步范围<select name="scope"><option value="selected">指定学年学期</option><option value="current">发现教务当前学期</option></select></label><div class="cs-sync-term-fields"><label>学年<input name="year" placeholder="2026-2027" pattern="[0-9]{4}-[0-9]{4}" required></label><label>学期<select name="term"><option value="1">第一学期</option><option value="2">第二学期</option><option value="3">夏季学期</option></select></label></div><p data-sync-error class="cs-sync-error" role="alert" hidden></p><footer><button type="button" data-sync-cancel class="ls-button">取消</button><button type="submit" class="ls-button ls-button-primary">开始同步</button></footer></form>`;
    document.body.appendChild(dialog);
    const form = dialog.querySelector('form'), scope = form.elements.scope, year = form.elements.year, term = form.elements.term;
    const updateScope = () => { const discover = scope.value === 'current'; year.disabled = term.disabled = discover; };
    scope.addEventListener('change', updateScope);
    dialog.querySelector('[data-sync-cancel]').addEventListener('click', () => dialog.close());
    button.addEventListener('click', event => {
        event.preventDefault(); if (busy) return;
        const selected = getTerm?.() || {};
        year.value = selected.year || ''; term.value = validTerms.includes(String(selected.term)) ? String(selected.term) : '1';
        scope.value = selected.year && validTerms.includes(String(selected.term)) ? 'selected' : 'current';
        updateScope(); dialog.querySelector('[data-sync-error]').hidden = true; dialog.showModal();
    });
    form.addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return;
        let target;
        try { target = normalizeAcademicSyncTerm(scope.value === 'current' ? {} : { year: year.value, term: term.value }); }
        catch (error) { const note = dialog.querySelector('[data-sync-error]'); note.textContent = error.message; note.hidden = false; return; }
        const context = getContext?.();
        const label = button.querySelector('.app-topbar-action__text strong') || button;
        const previous = label.textContent;
        busy = true; button.disabled = true; button.setAttribute('aria-busy', 'true'); label.textContent = '同步中…';
        feedback.textContent = '正在读取并核对教务课表与申请…'; dialog.close();
        try {
            onStart?.(target, context);
            const data = await syncAcademicSchedule(target);
            await onSuccess?.(data, { target, context });
            const warnings = data.overview.warnings || data.overview.sync_state?.warnings || data.result?.warnings || [];
            feedback.textContent = [...new Set([data.message || '教务同步完成。', data.overview.message, ...(Array.isArray(warnings) ? warnings.map(item => typeof item === 'string' ? item : item.message) : [])].filter(Boolean))].join(' ');
            onMessage?.(feedback.textContent, 'success');
        } catch (error) {
            feedback.textContent = `${error.message || '教务同步失败。'} 原有课表已保留。`;
            onError?.(error, { target, context }); onMessage?.(feedback.textContent, 'error');
        } finally { busy = false; button.disabled = false; button.removeAttribute('aria-busy'); label.textContent = previous; }
    });
    return { isBusy: () => busy, destroy() { dialog.remove(); feedback.remove(); } };
}
