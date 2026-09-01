// 课堂运行总台（offering hub）：纯前端筛选/排序 + 明细展开 + 删除。
// 数据全部由服务端渲染在卡片 data-* 属性上，模式与 manage_classes.js 一致。
import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const listEl = document.getElementById('offeringHubList');
const searchInput = document.getElementById('offeringHubSearchInput');
const semesterFilter = document.getElementById('offeringHubSemesterFilter');
const statusFilter = document.getElementById('offeringHubStatusFilter');
const sortSelect = document.getElementById('offeringHubSortSelect');
const resultCount = document.getElementById('offeringHubResultCount');
const filterEmpty = document.getElementById('offeringHubFilterEmpty');
const initialEmpty = document.getElementById('offeringHubInitialEmpty');
const resetBtn = document.getElementById('offeringHubFilterResetBtn');

const getCards = () => Array.from(listEl?.querySelectorAll('[data-offering-card]') || []);

function matchesStatus(card, status) {
    if (status === 'all') return true;
    if (status === 'combined') return card.dataset.combined === '1';
    if (status === 'missing-textbook') return (card.dataset.missing || '').split(' ').includes('textbook');
    if (status === 'missing-ai') return (card.dataset.missing || '').split(' ').includes('ai');
    return card.dataset.status === status;
}

function compareCards(a, b, mode) {
    if (mode === 'course') {
        const byCourse = (a.dataset.courseName || '').localeCompare(b.dataset.courseName || '', 'zh');
        if (byCourse !== 0) return byCourse;
        return (a.dataset.className || '').localeCompare(b.dataset.className || '', 'zh');
    }
    if (mode === 'progress-desc') {
        return Number(b.dataset.progress || 0) - Number(a.dataset.progress || 0);
    }
    if (mode === 'students-desc') {
        return Number(b.dataset.studentCount || 0) - Number(a.dataset.studentCount || 0);
    }
    // next-session：有下次课的在前、按日期升序；无排期的最后。
    const dateA = a.dataset.nextDate || '';
    const dateB = b.dataset.nextDate || '';
    if (dateA && dateB) return dateA.localeCompare(dateB);
    if (dateA) return -1;
    if (dateB) return 1;
    return (a.dataset.courseName || '').localeCompare(b.dataset.courseName || '', 'zh');
}

function applyFilters() {
    const keyword = (searchInput?.value || '').trim().toLowerCase();
    const semester = semesterFilter?.value || 'all';
    const status = statusFilter?.value || 'all';
    const sortMode = sortSelect?.value || 'next-session';

    const cards = getCards();
    let visibleCount = 0;
    cards.forEach((card) => {
        const matchesSemester = semester === 'all' || card.dataset.semesterKey === semester;
        const matchesKeyword = !keyword || (card.dataset.search || '').includes(keyword);
        const visible = matchesSemester && matchesKeyword && matchesStatus(card, status);
        card.hidden = !visible;
        if (visible) visibleCount += 1;
    });

    cards
        .slice()
        .sort((a, b) => compareCards(a, b, sortMode))
        .forEach((card) => listEl?.appendChild(card));

    if (resultCount) resultCount.textContent = String(visibleCount);
    if (filterEmpty) filterEmpty.hidden = visibleCount > 0 || !cards.length;
    if (initialEmpty) initialEmpty.hidden = cards.length > 0;
}

