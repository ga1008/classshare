/* One persistent search input and one bounded popup for every signature picker. */
const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
let sequence = 0;

export class SignatureMultiSelect {
    constructor({ root, items = [], selectedIds = [], identityLabels = [], onChange = () => {}, onSearch = null, disabled = false }) {
        this.root = root;
        this.items = items;
        this.selectedIds = [...new Set(selectedIds.map(Number))];
        this.identityLabels = identityLabels;
        this.onChange = onChange;
        this.onSearch = onSearch;
        this.disabled = disabled;
        this.identityOn = true;
        this.term = '';
        this.tab = 'candidates';
        this.abort = new AbortController();
        const id = `signature-picker-${++sequence}`;
        root.classList.add('spm');
        root.innerHTML = `<button type="button" class="spm-trigger" data-spm-open aria-haspopup="dialog" aria-expanded="false" aria-controls="${id}" ${disabled ? 'disabled' : ''}></button>
            <div id="${id}" class="spm-popover" popover="auto" role="dialog" aria-label="选择和排序签名">
                <div class="spm-search"><span aria-hidden="true">⌕</span><input type="search" role="combobox" aria-label="搜索签名姓名、职务" aria-autocomplete="list" aria-expanded="true" aria-controls="${id}-options" placeholder="搜索姓名、职务…" autocomplete="off"><button type="button" data-spm-close aria-label="关闭签名选择">×</button></div>
                <div class="spm-tools"><button type="button" data-spm-identity aria-pressed="true" ${identityLabels.length ? '' : 'hidden'}></button><span class="spm-tabs"><button type="button" data-spm-tab="candidates" aria-pressed="true">候选</button><button type="button" data-spm-tab="selected" aria-pressed="false"></button></span></div>
                <div class="spm-scroll"><div id="${id}-options" data-spm-options role="listbox" aria-label="签名候选" aria-multiselectable="true"></div><ol data-spm-selected aria-label="签名排版顺序" hidden></ol><p data-spm-empty hidden></p></div>
                <footer><span data-spm-count aria-live="polite"></span><button type="button" data-spm-clear>清空选择</button><button type="button" data-spm-done>完成选择</button></footer>
            </div>`;
        this.trigger = root.querySelector('[data-spm-open]');
        this.popup = root.querySelector('.spm-popover');
        this.input = root.querySelector('input');
        const listen = (element, event, callback) => element.addEventListener(event, callback, { signal: this.abort.signal });
        listen(this.trigger, 'click', () => this.open());
        listen(this.popup, 'toggle', event => {
            this.trigger.setAttribute('aria-expanded', String(event.newState === 'open'));
            if (event.newState === 'open') { this.position(); this.input.focus(); }
        });
        listen(window, 'resize', () => this.position());
        let composing = false;
        listen(this.input, 'compositionstart', () => { composing = true; });
        listen(this.input, 'compositionend', () => { composing = false; this.search(); });
        listen(this.input, 'input', () => { if (!composing) this.search(); });
        listen(this.popup, 'click', event => this.click(event));
        listen(this.popup, 'keydown', event => this.keydown(event));
        const list = root.querySelector('[data-spm-selected]');
        listen(list, 'dragstart', event => {
            const row = event.target.closest('[data-spm-id]');
            if (!row || this.disabled) return;
            this.dragging = row;
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', row.dataset.spmId);
            row.classList.add('is-dragging');
        });
        listen(list, 'dragover', event => {
            if (!this.dragging) return;
            event.preventDefault();
            const next = [...list.children].find(row => row !== this.dragging && event.clientY < row.getBoundingClientRect().top + row.offsetHeight / 2);
            list.insertBefore(this.dragging, next || null);
        });
        listen(list, 'drop', event => event.preventDefault());
        listen(list, 'dragend', () => {
            if (!this.dragging) return;
            this.dragging = null;
            this.change([...list.children].map(row => Number(row.dataset.spmId)));
        });
        this.render();
    }

    update({ items = this.items, selectedIds = this.selectedIds, disabled = this.disabled } = {}) {
        this.items = items;
        this.selectedIds = [...selectedIds];
        this.disabled = disabled;
        this.trigger.disabled = disabled;
        this.render();
    }

