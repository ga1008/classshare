import { apiFetch } from './api.js';
import { showToast, escapeHtml } from './ui.js';

const boot = (() => {
    try { return JSON.parse(document.getElementById('lp-editor-boot').textContent); }
    catch (_) { return { id: '', cover: {}, sessions: [] }; }
})();

const planId = boot.id;
const state = {
    cover: boot.cover || {},
    sessions: Array.isArray(boot.sessions) ? boot.sessions : [],
};

const COVER_FIELDS = [
    ['course_name', '课程名称'], ['course_category', '课程类别'], ['credits', '学分'],
    ['total_hours', '学时'], ['teacher_name', '授课教师'], ['teaching_unit', '教学单位'],
    ['class_name', '授课班级'], ['textbook', '使用教材'], ['publisher', '出版社'],
    ['semester_label', '学期'], ['school_name', '学校'],
];

const SESSION_FIELDS = [
    ['chapter', '授课章节', 'input'],
    ['objectives', '教学目的和要求', 'textarea'],
    ['key_points', '教学重点', 'textarea'],
    ['difficulties', '教学难点', 'textarea'],
    ['methods', '教学方法', 'input'],
    ['means', '教学手段', 'input'],
    ['process', '教学内容及过程（Markdown，支持表格）', 'textarea-lg'],
    ['side_notes', '旁批', 'textarea'],
    ['post_notes', '教学后记', 'textarea'],
];

function renderCover() {
    const grid = document.getElementById('lp-cover-grid');
    grid.innerHTML = COVER_FIELDS.map(([key, label]) => `
        <label class="lp-field">${escapeHtml(label)}
            <input data-cover="${key}" value="${escapeHtml(state.cover[key] || '')}">
        </label>`).join('');
    grid.querySelectorAll('[data-cover]').forEach((el) => {
        el.addEventListener('input', () => { state.cover[el.dataset.cover] = el.value; });
    });
}

function scheduleText(session) {
    const s = session.schedule || {};
    const weekdayMap = { 1: '星期一', 2: '星期二', 3: '星期三', 4: '星期四', 5: '星期五', 6: '星期六', 7: '星期日' };
    const weekday = s.weekday ? (weekdayMap[Number(s.weekday)] || s.weekday) : '';
    return s.text || [s.date, s.week_index ? `第${s.week_index}周` : '', weekday, s.sections ? `第${s.sections}节` : '']
        .filter(Boolean).join(' ');
}

function renderSessions() {
    const wrap = document.getElementById('lp-sessions');
    if (!state.sessions.length) {
        wrap.innerHTML = `<p class="lp-editor__empty">还没有课次，点击右上角「+ 添加课次」。</p>`;
        return;
    }
    wrap.innerHTML = state.sessions.map((session, idx) => {
        const fields = SESSION_FIELDS.map(([key, label, kind]) => {
            const value = escapeHtml(session[key] || '');
            if (kind === 'input') {
                return `<label class="lp-field">${label}<input data-s="${idx}" data-k="${key}" value="${value}"></label>`;
            }
            const rows = kind === 'textarea-lg' ? 12 : 3;
            return `<label class="lp-field lp-field--full">${label}<textarea data-s="${idx}" data-k="${key}" rows="${rows}">${value}</textarea></label>`;
        }).join('');
        return `
        <details class="lp-editor__session" ${idx === 0 ? 'open' : ''}>
            <summary>
                <span>第 ${idx + 1} 次课</span>
                <small>${escapeHtml(scheduleText(session) || '未排课')}</small>
                <button type="button" class="lp-link lp-link--danger" data-remove="${idx}">删除本次课</button>
            </summary>
            <label class="lp-field lp-field--full">授课时间
                <input data-s="${idx}" data-k="__schedule_text" value="${escapeHtml(scheduleText(session))}">
            </label>
            ${fields}
        </details>`;
    }).join('');

    wrap.querySelectorAll('[data-k]').forEach((el) => {
        el.addEventListener('input', () => {
            const i = Number(el.dataset.s);
            const key = el.dataset.k;
            if (key === '__schedule_text') {
                state.sessions[i].schedule = { ...(state.sessions[i].schedule || {}), text: el.value };
            } else {
                state.sessions[i][key] = el.value;
            }
        });
    });
    wrap.querySelectorAll('[data-remove]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const i = Number(btn.dataset.remove);
            if (!confirm(`删除第 ${i + 1} 次课？`)) return;
            state.sessions.splice(i, 1);
            renderSessions();
        });
    });
}

function addSession() {
    state.sessions.push({ index: state.sessions.length + 1, schedule: {}, chapter: '', process: '' });
    renderSessions();
    const items = document.querySelectorAll('.lp-editor__session');
    if (items.length) items[items.length - 1].setAttribute('open', '');
}

function refreshPreview() {
    const frame = document.getElementById('lp-preview-frame');
    if (frame) frame.src = `/lesson-plan/${planId}/preview?t=${Date.now()}`;
}

async function save() {
    const btn = document.getElementById('lp-save');
    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
        await apiFetch(`/api/lesson-plans/${planId}/content`, {
            method: 'PUT',
            body: { cover: state.cover, sessions: state.sessions },
        });
        showToast('已保存', 'success');
        refreshPreview();
    } catch (err) {
        showToast(err.message || '保存失败', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '保存';
    }
}

function init() {
    renderCover();
    renderSessions();
    document.getElementById('lp-save').addEventListener('click', save);
    document.getElementById('lp-add-session').addEventListener('click', addSession);
    document.getElementById('lp-refresh-preview').addEventListener('click', refreshPreview);
}

init();
