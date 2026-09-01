/**
 * LessonDoc 学习文档包向导(课程页入口)。
 *
 * 用法:课程卡「学习文档」按钮 → window.openLessonDocWizard(courseId)。
 * 无包 → 建包向导(课次划分预填课程模板 → 主题 → 生成范围);
 * 有包 → 管理面板(逐课状态/生成/重写/排除、绑定课堂、切主题、刷新引擎)。
 * 深链:/manage/teaching/courses?lessondoc=<courseId> 自动打开。
 *
 * 依赖:页面存在 window.COURSE_PAGE_DATA(courses 含 lessons);
 * 绑定课堂的课堂清单复用 GET /api/materials/{rootId}/learning-bindings
 * (返回 offerings:[{id, semester, class_name, course_name, home_bound}])。
 */

const THEMES = [
    { key: 'sky', label: '天蓝', color: '#0284c7' },
    { key: 'teal', label: '青绿', color: '#0d9488' },
    { key: 'violet', label: '紫', color: '#7c3aed' },
    { key: 'amber', label: '暖橙', color: '#d97706' },
    { key: 'rose', label: '玫红', color: '#e11d48' },
    { key: 'slate', label: '素雅', color: '#475569' },
];

const STATUS_LABELS = {
    pending: ['待生成', '#64748b'],
    queued: ['排队中', '#d97706'],
    running: ['生成中…', '#0284c7'],
    ready: ['✓ 就绪', '#16a34a'],
    failed: ['生成失败', '#dc2626'],
    excluded: ['已排除', '#94a3b8'],
};

let modalEl = null;
let pollTimer = null;
let currentCourseId = 0;

