import { apiFetch } from '/static/js/api.js';
import { setOverlayOpen } from '/static/js/ui_overlay_motion.js';
import { showToast } from '/static/js/ui.js';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

export function initClassroomMemberWorkspace(config, initializePanel) {
    const modal = document.querySelector('[data-member-workspace]');
    if (!modal || modal.dataset.memberControllerReady === '1') return;
    modal.dataset.memberControllerReady = '1';
    const offeringId = Number(modal.dataset.classOfferingId);
    const shell = modal.querySelector('.learning-modal-shell');
    const triggers = [...document.querySelectorAll('[data-learning-modal-open], [data-learning-scroll]')];
    const tabs = [...modal.querySelectorAll('[data-member-tab]')];
    const panels = Object.fromEntries([...modal.querySelectorAll('[data-member-panel]')].map(panel => [panel.dataset.memberPanel, panel]));
    const tabbar = modal.querySelector('[data-member-tabs]');
    const panelHost = modal.querySelector('[data-member-panels]');
    const detail = modal.querySelector('[data-member-detail]');
    const frame = detail.querySelector('iframe');
    const loading = detail.querySelector('[data-student-insight-loading]');
    const prompt = modal.querySelector('[data-member-draft-prompt]');
    const closeButton = modal.querySelector('#learning-modal-close');
    const search = modal.querySelector('[data-learning-roster-search]');
    const classFilter = modal.querySelector('[data-member-class-filter]');
    const stateFilter = modal.querySelector('[data-member-state-filter]');
    const roster = modal.querySelector('[data-learning-roster-list]');
    const empty = modal.querySelector('[data-learning-roster-empty]');
    const controllers = new Map(), pending = new Map(), loaded = new Set();
    let activeTab = 'members', activeTrigger = null, detailTrigger = null, generation = 0;
    let closeOperation = null, page = 1, pages = 1, rosterEpoch = 0, rosterAbort = null, filterTimer = 0, detailTimer = 0;

    const setVisible = (element, visible) => { element.hidden = !visible; element.inert = !visible; };
    const dirtyControllers = () => [...controllers.entries()].filter(([, controller]) => controller?.isDirty?.());
    const notifyVisibility = open => {
        modal.dispatchEvent(new CustomEvent('member-workspace-visibility', { bubbles: true, detail: { open, tab: activeTab } }));
    };
    function hidePrompt() {
        prompt.hidden = true;
        tabbar.inert = !detail.hidden;
        panelHost.inert = !detail.hidden;
        detail.inert = detail.hidden;
        closeButton.inert = false;
    }
    function showError(panel, message, retry) {
        panel.innerHTML = `<div class="member-panel-message" role="alert"><p>${escapeHtml(message || '读取失败，请稍后重试。')}</p><button type="button" class="btn btn-outline btn-sm" data-member-retry>重新读取</button></div>`;
        panel.querySelector('[data-member-retry]').addEventListener('click', retry);
    }
    async function loadRoster({ resetScroll = false } = {}) {
        const epoch = ++rosterEpoch;
        rosterAbort?.abort();
        const abort = rosterAbort = new AbortController();
        const params = new URLSearchParams({ q: search.value.trim(), state: stateFilter.value, page: String(page), page_size: '50' });
        if (classFilter.value) params.set('class_id', classFilter.value);
        roster.setAttribute('aria-busy', 'true');
        empty.hidden = true;
        try {
            const data = await apiFetch(`/api/classrooms/${offeringId}/members?${params}`, { signal: abort.signal, silent: true });
            if (epoch !== rosterEpoch || abort.signal.aborted) return;
            page = data.page; pages = data.pages;
            const selected = classFilter.value;
            classFilter.innerHTML = '<option value="">全部班级</option>' + data.classes.map(item => `<option value="${Number(item.id)}">${escapeHtml(item.name)} · ${Number(item.student_count)} 人</option>`).join('');
            classFilter.value = selected;
            let group = null;
            roster.innerHTML = data.items.map(item => {
                let heading = '';
                if (data.classes.length > 1 && group !== item.class_id) {
                    group = item.class_id;
                    heading = `<div class="member-roster-class-head"><strong>${escapeHtml(item.class_name)}</strong></div>`;
                }
                const score = item.score == null ? null : Number(item.score);
                const progress = Math.max(0, Math.min(100, Number(item.progress_percent || 0)));
                const metric = score == null ? '学情尚未生成' : `修为 ${escapeHtml(score)} · ${progress}%${item.snapshot_dirty ? ' · 待更新' : ''}`;
                return `${heading}<a class="member-row" href="/manage/students/${Number(item.id)}" data-student-insight-open data-student-insight-url="/manage/students/${Number(item.id)}?embed=1" data-student-name="${escapeHtml(item.name)}" data-learning-roster-item><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.student_id_number || '未填学号')} · ${escapeHtml(item.class_name)}</small></span><span class="member-row-progress">${score == null ? '' : `<progress value="${progress}" max="100" aria-label="本课进度 ${progress}%"></progress>`}${metric}</span>${item.needs_attention ? '<span class="member-row-tag">学习预警</span>' : '<span aria-hidden="true">→</span>'}</a>`;
            }).join('');
            modal.querySelector('[data-member-roster-summary]').textContent = `全班 ${data.student_count} 人 · 当前筛选 ${data.total} 人 · 有预警 ${data.attention_count} 人`;
            modal.querySelector('[data-member-page]').textContent = `${page} / ${pages} 页`;
            modal.querySelector('[data-member-previous]').disabled = page <= 1;
            modal.querySelector('[data-member-next]').disabled = page >= pages;
            if (!data.items.length) { empty.hidden = false; empty.textContent = data.student_count ? '没有匹配的成员，试试调整筛选条件。' : '当前课堂还没有学生名单。'; }
            if (resetScroll) panels.members.scrollTop = 0;
            loaded.add('members');
        } catch (error) {
            if (epoch !== rosterEpoch || abort.signal.aborted) return;
            empty.hidden = false;
            empty.textContent = error.message || '成员名单读取失败，请点击刷新重试。';
            modal.querySelector('[data-member-roster-summary]').textContent = '成员名单读取失败';
        } finally {
            if (epoch === rosterEpoch) roster.setAttribute('aria-busy', 'false');
        }
    }
    async function ensurePanel(key) {
        if (key === 'members') { if (!loaded.has(key)) await loadRoster(); return; }
        if (loaded.has(key)) { controllers.get(key)?.activate?.(); return; }
        if (pending.has(key)) return pending.get(key).promise;
        const panel = panels[key], abort = new AbortController();
        const operation = { abort, promise: null };
        pending.set(key, operation);
        const run = async () => {
            try {
                let controller;
                if (key === 'attendance') {
                    const module = await import('/static/js/attendance_reports.js');
                    if (abort.signal.aborted) return;
                    controller = await module.initClassroomAttendancePanel(panel.querySelector('[data-attendance-classroom-panel]'));
                } else {
                    panel.innerHTML = '<p class="member-panel-message" role="status">正在读取…</p>';
                    const html = await apiFetch(`/api/classrooms/${offeringId}/member-panels/${key}`, { headers: { Accept: 'text/html' }, signal: abort.signal, silent: true });
                    if (abort.signal.aborted) return;
                    panel.innerHTML = html;
                    if (key === 'overview' || key === 'alerts') {
                        const refresh = document.createElement('button');
                        refresh.type = 'button'; refresh.className = 'btn btn-ghost btn-sm'; refresh.textContent = '刷新';
                        refresh.addEventListener('click', () => {
                            controllers.get(key)?.destroy?.(); loaded.delete(key); void ensurePanel(key);
                        });
                        panel.prepend(refresh);
                    }
                    controller = await initializePanel(key, panel);
                }
                controllers.set(key, controller || {});
                loaded.add(key);
                if (modal.hidden || key !== activeTab || !detail.hidden) controller?.deactivate?.();
                else controller?.activate?.();
            } catch (error) {
                if (abort.signal.aborted) return;
                if (key === 'attendance') {
                    const target = panel.querySelector('[data-attendance-classroom-panel]');
                    if (target) showError(target, error.message, () => ensurePanel(key));
                } else showError(panel, error.message, () => ensurePanel(key));
            } finally { if (pending.get(key) === operation) pending.delete(key); }
        };
        operation.promise = run();
        return operation.promise;
    }
    function activate(key, focus = false) {
        if (!panels[key]) key = 'members';
        controllers.get(activeTab)?.deactivate?.();
        activeTab = key;
        tabs.forEach(tab => {
            const selected = tab.dataset.memberTab === key;
            tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
            if (selected) { tab.scrollIntoView({ block: 'nearest', inline: 'nearest' }); if (focus) tab.focus({ preventScroll: true }); }
        });
        Object.entries(panels).forEach(([name, panel]) => setVisible(panel, name === key));
        if (!modal.hidden && detail.hidden) void ensurePanel(key);
        notifyVisibility(!modal.hidden);
    }
    function leaveDetail() {
        window.clearTimeout(detailTimer);
        setVisible(detail, false); setVisible(panelHost, true); setVisible(tabbar, true);
        detailTrigger?.focus({ preventScroll: true });
        detailTrigger = null;
        if (!modal.hidden) void ensurePanel(activeTab);
    }
    function open(trigger) {
        generation++; closeOperation = null; activeTrigger = trigger || document.activeElement;
        document.body.classList.add('has-learning-modal');
        modal.setAttribute('aria-hidden', 'false'); modal.classList.add('is-open');
        triggers.forEach(item => item.setAttribute('aria-expanded', 'true'));
        setOverlayOpen(modal, true); hidePrompt();
        activate(activeTab); closeButton.focus({ preventScroll: true });
    }
    async function close(force = false) {
        if (modal.hidden) return true;
        if ([...controllers.values()].some(controller => controller?.isBusy?.() && controller?.isDirty?.())) {
            showToast('修改正在处理，请稍后关闭。', 'info');
            return false;
        }
        const dirty = dirtyControllers();
        if (!force && dirty.length) {
            if (!detail.hidden) leaveDetail();
            prompt.hidden = false; tabbar.inert = true; panelHost.inert = true; detail.inert = true; closeButton.inert = true;
            prompt.querySelector('button').focus({ preventScroll: true });
            return false;
        }
        if (closeOperation) return closeOperation;
        const epoch = ++generation;
        window.clearTimeout(filterTimer); window.clearTimeout(detailTimer);
        rosterAbort?.abort(); rosterEpoch++;
        pending.forEach(item => item.abort.abort()); pending.clear();
        controllers.forEach(controller => controller.deactivate?.());
        notifyVisibility(false); modal.classList.remove('is-open');
        const operation = setOverlayOpen(modal, false).then(completed => {
            if (!completed || epoch !== generation) return false;
            modal.setAttribute('aria-hidden', 'true'); document.body.classList.remove('has-learning-modal');
            triggers.forEach(item => item.setAttribute('aria-expanded', 'false'));
            activeTrigger?.focus?.({ preventScroll: true }); activeTrigger = null;
            return true;
        }).finally(() => { if (closeOperation === operation) closeOperation = null; });
        closeOperation = operation; return operation;
    }
    triggers.forEach(trigger => {
        trigger.setAttribute('aria-expanded', 'false');
        trigger.addEventListener('click', () => open(trigger));
    });
    closeButton.addEventListener('click', () => close());
    modal.addEventListener('click', event => {
        if (event.target === modal) { void close(); return; }
        const trigger = event.target.closest('[data-student-insight-open]');
        if (!trigger || !modal.contains(trigger)) return;
        event.preventDefault(); detailTrigger = trigger;
        controllers.get(activeTab)?.deactivate?.();
        setVisible(panelHost, false); setVisible(tabbar, false); setVisible(detail, true);
        detail.querySelector('[data-member-detail-title]').textContent = `${trigger.dataset.studentName || '学生'} · 成员详情`;
        detail.querySelector('[data-member-detail-link]').href = trigger.getAttribute('href');
        loading.hidden = false; loading.textContent = '正在加载成员详情…';
        frame.src = trigger.dataset.studentInsightUrl;
        detail.querySelector('[data-member-detail-back]').focus({ preventScroll: true });
        window.clearTimeout(detailTimer);
        detailTimer = window.setTimeout(() => { loading.textContent = '详情加载较慢，可使用“独立打开”继续查看。'; }, 10000);
    });
    frame.addEventListener('load', () => { window.clearTimeout(detailTimer); loading.hidden = true; });
    detail.querySelector('[data-member-detail-back]').addEventListener('click', leaveDetail);
    prompt.querySelector('[data-member-draft-edit]').addEventListener('click', () => {
        const key = dirtyControllers()[0]?.[0]; hidePrompt(); if (key) activate(key, true); else closeButton.focus();
    });
    prompt.querySelector('[data-member-draft-discard]').addEventListener('click', () => {
        dirtyControllers().forEach(([, controller]) => controller.reset?.()); hidePrompt(); void close(true);
    });
    tabs.forEach((tab, index) => {
        tab.addEventListener('click', () => activate(tab.dataset.memberTab));
        tab.addEventListener('keydown', event => {
            const indexFor = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 };
            if (!(event.key in indexFor)) return;
            event.preventDefault(); const target = tabs[indexFor[event.key]];
            tabs.forEach(item => { item.tabIndex = item === target ? 0 : -1; }); target.focus(); target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        });
    });
    document.addEventListener('keydown', event => {
        if (modal.hidden) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            if (!prompt.hidden) { hidePrompt(); closeButton.focus(); }
            else if (!detail.hidden) leaveDetail(); else void close();
            return;
        }
        if (event.key !== 'Tab') return;
        if (closeOperation) { event.preventDefault(); return; }
        const scope = prompt.hidden ? shell : prompt;
        const focusable = [...scope.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),iframe,[tabindex="0"]')].filter(item => !item.closest('[hidden],[inert]') && item.getClientRects().length);
        const first = focusable[0], last = focusable.at(-1);
        if (!first) { event.preventDefault(); shell.focus(); }
        else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    search.addEventListener('input', () => { window.clearTimeout(filterTimer); filterTimer = window.setTimeout(() => { page = 1; void loadRoster({ resetScroll: true }); }, 300); });
    [classFilter, stateFilter].forEach(control => control.addEventListener('change', () => { page = 1; void loadRoster({ resetScroll: true }); }));
    modal.querySelector('[data-member-roster-refresh]').addEventListener('click', () => loadRoster());
    modal.querySelector('[data-member-previous]').addEventListener('click', () => { if (page > 1) { page--; void loadRoster({ resetScroll: true }); } });
    modal.querySelector('[data-member-next]').addEventListener('click', () => { if (page < pages) { page++; void loadRoster({ resetScroll: true }); } });
    window.addEventListener('beforeunload', event => { if (dirtyControllers().length) { event.preventDefault(); event.returnValue = ''; } });
    modal.addEventListener('member-weights-saved', () => {
        loaded.delete('overview');
        if (!modal.hidden && activeTab === 'overview') void ensurePanel('overview');
        loaded.delete('members');
    });
    return { activate, open, close };
}
