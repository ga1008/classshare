import { apiFetch } from './api.js';

const statusText = {
    pending: '待审批',
    partially_approved: '部分已处理',
    approved: '已批准',
    rejected: '已拒绝',
    cancelled: '已结束',
    superseded: '无需重复审批',
};

function esc(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function uniqueIds(values) {
    const result = [];
    (Array.isArray(values) ? values : []).forEach((value) => {
        const id = Number(value || 0);
        if (id > 0 && !result.includes(id)) result.push(id);
    });
    return result.slice(0, 12);
}

function sameIds(a, b) {
    return a.length === b.length && a.every((id, index) => id === b[index]);
}

export class SignaturePointControl {
    constructor({
        root,
        pointKey,
        pointLabel,
        materialType,
        materialId,
        initialSelectedIds = [],
        onChange = () => {},
        onConfirm = null,
        onStateChange = () => {},
        notify = () => {},
    }) {
        this.root = typeof root === 'string' ? document.querySelector(root) : root;
        this.pointKey = pointKey;
        this.pointLabel = pointLabel;
        this.materialType = materialType;
        this.materialId = String(materialId || '');
        // confirmedIds = 服务端已生效的绑定；selectedIds = 工作区。两者不一致
        // 即“待确认”，只有点击“确认”并成功回写后台后才收敛。
        this.confirmedIds = uniqueIds(initialSelectedIds);
        this.selectedIds = [...this.confirmedIds];
        this.updating = false;
        this.onChange = onChange;
        this.onConfirm = onConfirm;
        this.onStateChange = onStateChange;
        this.notify = notify;
        this.state = null;
        this.loading = false;
        this.dialog = null;
        this.searchTerm = '';
        // 默认按签名点要求的职务身份过滤（如系主任含副系主任）；可一键显示全部。
        this.identityFilterOn = true;
        this.dialogSearchTerm = '';
        this.dialogIdentityFilterOn = true;
        if (!this.root) throw new Error(`Signature point root missing: ${pointKey}`);
        this.root.classList.add('spw-point');
        this.root.innerHTML = '<div class="spw-loading">正在读取签名点权限…</div>';
    }

    endpoint(suffix = 'state') {
        const base = `/api/signatures/points/${encodeURIComponent(this.pointKey)}`;
        if (suffix === 'state') {
            const query = new URLSearchParams({ material_type: this.materialType, material_id: this.materialId });
            return `${base}/state?${query.toString()}`;
        }
        return `${base}/${suffix}`;
    }

    async setMaterial(materialId, initialSelectedIds = []) {
        this.materialId = String(materialId || '');
        this.confirmedIds = uniqueIds(initialSelectedIds);
        this.selectedIds = [...this.confirmedIds];
        await this.load({ preserveSelection: false });
    }

    async load({ preserveSelection = true } = {}) {
        if (!this.materialId || this.loading) return;
        this.loading = true;
        try {
            const state = await apiFetch(this.endpoint(), { silent: true });
            this.state = state;
            this.pointLabel = state.point?.label || this.pointLabel;
            const usableIds = new Set((state.usable_signatures || []).map((item) => Number(item.id)));
            const serverSelected = uniqueIds(state.selected_signature_ids).filter((id) => usableIds.has(id));
            const wasDirty = this.isDirty();
            // 服务端有绑定则以其为准；否则沿用宿主注入的初始值（老记录兜底）。
            this.confirmedIds = serverSelected.length
                ? serverSelected
                : uniqueIds(this.confirmedIds).filter((id) => usableIds.has(id));
            const working = preserveSelection && wasDirty ? this.selectedIds : this.confirmedIds;
            this.selectedIds = uniqueIds(working).filter((id) => usableIds.has(id));
            this.render();
            this.emitState();
        } catch (error) {
            this.root.innerHTML = `<div class="spw-error"><strong>${esc(this.pointLabel)}</strong><span>${esc(error.message || '签名点加载失败')}</span><button type="button" data-spw-retry>重试</button></div>`;
            this.root.querySelector('[data-spw-retry]')?.addEventListener('click', () => this.load());
        } finally {
            this.loading = false;
        }
    }

    getSelectedIds() {
        return [...this.selectedIds];
    }

    isDirty() {
        return !sameIds(this.selectedIds, this.confirmedIds);
    }

    isUpdating() {
        return Boolean(this.updating);
    }

    emitState() {
        this.onStateChange({ dirty: this.isDirty(), updating: this.isUpdating(), selectedIds: this.getSelectedIds() });
    }

    applySelection(ids) {
        this.selectedIds = uniqueIds(ids);
        this.onChange(this.getSelectedIds());
        this.render();
        this.emitState();
    }

    async confirmSelection() {
        if (!this.isDirty() || this.updating) return;
        this.updating = true;
        this.render();
        this.emitState();
        try {
            if (this.onConfirm) await this.onConfirm(this.getSelectedIds());
            this.confirmedIds = [...this.selectedIds];
            this.notify('签名已确认，文档已同步更新。', 'success');
        } catch (error) {
            this.notify(error.message || '文档更新失败，签名尚未生效。', 'error');
        } finally {
            this.updating = false;
            this.render();
            this.emitState();
        }
    }

    signatureById(id) {
        return (this.state?.signatures || []).find((item) => Number(item.id) === Number(id));
    }

    requiredIdentityLabels() {
        return this.state?.point?.required_identity_labels || [];
    }

    bindImeSafeSearch(input, applyTerm) {
        // 输入过滤会整体重渲染并销毁输入框 DOM；若在输入法组合（拼音）期间
        // 重渲染，IME 组合被打断，中文永远打不出来。组合中只记录不渲染，
        // compositionend 后再应用过滤并恢复焦点/光标。
        let composing = false;
        const apply = () => {
            const caret = input.selectionStart;
            const restored = applyTerm(input.value || '');
            if (restored) {
                restored.focus();
                try { restored.setSelectionRange(caret, caret); } catch { /* type=search quirks */ }
            }
        };
        input.addEventListener('compositionstart', () => { composing = true; });
        input.addEventListener('compositionend', () => { composing = false; apply(); });
        input.addEventListener('input', () => { if (!composing) apply(); });
    }

    filterCandidates(items, searchTerm, identityOn) {
        const term = String(searchTerm || '').trim().toLowerCase();
        const hasIdentityRule = this.requiredIdentityLabels().length > 0;
        return (items || []).filter((item) => {
            if (identityOn && hasIdentityRule && !item.identity_match) return false;
            if (!term) return true;
            const haystack = `${item.subject_name || ''} ${item.name || ''} ${item.identity_label || ''}`.toLowerCase();
            return haystack.includes(term);
        });
    }

    identityToggleHtml(attr, isOn, hiddenCount) {
        const labels = this.requiredIdentityLabels();
        if (!labels.length) return '';
        const text = isOn
            ? `已按身份过滤：${labels.join('、')}（含副职）${hiddenCount ? ` · 隐藏 ${hiddenCount} 个` : ''} — 点击显示全部`
            : '正在显示全部签名 — 点击恢复身份过滤';
        return `<button type="button" class="spw-identity-toggle${isOn ? ' is-on' : ''}" ${attr}>${esc(text)}</button>`;
    }

    areaState() {
        if (this.updating) return 'updating';
        if (this.isDirty()) return 'dirty';
        return this.selectedIds.length ? 'confirmed' : 'neutral';
    }

    areaBadgeHtml() {
        switch (this.areaState()) {
            case 'updating':
                return '<span class="spw-state is-updating"><span class="spw-spinner" aria-hidden="true"></span>后台正在更新文档…</span>';
            case 'dirty':
                return '<span class="spw-state is-dirty">修改待确认</span>';
            case 'confirmed':
                return '<span class="spw-state is-confirmed">已生效 ✓</span>';
            default:
                return '<span class="spw-state is-neutral">尚未选择签名</span>';
        }
    }

    render() {
        const usable = this.state?.usable_signatures || [];
        const busy = this.updating;
        const selected = this.selectedIds.map((id, index) => {
            const signature = this.signatureById(id);
            if (!signature) return '';
            return `<li data-spw-selected="${id}" draggable="${busy ? 'false' : 'true'}">
                <span class="spw-drag" aria-hidden="true" title="拖动排序">⠿</span>
                <span class="spw-order">${index + 1}</span>
                <span><strong>${esc(signature.subject_name || signature.name)}</strong><small>${esc(signature.scope_label || '')}</small></span>
                <span class="spw-order-actions">
                    <button type="button" data-spw-move="up" aria-label="上移" ${index === 0 || busy ? 'disabled' : ''}>↑</button>
                    <button type="button" data-spw-move="down" aria-label="下移" ${index === this.selectedIds.length - 1 || busy ? 'disabled' : ''}>↓</button>
                    <button type="button" data-spw-remove aria-label="移除" ${busy ? 'disabled' : ''}>×</button>
                </span>
            </li>`;
        }).join('');
        const remaining = usable.filter((item) => !this.selectedIds.includes(Number(item.id)));
        const visible = this.filterCandidates(remaining, this.searchTerm, this.identityFilterOn);
        const hiddenCount = remaining.length - visible.length;
        const optionHtml = (item) => `<option value="${item.id}">${esc(item.subject_name || item.name)}${item.identity_label ? ` · ${esc(item.identity_label)}${item.identity_verified ? '✓' : ''}` : ''} · ${esc(item.signature_kind === 'stamp' ? '批语章' : (item.scope_label || ''))}</option>`;
        const personalOptions = visible.filter((item) => item.signature_kind !== 'stamp');
        const stampOptions = visible.filter((item) => item.signature_kind === 'stamp');
        // 批语章单独分组：同意/已阅等共享签章无需申请、不受身份过滤。
        const options = [
            personalOptions.length ? `<optgroup label="个人签名">${personalOptions.map(optionHtml).join('')}</optgroup>` : '',
            stampOptions.length ? `<optgroup label="批语章（无需申请）">${stampOptions.map(optionHtml).join('')}</optgroup>` : '',
        ].join('');
        const flowBadge = this.state?.active_flow
            ? `<span class="spw-flow-badge">${esc(statusText[this.state.active_flow.status] || '申请处理中')}</span>`
            : '';
        const areaState = this.areaState();
        const dirty = this.isDirty();
        this.root.innerHTML = `
            <div class="spw-head">
                <div><span>独立签名点</span><strong>${esc(this.pointLabel)}</strong></div>
                <div>${flowBadge}<button type="button" class="spw-apply" data-spw-apply ${busy ? 'disabled' : ''}>申请签名</button></div>
            </div>
            <p class="spw-scope">授权仅对“${esc(this.state?.material?.label || '当前材料')}”当前版本有效；材料重建后自动失效。</p>
            <div class="spw-selected is-${areaState}" data-spw-area>
                <div class="spw-selected-head">
                    <span class="spw-selected-title">已选签名${this.selectedIds.length ? ` · ${this.selectedIds.length}` : ''}</span>
                    ${this.areaBadgeHtml()}
                </div>
                <ul class="spw-selected-list" data-spw-list>${selected || '<li class="spw-selected-empty">从下方挑选签名，可拖动调整顺序</li>'}</ul>
                <div class="spw-selected-actions">
                    <button type="button" data-spw-clear ${this.selectedIds.length && !busy ? '' : 'disabled'}>全部取消</button>
                    ${dirty && !busy ? '<button type="button" data-spw-revert>还原</button>' : ''}
                    <span class="spw-selected-actions__spacer"></span>
                    <button type="button" class="spw-confirm" data-spw-confirm ${dirty && !busy ? '' : 'disabled'}>${busy ? '正在更新…' : '确认并更新文档'}</button>
                </div>
            </div>
            <div class="spw-picker-tools">
                <input type="search" data-spw-search placeholder="模糊搜索签名姓名…" value="${esc(this.searchTerm)}" ${remaining.length && !busy ? '' : 'disabled'}>
                ${this.identityToggleHtml('data-spw-identity-toggle', this.identityFilterOn, hiddenCount)}
            </div>
            <div class="spw-picker">
                <select data-spw-available ${visible.length && !busy ? '' : 'disabled'}>
                    <option value="">${visible.length ? '选择签名…' : (remaining.length ? '当前过滤条件下无可用签名' : '暂无更多可用签名')}</option>${options}
                </select>
            </div>
            <small class="spw-help">多人签名将按上方顺序等宽排布并保持原始比例；调整后点击“确认并更新文档”生效。</small>`;
        this.bindRootEvents();
    }

    bindRootEvents() {
        this.root.querySelector('[data-spw-apply]')?.addEventListener('click', () => this.openFlow());
        const searchInput = this.root.querySelector('[data-spw-search]');
        if (searchInput) {
            this.bindImeSafeSearch(searchInput, (term) => {
                this.searchTerm = term;
                this.render();
                return this.root.querySelector('[data-spw-search]');
            });
        }
        this.root.querySelector('[data-spw-identity-toggle]')?.addEventListener('click', () => {
            this.identityFilterOn = !this.identityFilterOn;
            this.render();
        });
        // 选中即加入：多余的“加入”按钮曾让人选完就以为已生效，直接点保存
        // 导致什么都没提交。
        this.root.querySelector('[data-spw-available]')?.addEventListener('change', () => {
            if (this.updating) return;
            const select = this.root.querySelector('[data-spw-available]');
            const id = Number(select?.value || 0);
            if (!id) return;
            const usable = this.state?.usable_signatures || [];
            const picked = usable.find((item) => Number(item.id) === id);
            if (picked?.signature_kind === 'stamp') {
                const hasPersonal = this.selectedIds.some((selectedId) => {
                    const entry = usable.find((item) => Number(item.id) === Number(selectedId));
                    return entry && entry.signature_kind !== 'stamp';
                });
                // 仅批语+线下盖章是合法场景，轻提示一次即可，不阻断。
                if (!hasPersonal && !window.confirm('该签名点当前没有个人签名，仅使用批语章（配合线下盖章）？')) {
                    select.value = '';
                    return;
                }
            }
            this.applySelection([...this.selectedIds, id]);
        });
        this.root.querySelector('[data-spw-clear]')?.addEventListener('click', () => {
            if (this.updating) return;
            this.applySelection([]);
        });
        this.root.querySelector('[data-spw-revert]')?.addEventListener('click', () => {
            if (this.updating) return;
            this.applySelection([...this.confirmedIds]);
        });
        this.root.querySelector('[data-spw-confirm]')?.addEventListener('click', () => this.confirmSelection());
        this.bindListEvents();
    }

    bindListEvents() {
        const list = this.root.querySelector('[data-spw-list]');
        if (!list) return;
        list.querySelectorAll('[data-spw-selected]').forEach((row) => {
            const id = Number(row.dataset.spwSelected || 0);
            row.querySelector('[data-spw-remove]')?.addEventListener('click', () => {
                if (this.updating) return;
                this.applySelection(this.selectedIds.filter((item) => item !== id));
            });
            row.querySelectorAll('[data-spw-move]').forEach((button) => button.addEventListener('click', () => {
                if (this.updating) return;
                const index = this.selectedIds.indexOf(id);
                const next = button.dataset.spwMove === 'up' ? index - 1 : index + 1;
                if (index < 0 || next < 0 || next >= this.selectedIds.length) return;
                const reordered = [...this.selectedIds];
                [reordered[index], reordered[next]] = [reordered[next], reordered[index]];
                this.applySelection(reordered);
            }));
        });
        // 拖拽排序：dragover 阶段只挪 DOM 不重渲染（重渲染会杀掉拖拽会话），
        // dragend 时按 DOM 顺序回读 selectedIds 再统一重渲染。↑↓ 按钮保留，
        // 作为触屏与键盘用户的等价操作。
        let draggingRow = null;
        list.querySelectorAll('[data-spw-selected]').forEach((row) => {
            row.addEventListener('dragstart', (event) => {
                if (this.updating) {
                    event.preventDefault();
                    return;
                }
                draggingRow = row;
                row.classList.add('is-dragging');
                event.dataTransfer.effectAllowed = 'move';
                try { event.dataTransfer.setData('text/plain', row.dataset.spwSelected); } catch { /* 某些浏览器要求 setData 才能拖动 */ }
            });
            row.addEventListener('dragend', () => {
                if (!draggingRow) return;
                draggingRow.classList.remove('is-dragging');
                draggingRow = null;
                const order = Array.from(list.querySelectorAll('[data-spw-selected]'))
                    .map((item) => Number(item.dataset.spwSelected || 0));
                if (sameIds(uniqueIds(order), this.selectedIds)) {
                    this.render();
                    return;
                }
                this.applySelection(order);
            });
        });
        list.addEventListener('dragover', (event) => {
            if (!draggingRow) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            const siblings = Array.from(list.querySelectorAll('[data-spw-selected]:not(.is-dragging)'));
            const next = siblings.find((item) => {
                const rect = item.getBoundingClientRect();
                return event.clientY <= rect.top + rect.height / 2;
            });
            if (next) {
                list.insertBefore(draggingRow, next);
            } else {
                list.appendChild(draggingRow);
            }
        });
        list.addEventListener('drop', (event) => {
            if (draggingRow) event.preventDefault();
        });
    }

    ensureDialog() {
        if (this.dialog) return this.dialog;
        const dialog = document.createElement('dialog');
        dialog.className = 'spw-dialog';
        dialog.innerHTML = '<div class="spw-dialog-panel" data-spw-dialog-panel></div>';
        document.body.appendChild(dialog);
        dialog.addEventListener('click', (event) => {
            if (event.target === dialog) dialog.close();
        });
        this.dialog = dialog;
        return dialog;
    }

    openFlow() {
        this.renderFlowDialog();
        this.ensureDialog().showModal();
    }

    renderFlowDialog() {
        const dialog = this.ensureDialog();
        const panel = dialog.querySelector('[data-spw-dialog-panel]');
        const flow = this.state?.active_flow;
        if (flow) {
            const items = (flow.items || []).map((item) => {
                const reviewers = (item.request?.reviewers || []).map((reviewer) => `<span class="is-${esc(reviewer.status)}">${esc(reviewer.name || reviewer.kind)} · ${esc(statusText[reviewer.status] || reviewer.status)}</span>`).join('');
                return `<li><div><strong>${esc(item.signature_name)}</strong><em class="is-${esc(item.status)}">${esc(statusText[item.status] || item.status)}</em></div><p>${reviewers || '等待审批人信息'}</p></li>`;
            }).join('');
            panel.innerHTML = `
                <header><div><span>签名申请流程</span><h3>${esc(this.pointLabel)}</h3></div><button type="button" data-spw-close aria-label="关闭">×</button></header>
                <div class="spw-dialog-body">
                    <div class="spw-flow-summary"><strong>${esc(statusText[flow.status] || flow.status)}</strong><span>${esc(flow.material_label || '')}</span></div>
                    <ol class="spw-flow-items">${items}</ol>
                    <p class="spw-flow-note">审批规则：签名归属人或签名者本人任一同意即可授权；未绑定账号的签名由平台管理员代为审批。多人签名分别审批，已通过的授权不会因结束其余申请而撤销。</p>
                </div>
                <footer><button type="button" data-spw-refresh>刷新状态</button><span></span><button type="button" class="is-danger" data-spw-end>结束申请</button></footer>`;
            panel.querySelector('[data-spw-end]')?.addEventListener('click', () => this.endFlow(flow.id));
            panel.querySelector('[data-spw-refresh]')?.addEventListener('click', () => this.refreshDialog());
        } else {
            const candidates = this.state?.requestable_signatures || [];
            const visibleCandidates = this.filterCandidates(candidates, this.dialogSearchTerm, this.dialogIdentityFilterOn);
            const hiddenCount = candidates.length - visibleCandidates.length;
            if (!Array.isArray(this.dialogRequestOrder)) this.dialogRequestOrder = [];
            const options = visibleCandidates.map((item) => {
                const order = this.dialogRequestOrder.indexOf(Number(item.id));
                return `<label><input type="checkbox" value="${item.id}" ${order >= 0 ? 'checked' : ''}><span class="spw-candidate-order">${order >= 0 ? order + 1 : '—'}</span><span><strong>${esc(item.subject_name || item.name)}</strong><small>${item.identity_label ? `${esc(item.identity_label)}${item.identity_verified ? '✓' : ''} · ` : ''}${esc(item.owner_name || item.scope_label || '')}${item.needs_admin_review ? ' · <em class="spw-admin-review">未绑定账号，由管理员审批</em>' : ''}</small></span></label>`;
            }).join('');
            panel.innerHTML = `
                <header><div><span>新建签名申请</span><h3>${esc(this.pointLabel)}</h3></div><button type="button" data-spw-close aria-label="关闭">×</button></header>
                <div class="spw-dialog-body">
                    <p class="spw-flow-note">按需要嵌入的先后顺序勾选签名。申请获批后，它只会出现在当前材料、当前签名点的可用列表中。</p>
                    <div class="spw-picker-tools">
                        <input type="search" data-spw-dialog-search placeholder="模糊搜索签名姓名…" value="${esc(this.dialogSearchTerm)}">
                        ${this.identityToggleHtml('data-spw-dialog-identity-toggle', this.dialogIdentityFilterOn, hiddenCount)}
                    </div>
                    <div class="spw-candidates" data-spw-candidates>${options || `<div class="spw-no-candidates">${candidates.length ? '当前过滤条件下没有匹配签名，可点击上方按钮显示全部。' : '暂无可申请签名；本人签名和已获授权签名可直接在签名点中选择。'}</div>`}</div>
                    <label class="spw-note"><span>申请说明（可选）</span><textarea maxlength="300" data-spw-note placeholder="说明材料用途或审批背景"></textarea></label>
                </div>
                <footer><button type="button" data-spw-refresh>刷新签名库</button><span></span><button type="button" class="is-primary" data-spw-create ${candidates.length ? '' : 'disabled'}>创建申请流程</button></footer>`;
            panel.querySelector('[data-spw-create]')?.addEventListener('click', () => this.createFlow());
            panel.querySelector('[data-spw-refresh]')?.addEventListener('click', () => this.refreshDialog());
            const dialogSearch = panel.querySelector('[data-spw-dialog-search]');
            if (dialogSearch) {
                this.bindImeSafeSearch(dialogSearch, (term) => {
                    this.dialogSearchTerm = term;
                    this.renderFlowDialog();
                    return this.dialog.querySelector('[data-spw-dialog-search]');
                });
            }
            panel.querySelector('[data-spw-dialog-identity-toggle]')?.addEventListener('click', () => {
                this.dialogIdentityFilterOn = !this.dialogIdentityFilterOn;
                this.renderFlowDialog();
            });
            panel.querySelectorAll('[data-spw-candidates] input').forEach((input) => input.addEventListener('change', () => {
                const id = Number(input.value);
                const currentIndex = this.dialogRequestOrder.indexOf(id);
                if (input.checked && currentIndex < 0) this.dialogRequestOrder.push(id);
                if (!input.checked && currentIndex >= 0) this.dialogRequestOrder.splice(currentIndex, 1);
                panel.querySelectorAll('[data-spw-candidates] input').forEach((candidate) => {
                    const order = this.dialogRequestOrder.indexOf(Number(candidate.value));
                    const badge = candidate.closest('label')?.querySelector('.spw-candidate-order');
                    if (badge) badge.textContent = order >= 0 ? String(order + 1) : '—';
                });
            }));
        }
        panel.querySelector('[data-spw-close]')?.addEventListener('click', () => dialog.close());
    }

    async refreshDialog() {
        await this.load();
        this.renderFlowDialog();
    }

    async createFlow() {
        const panel = this.dialog.querySelector('[data-spw-dialog-panel]');
        // Order preserved across searches/filters: hidden-but-checked stay in.
        const signatureIds = (this.dialogRequestOrder || []).slice();
        if (!signatureIds.length) {
            this.notify('请至少选择一个需要申请的签名。', 'error');
            return;
        }
        const button = panel.querySelector('[data-spw-create]');
        button.disabled = true;
        button.textContent = '正在创建…';
        try {
            await apiFetch(this.endpoint('flows'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    material_type: this.materialType,
                    material_id: this.materialId,
                    signature_ids: signatureIds,
                    note: panel.querySelector('[data-spw-note]')?.value || '',
                }),
                silent: true,
            });
            this.dialogRequestOrder = [];
            await this.load();
            this.renderFlowDialog();
            this.notify('签名申请流程已创建，审批人已收到通知。', 'success');
        } catch (error) {
            this.notify(error.message || '创建签名申请失败。', 'error');
            button.disabled = false;
            button.textContent = '创建申请流程';
        }
    }

    async endFlow(flowId) {
        const button = this.dialog.querySelector('[data-spw-end]');
        if (button) {
            button.disabled = true;
            button.textContent = '正在结束…';
        }
        try {
            await apiFetch(`/api/signatures/point-flows/${Number(flowId)}/end`, { method: 'POST', silent: true });
            await this.load();
            this.renderFlowDialog();
            this.notify('申请流程已结束，可重新创建。', 'success');
        } catch (error) {
            this.notify(error.message || '结束申请失败。', 'error');
            if (button) {
                button.disabled = false;
                button.textContent = '结束申请';
            }
        }
    }
}
