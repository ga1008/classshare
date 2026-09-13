import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';

const keys = ['material', 'task', 'interaction', 'consistency'];
const same = (left, right) => keys.every(key => left[key] === right[key]);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

export function initCultivationWeightSettings(config = window.APP_CONFIG || {}) {
    const panel = document.querySelector('[data-cultivation-weight-settings]');
    if (!panel) return;
    if (panel.weightEditor) return panel.weightEditor;
    const offeringId = Number(panel.dataset.classOfferingId || config.classOfferingId);
    const controls = Object.fromEntries([...panel.querySelectorAll('[data-weight-key]')].map(control => [control.dataset.weightKey, control]));
    const preview = panel.querySelector('[data-weight-preview]');
    const saveButton = panel.querySelector('[data-weight-save]');
    const previewButton = panel.querySelector('[data-weight-preview-button]');
    const cancelButton = panel.querySelector('[data-weight-cancel]');
    const status = panel.querySelector('[data-weight-status]');
    const read = () => Object.fromEntries(keys.map(key => [key, Number(controls[key].querySelector('[data-weight-number]').value)]));
    let saved = read(), revision = Number(panel.dataset.weightRevision || 0), canUpdate = panel.dataset.canUpdate === '1';
    let busy = false, conflictSettings = null, epoch = 0;
    const write = weights => keys.forEach(key => controls[key].querySelectorAll('input').forEach(input => { input.value = String(weights[key]); }));
    const dirty = () => !same(saved, read());
    const valid = () => { const weights = read(); return keys.every(key => Number.isInteger(weights[key]) && weights[key] >= 0 && weights[key] <= 100) && keys.reduce((sum, key) => sum + weights[key], 0) === 100; };
    function update() {
        const total = keys.reduce((sum, key) => sum + read()[key], 0);
        panel.querySelector('[data-weight-total]').textContent = Number.isFinite(total) ? String(total) : '—';
        panel.querySelector('[data-weight-total-state]').dataset.weightTotalState = valid() ? 'ok' : 'invalid';
        saveButton.disabled = busy || !valid() || !canUpdate || !dirty() || !!conflictSettings;
        previewButton.disabled = busy || !valid();
        if (cancelButton) { cancelButton.disabled = busy || (!dirty() && !conflictSettings); cancelButton.textContent = conflictSettings ? '采用最新设置' : '取消修改'; }
        panel.querySelectorAll('input,[data-weight-preset]').forEach(control => { control.disabled = busy; });
        panel.setAttribute('aria-busy', String(busy));
    }
    function applySettings(settings) {
        saved = { ...settings.weights }; revision = Number(settings.revision); canUpdate = Boolean(settings.can_update);
        panel.dataset.weightRevision = String(revision); panel.dataset.canUpdate = canUpdate ? '1' : '0';
        const label = panel.querySelector('[data-weight-version]');
        if (label) label.textContent = `${settings.version}${settings.updated_at ? ` · ${settings.updated_at}` : ''}`;
        if (status) status.textContent = canUpdate ? '已保存' : `冷却中 · ${settings.cooldown_remaining_days || 1} 天`;
    }
    const reset = () => {
        if (busy) return;
        epoch++;
        if (conflictSettings) { applySettings(conflictSettings); conflictSettings = null; }
        write(saved); preview.hidden = true; update();
    };
    keys.forEach(key => {
        const control = controls[key];
        const number = control.querySelector('[data-weight-number]'), slider = control.querySelector('[data-weight-slider]');
        number.addEventListener('input', () => { epoch++; slider.value = number.value; preview.hidden = true; update(); });
        slider.addEventListener('input', () => { epoch++; number.value = slider.value; preview.hidden = true; update(); });
    });
    panel.querySelectorAll('[data-weight-preset]').forEach(button => button.addEventListener('click', () => {
        if (busy) return;
        epoch++;
        write(Object.fromEntries(keys.map(key => [key, Number(button.dataset[`weight${key[0].toUpperCase()}${key.slice(1)}`])])));
        preview.hidden = true; update();
    }));
    previewButton.addEventListener('click', async () => {
        if (busy || !valid()) return;
        const requestEpoch = ++epoch, weights = read();
        busy = true; previewButton.textContent = '预览中…'; update();
        try {
            const data = await apiFetch(`/api/classrooms/${offeringId}/learning/weights/preview`, { method: 'POST', body: { weights }, silent: true });
            if (requestEpoch !== epoch) return;
            preview.hidden = false;
            preview.innerHTML = `<div class="learning-weight-preview__summary"><span>均分 ${escapeHtml(data.old_average)} → ${escapeHtml(data.new_average)}</span><strong>${escapeHtml(data.average_delta_label)}</strong><small>${Number(data.affected_count)} / ${Number(data.student_count)} 人变化</small></div><div class="learning-weight-preview__students">${(data.students_preview || []).map(student => `<span><b>${escapeHtml(student.name)}</b><small>${escapeHtml(student.old_score)} → ${escapeHtml(student.new_score)} · ${escapeHtml(student.delta_label)}</small></span>`).join('')}</div>`;
        } catch (error) { showToast(error.message || '权重预览失败。', 'error'); }
        finally { busy = false; previewButton.textContent = '预览变化'; update(); }
    });
    async function save() {
        if (busy || !valid() || !canUpdate || !dirty() || conflictSettings) return false;
        const submitted = read(), expected = revision;
        busy = true; saveButton.textContent = '保存中…'; update();
        try {
            const data = await apiFetch(`/api/classrooms/${offeringId}/learning/weights`, { method: 'POST', body: { weights: submitted, expected_revision: expected }, silent: true });
            applySettings(data.weight_settings); write(saved); preview.hidden = true;
            showToast(data.message || '修为权重已保存。', 'success');
            panel.dispatchEvent(new CustomEvent('member-weights-saved', { bubbles: true, detail: data }));
            return true;
        } catch (error) {
            if (error.status === 409 && error.data?.detail?.weight_settings) {
                conflictSettings = error.data.detail.weight_settings;
                if (status) status.textContent = '其他会话已更新设置。当前输入已保留，请采用最新设置后重新调整。';
            }
            showToast(error.message || '修为权重保存失败，输入已保留。', 'error');
            return false;
        } finally { busy = false; saveButton.textContent = '保存权重'; update(); }
    }
    saveButton.addEventListener('click', save);
    cancelButton?.addEventListener('click', reset);
    update();
    panel.weightEditor = { isDirty: dirty, isBusy: () => busy, reset, save };
    return panel.weightEditor;
}
