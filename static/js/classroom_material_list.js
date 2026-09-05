/**
 * 课次 / 课程首页"材料列表"浮窗。
 *
 * 课堂卡片的"材料入口"按钮点开后，以宽卡片列表展示该课次（或首页）绑定的全部
 * 学习材料：大标题为材料名称，下附一行由快速版 AI 生成的一句话简介；点击卡片任
 * 意位置在新标签打开材料（Markdown 阅读器或 HTML 渲染页）。列表只读，不触发简介生成。教师把鼠标移到卡片上
 * 时，右上角出现红色叉号可解绑，并有二次确认浮窗以防误点。
 *
 * 模块自包含：按需创建自己的弹窗 DOM，数据通过 REST 接口拉取，不依赖时间轴内部
 * 状态，因此课次卡片与首页卡片都能直接复用。
 */
import { apiFetch } from './api.js';
import { escapeHtml, showToast } from './ui.js';
import { ownClassroomMaterialFocus } from './classroom_material_focus.js';

const state = {
    classOfferingId: 0,
    sessionId: 0,
    isHome: false,
    isTeacher: false,
    canManage: false,
    onChanged: null,
    materials: [],
    loading: false,
    pendingRemoval: null,
};

let listBackdrop = null;
let confirmBackdrop = null;
let releaseListFocus = null;
let releaseConfirmFocus = null;

function buildOpenUrl(material) {
    const raw = String(material?.open_url || '').trim();
    if (!raw) return '';
    try {
        const url = new URL(raw, window.location.origin);
        if (state.classOfferingId) {
            url.searchParams.set('class_offering_id', String(state.classOfferingId));
        }
        if (!state.isHome && state.sessionId) {
            url.searchParams.set('session_id', String(state.sessionId));
        }
        return url.pathname + url.search + url.hash;
    } catch {
        return raw;
    }
}

function ensureDom() {
    if (listBackdrop) return;

    listBackdrop = document.createElement('div');
    listBackdrop.className = 'ls-mat-popup';
    listBackdrop.hidden = true;
    listBackdrop.setAttribute('aria-hidden', 'true');
    listBackdrop.innerHTML = `
        <div class="ls-mat-popup__dialog" role="dialog" aria-modal="true" aria-labelledby="lsMatPopupTitle">
            <div class="ls-mat-popup__header">
                <div>
                    <span class="ls-mat-popup__kicker">学习材料</span>
                    <h3 class="ls-mat-popup__title" id="lsMatPopupTitle">学习材料</h3>
                    <p class="ls-mat-popup__subtitle" id="lsMatPopupSubtitle">点击任意卡片进入材料</p>
                </div>
                <button type="button" class="ls-mat-popup__close" data-close-mat-popup aria-label="关闭">&times;</button>
            </div>
            <div class="ls-mat-popup__list" id="lsMatPopupList"></div>
        </div>
    `;
    document.body.appendChild(listBackdrop);

    listBackdrop.addEventListener('click', (event) => {
        if (event.target === listBackdrop || event.target.closest('[data-close-mat-popup]')) {
            closeListPopup();
        }
    });

    confirmBackdrop = document.createElement('div');
    confirmBackdrop.className = 'ls-mat-confirm';
    confirmBackdrop.hidden = true;
    confirmBackdrop.setAttribute('aria-hidden', 'true');
    confirmBackdrop.innerHTML = `
        <div class="ls-mat-confirm__dialog" role="alertdialog" aria-modal="true" aria-labelledby="lsMatConfirmTitle">
            <h4 class="ls-mat-confirm__title" id="lsMatConfirmTitle">确认解绑材料？</h4>
            <p class="ls-mat-confirm__body" id="lsMatConfirmBody"></p>
            <div class="ls-mat-confirm__actions">
                <button type="button" class="btn btn-ghost" data-cancel-removal>取消</button>
                <button type="button" class="btn btn-danger" data-confirm-removal>确认解绑</button>
            </div>
        </div>
    `;
    document.body.appendChild(confirmBackdrop);

    confirmBackdrop.addEventListener('click', (event) => {
        if (event.target === confirmBackdrop || event.target.closest('[data-cancel-removal]')) {
            closeConfirm();
        }
    });
    confirmBackdrop.querySelector('[data-confirm-removal]')?.addEventListener('click', () => {
        confirmRemoval().catch((error) => showToast(error.message || '解绑失败', 'error'));
    });

}

function renderList() {
    const listEl = document.getElementById('lsMatPopupList');
    if (!listEl) return;

    if (state.loading) {
        listEl.innerHTML = '<div class="ls-mat-empty">正在加载材料列表…</div>';
        return;
    }
    if (!state.materials.length) {
        listEl.innerHTML = state.isTeacher
            ? '<div class="ls-mat-empty">这里还没有绑定材料。关闭后可在“管理课次”中添加 Markdown 或 HTML。</div>'
            : '<div class="ls-mat-empty">教师还没有为这里绑定学习材料。</div>';
        return;
    }

    listEl.innerHTML = state.materials.map((material) => {
        const blurb = String(material.ai_blurb || '').trim()
            || material.material_path || '点击进入材料';
        const renderBadge = material.is_renderable
            ? '<span class="ls-mat-card__badge">可渲染</span>'
            : '';
        const removeBtn = state.canManage
            ? `<button type="button" class="ls-mat-card__del" data-remove-material="${material.material_id}" title="解绑该材料" aria-label="解绑该材料">
                   <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
               </button>`
            : '';
        return `
            <div class="ls-mat-card" data-open-material="${material.material_id}" role="button" tabindex="0">
                <div class="ls-mat-card__body">
                    <div class="ls-mat-card__head">
                        <strong class="ls-mat-card__title">${escapeHtml(material.name || '未命名材料')}</strong>
                        <span class="ls-mat-pill">${escapeHtml(material.type_label || '文档')}</span>
                        ${renderBadge}
                    </div>
                    <span class="ls-mat-card__blurb">${escapeHtml(blurb)}</span>
                </div>
                <svg class="ls-mat-card__go" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
                ${removeBtn}
            </div>
        `;
    }).join('');
}

