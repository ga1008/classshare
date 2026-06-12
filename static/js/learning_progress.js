import { apiFetch } from '/static/js/api.js';
import { initLearningCertificateReveal } from '/static/js/learning_certificate_reveal.js?v=cultivation-certificate-20260612';
import { showToast } from '/static/js/ui.js';

function initLearningProgressModal() {
    const modal = document.getElementById('learning-progress-modal');
    const panel = document.querySelector('[data-learning-panel]');
    const triggers = Array.from(document.querySelectorAll('[data-learning-modal-open], [data-learning-scroll]'));
    if (!modal || !panel || !triggers.length) return;

    const shell = modal.querySelector('.learning-modal-shell');
    const closeBtn = document.getElementById('learning-modal-close');
    const transitionMs = 260;
    let closeTimer = 0;
    let activeTrigger = null;

    const getFocusableElements = () => Array.from(
        modal.querySelectorAll('a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'),
    ).filter((element) => element.offsetParent !== null || element === document.activeElement);

    const setTriggerState = (expanded) => {
        triggers.forEach((trigger) => {
            trigger.setAttribute('aria-expanded', String(expanded));
        });
    };

    const openModal = (trigger = null) => {
        window.clearTimeout(closeTimer);
        activeTrigger = trigger || document.activeElement;
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-learning-modal');
        setTriggerState(true);
        window.requestAnimationFrame(() => {
            modal.classList.add('is-open');
            panel.classList.remove('is-learning-focus');
            void panel.offsetWidth;
            panel.classList.add('is-learning-focus');
            (closeBtn || shell)?.focus({ preventScroll: true });
            window.setTimeout(() => panel.classList.remove('is-learning-focus'), 1600);
        });
    };

    const closeModal = () => {
        modal.classList.remove('is-open');
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('has-learning-modal');
        setTriggerState(false);
        closeTimer = window.setTimeout(() => {
            if (!modal.classList.contains('is-open')) {
                modal.hidden = true;
                activeTrigger?.focus?.({ preventScroll: true });
                activeTrigger = null;
            }
        }, transitionMs);
    };

    triggers.forEach((trigger) => {
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-controls', 'learning-progress-modal');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.addEventListener('click', () => openModal(trigger));
    });
    closeBtn?.addEventListener('click', closeModal);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeModal();
    });
    document.addEventListener('keydown', (event) => {
        if (modal.hidden) return;
        const studentInsightModal = document.getElementById('student-insight-modal');
        if (studentInsightModal && !studentInsightModal.hidden) return;

        if (event.key === 'Escape') {
            closeModal();
            return;
        }

        if (event.key !== 'Tab') return;

        const focusableElements = getFocusableElements();
        if (!focusableElements.length) {
            event.preventDefault();
            shell?.focus({ preventScroll: true });
            return;
        }

        const firstFocusable = focusableElements[0];
        const lastFocusable = focusableElements[focusableElements.length - 1];
        if (event.shiftKey && document.activeElement === firstFocusable) {
            event.preventDefault();
            lastFocusable.focus({ preventScroll: true });
        } else if (!event.shiftKey && document.activeElement === lastFocusable) {
            event.preventDefault();
            firstFocusable.focus({ preventScroll: true });
        }
    });
}

function initTeacherLearningRoster() {
    const searchInput = document.querySelector('[data-learning-roster-search]');
    const rosterItems = Array.from(document.querySelectorAll('[data-learning-roster-item]'));
    const emptyState = document.querySelector('[data-learning-roster-empty]');
    if (!searchInput || !rosterItems.length) return;

    const normalize = (value) => String(value || '').trim().toLowerCase();
    const applyFilter = () => {
        const query = normalize(searchInput.value);
        let visibleCount = 0;
        rosterItems.forEach((item) => {
            const text = normalize(item.dataset.searchText || item.textContent);
            const isVisible = !query || text.includes(query);
            item.hidden = !isVisible;
            if (isVisible) visibleCount += 1;
        });
        if (emptyState) {
            emptyState.hidden = !query || visibleCount > 0;
        }
    };

    searchInput.addEventListener('input', applyFilter);
    applyFilter();
}

