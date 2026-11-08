/** Teacher-only durable appeal attention; server leases arbitrate tabs. */
import { apiFetch } from './api.js';
import { openDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createAmbientPoll } from './ambient_poll.js';

export function initAttendanceAttention({ enabled = true, navigate = url => window.location.assign(url) } = {}) {
    if (!enabled) return () => {};
    const base = '/api/login-attendance/attention', owner = `tab-${crypto.randomUUID()}`;
    let stopped = false, composing = false, lastEdit = 0, handle = null, item = null, busy = false, acknowledged = false;
    const requests = new Set();
    const input = event => { if (event.target.matches?.('input,textarea,select,[contenteditable="true"]')) lastEdit = Date.now(); };
    const startComposition = () => { composing = true; lastEdit = Date.now(); };
    const endComposition = () => { composing = false; lastEdit = Date.now(); };
    document.addEventListener('input', input, true); document.addEventListener('compositionstart', startComposition, true); document.addEventListener('compositionend', endComposition, true);
    async function call(path = '', options = {}) {
        const controller = new AbortController(); requests.add(controller);
        const deadline = window.setTimeout(() => controller.abort(), 20000);
        const abort = () => controller.abort(); options.signal?.addEventListener('abort', abort, { once: true });
        if (options.signal?.aborted) controller.abort();
        try { return await apiFetch(base + path, { silent: true, ...options, signal: controller.signal }); }
        catch (error) {
            if (error.status === 401 || error.status === 403) dispose();
            const affected = /^\/(\d+)\/(?:claim|ack)$/.exec(path)?.[1];
            // Ownership changes deliberately return 404. Release this stale
            // layer without acknowledging a request we can no longer manage.
            if (error.status === 404 && affected && String(item?.request_id) === affected) handle?.destroy();
            throw error;
        }
        finally { window.clearTimeout(deadline); options.signal?.removeEventListener('abort', abort); requests.delete(controller); }
    }
    const canOpen = () => !stopped && !document.hidden && !composing && Date.now() - lastEdit > 10000 && !getLayerSystem(document).top()
        && !document.activeElement?.matches('input,textarea,select,[contenteditable="true"]');
    function show(row) {
        item = row; acknowledged = false;
        const body = document.createElement('div'), message = document.createElement('p'), state = document.createElement('p'), controls = document.createElement('div');
        controls.className = 'lq-domain-toolbar'; state.setAttribute('role', 'status');
        message.textContent = `${row.name || '学生'}（${row.student_number || '学号待核对'}）提交了签到申诉。查看后可核对本次及此前课堂记录。`;
        async function act(open) {
            if (busy || stopped) return; busy = true; controls.querySelectorAll('button').forEach(n => n.disabled = true);
            try {
                await call(`/${row.request_id}/ack`, { method: 'POST' });
                if (stopped) return; acknowledged = true;
                const url = `/classroom/${row.class_offering_id}?session_id=${row.session_id}&attendance_id=${row.round_id}&student_id=${row.student_id}&appeal_id=${row.request_id}`;
                await getLayerSystem(document).close(handle, 'acknowledged');
                if (open) navigate(url);
            } catch (error) { state.textContent = error.message || '确认提醒失败，请重试。'; state.setAttribute('role', 'alert'); }
            finally { busy = false; if (!stopped) controls.querySelectorAll('button').forEach(n => n.disabled = false); if (document.hidden) handle?.destroy(); }
        }
        for (const [label, open, variant] of [['查看并处理', true, 'prominent'], ['已知悉，稍后处理', false, 'glass']]) {
            const button = createDomainButton({ label, variant, size: 'md' }); button.addEventListener('click', () => act(open)); controls.append(button);
        }
        const delivery = document.createElement('p'), emailStatus = row.email_delivery?.email_status;
        delivery.textContent = ({ sent: '通知邮件已发送。', queued: '通知邮件等待发送；站内提醒已生效。', failed: '通知邮件发送失败，请检查邮件设置；站内提醒仍有效。', not_configured: '尚未配置通知邮件；站内提醒仍有效。', skipped: '通知邮件已跳过；站内提醒仍有效。' })[emailStatus] || '通知邮件尚未确认入队，请检查邮件设置；站内提醒仍有效。';
        const emailLink = document.createElement('a'); emailLink.href = '/profile?section=email'; emailLink.textContent = '查看邮件设置';
        body.append(message, delivery, emailLink, state, controls);
        handle = openDialog({ title: '待处理签到申诉', body, size: 'md', closeButton: false }, {
            beforeClose: () => acknowledged && !stopped, onClose: () => { handle = item = null; }, onDestroy: () => { handle = item = null; },
        });
    }
    const poll = createAmbientPoll(async signal => {
        if (handle && item) {
            const lease = await call(`/${item.request_id}/claim`, { method: 'POST', body: { owner }, signal });
            if (!lease.claimed && !busy) handle.destroy();
            return;
        }
        if (!canOpen()) return;
        const value = await call('', { signal });
        for (const row of value.items || []) {
            if (!canOpen()) return;
            const claim = await call(`/${row.request_id}/claim`, { method: 'POST', body: { owner }, signal });
            if (claim.claimed && canOpen()) { show(row); break; }
        }
    }, { intervalMs: 20000, active: () => !stopped && !document.hidden && (handle || canOpen()) });
    const visibility = () => { if (document.hidden && handle && !busy) handle.destroy(); };
    document.addEventListener('visibilitychange', visibility);
    const pageHide = event => { if (event.persisted) { requests.forEach(c=>c.abort()); handle?.destroy(); } else dispose(); };
    const pageShow = event => { if(event.persisted&&!stopped) void poll.refresh(); };
    function dispose() {
        if (stopped) return; stopped = true; poll.dispose(); requests.forEach(c => c.abort()); handle?.destroy();
        document.removeEventListener('input', input, true); document.removeEventListener('compositionstart', startComposition, true); document.removeEventListener('compositionend', endComposition, true);
        document.removeEventListener('visibilitychange', visibility);
        window.removeEventListener('pagehide', pageHide); window.removeEventListener('pageshow', pageShow);
    }
    window.addEventListener('pagehide', pageHide); window.addEventListener('pageshow', pageShow); poll.start(1500);
    return dispose;
}

if (document.querySelector('script[data-login-attendance-teacher]') && !window.__loginAttendanceAttention) {
    window.__loginAttendanceAttention = initAttendanceAttention();
}
