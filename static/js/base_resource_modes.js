import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const RESOURCE_CONFIG = {
    class: {
        label: '班级',
        attributesUrl: (id) => `/api/manage/classes/${id}/attributes`,
        fields: [
            { key: 'name', label: '班级名称', type: 'text', required: true },
            { key: 'description', label: '班级简介', type: 'textarea' },
            { key: 'school_name', label: '学校', type: 'text' },
            { key: 'college', label: '学院', type: 'text' },
            { key: 'department', label: '系部', type: 'text' },
            { key: 'major', label: '专业', type: 'text' },
            { key: 'enrollment_year', label: '入学年份', type: 'number' },
            { key: 'expected_graduation_year', label: '预计毕业年份', type: 'number' },
            { key: 'program_duration_years', label: '学制年限', type: 'number' },
            { key: 'scope_level', label: '可见范围', type: 'select', options: [['class', '本班'], ['department', '本系部'], ['school', '本学校'], ['private', '私有']] },
        ],
    },
    course: {
        label: '课程',
        attributesUrl: (id) => `/api/manage/courses/${id}/attributes`,
        fields: [
            { key: 'name', label: '课程名称', type: 'text', required: true },
            { key: 'description', label: '课程简介', type: 'textarea' },
            { key: 'sect_name', label: '简称', type: 'text' },
            { key: 'credits', label: '学分', type: 'number', step: '0.5' },
            { key: 'total_hours', label: '总学时', type: 'number' },
            { key: 'school_name', label: '学校', type: 'text' },
            { key: 'college', label: '学院', type: 'text' },
            { key: 'department', label: '系部', type: 'text' },
            { key: 'scope_level', label: '可见范围', type: 'select', options: [['private', '私有'], ['department', '本系部'], ['school', '本学校']] },
        ],
    },
    textbook: {
        label: '教材',
        attributesUrl: (id) => `/api/manage/textbooks/${id}/attributes`,
        fields: [
            { key: 'title', label: '教材名称', type: 'text', required: true },
            { key: 'authors', label: '作者', type: 'list' },
            { key: 'publisher', label: '出版社', type: 'text' },
            { key: 'publication_date', label: '出版日期', type: 'text' },
            { key: 'tags', label: '标签', type: 'list' },
            { key: 'scope_level', label: '可见范围', type: 'select', options: [['private', '私有'], ['department', '本系部'], ['school', '本学校']] },
        ],
    },
    exam_paper: {
        label: '试卷',
        attributesUrl: (id) => `/api/exam-papers/${encodeURIComponent(id)}/attributes`,
        fields: [
            { key: 'tags', label: '标签', type: 'list' },
            { key: 'status', label: '状态', type: 'select', options: [['draft', '草稿'], ['ready', '就绪'], ['published', '已发布'], ['closed', '已截止'], ['archived', '已归档']] },
            { key: 'scope_level', label: '可见范围', type: 'select', options: [['private', '私有'], ['department', '本系部'], ['school', '本学校']] },
        ],
    },
    material: {
        label: '材料',
        attributesUrl: (id) => `/api/materials/${id}/attributes`,
        fields: [
            { key: 'name', label: '材料名称', type: 'text', required: true },
            { key: 'scope_level', label: '可见范围', type: 'select', options: [['private', '私有'], ['department', '本系部'], ['school', '本学校']] },
        ],
    },
};

let modal = null;
let styleAdded = false;
let activeState = null;

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function normalizeList(value) {
    if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean);
    return String(value || '').split(/[,\n，、]/).map((item) => item.trim()).filter(Boolean);
}