function initCultivationWeightSettings(config = window.APP_CONFIG || {}) {
    const panel = document.querySelector('[data-cultivation-weight-settings]');
    if (!panel) return;

    const classOfferingId = Number(panel.dataset.classOfferingId || config.classOfferingId);
    if (!classOfferingId) return;

    const controls = Array.from(panel.querySelectorAll('[data-weight-key]'));
    const totalEl = panel.querySelector('[data-weight-total]');
    const totalState = totalEl?.closest('[data-weight-total-state]');
    const previewEl = panel.querySelector('[data-weight-preview]');
    const previewButton = panel.querySelector('[data-weight-preview-button]');
    const saveButton = panel.querySelector('[data-weight-save]');
    const canUpdate = panel.dataset.canUpdate === '1';
    const keys = ['material', 'task', 'interaction', 'consistency'];

    const getControl = (key) => controls.find((control) => control.dataset.weightKey === key);

    const readWeights = () => keys.reduce((weights, key) => {
        const control = getControl(key);
        const number = control?.querySelector('[data-weight-number]');
        weights[key] = Math.max(0, Math.min(100, Number.parseInt(number?.value || '0', 10) || 0));
        return weights;
    }, {});

    const totalWeights = (weights = readWeights()) => keys.reduce((sum, key) => sum + Number(weights[key] || 0), 0);

    const setBusy = (button, busy, text = '') => {
        if (!button) return;
        if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
        button.disabled = busy || (button === saveButton && !canUpdate);
        button.classList.toggle('is-busy', busy);
        button.textContent = busy ? text : button.dataset.originalText;
    };

    const updateTotalState = () => {
        const total = totalWeights();
        if (totalEl) totalEl.textContent = String(total);
        totalState?.setAttribute('data-weight-total-state', total === 100 ? 'ok' : 'invalid');
        if (previewButton) previewButton.disabled = total !== 100;
        if (saveButton) saveButton.disabled = total !== 100 || !canUpdate;
        if (previewEl && total !== 100) {
            previewEl.hidden = false;
            previewEl.innerHTML = `<span class="learning-weight-preview__warning">合计需为 100，当前为 ${total}。</span>`;
        }
        return total;
    };

    const setControlValue = (key, value) => {
        const normalized = Math.max(0, Math.min(100, Number.parseInt(value, 10) || 0));
        const control = getControl(key);
        const slider = control?.querySelector('[data-weight-slider]');
        const number = control?.querySelector('[data-weight-number]');
        if (slider) slider.value = String(normalized);
        if (number) number.value = String(normalized);
    };

    const renderPreview = (data = {}) => {
        if (!previewEl) return;
        const students = Array.isArray(data.students_preview) ? data.students_preview : [];
        previewEl.hidden = false;
        previewEl.innerHTML = `
            <div class="learning-weight-preview__summary">
                <span>均分 ${escapeHtml(data.old_average ?? 0)} → ${escapeHtml(data.new_average ?? 0)}</span>
                <strong>${escapeHtml(data.average_delta_label || '+0.0')}</strong>
                <small>${Number(data.affected_count || 0)} / ${Number(data.student_count || 0)} 人变化</small>
            </div>
            ${students.length ? `
                <div class="learning-weight-preview__students">
                    ${students.map((student) => `
                        <span>
                            <b>${escapeHtml(student.name || '')}</b>
                            <small>${escapeHtml(student.old_score ?? 0)} → ${escapeHtml(student.new_score ?? 0)} · ${escapeHtml(student.delta_label || '+0.0')}</small>
                        </span>
                    `).join('')}
                </div>
            ` : '<p class="learning-weight-preview__empty">暂无可预览的学生数据。</p>'}
        `;
    };

    controls.forEach((control) => {
        const key = control.dataset.weightKey;
        const slider = control.querySelector('[data-weight-slider]');
        const number = control.querySelector('[data-weight-number]');
        const sync = (source) => {
            setControlValue(key, source.value);
            updateTotalState();
        };
        slider?.addEventListener('input', () => sync(slider));
        number?.addEventListener('input', () => sync(number));
    });

    panel.querySelectorAll('[data-weight-preset]').forEach((button) => {
        button.addEventListener('click', () => {
            keys.forEach((key) => {
                const datasetKey = `weight${key.charAt(0).toUpperCase()}${key.slice(1)}`;
                setControlValue(key, button.dataset[datasetKey]);
            });
            updateTotalState();
            if (previewEl) previewEl.hidden = true;
        });
    });

    previewButton?.addEventListener('click', async () => {
        if (updateTotalState() !== 100) return;
        setBusy(previewButton, true, '预览中...');
        try {
            const data = await apiFetch(`/api/classrooms/${classOfferingId}/learning/weights/preview`, {
                method: 'POST',
                body: { weights: readWeights() },
                silent: true,
            });
            renderPreview(data);
        } catch (error) {
            showToast(error.message || '权重预览失败。', 'error');
        } finally {
            setBusy(previewButton, false);
        }
    });

    saveButton?.addEventListener('click', async () => {
        if (updateTotalState() !== 100 || !canUpdate) return;
        setBusy(saveButton, true, '保存中...');
        try {
            const data = await apiFetch(`/api/classrooms/${classOfferingId}/learning/weights`, {
                method: 'POST',
                body: { weights: readWeights() },
                silent: true,
            });
            showToast(data.message || '修为权重已保存。', data.updated === false ? 'info' : 'success');
            if (data.updated !== false) {
                window.setTimeout(() => window.location.reload(), 600);
            }
        } catch (error) {
            showToast(error.message || '修为权重保存失败。', 'error');
        } finally {
            setBusy(saveButton, false);
        }
    });

    updateTotalState();
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function initTeacherExamRoster(config = window.APP_CONFIG || {}) {
    const panel = document.querySelector('[data-exam-roster-panel]');
    if (!panel || !config?.classOfferingId) return;

    const classOfferingId = Number(panel.dataset.classOfferingId || config.classOfferingId);
    const syncButton = panel.querySelector('[data-exam-roster-sync]');
    const exportButton = panel.querySelector('[data-exam-roster-export]');
    const statusEl = panel.querySelector('[data-exam-roster-status]');
    const statusTitleEl = panel.querySelector('[data-exam-roster-status-title]');
    const summaryEl = panel.querySelector('[data-exam-roster-summary]');
    const candidatesEl = panel.querySelector('[data-exam-roster-candidates]');
    const previewEl = panel.querySelector('[data-exam-roster-preview]');
    const form = panel.querySelector('[data-exam-roster-export-form]');
    if (!classOfferingId || !syncButton || !statusEl || !form) return;

    let latestStatus = null;
    const placePicker = panel.querySelector('[data-exam-place-picker]');
    const placeInput = panel.querySelector('[data-exam-place-input]');
    const placeKeyInput = panel.querySelector('[data-exam-place-key]');
    const placeIdInput = panel.querySelector('[data-exam-place-id]');
    const placeResultsEl = panel.querySelector('[data-exam-place-results]');
    let placeLookupTimer = null;
    let placeLookupSeq = 0;

    const setBusy = (button, busy, text) => {
        if (!button) return;
        if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
        button.disabled = busy;
        button.classList.toggle('is-busy', busy);
        button.textContent = busy ? text : button.dataset.originalText;
    };

    const setExportEnabled = (enabled) => {
        if (exportButton) exportButton.disabled = !enabled;
        form.classList.toggle('is-disabled', !enabled);
    };

    const closePlaceResults = () => {
        if (!placeResultsEl) return;
        placeResultsEl.hidden = true;
        placeResultsEl.innerHTML = '';
    };

    const clearPlaceSelection = () => {
        if (placeKeyInput) placeKeyInput.value = '';
        if (placeIdInput) placeIdInput.value = '';
    };

    const formatPlaceMeta = (place = {}) => [
        place.campus_name,
        place.building_name,
        place.room_type_name,
        place.exam_seat_count ? `考位 ${place.exam_seat_count}` : '',
        place.seat_count ? `座位 ${place.seat_count}` : '',
    ].filter(Boolean).join(' · ');

    const renderPlaceResults = (items = [], message = '') => {
        if (!placeResultsEl) return;
        if (!items.length) {
            placeResultsEl.innerHTML = `<div class="learning-exam-place-empty">${escapeHtml(message || '没有匹配的本地教学场地')}</div>`;
            placeResultsEl.hidden = false;
            return;
        }
        placeResultsEl.innerHTML = items.map((place) => `
            <button type="button" class="learning-exam-place-option" data-place-key="${escapeHtml(place.place_key || '')}" data-place-id="${escapeHtml(place.place_id || '')}" data-place-label="${escapeHtml(place.display_name || '')}">
                <strong>${escapeHtml(place.display_name || place.room_name || place.room_code || '')}</strong>
                <span>${escapeHtml(formatPlaceMeta(place))}</span>
            </button>
        `).join('');
        placeResultsEl.hidden = false;
    };

    const queryTeachingPlaces = async (query = '', seq = ++placeLookupSeq) => {
        if (!placeResultsEl) return;
        const params = new URLSearchParams({
            q: query.trim(),
            limit: '12',
        });
        placeResultsEl.innerHTML = '<div class="learning-exam-place-empty">正在查询本地教学场地...</div>';
        placeResultsEl.hidden = false;
        try {
            const response = await fetch(`/api/manage/classrooms/teaching-places?${params.toString()}`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) throw new Error('本地教学场地查询失败');
            const data = await response.json();
            if (seq !== placeLookupSeq) return;
            const items = Array.isArray(data.items) ? data.items : [];
            renderPlaceResults(items, query.trim() ? '没有匹配的本地教学场地，可手动填写地点' : '本地暂未同步可用考试场地');
        } catch (error) {
            if (seq !== placeLookupSeq) return;
            renderPlaceResults([], error.message || '本地教学场地查询失败，可手动填写地点');
        }
    };

    const schedulePlaceLookup = (query = '', delay = 180) => {
        if (!placeInput || !placeResultsEl) return;
        const seq = ++placeLookupSeq;
        window.clearTimeout(placeLookupTimer);
        placeLookupTimer = window.setTimeout(() => queryTeachingPlaces(query, seq), delay);
    };

    const choosePlace = (button) => {
        const label = button.dataset.placeLabel || button.textContent.trim();
        if (placeInput) placeInput.value = label;
        if (placeKeyInput) placeKeyInput.value = button.dataset.placeKey || '';
        if (placeIdInput) placeIdInput.value = button.dataset.placeId || '';
        closePlaceResults();
    };

    const applyDefaults = (defaults = {}) => {
        if (form.elements.exam_datetime && defaults.exam_datetime_local) {
            form.elements.exam_datetime.value = String(defaults.exam_datetime_local).slice(0, 16);
        }
        if (form.elements.exam_location && defaults.exam_location) {
            form.elements.exam_location.value = defaults.exam_location;
            clearPlaceSelection();
        }
        if (form.elements.chief_invigilator && defaults.chief_invigilator) {
            form.elements.chief_invigilator.value = defaults.chief_invigilator;
        }
        if (form.elements.assistant_invigilator && defaults.assistant_invigilator) {
            form.elements.assistant_invigilator.value = defaults.assistant_invigilator;
        }
    };

    const renderCandidates = (candidates = []) => {
        if (!candidatesEl) return;
        if (!candidates.length) {
            candidatesEl.hidden = true;
            candidatesEl.textContent = '';
            return;
        }
        candidatesEl.hidden = false;
        candidatesEl.innerHTML = `
            <div class="learning-exam-roster-candidates__title">请选择本课堂对应的考试课程</div>
            <div class="learning-exam-roster-candidates__list">
                ${candidates.map((item) => `
                    <button type="button" class="learning-exam-roster-candidate" data-exam-course-key="${escapeHtml(item.exam_course_key)}">
                        <strong>${escapeHtml(item.course_code || '')} ${escapeHtml(item.course_name || '')}</strong>
                        <span>${escapeHtml(item.teaching_class_name || '')} · ${escapeHtml(item.class_composition || '')}</span>
                        <small>${Number(item.declared_student_count || 0)} 人 · 匹配 ${Number(item.score || 0)}</small>
                    </button>
                `).join('')}
            </div>
        `;
    };

    const renderPreview = (data) => {
        if (!previewEl) return;
        if (!data || data.status !== 'success') {
            previewEl.hidden = true;
            previewEl.textContent = '';
            return;
        }
        const students = data.students_preview || [];
        const alignment = data.alignment || {};
        const missing = alignment.missing_local_students || [];
        const extra = alignment.extra_local_students || [];
        const count = Number(data.student_count || 0);
        previewEl.hidden = false;
        previewEl.innerHTML = `
            <div class="learning-exam-roster-preview__head">
                <span>名单预览</span>
                <strong>${count} 人</strong>
            </div>
            <div class="learning-exam-roster-preview__students">
                ${students.map((student) => `
                    <span title="${escapeHtml(`${student.student_number || ''} ${student.student_name || ''}`)}">
                        <b>${escapeHtml(student.student_name || '')}</b>
                        <small>${escapeHtml(student.student_number || '')}</small>
                    </span>
                `).join('')}
                ${count > students.length ? `<em>还有 ${count - students.length} 人</em>` : ''}
            </div>
            ${(missing.length || extra.length) ? `
                <div class="learning-exam-roster-preview__diff">
                    ${missing.length ? `<span><b>本地缺少</b>${missing.map((item) => escapeHtml(item.student_name || item.student_number || '')).join('、')}</span>` : ''}
                    ${extra.length ? `<span><b>本地多出</b>${extra.map((item) => escapeHtml(item.student_name || item.student_number || '')).join('、')}</span>` : ''}
                </div>
            ` : ''}
        `;
    };

    const renderStatus = (data) => {
        latestStatus = data;
        if (!data || data.status === 'empty') {
            if (statusTitleEl) statusTitleEl.textContent = '等待同步';
            statusEl.innerHTML = `<p class="learning-exam-roster-empty-note">${escapeHtml(data?.message || '尚未同步考试名单。')}</p>`;
            if (summaryEl) summaryEl.textContent = '同步后会显示名单人数和本地班级差异。';
            renderCandidates([]);
            renderPreview(null);
            setExportEnabled(false);
            applyDefaults(data?.default_export || {});
            return;
        }
        if (data.status === 'needs_confirmation') {
            if (statusTitleEl) statusTitleEl.textContent = '需要确认';
            statusEl.innerHTML = `<p class="learning-exam-roster-empty-note">${escapeHtml(data.message || '请选择要对齐的考试课程。')}</p>`;
            renderCandidates(data.candidates || []);
            renderPreview(null);
            setExportEnabled(false);
            return;
        }
        if (data.status !== 'success') {
            if (statusTitleEl) statusTitleEl.textContent = '暂不可用';
            statusEl.innerHTML = `<p class="learning-exam-roster-empty-note">${escapeHtml(data.message || '考试名单状态暂不可用。')}</p>`;
            renderCandidates([]);
            renderPreview(null);
            setExportEnabled(false);
            return;
        }
        const course = data.course || {};
        const alignment = data.alignment || {};
        if (statusTitleEl) statusTitleEl.textContent = '已同步';
        statusEl.innerHTML = `
            <div class="learning-exam-roster-course">
                <span>${escapeHtml(course.course_code || '')}</span>
                <strong>${escapeHtml(course.course_name || '')}</strong>
                <small>${escapeHtml(course.teaching_class_name || course.class_composition || '')}</small>
            </div>
            <div class="learning-exam-roster-metrics" aria-label="考试名单对齐统计">
                <span><b>${Number(alignment.exam_student_count || data.student_count || 0)}</b><small>教务名单</small></span>
                <span><b>${Number(alignment.matched_local_count || 0)}</b><small>本地匹配</small></span>
                <span class="${Number(alignment.missing_local_count || 0) ? 'is-warning' : ''}"><b>${Number(alignment.missing_local_count || 0)}</b><small>本地缺少</small></span>
                <span class="${Number(alignment.extra_local_count || 0) ? 'is-warning' : ''}"><b>${Number(alignment.extra_local_count || 0)}</b><small>本地多出</small></span>
            </div>
        `;
        if (summaryEl) {
            summaryEl.textContent = data.synced_at ? `上次同步：${data.synced_at}` : '名单已同步，可确认考试信息后导出。';
        }
        renderCandidates([]);
        renderPreview(data);
        applyDefaults(data.default_export || {});
        setExportEnabled(Number(data.student_count || 0) > 0);
    };

    const loadStatus = async () => {
        try {
            const data = await apiFetch(`/api/manage/classrooms/${classOfferingId}/exam-roster`, { silent: true });
            renderStatus(data);
        } catch (error) {
            statusEl.textContent = error.message || '读取考试名单状态失败。';
            setExportEnabled(false);
        }
    };

    const syncRoster = async (examCourseKey = '') => {
        setBusy(syncButton, true, '正在同步...');
        try {
            const data = await apiFetch(`/api/manage/classrooms/${classOfferingId}/exam-roster/sync`, {
                method: 'POST',
                body: examCourseKey ? { exam_course_key: examCourseKey } : {},
                silent: true,
            });
            renderStatus(data);
            if (data.status === 'success') {
                showToast(data.message || '考试名单已同步。', 'success');
            } else if (data.status === 'needs_confirmation') {
                showToast('请选择对应的教务系统考试课程。', 'info');
            }
        } catch (error) {
            showToast(error.message || '同步考试名单失败。', 'error');
        } finally {
            setBusy(syncButton, false);
        }
    };

    syncButton.addEventListener('click', () => syncRoster());
    candidatesEl?.addEventListener('click', (event) => {
        const candidate = event.target.closest('[data-exam-course-key]');
        if (!candidate) return;
        syncRoster(candidate.dataset.examCourseKey || '');
    });

    if (placeInput && placeResultsEl) {
        placeInput.addEventListener('focus', () => {
            schedulePlaceLookup(placeInput.value || '', 0);
        });
        placeInput.addEventListener('input', () => {
            clearPlaceSelection();
            schedulePlaceLookup(placeInput.value || '');
        });
        placeInput.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') closePlaceResults();
        });
        placeResultsEl.addEventListener('mousedown', (event) => {
            event.preventDefault();
        });
        placeResultsEl.addEventListener('click', (event) => {
            const option = event.target.closest('[data-place-key], [data-place-id]');
            if (!option) return;
            choosePlace(option);
        });
        document.addEventListener('click', (event) => {
            if (!placePicker || placePicker.contains(event.target)) return;
            closePlaceResults();
        });
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (!latestStatus || latestStatus.status !== 'success') {
            showToast('请先同步并确认考试名单。', 'warning');
            return;
        }
        const payload = {
            exam_datetime: form.elements.exam_datetime?.value || '',
            exam_location: form.elements.exam_location?.value || '',
            exam_location_place_key: form.elements.exam_location_place_key?.value || '',
            exam_location_place_id: form.elements.exam_location_place_id?.value || '',
            chief_invigilator: form.elements.chief_invigilator?.value || '',
            assistant_invigilator: form.elements.assistant_invigilator?.value || '',
        };
        setBusy(exportButton, true, '正在导出...');
        try {
            const response = await fetch(`/api/manage/classrooms/${classOfferingId}/exam-roster/export`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                const contentType = response.headers.get('content-type') || '';
                const errorData = contentType.includes('application/json') ? await response.json() : await response.text();
                const message = typeof errorData === 'object' ? errorData.detail : errorData;
                throw new Error(message || '导出签名表失败。');
            }
            const blob = await response.blob();
            const disposition = response.headers.get('content-disposition') || '';
            const filenameMatch = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
            const filename = filenameMatch
                ? decodeURIComponent(filenameMatch[1] || filenameMatch[2])
                : '考试签名表.xlsx';
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            showToast('考试签名表已生成。', 'success');
        } catch (error) {
            showToast(error.message || '导出签名表失败。', 'error');
        } finally {
            setBusy(exportButton, false);
        }
    });

    loadStatus();
}

