import { apiFetch } from './api.js';
import { showToast, escapeHtml } from './ui.js';
import {
    closePendingPreviewWindow,
    isPreviewLinkBusy,
    movePendingPreviewWindow,
    openPendingPreviewWindow,
    setPreviewLinkBusy,
    startProcessMaterialExportDownload,
} from './process_material_editor_preview.js';
import { openProcessMaterialConfirm } from './process_material_modal.js';

const boot = (() => {
    try { return JSON.parse(document.getElementById('lp-editor-boot').textContent); }
    catch (_) { return { id: '', cover: {}, sessions: [] }; }
})();

const planId = boot.id;
const state = {
    cover: boot.cover || {},
    sessions: Array.isArray(boot.sessions) ? boot.sessions : [],
    dirty: false,
    saving: false,
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

function setSaveState(kind, text) {
    const el = document.getElementById('lp-save-state');
    if (!el) return;
    el.classList.remove('is-clean', 'is-dirty', 'is-saving');
    el.classList.add(kind);
    el.textContent = text;
}

function markDirty() {
    state.dirty = true;
    setSaveState('is-dirty', '未保存');
}

function markClean() {
    state.dirty = false;
    setSaveState('is-clean', '已保存');
}

function restoreSaveState() {
    if (state.dirty) {
        setSaveState('is-dirty', '未保存');
    } else {
        setSaveState('is-clean', '已保存');
    }
}


function renderCover() {
    const grid = document.getElementById('lp-cover-grid');
    grid.innerHTML = COVER_FIELDS.map(([key, label]) => `
        <label class="lp-field">${escapeHtml(label)}
            <input data-cover="${key}" value="${escapeHtml(state.cover[key] || '')}">
        </label>`).join('');
    grid.querySelectorAll('[data-cover]').forEach((el) => {
        el.addEventListener('input', () => {
            if (state.saving) return;
            state.cover[el.dataset.cover] = el.value;
            markDirty();
        });
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
            if (state.saving) return;
            const i = Number(el.dataset.s);
            const key = el.dataset.k;
            if (key === '__schedule_text') {
                state.sessions[i].schedule = { ...(state.sessions[i].schedule || {}), text: el.value };
            } else {
                state.sessions[i][key] = el.value;
            }
            markDirty();
        });
    });
    wrap.querySelectorAll('[data-remove]').forEach((btn) => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            if (state.saving) return;
            const i = Number(btn.dataset.remove);
            const confirmed = await openProcessMaterialConfirm({
                title: '删除课次',
                message: `删除第 ${i + 1} 次课？`,
                detail: '该课次的主要内容、教学方法、安排和作业会从当前教案中移除。',
                confirmText: '删除',
                tone: 'danger',
            });
            if (!confirmed) return;
            state.sessions.splice(i, 1);
            markDirty();
            renderSessions();
        });
    });
}

function renderImportDetails() {
    const preview = boot.import_preview;
    if (!preview || !Object.keys(preview).length) return;
    const wrap = document.getElementById('lp-import');
    const details = document.getElementById('lp-import-details');
    if (!wrap || !details) return;
    details.hidden = false;
    details.open = Boolean((preview.warnings || []).length);
    const warnings = (preview.warnings || []).map((w) => `<li class="is-warn">${escapeHtml(w)}</li>`).join('');
    const sourceFiles = (preview.source_files || []).map(escapeHtml).join('、') || '—';
    const sessionCount = Number(preview.session_count ?? state.sessions.length ?? 0);
    const cover = preview.cover || {};
    const courseName = cover.course_name || state.cover.course_name || '';
    wrap.innerHTML = `
        <div class="ap-import__block"><strong>来源文件</strong><div>${sourceFiles}</div></div>
        <div class="ap-import__block"><strong>解析结果</strong><div>${courseName ? `${escapeHtml(courseName)} · ` : ''}${sessionCount} 次课</div></div>
        ${warnings ? `<div class="ap-import__block"><strong>提示</strong><ul class="ap-import__list">${warnings}</ul></div>` : ''}`;
}

function addSession() {
    if (state.saving) return;
    state.sessions.push({ index: state.sessions.length + 1, schedule: {}, chapter: '', process: '' });
    markDirty();
    renderSessions();
    const items = document.querySelectorAll('.lp-editor__session');
    if (items.length) items[items.length - 1].setAttribute('open', '');
}

function refreshPreview() {
    const frame = document.getElementById('lp-preview-frame');
    if (frame) frame.src = `/lesson-plan/${planId}/preview?t=${Date.now()}`;
}

