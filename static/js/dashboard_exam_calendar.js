import { apiFetch } from './api.js';
import { createDomainButton } from './lq/domain-controls.js';
import { openDialog } from './lq/dialogs.js';

const DAY = 86400000;
const SCHOOL_OFFSET = 8 * 3600000;
const endpoint = '/api/dashboard/academic-exams';
const schoolTime = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const pending = status => status === 'running' || status === 'pending';
const schoolDay = ms => new Date(ms + SCHOOL_OFFSET).toISOString().slice(0, 10);

function instant(value) {
    if (!value || !/[T ]\d\d:\d\d/.test(value)) return NaN;
    const iso = value.replace(' ', 'T');
    return Date.parse(/(?:Z|[+-]\d\d:?\d\d)$/i.test(iso) ? iso : iso + '+08:00');
}

function eventView(item, now) {
    const start = instant(item.starts_at);
    const end = instant(item.ends_at);
    const date = Number.isFinite(start) ? schoolDay(start) : item.date || '';
    const days = (Date.parse(date) - Date.parse(schoolDay(now))) / DAY;
    if (end <= now || (days < 0 && !(end > now))) return null;
    const ongoing = start <= now && now < end;
    const countdown = ongoing ? '进行中' : !Number.isFinite(days) ? '待定' :
        days <= 0 ? '今天' : days === 1 ? '明天' : `还有 ${days} 天`;
    const kind = item.kind === 'invigilation' ? '监考' : '考试';
    const dayLabel = Number.isFinite(days) ? date : '日期待定';
    let time = '';
    if (Number.isFinite(start)) time = schoolTime.format(start);
    if (Number.isFinite(end)) time += (time ? ' – ' : '截至 ') + schoolTime.format(end);
    return { item, kind, dayLabel, time, countdown, start, end,
        order: Number.isFinite(start) ? start : Date.parse(date + 'T00:00:00+08:00') || Infinity };
}

function element(tag, label, className) {
    const node = document.createElement(tag);
    node.textContent = label;
    if (className) node.className = className;
    return node;
}

function detail(view, trigger, onClose) {
    const { item, kind, dayLabel, time } = view;
    const facts = element('dl', '', 'ls-exam-facts');
    const rows = [
        ['科目', item.subject], ['日期', dayLabel], [item.room_time_varies ? '场次时间范围' : '时间', time],
        ['考场', item.location || '教室待定'],
        ...(item.detail_rows || []).map(row => [row.label, row.value]),
    ];
    for (const [label, value] of rows) {
        if (label && value) facts.append(element('dt', label), element('dd', value));
    }
    openDialog({ title: kind + '详情', size: 'sm', body: facts, attrs: { 'data-exam-details': '' } },
        { trigger, onClose, onDestroy: onClose });
}

