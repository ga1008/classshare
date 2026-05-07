import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';

const modal = document.querySelector('[data-teacher-onboarding-modal]');
const openButtons = Array.from(document.querySelectorAll('[data-teacher-onboarding-open]'));

if (modal && openButtons.length > 0) {
    const dialog = modal.querySelector('.teacher-onboarding-dialog');
    const submodal = modal.querySelector('[data-onboarding-submodal]');
    const elements = {
        closeButtons: Array.from(modal.querySelectorAll('[data-teacher-onboarding-dismiss]')),
        welcome: modal.querySelector('[data-onboarding-welcome]'),
        history: modal.querySelector('[data-onboarding-history]'),
        content: modal.querySelector('[data-onboarding-content]'),
        stepCount: modal.querySelector('[data-onboarding-step-count]'),
        footerNote: modal.querySelector('[data-onboarding-footer-note]'),
        prevButton: modal.querySelector('[data-onboarding-prev]'),
        nextButton: modal.querySelector('[data-onboarding-next]'),
        submodalTitle: modal.querySelector('[data-submodal-title]'),
        submodalBody: modal.querySelector('[data-submodal-body]'),
        submodalClose: modal.querySelector('[data-submodal-close]'),
    };

    const state = {
        payload: null,
        activeIndex: 0,
        isOpen: false,
        lastFocused: null,
        bodyOverflow: '',
        closeTimer: null,
        welcomeTimer: null,
        completing: false,
        materialExpandedIds: new Set(),
        materialLoadingIds: new Set(),
        selected: {
            semesterId: null,
            courseId: null,
            courseName: '',
            department: '',
            textbookId: null,
            materialIds: new Set(),
            classId: null,
            description: '',
            credits: 2,
            totalHours: 32,
            sectName: '',
            lessons: [],
            firstClassDate: '',
            weeklySchedule: [{ weekday: 0, section_count: 2 }],
            aiSystemPrompt: '',
            aiSyllabus: '',
            classroomUrl: '',
            courseDescriptionDraft: '',
            creditTouched: false,
        },
    };

    const steps = [
        { key: 'semester', label: '学期', prompt: '您是准备上哪个学期的课呢？' },
        { key: 'course', label: '课程', prompt: '请输入您要开课的课程名称' },
        { key: 'textbook', label: '教材', prompt: '在真正开始开设课堂前，有一些准备工作先要确认一下' },
        { key: 'materials', label: '教学材料', prompt: '上课用到的文档、PPT、思维导图或者其他材料' },
        { key: 'class', label: '班级', prompt: '这门课准备给哪个班级上呢？' },
        { key: 'details', label: '课程细节', prompt: '反过来补充一些课程细节和课堂安排' },
        { key: 'ai', label: 'AI 助教', prompt: '根据已有内容配置课堂 AI 助教' },
        { key: 'success', label: '完成', prompt: '恭喜开课成功' },
    ];

    function wizard() {
        return state.payload?.wizard || {};
    }

    function list(name) {
        const value = wizard()[name];
        return Array.isArray(value) ? value : [];
    }

    function selectedTextbook() {
        const textbookId = Number(state.selected.textbookId || 0);
        return list('textbooks').find((item) => Number(item.id) === textbookId) || null;
    }

    function selectedClass() {
        const classId = Number(state.selected.classId || 0);
        return list('classes').find((item) => Number(item.id) === classId) || null;
    }

    function selectedSemester() {
        const semesterId = Number(state.selected.semesterId || 0);
        return list('semesters').find((item) => Number(item.id) === semesterId) || null;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function normalizeText(value) {
        return String(value ?? '').trim();
    }

    function numberValue(value, fallback = 0) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function todayIso() {
        const now = new Date();
        return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    }

    function jsDateToCourseWeekday(rawDate) {
        const date = new Date(`${rawDate || todayIso()}T00:00:00`);
        if (Number.isNaN(date.getTime())) return 0;
        return (date.getDay() + 6) % 7;
    }

    function computeCredits(totalHours) {
        return Math.max(0.5, Math.round((Number(totalHours || 0) / 16) * 10) / 10);
    }

    function setDefaultSelections() {
        const defaults = wizard().defaults || {};
        if (!state.selected.department) {
            state.selected.department = normalizeText(defaults.department) || normalizeText(list('departments')[0]);
        }
        if (!state.selected.totalHours) {
            state.selected.totalHours = Number(defaults.total_hours || 32);
        }
        if (!state.selected.credits) {
            state.selected.credits = Number(defaults.credits || computeCredits(state.selected.totalHours));
        }
        if (!state.selected.firstClassDate) {
            state.selected.firstClassDate = selectedSemester()?.start_date || todayIso();
        }
        if (!Array.isArray(state.selected.weeklySchedule) || !state.selected.weeklySchedule.length) {
            state.selected.weeklySchedule = defaults.weekly_schedule || [{ weekday: jsDateToCourseWeekday(state.selected.firstClassDate), section_count: 2 }];
        }
    }

    async function loadState({ silent = false } = {}) {
        try {
            state.payload = await apiFetch('/api/manage/teacher-onboarding/state', { silent: true });
            setDefaultSelections();
            return state.payload;
        } catch (error) {
            if (!silent) {
                showToast(error.message || '新建课堂引导状态读取失败', 'error');
            }
            return null;
        }
    }

    async function reloadStateAndRender() {
        await loadState({ silent: true });
        render();
    }

    async function markDismissed(reason) {
        try {
            state.payload = await apiFetch('/api/manage/teacher-onboarding/dismiss', {
                method: 'POST',
                body: { reason },
                silent: true,
            });
            return true;
        } catch (error) {
            showToast(error.message || '引导状态保存失败', 'error');
            return false;
        }
    }

    function canGoNext() {
        const selected = state.selected;
        const key = steps[state.activeIndex]?.key;
        if (key === 'semester') return Boolean(selected.semesterId);
        if (key === 'course') return Boolean(normalizeText(selected.courseName));
        if (key === 'textbook') return Boolean(selected.textbookId);
        if (key === 'materials') return true;
        if (key === 'class') return Boolean(selected.classId);
        if (key === 'details') {
            const totalHours = Number(selected.totalHours || 0);
            const sectionTotal = selected.lessons.reduce((sum, item) => sum + Number(item.section_count || 0), 0);
            return Boolean(
                normalizeText(selected.courseName)
                && totalHours > 0
                && Number(selected.credits || 0) > 0
                && normalizeText(selected.description)
                && selected.lessons.length
                && sectionTotal === totalHours
                && normalizeText(selected.firstClassDate)
                && selected.weeklySchedule.length
            );
        }
        if (key === 'ai') return true;
        if (key === 'success') return Boolean(selected.classroomUrl);
        return false;
    }

    function nextLabel() {
        const key = steps[state.activeIndex]?.key;
        if (key === 'materials' && state.selected.materialIds.size === 0) return '跳过';
        if (key === 'ai') return state.completing ? '正在开课...' : '完成开课';
        if (key === 'success') return '进入课堂';
        return '下一步';
    }

    function updateFooter() {
        const key = steps[state.activeIndex]?.key;
        if (elements.prevButton) {
            elements.prevButton.disabled = state.activeIndex === 0 || state.completing;
            elements.prevButton.style.visibility = key === 'success' ? 'hidden' : '';
        }
        if (elements.nextButton) {
            elements.nextButton.disabled = !canGoNext() || state.completing;
            elements.nextButton.textContent = nextLabel();
        }
        if (!elements.footerNote) return;
        if (key === 'materials' && state.selected.materialIds.size === 0) {
            elements.footerNote.textContent = '教学材料可以稍后继续补充，当前步骤允许直接跳过。';
        } else if (key === 'details') {
            const total = state.selected.lessons.reduce((sum, item) => sum + Number(item.section_count || 0), 0);
            elements.footerNote.textContent = `当前课堂设置合计 ${total} / ${state.selected.totalHours || 0} 学时。`;
        } else if (key === 'success') {
            elements.footerNote.textContent = '课堂已经创建好，可以直接进入课堂继续调整细节。';
        } else {
            elements.footerNote.textContent = '每一步只确认一件事，随时可以返回调整。';
        }
    }

    function renderHistory() {
        if (!elements.history) return;
        const chips = steps.slice(0, state.activeIndex).filter((step) => step.key !== 'success');
        elements.history.closest('.teacher-onboarding-welcome-area')?.classList.toggle('has-history', chips.length > 0);
        elements.history.innerHTML = chips.map((step) => (
            `<span class="teacher-onboarding-history-chip">${escapeHtml(step.label)}</span>`
        )).join('');
    }

    function optionCard({ id, selected, title, meta = '', badges = [], details = '', muted = false, extraClass = '' }) {
        const badgeHtml = badges.filter(Boolean).map((badge) => (
            `<span class="onboarding-badge ${escapeHtml(badge.className || '')}">${escapeHtml(badge.label || badge)}</span>`
        )).join('');
        return `
            <button
                type="button"
                class="onboarding-option-card${selected ? ' is-selected' : ''}${muted ? ' is-muted' : ''}${extraClass ? ` ${escapeHtml(extraClass)}` : ''}"
                data-select-id="${escapeHtml(id)}"
            >
                <strong>${escapeHtml(title)}</strong>
                ${meta ? `<span>${escapeHtml(meta)}</span>` : ''}
                ${badgeHtml ? `<div class="onboarding-badge-row">${badgeHtml}</div>` : ''}
                ${details ? `<small>${escapeHtml(details)}</small>` : ''}
            </button>
        `;
    }

    function renderStepShell(innerHtml) {
        return `<div class="onboarding-step-shell">${innerHtml}</div>`;
    }

    function renderTitle(prompt, helper = '') {
        return `
            <div class="onboarding-step-title">
                <h3 id="teacherOnboardingPrompt">${escapeHtml(prompt)}</h3>
                ${helper ? `<p>${escapeHtml(helper)}</p>` : ''}
            </div>
        `;
    }

    function bindCardSelection(container, callback) {
        container.querySelectorAll('[data-select-id]').forEach((button) => {
            button.addEventListener('click', () => callback(button.dataset.selectId));
        });
    }

    function renderSemesterStep(container) {
        const semesters = list('semesters');
        const body = semesters.length ? semesters.map((semester) => optionCard({
            id: semester.id,
            selected: Number(state.selected.semesterId) === Number(semester.id),
            title: semester.name,
            meta: `${semester.start_date || '未设置'} 至 ${semester.end_date || '未设置'}`,
            badges: [
                { label: `${semester.week_count || 0} 周`, className: 'is-blue' },
                semester.is_current ? { label: '当前学期', className: 'is-green' } : null,
            ],
        })).join('') : '<div class="onboarding-empty">还没有可选学期，先新建一个学期再继续。</div>';

        container.innerHTML = renderStepShell(`
            ${renderTitle(steps[0].prompt, '学期会影响教学日历、周次和课堂时间轴。')}
            <div class="onboarding-toolbar">
                <span class="onboarding-hint">单击卡片选中学期。</span>
                <button type="button" class="btn btn-outline" data-action="create-semester">新建学期</button>
            </div>
            <div class="onboarding-grid">${body}</div>
        `);
        bindCardSelection(container, (id) => {
            state.selected.semesterId = Number(id);
            state.selected.firstClassDate = selectedSemester()?.start_date || state.selected.firstClassDate || todayIso();
            state.selected.weeklySchedule = [{ weekday: jsDateToCourseWeekday(state.selected.firstClassDate), section_count: 2 }];
            render();
        });
        container.querySelector('[data-action="create-semester"]')?.addEventListener('click', openSemesterSubmodal);
    }

    function courseSimilarityScore(course, keyword) {
        const name = normalizeText(course.name).toLowerCase();
        const target = normalizeText(keyword).toLowerCase();
        if (!target) return 0;
        if (name === target) return 100;
        if (name.includes(target) || target.includes(name)) return 70;
        const overlap = [...new Set(target.split(''))].filter((char) => name.includes(char)).length;
        return overlap;
    }

    function relatedClassSummary(course) {
        const ids = Array.isArray(course.related_class_ids) ? course.related_class_ids.map(Number) : [];
        const names = ids
            .map((id) => list('classes').find((item) => Number(item.id) === id)?.name)
            .filter(Boolean)
            .slice(0, 3);
        return names.length ? names.join('、') : '暂无正在上课的班级';
    }

    function renderCourseStep(container) {
        const courses = list('courses');
        const keyword = normalizeText(state.selected.courseName);
        const similarCourses = courses
            .map((course) => ({ course, score: courseSimilarityScore(course, keyword) }))
            .filter((item) => item.score > 0 || !keyword)
            .sort((a, b) => b.score - a.score || Number(b.course.offering_count || 0) - Number(a.course.offering_count || 0))
            .slice(0, 8);
        const departments = list('departments');
        const departmentOptions = departments.map((item) => `<option value="${escapeHtml(item)}"></option>`).join('');
        const similarHtml = similarCourses.length ? similarCourses.map(({ course }) => optionCard({
            id: course.id,
            selected: Number(state.selected.courseId) === Number(course.id),
            title: course.name,
            meta: course.department || '未设置系别',
            badges: [
                { label: `${course.total_hours || 0} 学时`, className: 'is-blue' },
                { label: `${course.offering_count || 0} 个课堂`, className: 'is-green' },
            ],
            details: '把鼠标移到卡片上可以看到更多确认信息。',
            extraClass: 'onboarding-course-similar',
        }).replace('</button>', `
            <span class="onboarding-course-more">
                系别：${escapeHtml(course.department || '未设置')}<br>
                已开课堂：${escapeHtml(course.offering_count || 0)} 个<br>
                正在上课：${escapeHtml(relatedClassSummary(course))}<br>
                课次：${escapeHtml(course.lesson_count || 0)} 次
            </span>
        </button>`)).join('') : '<div class="onboarding-empty">没有匹配到旧课程。继续输入后将按新课程处理。</div>';

        container.innerHTML = renderStepShell(`
            ${renderTitle(steps[1].prompt, '选择旧课程会自动绑定课程模板；只输入不选择时，即使同名也会按新课程创建。')}
            <div class="onboarding-field-grid">
                <div class="onboarding-field">
                    <label for="onboardingCourseNameInput">课程名称</label>
                    <input id="onboardingCourseNameInput" type="text" value="${escapeHtml(state.selected.courseName)}" placeholder="例如：动态 Web 程序设计">
                </div>
                <div class="onboarding-field">
                    <label for="onboardingCourseDepartmentInput">所属系别</label>
                    <input id="onboardingCourseDepartmentInput" type="text" list="onboardingDepartmentOptions" value="${escapeHtml(state.selected.department)}" placeholder="例如：网络工程系">
                    <datalist id="onboardingDepartmentOptions">${departmentOptions}</datalist>
                </div>
            </div>
            <div class="onboarding-recommend-panel">
                <strong>是“${escapeHtml(keyword || '这门')}”课程吗？</strong>
                <div class="onboarding-grid is-compact">${similarHtml}</div>
            </div>
        `);

        const nameInput = container.querySelector('#onboardingCourseNameInput');
        const departmentInput = container.querySelector('#onboardingCourseDepartmentInput');
        nameInput?.addEventListener('input', () => {
            const value = normalizeText(nameInput.value);
            const cursorPosition = nameInput.selectionStart || value.length;
            if (value !== state.selected.courseName) {
                state.selected.courseId = null;
                state.selected.courseName = value;
                state.selected.sectName = '';
            }
            render();
            window.requestAnimationFrame(() => {
                const nextInput = document.getElementById('onboardingCourseNameInput');
                if (nextInput) {
                    nextInput.focus({ preventScroll: true });
                    nextInput.setSelectionRange(cursorPosition, cursorPosition);
                }
            });
        });
        departmentInput?.addEventListener('input', () => {
            state.selected.department = normalizeText(departmentInput.value);
            updateFooter();
        });
        bindCardSelection(container, (id) => {
            const course = courses.find((item) => Number(item.id) === Number(id));
            if (!course) return;
            state.selected.courseId = Number(course.id);
            state.selected.courseName = course.name || '';
            state.selected.department = course.department || state.selected.department;
            state.selected.description = course.description || state.selected.description;
            state.selected.credits = Number(course.credits || state.selected.credits || computeCredits(course.total_hours));
            state.selected.totalHours = Number(course.total_hours || state.selected.totalHours || 32);
            state.selected.sectName = course.sect_name || '';
            state.selected.lessons = Array.isArray(course.lessons) ? course.lessons.map((lesson) => ({ ...lesson })) : [];
            state.selected.courseDescriptionDraft = '';
            render();
        });
    }

    function sortedTextbooks() {
        const courseId = Number(state.selected.courseId || 0);
        const courseName = normalizeText(state.selected.courseName).toLowerCase();
        const selectedId = Number(state.selected.textbookId || 0);
        return [...list('textbooks')].sort((a, b) => {
            const aSelected = selectedId && Number(a.id) === selectedId ? 1 : 0;
            const bSelected = selectedId && Number(b.id) === selectedId ? 1 : 0;
            if (aSelected !== bSelected) return bSelected - aSelected;
            const aLinked = courseId && Array.isArray(a.related_course_ids) && a.related_course_ids.includes(courseId) ? 1 : 0;
            const bLinked = courseId && Array.isArray(b.related_course_ids) && b.related_course_ids.includes(courseId) ? 1 : 0;
            if (aLinked !== bLinked) return bLinked - aLinked;
            const aName = `${a.title || ''} ${a.introduction || ''}`.toLowerCase().includes(courseName) ? 1 : 0;
            const bName = `${b.title || ''} ${b.introduction || ''}`.toLowerCase().includes(courseName) ? 1 : 0;
            return bName - aName || Number(b.offering_count || 0) - Number(a.offering_count || 0);
        });
    }

    function renderTextbookStep(container) {
        const textbooks = sortedTextbooks();
        const body = textbooks.length ? textbooks.map((textbook, index) => optionCard({
            id: textbook.id,
            selected: Number(state.selected.textbookId) === Number(textbook.id),
            title: textbook.title,
            meta: [textbook.author_display, textbook.publisher].filter(Boolean).join(' · '),
            badges: [
                index < 3 ? { label: '推荐', className: 'is-green' } : null,
                textbook.publication_year ? { label: textbook.publication_year, className: 'is-blue' } : null,
            ],
            details: textbook.introduction_preview || textbook.catalog_preview || '',
        })).join('') : '<div class="onboarding-empty">还没有教材。可以先录入教材名称，后续再补充附件和目录。</div>';

        container.innerHTML = renderStepShell(`
            ${renderTitle(steps[2].prompt, '请选择已有教材，或者重新录入一个。与当前课程关联度高的教材会排在前面。')}
            <div class="onboarding-toolbar">
                <span class="onboarding-hint">教材会用于课程简介、拆课和 AI 助教上下文。</span>
                <button type="button" class="btn btn-outline" data-action="create-textbook">新建教材</button>
            </div>
            <div class="onboarding-grid">${body}</div>
        `);
        bindCardSelection(container, (id) => {
            state.selected.textbookId = Number(id);
            state.selected.courseDescriptionDraft = '';
            render();
        });
        container.querySelector('[data-action="create-textbook"]')?.addEventListener('click', openTextbookSubmodal);
    }

    function normalizeMaterialItem(item) {
        return {
            ...item,
            id: Number(item.id || 0),
            parent_id: item.parent_id === null || item.parent_id === undefined ? null : Number(item.parent_id),
            root_id: Number(item.root_id || item.id || 0),
            child_count: Number(item.child_count || 0),
            related_course_ids: Array.isArray(item.related_course_ids) ? item.related_course_ids.map(Number) : [],
            is_markdown: Boolean(item.is_markdown || (item.node_type === 'file' && item.preview_type === 'markdown')),
        };
    }

    function upsertMaterials(items) {
        const wizardState = wizard();
        if (!Array.isArray(wizardState.materials)) wizardState.materials = [];
        const byId = new Map(wizardState.materials.map((item) => [Number(item.id), normalizeMaterialItem(item)]));
        (items || []).forEach((item) => {
            const normalized = normalizeMaterialItem(item);
            if (normalized.id) byId.set(normalized.id, { ...(byId.get(normalized.id) || {}), ...normalized });
        });
        wizardState.materials = Array.from(byId.values());
    }

    function materialTypeLabel(material) {
        if (material.node_type === 'folder') return '文件夹';
        if (material.preview_type === 'markdown') return 'Markdown';
        if (material.preview_type === 'image') return '图片';
        if (material.preview_type === 'text') return material.file_ext ? material.file_ext.toUpperCase() : '文本';
        return material.file_ext ? material.file_ext.toUpperCase() : '文件';
    }

    function materialCompare(a, b) {
        const courseId = Number(state.selected.courseId || 0);
        const aSelected = state.selected.materialIds.has(Number(a.id)) ? 1 : 0;
        const bSelected = state.selected.materialIds.has(Number(b.id)) ? 1 : 0;
        if (aSelected !== bSelected) return bSelected - aSelected;
        const aLinked = courseId && Array.isArray(a.related_course_ids) && a.related_course_ids.includes(courseId) ? 1 : 0;
        const bLinked = courseId && Array.isArray(b.related_course_ids) && b.related_course_ids.includes(courseId) ? 1 : 0;
        if (aLinked !== bLinked) return bLinked - aLinked;
        if (a.node_type !== b.node_type) return a.node_type === 'folder' ? -1 : 1;
        if (Boolean(a.is_markdown) !== Boolean(b.is_markdown)) return Number(Boolean(b.is_markdown)) - Number(Boolean(a.is_markdown));
        return String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN');
    }

    function sortedMaterials() {
        return [...list('materials')].map(normalizeMaterialItem).sort(materialCompare);
    }

    function buildMaterialTree() {
        const materials = sortedMaterials();
        const byId = new Map(materials.map((item) => [Number(item.id), item]));
        const childrenByParent = new Map();
        materials.forEach((item) => {
            const parentId = item.parent_id && byId.has(item.parent_id) ? item.parent_id : null;
            if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
            childrenByParent.get(parentId).push(item);
        });
        childrenByParent.forEach((items) => items.sort(materialCompare));
        return { roots: childrenByParent.get(null) || [], childrenByParent };
    }

    function materialIsRecommended(material) {
        const courseId = Number(state.selected.courseId || 0);
        return Boolean(courseId && Array.isArray(material.related_course_ids) && material.related_course_ids.includes(courseId));
    }

    function renderMaterialTreeNode(material, childrenByParent, depth = 0) {
        const materialId = Number(material.id);
        const isFolder = material.node_type === 'folder';
        const isExpanded = state.materialExpandedIds.has(materialId);
        const isSelected = state.selected.materialIds.has(materialId);
        const isLoading = state.materialLoadingIds.has(materialId);
        const children = childrenByParent.get(materialId) || [];
        const hasChildren = isFolder && (children.length > 0 || Number(material.child_count || 0) > 0);
        const badgeHtml = [
            materialIsRecommended(material) ? { label: '推荐', className: 'is-green' } : null,
            { label: materialTypeLabel(material), className: material.is_markdown ? 'is-blue' : 'is-amber' },
            isFolder && material.child_count ? { label: `${material.child_count} 项`, className: 'is-blue' } : null,
        ].filter(Boolean).map((badge) => (
            `<span class="onboarding-badge ${escapeHtml(badge.className || '')}">${escapeHtml(badge.label)}</span>`
        )).join('');
        const childrenHtml = isExpanded
            ? (children.length
                ? children.map((child) => renderMaterialTreeNode(child, childrenByParent, depth + 1)).join('')
                : `<div class="onboarding-material-tree-loading">${isLoading ? '正在加载子目录...' : '这个目录下暂时没有可展示材料。'}</div>`)
            : '';
        return `
            <article class="onboarding-material-node${isSelected ? ' is-selected' : ''}" style="--tree-depth:${depth}">
                <div class="onboarding-material-row">
                    <button
                        type="button"
                        class="onboarding-material-toggle"
                        data-material-toggle="${escapeHtml(materialId)}"
                        ${hasChildren ? '' : 'disabled'}
                        aria-label="${isExpanded ? '收起目录' : '展开目录'}"
                    >${hasChildren ? (isExpanded ? '⌄' : '›') : ''}</button>
                    <button type="button" class="onboarding-material-select" data-material-select="${escapeHtml(materialId)}">
                        <span class="onboarding-material-icon" aria-hidden="true">${isFolder ? '□' : '·'}</span>
                        <span class="onboarding-material-copy">
                            <strong>${escapeHtml(material.name || '未命名材料')}</strong>
                            <small>${escapeHtml(material.material_path || '材料库根目录')}</small>
                        </span>
                        ${badgeHtml ? `<span class="onboarding-badge-row">${badgeHtml}</span>` : ''}
                    </button>
                </div>
                ${childrenHtml ? `<div class="onboarding-material-children">${childrenHtml}</div>` : ''}
            </article>
        `;
    }

    function renderMaterialsStep(container) {
        const { roots, childrenByParent } = buildMaterialTree();
        const body = roots.length
            ? roots.map((material) => renderMaterialTreeNode(material, childrenByParent)).join('')
            : '<div class="onboarding-empty">还没有教学材料。可以先跳过，也可以马上导入文件或整个文件夹。</div>';

        container.innerHTML = renderStepShell(`
            ${renderTitle(steps[3].prompt, '课程材料通常按文件夹整理。可以选择整个目录，也可以展开后选择具体文件；后续还能使用深度思考 AI 协助生成或优化材料。')}
            <div class="onboarding-toolbar">
                <span class="onboarding-hint">默认收起目录；可多选，不选时下一步会显示为“跳过”。</span>
                <button type="button" class="btn btn-outline" data-action="create-material">导入材料</button>
            </div>
            <div class="onboarding-material-tree">${body}</div>
        `);
        container.querySelectorAll('[data-material-select]').forEach((button) => {
            button.addEventListener('click', () => {
                const materialId = Number(button.dataset.materialSelect);
                if (state.selected.materialIds.has(materialId)) {
                    state.selected.materialIds.delete(materialId);
                } else {
                    state.selected.materialIds.add(materialId);
                }
                render();
            });
        });
        container.querySelectorAll('[data-material-toggle]').forEach((button) => {
            button.addEventListener('click', () => toggleMaterialFolder(Number(button.dataset.materialToggle)));
        });
        container.querySelector('[data-action="create-material"]')?.addEventListener('click', openMaterialSubmodal);
    }

    async function toggleMaterialFolder(materialId) {
        if (!materialId || state.materialLoadingIds.has(materialId)) return;
        if (state.materialExpandedIds.has(materialId)) {
            state.materialExpandedIds.delete(materialId);
            render();
            return;
        }
        state.materialExpandedIds.add(materialId);
        const hasLoadedChildren = list('materials').some((item) => Number(item.parent_id || 0) === materialId);
        const folder = list('materials').find((item) => Number(item.id) === materialId);
        if (!hasLoadedChildren && Number(folder?.child_count || 0) > 0) {
            state.materialLoadingIds.add(materialId);
            render();
            try {
                const result = await apiFetch(`/api/materials/library?parent_id=${encodeURIComponent(materialId)}&sort_by=name&sort_order=asc`, { silent: true });
                upsertMaterials(result.items || []);
            } catch (error) {
                showToast(error.message || '加载子目录失败', 'error');
            } finally {
                state.materialLoadingIds.delete(materialId);
            }
        }
        render();
    }

    function toggleMaterialSelection(id) {
        const materialId = Number(id);
        if (!materialId) return;
        if (state.selected.materialIds.has(materialId)) {
            state.selected.materialIds.delete(materialId);
        } else {
            state.selected.materialIds.add(materialId);
        }
    }

    function selectedOrDescendantMaterialIds() {
        const selectedIds = [...state.selected.materialIds].map(Number);
        const allMaterials = list('materials').map(normalizeMaterialItem);
        const selectedFolders = new Set(
            allMaterials
                .filter((item) => selectedIds.includes(Number(item.id)) && item.node_type === 'folder')
                .map((item) => Number(item.id))
        );
        if (!selectedFolders.size) return selectedIds;
        const selectedPaths = allMaterials
            .filter((item) => selectedFolders.has(Number(item.id)))
            .map((item) => normalizeText(item.material_path))
            .filter(Boolean);
        allMaterials.forEach((item) => {
            const materialId = Number(item.id);
            const itemPath = normalizeText(item.material_path);
            if (selectedIds.includes(materialId) || !itemPath) return;
            if (selectedPaths.some((path) => itemPath.startsWith(`${path}/`))) selectedIds.push(materialId);
        });
        return selectedIds;
    }

    function sortedClasses() {
        const courseId = Number(state.selected.courseId || 0);
        const department = normalizeText(state.selected.department);
        const selectedId = Number(state.selected.classId || 0);
        return [...list('classes')].sort((a, b) => {
            const aSelected = selectedId && Number(a.id) === selectedId ? 1 : 0;
            const bSelected = selectedId && Number(b.id) === selectedId ? 1 : 0;
            if (aSelected !== bSelected) return bSelected - aSelected;
            const aLinked = courseId && Array.isArray(a.related_course_ids) && a.related_course_ids.includes(courseId) ? 1 : 0;
            const bLinked = courseId && Array.isArray(b.related_course_ids) && b.related_course_ids.includes(courseId) ? 1 : 0;
            if (aLinked !== bLinked) return bLinked - aLinked;
            const aDept = department && a.department === department ? 1 : 0;
            const bDept = department && b.department === department ? 1 : 0;
            return bDept - aDept || String(a.name || '').localeCompare(String(b.name || ''));
        });
    }

    function renderClassStep(container) {
        const classes = sortedClasses();
        const body = classes.length ? classes.map((item, index) => optionCard({
            id: item.id,
            selected: Number(state.selected.classId) === Number(item.id),
            title: item.name,
            meta: item.department || '未设置系别',
            badges: [
                index < 4 ? { label: '推荐', className: 'is-green' } : null,
                { label: `${item.student_count || 0} 名学生`, className: 'is-blue' },
            ],
        })).join('') : '<div class="onboarding-empty">还没有可选班级。可以先创建一个空班级，稍后再导入学生名单。</div>';

        container.innerHTML = renderStepShell(`
            ${renderTitle(steps[4].prompt, '课程和班级都按系别归属管理，同系别班级会优先推荐。')}
            <div class="onboarding-toolbar">
                <span class="onboarding-hint">当前课程系别：${escapeHtml(state.selected.department || '未设置')}</span>
                <button type="button" class="btn btn-outline" data-action="create-class">录入新班级</button>
            </div>
            <div class="onboarding-grid">${body}</div>
        `);
        bindCardSelection(container, (id) => {
            state.selected.classId = Number(id);
            render();
        });
        container.querySelector('[data-action="create-class"]')?.addEventListener('click', openClassSubmodal);
    }

    function ensureLessons() {
        const totalHours = Number(state.selected.totalHours || 32);
        if (state.selected.lessons.length) return;
        const sectionCount = 2;
        const lessonCount = Math.max(1, Math.ceil(totalHours / sectionCount));
        state.selected.lessons = Array.from({ length: lessonCount }, (_, index) => ({
            title: `第${index + 1}次课`,
            content: `${state.selected.courseName || '本课程'}第${index + 1}次课内容，可结合教材和课堂目标继续细化。`,
            section_count: index === lessonCount - 1 ? totalHours - sectionCount * (lessonCount - 1) || sectionCount : sectionCount,
            learning_material_id: null,
        }));
    }

    function updateLesson(index, field, value) {
        const lesson = state.selected.lessons[index];
        if (!lesson) return;
        if (field === 'section_count') {
            lesson[field] = Number(value || 0);
        } else if (field === 'learning_material_id') {
            lesson[field] = Number(value || 0) || null;
        } else {
            lesson[field] = value;
        }
        updateFooter();
    }

    function matchTokens(value) {
        const text = normalizeText(value).toLowerCase();
        if (!text) return [];
        const tokens = text.match(/[a-z0-9]+|[\u4e00-\u9fa5]{2,}/g) || [];
        return [...new Set(tokens.filter((token) => token.length >= 2))];
    }

    function scoreMaterialForLesson(material, lesson, index) {
        const materialText = [
            material.name,
            material.material_path,
            material.preview_type,
            material.file_ext,
        ].map(normalizeText).join(' ').toLowerCase();
        const lessonText = [
            state.selected.courseName,
            lesson.title,
            lesson.content,
        ].map(normalizeText).join(' ');
        let score = 0;
        if (!materialText) return score;
        if (material.is_markdown) score += 1;
        if (Number(state.selected.courseId || 0) && Array.isArray(material.related_course_ids)) {
            score += material.related_course_ids.includes(Number(state.selected.courseId)) ? 6 : 0;
        }
        matchTokens(lessonText).forEach((token) => {
            if (materialText.includes(token)) {
                score += token.length >= 4 ? 4 : 2;
            }
        });
        const lessonNumber = index + 1;
        [`第${lessonNumber}`, `${lessonNumber}次`, `lesson${lessonNumber}`, `lesson ${lessonNumber}`].forEach((marker) => {
            if (materialText.includes(marker.toLowerCase())) score += 5;
        });
        return score;
    }

    function autoBindMaterials(markdownMaterials) {
        const candidates = markdownMaterials.length ? markdownMaterials : list('materials').filter((item) => item.is_markdown);
        const unusedIds = new Set(candidates.map((item) => Number(item.id)));
        state.selected.lessons.forEach((lesson, index) => {
            let bestMaterial = null;
            let bestScore = 0;
            candidates.forEach((material) => {
                const materialId = Number(material.id);
                if (!unusedIds.has(materialId)) return;
                const score = scoreMaterialForLesson(material, lesson, index);
                if (score > bestScore) {
                    bestScore = score;
                    bestMaterial = material;
                }
            });
            if (!bestMaterial && candidates[index] && unusedIds.has(Number(candidates[index].id))) {
                bestMaterial = candidates[index];
            }
            if (!bestMaterial) return;
            lesson.learning_material_id = Number(bestMaterial.id);
            unusedIds.delete(Number(bestMaterial.id));
        });
    }

    function renderDetailsStep(container) {
        ensureLessons();
        const selectedMaterialIds = selectedOrDescendantMaterialIds();
        const markdownMaterials = list('materials').filter((item) => item.is_markdown && (selectedMaterialIds.length === 0 || selectedMaterialIds.includes(Number(item.id))));
        const materialOptions = ['<option value="">不绑定</option>']
            .concat(markdownMaterials.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`))
            .join('');
        const lessonsHtml = state.selected.lessons.map((lesson, index) => `
            <article class="onboarding-lesson-row" data-lesson-index="${index}">
                <div class="onboarding-field">
                    <label>第 ${index + 1} 次课标题</label>
                    <input type="text" value="${escapeHtml(lesson.title || '')}" data-field="title">
                </div>
                <div class="onboarding-field">
                    <label>学时</label>
                    <input type="number" min="1" max="12" step="1" value="${escapeHtml(lesson.section_count || 2)}" data-field="section_count">
                </div>
                <div class="onboarding-field">
                    <label>绑定材料</label>
                    <select data-field="learning_material_id">${materialOptions}</select>
                </div>
                <button type="button" class="btn btn-ghost btn-sm text-danger" data-action="remove-lesson">删除</button>
                <div class="onboarding-field full-span">
                    <label>上课内容</label>
                    <textarea rows="2" data-field="content">${escapeHtml(lesson.content || '')}</textarea>
                </div>
            </article>
        `).join('');
        const aiSuggestion = state.selected.courseDescriptionDraft ? `
            <div class="onboarding-ai-suggestion">
                <strong>快速 AI 推荐简介</strong>
                <p>${escapeHtml(state.selected.courseDescriptionDraft)}</p>
                <button type="button" class="btn btn-outline btn-sm" data-action="apply-description">一键填入</button>
            </div>
        ` : '';

        container.innerHTML = renderStepShell(`
            ${renderTitle(steps[5].prompt, '能自动带入的内容已经先带入，仍然可以手动修改。')}
            <div class="onboarding-field-grid">
                <div class="onboarding-field">
                    <label for="onboardingCreditsInput">学分</label>
                    <input id="onboardingCreditsInput" type="number" min="0.5" max="20" step="0.5" value="${escapeHtml(state.selected.credits)}">
                </div>
                <div class="onboarding-field">
                    <label for="onboardingTotalHoursInput">学时</label>
                    <input id="onboardingTotalHoursInput" type="number" min="1" max="512" step="1" value="${escapeHtml(state.selected.totalHours)}">
                </div>
                <div class="onboarding-field">
                    <label for="onboardingFirstDateInput">第一次上课日期</label>
                    <input id="onboardingFirstDateInput" type="date" value="${escapeHtml(state.selected.firstClassDate || todayIso())}">
                </div>
                <div class="onboarding-field">
                    <label for="onboardingWeeklySectionInput">每周本日小节数</label>
                    <input id="onboardingWeeklySectionInput" type="number" min="1" max="12" step="1" value="${escapeHtml(state.selected.weeklySchedule[0]?.section_count || 2)}">
                </div>
                <div class="onboarding-field full-span">
                    <label for="onboardingDescriptionInput">课程简介</label>
                    <textarea id="onboardingDescriptionInput" rows="4" placeholder="课程定位、学习目标、实践方式和适用专业">${escapeHtml(state.selected.description)}</textarea>
                </div>
            </div>
            ${aiSuggestion}
            <div class="onboarding-toolbar">
                <div class="onboarding-badge-row">
                    <span class="onboarding-badge is-green">8 学时 = 0.5 学分</span>
                    <span class="onboarding-badge is-blue">32 学时 = 2.0 学分</span>
                </div>
                <div class="onboarding-badge-row">
                    <button type="button" class="btn btn-outline btn-sm" data-action="generate-description">快速 AI 生成简介</button>
                    <button type="button" class="btn btn-outline btn-sm" data-action="generate-lessons">AI 生成课堂设置</button>
                    <button type="button" class="btn btn-ghost btn-sm" data-action="bind-materials">智能绑定材料</button>
                    <button type="button" class="btn btn-ghost btn-sm" data-action="add-lesson">新增一次课</button>
                </div>
            </div>
            <div class="onboarding-lesson-list">${lessonsHtml}</div>
        `);

        container.querySelectorAll('[data-lesson-index]').forEach((row) => {
            const index = Number(row.dataset.lessonIndex);
            row.querySelectorAll('[data-field]').forEach((field) => {
                if (field.dataset.field === 'learning_material_id') {
                    field.value = String(state.selected.lessons[index]?.learning_material_id || '');
                }
                field.addEventListener('input', () => updateLesson(index, field.dataset.field, field.value));
                field.addEventListener('change', () => updateLesson(index, field.dataset.field, field.value));
            });
        });
        container.querySelector('#onboardingCreditsInput')?.addEventListener('input', (event) => {
            state.selected.creditTouched = true;
            state.selected.credits = numberValue(event.target.value, 0);
            updateFooter();
        });
        container.querySelector('#onboardingTotalHoursInput')?.addEventListener('input', (event) => {
            state.selected.totalHours = numberValue(event.target.value, 0);
            if (!state.selected.creditTouched) {
                state.selected.credits = computeCredits(state.selected.totalHours);
                const creditInput = container.querySelector('#onboardingCreditsInput');
                if (creditInput) creditInput.value = String(state.selected.credits);
            }
            updateFooter();
        });
        container.querySelector('#onboardingFirstDateInput')?.addEventListener('input', (event) => {
            state.selected.firstClassDate = event.target.value;
            state.selected.weeklySchedule[0] = {
                ...state.selected.weeklySchedule[0],
                weekday: jsDateToCourseWeekday(event.target.value),
            };
            updateFooter();
        });
        container.querySelector('#onboardingWeeklySectionInput')?.addEventListener('input', (event) => {
            state.selected.weeklySchedule[0] = {
                weekday: jsDateToCourseWeekday(state.selected.firstClassDate),
                section_count: numberValue(event.target.value, 2),
            };
            updateFooter();
        });
        container.querySelector('#onboardingDescriptionInput')?.addEventListener('input', (event) => {
            state.selected.description = event.target.value;
            state.selected.aiSystemPrompt = '';
            state.selected.aiSyllabus = '';
            updateFooter();
        });
        container.querySelector('[data-action="apply-description"]')?.addEventListener('click', () => {
            state.selected.description = state.selected.courseDescriptionDraft;
            render();
        });
        container.querySelector('[data-action="generate-description"]')?.addEventListener('click', generateDescription);
        container.querySelector('[data-action="generate-lessons"]')?.addEventListener('click', generateLessons);
        container.querySelector('[data-action="bind-materials"]')?.addEventListener('click', () => {
            autoBindMaterials(markdownMaterials);
            render();
        });
        container.querySelector('[data-action="add-lesson"]')?.addEventListener('click', () => {
            state.selected.lessons.push({
                title: `第${state.selected.lessons.length + 1}次课`,
                content: '',
                section_count: 2,
                learning_material_id: null,
            });
            render();
        });
        container.querySelectorAll('[data-action="remove-lesson"]').forEach((button) => {
            button.addEventListener('click', () => {
                const row = button.closest('[data-lesson-index]');
                const index = Number(row?.dataset.lessonIndex || -1);
                if (index >= 0) {
                    state.selected.lessons.splice(index, 1);
                    render();
                }
            });
        });
    }

    function buildAiDraft() {
        const teacherName = normalizeText(wizard().teacher?.name) || '老师';
        const courseName = normalizeText(state.selected.courseName) || '本课程';
        const className = selectedClass()?.name || '当前班级';
        const semesterName = selectedSemester()?.name || '当前学期';
        const textbookTitle = selectedTextbook()?.title || '已选教材';
        const materialNames = [...state.selected.materialIds]
            .map((id) => list('materials').find((item) => Number(item.id) === Number(id))?.name)
            .filter(Boolean)
            .join('、') || '未选择教学材料';
        const summary = `课程：${courseName}\n班级：${className}\n学期：${semesterName}\n系别：${state.selected.department || '未设置'}\n教材：${textbookTitle}\n教学材料：${materialNames}`;
        return {
            system: `你是《${courseName}》课堂的 AI 助教，协助${teacherName}老师服务本课堂。请优先依据课程简介、教材、课堂材料和教师发布的任务回答问题。面对学生时强调思路引导，不直接代写作业或泄露考试答案；面对教师时可以协助备课、活动设计和表达优化。始终使用简体中文，回答准确、具体、边界清晰。\n\n${summary}`,
            syllabus: `${summary}\n\n课程简介：\n${state.selected.description || '待补充'}\n\n课堂设置：\n${state.selected.lessons.map((lesson, index) => `${index + 1}. ${lesson.title || '未命名'}：${lesson.content || ''}`).join('\n')}`,
        };
    }

    function renderAiStep(container) {
        if (!state.selected.aiSystemPrompt || !state.selected.aiSyllabus) {
            const draft = buildAiDraft();
            state.selected.aiSystemPrompt = state.selected.aiSystemPrompt || draft.system;
            state.selected.aiSyllabus = state.selected.aiSyllabus || draft.syllabus;
        }
        container.innerHTML = renderStepShell(`
            ${renderTitle(steps[6].prompt, '系统已根据学期、课程、教材、材料和班级生成草稿，保存前可以继续微调。')}
            <div class="onboarding-field-grid">
                <div class="onboarding-field full-span">
                    <label for="onboardingAiPromptInput">系统提示词</label>
                    <textarea id="onboardingAiPromptInput" rows="8">${escapeHtml(state.selected.aiSystemPrompt)}</textarea>
                </div>
                <div class="onboarding-field full-span">
                    <label for="onboardingAiSyllabusInput">课堂知识依据</label>
                    <textarea id="onboardingAiSyllabusInput" rows="7">${escapeHtml(state.selected.aiSyllabus)}</textarea>
                </div>
            </div>
        `);
        container.querySelector('#onboardingAiPromptInput')?.addEventListener('input', (event) => {
            state.selected.aiSystemPrompt = event.target.value;
        });
        container.querySelector('#onboardingAiSyllabusInput')?.addEventListener('input', (event) => {
            state.selected.aiSyllabus = event.target.value;
        });
    }

    function renderSuccessStep(container) {
        const teacherName = normalizeText(wizard().teacher?.name) || '';
        container.innerHTML = renderStepShell(`
            ${renderTitle('完成课堂开设', `恭喜开课成功，预祝${teacherName ? `${teacherName}老师` : '老师'}课程顺利。`)}
            <div class="onboarding-recommend-panel">
                <strong>${escapeHtml(state.selected.courseName)} / ${escapeHtml(selectedClass()?.name || '')}</strong>
                <p>课堂时间轴、课程模板、教材、材料和 AI 助教配置已经保存。</p>
                <div class="onboarding-badge-row">
                    <span class="onboarding-badge is-green">${escapeHtml(selectedSemester()?.name || '已绑定学期')}</span>
                    <span class="onboarding-badge is-blue">${escapeHtml(selectedTextbook()?.title || '已绑定教材')}</span>
                    <span class="onboarding-badge is-amber">${state.selected.materialIds.size} 个材料</span>
                </div>
            </div>
        `);
    }

    function render() {
        if (!elements.content) return;
        const step = steps[state.activeIndex] || steps[0];
        if (elements.stepCount) {
            elements.stepCount.textContent = `第 ${state.activeIndex + 1} 步 / 共 ${steps.length} 步`;
        }
        renderHistory();
        if (step.key === 'semester') renderSemesterStep(elements.content);
        if (step.key === 'course') renderCourseStep(elements.content);
        if (step.key === 'textbook') renderTextbookStep(elements.content);
        if (step.key === 'materials') renderMaterialsStep(elements.content);
        if (step.key === 'class') renderClassStep(elements.content);
        if (step.key === 'details') renderDetailsStep(elements.content);
        if (step.key === 'ai') renderAiStep(elements.content);
        if (step.key === 'success') renderSuccessStep(elements.content);
        updateFooter();
    }

    function goToStep(index) {
        const target = Math.min(Math.max(index, 0), steps.length - 1);
        if (target === state.activeIndex || state.completing) return;
        const shell = elements.content?.querySelector('.onboarding-step-shell');
        if (!shell) {
            state.activeIndex = target;
            render();
            return;
        }
        shell.classList.add('is-leaving');
        window.setTimeout(() => {
            state.activeIndex = target;
            render();
        }, 230);
    }

    async function handleNext() {
        const key = steps[state.activeIndex]?.key;
        if (key === 'success') {
            if (state.selected.classroomUrl) {
                window.location.href = state.selected.classroomUrl;
            }
            return;
        }
        if (key === 'ai') {
            await completeOnboarding();
            return;
        }
        if (canGoNext()) {
            goToStep(state.activeIndex + 1);
        }
    }

    function closeSubmodal() {
        if (!submodal) return;
        submodal.hidden = true;
        if (elements.submodalBody) elements.submodalBody.innerHTML = '';
    }

    function openSubmodal(title, bodyHtml, onSubmit) {
        if (!submodal || !elements.submodalBody || !elements.submodalTitle) return null;
        elements.submodalTitle.textContent = title;
        elements.submodalBody.innerHTML = `<form data-submodal-form>${bodyHtml}</form>`;
        submodal.hidden = false;
        const form = elements.submodalBody.querySelector('[data-submodal-form]');
        form?.addEventListener('submit', async (event) => {
            event.preventDefault();
            const submit = form.querySelector('[type="submit"]');
            const originalText = submit?.textContent || '';
            if (submit) {
                submit.disabled = true;
                submit.textContent = '保存中...';
            }
            try {
                await onSubmit(new FormData(form), form);
                closeSubmodal();
                await reloadStateAndRender();
            } catch (error) {
                showToast(error.message || '保存失败', 'error');
            } finally {
                if (submit) {
                    submit.disabled = false;
                    submit.textContent = originalText;
                }
            }
        });
        form?.querySelector('input, textarea, select')?.focus({ preventScroll: true });
        return form;
    }

    function openSemesterSubmodal() {
        openSubmodal('新建学期', `
            <div class="onboarding-field">
                <label>学期名称</label>
                <input name="name" type="text" placeholder="例如：2026 春季学期">
            </div>
            <div class="onboarding-field">
                <label>开始日期</label>
                <input name="start_date" type="date" required value="${todayIso()}">
            </div>
            <div class="onboarding-field">
                <label>结束日期</label>
                <input name="end_date" type="date" required>
            </div>
            <div class="teacher-onboarding-submodal-actions">
                <button type="button" class="btn btn-outline" data-submodal-close-local>取消</button>
                <button type="submit" class="btn btn-primary">保存学期</button>
            </div>
        `, async (formData) => {
            const name = normalizeText(formData.get('name'));
            const startDate = normalizeText(formData.get('start_date'));
            await apiFetch('/api/manage/semesters/save', { method: 'POST', body: formData, silent: true });
            await loadState({ silent: true });
            const created = list('semesters').find((item) => (
                (name && item.name === name) || (startDate && item.start_date === startDate)
            )) || list('semesters')[0];
            if (created) state.selected.semesterId = Number(created.id);
        });
    }

    function openTextbookSubmodal() {
        openSubmodal('录入教材', `
            <div class="onboarding-field">
                <label>教材名称</label>
                <input name="title" type="text" required placeholder="例如：Web 程序设计基础">
            </div>
            <div class="onboarding-field">
                <label>作者</label>
                <input name="authors_text" type="text" placeholder="多位作者用逗号分隔">
            </div>
            <div class="onboarding-field">
                <label>出版社</label>
                <input name="publisher" type="text">
            </div>
            <div class="onboarding-field">
                <label>教材简介</label>
                <textarea name="introduction" rows="3"></textarea>
            </div>
            <div class="onboarding-field">
                <label>目录或章节线索</label>
                <textarea name="catalog_text" rows="4"></textarea>
            </div>
            <div class="teacher-onboarding-submodal-actions">
                <button type="button" class="btn btn-outline" data-submodal-close-local>取消</button>
                <button type="submit" class="btn btn-primary">保存教材</button>
            </div>
        `, async (formData) => {
            const authors = normalizeText(formData.get('authors_text'))
                .split(/[，,]/)
                .map((item) => normalizeText(item))
                .filter(Boolean);
            formData.delete('authors_text');
            formData.set('authors_json', JSON.stringify(authors));
            formData.set('tags_json', JSON.stringify(state.selected.department ? [state.selected.department] : []));
            const result = await apiFetch('/api/manage/textbooks/save', { method: 'POST', body: formData, silent: true });
            if (result?.textbook_id) state.selected.textbookId = Number(result.textbook_id);
            state.selected.courseDescriptionDraft = '';
        });
    }

    function openMaterialSubmodal() {
        let uploadFiles = [];
        const form = openSubmodal('导入教学材料', `
            <div class="onboarding-upload-choice-grid">
                <button type="button" class="onboarding-upload-choice" data-action="pick-material-files">
                    <strong>上传单个或多个文件</strong>
                    <span>适合补充零散课件、文档、PPT 或思维导图。</span>
                </button>
                <button type="button" class="onboarding-upload-choice" data-action="pick-material-folder">
                    <strong>上传整个文件夹</strong>
                    <span>会保留原有目录结构，适合直接导入一整套课程资料。</span>
                </button>
            </div>
            <input type="file" data-material-file-input multiple hidden>
            <input type="file" data-material-folder-input webkitdirectory directory multiple hidden>
            <div class="onboarding-upload-summary" data-upload-summary>还没有选择文件。</div>
            <p class="onboarding-hint">后面也可以使用深度思考 AI 协助生成课程材料、整理目录或把资料优化成课堂学习文档。</p>
            <div class="teacher-onboarding-submodal-actions">
                <button type="button" class="btn btn-outline" data-submodal-close-local>取消</button>
                <button type="submit" class="btn btn-primary">导入材料</button>
            </div>
        `, async (_formData, form) => {
            const files = uploadFiles.length ? uploadFiles : Array.from(form.querySelector('[data-material-file-input]')?.files || []);
            if (!files.length) throw new Error('请选择要导入的文件');
            const uploadData = new FormData();
            files.forEach((file) => uploadData.append('files', file, file.name));
            uploadData.set('manifest', JSON.stringify(files.map((file) => ({
                relative_path: file.webkitRelativePath || file.name,
                content_type: file.type || '',
            }))));
            const result = await apiFetch('/api/materials/upload', { method: 'POST', body: uploadData, silent: true });
            (result.created_items || []).forEach((item) => {
                if (item?.id) state.selected.materialIds.add(Number(item.id));
                if (item?.node_type === 'folder') state.materialExpandedIds.delete(Number(item.id));
            });
        });
        if (!form) return;
        const fileInput = form.querySelector('[data-material-file-input]');
        const folderInput = form.querySelector('[data-material-folder-input]');
        const summary = form.querySelector('[data-upload-summary]');
        const updateSummary = (files, sourceLabel) => {
            uploadFiles = Array.from(files || []);
            const folderNames = new Set(uploadFiles.map((file) => (file.webkitRelativePath || '').split('/')[0]).filter(Boolean));
            if (!summary) return;
            if (!uploadFiles.length) {
                summary.textContent = '还没有选择文件。';
                return;
            }
            const folderText = folderNames.size ? `，包含 ${folderNames.size} 个顶层文件夹` : '';
            summary.textContent = `${sourceLabel}：已选择 ${uploadFiles.length} 个文件${folderText}。`;
        };
        form.querySelector('[data-action="pick-material-files"]')?.addEventListener('click', () => fileInput?.click());
        form.querySelector('[data-action="pick-material-folder"]')?.addEventListener('click', () => folderInput?.click());
        fileInput?.addEventListener('change', () => updateSummary(fileInput.files, '文件上传'));
        folderInput?.addEventListener('change', () => updateSummary(folderInput.files, '文件夹上传'));
    }

    function openClassSubmodal() {
        openSubmodal('录入新班级', `
            <div class="onboarding-field">
                <label>班级名称</label>
                <input name="name" type="text" required placeholder="例如：网络工程 2401 班">
            </div>
            <div class="onboarding-field">
                <label>所属系别</label>
                <input name="department" type="text" required value="${escapeHtml(state.selected.department)}" placeholder="例如：网络工程系">
            </div>
            <div class="onboarding-field">
                <label>备注</label>
                <textarea name="description" rows="3" placeholder="可选，稍后仍可导入学生名单"></textarea>
            </div>
            <div class="teacher-onboarding-submodal-actions">
                <button type="button" class="btn btn-outline" data-submodal-close-local>取消</button>
                <button type="submit" class="btn btn-primary">保存班级</button>
            </div>
        `, async (formData) => {
            const result = await apiFetch('/api/manage/teacher-onboarding/classes/create', {
                method: 'POST',
                body: Object.fromEntries(formData.entries()),
                silent: true,
            });
            if (result?.class?.id) state.selected.classId = Number(result.class.id);
        });
    }

    async function generateDescription(event) {
        const button = event?.currentTarget;
        const originalText = button?.textContent || '';
        if (button) {
            button.disabled = true;
            button.textContent = '生成中...';
        }
        try {
            const result = await apiFetch('/api/manage/teacher-onboarding/course-description', {
                method: 'POST',
                body: {
                    course_name: state.selected.courseName,
                    department: state.selected.department,
                    textbook_id: state.selected.textbookId,
                },
                silent: true,
            });
            state.selected.courseDescriptionDraft = result.description || '';
            if (!state.selected.description) {
                state.selected.description = state.selected.courseDescriptionDraft;
            }
            state.selected.aiSystemPrompt = '';
            state.selected.aiSyllabus = '';
            showToast('课程简介已生成', 'success');
            render();
        } catch (error) {
            showToast(error.message || '课程简介生成失败', 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = originalText;
            }
        }
    }

    async function generateLessons(event) {
        const button = event?.currentTarget;
        const originalText = button?.textContent || '';
        if (button) {
            button.disabled = true;
            button.textContent = 'AI 生成中...';
        }
        try {
            const result = await apiFetch('/api/manage/courses/ai-generate-lessons', {
                method: 'POST',
                body: {
                    name: state.selected.courseName,
                    description: state.selected.description,
                    textbook_id: state.selected.textbookId,
                    total_hours: state.selected.totalHours,
                    per_session_sections: Number(state.selected.weeklySchedule[0]?.section_count || 2),
                },
                silent: true,
            });
            state.selected.lessons = Array.isArray(result.lessons) ? result.lessons : [];
            state.selected.aiSystemPrompt = '';
            state.selected.aiSyllabus = '';
            showToast(result.message || '课堂设置已生成', 'success');
            render();
        } catch (error) {
            showToast(error.message || 'AI 生成课堂设置失败', 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = originalText;
            }
        }
    }

    function completePayload() {
        const materialIds = [...state.selected.materialIds];
        const candidateHomeMaterialIds = selectedOrDescendantMaterialIds();
        const homeMaterial = list('materials').find((item) => (
            candidateHomeMaterialIds.includes(Number(item.id)) && item.is_markdown
        ));
        return {
            semester_id: state.selected.semesterId,
            class_id: state.selected.classId,
            textbook_id: state.selected.textbookId,
            material_ids: materialIds,
            home_learning_material_id: homeMaterial?.id || null,
            course: {
                course_id: state.selected.courseId,
                name: state.selected.courseName,
                department: state.selected.department,
                sect_name: state.selected.sectName,
                description: state.selected.description,
                credits: state.selected.credits,
                total_hours: state.selected.totalHours,
                lessons: state.selected.lessons.map((lesson) => ({
                    title: lesson.title,
                    content: lesson.content,
                    section_count: Number(lesson.section_count || 0),
                    learning_material_id: lesson.learning_material_id || null,
                })),
            },
            schedule: {
                first_class_date: state.selected.firstClassDate,
                weekly_schedule: state.selected.weeklySchedule,
            },
            ai: {
                system_prompt: state.selected.aiSystemPrompt,
                syllabus: state.selected.aiSyllabus,
            },
        };
    }

    async function completeOnboarding() {
        if (state.completing) return;
        state.completing = true;
        updateFooter();
        try {
            const result = await apiFetch('/api/manage/teacher-onboarding/complete', {
                method: 'POST',
                body: completePayload(),
                silent: true,
            });
            state.selected.classroomUrl = result.classroom_url || '';
            if (result.course_id && !state.selected.courseId) state.selected.courseId = Number(result.course_id);
            showToast(result.message || '课堂开设成功', 'success');
            await loadState({ silent: true });
            state.completing = false;
            goToStep(steps.length - 1);
        } catch (error) {
            showToast(error.message || '课堂开设失败', 'error');
        } finally {
            state.completing = false;
            updateFooter();
        }
    }

    async function openGuide(source = 'manual') {
        const payload = source === 'manual'
            ? await loadState({ silent: false })
            : (state.payload || await loadState({ silent: true }));
        if (!payload) return;

        state.activeIndex = 0;
        state.lastFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        state.bodyOverflow = document.body.style.overflow || '';
        if (elements.welcome) {
            const teacherName = normalizeText(wizard().teacher?.name) || '';
            elements.welcome.textContent = `欢迎${teacherName}老师，接下来我们一起一步步完成课堂的开设`;
        }
        render();

        window.clearTimeout(state.closeTimer);
        window.clearTimeout(state.welcomeTimer);
        modal.hidden = false;
        dialog?.classList.remove('is-welcome-compact');
        dialog?.classList.add('is-welcome-pending');
        document.body.style.overflow = 'hidden';
        window.requestAnimationFrame(() => {
            modal.classList.add('is-open');
            state.isOpen = true;
            modal.querySelector('[data-teacher-onboarding-dismiss]')?.focus({ preventScroll: true });
        });
        state.welcomeTimer = window.setTimeout(() => {
            dialog?.classList.remove('is-welcome-pending');
            dialog?.classList.add('is-welcome-compact');
        }, 3000);
    }

    async function closeGuide(reason = 'manual_exit') {
        if (!state.isOpen) return;
        const persisted = await markDismissed(reason);
        if (!persisted) return;

        modal.classList.remove('is-open');
        state.isOpen = false;
        document.body.style.overflow = state.bodyOverflow;
        closeSubmodal();
        window.clearTimeout(state.welcomeTimer);
        state.closeTimer = window.setTimeout(() => {
            if (!state.isOpen) modal.hidden = true;
        }, 220);
        if (state.lastFocused && document.contains(state.lastFocused)) {
            state.lastFocused.focus({ preventScroll: true });
        }
    }

    openButtons.forEach((button) => {
        button.addEventListener('click', () => openGuide('manual'));
    });

    elements.closeButtons.forEach((button) => {
        button.addEventListener('click', () => closeGuide('manual_exit'));
    });

    elements.submodalClose?.addEventListener('click', closeSubmodal);
    elements.submodalBody?.addEventListener('click', (event) => {
        if (event.target.closest('[data-submodal-close-local]')) {
            closeSubmodal();
        }
    });

    elements.prevButton?.addEventListener('click', () => goToStep(state.activeIndex - 1));
    elements.nextButton?.addEventListener('click', handleNext);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && state.isOpen) {
            event.preventDefault();
            if (submodal && !submodal.hidden) {
                closeSubmodal();
            } else {
                closeGuide('manual_exit');
            }
        }
    });

    window.setTimeout(async () => {
        const payload = await loadState({ silent: true });
        if (payload?.should_auto_open) {
            openGuide('auto');
        }
    }, 350);
}
