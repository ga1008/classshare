/** One bounded visibility request for the classroom; no roster is loaded on entry. */
import { apiFetch } from './api.js';
import { createAmbientPoll } from './ambient_poll.js';

let value = { visible: false, session_ids: [], local_session_ids: [] };
let pending = null, lastRead = 0, initialized = false, poll = null;
const offering = () => Number(window.APP_CONFIG?.classOfferingId);
const role = () => window.APP_CONFIG?.userInfo?.role;

export function attendanceEntryState(sessionId) {
    const id = Number(sessionId);
    return { visible: role() === 'teacher' || value.session_ids.includes(id), local: role() === 'teacher' || value.local_session_ids.includes(id) };
}

export async function refreshAttendanceVisibility({ force = false, signal } = {}) {
    if (!offering() || !['teacher', 'student'].includes(role())) return value;
    if (role() === 'teacher') return { visible: true, session_ids: [], local_session_ids: [] };
    if (pending) return pending;
    if (!force && Date.now() - lastRead < 15000) return value;
    const controller = new AbortController(), deadline = window.setTimeout(() => controller.abort(), 15000);
    const abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) controller.abort();
    pending = apiFetch(`/api/classrooms/${offering()}/login-attendance/dashboard/visibility`, { silent: true, signal: controller.signal })
        .then(result => {
            value = { visible: result.visible === true, session_ids: (result.session_ids || []).map(Number), local_session_ids: (result.local_session_ids || []).map(Number) };
            lastRead = Date.now();
            const entry = document.getElementById('cw-attendance-dashboard');
            if (entry) entry.hidden = !value.visible;
            document.dispatchEvent(new CustomEvent('classroom:attendance-visibility', { detail: value }));
            return value;
        }).finally(() => { window.clearTimeout(deadline); signal?.removeEventListener('abort', abort); pending = null; });
    return pending;
}

export async function openAttendanceDashboardFromEntry({ trigger, sessionId, studentId, appealId } = {}) {
    if (!offering() || !trigger?.isConnected || trigger.disabled) return;
    trigger.disabled = true;
    try {
        const mod = await import('./attendance_dashboard.js');
        if (trigger.isConnected) return mod.openAttendanceDashboard({ classOfferingId: offering(), userRole: role(), trigger, sessionId, studentId, appealId });
    } finally { if (trigger.isConnected) trigger.disabled = false; }
}

export function initAttendanceDashboardEntry() {
    if (initialized) return;
    initialized = true;
    const entry = document.getElementById('cw-attendance-dashboard');
    if (!entry) return;
    entry.hidden = role() !== 'teacher';
    entry.addEventListener('click', async () => {
        try { await openAttendanceDashboardFromEntry({ trigger: entry }); }
        catch (error) { const { showToast } = await import('./ui.js'); showToast(error.message || '签到统计暂时无法读取，请重试。', 'error'); }
    });
    const refresh = () => { void refreshAttendanceVisibility({ force: true }).catch(() => {}); };
    document.addEventListener('classroom:attendance-changed', refresh);
    const link = new URLSearchParams(window.location.search);
    if (link.get('attendance_dashboard') === '1') {
        const integer = name => /^[1-9]\d*$/.test(link.get(name) || '') && Number.isSafeInteger(Number(link.get(name))) ? Number(link.get(name)) : null;
        void openAttendanceDashboardFromEntry({ trigger: entry, sessionId: integer('session_id'), studentId: integer('student_id'), appealId: integer('smart_appeal_id') || integer('appeal_id') }).catch(() => {});
    }
    if (role() !== 'student') return;
    refresh();
    const start = () => { poll = createAmbientPoll(signal => refreshAttendanceVisibility({ force: true, signal }), { intervalMs: 60000, timeoutMs: 15000, active: () => !document.hidden }); poll.start(60000); };
    start();
    window.addEventListener('pagehide', () => poll?.dispose());
    window.addEventListener('pageshow', event => { if (event.persisted) { refresh(); start(); } });
}