function setActionButtons({ busy = false } = {}) {
    const buttons = [
        document.getElementById('lp-add-session'),
        document.getElementById('lp-save'),
        document.getElementById('lp-refresh-preview'),
        document.getElementById('lp-export-word'),
        document.getElementById('lp-export-pdf'),
        document.getElementById('lp-export-png'),
    ].filter(Boolean);
    buttons.forEach((button) => {
        button.disabled = busy;
        button.classList.toggle('lp-btn--disabled', busy);
        if (busy) {
            button.setAttribute('aria-disabled', 'true');
        } else {
            button.removeAttribute('aria-disabled');
        }
    });
}

function setEditorBusy(busy) {
    state.saving = Boolean(busy);
    const form = document.getElementById('lp-editor-form');
    form?.classList.toggle('is-saving', state.saving);
    form?.querySelectorAll('input, select, textarea, button').forEach((control) => {
        control.disabled = state.saving;
    });
    setPreviewLinkBusy(document.getElementById('lp-open-preview'), state.saving);
    setActionButtons({ busy: state.saving });
}

async function persistContent() {
    return await apiFetch(`/api/lesson-plans/${planId}/content`, {
        method: 'PUT',
        body: { cover: state.cover, sessions: state.sessions },
    });
}

async function save({ refresh = true } = {}) {
    if (state.saving) return;
    const btn = document.getElementById('lp-save');
    const oldText = btn?.textContent || '保存';
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    if (btn) btn.textContent = '保存中…';
    try {
        await persistContent();
        markClean();
        showToast('已保存', 'success');
        if (refresh) refreshPreview();
    } catch (err) {
        showToast(err.message || '保存失败', 'error');
    } finally {
        if (btn) btn.textContent = oldText;
        setEditorBusy(false);
        restoreSaveState();
    }
}

async function saveAndRefreshPreview() {
    if (state.saving) return;
    const btn = document.getElementById('lp-refresh-preview');
    const oldText = btn?.textContent || '保存并刷新预览';
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    if (btn) btn.textContent = '刷新中…';
    try {
        await persistContent();
        markClean();
        refreshPreview();
        showToast('已保存并刷新预览', 'success');
    } catch (err) {
        showToast(err.message || '保存失败，无法刷新预览', 'error');
    } finally {
        if (btn) btn.textContent = oldText;
        setEditorBusy(false);
        restoreSaveState();
    }
}

async function exportLessonPlan(format = 'docx') {
    if (state.saving) return;
    const normalized = ['docx', 'pdf', 'png'].includes(format) ? format : 'docx';
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    try {
        await persistContent();
        markClean();
        setEditorBusy(false);
        restoreSaveState();
        startProcessMaterialExportDownload(
            `/api/lesson-plans/${planId}/export?fmt=${normalized}`,
            showToast,
            normalized === 'pdf' ? 'PDF' : (normalized === 'png' ? 'PNG' : 'Word'),
        );
    } catch (err) {
        showToast(err.message || '保存失败，无法导出', 'error');
        setEditorBusy(false);
        restoreSaveState();
    }
}

async function openSavedPreview(event) {
    const link = event.currentTarget;
    if (state.saving || isPreviewLinkBusy(link)) {
        event.preventDefault();
        return;
    }
    if (!state.dirty) return;
    event.preventDefault();
    const previewWindow = openPendingPreviewWindow(showToast);
    if (!previewWindow) return;
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    try {
        await persistContent();
        markClean();
        refreshPreview();
        movePendingPreviewWindow(previewWindow, link.href);
        showToast('已保存并打开预览', 'success');
    } catch (err) {
        closePendingPreviewWindow(previewWindow);
        showToast(err.message || '保存失败，无法打开预览', 'error');
    } finally {
        setEditorBusy(false);
        restoreSaveState();
    }
}

function init() {
    renderCover();
    renderSessions();
    renderImportDetails();
    document.getElementById('lp-save').addEventListener('click', () => save());
    document.getElementById('lp-add-session').addEventListener('click', addSession);
    document.getElementById('lp-open-preview').addEventListener('click', openSavedPreview);
    document.getElementById('lp-refresh-preview').addEventListener('click', saveAndRefreshPreview);
    document.getElementById('lp-export-word').addEventListener('click', () => exportLessonPlan('docx'));
    document.getElementById('lp-export-pdf').addEventListener('click', () => exportLessonPlan('pdf'));
    document.getElementById('lp-export-png').addEventListener('click', () => exportLessonPlan('png'));
    window.addEventListener('beforeunload', (event) => {
        if (!state.dirty) return;
        event.preventDefault();
        event.returnValue = '';
    });
}

init();