function initStudentInsightModal() {
    const modal = document.getElementById('student-insight-modal');
    const frame = modal?.querySelector('[data-student-insight-frame]');
    const loading = modal?.querySelector('[data-student-insight-loading]');
    const closeBtn = document.getElementById('student-insight-modal-close');
    const titleEl = document.getElementById('student-insight-modal-title');
    const triggers = Array.from(document.querySelectorAll('[data-student-insight-open]'));
    if (!modal || !frame || !triggers.length) return;

    const shell = modal.querySelector('.student-insight-modal-shell');
    const transitionMs = 240;
    let activeTrigger = null;
    let closeTimer = 0;
    let loadGuardTimer = 0;

    const getFocusableElements = () => Array.from(
        modal.querySelectorAll('a[href], button:not([disabled]), iframe, textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'),
    ).filter((element) => element.offsetParent !== null || element === document.activeElement);

    const openModal = (trigger) => {
        const url = trigger.dataset.studentInsightUrl || `${trigger.getAttribute('href') || ''}?embed=1`;
        if (!url) return;
        window.clearTimeout(closeTimer);
        window.clearTimeout(loadGuardTimer);
        activeTrigger = trigger;
        if (titleEl) {
            const studentName = String(trigger.dataset.studentName || '').trim();
            titleEl.textContent = studentName ? `${studentName} · 成员详情` : '成员详情';
        }
        if (loading) loading.hidden = false;
        frame.classList.add('is-loading');
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-student-insight-modal');
        window.requestAnimationFrame(() => {
            modal.classList.add('is-open');
            (closeBtn || shell)?.focus({ preventScroll: true });
            frame.src = url;
            loadGuardTimer = window.setTimeout(() => {
                if (loading) loading.hidden = true;
                frame.classList.remove('is-loading');
            }, 5000);
        });
    };

    const closeModal = () => {
        window.clearTimeout(loadGuardTimer);
        modal.classList.remove('is-open');
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('has-student-insight-modal');
        closeTimer = window.setTimeout(() => {
            if (!modal.classList.contains('is-open')) {
                modal.hidden = true;
                activeTrigger?.focus?.({ preventScroll: true });
                activeTrigger = null;
            }
        }, transitionMs);
    };

    triggers.forEach((trigger) => {
        trigger.addEventListener('click', (event) => {
            event.preventDefault();
            openModal(trigger);
        });
    });

    frame.addEventListener('load', () => {
        window.clearTimeout(loadGuardTimer);
        if (loading) loading.hidden = true;
        frame.classList.remove('is-loading');
    });
    closeBtn?.addEventListener('click', closeModal);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeModal();
    });
    document.addEventListener('keydown', (event) => {
        if (modal.hidden) return;
        if (event.key === 'Escape') {
            closeModal();
            return;
        }
        if (event.key !== 'Tab') return;

        const focusableElements = getFocusableElements();
        if (!focusableElements.length) {
            event.preventDefault();
            shell?.focus({ preventScroll: true });
            return;
        }
        const firstFocusable = focusableElements[0];
        const lastFocusable = focusableElements[focusableElements.length - 1];
        if (event.shiftKey && document.activeElement === firstFocusable) {
            event.preventDefault();
            lastFocusable.focus({ preventScroll: true });
        } else if (!event.shiftKey && document.activeElement === lastFocusable) {
            event.preventDefault();
            firstFocusable.focus({ preventScroll: true });
        }
    });
}