function addStyleOnce() {
    if (styleAdded) return;
    styleAdded = true;
    const style = document.createElement('style');
    style.textContent = `
        .resource-mode-backdrop {
            position: fixed;
            inset: 0;
            z-index: 1600;
            display: none;
            align-items: center;
            justify-content: center;
            padding: 18px;
            background: rgba(15, 23, 42, 0.48);
            backdrop-filter: blur(10px);
        }
        .resource-mode-backdrop.is-open { display: flex; }
        .resource-mode-dialog {
            width: min(760px, 100%);
            max-height: min(88vh, 820px);
            overflow: hidden;
            border-radius: 14px;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: #fff;
            box-shadow: 0 30px 80px -38px rgba(15, 23, 42, 0.72);
        }
        .resource-mode-head,
        .resource-mode-footer {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 16px 18px;
            border-bottom: 1px solid rgba(226, 232, 240, 0.9);
        }
        .resource-mode-footer {
            border-top: 1px solid rgba(226, 232, 240, 0.9);
            border-bottom: 0;
            justify-content: flex-end;
        }
        .resource-mode-title { margin: 0; color: #0f172a; font-size: 1.05rem; }
        .resource-mode-subtitle { margin: 4px 0 0; color: #64748b; font-size: 0.84rem; }
        .resource-mode-close {
            width: 34px;
            height: 34px;
            border: 1px solid rgba(148, 163, 184, 0.24);
            border-radius: 999px;
            background: rgba(248, 250, 252, 0.92);
            color: #64748b;
            cursor: pointer;
        }
        .resource-mode-body {
            display: grid;
            gap: 14px;
            max-height: calc(min(88vh, 820px) - 136px);
            overflow: auto;
            padding: 16px 18px;
            background: rgba(248, 250, 252, 0.78);
        }
        .resource-mode-tabs {
            display: inline-flex;
            width: fit-content;
            gap: 4px;
            padding: 4px;
            border-radius: 10px;
            border: 1px solid rgba(148, 163, 184, 0.22);
            background: #fff;
        }
        .resource-mode-tab {
            min-height: 32px;
            padding: 0 12px;
            border: 0;
            border-radius: 8px;
            background: transparent;
            color: #64748b;
            font-weight: 800;
        }
        .resource-mode-tab.is-active { background: #0f766e; color: #fff; }
        .resource-mode-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }
        .resource-mode-field { display: grid; gap: 6px; }
        .resource-mode-field.is-wide { grid-column: 1 / -1; }
        .resource-mode-field label { color: #475569; font-size: 0.78rem; font-weight: 800; }
        .resource-mode-field :is(input, textarea, select) {
            width: 100%;
            border: 1px solid rgba(148, 163, 184, 0.28);
            border-radius: 8px;
            padding: 9px 10px;
            background: #fff;
            color: #0f172a;
            font: inherit;
        }
        .resource-mode-field textarea { min-height: 88px; resize: vertical; }
        .resource-mode-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(136px, 1fr));
            gap: 8px;
        }
        .resource-mode-stat {
            padding: 10px;
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 8px;
            background: #fff;
        }
        .resource-mode-stat span { display: block; color: #64748b; font-size: 0.72rem; font-weight: 800; }
        .resource-mode-stat strong { display: block; margin-top: 4px; color: #0f172a; font-size: 0.98rem; }
        @media (max-width: 680px) {
            .resource-mode-grid { grid-template-columns: 1fr; }
        }
    `;
    document.head.appendChild(style);
}

