import { apiFetch } from './api.js';
import { showToast } from './ui.js';

function initBatch(root) {
    if (root.dataset.bound) return;
    root.dataset.bound = '1';
    const offeringId = root.dataset.classOfferingId;
    const status = root.querySelector('[data-classification-batch-status]');
    const tbody = root.querySelector('[data-classification-batch-rows]');
    const save = root.querySelector('[data-classification-batch-save]');
    const clear = root.querySelector('[data-classification-batch-clear]');
    const previous = root.querySelector('[data-classification-batch-previous]');
    const next = root.querySelector('[data-classification-batch-next]');
    const selected = new Map();
    let rows = [], options = [], offset = 0, nextOffset = null, busy = false, loaded = false;

    function updateControls() {
        save.disabled = busy || selected.size === 0;
        clear.disabled = busy || selected.size === 0;
        previous.disabled = busy || selected.size > 0 || offset === 0;
        next.disabled = busy || selected.size > 0 || nextOffset === null;
        save.textContent = selected.size ? `保存已核对的 ${selected.size} 项` : '保存已核对分类';
        tbody.querySelectorAll('input, select').forEach(control => { control.disabled = busy; });
    }

    function render() {
        tbody.replaceChildren();
        for (const item of rows) {
            const tr = document.createElement('tr');
            const checkCell = document.createElement('td');
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.setAttribute('aria-label', `确认 ${item.title} 的分类`);
            checkCell.append(checkbox);
            const taskCell = document.createElement('td');
            const link = document.createElement('a');
            link.href = `/assignment/${encodeURIComponent(item.assignment_id)}`;
            link.textContent = item.title || `任务 ${item.assignment_id}`;
            const current = document.createElement('small');
            current.textContent = item.classification_status === 'confirmed' ? `当前：${item.assessment_kind_label}` : '当前：分类待确认';
            taskCell.append(link, current);
            const selectCell = document.createElement('td');
            const select = document.createElement('select');
            select.setAttribute('aria-label', `${item.title} 的任务分类`);
            const blank = document.createElement('option');
            blank.value = '';
            blank.textContent = '请选择分类';
            select.append(blank);
            for (const option of options) {
                const node = document.createElement('option');
                node.value = option.value;
                node.textContent = option.label;
                select.append(node);
            }
            select.value = item.assessment_kind || '';
            selectCell.append(select);
            const suggestion = options.find(option => option.value === item.suggested_assessment_kind);
            if (suggestion && !item.assessment_kind) {
                const note = document.createElement('small');
                note.textContent = `名称建议：${suggestion.label}，请自行核对`;
                selectCell.append(note);
            }
            const impactCell = document.createElement('td');
            const impact = item.impact || {};
            impactCell.textContent = `已有成绩 ${impact.scored_count || 0} 份 · 批改中 ${impact.grading_count || 0} 份`;
            const materialNote = document.createElement('small');
            materialNote.textContent = `关联成绩材料 ${(impact.referenced_materials || []).length} 份，保留原版本`;
            impactCell.append(materialNote);
            tr.append(checkCell, taskCell, selectCell, impactCell);
            const sync = () => {
                if (checkbox.checked && select.value) {
                    selected.set(String(item.assignment_id), { assignment_id: item.assignment_id, assessment_kind: select.value, expected_version: item.assessment_kind_version });
                } else {
                    checkbox.checked = false;
                    selected.delete(String(item.assignment_id));
                }
                updateControls();
            };
            select.addEventListener('change', () => { checkbox.checked = Boolean(select.value); sync(); });
            checkbox.addEventListener('change', sync);
            tbody.append(tr);
        }
        updateControls();
    }

    async function load(pageOffset = 0) {
        if (busy) return;
        busy = true;
        status.textContent = '正在读取分类与成绩来源影响…';
        updateControls();
        try {
            const data = await apiFetch(`/api/classrooms/${encodeURIComponent(offeringId)}/assessment-classifications?limit=100&offset=${pageOffset}`, { silent: true });
            rows = data.assignments || [];
            options = data.assessment_kind_options || [];
            nextOffset = data.next_offset ?? null;
            offset = pageOffset;
            selected.clear();
            loaded = true;
            render();
            const incomplete = rows.some(item => item.impact?.material_scan_complete === false);
            status.textContent = rows.length ? `本页 ${rows.length} 项。修改分类只影响后续批改与实时分组，正在批改的任务保留当次档位。${incomplete ? '材料影响仅核对最近 200 份；更早材料请另行核对。' : ''}` : '当前课堂暂无可确认的正式任务。';
        } catch (error) {
            status.textContent = error?.message || '读取失败，请收起后重新展开重试。';
            loaded = false;
        } finally {
            busy = false;
            updateControls();
        }
    }

    save.addEventListener('click', async () => {
        if (busy || selected.size === 0) return;
        busy = true;
        updateControls();
        try {
            const data = await apiFetch('/api/assignments/assessment-kinds/confirm', {
                method: 'POST', silent: true,
                body: { items: [...selected.values()], reason: '教师在课堂批量核对任务分类' },
            });
            for (const item of data.assignments || []) {
                window.dispatchEvent(new CustomEvent('lanshare:assessment-kind-updated', { detail: item }));
            }
            showToast(`已确认分类，更新 ${data.changed_count || 0} 项；原成绩保留`, 'success');
            busy = false;
            await load(offset);
        } catch (error) {
            const rejected = [400, 403, 404, 409, 422].includes(error?.status);
            const message = rejected ? `${error.message}。本批未保存，请重新核对。` : '未收到保存确认，请核对重新读取后的当前分类。';
            showToast(message, 'error');
            selected.clear();
            busy = false;
            await load(offset);
            status.textContent = `${message}${loaded ? '已读取最新状态。' : '重新读取失败，请收起后再展开。'}`;
        } finally {
            busy = false;
            updateControls();
        }
    });
    clear.addEventListener('click', () => { selected.clear(); render(); });
    previous.addEventListener('click', () => void load(Math.max(0, offset - 100)));
    next.addEventListener('click', () => { if (nextOffset !== null) void load(nextOffset); });
    root.addEventListener('toggle', () => { if (root.open && !loaded) void load(); });
}

function init() { document.querySelectorAll('[data-assessment-classification-batch]').forEach(initBatch); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