function initStageExamButton(config) {
    const buttons = Array.from(document.querySelectorAll('.learning-stage-exam-btn'));
    const setButtonBusy = (button, busy) => {
        const label = button.querySelector('[data-learning-stage-action-label]');
        const nextText = '生成中';
        if (label && !button.dataset.originalActionLabel) {
            button.dataset.originalActionLabel = label.textContent;
        }
        if (!label && !button.dataset.originalText) {
            button.dataset.originalText = button.textContent;
        }
        button.disabled = busy;
        button.classList.toggle('is-busy', busy);
        if (label) {
            label.textContent = busy ? nextText : button.dataset.originalActionLabel;
        } else {
            button.textContent = busy ? 'AI 正在布置试炼...' : button.dataset.originalText;
        }
    };

    if (buttons.length && config?.classOfferingId) {
        buttons.forEach((button) => {
            button.addEventListener('click', async () => {
                const stageKey = button.dataset.stageKey;
                if (!stageKey) return;
                buttons
                    .filter((candidate) => candidate.dataset.stageKey === stageKey)
                    .forEach((candidate) => setButtonBusy(candidate, true));
                try {
                    const result = await apiFetch(
                        `/api/classrooms/${config.classOfferingId}/learning/stages/${stageKey}/exam`,
                        { method: 'POST', silent: true },
                    );
                    if (result.status === 'generating') {
                        showToast(result.message || 'AI 正在生成破境试炼，请稍后刷新。', 'info');
                        buttons
                            .filter((candidate) => candidate.dataset.stageKey === stageKey)
                            .forEach((candidate) => setButtonBusy(candidate, false));
                        return;
                    }
                    showToast(result.status === 'exists' ? '破境试炼已准备好，正在进入。' : '破境试炼已生成。', 'success');
                    window.location.href = result.exam_url || `/classroom/${config.classOfferingId}`;
                } catch (error) {
                    showToast(error.message || '破境试炼生成失败', 'error');
                    buttons
                        .filter((candidate) => candidate.dataset.stageKey === stageKey)
                        .forEach((candidate) => setButtonBusy(candidate, false));
                }
            });
        });
    }

    const deleteButton = document.querySelector('.learning-stage-delete-btn');
    if (!deleteButton || !config?.classOfferingId) return;
    deleteButton.addEventListener('click', async () => {
        const stageKey = deleteButton.dataset.stageKey;
        if (!stageKey) return;
        if (!window.confirm('确定删除这份个人破境试炼吗？删除后可以重新生成。')) return;
        const originalText = deleteButton.textContent;
        deleteButton.disabled = true;
        deleteButton.textContent = '正在删除...';
        try {
            const result = await apiFetch(
                `/api/classrooms/${config.classOfferingId}/learning/stages/${stageKey}/exam`,
                { method: 'DELETE', silent: true },
            );
            showToast(result.message || '个人破境试炼已删除，可以重新试炼。', 'success');
            window.setTimeout(() => window.location.reload(), 500);
        } catch (error) {
            showToast(error.message || '删除试炼失败', 'error');
            deleteButton.disabled = false;
            deleteButton.textContent = originalText;
        }
    });
}

