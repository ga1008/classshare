import { apiFetch } from './api.js';
import { createComponent } from './lq/components.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createForm } from './lq/forms.js';
import { ATTENDANCE_STATES, RECORDED_ATTENDANCE_STATES } from './login_attendance_states.js';

export const stateOptions = ATTENDANCE_STATES.map(([value, label]) => ({ value, label }));
const labels = Object.fromEntries([...RECORDED_ATTENDANCE_STATES, ['PENDING', '待确认'], ['UNKNOWN', '待确认'], ['EXEMPT', '免考勤']]);
export const statusText = status => labels[status] || '暂无记录';
export const sourceText = source => ({ local: '本地', smart_synced: '智慧课堂', confirmed_pdf: '签到原件', mixed: '多来源' }[source] || '');
export const studentLabel = row => [row.name, row.student_number].filter(Boolean).join(' · ');
export const lessonLabel = row => [row.section_text, row.title || '课程'].filter(Boolean).join(' · ');
export function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}
export function action(label, fn, variant = 'glass') {
    const button = createDomainButton({ label, variant, size: 'sm' });
    button.addEventListener('click', fn);
    return button;
}
export function badge(label, tone = 'neutral') {
    return createComponent('chip', { label, kind: 'status', tone });
}
export function field(kind, label, props = {}) {
    const wrapper = createForm(kind, { id: `attendance-field-${crypto.randomUUID()}`, label, ...props });
    return { wrapper, input: wrapper.querySelector('input,textarea,select') };
}
export function feedback(node, message, alert = false) {
    node.setAttribute('role', alert ? 'alert' : 'status');
    node.textContent = message;
}
/** A closed dialog cancels its requests and cannot start another lesson. */
export function calendarRequests() {
    const pending = new Set();
    let closed = false;
    return {
        get closed() { return closed; },
        close() { closed = true; pending.forEach(c => c.abort()); },
        async read(url, options = {}, timeout = 25000) {
            if (closed) throw new Error('窗口已关闭');
            const controller = new AbortController();
            pending.add(controller);
            const timer = window.setTimeout(() => controller.abort(), timeout);
            try { return await apiFetch(url, { ...options, silent: true, signal: controller.signal }); }
            finally { window.clearTimeout(timer); pending.delete(controller); }
        }
    };
}