    open() { if (!this.disabled) { this.popup.showPopover(); this.position(); } }
    close() { this.popup.hidePopover(); this.trigger.focus(); }
    position() {
        if (!this.popup.matches(':popover-open')) return;
        const rect = this.trigger.getBoundingClientRect();
        const width = Math.min(Math.max(rect.width, 360), 480, window.innerWidth - 24);
        const height = Math.min(440, window.innerHeight * .6);
        const below = window.innerHeight - rect.bottom - 12;
        const top = below >= Math.min(height, 320) ? rect.bottom + 6 : Math.max(12, rect.top - height - 6);
        Object.assign(this.popup.style, { width: `${width}px`, maxHeight: `${Math.min(height, window.innerHeight - top - 12)}px`, left: `${Math.min(Math.max(12, rect.left), window.innerWidth - width - 12)}px`, top: `${top}px` });
    }

    search() {
        this.term = this.input.value.trim().toLocaleLowerCase();
        this.renderLists();
        clearTimeout(this.searchTimer);
        if (this.onSearch) this.searchTimer = setTimeout(async () => {
            const term = this.term;
            try {
                const items = await this.onSearch(term);
                if (!this.abort.signal.aborted && term === this.term && Array.isArray(items)) {
                    const byId = new Map(this.items.filter(item => this.selectedIds.includes(Number(item.id))).map(item => [Number(item.id), item]));
                    items.forEach(item => byId.set(Number(item.id), item));
                    this.items = [...byId.values()];
                    this.render();
                }
            } catch { /* Existing candidates remain usable on a search failure. */ }
        }, 200);
    }

    change(ids) {
        if (this.disabled) return;
        if (ids.length > 12) {
            this.root.querySelector('[data-spm-count]').textContent = '每个位置最多选择12项';
            return;
        }
        this.selectedIds = [...new Set(ids)];
        this.render();
        this.onChange([...this.selectedIds]);
    }