function initLearningMountain(config) {
    const container = document.querySelector('[data-learning-mountain-chart]');
    const hint = document.querySelector('[data-learning-mountain-hint]');
    const position = config?.learningProgress?.class_position;
    if (!container || !position?.current || !position?.leader || !position?.mountain) return;

    const svgNS = 'http://www.w3.org/2000/svg';
    const width = 360;
    const height = 150;
    const baseY = 130;
    const peakY = 18;
    const peakX = 178;
    const leftBaseX = 30;
    const rightBaseX = 330;
    const labelX = 274;
    const minScore = Number(position.mountain.min_score ?? 0);
    const maxScore = Number(position.mountain.max_score ?? 100);
    const scoreRange = Math.max(1, maxScore - minScore);
    const currentScore = Number(position.current.score ?? 0);
    const leaderScore = Number(position.leader.score ?? maxScore);
    const currentName = position.current.name || config?.userInfo?.name || '您';
    const leaderName = position.leader.name || '同学';
    const samePerson = Boolean(position.leader.is_self) || leaderName === currentName;
    const compactText = (text, maxLength) => {
        const normalized = String(text || '').trim();
        return normalized.length > maxLength ? `${normalized.slice(0, Math.max(1, maxLength - 1))}…` : normalized;
    };

    const create = (tag, attrs = {}) => {
        const node = document.createElementNS(svgNS, tag);
        Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
        return node;
    };
    const yFor = (score) => {
        const ratio = maxScore === minScore ? 0.5 : (Number(score || 0) - minScore) / scoreRange;
        return baseY - Math.max(0, Math.min(1, ratio)) * (baseY - peakY);
    };
    const selfY = Math.max(peakY, Math.min(baseY, yFor(currentScore)));
    const selfLabel = samePerson
        ? `修为 ${currentScore.toFixed(1)}`
        : `${compactText(currentName, 5)} · ${currentScore.toFixed(1)}`;
    const selfLabelY = selfY <= peakY + 12 ? selfY + 18 : selfY - 6;
    const peakLabel = `${compactText(leaderName, 6)} · ${leaderScore.toFixed(1)}`;

    container.textContent = '';
    const svg = create('svg', {
        viewBox: `0 0 ${width} ${height}`,
        role: 'img',
        'aria-label': '全班修为山峰，山顶为最高修为，横向虚线为您的修为位置',
    });
    const defs = create('defs');
    const gradient = create('linearGradient', { id: 'learningMountainFill', x1: '0', x2: '0', y1: '0', y2: '1' });
    gradient.append(
        create('stop', { offset: '0%', 'stop-color': '#14b8a6', 'stop-opacity': '0.28' }),
        create('stop', { offset: '58%', 'stop-color': '#38bdf8', 'stop-opacity': '0.16' }),
        create('stop', { offset: '100%', 'stop-color': '#f59e0b', 'stop-opacity': '0.06' }),
    );
    defs.appendChild(gradient);
    svg.appendChild(defs);

    svg.appendChild(create('path', {
        class: 'learning-mountain__area',
        d: `M ${leftBaseX} ${baseY} C 82 112, 116 60, ${peakX} ${peakY} C 238 58, 284 106, ${rightBaseX} ${baseY} Z`,
        fill: 'url(#learningMountainFill)',
    }));
    svg.appendChild(create('path', {
        class: 'learning-mountain__ridge',
        d: `M ${leftBaseX} ${baseY} C 82 112, 116 60, ${peakX} ${peakY} C 238 58, 284 106, ${rightBaseX} ${baseY}`,
    }));
    svg.appendChild(create('line', {
        class: 'learning-mountain__peak-flag-pole',
        x1: peakX + 9,
        x2: peakX + 9,
        y1: peakY + 4,
        y2: peakY - 15,
    }));
    svg.appendChild(create('path', {
        class: 'learning-mountain__peak-flag',
        d: `M ${peakX + 9} ${peakY - 15} L ${peakX + 38} ${peakY - 9} L ${peakX + 9} ${peakY - 2} Z`,
    }));
    svg.appendChild(create('circle', {
        class: 'learning-mountain__peak-dot',
        cx: peakX,
        cy: peakY,
        r: 4.8,
    }));
    const peakText = create('text', {
        class: 'learning-mountain__peak-label',
        x: peakX,
        y: peakY + 24,
        'text-anchor': 'middle',
    });
    peakText.textContent = peakLabel;
    svg.appendChild(peakText);

    svg.appendChild(create('line', {
        class: 'learning-mountain__self-line',
        x1: 42,
        x2: labelX - 7,
        y1: selfY,
        y2: selfY,
    }));
    svg.appendChild(create('circle', {
        class: 'learning-mountain__self-halo',
        cx: labelX - 8,
        cy: selfY,
        r: 8.5,
    }));
    svg.appendChild(create('circle', {
        class: 'learning-mountain__self-dot',
        cx: labelX - 8,
        cy: selfY,
        r: 3.5,
    }));
    const labelBg = create('rect', {
        class: 'learning-mountain__self-label-bg',
        x: labelX - 2,
        y: selfLabelY - 12,
        width: Math.min(82, Math.max(52, selfLabel.length * 8.2)),
        height: 17,
        rx: 8,
    });
    const selfText = create('text', {
        class: 'learning-mountain__self-label',
        x: labelX + 5,
        y: selfLabelY,
    });
    selfText.textContent = selfLabel;
    svg.append(labelBg, selfText);

    const setHint = () => {
        if (!hint) return;
        hint.textContent = samePerson
            ? `${leaderName} 位于山顶，修为 ${leaderScore.toFixed(1)}。`
            : `${currentName} 当前第 ${position.current.rank} / ${position.total} 位，修为 ${currentScore.toFixed(1)}。`;
    };

    svg.addEventListener('pointerenter', setHint);
    svg.addEventListener('focus', setHint);
    container.appendChild(svg);
    setHint();
}

