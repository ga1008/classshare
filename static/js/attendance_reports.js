import { apiFetch } from './api.js';

const API = '/api/attendance-reports';
const PAGE = '/manage/archive/attendance-reports';
const controllers = new WeakMap();
const labels = { CHECKED: '出勤', UNCHECKED: '缺课', SICK_LEAVE: '病假', PERSONAL_LEAVE: '事假', LATE_OR_EARLY: '迟到或早退', UNKNOWN: '待核实', NOT_APPLICABLE: '不适用' };
const states = { queued: '排队中', exporting: '下载中', cached: '原件已缓存', source_cached: '原件已缓存', parsing: '解析中', processing: '处理中', needs_review: '待核对', validated: '核验通过待确认', confirmed: '已确认', failed: '失败', cancelled: '已取消', running: '处理中', retry_wait: '等待重试', result_ready: '结果待交付', succeeded: '已完成', dead_letter: '失败', review_required: '需要核对' };
const activeStates = new Set(['queued', 'running', 'exporting', 'parsing', 'processing', 'retry_wait', 'result_ready']);
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const positive = (value, fallback = 1) => Math.max(1, Number.parseInt(value, 10) || fallback);
const items = data => Array.isArray(data?.items) ? data.items : [];
const key = () => globalThis.crypto?.randomUUID?.() || `attendance-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const date = value => value ? String(value).replace('T', ' ').replace(/\.\d+(?:Z)?$/, '').slice(0, 19) : '—';
const sourcePath = (reportId, versionId, download = false) => `${API}/${encodeURIComponent(reportId)}/versions/${encodeURIComponent(versionId)}/source.pdf?download=${download ? 1 : 0}`;
const detailPath = (id, params = {}) => `${PAGE}/${encodeURIComponent(id)}${Object.keys(params).length ? `?${new URLSearchParams(params)}` : ''}`;
const empty = (title, copy = '', action = '') => `<div class="att-empty"><strong>${esc(title)}</strong><p>${esc(copy)}</p>${action}</div>`;
const button = (action, title, extra = '', className = '') => `<button type="button" class="att-btn ${className}" data-att-action="${esc(action)}" ${extra}>${esc(title)}</button>`;
const badge = value => `<span class="att-badge att-badge--${value === 'confirmed' || value === 'succeeded' ? 'good' : ['failed', 'dead_letter'].includes(value) ? 'error' : ['needs_review', 'validated', 'review_required'].includes(value) ? 'warn' : 'muted'}">${esc(states[value] || value || '待下载')}</span>`;
async function request(path, options = {}) {
    try { return await apiFetch(path.startsWith('/api/') ? path : `${API}${path}`, { silent: true, ...options }); }
    catch (error) { if (options.signal?.aborted) throw new DOMException('Request superseded', 'AbortError'); throw error; }
}
function query(values) {
    const params = new URLSearchParams();
    Object.entries(values).forEach(([name, value]) => { if (value !== '' && value !== undefined && value !== null) params.set(name, String(value)); });
    return params.toString();
}
function notify(node, text, kind = 'info') { if (!node) return; node.textContent = text; node.dataset.kind = kind; node.hidden = !text; }
function statusOf(row) { return ['queued', 'exporting', 'failed', 'cancelled'].includes(row.source_state) ? row.source_state : row.parse_state || row.source_state || row.state || 'queued'; }
function options(select, values, emptyLabel, selected = '') {
    const list = (values || []).map(value => typeof value === 'object' ? { value: String(value.value ?? value.id ?? ''), label: value.label ?? value.name ?? value.value ?? '' } : { value: String(value), label: String(value) });
    if (selected && !list.some(value => value.value === String(selected))) list.push({ value: String(selected), label: String(selected) });
    select.innerHTML = `<option value="">${esc(emptyLabel)}</option>${list.map(value => `<option value="${esc(value.value)}">${esc(value.label)}</option>`).join('')}`;
    select.value = selected;
}
function paginate(node, total, page, pageSize, scope, label = '条', allowSize = false) {
    const pages = Math.max(1, Math.ceil(Number(total || 0) / pageSize));
    node.innerHTML = `${allowSize ? `<label>每页<select data-att-page-size="${scope}" aria-label="每页数量">${[10, 25, 50, 100].map(n => `<option value="${n}" ${n === pageSize ? 'selected' : ''}>${n}条</option>`).join('')}</select></label>` : ''}<span>共 ${Number(total || 0)} ${esc(label)} · 第 ${page} / ${pages} 页</span>${button('page', '上一页', `data-scope="${scope}" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}`)}${button('page', '下一页', `data-scope="${scope}" data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}`)}`;
}
function wireTabs(root, choose) {
    root.addEventListener('keydown', event => {
        const tab = event.target.closest('[data-att-tab]');
        if (!tab) return;
        const tabs = $$('[data-att-tab]', root), index = tabs.indexOf(tab);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : null;
        if (next !== null) { event.preventDefault(); tabs.forEach(item => item.tabIndex = -1); tabs[next].tabIndex = 0; tabs[next].focus(); }
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(tab.dataset.attTab); }
    });
}

/** The source picker is shared by the archive and the classroom; it owns no modal. */
class SourcePanel {
    constructor(container, { offeringId = '', onQueued = () => {} } = {}) {
        this.root = container; this.offeringId = offeringId; this.onQueued = onQueued; this.bindings = []; this.candidates = []; this.active = false; this.epoch = 0;
        container.classList.add('att-classroom');
        container.innerHTML = `<div class="att-notice" data-source-message role="status" aria-live="polite" hidden></div><div data-source-identity></div><form class="att-source-form" data-source-form><label>学年<input name="year" placeholder="例如 2025-2026" inputmode="numeric" pattern="[0-9]{4}-[0-9]{4}" required></label><label>学期<select name="term" required><option value="">请选择学期</option><option value="1">第一学期</option><option value="2">第二学期</option></select></label><div class="att-toolbar att-wide">${button('refresh-source', '查询该学期授课教学班')}<a class="att-btn" href="/manage/me/credentials">智慧课堂账号设置</a></div></form><div data-source-options class="att-source-options"></div><div class="att-toolbar">${button('export-source', '导出并解析', 'disabled', 'att-btn--primary')}<span class="att-muted">导出所选教学班全部点名，关闭窗口后继续处理。</span></div><div data-source-result></div>`;
        this.form = $('[data-source-form]', container); this.message = $('[data-source-message]', container);
        container.addEventListener('click', event => { const action = event.target.closest('[data-att-action]')?.dataset.attAction; if (action === 'refresh-source') this.refresh(); if (action === 'export-source') this.export(); });
        container.addEventListener('change', event => {
            if (event.target.matches('[name=source]')) $('[data-att-action=export-source]', container).disabled = false;
            if (event.target.matches('[name=year],[name=term]')) { this.candidates = []; this.epoch += 1; this.abort?.abort(); this.renderSources(); }
        });
        this.form.addEventListener('submit', event => { event.preventDefault(); this.refresh(); });
    }
    async activate() { this.active = true; await this.load(); }
    deactivate() { this.active = false; this.abort?.abort(); this.epoch += 1; }
    selected() {
        const value = $('[name=source]:checked', this.root)?.value || '';
        return value.startsWith('b:') ? { binding: this.bindings.find(row => String(row.id) === value.slice(2)) } : { candidate: this.candidates.find((_, index) => String(index) === value.slice(2)) };
    }
    renderSources(preferred = '') {
        const year = this.form.elements.year.value, term = this.form.elements.term.value;
        const bindings = this.bindings.filter(row => (!year || row.academic_year === year) && (!term || String(row.academic_term) === term));
        const rows = bindings.map(row => ({ value: `b:${row.id}`, row, saved: true })).concat(this.candidates.map((row, index) => ({ value: `c:${index}`, row })));
        const selected = preferred || (rows.length === 1 && rows[0].saved ? rows[0].value : '');
        $('[data-source-options]', this.root).innerHTML = rows.length ? rows.map(({ row, value, saved }) => `<label class="att-source-option"><input type="radio" name="source" value="${esc(value)}" ${selected === value ? 'checked' : ''}><span><strong>${esc(row.course_name)} · ${esc(row.teaching_class_name || row.remote_class_name || '教学班')}</strong><small>${esc(row.academic_year)} · 第${esc(row.academic_term)}学期 · ${esc(row.course_code || '')}${saved ? ' · 已保存来源' : ''}</small></span></label>`).join('') : empty('请选择准确的学年学期', '查询后选择授课教学班；同名课程的不同教学班需分别确认。');
        $('[data-att-action=export-source]', this.root).disabled = !selected || this.archiveEnabled === false || this.credentialAvailable === false;
    }
    async load() {
        const epoch = ++this.epoch; this.abort?.abort(); this.abort = new AbortController();
        try {
            const data = await request(`/source-options?${query({ class_offering_id: this.offeringId })}`, { signal: this.abort.signal });
            if (!this.active || epoch !== this.epoch) return;
            this.bindings = data.bindings || [];
            this.archiveEnabled = data.archive_enabled !== false; this.parseEnabled = data.parse_enabled !== false; this.credentialAvailable = data.credential_available !== false;
            this.form.hidden = !this.archiveEnabled;
            $('[data-source-options]', this.root).hidden = !this.archiveEnabled;
            $('[data-att-action=export-source]', this.root).closest('.att-toolbar').hidden = !this.archiveEnabled;
            $('[data-att-action=export-source]', this.root).textContent = this.parseEnabled ? '导出并解析' : '导出并缓存原件';
            if (!this.form.elements.year.value) this.form.elements.year.value = data.year || this.bindings[0]?.academic_year || '';
            if (!this.form.elements.term.value) this.form.elements.term.value = String(data.term || this.bindings[0]?.academic_term || '');
            $('[data-source-identity]', this.root).innerHTML = `<p class="att-muted">${data.credential_available === false ? '请先配置并验证智慧课堂账号，再查询授课教学班。' : this.offeringId ? '已带入当前课堂。请核对学年学期与授课教学班。' : '选择原始点名来源，历史学期可直接查询。'}</p>`;
            $('[data-att-action=refresh-source]', this.root).disabled = data.credential_available === false;
            this.renderSources();
            notify(this.message, this.archiveEnabled ? '' : '当前暂停新导出，已有档案和原件仍可查看。');
        } catch (error) { if (error.name !== 'AbortError' && epoch === this.epoch) notify(this.message, error.message || '来源读取失败，请重新打开重试。', 'error'); }
    }
    async refresh() {
        if (this.busy || this.archiveEnabled === false || !this.form.reportValidity()) return;
        const year = this.form.elements.year.value.trim(), term = this.form.elements.term.value;
        const years = year.split('-').map(Number);
        if (years[1] !== years[0] + 1) { notify(this.message, '请输入连续的学年，例如 2025-2026。', 'error'); return; }
        const epoch = ++this.epoch; this.busy = true; this.abort?.abort(); this.abort = new AbortController();
        const action = $('[data-att-action=refresh-source]', this.root); action.disabled = true; notify(this.message, '正在查询该学期授课教学班…');
        try {
            const data = await request('/source-options/refresh', { method: 'POST', body: { year, term: Number(term), ...(this.offeringId ? { class_offering_id: Number(this.offeringId) } : {}) }, signal: this.abort.signal });
            if (!this.active || epoch !== this.epoch) return;
            this.candidates = items(data); this.renderSources(); notify(this.message, this.candidates.length ? `找到 ${this.candidates.length} 个授课教学班，请核对后选择。` : '该学期没有授课教学班，请核对学期和智慧课堂账号。');
        } catch (error) { if (error.name !== 'AbortError' && epoch === this.epoch) notify(this.message, error.message, 'error'); }
        finally { this.busy = false; action.disabled = false; }
    }
    async export() {
        if (this.busy || this.archiveEnabled === false || this.credentialAvailable === false) return;
        const selected = this.selected(); if (!selected.binding && !selected.candidate) return;
        this.busy = true; const action = $('[data-att-action=export-source]', this.root); action.disabled = true;
        try {
            let binding = selected.binding;
            if (!binding) {
                notify(this.message, '正在确认来源并保存关联…');
                const data = await request('/source-bindings', { method: 'POST', body: { source_token: selected.candidate.source_token, ...(this.offeringId ? { class_offering_id: Number(this.offeringId) } : {}) } });
                binding = data.binding; this.bindings = [...this.bindings.filter(row => String(row.id) !== String(binding.id)), binding]; this.candidates = []; this.renderSources(`b:${binding.id}`);
            }
            const requestKey = this.requestKey || (this.requestKey = key());
            const result = await request('/exports', { method: 'POST', body: { binding_id: binding.id, expected_binding_revision: binding.revision, idempotency_key: requestKey } });
            this.requestKey = null;
            notify(this.message, '导出任务已受理，可离开页面，稍后查看原件与解析结果。');
            $('[data-source-result]', this.root).innerHTML = `<a class="att-btn" href="${esc(detailPath(result.report_id))}">前往签到统计表查看进度</a>`;
            this.onQueued(result);
        } catch (error) { if (error.status && error.status < 500) this.requestKey = null; notify(this.message, error.message || '提交未完成，可安全重试。', 'error'); }
        finally { this.busy = false; action.disabled = false; }
    }
}

export function initClassroomAttendancePanel(container) {
    if (!container) return null;
    if (controllers.has(container)) return controllers.get(container);
    container.classList.add('att-classroom');
    container.innerHTML = `<div class="att-card"><div class="att-toolbar"><h3>智慧课堂签到</h3><a class="att-btn" href="${PAGE}?offering=${encodeURIComponent(container.dataset.classOfferingId || '')}">全部签到档案</a></div><div data-classroom-attendance-recent></div></div><div class="att-card"><h3>导出原始点名记录</h3><div data-classroom-attendance-source></div></div>`;
    let active = false, timer = 0, epoch = 0, abort, delay = 2000;
    const source = new SourcePanel($('[data-classroom-attendance-source]', container), { offeringId: container.dataset.classOfferingId, onQueued: () => { delay = 2000; loadRecent(); } });
    async function loadRecent() {
        clearTimeout(timer); if (!active || document.hidden) return;
        const current = ++epoch; abort?.abort(); abort = new AbortController();
        try {
            const data = await request(`?${query({ offering: container.dataset.classOfferingId, page: 1, page_size: 5 })}`, { signal: abort.signal });
            if (!active || current !== epoch) return;
            const rows = items(data);
            $('[data-classroom-attendance-recent]', container).innerHTML = rows.length ? rows.map(row => `<div class="att-toolbar"><div><strong>${esc(row.course_name)} · ${esc(row.teaching_class_name)}</strong><p class="att-muted">${esc(row.academic_year)} 第${esc(row.academic_term)}学期 · ${esc(date(row.updated_at))}</p></div>${badge(statusOf(row))}<a class="att-btn" href="${esc(detailPath(row.id))}">查看</a>${row.source_version_id && ['cached', 'source_cached'].includes(row.source_state) ? `<a class="att-btn" href="${esc(sourcePath(row.id, row.source_version_id, true))}">下载原件</a>` : ''}</div>`).join('') : empty('尚无签到档案', '在下方核对来源并导出，原件缓存后即可查看。');
            if (rows.some(row => activeStates.has(statusOf(row)) || activeStates.has(row.source_state))) { timer = setTimeout(loadRecent, delay); delay = Math.min(10000, delay + 3000); }
        } catch (error) { if (error.name !== 'AbortError' && current === epoch) $('[data-classroom-attendance-recent]', container).innerHTML = empty('暂时无法读取签到档案', error.message, button('retry-recent', '重试')); }
    }
    container.addEventListener('click', event => { if (event.target.closest('[data-att-action=retry-recent]')) loadRecent(); });
    const control = {
        activate() { active = true; delay = 2000; source.activate(); loadRecent(); },
        deactivate() { active = false; clearTimeout(timer); epoch += 1; abort?.abort(); source.deactivate(); },
        destroy() { this.deactivate(); document.removeEventListener('visibilitychange', visible); controllers.delete(container); },
    };
    const visible = () => { if (active && !document.hidden) loadRecent(); else clearTimeout(timer); };
    document.addEventListener('visibilitychange', visible); controllers.set(container, control); control.activate(); return control;
}

class ArchiveController {
    constructor(root) {
        this.root = root; this.reportId = root.dataset.reportId || ''; this.listPage = 1; this.pageSize = 25; this.tab = 'overview'; this.matrixPage = 1; this.studentPage = 1; this.columnPage = 1; this.reviewPage = 1; this.epochs = {}; this.aborts = {}; this.pollDelay = 2000;
        this.source = new SourcePanel($('[data-att-source-content]', root), { offeringId: root.dataset.classOfferingId || new URLSearchParams(location.search).get('offering') || '', onQueued: result => { notify(this.notice, '任务已提交，原件缓存后即可查看。'); this.reportId ? this.loadDetail() : this.loadList(); } });
        this.notice = $('[data-att-notice]', root); this.filters = $('[data-att-filters]', root); this.evidence = $('[data-att-evidence-dialog]', root); this.reviewForm = $('[data-att-review-form]', root);
        this.setup();
        if (root.dataset.archiveEnabled === 'false') $$('[data-att-action=open-source]', root).forEach(node => node.hidden = true);
        if (this.reportId) { $('[data-att-list-view]', root).hidden = true; $('[data-att-detail-view]', root).hidden = false; this.loadDetail(); } else { this.readURL(); this.loadList(); }
    }
    async get(path, lane) { this.aborts[lane]?.abort(); const controller = this.aborts[lane] = new AbortController(); return request(path, { signal: controller.signal }); }
    next(lane) { return this.epochs[lane] = (this.epochs[lane] || 0) + 1; }
    current(lane, epoch) { return this.epochs[lane] === epoch; }
    setup() {
        this.root.addEventListener('click', event => {
            const tab = event.target.closest('[data-att-tab]'); if (tab) { this.chooseTab(tab.dataset.attTab); return; }
            const target = event.target.closest('[data-att-action]'); if (target && !target.closest('[data-att-source-content]')) this.action(target.dataset.attAction, target);
        });
        this.filters.addEventListener('submit', event => event.preventDefault());
        this.filters.addEventListener('change', event => {
            const name = event.target.name;
            if (name === 'year' || name === 'term') { this.filters.elements.course.value = ''; this.filters.elements.teaching_class.value = ''; }
            if (name === 'term' || name === 'course') this.filters.elements.teaching_class.value = '';
            this.listPage = 1; this.writeURL(); this.loadList();
        });
        this.filters.addEventListener('input', event => { if (event.target.name === 'q') { clearTimeout(this.searchTimer); this.searchTimer = setTimeout(() => { this.listPage = 1; this.writeURL(); this.loadList(); }, 300); } });
        this.filters.addEventListener('reset', () => { setTimeout(() => { this.listPage = 1; this.offeringFilter = ''; this.writeURL(); this.loadList(); }, 0); });
        $('[data-att-sort]', this.root).addEventListener('change', () => { this.listPage = 1; this.writeURL(); this.loadList(); });
        this.root.addEventListener('change', event => {
            if (event.target.dataset.attPageSize) { this.pageSize = Number(event.target.value); this.listPage = 1; this.writeURL(); this.loadList(); }
            if (event.target.matches('[data-att-version]')) { this.selectedVersion = event.target.value; this.selectedRun = ''; this.chooseRunForVersion(); this.renderDetail(); this.loadPanel(); this.writeDetailURL(); }
            if (event.target.matches('[data-att-run]')) { this.selectedRun = event.target.value; this.renderDetail(); this.loadPanel(); this.writeDetailURL(); }
            if (event.target.matches('[data-att-matrix-quality]')) { this.matrixPage = 1; this.loadMatrix(); }
        });
        ['matrix', 'student'].forEach(type => $('[data-att-' + type + '-query]', this.root).addEventListener('input', () => { clearTimeout(this[type + 'Timer']); this[type + 'Timer'] = setTimeout(() => { if (type === 'matrix') { this.matrixPage = 1; this.loadMatrix(); } else { this.studentPage = 1; this.loadStudents(); } }, 300); }));
        this.reviewForm.addEventListener('input', () => { this.dirty = true; });
        this.reviewForm.addEventListener('change', event => {
            if (event.target.name === 'normalized_status') this.updateCellReviewFields();
            if (event.target.name === 'student_number' && this.reviewForm.elements.local_student_id) this.reviewForm.elements.local_student_id.value = '';
            if (event.target.name === 'source_datetime' && this.reviewForm.elements.local_session_id) this.reviewForm.elements.local_session_id.value = '';
        });
        this.reviewForm.addEventListener('submit', event => { event.preventDefault(); this.saveReview(); });
        this.evidence.addEventListener('cancel', event => { event.preventDefault(); this.closeEvidence(); });
        window.addEventListener('beforeunload', event => { if (this.dirty) { event.preventDefault(); event.returnValue = ''; } });
        window.addEventListener('popstate', () => { if (!this.reportId) { this.readURL(); this.loadList(); } else { const params = new URLSearchParams(location.search); this.selectedRun = params.get('run') || ''; this.selectedVersion = params.get('version') || ''; this.loadDetail(); } });
        document.addEventListener('visibilitychange', () => { clearTimeout(this.pollTimer); if (!document.hidden) this.reportId ? this.loadDetail() : this.loadList(); });
        wireTabs(this.root, tab => this.chooseTab(tab));
    }
    values() { return { ...Object.fromEntries(new FormData(this.filters)), page: this.listPage, page_size: this.pageSize, sort: $('[data-att-sort]', this.root).value, ...(this.offeringFilter ? { offering: this.offeringFilter } : {}) }; }
    readURL() { const params = new URLSearchParams(location.search); for (const field of this.filters.elements) if (field.name) { if (field.tagName === 'SELECT' && params.get(field.name) && ![...field.options].some(option => option.value === params.get(field.name))) field.add(new Option(params.get(field.name), params.get(field.name))); field.value = params.get(field.name) || (field.name === 'deleted' ? '0' : ''); } this.listPage = positive(params.get('page')); this.pageSize = [10, 25, 50, 100].includes(Number(params.get('page_size'))) ? Number(params.get('page_size')) : 25; this.offeringFilter = params.get('offering') || ''; $('[data-att-sort]', this.root).value = params.get('sort') || 'updated_desc'; }
    writeURL() { const values = this.values(); if (values.page === 1) delete values.page; if (values.page_size === 25) delete values.page_size; if (values.deleted === '0') delete values.deleted; if (values.sort === 'updated_desc') delete values.sort; history.replaceState(null, '', `${PAGE}${query(values) ? '?' + query(values) : ''}`); }
    writeDetailURL() { const values = { ...(this.selectedVersion ? { version: this.selectedVersion } : {}), ...(this.selectedRun ? { run: this.selectedRun } : {}) }; const back = new URLSearchParams(location.search).get('back'); if (back) values.back = back; history.replaceState(null, '', detailPath(this.reportId, values)); }
    async loadList() {
        const epoch = this.next('list'), values = this.values(); clearTimeout(this.pollTimer); $('[data-att-reports]', this.root).setAttribute('aria-busy', 'true');
        try {
            const [data, facets] = await Promise.all([this.get(`?${query(values)}`, 'list'), this.get(`/options?${query(values)}`, 'options')]);
            if (!this.current('list', epoch)) return;
            this.listData = data; this.listPage = positive(data.page, this.listPage);
            options(this.filters.elements.year, facets.years, '全部学年', values.year); options(this.filters.elements.course, facets.courses, '全部课程', values.course); options(this.filters.elements.teaching_class, facets.teaching_classes, '全部教学班', values.teaching_class);
            $('[data-att-count]', this.root).textContent = `共 ${Number(data.total || 0)} 份${values.deleted === '1' ? '已删除' : ''}档案`;
            $('[data-att-chips]', this.root).innerHTML = [...this.filters.elements].filter(field => field.name && field.value && !(field.name === 'deleted' && field.value === '0')).map(field => `<span class="att-chip">${esc(field.tagName === 'SELECT' ? field.selectedOptions[0]?.textContent : field.value)}</span>`).join('') + (this.offeringFilter ? '<span class="att-chip">当前课堂</span>' : '');
            const rows = items(data), returnQuery = location.search.slice(1);
            $('[data-att-reports]', this.root).innerHTML = rows.length ? `<div class="att-table-wrap"><table class="att-table att-list-table"><thead><tr><th>课程 / 教学班</th><th>学年学期</th><th>学生 × 点名</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${rows.map(row => `<tr><td><a href="${esc(detailPath(row.id, returnQuery ? { back: returnQuery } : {}))}"><strong>${esc(row.course_name || '未命名课程')}</strong></a><small>${esc(row.teaching_class_name || '未标教学班')} · ${esc(row.course_code || '')}</small></td><td data-label="学期">${esc(row.academic_year)} 第${esc(row.academic_term)}学期</td><td data-label="规模">${Number(row.student_count || 0)} × ${Number(row.session_count || 0)}</td><td>${badge(statusOf(row))}${row.confirmed_parse_run_id && row.parse_state !== 'confirmed' ? '<small>已有确认结果可用</small>' : ''}</td><td data-label="更新">${esc(date(row.updated_at))}</td><td><div class="att-row-actions"><a class="att-btn" href="${esc(detailPath(row.id, returnQuery ? { back: returnQuery } : {}))}">查看</a>${row.source_version_id && ['cached', 'source_cached'].includes(row.source_state) ? `<a class="att-btn" href="${esc(sourcePath(row.id, row.source_version_id, true))}">原件</a>` : ''}${row.deleted_at ? button('restore-list', '恢复', `data-id="${esc(row.id)}" data-revision="${esc(row.revision)}"`) : ''}</div></td></tr>`).join('')}</tbody></table></div>` : empty(values.q || values.year || values.term || values.course || values.teaching_class || values.status || this.offeringFilter ? '没有符合条件的档案' : values.deleted === '1' ? '暂无已删除档案' : '还没有签到统计表', '可以调整筛选条件，或从智慧课堂导出原始点名记录。', button('open-source', '从智慧课堂导出', '', 'att-btn--primary'));
            paginate($('[data-att-list-pagination]', this.root), data.total, this.listPage, this.pageSize, 'list', '份档案', true);
            if (this.root.dataset.archiveEnabled === 'false') $$('[data-att-action=open-source]', this.root).forEach(node => node.hidden = true);
            if (rows.some(row => activeStates.has(statusOf(row)) || activeStates.has(row.source_state))) this.pollTimer = setTimeout(() => this.loadList(), 5000);
        } catch (error) { if (error.name !== 'AbortError' && this.current('list', epoch)) { $('[data-att-reports]', this.root).innerHTML = empty('档案暂时无法读取', error.message, button('reload-list', '重试')); $('[data-att-count]', this.root).textContent = '读取失败'; } }
        finally { if (this.current('list', epoch)) $('[data-att-reports]', this.root).removeAttribute('aria-busy'); }
    }
    chooseRunForVersion() { const available = (this.detail?.runs || []).filter(run => String(run.source_version_id) === String(this.selectedVersion)); if (!available.some(run => String(run.id) === String(this.selectedRun))) this.selectedRun = String(available[0]?.id || ''); }
    run() { return (this.detail?.runs || []).find(run => String(run.id) === String(this.selectedRun)); }
    version() { return (this.detail?.versions || []).find(version => String(version.id) === String(this.selectedVersion)); }
    async loadDetail() {
        const epoch = this.next('detail'); clearTimeout(this.pollTimer);
        try {
            const data = await this.get(`/${encodeURIComponent(this.reportId)}`, 'detail'); if (!this.current('detail', epoch)) return;
            this.detail = data;
            const params = new URLSearchParams(location.search);
            if (!this.selectedVersion) this.selectedVersion = params.get('version') || String(data.report.source_version_id || data.report.latest_source_version_id || data.versions?.[0]?.id || '');
            if (!this.selectedRun) this.selectedRun = params.get('run') || String(data.active_run_id || data.report.confirmed_parse_run_id || '');
            const requestedRun = data.runs?.find(run => String(run.id) === this.selectedRun);
            if (requestedRun && !params.get('version')) this.selectedVersion = String(requestedRun.source_version_id);
            this.chooseRunForVersion();
            this.renderDetail(); if (!this.dirty) this.loadPanel();
            const back = params.get('back'); $('[data-att-back]', this.root).href = PAGE + (back ? '?' + new URLSearchParams(back).toString() : '');
            if (data.jobs?.some(job => activeStates.has(job.status))) { this.pollTimer = setTimeout(() => this.loadDetail(), this.pollDelay); this.pollDelay = Math.min(10000, this.pollDelay + 3000); }
        } catch (error) { if (error.name !== 'AbortError' && this.current('detail', epoch)) notify(this.notice, error.message || '档案读取失败，请重试。', 'error'); }
    }
    renderDetail() {
        const { report, versions = [], runs = [], jobs = [] } = this.detail, run = this.run(), version = this.version(), cached = version?.source_file_hash && version?.source_state === 'cached';
        $('[data-att-detail-head]', this.root).innerHTML = `<section class="att-card"><h3 class="att-detail-title">${esc(report.course_name)} · ${esc(report.teaching_class_name)}</h3><div class="att-identity"><span>${esc(report.academic_year)} 第${esc(report.academic_term)}学期</span><span>${esc(report.course_code)}</span><span>该教学班全部点名</span>${report.deleted_at ? '<span class="att-badge att-badge--error">已删除</span>' : ''}</div><div class="att-version-controls"><label>原件版本<select data-att-version>${versions.map(item => `<option value="${esc(item.id)}" ${String(item.id) === this.selectedVersion ? 'selected' : ''}>第${esc(item.version_no)}版 · ${esc(date(item.fetched_at))} · ${esc(states[item.source_state] || item.source_state)}</option>`).join('')}</select></label><label>解析版本<select data-att-run>${runs.filter(item => String(item.source_version_id) === this.selectedVersion).map(item => `<option value="${esc(item.id)}" ${String(item.id) === this.selectedRun ? 'selected' : ''}>解析${esc(item.run_no || item.id)} · ${esc(states[item.state] || item.state)}</option>`).join('') || '<option value="">尚无解析</option>'}</select></label>${cached ? `<a class="att-btn" href="${esc(sourcePath(report.id, version.id))}" target="_blank" rel="noopener">预览原件</a><a class="att-btn" href="${esc(sourcePath(report.id, version.id, true))}">下载原件</a>` : ''}</div><div class="att-toolbar" style="margin-top:16px;margin-bottom:0">${run ? badge(run.state) : badge(version?.source_state)}${report.confirmed_parse_run_id && String(report.confirmed_parse_run_id) !== this.selectedRun ? '<span class="att-muted">当前查看候选，另有已确认结果可用</span>' : ''}${!report.deleted_at ? `${cached ? button('reparse', '用原件重新解析') : ''}${button('reexport', '重新导出')}${run && run.state !== 'confirmed' ? button('confirm-run', '确认解析结果', `${run.validation?.can_confirm ? '' : 'disabled'}`, 'att-btn--primary') : ''}${button('delete-report', '删除档案', '', 'att-btn--danger')}` : button('restore-report', '恢复档案', '', 'att-btn--primary')}</div></section>${jobs.filter(job => activeStates.has(job.status) || ['failed', 'dead_letter', 'review_required'].includes(job.status)).map(job => `<div class="att-notice"><div class="att-toolbar" style="margin:0"><span>${job.task_type === 'attendance_export' ? '原件导出' : '解析核验'} · ${esc(states[job.status] || job.status)}</span>${activeStates.has(job.status) ? button('cancel-job', '取消任务', `data-id="${esc(job.id)}" data-revision="${esc(job.revision || '')}"`) : ''}</div>${job.error ? `<p>${esc(job.error)}</p>` : ''}</div>`).join('')}`;
        const metrics = run?.validation || {};
        $('[data-att-panel=overview]', this.root).innerHTML = run ? `<div class="att-metrics">${[['学生', metrics.student_count ?? report.student_count ?? 0], ['点名', metrics.session_count ?? report.session_count ?? 0], ['待核实', metrics.unknown_count ?? 0], ['来源冲突', metrics.conflict_count ?? 0]].map(([label, value]) => `<div class="att-metric"><span>${label}</span><strong>${Number(value)}</strong></div>`).join('')}</div><section class="att-card"><h3>解析与核验</h3><p>${run.ai_used ? '已执行 AI 解析' : '尚未完成 AI 解析'} · ${esc(states[run.state] || run.state)}</p><p class="att-muted">${Number(metrics.unmapped_student_count || 0)} 名学生、${Number(metrics.unmapped_session_count || 0)} 次点名尚未关联本地记录。未关联历史事实不自动计入课堂成绩。</p><p class="att-muted">原件缓存于 ${esc(date(version?.fetched_at))}${run.confirmed_at ? ` · 确认于 ${esc(date(run.confirmed_at))}` : ''}</p>${this.validationHTML(metrics)}${this.coverageHTML(run)}</section><section class="att-card"><h3>统计口径</h3><p>按本次原件全部点名逐格统计。病假、事假分别计数；空白、缺格与冲突标为待核实。</p><p class="att-muted">完整率 = 已知状态 / 适用点名。存在未知时，只展示已知记录出勤率，完整来源出勤率留空。零次点名表示暂无记录。</p></section>` : empty(cached ? '原件已保存，等待解析' : '正在准备原件', cached ? '可以先预览或下载原件；AI 失败不会移除已缓存 PDF。' : '后台任务结束后可在这里查看原件与逐次签到。');
        $('[data-att-legend]', this.root).innerHTML = Object.entries(labels).map(([value, label]) => `<span class="att-cell att-status-${value}">${esc(label)}</span>`).join('');
        if (this.root.dataset.archiveEnabled === 'false') $$('[data-att-action=reexport]', this.root).forEach(node => node.hidden = true);
        if (this.root.dataset.parseEnabled === 'false') $$('[data-att-action=reparse]', this.root).forEach(node => node.hidden = true);
        const binding = this.detail.binding || {};
        if (binding.class_offering_id && report.confirmed_parse_run_id && !report.deleted_at) $('[data-att-detail-head]', this.root).insertAdjacentHTML('beforeend', `<div class="att-card"><div class="att-toolbar" style="margin:0"><div><strong>课堂出勤成绩来源</strong><p class="att-muted">${binding.is_grade_source ? '已采用此来源。' : '同一课堂有多个来源时，需明确选择采用哪一份。'}切换只影响以后生成的出勤成绩，已保存和已发布成绩保持原值。</p></div>${binding.is_grade_source ? '<span class="att-badge att-badge--good">已采用</span>' : button('use-grade-source', '用作课堂出勤成绩来源')}</div></div>`);
    }
    coverageHTML(run) { const coverage = run.coverage || {}; const ai = run.ai_coverage || {}; const count = value => Array.isArray(value) ? value.length : value; return `<p class="att-muted">页面覆盖：${esc(count(coverage.processed_pages) ?? coverage.page_count ?? '待确认')} / ${esc(coverage.total_pages ?? this.version()?.source_page_count ?? '—')}；已识别单元格 ${esc(run.validation?.cell_count ?? 0)}。${ai.processed_blocks != null ? `AI块覆盖 ${Number(count(ai.processed_blocks))} / ${Number(ai.total_blocks || 0)}。` : ''}</p>`; }
    validationHTML(validation) { const rows = [...(validation.blockers || []).map(item => ({ ...item, blocking: true })), ...(validation.warnings || [])]; return rows.length ? `<ul class="att-validation">${rows.map(item => `<li>${item.blocking ? '<span class="att-badge att-badge--warn">待处理</span> ' : ''}${esc(item.message || item.code || item)}</li>`).join('')}</ul>` : '<p class="att-muted">暂无阻断核验项。</p>'; }
    chooseTab(tab) { this.tab = tab; $$('[data-att-tab]', this.root).forEach(button => { const active = button.dataset.attTab === tab; button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1; }); $$('[data-att-panel]', this.root).forEach(panel => panel.hidden = panel.dataset.attPanel !== tab); this.loadPanel(); }
    loadPanel() { if (this.tab === 'matrix') this.loadMatrix(); if (this.tab === 'students') this.loadStudents(); if (this.tab === 'review') this.loadReviews(); }
    runPath() { return `/${encodeURIComponent(this.reportId)}/runs/${encodeURIComponent(this.selectedRun)}`; }
    async loadMatrix() {
        const epoch = this.next('matrix'), scope = `${this.selectedRun}:${this.selectedVersion}`; if (!this.run()) { $('[data-att-matrix-content]', this.root).innerHTML = empty('尚无可查看的解析', '原件缓存后在后台解析。'); return; }
        try {
            const [students, sessions] = await Promise.all([
                this.get(`${this.runPath()}/students?${query({ page: this.matrixPage, page_size: 25, q: $('[data-att-matrix-query]', this.root).value, quality_state: $('[data-att-matrix-quality]', this.root).value })}`, 'matrix-students'),
                this.get(`${this.runPath()}/sessions?${query({ page: this.columnPage, page_size: 20 })}`, 'matrix-sessions'),
            ]);
            if (!this.current('matrix', epoch) || scope !== `${this.selectedRun}:${this.selectedVersion}`) return;
            const rows = items(students), columns = items(sessions);
            const cells = rows.length && columns.length ? await this.get(`${this.runPath()}/cells?${query({ student_ids: rows.map(row => row.id).join(','), session_ids: columns.map(column => column.id).join(',') })}`, 'matrix-cells') : { items: [] };
            if (!this.current('matrix', epoch) || scope !== `${this.selectedRun}:${this.selectedVersion}`) return;
            this.cells = new Map(items(cells).map(cell => [String(cell.id), cell])); this.matrixStudents = rows; this.matrixSessions = columns;
            const lookup = new Map(items(cells).map(cell => [`${cell.student_row_id}:${cell.session_column_id}`, cell]));
            $('[data-att-matrix-content]', this.root).innerHTML = rows.length && columns.length ? `<div class="att-table-wrap att-matrix" tabindex="0" aria-label="逐次签到表，可左右滚动"><table class="att-table"><thead><tr><th>学生 / 学号</th>${columns.map(column => `<th>${esc(column.source_header || date(column.source_datetime))}<small>${column.mapping_state === 'matched' ? '已关联课次' : '未关联课次'}</small>${button('review-session', '核对课次', `data-id="${esc(column.id)}"`, 'att-btn--text')}</th>`).join('')}</tr></thead><tbody>${rows.map(student => `<tr><td><strong>${esc(student.source_name)}</strong><small>${esc(student.student_number)}</small>${button('review-student', student.identity_state === 'matched' ? '核对身份' : '关联学生', `data-id="${esc(student.id)}"`, 'att-btn--text')}</td>${columns.map(column => { const cell = lookup.get(`${student.id}:${column.id}`); const status = cell?.normalized_status || 'UNKNOWN'; return `<td>${cell ? `<button type="button" class="att-cell att-status-${esc(Object.hasOwn(labels, status) ? status : 'UNKNOWN')} ${cell.quality_state === 'conflict' ? 'att-quality-conflict' : ''}" data-att-action="evidence" data-id="${esc(cell.id)}" title="${esc(student.source_name)} · ${esc(column.source_header)} · ${esc(labels[status] || status)}${cell.quality_state === 'conflict' ? '（来源冲突）' : ''}">${esc(labels[status] || '待核实')}${cell.quality_state === 'conflict' ? ' !' : ''}</button>` : '<span class="att-cell att-status-UNKNOWN">待核实</span>'}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>` : empty('没有符合条件的记录', '调整学生或异常状态筛选条件后重试。');
            paginate($('[data-att-matrix-pagination]', this.root), students.total, this.matrixPage, 25, 'matrix', '名学生'); paginate($('[data-att-column-pagination]', this.root), sessions.total, this.columnPage, 20, 'columns', '次点名');
        } catch (error) { if (error.name !== 'AbortError' && this.current('matrix', epoch)) $('[data-att-matrix-content]', this.root).innerHTML = empty('签到矩阵读取失败', error.message, button('reload-matrix', '重试')); }
    }
    async loadStudents() {
        const epoch = this.next('students'); if (!this.run()) { $('[data-att-students-content]', this.root).innerHTML = empty('尚无学生统计', '等待原件解析完成。'); return; }
        try {
            const data = await this.get(`${this.runPath()}/students?${query({ page: this.studentPage, page_size: 25, q: $('[data-att-student-query]', this.root).value })}`, 'students'); if (!this.current('students', epoch)) return;
            this.summaryStudents = items(data);
            $('[data-att-students-content]', this.root).innerHTML = items(data).length ? `<div class="att-table-wrap"><table class="att-table"><thead><tr><th>姓名 / 学号</th><th>班级</th><th>出勤</th><th>缺课</th><th>病假</th><th>事假</th><th>迟到或早退</th><th>不适用</th><th>待核实</th><th>完整率</th><th>已知记录出勤率</th><th>完整来源出勤率</th></tr></thead><tbody>${items(data).map(student => { const summary = student.summary || {}, noRecords = Number(summary.applicable || 0) === 0; return `<tr><td><strong>${esc(student.source_name)}</strong><small>${esc(student.student_number)}</small>${button('review-student', student.identity_state === 'matched' ? '核对身份' : '关联学生', `data-id="${esc(student.id)}"`, 'att-btn--text')}</td><td>${esc(student.source_class_name)}</td>${['checked', 'absent', 'sick_leave', 'personal_leave', 'late_or_early', 'not_applicable', 'unknown'].map(status => `<td>${Number(summary[status] || 0)}</td>`).join('')}<td>${this.percent(summary.completeness_rate, noRecords)}</td><td>${this.percent(summary.known_attendance_rate, noRecords)}</td><td>${this.percent(summary.attendance_rate, noRecords)}</td></tr>`; }).join('')}</tbody></table></div>` : empty('没有找到该学生', '核对姓名或学号；历史原件与当前班级名单可能不同。');
            paginate($('[data-att-students-pagination]', this.root), data.total, this.studentPage, 25, 'students', '名学生');
        } catch (error) { if (error.name !== 'AbortError' && this.current('students', epoch)) $('[data-att-students-content]', this.root).innerHTML = empty('学生统计读取失败', error.message, button('reload-students', '重试')); }
    }
    percent(value, noRecords = false) { return noRecords ? '暂无适用点名' : value === null || value === undefined ? '待核实' : `${Number(value).toFixed(1)}%`; }
    async loadReviews() {
        const run = this.run(); if (!run) { $('[data-att-review-content]', this.root).innerHTML = empty('尚无核对记录'); return; }
        const epoch = this.next('reviews');
        try {
            const data = await this.get(`${this.runPath()}/reviews?${query({ page: this.reviewPage, page_size: 25 })}`, 'reviews'); if (!this.current('reviews', epoch)) return;
            $('[data-att-review-content]', this.root).innerHTML = `<section class="att-card"><h3>待处理事项</h3>${this.validationHTML(run.validation || {})}<p class="att-muted">在逐次签到中点击单元格，可查看原件对应页并核对。通过姓名或点名列下的核对入口，可更正来源文字、关联学生与课次。已确认结果的更正需要建立新的解析版本。</p>${button('show-matrix', '打开逐次签到')}</section><section class="att-card"><h3>核对轨迹</h3>${items(data).length ? `<ol class="att-timeline">${items(data).map(row => `<li><strong>${esc(({ review: '人工核对', confirm: '确认版本' })[row.event_type] || '版本操作')} · ${esc(date(row.created_at))}</strong><p>${esc(row.reason || '版本操作')}</p><small class="att-muted">操作人 ${esc(row.actor_name || row.actor_id || '教师')} · ${esc(({ cell: '签到记录', student: '学生身份', session: '点名课次', run: '解析版本' })[row.target_type] || '档案')}</small></li>`).join('')}</ol>` : '<p class="att-muted">本次解析还没有人工核对记录。</p>'}<nav class="att-pagination" data-att-review-pagination aria-label="核对记录分页"></nav></section>`;
            paginate($('[data-att-review-pagination]', this.root), data.total, this.reviewPage, 25, 'reviews', '次操作');
        } catch (error) { if (error.name !== 'AbortError' && this.current('reviews', epoch)) $('[data-att-review-content]', this.root).innerHTML = empty('核对记录读取失败', error.message, button('reload-reviews', '重试')); }
    }
    openEvidence(id) {
        const cell = this.cells?.get(String(id)); if (!cell) return;
        const student = this.matrixStudents.find(row => row.id === cell.student_row_id), session = this.matrixSessions.find(row => row.id === cell.session_column_id), page = positive(cell.evidence_page || cell.source_page);
        $('[data-att-cell-review-fields]', this.root).hidden = false;
        $('[data-att-identity-review-fields]', this.root).hidden = true;
        $('[data-att-identity-review-fields]', this.root).innerHTML = '';
        this.reviewContext = { targetType: 'cell', target: cell, runId: this.selectedRun, runRevision: this.run().revision, versionId: this.selectedVersion };
        $('[data-att-cell-source]', this.root).textContent = `原始文字：${cell.raw_text || cell.raw_status || '空白'}${cell.api_status ? `；API当前状态：${labels[cell.api_status] || cell.api_status}` : ''}${cell.ai_status ? `；AI状态：${labels[cell.ai_status] || cell.ai_status}` : ''}。定位页码和原始记录保留，核对只修改解释。`;
        this.reviewForm.elements.normalized_status.value = Object.hasOwn(labels, cell.normalized_status) ? cell.normalized_status : 'UNKNOWN'; this.reviewForm.elements.reason.value = '';
        this.reviewForm.elements.quality_state.value = cell.quality_state === 'resolved_historical_difference' ? cell.quality_state : 'conflict';
        this.reviewForm.elements.applicability_evidence.value = '';
        this.showEvidence(page, `${student?.source_name || ''} · ${student?.student_number || ''} · ${session?.source_header || ''}`);
        this.updateCellReviewFields();
    }
    updateCellReviewFields() {
        if (this.reviewContext?.targetType !== 'cell') return;
        const form = this.reviewForm, cell = this.reviewContext.target, status = form.elements.normalized_status.value;
        const difference = Boolean(cell.api_status && cell.api_status !== status) || cell.quality_state === 'conflict';
        $('[data-att-conflict-resolution]', this.root).hidden = !difference;
        $('[data-att-applicability]', this.root).hidden = status !== 'NOT_APPLICABLE';
        form.elements.applicability_evidence.required = status === 'NOT_APPLICABLE';
    }
    openIdentity(type, id) {
        const rows = type === 'student' ? (this.tab === 'students' ? this.summaryStudents : this.matrixStudents) : this.matrixSessions;
        const row = (rows || []).find(item => String(item.id) === String(id)); if (!row) return;
        this.reviewContext = { targetType: type, target: row, runId: this.selectedRun, runRevision: this.run().revision, versionId: this.selectedVersion };
        const identity = $('[data-att-identity-review-fields]', this.root), input = (name, label, value, inputType = 'text') => `<label>${esc(label)}<input name="${name}" type="${inputType}" value="${esc(value)}" maxlength="200" ${inputType === 'datetime-local' ? 'step="1"' : ''}></label>`;
        const localField = type === 'student' ? 'local_student_id' : 'local_session_id';
        identity.innerHTML = (type === 'student'
            ? input('source_name', '原件姓名', row.source_name) + input('student_number', '原件学号', row.student_number) + input('source_class_name', '原件班级', row.source_class_name)
            : input('source_header', '原件点名列标题', row.source_header) + input('source_datetime', '点名日期时间', String(row.source_datetime || '').replace(' ', 'T').slice(0, 19), 'datetime-local'))
            + `<label>${type === 'student' ? '关联本课堂学生' : '关联本课堂课次'}<select name="${localField}"><option value="">保持未关联</option>${(row.mapping_candidates || []).map(candidate => `<option value="${esc(candidate.id)}" ${String(candidate.id) === String(row[localField]) ? 'selected' : ''}>${esc(candidate.label)}</option>`).join('')}</select></label><p class="att-muted">${type === 'student' ? '只提供本课堂学号精确一致的候选。更正学号后先保存并重新打开，再选择对应学生。' : '候选来自本课堂同日课次。更正日期后先保存并重新打开，再关联课次。'}未关联不会丢失历史签到事实。</p>`;
        identity.hidden = false; $('[data-att-cell-review-fields]', this.root).hidden = true;
        this.reviewForm.elements.applicability_evidence.required = false; this.reviewForm.elements.reason.value = '';
        $('[data-att-cell-source]', this.root).textContent = '请对照原件核对来源文字。学生和课次的关联只对当前解析版本生效。';
        this.showEvidence(positive(row.source_page || row.evidence_page || row.evidence?.page), type === 'student' ? `${row.source_name || ''} · ${row.student_number || ''}` : row.source_header || '点名课次');
    }
    showEvidence(page, description) {
        this.dirty = false; this.reviewConflict = false;
        $('[data-att-evidence-label]', this.root).textContent = `${description} · 原件第 ${page} 页`;
        const href = sourcePath(this.reportId, this.selectedVersion) + `#page=${page}&view=FitH`;
        $('[data-att-evidence-frame]', this.root).src = href; $('[data-att-evidence-link]', this.root).href = href;
        const readonly = !['needs_review', 'validated'].includes(this.run().state) || Boolean(this.detail.report.deleted_at);
        for (const field of this.reviewForm.elements) field.disabled = readonly;
        notify($('[data-att-review-message]', this.root), readonly ? '当前版本已冻结。可关闭后用原件重新解析，在新候选中更正。' : '');
        this.evidence.showModal();
    }
    async closeEvidence() { if (this.saving) return; if (this.dirty && !(await this.ask('放弃本次尚未保存的核对修改？'))) return; this.dirty = false; this.evidence.close(); $('[data-att-evidence-frame]', this.root).removeAttribute('src'); if (this.reviewConflict) { this.reviewConflict = false; await this.loadDetail(); } }
    async saveReview() {
        if (this.saving || !this.reviewContext || !this.reviewForm.reportValidity()) return;
        this.saving = true; const submit = $('[data-att-save-review]', this.root); submit.disabled = true;
        const context = this.reviewContext;
        try {
            const form = this.reviewForm.elements; let changes;
            if (context.targetType === 'cell') {
                changes = { normalized_status: form.normalized_status.value };
                if (!$('[data-att-conflict-resolution]', this.root).hidden) changes.quality_state = form.quality_state.value;
                if (changes.normalized_status === 'NOT_APPLICABLE') changes.applicability_evidence = form.applicability_evidence.value.trim();
            } else if (context.targetType === 'student') changes = { student_number: form.student_number.value.trim(), source_name: form.source_name.value.trim(), source_class_name: form.source_class_name.value.trim(), local_student_id: form.local_student_id.value ? Number(form.local_student_id.value) : null };
            else changes = { source_header: form.source_header.value.trim(), source_datetime: form.source_datetime.value || null, local_session_id: form.local_session_id.value ? Number(form.local_session_id.value) : null };
            await request(`/${encodeURIComponent(this.reportId)}/runs/${encodeURIComponent(context.runId)}/review`, { method: 'PATCH', body: { target_type: context.targetType, target_id: context.target.id, changes, reason: form.reason.value.trim(), expected_revision: context.runRevision } });
            this.dirty = false; this.evidence.close(); $('[data-att-evidence-frame]', this.root).removeAttribute('src'); notify(this.notice, '核对结果已保存，统计和确认条件已更新。'); await this.loadDetail();
        } catch (error) { this.reviewConflict = error.status === 409; notify($('[data-att-review-message]', this.root), error.status === 409 ? '该解析已在另一窗口更新。本次输入已保留；请取消修改，重新打开当前记录对照后再保存。' : error.message, 'error'); }
        finally { this.saving = false; submit.disabled = false; }
    }
    ask(copy) { const dialog = $('[data-att-confirm-dialog]', this.root); if (dialog.open) return Promise.resolve(false); $('[data-att-confirm-copy]', this.root).textContent = copy; return new Promise(resolve => { dialog.returnValue = ''; dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }); dialog.showModal(); }); }
    async mutation(target, path, method, body, message) { if (target.disabled) return; target.disabled = true; try { const data = await request(path, { method, body }); if (data.source_version_id) this.selectedVersion = String(data.source_version_id); if (data.parse_run_id) this.selectedRun = String(data.parse_run_id); notify(this.notice, message); this.reportId ? await this.loadDetail() : await this.loadList(); } catch (error) { notify(this.notice, error.status === 409 ? `${error.message} 请检查最新版本后重试。` : error.message, 'error'); if (error.status === 409 && this.reportId) await this.loadDetail(); } finally { if (target.isConnected) target.disabled = false; } }
    async action(action, target) {
        if (action === 'open-source') { $('[data-att-source-box]', this.root).hidden = false; this.source.activate(); $('[data-att-source-box]', this.root).scrollIntoView({ behavior: 'smooth', block: 'start' }); }
        if (action === 'close-source') { $('[data-att-source-box]', this.root).hidden = true; this.source.deactivate(); }
        if (action === 'reload-list') this.loadList();
        if (action === 'reload-matrix' || action === 'show-matrix') this.chooseTab('matrix');
        if (action === 'reload-students') this.loadStudents();
        if (action === 'reload-reviews') this.loadReviews();
        if (action === 'page') { const page = positive(target.dataset.page); if (target.dataset.scope === 'list') { this.listPage = page; this.writeURL(); this.loadList(); } if (target.dataset.scope === 'matrix') { this.matrixPage = page; this.loadMatrix(); } if (target.dataset.scope === 'columns') { this.columnPage = page; this.loadMatrix(); } if (target.dataset.scope === 'students') { this.studentPage = page; this.loadStudents(); } if (target.dataset.scope === 'reviews') { this.reviewPage = page; this.loadReviews(); } }
        if (action === 'evidence') this.openEvidence(target.dataset.id);
        if (action === 'review-student' || action === 'review-session') this.openIdentity(action === 'review-student' ? 'student' : 'session', target.dataset.id);
        if (action === 'close-evidence' || action === 'cancel-review') this.closeEvidence();
        if (action === 'restore-list') return this.mutation(target, `/${encodeURIComponent(target.dataset.id)}/restore`, 'POST', { expected_revision: Number(target.dataset.revision) }, '档案已恢复。');
        if (!this.detail) return;
        const report = this.detail.report, run = this.run();
        if (action === 'use-grade-source' && await this.ask('将此教学班来源用于以后生成的课堂出勤成绩？已有成绩不会自动改变。')) return this.mutation(target, `/source-bindings/${encodeURIComponent(report.binding_id)}/grade-source`, 'POST', { expected_revision: this.detail.binding.revision }, '已选定课堂出勤成绩来源。');
        if (action === 'confirm-run' && await this.ask('确认当前原件与解析范围，保存为该档案的已确认结果？已发布成绩不会自动改变。')) return this.mutation(target, `${this.runPath()}/confirm`, 'POST', { expected_run_revision: run.revision, expected_report_revision: report.revision }, '当前解析版本已确认。');
        if (action === 'reparse') return this.mutation(target, `/${encodeURIComponent(this.reportId)}/versions/${encodeURIComponent(this.selectedVersion)}/parse-runs`, 'POST', { idempotency_key: key() }, '已用缓存原件开始新解析，旧确认结果继续可用。');
        if (action === 'reexport' && await this.ask('重新访问智慧课堂，导出该教学班全部点名？将保留现有原件与确认结果。')) return this.mutation(target, '/exports', 'POST', { binding_id: report.binding_id, expected_binding_revision: this.detail.binding.revision, idempotency_key: key() }, '新的导出任务已提交。');
        if (action === 'cancel-job' && await this.ask('取消这项后台任务？已经缓存的原件和已确认结果会保留。')) return this.mutation(target, `/jobs/${encodeURIComponent(target.dataset.id)}/cancel`, 'POST', { expected_revision: Number(target.dataset.revision || 0) }, '已请求取消任务。');
        if (action === 'delete-report' && await this.ask('将这份档案移入已删除列表？原始文件和历史结果保留，可恢复。')) return this.mutation(target, `/${encodeURIComponent(this.reportId)}`, 'DELETE', { expected_revision: report.revision }, '档案已移入已删除列表。');
        if (action === 'restore-report') return this.mutation(target, `/${encodeURIComponent(this.reportId)}/restore`, 'POST', { expected_revision: report.revision }, '档案已恢复。');
    }
}

const archiveRoot = document.querySelector('[data-attendance-root]');
if (archiveRoot && !controllers.has(archiveRoot)) controllers.set(archiveRoot, new ArchiveController(archiveRoot));