function mountExamCalendar(panel) {
    const list = panel.querySelector('[data-exam-list]');
    const empty = panel.querySelector('[data-exam-empty]');
    const status = panel.querySelector('[data-exam-status]');
    const refresh = panel.querySelector('[data-exam-refresh]');
    let snapshot = null;
    let readAgain = false;
    let loading = false;
    let dialogOpen = false;
    let poll = 0;
    let clock = 0;
    let interval = 2000;
    let lastList = '';

    function renderItems() {
        clearTimeout(clock);
        if (!snapshot || dialogOpen || document.hidden) return;
        const now = Date.now();
        const views = snapshot.items.map(item => eventView(item, now))
            .filter(Boolean).sort((a, b) => a.order - b.order);
        const fingerprint = JSON.stringify(views);
        if (lastList !== fingerprint) {
            const fragment = document.createDocumentFragment();
            const focused = document.activeElement;
            const focusedKey = list.contains(focused) ? focused.dataset.examKey : null;
            let replacement = null;
            for (const view of views) {
                const {item, kind, dayLabel, countdown} = view;
                const button = createDomainButton({ kind: 'choice', variant: 'glass', className: 'ls-exam-row',
                    attrs: { 'data-lq-shape': 'surface', 'aria-haspopup': 'dialog' } });
                button.dataset.examKey = JSON.stringify([item.kind, item.subject, dayLabel, view.start, item.location]);
                if (button.dataset.examKey === focusedKey) replacement = button;
                const copy = element('span', '', 'ls-exam-copy');
                copy.append(element('strong', item.subject || kind),
                    element('small', [kind, dayLabel, item.location || '教室待定'].join(' · ')));
                button.append(copy, element('span', countdown, 'ls-exam-count'));
                button.addEventListener('click', () => {
                    dialogOpen = true;
                    detail(view, button, () => { dialogOpen = false; renderItems(); });
                });
                const entry = document.createElement('li');
                entry.append(button);
                fragment.append(entry);
            }
            list.replaceChildren(fragment);
            list.hidden = !views.length;
            empty.hidden = Boolean(views.length);
            if (focusedKey) (replacement || refresh).focus({ preventScroll: true });
            lastList = fingerprint;
        }
        // Wake at the next school midnight or precise exam boundary.
        let next = Math.floor((now + SCHOOL_OFFSET) / DAY + 1) * DAY - SCHOOL_OFFSET;
        for (const view of views) {
            for (const boundary of [view.start, view.end]) if (boundary > now && boundary < next) next = boundary;
        }
        clock = window.setTimeout(renderItems, next - now + 25);
    }

    function updateControl() {
        const queued = pending(snapshot?.sync?.status);
        refresh.disabled = loading || (!readAgain && (queued || snapshot?.can_refresh === false));
        refresh.setAttribute('aria-busy', String(loading || (queued && !readAgain)));
        const label = readAgain ? '重新读取教务安排' : '同步本学期教务安排';
        refresh.setAttribute('aria-label', label);
        refresh.title = label;
    }

    async function load(method = 'GET') {
        if (loading) return;
        clearTimeout(poll);
        loading = true;
        updateControl();
        if (method === 'POST') status.textContent = '正在提交本学期同步请求…';
        try {
            const data = await apiFetch(endpoint + (method === 'POST' ? '/refresh' : ''), {
                method, credentials: 'same-origin', cache: 'no-store', silent: true, signal: AbortSignal.timeout(20000),
            });
            if (!Array.isArray(data?.items) || !data.sync) throw new Error('教务安排暂时无法读取，请重试。');
            snapshot = data;
            readAgain = false;
            renderItems();
            const state = data.sync.status;
            status.textContent = data.sync.message || (pending(state) ? '正在同步本学期安排…' :
                state === 'failed' ? '同步失败，可再次同步。' :
                state === 'partial_success' ? '部分安排同步失败，可再次同步。' :
                state === 'unavailable' ? '当前没有可同步的教务账号。' :
                state === 'success' ? '本学期安排已更新。' : '');
            if (pending(state)) {
                if (!document.hidden) poll = window.setTimeout(() => load(), interval);
                interval = Math.min(interval * 1.5, 12000);
            } else interval = 2000;
        } catch (error) {
            readAgain = true;
            status.textContent = (error.message || '读取教务安排失败。') + ' 可点击右上角重新读取。';
            // A lost POST response can still represent an accepted background job.
            if ((method === 'POST' || pending(snapshot?.sync?.status)) && !document.hidden)
                poll = window.setTimeout(() => load(), 15000);
        } finally {
            loading = false;
            updateControl();
        }
    }

    refresh.addEventListener('click', () => load(readAgain || !snapshot ? 'GET' : 'POST'));
    document.addEventListener('visibilitychange', () => {
        clearTimeout(poll);
        clearTimeout(clock);
        if (!document.hidden) { renderItems(); void load(); }
    });
    void load();
}

const panel = document.querySelector('[data-exam-calendar]');
if (panel) mountExamCalendar(panel);
