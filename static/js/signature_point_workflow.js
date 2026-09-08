import { apiFetch } from './api.js';
import { SignatureMultiSelect } from './signature_multi_select.js?v=material-workflows-1';

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
        this.dialogRequestNote = '';
        this.dialogAutoApply = true;
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
        if (String(materialId || '') !== this.materialId) { this.dialog?.close(); this.dialogRequestOrder = undefined; }
        this.materialId = String(materialId || '');
        this.confirmedIds = uniqueIds(initialSelectedIds);
        this.selectedIds = [...this.confirmedIds];
        await this.load({ preserveSelection: false });
    }

    async load({ preserveSelection = true } = {}) {
        if (!this.materialId) return;
        const sequence = this.loadSequence = (this.loadSequence || 0) + 1;
        this.loading = true;
        try {
            const state = await apiFetch(this.endpoint(), { silent: true });
            if (sequence !== this.loadSequence) return;
            this.state = state;
            this.pointLabel = state.point?.label || this.pointLabel;
            const usableIds = new Set((state.usable_signatures || []).map((item) => Number(item.id)));
            const serverSelected = uniqueIds(state.selected_signature_ids).filter((id) => usableIds.has(id));
            const wasDirty = this.isDirty();
            // 服务端有绑定则以其为准；否则沿用宿主注入的初始值（老记录兜底）。
            this.confirmedIds = Array.isArray(state.selected_signature_ids)
                ? serverSelected
                : uniqueIds(this.confirmedIds).filter((id) => usableIds.has(id));
            const working = preserveSelection && wasDirty ? this.selectedIds : this.confirmedIds;
            const visibleIds = new Set((state.signatures || []).map(item => Number(item.id)));
            this.selectedIds = uniqueIds(working).filter((id) => visibleIds.has(id));
            this.render();
            this.emitState();
        } catch (error) {
            if (sequence !== this.loadSequence) return;
            this.root.innerHTML = `<div class="spw-error"><strong>${esc(this.pointLabel)}</strong><span>${esc(error.message || '签名点加载失败')}</span><button type="button" data-spw-retry>重试</button></div>`;
            this.root.querySelector('[data-spw-retry]')?.addEventListener('click', () => this.load());
        } finally {
            if (sequence === this.loadSequence) this.loading = false;
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
        this.picker?.update({ selectedIds: this.selectedIds });
        this.updateSelectionStatus();
        this.emitState();
    }

    async confirmSelection() {
        if (!this.isDirty() || this.updating) return;
        if (this.selectedIds.some(id => !this.signatureById(id)?.can_use)) {
            this.dialogRequestOrder = [...this.selectedIds];
            this.openFlow();
            return;
        }
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
        this.picker?.destroy();
        this.root.innerHTML = `
            <div class="spw-head"><div><strong>${esc(this.pointLabel)}</strong></div><button type="button" class="spw-apply" data-spw-apply ${this.updating ? 'disabled' : ''}>${this.state?.active_flow ? '查看申请' : '申请签名'}</button></div>
            <p class="spw-scope">仅用于当前材料及该签名位置。内容变更后需重新确认授权。</p>
            <div class="spw-selected" data-spw-area>
                <div class="spw-selected-head"><span class="spw-selected-title">签名与排版顺序</span><span data-spw-status></span></div>
                <div data-spw-picker></div>
                <div class="spw-selected-actions"><button type="button" data-spw-clear>全部取消</button><button type="button" data-spw-revert>还原</button><span class="spw-selected-actions__spacer"></span><button type="button" class="spw-confirm" data-spw-confirm></button></div>
            </div>`;
        this.picker = new SignatureMultiSelect({
            root: this.root.querySelector('[data-spw-picker]'), items: this.state?.signatures || [],
            selectedIds: this.selectedIds, identityLabels: this.requiredIdentityLabels(), disabled: this.updating,
            onChange: ids => this.applySelection(ids), onSearch: term => this.searchSignatures(term),
        });
        this.root.querySelector('[data-spw-apply]').addEventListener('click', () => this.openFlow());
        this.root.querySelector('[data-spw-clear]').addEventListener('click', () => this.applySelection([]));
        this.root.querySelector('[data-spw-revert]').addEventListener('click', () => this.applySelection(this.confirmedIds));
        this.root.querySelector('[data-spw-confirm]').addEventListener('click', () => this.confirmSelection());
        this.updateSelectionStatus();
    }

    updateSelectionStatus() {
        const area = this.root.querySelector('[data-spw-area]');
        if (!area) return;
        area.className = `spw-selected is-${this.areaState()}`;
        this.root.querySelector('[data-spw-status]').innerHTML = this.areaBadgeHtml();
        this.root.querySelector('[data-spw-clear]').disabled = this.updating || !this.selectedIds.length;
        this.root.querySelector('[data-spw-revert]').hidden = !this.isDirty();
        this.root.querySelector('[data-spw-revert]').disabled = this.updating;
        const confirm = this.root.querySelector('[data-spw-confirm]');
        confirm.disabled = !this.isDirty() || this.updating;
        confirm.textContent = this.updating ? '正在更新…' : this.selectedIds.some(id => !this.signatureById(id)?.can_use) ? '申请并应用签名' : '确认并更新文档';
    }

    async searchSignatures(term) {
        const sequence = this.loadSequence;
        const materialId = this.materialId;
        const data = await apiFetch(`${this.endpoint()}&q=${encodeURIComponent(term)}`, { silent: true });
        if (sequence !== this.loadSequence || materialId !== this.materialId) return [];
        const entries = new Map((this.state?.signatures || []).map(item => [Number(item.id), item]));
        (data.signatures || []).forEach(item => entries.set(Number(item.id), item));
        if (this.state) this.state.signatures = [...entries.values()];
        return data.signatures || [];
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
        this.dialogPicker?.destroy();
        this.dialogPicker = null;
        const dialog = this.ensureDialog();
        const panel = dialog.querySelector('[data-spw-dialog-panel]');
        const flow = this.state?.active_flow;
        if (flow) {
            const items = (flow.items || []).map((item) => {
                const reviewers = (item.request?.reviewers || []).map((reviewer) => `<span class="is-${esc(reviewer.status)}">${esc(reviewer.name || reviewer.kind)} · ${esc(statusText[reviewer.status] || reviewer.status)}</span>`).join('');
                return `<li><div><strong>${esc(item.signature_name)}</strong><em class="is-${esc(item.status)}">${esc(statusText[item.status] || item.status)}</em></div><p>${reviewers || (item.request_id ? '等待审批人信息' : '直接使用，无需审批')}</p></li>`;
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
            this.dialogPicker?.destroy();
            if (!Array.isArray(this.dialogRequestOrder)) this.dialogRequestOrder = [...this.selectedIds];
            panel.innerHTML = `
                <header><div><span>申请使用签名</span><h3>${esc(this.pointLabel)}</h3></div><button type="button" data-spw-close aria-label="关闭">×</button></header>
                <div class="spw-dialog-body">
                    <p class="spw-flow-note">${esc(this.state?.material?.label || '')} · 按最终排版顺序选择签名；本人签名、已授权签名与特殊签名无需重复申请。</p>
                    <div data-spw-request-picker></div>
                    <label class="spw-note"><span>申请说明（可选）</span><textarea maxlength="300" data-spw-note placeholder="材料用途或审批背景">${esc(this.dialogRequestNote)}</textarea></label>
                    <label class="spw-auto-apply"><input type="checkbox" data-spw-auto-apply ${this.dialogAutoApply ? 'checked' : ''}><span>本位置全部获批后，按当前顺序自动应用到文档</span></label>
                </div>
                <footer><button type="button" data-spw-refresh>刷新签名库</button><span></span><button type="button" class="is-primary" data-spw-create>提交签名配置</button></footer>`;
            this.dialogPicker = new SignatureMultiSelect({
                root: panel.querySelector('[data-spw-request-picker]'), items: this.state?.signatures || [],
                selectedIds: this.dialogRequestOrder, identityLabels: this.requiredIdentityLabels(),
                onChange: ids => { this.dialogRequestOrder = ids; }, onSearch: term => this.searchSignatures(term),
            });
            panel.querySelector('[data-spw-note]').addEventListener('input', event => { this.dialogRequestNote = event.target.value; });
            panel.querySelector('[data-spw-auto-apply]').addEventListener('change', event => { this.dialogAutoApply = event.target.checked; });
            panel.querySelector('[data-spw-create]').addEventListener('click', () => this.createFlow());
            panel.querySelector('[data-spw-refresh]').addEventListener('click', () => this.refreshDialog());
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
        this.updating = true; this.emitState();
        try {
            await apiFetch(this.endpoint('flows'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    material_type: this.materialType,
                    material_id: this.materialId,
                    signature_ids: signatureIds,
                    note: this.dialogRequestNote,
                    auto_apply: this.dialogAutoApply,
                    expected_revision: this.state?.material?.revision || '',
                    opinion_mode: this.dialogRequestOrder.some(id => this.signatureById(id)?.signature_kind === 'stamp') ? 'stamp' : 'keep',
                }),
                silent: true,
            });
            this.dialogRequestOrder = [];
            this.dialogRequestNote = '';
            this.selectedIds = [...this.confirmedIds];
            await this.load();
            if (this.state?.active_flow) this.renderFlowDialog(); else this.dialog.close();
            this.notify('签名配置已提交，可在签名审批与使用页面查看进度。', 'success');
        } catch (error) {
            this.notify(error.message || '创建签名申请失败。', 'error');
            button.disabled = false;
            button.textContent = '创建申请流程';
        } finally { this.updating = false; this.picker?.update({disabled:false}); this.updateSelectionStatus(); this.emitState(); }
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