function getMaterial(materialId) {
    return state.materials.find((item) => Number(item.material_id) === Number(materialId)) || null;
}

function openMaterial(materialId) {
    const material = getMaterial(materialId);
    const url = buildOpenUrl(material);
    if (!url) {
        showToast('该材料暂时无法打开', 'warning');
        return;
    }
    window.open(url, '_blank', 'noopener');
}

async function reload() {
    state.loading = true;
    renderList();
    const params = new URLSearchParams({ session_id: String(state.sessionId || 0), generate_blurbs: 'false' });
    try {
        const data = await apiFetch(
            `/api/classrooms/${state.classOfferingId}/learning-materials?${params.toString()}`,
            { silent: true },
        );
        state.materials = Array.isArray(data.materials) ? data.materials : [];
        state.canManage = state.isTeacher && Boolean(data.can_manage);
    } finally {
        state.loading = false;
        renderList();
    }
}

function showConfirm(material) {
    ensureDom();
    state.pendingRemoval = material;
    const body = document.getElementById('lsMatConfirmBody');
    if (body) {
        body.innerHTML = `确定要从这里解绑 <strong>${escapeHtml(material.name || '该材料')}</strong> 吗？解绑后学生将不再从此入口看到它，材料本身不会被删除。`;
    }
    confirmBackdrop.hidden = false;
    confirmBackdrop.setAttribute('aria-hidden', 'false');
    releaseConfirmFocus = ownClassroomMaterialFocus(confirmBackdrop, closeConfirm, confirmBackdrop.querySelector('[data-cancel-removal]'));
}

function closeConfirm() {
    if (!confirmBackdrop) return;
    confirmBackdrop.hidden = true;
    confirmBackdrop.setAttribute('aria-hidden', 'true');
    state.pendingRemoval = null;
    releaseConfirmFocus?.(); releaseConfirmFocus = null;
}

async function confirmRemoval() {
    const material = state.pendingRemoval;
    if (!material) return;
    const confirmBtn = confirmBackdrop.querySelector('[data-confirm-removal]');
    if (confirmBtn) confirmBtn.disabled = true;
    try {
        const result = await apiFetch(
            `/api/classrooms/${state.classOfferingId}/learning-materials`,
            {
                method: 'DELETE',
                body: { session_id: state.sessionId || 0, material_id: material.material_id },
                silent: true,
            },
        );
        showToast(result.message || '已解绑该材料', 'success');
        closeConfirm();
        await reload();
        listBackdrop.querySelector('[data-close-mat-popup]')?.focus({ preventScroll: true });
        if (typeof state.onChanged === 'function') {
            state.onChanged(result);
        }
    } finally {
        if (confirmBtn) confirmBtn.disabled = false;
    }
}

function closeListPopup() {
    if (!listBackdrop) return;
    if (!confirmBackdrop.hidden) closeConfirm();
    listBackdrop.hidden = true;
    listBackdrop.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('has-ls-mat-popup');
    releaseListFocus?.(); releaseListFocus = null;
}

export function openMaterialListPopup({
    classOfferingId,
    sessionId = 0,
    isHome = false,
    isTeacher = false,
    title = '学习材料',
    subtitle = '点击任意卡片进入材料',
    onChanged = null,
    returnFocus = null,
} = {}) {
    ensureDom();
    state.classOfferingId = Number(classOfferingId) || 0;
    state.sessionId = isHome ? 0 : (Number(sessionId) || 0);
    state.isHome = Boolean(isHome);
    state.isTeacher = Boolean(isTeacher);
    state.canManage = false;
    state.onChanged = onChanged;
    state.materials = [];

    const titleEl = document.getElementById('lsMatPopupTitle');
    const subtitleEl = document.getElementById('lsMatPopupSubtitle');
    if (titleEl) titleEl.textContent = title || '学习材料';
    if (subtitleEl) subtitleEl.textContent = subtitle || '点击任意卡片进入材料';

    const listEl = document.getElementById('lsMatPopupList');
    if (listEl && !listEl.dataset.bound) {
        listEl.dataset.bound = 'true';
        listEl.addEventListener('click', (event) => {
            const removeBtn = event.target.closest('[data-remove-material]');
            if (removeBtn) {
                event.stopPropagation();
                const material = getMaterial(removeBtn.dataset.removeMaterial);
                if (material) showConfirm(material);
                return;
            }
            const card = event.target.closest('[data-open-material]');
            if (card) openMaterial(card.dataset.openMaterial);
        });
        listEl.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            if (event.target.closest('[data-remove-material]')) return;
            const card = event.target.closest('[data-open-material]');
            if (card) {
                event.preventDefault();
                openMaterial(card.dataset.openMaterial);
            }
        });
    }

    listBackdrop.hidden = false;
    listBackdrop.setAttribute('aria-hidden', 'false');
    document.body.classList.add('has-ls-mat-popup');
    releaseListFocus?.();
    releaseListFocus = ownClassroomMaterialFocus(listBackdrop, closeListPopup, listBackdrop.querySelector('[data-close-mat-popup]'), returnFocus);
    return reload();
}