function ensureModal() {
    if (modal) return modal;
    addStyleOnce();
    modal = document.createElement('div');
    modal.className = 'resource-mode-backdrop';
    modal.setAttribute('aria-hidden', 'true');
    modal.innerHTML = `
        <section class="resource-mode-dialog" role="dialog" aria-modal="true" aria-labelledby="resourceModeTitle">
            <div class="resource-mode-head">
                <div>
                    <h3 class="resource-mode-title" id="resourceModeTitle">资源属性</h3>
                    <p class="resource-mode-subtitle" id="resourceModeSubtitle"></p>
                </div>
                <button type="button" class="resource-mode-close" data-resource-mode-close aria-label="关闭">×</button>
            </div>
            <form id="resourceModeForm">
                <div class="resource-mode-body">
                    <div class="resource-mode-tabs" aria-label="资源编辑模式">
                        <button type="button" class="resource-mode-tab is-active">属性</button>
                        <button type="button" class="resource-mode-tab" disabled>内容</button>
                    </div>
                    <div class="resource-mode-grid" id="resourceModeFields"></div>
                    <div class="resource-mode-stats" id="resourceModeStats"></div>
                </div>
                <div class="resource-mode-footer">
                    <button type="button" class="btn btn-outline" data-resource-mode-close>关闭</button>
                    <button type="submit" class="btn btn-primary" id="resourceModeSaveBtn">保存属性</button>
                </div>
            </form>
        </section>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (event) => {
        if (event.target === modal || event.target.closest('[data-resource-mode-close]')) {
            closeModal();
        }
    });
    modal.querySelector('#resourceModeForm')?.addEventListener('submit', saveAttributes);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && modal?.classList.contains('is-open')) closeModal();
    });
    return modal;
}

function closeModal() {
    modal?.classList.remove('is-open');
    modal?.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    activeState = null;
}

function openModal() {
    ensureModal();
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
}

function renderField(field, attributes, canEdit) {
    const rawValue = attributes[field.key];
    const disabled = canEdit ? '' : ' disabled';
    const required = field.required ? ' required' : '';
    const fieldClass = field.type === 'textarea' ? 'resource-mode-field is-wide' : 'resource-mode-field';
    if (field.type === 'textarea') {
        return `<div class="${fieldClass}"><label>${escapeHtml(field.label)}</label><textarea name="${field.key}"${disabled}${required}>${escapeHtml(rawValue || '')}</textarea></div>`;
    }
    if (field.type === 'select') {
        const value = String(rawValue || '');
        const options = (field.options || []).map(([optionValue, label]) => `
            <option value="${escapeHtml(optionValue)}"${value === optionValue ? ' selected' : ''}>${escapeHtml(label)}</option>
        `).join('');
        return `<div class="${fieldClass}"><label>${escapeHtml(field.label)}</label><select name="${field.key}"${disabled}${required}>${options}</select></div>`;
    }
    if (field.type === 'list') {
        return `<div class="${fieldClass}"><label>${escapeHtml(field.label)}</label><input name="${field.key}" type="text" value="${escapeHtml(normalizeList(rawValue).join('、'))}"${disabled}${required}></div>`;
    }
    return `<div class="${fieldClass}"><label>${escapeHtml(field.label)}</label><input name="${field.key}" type="${field.type || 'text'}" value="${escapeHtml(rawValue ?? '')}" step="${field.step || '1'}"${disabled}${required}></div>`;
}

function renderStats(stats) {
    const entries = Object.entries(stats || {});
    if (!entries.length) return '';
    return entries.map(([key, value]) => `
        <div class="resource-mode-stat">
            <span>${escapeHtml(key.replaceAll('_', ' '))}</span>
            <strong>${escapeHtml(value ?? 0)}</strong>
        </div>
    `).join('');
}

async function openAttributes(button) {
    const resourceType = button.dataset.resourceType;
    const resourceId = button.dataset.resourceId;
    const config = RESOURCE_CONFIG[resourceType];
    if (!config || !resourceId) return;
    button.disabled = true;
    try {
        const payload = await apiFetch(config.attributesUrl(resourceId), { silent: true });
        const attributes = payload.attributes || {};
        const permissions = attributes.permissions || {};
        const canEdit = Boolean(permissions.can_edit_attributes);
        activeState = { config, attributes, resourceId };
        ensureModal();
        modal.querySelector('#resourceModeTitle').textContent = `${config.label}属性`;
        modal.querySelector('#resourceModeSubtitle').textContent = canEdit
            ? '仅保存归属、范围、状态和统计相关字段，不改写资源正文。'
            : '当前账号可查看属性，但不能保存属性修改。';
        modal.querySelector('#resourceModeFields').innerHTML = config.fields
            .map((field) => renderField(field, attributes, canEdit))
            .join('');
        modal.querySelector('#resourceModeStats').innerHTML = renderStats(attributes.stats || {});
        modal.querySelector('#resourceModeSaveBtn').disabled = !canEdit;
        openModal();
    } catch (error) {
        showMessage(error.message || '属性加载失败', 'error');
    } finally {
        button.disabled = false;
    }
}

function collectPayload(form) {
    const output = {};
    for (const field of activeState.config.fields) {
        const node = form.elements[field.key];
        if (!node) continue;
        if (field.type === 'number') {
            output[field.key] = node.value === '' ? null : Number(node.value);
        } else if (field.type === 'list') {
            output[field.key] = normalizeList(node.value);
        } else {
            output[field.key] = String(node.value || '').trim();
        }
    }
    return output;
}

async function saveAttributes(event) {
    event.preventDefault();
    if (!activeState) return;
    const saveBtn = modal.querySelector('#resourceModeSaveBtn');
    const originalText = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中...';
    try {
        const result = await apiFetch(activeState.config.attributesUrl(activeState.resourceId), {
            method: 'PATCH',
            body: collectPayload(event.currentTarget),
            silent: true,
        });
        showMessage(result.message || '属性已保存', 'success');
        window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
        showMessage(error.message || '属性保存失败', 'error');
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = originalText;
    }
}

function handleResourceAttributeClick(event) {
    const button = event.target.closest('[data-resource-attributes]');
    if (!button) return;
    event.preventDefault();
    openAttributes(button);
}

document.addEventListener('click', handleResourceAttributeClick, true);
window.LanShareBaseResourceModesReady = true;
