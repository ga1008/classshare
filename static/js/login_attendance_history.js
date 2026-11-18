import { openLoginAttendance } from './login_attendance.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createForm } from './lq/forms.js';
import { enhanceDropdowns } from './lq/dropdown.js';

export function openAttendanceHistory({ classOfferingId, trigger, preferredRoundId }) {
    return openLoginAttendance({ classOfferingId, userRole: 'teacher', trigger, historyOnly: true,
        attendanceId: preferredRoundId, mountHistory: mountHistoryNavigation })?.handle;
}

function roundLabel(row) {
    const c = row.context || {};
    return [c.session_date, c.detail_title || c.session_title || c.course_name, c.class_name || c.teaching_class_name,
        `点名 ${row.id}`].filter(Boolean).join(' · ');
}

function mountHistoryNavigation({ host, request, openRound, preferredRoundId }) {
    const message = document.createElement('p');
    message.setAttribute('role', 'status');
    const controls = document.createElement('div');
    controls.className = 'la-actions';
    const newer = createDomainButton({ label: '较新一轮' });
    const older = createDomainButton({ label: '较早一轮' });
    const retry = createDomainButton({ label: '重新读取' });
    controls.append(newer, older, retry);
    const jump = document.createElement('details');
    jump.className = 'lq-collapsible';
    const summary = document.createElement('summary');
    summary.className = 'lq-collapsible__summary';
    summary.textContent = '按课次查找';
    const field = createForm('select', { id: 'la-history-' + crypto.randomUUID(), label: '本页点名记录', options: [] });
    const select = field.querySelector('select');
    const pages = document.createElement('div');
    pages.className = 'la-actions';
    const previousPage = createDomainButton({ label: '较新一页' }), nextPage = createDomainButton({ label: '更早一页' });
    pages.append(previousPage, nextPage);
    const jumpContent = document.createElement('div');
    jumpContent.className = 'lq-collapsible__content la-workspace';
    jumpContent.append(field, pages); jump.append(summary, jumpContent);
    host.append(message, controls, jump);
    const dropdowns = enhanceDropdowns(jump);

    // Keep one bounded page of round summaries. Only cursors are retained when moving back.
    let items = [], index = -1, cursors = [null], next = null, pending = false, saving = false, disposed = false, retryAction = null;
    let fetchedPage = null;
    function paint() {
        const first = index <= 0, last = index >= items.length - 1, anchored = cursors.length === 1 && cursors[0] !== null;
        const hasNewerPage = cursors.length > 1 || anchored;
        const busy = pending || saving;
        newer.disabled = busy || (first && !hasNewerPage);
        older.disabled = busy || (last && !next);
        newer.textContent = first && anchored ? '最新一轮' : '较新一轮';
        previousPage.disabled = busy || !hasNewerPage;
        nextPage.disabled = busy || !next;
        pages.hidden = !hasNewerPage && !next;
        select.disabled = busy;
        select.value = String(items[index]?.id ?? '');
        retry.hidden = !retryAction;
        retry.disabled = busy;
        newer.hidden = older.hidden = items.length < 2 && !hasNewerPage && !next;
        controls.hidden = newer.hidden && retry.hidden;
        jump.hidden = items.length < 3 && !hasNewerPage && !next;
        message.hidden = !message.textContent;
        host.hidden = message.hidden && controls.hidden && jump.hidden;
    }
    async function run(action) {
        if (pending || saving || disposed) return;
        const focus = host.contains(document.activeElement) ? document.activeElement : null;
        pending = true; retryAction = null;
        message.setAttribute('role', 'status');
        message.textContent = items.length ? '' : '正在读取点名记录…';
        paint();
        try { await action(); }
        catch (error) {
            if (!disposed) {
                message.textContent = error.message || '读取点名记录失败。';
                message.setAttribute('role', 'alert');
                retryAction = action;
            }
        } finally {
            pending = false;
            if (!disposed) {
                paint();
                if (focus && document.activeElement === document.body) {
                    [focus, retry, older, newer].find(el => !el.disabled && el.getClientRects().length)?.focus({ preventScroll: true });
                }
            }
        }
    }
    async function show(rows, pos, pageCursors, nextCursor) {
        if (disposed) return;
        if (!rows.length) {
            if (items.length) throw new Error('该页记录已变化，请重新读取。');
            message.textContent = '本课程暂无点名记录。';
            return;
        }
        if (!await openRound(rows[pos].id) || disposed) return;
        if (items !== rows) select.replaceChildren(...rows.map(r => new Option(roundLabel(r), String(r.id))));
        items = rows; index = pos; cursors = pageCursors; next = nextCursor;
        message.textContent = '';
    }
    async function fetchPage(cursor) {
        if (fetchedPage?.cursor === cursor) return fetchedPage.value;
        const query = new URLSearchParams({ limit: '50' });
        if (cursor) query.set('before_id', String(cursor));
        const value = await request('/history?' + query);
        fetchedPage = { cursor, value };
        return value;
    }
    async function loadLatest() {
        let value = await fetchPage(null);
        let pos = value.items.findIndex(r => r.id === preferredRoundId);
        let history = [null];
        // An explicit lesson can point further back than the latest page. Start at
        // that exact round with one bounded cursor lookup; the course header starts at the latest.
        if (pos < 0 && preferredRoundId && value.next_before_id && preferredRoundId < value.next_before_id) {
            const older = await fetchPage(preferredRoundId + 1);
            if (older.items[0]?.id === preferredRoundId) {
                value = older; history = [preferredRoundId + 1];
            }
        }
        await show(value.items, Math.max(0, pos), history, value.next_before_id);
    }
    async function changePage(direction) {
        let path;
        let last;
        if (direction > 0) { path = [...cursors, next]; last = false; }
        else if (cursors.length === 1) { path = [null]; last = false; }
        else { path = cursors.slice(0, -1); last = true; }
        const value = await fetchPage(path.at(-1));
        await show(value.items, last ? value.items.length - 1 : 0, path, value.next_before_id);
    }
    newer.addEventListener('click', () => run(() => index > 0 ? show(items, index - 1, cursors, next) : changePage(-1)));
    older.addEventListener('click', () => run(() => index + 1 < items.length ? show(items, index + 1, cursors, next) : changePage(1)));
    previousPage.addEventListener('click', () => run(() => changePage(-1)));
    nextPage.addEventListener('click', () => run(() => changePage(1)));
    select.addEventListener('change', () => {
        const pos = select.selectedIndex;
        if (pos >= 0 && pos !== index) void run(() => show(items, pos, cursors, next));
    });
    retry.addEventListener('click', () => { if (retryAction) void run(retryAction); });
    void run(loadLatest);
    return {
        setSaving(value) { saving = value; paint(); },
        destroy() { disposed = true; dropdowns.forEach(binding => binding.destroy()); }
    };
}
