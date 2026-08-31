// 教务同步后「一键开设课堂」共享组件：同步结果面板与开设课堂页共用。
// 设计语言：teal 渐变 hero + 清单行卡 + 行内执行状态，与合并向导同族。
import { apiFetch } from '/static/js/api.js';

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

const SPARK_ICON = `
<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <path d="M13 2 4.5 13.5h5L10 22l8.5-11.5h-5L13 2Z"
        fill="currentColor" stroke="currentColor" stroke-width="1.2"
        stroke-linejoin="round"/>
</svg>`;

function candidateKey(item) {
    return `${item.course_id}::${item.teaching_class_id || item.teaching_class_name}`;
}

function textbookOptions(textbooks, suggested) {
    const suggestedId = suggested?.id || '';
    const options = [`<option value="">暂不选择教材</option>`];
    for (const item of textbooks) {
        const selected = String(item.id) === String(suggestedId) ? ' selected' : '';
        const suffix = String(item.id) === String(suggestedId) ? '（沿用本课程历史教材）' : '';
        options.push(`<option value="${item.id}"${selected}>${escapeHtml(item.title)}${escapeHtml(suffix)}</option>`);
    }
    return options.join('');
}

function renderCandidateRow(item, textbooks) {
    const key = candidateKey(item);
    const classChips = (item.class_names || []).map(
        (name) => `<span class="obs-chip">${escapeHtml(name)}</span>`
    ).join('');
    return `
    <label class="obs-row" data-obs-row data-obs-key="${escapeHtml(key)}">
        <span class="obs-row__check">
            <input type="checkbox" checked data-obs-check
                   data-course-id="${item.course_id}"
                   data-teaching-class-id="${escapeHtml(item.teaching_class_id || '')}"
                   data-teaching-class-name="${escapeHtml(item.teaching_class_name || '')}"
                   data-label="${escapeHtml(item.course_name)} · ${escapeHtml((item.class_names || []).join('·'))}">
        </span>
        <span class="obs-row__main">
            <span class="obs-row__title">
                <strong>${escapeHtml(item.course_name)}</strong>
                ${item.course_code ? `<em class="obs-code">${escapeHtml(item.course_code)}</em>` : ''}
                ${item.is_combined ? `<em class="obs-combined">合班 · ${item.class_ids.length} 班</em>` : ''}
            </span>
            <span class="obs-row__chips">${classChips}</span>
        </span>
        <span class="obs-row__stats">
            <span><strong>${item.student_count}</strong><small>名学生</small></span>
            <span><strong>${item.session_count}</strong><small>次排课</small></span>
        </span>
        <span class="obs-row__textbook">
            <select class="form-control" data-obs-textbook>${textbookOptions(textbooks, item.suggested_textbook)}</select>
        </span>
        <span class="obs-row__status" data-obs-status></span>
    </label>`;
}

function renderShell(payload) {
    const { candidates, blocked, summary } = payload;
    const blockedNote = blocked?.length
        ? `<p class="obs-blocked">另有 ${blocked.length} 个教学班暂不可开设：${blocked.map((b) => `${escapeHtml(b.course_name)}（${escapeHtml(b.reason)}）`).join('；')}</p>`
        : '';
    return `
    <section class="obs-panel" data-obs-panel>
        <header class="obs-hero">
            <span class="obs-hero__icon">${SPARK_ICON}</span>
            <span class="obs-hero__copy">
                <strong>课堂一键就绪</strong>
                <span>检测到 <b>${summary.course_count}</b> 门课程的 <b>${summary.candidate_count}</b> 个教学班可直接开设课堂，覆盖 <b>${summary.student_count}</b> 名学生——课程、班级、课次与真实排课均已同步完成，教材可留空稍后补选。</span>
            </span>
        </header>
        <div class="obs-list">
            ${candidates.map((item) => renderCandidateRow(item, payload.textbooks)).join('')}
        </div>
        ${blockedNote}
        <footer class="obs-footer">
            <span class="obs-footer__hint">未选教材的课堂暂不能生成 AI 助教配置，其余功能不受影响。</span>
            <span class="obs-footer__actions">
                <button type="button" class="btn btn-ghost btn-sm" data-obs-toggle-all>全选 / 清空</button>
                <button type="button" class="btn btn-primary" data-obs-execute>一键开设 <b data-obs-count>${candidates.length}</b> 个课堂</button>
            </span>
        </footer>
    </section>`;
}

function renderDone(result, variant) {
    const parts = [`已创建 <b>${result.created_count}</b> 个课堂`];
    if (result.skipped_count) parts.push(`跳过 ${result.skipped_count}`);
    if (result.failed_count) parts.push(`<span class="obs-danger">失败 ${result.failed_count}</span>`);
    const followUp = variant === 'sync-dialog'
        ? '<a class="obs-done__link" href="/manage/teaching/offerings">前往开设课堂页查看 →</a>'
        : '<button type="button" class="btn btn-ghost btn-sm" data-obs-reload>刷新页面查看</button>';
    return `
    <div class="obs-done">
        <span class="obs-done__badge" aria-hidden="true">✓</span>
        <span>${parts.join(' · ')}</span>
        ${followUp}
    </div>`;
}