function resetFilters() {
    if (searchInput) searchInput.value = '';
    if (semesterFilter) semesterFilter.value = 'all';
    if (statusFilter) {
        statusFilter.value = 'all';
        statusFilter.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (sortSelect) sortSelect.value = 'next-session';
    applyFilters();
}

async function handleDelete(button) {
    const offeringId = Number(button.dataset.offeringId || 0);
    const offeringName = button.dataset.offeringName || '该课堂';
    if (!offeringId) return;
    const confirmed = window.confirm(
        `确定删除「${offeringName}」吗？\n课堂下的作业、互动与成绩记录将一并删除，此操作不可恢复。`
    );
    if (!confirmed) return;
    try {
        await apiFetch(`/api/manage/class_offerings/${offeringId}`, { method: 'DELETE' });
        showMessage('课堂已删除', 'success');
        window.location.reload();
    } catch (error) {
        // apiFetch 已弹出错误 toast，这里不再重复提示。
    }
}

/**
 * 一键绑定学习文档包：先按课程查包，命中唯一包直接绑；无包引导去课程页生成。
 * 绑定本身复用 POST /api/lessondoc/packs/{id}/bind（确定性：首页 + lesson_N ↔ 第 N 次课）。
 */
async function handleBindLessonDoc(button) {
    const offeringId = Number(button.dataset.offeringId || 0);
    const courseId = Number(button.dataset.courseId || 0);
    if (!offeringId || !courseId) return;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = '查询中…';
    try {
        const listed = await apiFetch(`/api/lessondoc/packs?course_id=${courseId}`);
        const packs = listed?.packs || [];
        if (!packs.length) {
            showMessage('本课程还没有学习文档包，先去「内容资产 → 课程」生成', 'warning');
            window.open(`/manage/teaching/courses?lessondoc=${courseId}`, '_blank', 'noopener');
            return;
        }
        const pack = packs[0];
        if (!pack.ready_count) {
            showMessage('该学习文档包还没有已生成的课次，请先生成再绑定', 'warning');
            return;
        }
        const confirmed = window.confirm(
            `把学习文档包（${pack.ready_count}/${pack.total_count} 课就绪）绑定到这个课堂？\n`
            + '课程首页会挂到课堂主页，每个已生成课次自动对应同序号的课次。'
        );
        if (!confirmed) return;
        button.textContent = '绑定中…';
        const result = await apiFetch(`/api/lessondoc/packs/${pack.id}/bind`, {
            method: 'POST',
            body: { class_offering_ids: [offeringId] },
        });
        const binding = result?.binding || {};
        showMessage(
            `绑定完成：首页 ${binding.total_home_assignments ?? 0} 处，课次 ${binding.total_assignments ?? 0} 处`,
            'success'
        );
        window.location.reload();
    } catch (error) {
        // apiFetch 已弹出错误 toast
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
}

// -- 页内编辑抽屉：iframe 复用「开设课堂」编辑器的 embed 模式，避免复制表单逻辑 --
const drawerBackdrop = document.getElementById('offeringHubEditDrawer');
const drawerFrame = document.getElementById('offeringHubDrawerFrame');
const drawerTitle = document.getElementById('offeringHubDrawerTitle');
const drawerOpenFull = document.getElementById('offeringHubDrawerOpenFull');
const drawerClose = document.getElementById('offeringHubDrawerClose');
let drawerFrameLoads = 0;

function openEditDrawer(href, title) {
    if (!drawerBackdrop || !drawerFrame || !href) return false;
    drawerFrameLoads = 0;
    drawerFrame.src = `${href}${href.includes('?') ? '&' : '?'}embed=1`;
    if (drawerTitle) drawerTitle.textContent = title || '编辑课堂配置';
    if (drawerOpenFull) drawerOpenFull.href = href;
    drawerBackdrop.hidden = false;
    drawerBackdrop.setAttribute('aria-hidden', 'false');
    document.body.classList.add('offering-hub-drawer-open');
    return true;
}

function closeEditDrawer() {
    if (!drawerBackdrop) return;
    drawerBackdrop.hidden = true;
    drawerBackdrop.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('offering-hub-drawer-open');
    const dirty = drawerFrameLoads > 1;
    if (drawerFrame) drawerFrame.src = 'about:blank';
    // iframe 内保存成功会自刷新（load 次数 > 1），此时刷新总台同步最新数据。
    if (dirty) window.location.reload();
}

function bindDrawerEvents() {
    drawerFrame?.addEventListener('load', () => {
        if ((drawerFrame.src || '').startsWith('about:')) return;
        drawerFrameLoads += 1;
    });
    drawerClose?.addEventListener('click', closeEditDrawer);
    drawerBackdrop?.addEventListener('click', (event) => {
        if (event.target === drawerBackdrop) closeEditDrawer();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && drawerBackdrop && !drawerBackdrop.hidden) closeEditDrawer();
    });
}

function bindEvents() {
    searchInput?.addEventListener('input', applyFilters);
    semesterFilter?.addEventListener('change', applyFilters);
    statusFilter?.addEventListener('change', applyFilters);
    sortSelect?.addEventListener('change', applyFilters);
    resetBtn?.addEventListener('click', resetFilters);

    document.querySelectorAll('[data-todo-status]').forEach((button) => {
        button.addEventListener('click', () => {
            if (!statusFilter) return;
            statusFilter.value = button.dataset.todoStatus || 'all';
            statusFilter.dispatchEvent(new Event('change', { bubbles: true }));
            listEl?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });

    listEl?.addEventListener('click', (event) => {
        const editLink = event.target.closest('[data-action="edit-config"]');
        if (editLink) {
            if (openEditDrawer(editLink.getAttribute('href'), editLink.dataset.offeringTitle)) {
                event.preventDefault();
            }
            return;
        }
        const toggleButton = event.target.closest('[data-action="toggle-detail"]');
        if (toggleButton) {
            const card = toggleButton.closest('[data-offering-card]');
            const detail = card?.querySelector('[data-offering-detail]');
            if (detail) {
                detail.hidden = !detail.hidden;
                toggleButton.textContent = detail.hidden ? '明细' : '收起';
            }
            return;
        }
        const bindDocButton = event.target.closest('[data-action="bind-lessondoc"]');
        if (bindDocButton) {
            handleBindLessonDoc(bindDocButton);
            return;
        }
        const deleteButton = event.target.closest('[data-action="delete-offering"]');
        if (deleteButton) handleDelete(deleteButton);
    });
}

bindEvents();
bindDrawerEvents();
applyFilters();