async function api(url, options = {}) {
    const resp = await fetch(url, {
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    let data = null;
    try { data = await resp.json(); } catch (e) { /* 空响应 */ }
    if (!resp.ok) {
        const message = (data && (data.detail?.message || data.detail || data.message)) || `请求失败(${resp.status})`;
        throw new Error(typeof message === 'string' ? message : JSON.stringify(message));
    }
    return data;
}

function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

function courseInfo(courseId) {
    const data = window.COURSE_PAGE_DATA || {};
    return (data.courses || []).find((c) => Number(c.id) === Number(courseId)) || null;
}

function closeWizard() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    modalEl?.remove();
    modalEl = null;
}

function shell(title, bodyHtml, footHtml = '') {
    closeWizard();
    modalEl = document.createElement('div');
    modalEl.className = 'modal-backdrop';
    modalEl.style.display = 'flex';
    modalEl.innerHTML = `
        <div class="modal-dialog modal-dialog-scrollable modal-dialog-wide">
            <div class="modal-content">
                <div class="modal-header">
                    <div>
                        <h3 class="modal-title">${esc(title)}</h3>
                        <p class="modal-subtitle">配置驱动的课程学习文档包(PPT 式课次页 + 课程首页,自动绑定课次)</p>
                    </div>
                    <button class="modal-close" data-ld-close>&times;</button>
                </div>
                <div class="modal-body" data-ld-body>${bodyHtml}</div>
                ${footHtml ? `<div class="modal-footer" data-ld-foot>${footHtml}</div>` : ''}
            </div>
        </div>`;
    document.body.appendChild(modalEl);
    modalEl.addEventListener('click', (event) => {
        if (event.target === modalEl || event.target.closest('[data-ld-close]')) closeWizard();
    });
    return modalEl;
}

function notify(message, isError = false) {
    const box = modalEl?.querySelector('[data-ld-notify]');
    if (!box) { if (isError) alert(message); return; }
    box.textContent = message;
    box.style.color = isError ? '#dc2626' : '#16a34a';
    box.style.display = 'block';
}

/* ---------------------------------------------------------------- 建包向导 */

function defaultLessonRows(course) {
    const lessons = Array.isArray(course?.lessons) ? course.lessons : [];
    if (lessons.length) {
        return lessons.map((lesson) => ({
            n: Number(lesson.order_index) || 0,
            title: lesson.title || '',
        })).filter((row) => row.n > 0);
    }
    const totalHours = Number(course?.total_hours) || 0;
    const count = totalHours ? Math.max(1, Math.floor(totalHours / 2)) : 0;
    return Array.from({ length: count }, (_, i) => ({ n: i + 1, title: `第${i + 1}次课` }));
}

function renderCreateView(course) {
    const rows = defaultLessonRows(course);
    const themeCards = THEMES.map((theme, i) => `
        <label style="display:inline-flex;align-items:center;gap:6px;margin:0 14px 8px 0;cursor:pointer;">
            <input type="radio" name="ldTheme" value="${theme.key}" ${i === 0 ? 'checked' : ''}>
            <span style="display:inline-block;width:16px;height:16px;border-radius:4px;background:${theme.color}"></span>
            ${theme.label}
        </label>`).join('');
    const rowsHtml = rows.map((row) => `
        <tr data-ld-row data-n="${row.n}">
            <td style="white-space:nowrap;padding:4px 8px;"><label style="display:flex;gap:6px;align-items:center;">
                <input type="checkbox" data-ld-include checked> 第${row.n}课</label></td>
            <td style="padding:4px 8px;"><input type="text" data-ld-title value="${esc(row.title)}" placeholder="课次标题" style="width:100%;"></td>
            <td style="padding:4px 8px;"><input type="text" data-ld-hint placeholder="本课生成提示(可选)" style="width:100%;"></td>
        </tr>`).join('');

    shell(`生成学习文档包 · ${course?.name || ''}`, `
        <div data-ld-notify style="display:none;margin-bottom:10px;font-size:.92em;"></div>
        ${rows.length ? '' : `<div class="callout" style="margin-bottom:12px;">该课程还没有课次划分:请先在课程卡「课次」里配置课堂设置(可用 AI 按教材拆分),再回来生成学习文档包。</div>`}
        <div style="margin-bottom:12px;">
            <strong>① 课次划分</strong>
            <span style="color:#64748b;font-size:.88em;">(取消勾选=排除该课次;标题与提示可修改)</span>
            <div style="max-height:300px;overflow:auto;border:1px solid #e2e8f0;border-radius:10px;margin-top:8px;">
                <table style="width:100%;border-collapse:collapse;font-size:.92em;">
                    <tbody>${rowsHtml}</tbody>
                </table>
            </div>
        </div>
        <div style="margin-bottom:12px;">
            <strong>② 配色主题</strong>
            <div style="margin-top:6px;">${themeCards}</div>
        </div>
        <div style="margin-bottom:12px;">
            <strong>③ 课程级生成提示(可选)</strong>
            <textarea data-ld-course-hint rows="2" style="width:100%;margin-top:6px;"
                placeholder="对整门课的编写要求,如:面向外语院校学生,弱化数学推导,多用生活类比"></textarea>
        </div>
        <div>
            <strong>④ 立即生成范围</strong>
            <select data-ld-scope style="margin-left:8px;">
                <option value="first2" selected>首页 + 前 2 课(推荐)</option>
                <option value="all">全部课次(耗时较长)</option>
                <option value="none">仅创建骨架,稍后手动生成</option>
            </select>
        </div>
    `, `
        <button class="btn btn-outline" data-ld-close>取消</button>
        <button class="btn btn-primary" data-ld-submit ${rows.length ? '' : 'disabled'}>创建学习文档包</button>
    `);

    modalEl.querySelector('[data-ld-submit]')?.addEventListener('click', async (event) => {
        const btn = event.currentTarget;
        btn.disabled = true;
        btn.textContent = '创建中…';
        try {
            const lessonRows = Array.from(modalEl.querySelectorAll('[data-ld-row]')).map((tr) => ({
                n: Number(tr.dataset.n),
                title: tr.querySelector('[data-ld-title]').value.trim(),
                userHint: tr.querySelector('[data-ld-hint]').value.trim(),
                excluded: !tr.querySelector('[data-ld-include]').checked,
            }));
            const payload = {
                course_id: Number(course.id),
                theme: modalEl.querySelector('input[name="ldTheme"]:checked')?.value || 'sky',
                course_hint: modalEl.querySelector('[data-ld-course-hint]').value.trim(),
                lessons: lessonRows,
                generate_scope: modalEl.querySelector('[data-ld-scope]').value,
            };
            const result = await api('/api/lessondoc/packs', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            await renderManageView(result.pack, course);
            notify(result.message || '学习文档包已创建');
        } catch (error) {
            btn.disabled = false;
            btn.textContent = '创建学习文档包';
            notify(error.message, true);
        }
    });
}

/* ---------------------------------------------------------------- 管理面板 */

function lessonRowHtml(lesson) {
    const [label, color] = STATUS_LABELS[lesson.gen_status] || [lesson.gen_status, '#64748b'];
    const warningBadge = (lesson.warnings || []).length
        ? `<span title="${esc(lesson.warnings.join('\n'))}" style="cursor:help;color:#d97706;">⚠${lesson.warnings.length}</span>`
        : '';
    const busy = lesson.gen_status === 'queued' || lesson.gen_status === 'running';
    const actions = lesson.gen_status === 'excluded'
        ? `<button class="btn btn-ghost btn-sm" data-ld-restore data-n="${lesson.lesson_no}">恢复</button>`
        : `
            <button class="btn btn-outline btn-sm" data-ld-gen data-n="${lesson.lesson_no}"
                data-rewrite="${lesson.gen_status === 'ready' ? 1 : 0}" ${busy ? 'disabled' : ''}>
                ${lesson.gen_status === 'ready' ? 'AI 重写' : 'AI 生成'}
            </button>
            ${lesson.gen_status === 'pending' || lesson.gen_status === 'failed'
                ? `<button class="btn btn-ghost btn-sm" data-ld-exclude data-n="${lesson.lesson_no}">排除</button>` : ''}`;
    return `
        <tr>
            <td style="white-space:nowrap;padding:6px 10px;">第${lesson.lesson_no}课</td>
            <td style="padding:6px 10px;"><span style="color:${color};font-weight:600;">${label}</span> ${warningBadge}</td>
            <td style="color:#64748b;font-size:.85em;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:6px 10px;"
                title="${esc(lesson.user_hint || '')}">${esc(lesson.user_hint || '')}</td>
            <td style="text-align:right;white-space:nowrap;padding:6px 10px;">${actions}</td>
        </tr>`;
}

async function renderManageView(packSummary, course) {
    const pack = packSummary;
    const themeOptions = THEMES.map((theme) =>
        `<option value="${theme.key}" ${String(pack.theme || '').startsWith(theme.key) ? 'selected' : ''}>${theme.label}</option>`
    ).join('');

    shell(`学习文档包 · ${course?.name || ''}`, `
        <div data-ld-notify style="display:none;margin-bottom:10px;font-size:.92em;"></div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:12px;">
            <span class="academic-badge is-accent">lessondoc ${esc(String(pack.spec_version || '2.0').replace('lessondoc/', ''))}</span>
            <strong data-ld-progress>${pack.ready_count} / ${pack.total_count} 课就绪</strong>
            <a class="btn btn-outline btn-sm" href="${esc(pack.render_shell_url)}" target="_blank" rel="noopener">打开首页</a>
            <label style="font-size:.9em;">主题
                <select data-ld-theme>${themeOptions}</select>
            </label>
            <button class="btn btn-ghost btn-sm" data-ld-refresh-assets title="把包内引擎更新到平台最新版本">刷新引擎</button>
        </div>
        <div style="display:flex;gap:8px;margin-bottom:10px;">
            <button class="btn btn-primary btn-sm" data-ld-batch>补齐待生成课次</button>
            <button class="btn btn-outline btn-sm" data-ld-bind>绑定课堂</button>
        </div>
        <div style="max-height:340px;overflow:auto;border:1px solid #e2e8f0;border-radius:10px;">
            <table style="width:100%;border-collapse:collapse;font-size:.92em;">
                <tbody data-ld-lessons>${(pack.lessons || []).map(lessonRowHtml).join('')}</tbody>
            </table>
        </div>
        <div data-ld-bind-panel style="display:none;margin-top:12px;border:1px solid #e2e8f0;border-radius:10px;padding:12px;"></div>
    `, `<button class="btn btn-outline" data-ld-close>关闭</button>`);

    const packId = pack.id;

    async function reload() {
        try {
            const data = await api(`/api/lessondoc/packs/${packId}`);
            const fresh = data.pack;
            const tbody = modalEl?.querySelector('[data-ld-lessons]');
            const progress = modalEl?.querySelector('[data-ld-progress]');
            if (tbody) tbody.innerHTML = (fresh.lessons || []).map(lessonRowHtml).join('');
            if (progress) progress.textContent = `${fresh.ready_count} / ${fresh.total_count} 课就绪`;
            const busy = (fresh.lessons || []).some((l) => l.gen_status === 'queued' || l.gen_status === 'running');
            if (!busy && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
            if (busy && !pollTimer) pollTimer = setInterval(reload, 5000);
        } catch (e) { /* 弹窗可能已关闭 */ }
    }
    if ((pack.lessons || []).some((l) => l.gen_status === 'queued' || l.gen_status === 'running')) {
        pollTimer = setInterval(reload, 5000);
    }

    modalEl.addEventListener('click', async (event) => {
        const gen = event.target.closest('[data-ld-gen]');
        const exclude = event.target.closest('[data-ld-exclude]');
        const restore = event.target.closest('[data-ld-restore]');
        try {
            if (gen) {
                const n = Number(gen.dataset.n);
                const isRewrite = gen.dataset.rewrite === '1';
                const hint = prompt(
                    isRewrite ? `第${n}课 AI 重写:请输入改进要求(可留空)` : `第${n}课 AI 生成:补充提示(可留空)`,
                    '');
                if (hint === null) return;
                const result = await api(`/api/lessondoc/packs/${packId}/lessons/${n}/generate`, {
                    method: 'POST',
                    body: JSON.stringify({ mode: isRewrite ? 'rewrite' : 'generate', user_hint: hint }),
                });
                notify(result.message);
                await reload();
                if (!pollTimer) pollTimer = setInterval(reload, 5000);
            } else if (exclude || restore) {
                const n = Number((exclude || restore).dataset.n);
                await api(`/api/lessondoc/packs/${packId}/lessons/${n}`, {
                    method: 'PUT',
                    body: JSON.stringify({ excluded: Boolean(exclude) }),
                });
                await reload();
            } else if (event.target.closest('[data-ld-batch]')) {
                const result = await api(`/api/lessondoc/packs/${packId}/generate-batch`, {
                    method: 'POST', body: JSON.stringify({}),
                });
                notify(result.message);
                await reload();
                if (!pollTimer) pollTimer = setInterval(reload, 5000);
            } else if (event.target.closest('[data-ld-refresh-assets]')) {
                const result = await api(`/api/lessondoc/packs/${packId}/refresh-assets`, { method: 'POST' });
                notify(result.message);
            } else if (event.target.closest('[data-ld-bind]')) {
                await openBindPanel(pack);
            }
        } catch (error) {
            notify(error.message, true);
        }
    });

    modalEl.querySelector('[data-ld-theme]')?.addEventListener('change', async (event) => {
        try {
            const result = await api(`/api/lessondoc/packs/${packId}/theme`, {
                method: 'PUT', body: JSON.stringify({ theme: event.target.value }),
            });
            notify(result.message);
        } catch (error) { notify(error.message, true); }
    });
}

async function openBindPanel(pack) {
    const panel = modalEl?.querySelector('[data-ld-bind-panel]');
    if (!panel) return;
    if (panel.style.display !== 'none') { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    panel.innerHTML = '正在加载课堂列表…';
    try {
        // 复用材料多绑定接口的课堂清单(root 材料即包根)
        const data = await api(`/api/materials/${pack.root_material_id}/learning-bindings`);
        const offerings = data.offerings || [];
        if (!offerings.length) {
            panel.innerHTML = '你还没有可绑定的课堂。先在「开设课堂」创建课堂后再来绑定。';
            return;
        }
        panel.innerHTML = `
            <strong>选择要绑定的课堂</strong>
            <p style="color:#64748b;font-size:.85em;margin:4px 0 8px;">
                绑定 = 首页挂到课堂主页,lesson_N 自动对应第 N 次课;之后新生成的课次要再点一次绑定同步。
            </p>
            <div style="max-height:180px;overflow:auto;">
                ${offerings.map((o) => `
                    <label style="display:flex;gap:8px;align-items:center;padding:4px 0;">
                        <input type="checkbox" data-ld-offering value="${o.id}" ${o.home_bound ? 'checked' : ''}>
                        ${esc(o.course_name || '')} · ${esc(o.class_name || '')}${o.semester ? `(${esc(o.semester)})` : ''}
                        ${o.home_bound ? '<span style="color:#16a34a;font-size:.82em;">已绑首页</span>' : ''}
                    </label>`).join('')}
            </div>
            <button class="btn btn-primary btn-sm" data-ld-bind-submit style="margin-top:8px;">确认绑定</button>`;
        panel.querySelector('[data-ld-bind-submit]').addEventListener('click', async () => {
            const ids = Array.from(panel.querySelectorAll('[data-ld-offering]:checked')).map((cb) => Number(cb.value));
            if (!ids.length) { notify('请先勾选课堂', true); return; }
            try {
                const result = await api(`/api/lessondoc/packs/${pack.id}/bind`, {
                    method: 'POST', body: JSON.stringify({ class_offering_ids: ids }),
                });
                const binding = result.binding || {};
                notify(`绑定完成:首页 ${binding.total_home_assignments ?? 0} 处,课次 ${binding.total_assignments ?? 0} 处`);
                panel.style.display = 'none';
            } catch (error) { notify(error.message, true); }
        });
    } catch (error) {
        panel.innerHTML = `<span style="color:#dc2626;">${esc(error.message)}</span>`;
    }
}

/* ---------------------------------------------------------------- 入口 */

/** 按 pack_id 直开管理面板(材料页「管理课次」用,不经过课程维度查找)。 */
window.openLessonDocPackManager = async function openLessonDocPackManager(packId) {
    shell('学习文档包', '<p>正在加载学习文档包…</p>');
    try {
        const data = await api(`/api/lessondoc/packs/${Number(packId)}`);
        const pack = data.pack;
        const courseName = pack?.manifest?.course?.name || '';
        await renderManageView(pack, { id: pack.course_id, name: courseName });
    } catch (error) {
        shell('学习文档包', `<p style="color:#dc2626;">${esc(error.message)}</p>`);
    }
};

window.openLessonDocWizard = async function openLessonDocWizard(courseId) {
    currentCourseId = Number(courseId);
    const course = courseInfo(currentCourseId) || { id: currentCourseId, name: '' };
    shell(`学习文档包 · ${course.name || ''}`, '<p>正在检查该课程的学习文档包…</p>');
    try {
        const data = await api(`/api/lessondoc/packs?course_id=${currentCourseId}`);
        const packs = data.packs || [];
        if (packs.length) {
            await renderManageView(packs[0], course);
        } else {
            renderCreateView(course);
        }
    } catch (error) {
        shell('学习文档包', `<p style="color:#dc2626;">${esc(error.message)}</p>`);
    }
};

document.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-action="lessondoc-pack"]');
    if (!btn) return;
    event.preventDefault();
    event.stopPropagation();
    window.openLessonDocWizard(btn.dataset.courseId);
});

/* 深链 ?lessondoc=<courseId> 自动打开 */
const deepLink = new URLSearchParams(location.search).get('lessondoc');
if (deepLink && /^\d+$/.test(deepLink)) {
    if (document.readyState !== 'loading') {
        window.openLessonDocWizard(deepLink);
    } else {
        window.addEventListener('DOMContentLoaded', () => window.openLessonDocWizard(deepLink));
    }
}