export async function mountOfferingBootstrap(container, { semesterId, variant = 'page', onCreated } = {}) {
    if (!container || !semesterId) return false;
    let payload;
    try {
        payload = await apiFetch(
            `/api/manage/class_offerings/bootstrap/candidates?semester_id=${Number(semesterId)}`,
            { silent: true },
        );
    } catch (error) {
        return false; // 候选检测失败静默，不打断主流程
    }
    if (!payload?.candidates?.length) return false;

    container.innerHTML = renderShell(payload);
    container.hidden = false;
    const panel = container.querySelector('[data-obs-panel]');
    const executeBtn = panel.querySelector('[data-obs-execute]');
    const countNode = panel.querySelector('[data-obs-count]');

    const checkedBoxes = () => [...panel.querySelectorAll('[data-obs-check]:checked')];
    const refreshCount = () => {
        const total = checkedBoxes().length;
        countNode.textContent = String(total);
        executeBtn.disabled = total === 0;
    };
    panel.addEventListener('change', (event) => {
        if (event.target.closest('[data-obs-check]')) {
            event.target.closest('[data-obs-row]')?.classList.toggle('is-unchecked', !event.target.checked);
            refreshCount();
        }
    });
    panel.querySelector('[data-obs-toggle-all]').addEventListener('click', () => {
        const boxes = [...panel.querySelectorAll('[data-obs-check]')];
        const anyUnchecked = boxes.some((box) => !box.checked);
        boxes.forEach((box) => {
            box.checked = anyUnchecked;
            box.closest('[data-obs-row]')?.classList.toggle('is-unchecked', !anyUnchecked);
        });
        refreshCount();
    });

    executeBtn.addEventListener('click', async () => {
        const boxes = checkedBoxes();
        if (!boxes.length) return;
        const selections = boxes.map((box) => ({
            course_id: Number(box.dataset.courseId),
            teaching_class_id: box.dataset.teachingClassId || '',
            teaching_class_name: box.dataset.teachingClassName || '',
            label: box.dataset.label || '',
            textbook_id: box.closest('[data-obs-row]')?.querySelector('[data-obs-textbook]')?.value || null,
        }));
        executeBtn.disabled = true;
        executeBtn.innerHTML = '<span class="obs-spinner" aria-hidden="true"></span> 正在开设…';
        boxes.forEach((box) => {
            const status = box.closest('[data-obs-row]')?.querySelector('[data-obs-status]');
            if (status) status.innerHTML = '<span class="obs-status is-busy">创建中</span>';
        });
        try {
            const result = await apiFetch('/api/manage/class_offerings/bootstrap/execute', {
                method: 'POST',
                body: { semester_id: Number(semesterId), selections },
                silent: true,
            });
            const byKey = new Map((result.results || []).map((item) => [
                `${item.key?.[0]}::${item.key?.[1]}`, item,
            ]));
            for (const box of boxes) {
                const key = `${box.dataset.courseId}::${box.dataset.teachingClassId || box.dataset.teachingClassName}`;
                const row = box.closest('[data-obs-row]');
                const status = row?.querySelector('[data-obs-status]');
                const item = byKey.get(key);
                if (!status) continue;
                if (item?.status === 'created') {
                    status.innerHTML = `<a class="obs-status is-ok" href="/classroom/${item.offering_id}">已创建 · 进入课堂 →</a>`;
                    row.classList.add('is-created');
                } else if (item?.status === 'skipped') {
                    status.innerHTML = `<span class="obs-status is-muted" title="${escapeHtml(item.message)}">已存在</span>`;
                } else {
                    status.innerHTML = `<span class="obs-status is-fail" title="${escapeHtml(item?.message || '创建失败')}">失败 · ${escapeHtml((item?.message || '未知原因').slice(0, 42))}</span>`;
                }
                box.disabled = true;
            }
            panel.querySelector('.obs-footer').outerHTML = renderDone(result, variant);
            panel.querySelector('[data-obs-reload]')?.addEventListener('click', () => window.location.reload());
            if (typeof onCreated === 'function') onCreated(result);
        } catch (error) {
            executeBtn.disabled = false;
            executeBtn.textContent = '重试开设';
            boxes.forEach((box) => {
                const status = box.closest('[data-obs-row]')?.querySelector('[data-obs-status]');
                if (status) status.innerHTML = `<span class="obs-status is-fail">${escapeHtml(error.message || '请求失败')}</span>`;
            });
        }
    });
    refreshCount();
    return true;
}