    click(event) {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.matches('[data-spm-close], [data-spm-done]')) return this.close();
        if (button.matches('[data-spm-clear]')) return this.change([]);
        if (button.matches('[data-spm-identity]')) { this.identityOn = !this.identityOn; return this.render(); }
        if (button.dataset.spmTab) { this.tab = button.dataset.spmTab; return this.render(); }
        if (button.dataset.spmOption) {
            const id = Number(button.dataset.spmOption);
            return this.change(this.selectedIds.includes(id) ? this.selectedIds.filter(value => value !== id) : [...this.selectedIds, id]);
        }
        const row = button.closest('[data-spm-id]');
        if (!row) return;
        const id = Number(row.dataset.spmId);
        if (button.hasAttribute('data-spm-remove')) return this.change(this.selectedIds.filter(value => value !== id));
        const index = this.selectedIds.indexOf(id), target = index + Number(button.dataset.spmMove || 0);
        if (target < 0 || target >= this.selectedIds.length) return;
        const next = [...this.selectedIds];
        [next[index], next[target]] = [next[target], next[index]];
        this.change(next);
        this.root.querySelector(`[data-spm-id="${id}"] [data-spm-move="${button.dataset.spmMove}"]`)?.focus();
    }

    keydown(event) {
        if (event.isComposing) return;
        if (event.key === 'Enter' && event.target === this.input) { event.preventDefault(); this.popup.querySelector('[data-spm-option]')?.click(); return; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); return this.close(); }
        if (!['ArrowDown', 'ArrowUp'].includes(event.key) || this.tab !== 'candidates') return;
        const buttons = [...this.popup.querySelectorAll('[data-spm-option]')];
        if (!buttons.length) return;
        event.preventDefault();
        const index = buttons.indexOf(document.activeElement);
        buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus();
    }

    render() {
        const byId = new Map(this.items.map(item => [Number(item.id), item]));
        const labels = this.selectedIds.slice(0, 3).map((id, index) => `<span class="spm-tag"><b>${index + 1}</b>${escape(byId.get(id)?.subject_name || byId.get(id)?.name || `签名 ${id}`)}</span>`).join('');
        this.trigger.innerHTML = `<span class="spm-values">${labels || '<span class="spm-placeholder">搜索并选择签名…</span>'}${this.selectedIds.length > 3 ? `<small>另有 ${this.selectedIds.length - 3} 项</small>` : ''}</span><span aria-hidden="true">⌄</span>`;
        const identity = this.root.querySelector('[data-spm-identity]');
        identity.textContent = this.identityOn ? `本岗位 · ${this.identityLabels.join('、')}` : '全部可见签名';
        identity.setAttribute('aria-pressed', String(this.identityOn));
        this.root.querySelectorAll('[data-spm-tab]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.spmTab === this.tab));
            if (button.dataset.spmTab === 'selected') button.textContent = `已选 ${this.selectedIds.length} · 排序`;
        });
        this.root.querySelector('[data-spm-count]').textContent = `已选 ${this.selectedIds.length}/12 项`;
        this.root.querySelector('[data-spm-clear]').disabled = this.disabled || !this.selectedIds.length;
        this.renderLists();
    }

    renderLists() {
        const candidates = this.root.querySelector('[data-spm-options]');
        const selected = this.root.querySelector('[data-spm-selected]');
        const scroll = this.root.querySelector('.spm-scroll');
        const scrollTop = scroll.scrollTop;
        candidates.hidden = this.tab !== 'candidates';
        selected.hidden = this.tab !== 'selected';
        const visible = this.items.filter(item => (!this.identityOn || !this.identityLabels.length || item.identity_match !== false || item.signature_kind === 'stamp')
            && (!this.term || `${item.subject_name || ''} ${item.name || ''} ${item.identity_label || ''}`.toLocaleLowerCase().includes(this.term)));
        candidates.innerHTML = ['personal', 'stamp'].map(kind => {
            const group = visible.filter(item => (item.signature_kind === 'stamp' ? 'stamp' : 'personal') === kind);
            if (!group.length) return '';
            return `<div class="spm-group" role="presentation">${kind === 'stamp' ? '特殊签名 · 无需申请' : '个人签名'}</div>` + group.map(item => {
                const index = this.selectedIds.indexOf(Number(item.id));
                return `<button type="button" role="option" aria-selected="${index >= 0}" data-spm-option="${Number(item.id)}" ${this.disabled ? 'disabled' : ''}>
                    <span class="spm-check">${index >= 0 ? index + 1 : ''}</span><span class="spm-candidate"><strong>${escape(item.subject_name || item.name)}</strong><small>${escape([item.identity_label, item.scope_label].filter(Boolean).join(' · '))}</small></span>
                    <span class="spm-access ${item.can_use ? 'is-ready' : ''}">${kind === 'stamp' ? '无需申请' : item.can_use ? '可使用' : '需申请'}</span></button>`;
            }).join('');
        }).join('');
        const byId = new Map(this.items.map(item => [Number(item.id), item]));
        selected.innerHTML = this.selectedIds.map((id, index) => {
            const item = byId.get(id);
            return `<li data-spm-id="${id}" draggable="${!this.disabled}"><span aria-hidden="true">⠿</span><b class="spm-order">${index + 1}</b><span class="spm-selected-name">${escape(item?.subject_name || item?.name || `签名 ${id}`)}</span><button type="button" data-spm-move="-1" aria-label="上移" ${index === 0 || this.disabled ? 'disabled' : ''}>↑</button><button type="button" data-spm-move="1" aria-label="下移" ${index === this.selectedIds.length - 1 || this.disabled ? 'disabled' : ''}>↓</button><button type="button" data-spm-remove aria-label="移除" ${this.disabled ? 'disabled' : ''}>×</button></li>`;
        }).join('');
        const empty = this.root.querySelector('[data-spm-empty]');
        empty.hidden = this.tab === 'candidates' ? visible.length > 0 : this.selectedIds.length > 0;
        empty.textContent = this.tab === 'selected' ? '尚未选择签名' : '没有匹配项，可调整关键词或显示全部可见签名。';
        scroll.scrollTop = scrollTop;
    }

    destroy() { clearTimeout(this.searchTimer); this.abort.abort(); if (this.popup.matches(':popover-open')) this.popup.hidePopover(); }
}