function initCultivationAlertInbox() {
    const inbox = document.querySelector('[data-cultivation-alert-inbox]');
    if (!inbox) return;
    const classOfferingId = inbox.dataset.classOfferingId;
    const list = inbox.querySelector('[data-cultivation-alert-list]');
    if (!classOfferingId || !list) return;

    const showEmptyIfNeeded = () => {
        if (list.querySelector('[data-cultivation-alert-item]')) return;
        const empty = document.createElement('p');
        empty.className = 'learning-alert-empty';
        empty.textContent = '当前没有未处理修为预警。';
        list.replaceWith(empty);
    };

    const findAlertById = (alertId) => {
        const normalizedId = String(alertId || '');
        if (!normalizedId) return null;
        return Array.from(list.querySelectorAll('[data-cultivation-alert-item]'))
            .find((candidate) => String(candidate.dataset.alertId || '') === normalizedId);
    };

    const removeAlertById = (alertId) => {
        const item = findAlertById(alertId);
        if (!item) return false;
        item.remove();
        showEmptyIfNeeded();
        return true;
    };

    const markAlertSideEffect = (item, action, button) => {
        if (!item) return;
        const normalizedAction = String(action || '').replace(/_/g, '-');
        item.classList.remove('is-updating');
        item.classList.add(`has-${normalizedAction}`);
        const targetButton = button || item.querySelector(`[data-cultivation-alert-action="${action}"]`);
        if (targetButton) {
            targetButton.classList.add('is-done');
            targetButton.disabled = true;
            targetButton.textContent = action === 'private_message' ? '已私信' : '已备注';
        }
    };

    list.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-cultivation-alert-action]');
        if (!button) return;
        const item = button.closest('[data-cultivation-alert-item]');
        const alertId = item?.dataset.alertId;
        const action = button.dataset.cultivationAlertAction;
        if (!item || !alertId || !action) return;
        item.classList.add('is-updating');
        try {
            await apiFetch(`/api/classrooms/${classOfferingId}/learning/alerts/${alertId}/actions`, {
                method: 'POST',
                body: {
                    action,
                    snooze_days: action === 'snoozed' ? 7 : undefined,
                },
            });
            if (action === 'private_message' || action === 'support_note') {
                markAlertSideEffect(item, action, button);
                showToast(action === 'private_message' ? '已发送关怀私信。' : '已记入共享备注。', 'success');
                return;
            }
            removeAlertById(alertId);
            showToast(action === 'snoozed' ? '已静音本周预警。' : '预警已标记处理。', 'success');
        } catch (error) {
            item.classList.remove('is-updating');
            showToast(error.message || '预警状态更新失败。', 'error');
        }
    });

    window.addEventListener('message', (event) => {
        if (event.origin !== window.location.origin) return;
        const data = event.data || {};
        if (data.type === 'cultivation-alert-updated') {
            removeAlertById(data.alertId);
            return;
        }
        if (data.type === 'cultivation-alert-side-effect') {
            const item = findAlertById(data.alertId);
            markAlertSideEffect(item, data.action);
        }
    });
}

export function initLearningProgress(config = window.APP_CONFIG || {}) {
    initLearningProgressModal();
    initTeacherLearningRoster();
    initCultivationWeightSettings(config);
    initTeacherExamRoster(config);
    initCultivationAlertInbox();
    initStudentInsightModal();
    initStageExamButton(config);
    initLearningMountain(config);
    initLearningCertificateReveal(config);
}
