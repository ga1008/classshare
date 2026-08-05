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

export class SignaturePointControl {
    constructor({
        root,
        pointKey,
        pointLabel,
        materialType,
        materialId,
        initialSelectedIds = [],
        onChange = () => {},
        notify = () => {},
    }) {
        this.root = typeof root === 'string' ? document.querySelector(root) : root;
        this.pointKey = pointKey;
        this.pointLabel = pointLabel;
        this.materialType = materialType;
        this.materialId = String(materialId || '');
        this.selectedIds = uniqueIds(initialSelectedIds);
        this.onChange = onChange;
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
        this.selectedIds = uniqueIds(initialSelectedIds);
        await this.load();
    }

    async load({ preserveSelection = true } = {}) {
        if (!this.materialId || this.loading) return;
        this.loading = true;
        try {
            const state = await apiFetch(this.endpoint(), { silent: true });
            this.state = state;
            this.pointLabel = state.point?.label || this.pointLabel;
            const serverSelected = uniqueIds(state.selected_signature_ids);
            const usableIds = new Set((state.usable_signatures || []).map((item) => Number(item.id)));
            const current = preserveSelection ? this.selectedIds : [];
            this.selectedIds = uniqueIds(serverSelected.length ? serverSelected : current).filter((id) => usableIds.has(id));
            this.render();
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

    signatureById(id) {
        return (this.state?.signatures || []).find((item) => Number(item.id) === Number(id));
    }

    requiredIdentityLabels() {
        return this.state?.point?.required_identity_labels || [];
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

    render() {
        const usable = this.state?.usable_signatures || [];
        const selected = this.selectedIds.map((id, index) => {
            const signature = this.signatureById(id);
            if (!signature) return '';
            return `<li data-spw-selected="${id}">
                <span class="spw-order">${index + 1}</span>
                <span><strong>${esc(signature.subject_name || signature.name)}</strong><small>${esc(signature.scope_label || '')}</small></span>
                <span class="spw-order-actions">
                    <button type="button" data-spw-move="up" aria-label="上移" ${index === 0 ? 'disabled' : ''}>↑</button>
                    <button type="button" data-spw-move="down" aria-label="下移" ${index === this.selectedIds.length - 1 ? 'disabled' : ''}>↓</button>
                    <button type="button" data-spw-remove aria-label="移除">×</button>
                </span>
            </li>`;
        }).join('');
        const remaining = usable.filter((item) => !this.selectedIds.includes(Number(item.id)));
        const visible = this.filterCandidates(remaining, this.searchTerm, this.identityFilterOn);
        const hiddenCount = remaining.length - visible.length;
        const options = visible.map((item) => `<option value="${item.id}">${esc(item.subject_name || item.name)}${item.identity_label ? ` · ${esc(item.identity_label)}${item.identity_verified ? '✓' : ''}` : ''} · ${esc(item.scope_label || '')}</option>`).join('');
        const flowBadge = this.state?.active_flow
            ? `<span class="spw-flow-badge">${esc(statusText[this.state.active_flow.status] || '申请处理中')}</span>`
            : '';
        this.root.innerHTML = `
            <div class="spw-head">
                <div><span>独立签名点</span><strong>${esc(this.pointLabel)}</strong></div>
                <div>${flowBadge}<button type="button" class="spw-apply" data-spw-apply>申请签名</button></div>
            </div>
            <p class="spw-scope">授权仅对“${esc(this.state?.material?.label || '当前材料')}”当前版本有效；材料重建后自动失效。</p>
            <ul class="spw-selected-list">${selected || '<li class="spw-selected-empty">尚未选择签名</li>'}</ul>
            <div class="spw-picker-tools">
                <input type="search" data-spw-search placeholder="模糊搜索签名姓名…" value="${esc(this.searchTerm)}" ${remaining.length ? '' : 'disabled'}>
                ${this.identityToggleHtml('data-spw-identity-toggle', this.identityFilterOn, hiddenCount)}
            </div>
            <div class="spw-picker">
                <select data-spw-available ${visible.length ? '' : 'disabled'}>
                    <option value="">${visible.length ? '选择一个已获准签名…' : (remaining.length ? '当前过滤条件下无可用签名' : '暂无更多可用签名')}</option>${options}
                </select>
                <button type="button" data-spw-add ${visible.length ? '' : 'disabled'}>加入</button>
            </div>
            <small class="spw-help">多人签名将按上方顺序等宽排布并保持原始比例。</small>`;
        this.bindRootEvents();
    }

    bindRootEvents() {
        this.root.querySelector('[data-spw-apply]')?.addEventListener('click', () => this.openFlow());
        const searchInput = this.root.querySelector('[data-spw-search]');
        searchInput?.addEventListener('input', () => {
            this.searchTerm = searchInput.value || '';
            const caret = searchInput.selectionStart;
            this.render();
            const restored = this.root.querySelector('[data-spw-search]');
            if (restored) {
                restored.focus();
                try { restored.setSelectionRange(caret, caret); } catch { /* type=search quirks */ }
            }
        });
        this.root.querySelector('[data-spw-identity-toggle]')?.addEventListener('click', () => {
            this.identityFilterOn = !this.identityFilterOn;
            this.render();
        });
        this.root.querySelector('[data-spw-add]')?.addEventListener('click', () => {
            const select = this.root.querySelector('[data-spw-available]');
            const id = Number(select?.value || 0);
            if (!id) return;
            this.selectedIds = uniqueIds([...this.selectedIds, id]);
            this.onChange(this.getSelectedIds());
            this.render();
        });
        this.root.querySelectorAll('[data-spw-selected]').forEach((row) => {
            const id = Number(row.dataset.spwSelected || 0);
            row.querySelector('[data-spw-remove]')?.addEventListener('click', () => {
                this.selectedIds = this.selectedIds.filter((item) => item !== id);
                this.onChange(this.getSelectedIds());
                this.render();
            });
            row.querySelectorAll('[data-spw-move]').forEach((button) => button.addEventListener('click', () => {
                const index = this.selectedIds.indexOf(id);
                const next = button.dataset.spwMove === 'up' ? index - 1 : index + 1;
                if (index < 0 || next < 0 || next >= this.selectedIds.length) return;
                [this.selectedIds[index], this.selectedIds[next]] = [this.selectedIds[next], this.selectedIds[index]];
                this.onChange(this.getSelectedIds());
                this.render();
            }));
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
            dialogSearch?.addEventListener('input', () => {
                this.dialogSearchTerm = dialogSearch.value || '';
                const caret = dialogSearch.selectionStart;
                this.renderFlowDialog();
                const restored = this.dialog.querySelector('[data-spw-dialog-search]');
                if (restored) {
                    restored.focus();
                    try { restored.setSelectionRange(caret, caret); } catch { /* type=search quirks */ }
                }
            });
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
