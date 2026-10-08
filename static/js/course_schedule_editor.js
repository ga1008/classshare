/**
 * 课表编辑模式（教师端）。
 *
 * 布局：顶栏选学年学期 → 左侧全学期周列表 → 中间选中周的完整周表格 → 右侧课次属性抽屉。
 * 交互：
 *   - 按住课次卡片拖到同周其他节次（第 1 节早读不可放置；占用节数与原课次一致）；
 *   - 拖到左侧周列表的某一周，该周会"摊开"到中间，继续放到目标节次即完成跨周调整；
 *   - 单击课次打开右侧属性：手动设置周/星期/起始节、教室（教务场地）、调课原因、课次材料。
 * 每一次放置 / 保存都会立即写入平台本地草稿；「保存到教务」把草稿写进教务
 * 调停课申请的"待提交"列表，不提交申请——提交由教师登录教务系统核对后完成。
 */

import { DECK_CSS } from './course_schedule_styles.js';
import { EDITOR_CSS } from './course_schedule_editor_styles.js';
import { compactClassroomName, scheduleChanges, scheduleChangeLabel } from './course_schedule_presentation.js?v=schedule-glass-20260920';
import { projectScheduleChanges } from './course_schedule_change_links.js';
import { scheduleLessonLanes } from './course_schedule_deck.js';
import { syncAcademicSchedule } from '/static/js/academic_schedule_sync.js?v=academic-sync-20260919';
import { getLQ } from './lq/index.js';
import { bindDropdown } from './lq/dropdown.js';
import { bindOverflowLabels } from './lq/overflow-label.js';
import { createChangeComparison, createDetailButton, groupChanges, changeStatus } from './course_schedule_changes.js';

const LQ = getLQ();
const API = '/api/manage/academic/course-schedule/editor';
const DECK_STYLE_ID = 'course-schedule-deck-style';
const EDITOR_STYLE_ID = 'course-schedule-editor-style';
const DAY_NAMES = ['一', '二', '三', '四', '五', '六', '日'];
const BAND_LABELS = { dawn: '早读', am: '上午', pm: '下午', eve: '晚上' };
const DRAG_THRESHOLD = 6;
const WEEK_HOVER_DELAY = 320;
const CREDENTIAL_URL = '/manage/academic/integrations';

const bootElement = document.getElementById('course-schedule-editor-boot');
const root = document.querySelector('[data-cse-root]');
if (bootElement && root) init(JSON.parse(bootElement.textContent || '{}'));

/** @param {number} section */
function sectionBand(section) {
    if (section <= 1) return 'dawn';
    if (section <= 5) return 'am';
    if (section <= 9) return 'pm';
    return 'eve';
}

function escapeHtml(value) {
    return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function ensureStyles() {
    if (!document.getElementById(DECK_STYLE_ID)) {
        const deck = document.createElement('style'); deck.id = DECK_STYLE_ID; deck.textContent = DECK_CSS; document.head.appendChild(deck);
    }
    if (!document.getElementById(EDITOR_STYLE_ID)) {
        const style = document.createElement('style'); style.id = EDITOR_STYLE_ID; style.textContent = EDITOR_CSS; document.head.appendChild(style);
    }
}

function sectionText(sections = []) {
    if (!sections.length) return '';
    return sections.length === 1 ? `第${sections[0]}节` : `第${sections[0]}-${sections[sections.length - 1]}节`;
}

function toast(message, tone = 'info') {
    LQ.toast(message, { tone, duration: tone === 'danger' ? 6000 : 3600 }).catch(() => {});
}

async function api(url, options = {}) {
    const response = await fetch(url, {
        credentials: 'same-origin', cache: 'no-store',
        headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}) },
        ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(data.detail || data.message || `请求失败（HTTP ${response.status}）`);
        error.status = response.status; error.data = data;
        throw error;
    }
    return data;
}

function init(boot) {
    ensureStyles();
    document.body.classList.add('cse-page');
    const state = {
        payload: null, activeWeek: 0, selectedKey: '', drag: null, form: null, roomsTimer: null, roomsRequest: null,
        busy: false, lastPush: null, materials: { key: '', items: null, loading: false },
        availability: { key: '', roomKey: '', data: null, loading: false },
        freeRooms: { slotKey: '', items: [], status: '', roomStatus: '', loading: false, message: '', page: 0, hasMore: false },
        proofsBusy: false,
    };
    let availabilityRequestId = 0;
    let freeRoomsRequestId = 0;
    const refs = {
        termSelect: root.querySelector('[data-cse-term]'),
        meta: root.querySelector('[data-cse-meta]'),
        pushBtn: root.querySelector('[data-cse-push]'),
        syncBtn: root.querySelector('[data-cse-sync]'),
        backLink: root.querySelector('[data-cse-back]'),
        layout: root.querySelector('[data-cse-layout]'),
        weeks: root.querySelector('[data-cse-weeks]'),
        stageTitle: root.querySelector('[data-cse-stage-title]'),
        stageSub: root.querySelector('[data-cse-stage-sub]'),
        stageBody: root.querySelector('[data-cse-stage-body]'),
        drawer: root.querySelector('[data-cse-drawer]'),
        drafts: root.querySelector('[data-cse-drafts]'),
        feedback: root.querySelector('[data-cse-feedback]'),
        availSync: root.querySelector('[data-cse-avail-sync]'),
        availMeta: root.querySelector('[data-cse-avail-meta]'),
        legend: root.querySelector('[data-cse-legend]'),
        calendarNote: root.querySelector('[data-cse-calnote]'),
        swaps: root.querySelector('[data-cse-swaps]'),
        holidayRefresh: root.querySelector('[data-cse-holiday-refresh]'),
    };

    /* ------------------------------------------------------------------ data helpers */
    const overview = () => state.payload?.overview || {};
    const displayViews = new WeakMap();
    const weeks = () => {
        const canonical = overview();
        if (!displayViews.has(canonical)) displayViews.set(canonical, projectScheduleChanges(canonical));
        return displayViews.get(canonical).weeks || [];
    };
    const rules = () => state.payload?.rules || { min_section: 2, max_section: 11, max_week: weeks().length };
    const drafts = () => state.payload?.drafts || [];
    const term = () => overview().selected_term || {};
    const activeWeekData = () => weeks().find(week => Number(week.week_index) === state.activeWeek) || null;
    const pendingDrafts = () => drafts().filter(d => ['draft', 'conflict', 'failed'].includes(d.status));
    const draftById = id => drafts().find(d => d.id === Number(id)) || null;

    /* ------------------------------------------------------------------ calendar: today / holidays / 调休 */
    const today = () => String(state.payload?.today || new Date().toISOString().slice(0, 10));
    const calendar = () => state.payload?.calendar || { days: [], swaps: [] };
    const calendarDay = iso => (calendar().days || []).find(day => day.date === iso) || null;
    /** Legal start sections (两小节为单位): 2, 4, 6, 8, 10 … */
    const pairStarts = () => {
        const list = rules().pair_starts;
        if (Array.isArray(list) && list.length) return list.map(Number);
        const { min_section: minSection, max_section: maxSection } = rules();
        const out = [];
        for (let s = minSection; s < maxSection; s += 2) out.push(s);
        return out;
    };

    /** ISO date of (week, weekday) from the term's week-1 Monday; '' when unknown. */
    function dateOf(week, weekday) {
        const monday = String(term().week1_monday || '');
        if (!monday || !week || !weekday) return '';
        const base = new Date(`${monday}T00:00:00`);
        if (Number.isNaN(base.getTime())) return '';
        base.setDate(base.getDate() + (Number(week) - 1) * 7 + (Number(weekday) - 1));
        const pad = n => String(n).padStart(2, '0');
        return `${base.getFullYear()}-${pad(base.getMonth() + 1)}-${pad(base.getDate())}`;
    }
    const isPastDate = iso => Boolean(iso) && iso < today();
    const shortDate = iso => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : '');

    /** Column state for a (week, weekday): holiday / past / workday(调休上课) / ''. */
    /** Column state for a (week, weekday): holiday / past / ended(学期外) / workday(调休上课) / ''. */
    function dayState(week, weekday) {
        const iso = dateOf(week, weekday);
        const info = iso ? calendarDay(iso) : null;
        if (info?.kind === 'holiday') return { kind: 'holiday', iso, info };
        if (isPastDate(iso)) return { kind: 'past', iso, info };
        const termEnd = String(calendar().term_end || '');
        if (termEnd && iso && iso > termEnd) return { kind: 'ended', iso, info };
        if (info?.kind === 'workday' && info.makeup_for_date) return { kind: 'workday', iso, info };
        return { kind: '', iso, info };
    }
    const LOCKED_KINDS = new Set(['holiday', 'past', 'ended']);
    const LOCK_REASONS = { dawn: '第 1 节为早读，不可放置', holiday: '节假日不可放置', past: '已过去的日期不可放置', ended: '学期已结束，不可放置' };

    function effectiveSlot(week, weekday) {
        const day = dayState(week, weekday);
        if (day.kind === 'workday' && day.info.makeup_week) return { week: Number(day.info.makeup_week), weekday: Number(day.info.makeup_weekday) };
        return { week: Number(week), weekday: Number(weekday) };
    }

    const SWAP_COLORS = ['hsl(var(--ls-primary))', 'hsl(var(--ls-warning))', 'hsl(var(--ls-success))', 'hsl(var(--ls-destructive))', 'hsl(var(--ls-info, var(--ls-primary)))', 'hsl(var(--ls-ink-2))'];
    const swapColor = swap => SWAP_COLORS[Number(swap?.color_index || 0) % SWAP_COLORS.length];
    const swapsForWeek = week => (calendar().swaps || []).filter(s => Number(s.week) === Number(week) || Number(s.makeup_week) === Number(week));
    const swapTitle = swap => `${shortDate(swap.workday_date)}（周${DAY_NAMES[swap.weekday - 1] || ''}）调休上课：补第${swap.makeup_week}周 ${shortDate(swap.makeup_for_date)}（${swap.makeup_for_weekday || ''}）课程${swap.inferred ? '（补课星期为推断，以学校通知为准）' : ''}`;

    /** Lessons that actually happen on a 调休上课 day: the replaced weekday's lessons, rendered as mirrors. */
    function mirrorLessons(week) {
        const out = [];
        if (!week) return out;
        for (let weekday = 1; weekday <= 7; weekday += 1) {
            const day = dayState(week.week_index, weekday);
            const info = day.info; // 已过去的调休日也显示镜像，便于核对
            if (!(info?.kind === 'workday' && info.makeup_for_date && info.makeup_week)) continue;
            const source = weeks().find(w => Number(w.week_index) === Number(info.makeup_week));
            for (const lesson of source?.lessons || []) {
                if (Number(lesson.weekday) !== Number(info.makeup_weekday) || lesson.edit_ghost || lesson.edit_draft || lesson.counts_towards_total === false) continue;
                out.push({ ...lesson, weekday, edit_mirror: true, mirror_label: `调休 · 按${info.makeup_for_weekday || ''}课表`, actual_date: day.iso,
                    event_key: `mirror:${lesson.event_key}:${day.iso}`, source_event_key: lesson.event_key, edit_ghost: false, edit_draft: null,
                    adjustment: null, schedule_changes: [], counts_towards_total: true });
            }
        }
        return out;
    }

    /* ------------------------------------------------------------------ 可调时段 (availability) */
    const AVAIL_LABELS = { block: '学生有课', teacher: '本人有课', room: '教室已占用', ok: '可放置', unknown: '教室占用未查询' };

    function availabilityData(sourceKey) {
        return state.availability.data && state.availability.key === sourceKey ? state.availability.data : null;
    }

    function cellState(data, week, weekday, section) {
        if (!data) return '';
        const w = String(week), d = String(weekday), s = String(section);
        const pick = map => ((map || {})[w] || {})[d]?.[s];
        if (pick(data.students)) return 'block';
        if (pick(data.teacher)) return 'teacher';
        if (pick(data.room_busy)) return 'room';
        if (pick(data.room_checked) === 'free' || data.room?.timetable_synced) return 'ok';
        return 'unknown';
    }

    function cellReason(data, week, weekday, section) {
        const w = String(week), d = String(weekday), s = String(section);
        const pick = map => ((map || {})[w] || {})[d]?.[s];
        return pick(data?.students) || pick(data?.teacher) || pick(data?.room_busy) || '';
    }

    /** Verdict for a candidate slot: block (students/teacher) > room > unknown > ok. */
    function slotVerdict(data, week, weekday, sections) {
        if (!data) return { level: 'unknown', reasons: ['尚未加载可调时段'] };
        const reasons = [];
        let level = 'ok';
        for (const section of sections) {
            const st = cellState(data, week, weekday, section);
            if (st === 'block' || st === 'teacher') { level = 'block'; reasons.push(`第${section}节：${AVAIL_LABELS[st]}（${cellReason(data, week, weekday, section)}）`); }
        }
        if (level === 'block') return { level, reasons };
        for (const section of sections) {
            const st = cellState(data, week, weekday, section);
            if (st === 'room') { level = 'room'; reasons.push(`第${section}节：教室已占用（${cellReason(data, week, weekday, section)}）`); }
            else if (st === 'unknown' && level === 'ok') level = 'unknown';
        }
        const requested = new Set(sections.map(Number));
        for (const block of data.room_busy_blocks || []) {
            if (Number(block.week) !== Number(week) || Number(block.weekday) !== Number(weekday)
                || !block.sections?.length || !block.sections.every(section => requested.has(Number(section)))) continue;
            level = 'room';
            reasons.push(`第${block.sections.join('、')}节组合不可用${block.detail ? `（${block.detail}）` : ''}`);
        }
        return { level, reasons };
    }

    function freeStartCount(data, week, span) {
        if (!data) return null;
        const { max_section: maxSection } = rules();
        let count = 0;
        for (let weekday = 1; weekday <= 7; weekday += 1) {
            const day = dayState(week, weekday);
            if (LOCKED_KINDS.has(day.kind)) continue;      // 节假日 / 已过去 / 学期外 不可放
            const eff = effectiveSlot(week, weekday);
            for (const start of pairStarts()) {
                if (start + span - 1 > maxSection) continue;
                const sections = Array.from({ length: span }, (_, i) => start + i);
                // 学生/本人有空即计为可放（教室占用可在放置后二次搜索解决）
                if (['ok', 'unknown', 'room'].includes(slotVerdict(data, eff.week, eff.weekday, sections).level)) count += 1;
            }
        }
        return count;
    }

    async function loadAvailability(sourceKey, { roomId = '', roomName = '' } = {}) {
        if (!sourceKey || !state.payload?.editable) return null;
        const roomKey = `${roomId}|${roomName}`;
        const termKey = `${term().year}|${term().term}`;
        if (state.availability.key === sourceKey && state.availability.roomKey === roomKey && state.availability.termKey === termKey && (state.availability.data || state.availability.loading)) return state.availability.data;
        const requestId = ++availabilityRequestId;
        const isCurrent = () => requestId === availabilityRequestId && termKey === `${term().year}|${term().term}`;
        state.availability = { key: sourceKey, roomKey, termKey, data: null, loading: true };
        try {
            const params = new URLSearchParams({ year: term().year || '', term: term().term || '', event_key: sourceKey, room_id: roomId, room: roomName });
            const data = await api(`${API}/availability?${params}`);
            if (!isCurrent()) return null;
            state.availability = { key: sourceKey, roomKey, termKey, data: data.availability || null, loading: false };
        } catch (error) {
            if (!isCurrent()) return null;
            state.availability = { key: sourceKey, roomKey, termKey, data: null, loading: false };
            toast(error.message, 'danger');
        }
        renderWeekRail(); renderStage(); renderLegend(); renderDrawerVerdict();
        return state.availability.data;
    }

    function renderLegend() {
        if (!refs.legend) return;
        const selection = state.selectedKey ? resolveSelection(state.selectedKey) : null;
        const data = selection ? availabilityData(selection.lesson.event_key) : null;
        if (!selection) { refs.legend.hidden = true; refs.legend.innerHTML = ''; return; }
        const coverage = data?.coverage || {};
        const notes = [];
        if (!data) notes.push(state.availability.loading ? '正在计算可调时段…' : '可调时段未加载');
        else {
            if (coverage.students === 'synced') notes.push(`学生课表：${(coverage.admin_classes || []).map(c => c.name).join('、') || '已同步'}`);
            else if (coverage.students === 'no_scope') notes.push('学生课表：未同步班级名单，无法判断学生是否有课');
            else notes.push('学生课表：未同步（点「同步可调时段」）');
            notes.push(coverage.room === 'timetable' ? `教室 ${data.room?.name || ''}：整学期占用已同步` : coverage.room === 'checks' ? `教室 ${data.room?.name || ''}：已实时查询 ${data.room?.checked_slots || 0} 个时段` : `教室 ${data.room?.name || ''}：占用未查询，放置后可二次搜索空闲教室`);
        }
        refs.legend.hidden = false;
        refs.legend.innerHTML = `<span class="cse-legend__item cse-legend__item--block">学生/本人有课 · 禁放</span><span class="cse-legend__item cse-legend__item--room">教室已占用 · 需换教室</span><span class="cse-legend__item cse-legend__item--ok">可放置</span><span class="cse-legend__item cse-legend__item--unknown">教室未查询</span><span class="cse-legend__item cse-legend__item--holiday">节假日 · 禁放</span><span class="cse-legend__item cse-legend__item--past">已过去 / 学期外 · 禁放</span><span class="cse-legend__item cse-legend__item--workday">调休上课 · 按被补那天课表</span><span class="cse-legend__note">${escapeHtml(notes.join(' · '))}</span>`;
    }

    /** Holiday / 调休 legend for the active week (shown even without a selection). */
    function renderCalendarNote() {
        const box = refs.calendarNote;
        if (!box) return;
        const week = activeWeekData();
        if (!week) { box.hidden = true; box.innerHTML = ''; return; }
        const items = [];
        for (let weekday = 1; weekday <= 7; weekday += 1) {
            const day = dayState(week.week_index, weekday);
            if (day.kind === 'holiday') items.push(`<span class="cse-calnote__item cse-calnote__item--holiday">周${DAY_NAMES[weekday - 1]} ${shortDate(day.iso)} ${escapeHtml(day.info.label || '放假')}</span>`);
        }
        for (const swap of swapsForWeek(week.week_index)) {
            const outgoing = Number(swap.week) === Number(week.week_index);
            const text = outgoing
                ? `周${DAY_NAMES[swap.weekday - 1]} ${shortDate(swap.workday_date)} 调休上课 → ${Number(swap.makeup_week) === Number(week.week_index) ? '' : `第${swap.makeup_week}周 `}${swap.makeup_for_weekday || ''} ${shortDate(swap.makeup_for_date)} 的课`
                : `${swap.makeup_for_weekday || ''} ${shortDate(swap.makeup_for_date)} 的课 ← 第${swap.week}周 周${DAY_NAMES[swap.weekday - 1]} ${shortDate(swap.workday_date)} 调休上课`;
            items.push(`<span class="cse-calnote__item cse-calnote__item--swap" style="--cse-swap:${swapColor(swap)}" title="${escapeHtml(swapTitle(swap))}"><i></i>${escapeHtml(text)}${swap.inferred ? '<small>推断</small>' : ''}</span>`);
        }
        box.hidden = !items.length;
        box.innerHTML = items.join('');
    }

    function renderAvailMeta() {
        if (!refs.availMeta) return;
        const sync = state.payload?.availability_sync;
        if (!sync || sync.status === 'never') { refs.availMeta.textContent = '可调时段：尚未同步学生课表与教室占用'; return; }
        const when = sync.synced_at || sync.updated_at || '';
        refs.availMeta.textContent = `可调时段：${sync.message || sync.status}${when ? `（${when.replace('T', ' ').slice(0, 16)}）` : ''}`;
    }

    function findLesson(key) {
        for (const week of weeks()) {
            const lesson = (week.lessons || []).find(item => item.event_key === key);
            if (lesson) return { lesson, week };
        }
        return null;
    }

    /** Resolve any card key (real lesson or ghost) to its source lesson + draft. */
    function resolveSelection(key) {
        const found = findLesson(key);
        if (!found) return null;
        if (found.lesson.edit_ghost) {
            const source = findLesson(found.lesson.source_event_key);
            return source ? { ...source, draft: draftById(found.lesson.edit_draft_id), ghostKey: key } : null;
        }
        const draft = found.lesson.edit_draft ? draftById(found.lesson.edit_draft.id) : null;
        return { ...found, draft, ghostKey: draft ? `draft:${draft.id}` : '' };
    }

    function applyPayload(payload, { keepWeek = true, keepSelection = true } = {}) {
        state.payload = payload;
        const list = weeks();
        const focus = Number(term().focus_week) || 0;
        const exists = list.some(week => Number(week.week_index) === state.activeWeek);
        if (!keepWeek || !exists) {
            const current = list.find(week => week.is_current);
            state.activeWeek = focus && list.some(w => Number(w.week_index) === focus) ? focus : (current ? Number(current.week_index) : Number(list[0]?.week_index || 0));
        }
        if (!keepSelection || (state.selectedKey && !resolveSelection(state.selectedKey))) state.selectedKey = '';
        renderAll();
    }

    /* ------------------------------------------------------------------ rendering */
    function renderAll() {
        renderTermSelect();
        renderMeta();
        renderAvailMeta();
        renderWeekRail();
        renderStage();
        renderDrawer();
        renderLegend();
        renderDrafts();
    }

    function renderTermSelect() {
        if (!refs.termSelect) return;
        const entries = overview().terms || [];
        const selected = term();
        const suffix = t => (t.status === 'current' ? '（进行中）' : t.status === 'ended' ? '（已结束）' : t.status === 'future' ? '（未开始）' : '');
        refs.termSelect.innerHTML = entries.length
            ? entries.map(t => `<option value="${escapeHtml(t.year)}|${escapeHtml(t.term)}"${t.year === selected.year && t.term === selected.term ? ' selected' : ''}>${escapeHtml(t.label)}${suffix(t)}</option>`).join('')
            : '<option value="">暂无学期数据</option>';
        refs.termSelect.disabled = !entries.length || state.busy;
    }

    function renderMeta() {
        const pending = pendingDrafts().length;
        const pushed = drafts().filter(d => d.status === 'pushed').length;
        if (refs.meta) {
            refs.meta.innerHTML = `<strong>${escapeHtml(term().label || '未选择学期')}</strong><span>${escapeHtml(overview().schedule_source === 'academic' ? '教务正式课表' : '尚未同步教务课表')} · ${weeks().length} 周 · 本地草稿 ${drafts().length} 项（待保存 ${pending}，已在教务 ${pushed}）</span>`;
        }
        if (refs.pushBtn) {
            refs.pushBtn.disabled = state.busy || !pending || !state.payload?.editable;
            refs.pushBtn.innerHTML = `检测冲突并保存 <span class="cse-btn__badge">${pending}</span>`;
            refs.pushBtn.title = '先用教务自身的冲突检测试跑每一项，再把没有冲突的写入教务草稿；有冲突的会明确告诉你原因和下一步。';
        }
        if (refs.syncBtn) refs.syncBtn.disabled = state.busy;
        if (refs.availSync) refs.availSync.disabled = state.busy || !state.payload?.editable;
        if (refs.backLink) {
            const t = term();
            refs.backLink.href = `/manage/academic/course-schedule${t.year ? `?year=${encodeURIComponent(t.year)}&term=${encodeURIComponent(t.term)}` : ''}`;
        }
    }

    /** Whether a week can receive a drop at all (independent of the selected lesson). */
    function weekLocked(index) {
        return Array.from({ length: 7 }, (_, i) => dayState(index, i + 1)).every(d => LOCKED_KINDS.has(d.kind));
    }

    function renderWeekRail() {
        if (!refs.weeks) return;
        const selection = state.drag ? resolveSelection(state.drag.sourceKey) : (state.selectedKey ? resolveSelection(state.selectedKey) : null);
        const availData = selection ? availabilityData(selection.lesson.event_key) : null;
        const span = selection ? (selection.lesson.sections || []).length : 0;
        const items = weeks().map(week => {
            const index = Number(week.week_index);
            const locked = weekLocked(index);
            const free = !locked && availData && span ? freeStartCount(availData, index, span) : null;
            const draftCount = new Set((week.lessons || []).filter(l => l.edit_draft || l.edit_ghost).map(l => l.edit_draft ? l.edit_draft.id : l.edit_draft_id)).size;
            const holidays = Array.from({ length: 7 }, (_, i) => dayState(index, i + 1)).filter(d => d.kind === 'holiday');
            const swaps = swapsForWeek(index);
            const glow = locked ? 'is-locked' : (selection ? (free === null ? '' : (free > 0 ? 'is-droppable' : 'is-blocked')) : '');
            const classes = ['cse-week', index === state.activeWeek ? 'is-active' : '', week.is_current ? 'is-current' : '', glow, holidays.length ? 'has-holiday' : ''].filter(Boolean).join(' ');
            const marks = [
                draftCount ? `<span class="cse-week__draft" title="本周有 ${draftCount} 项调整">${draftCount} 项调整</span>` : '',
                holidays.length ? `<span class="cse-week__holiday" title="${escapeHtml(holidays.map(d => `${shortDate(d.iso)} ${d.info.label || '放假'}`).join('；'))}">假 ${holidays.length}</span>` : '',
                ...swaps.map(swap => `<span class="cse-week__swap" data-cse-swap-dot="${escapeHtml(swap.workday_date)}" style="--cse-swap:${swapColor(swap)}" title="${escapeHtml(swapTitle(swap))}">${Number(swap.week) === index ? '调休' : '被补'}</span>`),
            ].filter(Boolean).join('');
            const lockedTitle = locked ? '本周全部日期已过去或已超出学期，不可放置' : (selection && free === 0 ? '本周没有学生与本人都有空的时段' : '');
            return `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass ${classes}" data-cse-week="${index}"${locked ? ' data-locked="1"' : ''} aria-pressed="${index === state.activeWeek}"${lockedTitle ? ` title="${escapeHtml(lockedTitle)}"` : ''}>
                <strong class="cse-week__title">${escapeHtml(week.label)}</strong>
                <small class="cse-week__range">${escapeHtml(week.date_range_label || '')}${week.lesson_count ? ` · ${week.lesson_count} 节` : ''}</small>
                ${marks ? `<span class="cse-week__marks">${marks}</span>` : ''}
            </button>`;
        });
        refs.weeks.innerHTML = `<div class="cse-weeks__title">全部周次</div>${items.join('')}`;
        refs.weeks.querySelector('.cse-week.is-active')?.scrollIntoView({ block: 'nearest' });
        scheduleSwapLines();
    }

    function lessonCardHtml(lesson, { minSection, maxSection, columnBase, lane = { lane: 0, count: 1 } }) {
        const sections = lesson.sections || [];
        const start = Math.max(minSection, sections[0] || minSection);
        const end = Math.min(maxSection, sections[sections.length - 1] || start);
        const column = Math.min(7, Math.max(1, lesson.weekday || 1)) + columnBase - 1;
        const laneStyle = lane.count > 1 ? `width:calc(100% / ${lane.count} - 2px);margin-left:calc(100% / ${lane.count} * ${lane.lane});` : '';
        const gridPos = `grid-column:${column};grid-row:${start - minSection + 2} / span ${Math.max(1, end - start + 1)};${laneStyle}`;
        const room = String(lesson.classroom || lesson.classroom_short || '教室待定');
        const ghost = Boolean(lesson.edit_ghost);
        const moved = Boolean(lesson.edit_draft);
        const mirror = Boolean(lesson.edit_mirror);
        const past = !ghost && !mirror && isPastDate(String(lesson.actual_date || ''));
        const status = ghost ? lesson.edit_status : (moved ? lesson.edit_draft.status : '');
        const statusLabel = status ? (state.payload?.status_labels?.[status] || status) : '';
        const key = lesson.event_key || '';
        const selected = state.selectedKey && (state.selectedKey === key || (ghost && state.selectedKey === lesson.source_event_key) || (moved && state.selectedKey === `draft:${lesson.edit_draft.id}`));
        const dragging = state.drag && (state.drag.sourceKey === key || state.drag.ghostKey === key);
        const changes = scheduleChanges(lesson);
        const pendingChange = changes.length && lesson.counts_towards_total === false;
        const classes = ['lq-domain-region', 'cs-lesson', 'cs-lesson--cell', 'cse-lesson', ghost ? 'cse-lesson--ghost' : '', moved ? 'cse-lesson--moved' : '', mirror ? 'cse-lesson--mirror' : '', past ? 'cse-lesson--past' : '',
            selected ? 'is-selected' : '', dragging ? 'is-drag-source' : '', pendingChange ? 'cs-lesson--proposed' : ''].filter(Boolean).join(' ');
        const roomBusy = ghost ? lesson.edit_room_status === 'busy' : (moved && lesson.edit_draft.room_status === 'busy');
        const tag = mirror ? `<span class="cse-tag cse-tag--workday">${escapeHtml(lesson.mirror_label || '调休上课')}</span>`
            : ghost ? `<span class="cse-tag cse-tag--${escapeHtml(status)}">${escapeHtml(statusLabel)}</span>${roomBusy ? '<span class="cse-tag cse-tag--room">需换教室</span>' : ''}`
                : moved ? `<span class="cse-tag cse-tag--muted">已计划调至 ${escapeHtml(lesson.edit_draft.proposed_label)}</span>`
                    : changes.length ? `<span class="cse-tag cse-tag--muted">${escapeHtml(changes.map(change => scheduleChangeLabel(lesson, change)).join('；'))}${pendingChange ? '（仅对照，不可编辑）' : ''}</span>`
                        : past ? '<span class="cse-tag cse-tag--muted">已上过 · 不可调整</span>' : '';
        const time = [lesson.actual_date, lesson.section_label].filter(Boolean).join(' · ');
        return `<div class="cs-lesson-slot" style="${gridPos}">
            <div data-lq-component="region" class="${classes}" data-cse-lesson="${escapeHtml(key)}" data-status="${escapeHtml(status)}"${mirror ? ' data-mirror="1"' : ''}${past ? ' data-past="1"' : ''} tabindex="0" role="button"
                 aria-label="${escapeHtml(`${lesson.course_name} ${time} ${room}`)}" title="${escapeHtml(`${lesson.course_name} · ${room}`)}" style="--cs-accent:hsl(var(--ls-primary))">
                <div data-lq-component="surface" class="lq-surface cs-lesson__surface">
                    <div class="cs-lesson__main">
                        <strong class="cs-lesson__title">${escapeHtml(lesson.course_name)}</strong>
                        <div class="cs-lesson__details">
                            <span class="cs-lesson__meta">${escapeHtml(time)}</span>
                            ${lesson.class_label ? `<span class="cs-lesson__meta">${escapeHtml(lesson.class_label)}</span>` : ''}
                        </div>
                    </div>
                    <div class="cs-lesson__footer">
                        <span class="cs-lesson__room" title="${escapeHtml(room)}"><span class="cs-lesson__room-short">${escapeHtml(compactClassroomName(room))}</span></span>
                        ${tag}
                    </div>
                </div>
            </div>
        </div>`;
    }

    function requestRelationsHtml(week) {
        const seen = new Set(), entries = [];
        for (const lesson of week.lessons || []) for (const change of scheduleChanges(lesson)) {
            const id = JSON.stringify([change.phase, change.request_id, change.detail_id || lesson.session_id]);
            if (seen.has(id)) continue;
            seen.add(id);
            const isOriginal = change.endpoint === 'original';
            const originalKey = isOriginal ? lesson.event_key : change.counterpart_event_key;
            const targetKey = isOriginal ? change.counterpart_event_key : lesson.event_key;
            const position = slot => slot ? `${slot.date} ${sectionText(slot.sections)} ${slot.room || ''}`.trim() : '无目标课次';
            const jump = (key, label) => key ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--ghost lq-btn--sm" data-cse-request-jump="${escapeHtml(key)}">${escapeHtml(label)}</button>` : escapeHtml(label);
            entries.push(`<p><strong>${escapeHtml(lesson.course_name)} · ${escapeHtml(scheduleChangeLabel(lesson, change))}</strong><br>${jump(originalKey, `原安排：${position(change.original)}`)} → ${jump(targetKey, `${change.phase === 'approved' ? '现安排' : '计划安排'}：${position(change.proposed)}`)}</p>`);
        }
        return entries.length ? `<section data-lq-component="surface" class="lq-surface cse-request-relations" data-lq-padding="sm" aria-label="调课申请关系"><strong>调课申请 · 原安排与计划位置</strong><p>以下来自调课申请，与节假日调休分开；计划位置仅供对照，不计入正式课时。</p>${entries.join('')}</section>` : '';
    }

    function renderStage() {
        const week = activeWeekData();
        if (refs.stageTitle) refs.stageTitle.textContent = week ? `${week.label}${week.is_current ? '（本周）' : ''}` : '—';
        if (refs.stageSub) refs.stageSub.textContent = week ? `${week.date_range_label || ''} · ${week.lesson_count} 节安排 · ${week.total_hours} 课时` : '';
        if (!refs.stageBody) return;
        if (!state.payload?.editable) {
            refs.stageBody.innerHTML = `<div class="cse-empty"><strong>当前学期尚无教务正式课表</strong><p>请先点击「同步教务课表」拉取正式课表与调停课申请，再进入编辑模式。</p></div>`;
            return;
        }
        if (!week) { refs.stageBody.innerHTML = '<div class="cse-empty"><strong>请选择周次</strong></div>'; return; }
        const { min_section: minSection, max_section: maxSection } = rules();
        const sectionCount = maxSection;
        const columnBase = 3;
        const overlaySelection = state.drag ? resolveSelection(state.drag.sourceKey) : (state.selectedKey ? resolveSelection(state.selectedKey) : null);
        const availData = overlaySelection ? availabilityData(overlaySelection.lesson.event_key) : null;
        const todayIso = today();
        const days = Array.from({ length: 7 }, (_, i) => dayState(week.week_index, i + 1));
        const todayColumn = days.findIndex(d => d.iso === todayIso) + 1;
        const dayHeads = DAY_NAMES.map((day, index) => {
            const weekday = index + 1;
            const st = days[index];
            const swap = st.kind === 'workday' ? (calendar().swaps || []).find(s => s.workday_date === st.iso) : null;
            const classes = ['cs-grid__day', 'cse-dayhead', weekday >= 6 ? 'cs-grid__day--weekend' : '', weekday === todayColumn ? 'cs-grid__day--today' : '', st.kind ? `cse-dayhead--${st.kind}` : ''].filter(Boolean).join(' ');
            const tag = st.kind === 'holiday' ? `<span class="cse-dayhead__tag cse-dayhead__tag--holiday" title="${escapeHtml(st.info.label || '放假')}">${escapeHtml(st.info.label || '放假')}</span>`
                : st.kind === 'workday' ? `<span class="cse-dayhead__tag cse-dayhead__tag--workday" style="--cse-swap:${swapColor(swap)}" title="${escapeHtml(swap ? swapTitle(swap) : st.info.label || '')}">调休 · 补${escapeHtml(st.info.makeup_for_weekday || '')} ${shortDate(st.info.makeup_for_date)}${st.info.inferred ? '?' : ''}</span>`
                    : st.kind === 'past' ? '<span class="cse-dayhead__tag cse-dayhead__tag--past">已过</span>'
                        : st.kind === 'ended' ? '<span class="cse-dayhead__tag cse-dayhead__tag--past">学期外</span>' : '';
            return `<div class="${classes}" data-cse-dayhead="${weekday}" data-date="${escapeHtml(st.iso)}" style="grid-column:${index + columnBase};grid-row:1;"><span class="cse-dayhead__name">周${day}<small>${escapeHtml(shortDate(st.iso))}${weekday === todayColumn ? ' · 今天' : ''}</small></span>${tag}</div>`;
        }).join('');
        const sectionLabels = Array.from({ length: sectionCount }, (_, offset) => {
            const section = 1 + offset;
            const pairStart = pairStarts().includes(section);
            return `<div class="cs-grid__section cs-grid__section--${sectionBand(section)}${pairStart ? ' cse-section--pair' : ''}" style="grid-column:2;grid-row:${offset + 2};" title="${section < minSection ? '早读时段不可放置课次' : pairStart ? `课次可从第 ${section} 节开始` : ''}">${section}</div>`;
        }).join('');
        const cells = Array.from({ length: sectionCount * 7 }, (_, cell) => {
            const section = 1 + Math.floor(cell / 7);
            const weekday = (cell % 7) + 1;
            const st = days[weekday - 1];
            const dayLock = LOCKED_KINDS.has(st.kind) ? st.kind : '';
            const locked = section < minSection || Boolean(dayLock);
            const eff = effectiveSlot(week.week_index, weekday);
            const avail = availData && !locked ? cellState(availData, eff.week, eff.weekday, section) : '';
            const reason = avail ? cellReason(availData, eff.week, eff.weekday, section) : '';
            const classes = ['cs-grid__cellbg', `cs-grid__cellbg--${sectionBand(section)}`, weekday >= 6 ? 'cs-grid__cellbg--weekend' : '',
                weekday === todayColumn ? 'cs-grid__cellbg--today' : '', section < minSection ? 'cs-grid__cellbg--locked' : '', dayLock ? `cs-grid__cellbg--${dayLock}` : '',
                st.kind === 'workday' ? 'cs-grid__cellbg--workday' : '', avail ? `is-avail-${avail}` : ''].filter(Boolean).join(' ');
            const title = dayLock ? `${LOCK_REASONS[dayLock]}${st.kind === 'holiday' ? `（${st.info.label || ''}）` : ''}` : reason ? `${AVAIL_LABELS[avail] || ''}：${reason}` : '';
            return `<div class="${classes}" data-cse-cell data-weekday="${weekday}" data-section="${section}"${locked ? ` data-locked="${dayLock || 'dawn'}"` : ''}${title ? ` title="${escapeHtml(title)}"` : ''} style="grid-column:${weekday - 1 + columnBase};grid-row:${section + 1};"></div>`;
        }).join('');
        const bands = [];
        let blockStart = 0;
        for (let offset = 1; offset <= sectionCount; offset += 1) {
            const prevBand = sectionBand(1 + blockStart);
            const band = offset < sectionCount ? sectionBand(1 + offset) : '';
            if (offset === sectionCount || band !== prevBand) {
                bands.push(`<div class="cs-grid__band cs-grid__band--${prevBand}" style="grid-column:1;grid-row:${blockStart + 2} / span ${offset - blockStart};">${BAND_LABELS[prevBand]}</div>`);
                blockStart = offset;
            }
        }
        const visibleLessons = [...(week.lessons || []), ...mirrorLessons(week)];
        const lanes = scheduleLessonLanes(visibleLessons);
        const lessons = visibleLessons.map((lesson, index) => lessonCardHtml(lesson, { minSection: 1, maxSection, columnBase, lane: lanes.get(index) })).join('');
        const rows = Array.from({ length: sectionCount }, (_, offset) => (1 + offset < minSection ? 'minmax(22px, .5fr)' : 'minmax(40px, 1fr)')).join(' ');
        refs.stageBody.innerHTML = `${requestRelationsHtml(week)}<div class="cs-grid cs-grid--expanded cse-grid" style="grid-template-columns:30px 54px repeat(7, minmax(0, 1fr));grid-template-rows:44px ${rows};">
            <div class="cs-grid__corner" style="grid-column:1 / span 2;grid-row:1;">节</div>${dayHeads}${bands.join('')}${sectionLabels}${cells}${lessons}
        </div>`;
        renderCalendarNote();
        scheduleSwapLines();
    }

    /* ------------------------------------------------------------------ 调休连线 (swap arrows) */
    let swapLinesFrame = 0;
    function scheduleSwapLines() {
        if (swapLinesFrame) return;
        swapLinesFrame = window.requestAnimationFrame(() => { swapLinesFrame = 0; renderSwapLines(); });
    }

    /** Anchor rectangle (relative to the layout box) for a day header or a week rail card. */
    function anchorRect(element, layoutRect) {
        if (!element) return null;
        const r = element.getBoundingClientRect();
        if (!r.width || !r.height) return null;
        return { left: r.left - layoutRect.left, top: r.top - layoutRect.top, right: r.right - layoutRect.left, bottom: r.bottom - layoutRect.top,
            cx: (r.left + r.right) / 2 - layoutRect.left, cy: (r.top + r.bottom) / 2 - layoutRect.top };
    }

    function swapPath(from, to, kind) {
        // 方向：被补那天（课从这里来）→ 调休上课日（课在这里上）。同周在列头之间画弧；跨周与左侧周卡相连。
        if (kind === 'head-head') {
            const lift = Math.max(26, Math.min(60, Math.abs(to.cx - from.cx) * 0.25));
            const y = Math.min(from.top, to.top) - 6;
            const start = { x: from.cx, y: from.top }, end = { x: to.cx, y: to.top };
            return { start, end, d: `M ${start.x} ${start.y} C ${start.x} ${y - lift}, ${end.x} ${y - lift}, ${end.x} ${end.y}`, label: { x: (from.cx + to.cx) / 2, y: y - lift * 0.75 } };
        }
        if (kind === 'head-rail') {
            const start = { x: from.cx, y: from.top }, end = { x: to.right + 2, y: to.cy };
            const midX = end.x + Math.max(40, (start.x - end.x) * 0.35);
            return { start, end, d: `M ${start.x} ${start.y} C ${start.x} ${start.y - 40}, ${midX} ${end.y}, ${end.x} ${end.y}`, label: { x: (start.x + midX) / 2, y: start.y - 34 } };
        }
        if (kind === 'rail-head') {
            const start = { x: from.right + 2, y: from.cy }, end = { x: to.cx, y: to.top };
            const midX = start.x + Math.max(40, (end.x - start.x) * 0.35);
            return { start, end, d: `M ${start.x} ${start.y} C ${midX} ${start.y}, ${end.x} ${end.y - 40}, ${end.x} ${end.y}`, label: { x: (start.x + midX) / 2, y: start.y - 14 } };
        }
        const x = Math.max(from.right, to.right) + 14;
        const start = { x: from.right, y: from.cy }, end = { x: to.right, y: to.cy };
        return { start, end, d: `M ${start.x} ${start.y} C ${x} ${start.y}, ${x} ${end.y}, ${end.x} ${end.y}`, label: { x: x + 4, y: (start.y + end.y) / 2 } };
    }

    function renderSwapLines() {
        const svg = refs.swaps;
        const layout = refs.layout;
        if (!svg || !layout) return;
        const swaps = calendar().swaps || [];
        const week = activeWeekData();
        if (!swaps.length || !week || !state.payload?.editable) { svg.replaceChildren(); svg.setAttribute('hidden', ''); return; }
        const layoutRect = layout.getBoundingClientRect();
        svg.removeAttribute('hidden'); // SVG 元素没有 hidden IDL 属性，只能操作 attribute
        svg.setAttribute('viewBox', `0 0 ${layoutRect.width} ${layoutRect.height}`);
        svg.setAttribute('width', String(layoutRect.width)); svg.setAttribute('height', String(layoutRect.height));
        const railRect = refs.weeks?.getBoundingClientRect();
        const railVisible = card => { if (!railRect || !card) return false; const r = card.getBoundingClientRect(); return r.bottom > railRect.top + 4 && r.top < railRect.bottom - 4; };
        const head = weekday => refs.stageBody?.querySelector(`[data-cse-dayhead="${weekday}"]`);
        const railCard = w => refs.weeks?.querySelector(`[data-cse-week="${w}"]`);
        const grid = refs.stageBody?.querySelector('.cse-grid');
        const gridRect = grid ? anchorRect(grid, layoutRect) : null;
        const ns = 'http://www.w3.org/2000/svg';
        const el = (tag, attrs = {}, text = '') => { const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v)); if (text) n.textContent = text; return n; };
        const defs = el('defs');
        const nodes = [defs];
        const active = Number(week.week_index);
        swaps.forEach((swap, index) => {
            const color = swapColor(swap);
            const sourceWeek = Number(swap.makeup_week), targetWeek = Number(swap.week); // 课从被补那天来，到调休日上
            let from = null, to = null, kind = '';
            if (sourceWeek === active && targetWeek === active) { from = anchorRect(head(swap.makeup_weekday), layoutRect); to = anchorRect(head(swap.weekday), layoutRect); kind = 'head-head'; }
            else if (sourceWeek === active) { const card = railCard(targetWeek); if (!railVisible(card)) return; from = anchorRect(head(swap.makeup_weekday), layoutRect); to = anchorRect(card, layoutRect); kind = 'head-rail'; }
            else if (targetWeek === active) { const card = railCard(sourceWeek); if (!railVisible(card)) return; from = anchorRect(card, layoutRect); to = anchorRect(head(swap.weekday), layoutRect); kind = 'rail-head'; }
            else { if (sourceWeek === targetWeek) return; /* 同周调休在周卡徽标里已标出，切到该周才画列头弧线 */ const a = railCard(sourceWeek), b = railCard(targetWeek); if (!railVisible(a) || !railVisible(b)) return; from = anchorRect(a, layoutRect); to = anchorRect(b, layoutRect); kind = 'rail-rail'; }
            if (!from || !to) return;
            const markerId = `cse-swap-arrow-${index}`;
            const marker = el('marker', { id: markerId, markerWidth: 12, markerHeight: 12, refX: 9, refY: 6, orient: 'auto-start-reverse', markerUnits: 'userSpaceOnUse', overflow: 'visible' });
            marker.append(el('path', { d: 'M 1 1.5 L 10.5 6 L 1 10.5 Z', fill: color, stroke: color, 'stroke-width': 1, 'stroke-linejoin': 'round' }));
            defs.append(marker);
            const { d, label, start } = swapPath(from, to, kind);
            const group = el('g', { class: `cse-swap cse-swap--${kind}`, 'data-cse-swap': swap.workday_date, style: `color:${color}` });
            group.append(el('title', {}, swapTitle(swap)));
            // 目标列（调休上课日）用细虚线包裹，留内边距，不侵占相邻列
            if (targetWeek === active && gridRect) {
                const column = anchorRect(head(swap.weekday), layoutRect);
                if (column) group.append(el('rect', { class: 'cse-swap__column', x: column.left + 3, y: column.top - 2, width: Math.max(0, column.right - column.left - 6), height: Math.max(0, gridRect.bottom - column.top - 2), rx: 10, stroke: color }));
            }
            group.append(el('path', { class: 'cse-swap__line', d, stroke: color, 'marker-end': `url(#${markerId})` }));
            group.append(el('circle', { class: 'cse-swap__origin', cx: start.x, cy: start.y, r: 3.2, fill: color, stroke: color }));
            const sourceDay = swap.makeup_for_weekday || `周${DAY_NAMES[swap.makeup_weekday - 1] || ''}`;
            const targetDay = `周${DAY_NAMES[swap.weekday - 1] || ''}`;
            const text = kind === 'head-head' ? `${sourceDay}的课 → ${targetDay}上`
                : kind === 'head-rail' ? `${sourceDay}的课 → 第${targetWeek}周${targetDay}上`
                    : kind === 'rail-head' ? `第${sourceWeek}周${sourceDay}的课 → ${targetDay}上` : `第${sourceWeek}周→第${targetWeek}周`;
            const width = Math.ceil([...text].reduce((t, c) => t + (c.charCodeAt(0) > 255 ? 12 : 7), 12));
            const labelGroup = el('g', { class: 'cse-swap__label', transform: `translate(${label.x} ${label.y})` });
            labelGroup.append(el('rect', { x: -width / 2, y: -9, width, height: 18, rx: 6 }));
            labelGroup.append(el('text', { x: 0, y: 0 }, text));
            group.append(labelGroup);
            nodes.push(group);
        });
        svg.replaceChildren(...nodes);
    }

    /* ------------------------------------------------------------------ drawer */
    function buildForm(selection) {
        const { lesson, week, draft } = selection;
        const proposed = draft?.proposed;
        return {
            key: lesson.event_key, span: (lesson.sections || []).length,
            week: proposed ? Number(proposed.week) : Number(week.week_index),
            weekday: proposed ? Number(proposed.weekday) : Number(lesson.weekday),
            start: proposed ? Number(proposed.sections[0]) : Number(lesson.sections[0]),
            room: proposed ? String(proposed.room || '') : String(lesson.classroom || ''),
            room_id: proposed ? String(proposed.room_id || '') : '',
            reason: draft?.reason || '',
        };
    }

    function renderDrawer() {
        if (!refs.drawer) return;
        refs.drawer.dataset.lqPanelPresence = '';
        const selection = state.selectedKey ? resolveSelection(state.selectedKey) : null;
        refs.layout?.classList.toggle('has-drawer', Boolean(selection));
        if (!selection) { refs.drawer.hidden = true; refs.drawer.innerHTML = ''; state.form = null; return; }
        const { lesson, week, draft } = selection;
        if (!state.form || state.form.key !== lesson.event_key) state.form = buildForm(selection);
        const form = state.form;
        const { max_section: maxSection, max_week: maxWeek } = rules();
        const pastLesson = isPastDate(String(lesson.actual_date || ''));
        const locked = lesson.counts_towards_total === false || draft?.status === 'pushed' || pastLesson;
        const termEnd = String(calendar().term_end || '');
        const weekOptions = Array.from({ length: Math.max(maxWeek, weeks().length) }, (_, i) => i + 1).map(w => {
            const end = dateOf(w, 7), start = dateOf(w, 1);
            const flag = end && isPastDate(end) ? '已过' : (termEnd && start && start > termEnd ? '学期外' : '');
            return `<option value="${w}"${w === form.week ? ' selected' : ''}${flag ? ' data-hint="' + flag + '"' : ''}>第${w}周 ${escapeHtml(shortDate(start))}–${escapeHtml(shortDate(end))}</option>`;
        }).join('');
        const dayOptions = DAY_NAMES.map((d, i) => {
            const st = dayState(form.week, i + 1);
            const hint = st.kind === 'holiday' ? (st.info.label || '放假') : st.kind === 'past' ? '已过' : st.kind === 'ended' ? '学期外' : st.kind === 'workday' ? `调休·补${st.info.makeup_for_weekday || ''}` : '';
            return `<option value="${i + 1}"${i + 1 === form.weekday ? ' selected' : ''}${hint ? ` data-hint="${escapeHtml(hint)}"` : ''}>周${d} ${escapeHtml(shortDate(st.iso))}</option>`;
        }).join('');
        const starts = pairStarts().filter(s => s + form.span - 1 <= maxSection);
        if (form.start && !starts.includes(form.start)) starts.push(form.start);
        const startOptions = starts.sort((a, b) => a - b)
            .map(s => `<option value="${s}"${s === form.start ? ' selected' : ''}${pairStarts().includes(s) ? '' : ' data-hint="不合规"'}>第${s}${form.span > 1 ? `-${s + form.span - 1}` : ''}节 · ${BAND_LABELS[sectionBand(s)]}</option>`).join('');
        const targetDay = dayState(form.week, form.weekday);
        const dayNote = targetDay.kind === 'holiday' ? `<div class="cse-status cse-status--conflict">目标日期 ${shortDate(targetDay.iso)} 为节假日（${escapeHtml(targetDay.info.label || '放假')}），不能安排课程。</div>`
            : targetDay.kind === 'past' ? `<div class="cse-status cse-status--conflict">目标日期 ${shortDate(targetDay.iso)} 已经过去，不能放置。</div>`
                : targetDay.kind === 'ended' ? `<div class="cse-status cse-status--conflict">目标日期 ${shortDate(targetDay.iso)} 已超出学期结束日期（${escapeHtml(termEnd)}），不能放置。</div>`
                    : targetDay.kind === 'workday' ? `<div class="cse-status cse-status--workday">目标日期 ${shortDate(targetDay.iso)} 为调休上课日：当天按 ${escapeHtml(targetDay.info.makeup_for_weekday || '')}（${shortDate(targetDay.info.makeup_for_date)}）课表上课，冲突与可调时段按那一天判断。${targetDay.info.inferred ? '补课星期为推断，以学校通知为准。' : ''}</div>` : '';
        const statusBlock = pastLesson ? '<div class="cse-status cse-status--muted"><strong>该课次已上过</strong><br>已发生的课次不能再调整；调整后续课次时，课次序号与材料保持不变。</div>'
            : draft ? `<div class="cse-status cse-status--${escapeHtml(draft.status)}"><strong>${escapeHtml(draft.status_label)}</strong>${draft.remote_message ? `<br>${escapeHtml(draft.remote_message)}` : ''}${draft.status === 'pushed' ? '<br>如需修改，请先「从教务撤回」。' : ''}${precheckOf(draft) && draft.status !== 'pushed' ? `<br>${escapeHtml(PRECHECK_LABELS[precheckOf(draft).status] || '教务预检')}${precheckOf(draft).message ? `：${escapeHtml(precheckOf(draft).message)}` : ''}` : ''}</div>` : '';
        const originalRoom = String(lesson.classroom || '');
        const roomOptions = `<option value=""${form.room_id ? '' : ' selected'}>沿用原教室${originalRoom ? `：${escapeHtml(compactClassroomName(originalRoom))}` : ''}</option>${form.room_id ? `<option value="${escapeHtml(form.room_id)}" selected>${escapeHtml(form.room)}</option>` : ''}`;
        const proofs = draft ? (draft.proofs || []) : [];
        const proofsBlock = draft ? `<section class="cse-section" data-cse-proofs>
                    <div class="cse-section__title">证明材料 <span class="cse-section__hint">放假通知、会议通知等；提交教务申请时需一并附上</span></div>
                    ${proofs.length ? `<div class="cse-proofs">${proofs.map(p => `<span class="cse-proof"><a href="${API}/drafts/${draft.id}/proofs/${escapeHtml(p.id)}" target="_blank" rel="noopener" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</a><small>${formatBytes(p.size)}</small><button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-proof__remove" data-cse-proof-del="${escapeHtml(p.id)}" aria-label="删除 ${escapeHtml(p.name)}"${locked ? ' disabled' : ''}>×</button></span>`).join('')}</div>` : '<div class="cse-materials__empty">还没有上传证明材料。</div>'}
                    <label class="cse-btn cse-btn--sm cse-upload${state.proofsBusy ? ' is-busy' : ''}"><input data-lq-component="file" class="lq-native-file" type="file" data-cse-proof-input multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx,.txt" hidden${locked ? ' disabled' : ''}>${state.proofsBusy ? '上传中…' : '添加证明材料'}</label>
                </section>` : '';
        const materialsBlock = renderMaterialsBlock(lesson);
        refs.drawer.hidden = false;
        refs.drawer.innerHTML = `
            <div class="cse-drawer__head">
                <div><h3>${escapeHtml(lesson.course_name)}</h3><p>${escapeHtml(lesson.class_label || lesson.teaching_class_name || '')}${lesson.teaching_class_name && lesson.class_label !== lesson.teaching_class_name ? ` · ${escapeHtml(lesson.teaching_class_name)}` : ''}</p></div>
                <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-drawer__close" data-cse-close aria-label="关闭课次属性">×</button>
            </div>
            <div class="cse-drawer__body">
                ${statusBlock}
                <section class="cse-section cse-section--origin">
                    <div class="cse-section__title">原安排</div>
                    <div class="cse-origin">
                        <div class="cse-origin__time">${escapeHtml(week.label)} ${escapeHtml(lesson.weekday_label || '')} ${escapeHtml(lesson.section_label || '')}${lesson.actual_date ? `<small>${escapeHtml(lesson.actual_date)}</small>` : ''}</div>
                        <div class="cse-origin__meta"><span>${escapeHtml(lesson.classroom || '教室待定')}</span><span>${form.span} 节${lesson.session_no ? ` · 第${lesson.session_no}次课${lesson.session_total ? `（共${lesson.session_total}次）` : ''}` : ''}</span></div>
                    </div>
                </section>
                <section class="cse-section">
                    <div class="cse-section__title">调整到</div>
                    <div class="cse-field-grid">
                        <div class="cse-field"><label for="cseWeek">周次</label><select data-lq-component="select" class="lq-select" id="cseWeek" data-cse-field="week" data-lq-dropdown aria-label="周次"${locked ? ' disabled' : ''}>${weekOptions}</select></div>
                        <div class="cse-field"><label for="cseStart">节次</label><select data-lq-component="select" class="lq-select" id="cseStart" data-cse-field="start" data-lq-dropdown aria-label="节次"${locked ? ' disabled' : ''}>${startOptions}</select></div>
                        <div class="cse-field cse-field--full"><label for="cseWeekday">星期</label><select data-lq-component="select" class="lq-select" id="cseWeekday" data-cse-field="weekday" data-lq-dropdown aria-label="星期"${locked ? ' disabled' : ''}>${dayOptions}</select></div>
                    </div>
                    ${dayNote}
                    <div class="cse-field" data-cse-room-dropdown>
                        <label for="cseRoom">教室（教务场地）</label>
                        <select data-lq-component="select" class="lq-select" id="cseRoom" data-cse-field="room" data-lq-dropdown data-lq-searchable data-lq-placeholder="沿用原教室" aria-label="教室"${locked ? ' disabled' : ''}>${roomOptions}</select>
                        <div class="cse-field__hint">${form.room_id ? `已选教务场地 ${escapeHtml(form.room_id)}` : '输入楼名或教室号搜索教务场地；不选则沿用原教室'}</div>
                    </div>
                    <div class="cse-verdict" data-cse-verdict></div>
                    <div class="cse-field cse-free-rooms" data-cse-free-rooms-panel>
                        <div class="cse-free-rooms__head">
                            <label>该时段空闲教室（实时查教务）</label>
                            <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" data-cse-free-rooms data-cse-locked="${locked}"${locked ? ' disabled' : ''}>查询空闲教室</button>
                        </div>
                        <div class="cse-free-rooms__body" data-cse-free-rooms-list></div>
                    </div>
                    <div class="cse-field">
                        <div class="cse-field__labelrow"><label for="cseReason">调课原因</label><button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm cse-btn--ai" data-cse-reason-ai${locked ? ' disabled' : ''}>AI 填写</button></div>
                        <textarea data-lq-component="textarea" id="cseReason" class="lq-textarea cse-textarea" data-cse-field="reason" maxlength="400" rows="2" placeholder="简短说明，例如：国庆假期调休，课程顺延"${locked ? ' disabled' : ''}>${escapeHtml(form.reason)}</textarea>
                        <div class="cse-field__hint">随草稿一并写入教务，可在教务提交时修改。</div>
                    </div>
                </section>
                ${proofsBlock}
                ${materialsBlock}
            </div>
            <div class="cse-drawer__foot">
                ${locked ? '' : '<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--primary lq-btn--prominent" data-cse-save>保存变更</button>'}
                ${draft && draft.status !== 'pushed' ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--danger lq-btn--destructive" data-cse-discard="${draft.id}">撤销变更</button>` : ''}
                ${draft && draft.status === 'pushed' ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--danger lq-btn--destructive" data-cse-withdraw="${draft.id}">从教务撤回</button>` : ''}
                ${lesson.classroom_url ? `<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn" href="${escapeHtml(lesson.classroom_url)}">进入课堂</a>` : ''}
            </div>`;
        bindDrawerDropdowns();
        if (lesson.class_offering_id && lesson.session_id) loadMaterials(lesson);
        renderDrawerVerdict();
        renderFreeRooms();
        loadAvailability(lesson.event_key, { roomId: form.room_id, roomName: form.room_id ? form.room : '' });
    }

    function formatBytes(size) {
        const n = Number(size) || 0;
        return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`;
    }

    /** Glass dropdowns for the drawer selects; the native selects stay the value owners. */
    function bindDrawerDropdowns() {
        for (const select of refs.drawer?.querySelectorAll('select[data-lq-dropdown]') || []) {
            if (select.dataset.cseField === 'room') {
                let binding = null;
                binding = bindDropdown(select, { searchable: true, placeholder: '沿用原教室', onQuery: ticket => queryRooms(binding, ticket) });
            } else bindDropdown(select);
        }
    }

    async function queryRooms(binding, ticket) {
        const q = String(ticket.query || '').trim();
        const original = state.form ? resolveSelection(state.form.key)?.lesson : null;
        const keep = [{ value: '', label: `沿用原教室${original?.classroom ? `：${compactClassroomName(original.classroom)}` : ''}` }];
        if (!q) { binding.setResults({ ...ticket, options: keep.concat(state.form?.room_id ? [{ value: state.form.room_id, label: state.form.room }] : []) }); return; }
        state.roomsRequest?.abort?.();
        const controller = new AbortController(); state.roomsRequest = controller;
        try {
            const data = await api(`${API}/rooms?q=${encodeURIComponent(q)}&limit=30`, { signal: controller.signal });
            if (controller.signal.aborted) return;
            const rooms = (data.rooms || []).map(room => ({ value: String(room.room_id), label: String(room.full_name || room.name), hint: [room.building, room.seat_count ? `${room.seat_count} 座` : '', room.schedulable ? '' : '不可排课'].filter(Boolean).join(' · ') }));
            binding.setResults({ ...ticket, options: keep.concat(rooms), message: rooms.length ? '' : '没有匹配的教务场地' });
        } catch (error) { if (error.name !== 'AbortError') binding.setResults({ ...ticket, status: 'error', message: error.message }); }
    }

    async function fillReasonByAi() {
        const form = state.form;
        if (!form) return;
        const button = refs.drawer?.querySelector('[data-cse-reason-ai]');
        if (button) { button.disabled = true; button.textContent = 'AI 思考中…'; }
        try {
            const data = await api(`${API}/reason-suggest`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term, event_key: form.key, week: form.week, weekday: form.weekday, sections: currentFormSections(), room: form.room_id ? form.room : '', note: form.reason }) });
            state.form = { ...state.form, reason: data.reason || '' };
            const area = refs.drawer?.querySelector('[data-cse-field="reason"]');
            if (area) area.value = state.form.reason;
            toast(data.source === 'ai' ? 'AI 已填写调课原因，可继续修改。' : '已按校历规则填写调课原因（AI 暂不可用）。', data.source === 'ai' ? 'success' : 'info');
        } catch (error) { toast(error.message, 'danger'); }
        finally { if (button) { button.disabled = false; button.textContent = 'AI 填写'; } }
    }

    function patchDraft(draft) {
        if (!draft || !state.payload) return;
        const list = drafts();
        const index = list.findIndex(d => d.id === draft.id);
        if (index >= 0) list[index] = draft; else list.push(draft);
        state.payload = { ...state.payload, drafts: list };
    }

    async function uploadProofs(draftId, files) {
        if (!files?.length) return null;
        state.proofsBusy = true; renderDrawer();
        try {
            const body = new FormData();
            for (const file of files) body.append('files', file, file.name);
            const response = await fetch(`${API}/drafts/${draftId}/proofs`, { method: 'POST', body, credentials: 'same-origin' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || '证明材料上传失败');
            patchDraft(data.draft);
            toast(`已上传 ${data.stored?.length || 0} 份证明材料。`, 'success');
            return data.draft;
        } catch (error) { toast(error.message, 'danger'); return null; }
        finally { state.proofsBusy = false; renderDrawer(); renderDrafts(); }
    }

    async function removeProof(draftId, fileId) {
        try {
            const data = await api(`${API}/drafts/${draftId}/proofs/${encodeURIComponent(fileId)}`, { method: 'DELETE' });
            patchDraft(data.draft); renderDrawer(); renderDrafts();
        } catch (error) { toast(error.message, 'danger'); }
    }

    function currentFormSections() {
        const form = state.form;
        return form ? Array.from({ length: form.span }, (_, i) => form.start + i) : [];
    }

    function renderDrawerVerdict() {
        const box = refs.drawer?.querySelector('[data-cse-verdict]');
        if (!box || !state.form) return;
        const data = availabilityData(state.form.key);
        const day = dayState(state.form.week, state.form.weekday);
        if (day.kind === 'holiday' || day.kind === 'past') {
            box.className = 'cse-verdict cse-verdict--block';
            box.innerHTML = `<strong>${day.kind === 'holiday' ? `节假日（${escapeHtml(day.info.label || '放假')}）不可放置` : '已过去的日期不可放置'}</strong>`;
            return;
        }
        if (!pairStarts().includes(state.form.start)) {
            box.className = 'cse-verdict cse-verdict--block';
            box.innerHTML = '<strong>起始节次不合规：课次只能从第 2、4、6、8、10 节开始</strong>';
            return;
        }
        const eff = effectiveSlot(state.form.week, state.form.weekday);
        const verdict = slotVerdict(data, eff.week, eff.weekday, currentFormSections());
        const text = { block: '不可调整到此时段', room: '学生有空，但教室已占用：请在下方选择空闲教室', unknown: '学生时段可用；教室占用尚未查询，可点「查询空闲教室」确认', ok: '该时段可放置' }[verdict.level];
        box.className = `cse-verdict cse-verdict--${verdict.level}`;
        box.innerHTML = `<strong>${escapeHtml(text)}</strong>${verdict.reasons.length ? `<ul>${verdict.reasons.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>` : ''}`;
    }

    function freeRoomSlotKey() {
        const form = state.form;
        return form ? JSON.stringify([term().year, term().term, form.key, form.week, form.weekday,
            currentFormSections(), form.room_id || '', form.room_id ? form.room : resolveSelection(form.key)?.lesson?.classroom || '']) : '';
    }

    function renderFreeRooms() {
        const list = refs.drawer?.querySelector('[data-cse-free-rooms-list]');
        if (!list || !state.form) return;
        if (state.freeRooms.slotKey && state.freeRooms.slotKey !== freeRoomSlotKey()) {
            freeRoomsRequestId += 1;
            state.freeRooms = { slotKey: '', items: [], status: '', roomStatus: '', loading: false, message: '', page: 0, hasMore: false };
        }
        const fr = state.freeRooms;
        const queryButton = refs.drawer.querySelector('[data-cse-free-rooms]');
        if (queryButton) {
            queryButton.disabled = queryButton.dataset.cseLocked === 'true' || fr.loading;
            queryButton.textContent = fr.loading ? '查询中…' : '查询空闲教室';
        }
        if (fr.slotKey !== freeRoomSlotKey()) { list.innerHTML = '<div class="cse-materials__empty">选择目标时段后点击「查询空闲教室」，教务会返回该时段空闲教室。</div>'; return; }
        if (fr.loading && !fr.items.length) { list.innerHTML = '<div class="cse-materials__empty">正在向教务查询空闲教室…</div>'; return; }
        if (fr.status !== 'success') { list.innerHTML = `<div class="cse-materials__empty">${escapeHtml(fr.message || '查询失败')}</div>`; return; }
        const roomLine = fr.roomStatus === 'busy' ? '<div class="cse-status cse-status--conflict">当前教室该时段已被占用，请从下方选择一间空闲教室。</div>'
            : fr.roomStatus === 'free' ? '<div class="cse-status cse-status--pushed">当前教室该时段空闲，可直接保存。</div>'
                : fr.roomStatusMessage ? `<div class="cse-materials__empty" role="status">${escapeHtml(fr.roomStatusMessage)}</div>` : '';
        const items = fr.items.map(room => `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-rooms__item" data-cse-room="${escapeHtml(room.place_id || room.room_code || '')}" data-cse-room-name="${escapeHtml(room.display_name || room.room_full_name || room.room_name || '')}"><span>${escapeHtml(room.display_name || room.room_full_name || room.room_name || '')}</span><small>${escapeHtml([room.campus_name, room.building_name, room.seat_count ? `${room.seat_count} 座` : '', room.room_type_name].filter(Boolean).join(' · '))}</small></button>`).join('');
        const more = fr.hasMore ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass" data-cse-free-more${fr.loading ? ' disabled' : ''}>${fr.loading ? '查询中…' : '查看更多空闲教室'}</button>` : '';
        const error = fr.pageError ? `<div class="cse-materials__empty" role="status">${escapeHtml(fr.pageError)}</div>` : '';
        list.innerHTML = `${roomLine}${items ? `<div class="cse-free-rooms__grid">${items}</div><div class="cse-field__hint">已显示 ${fr.items.length} 间${fr.total ? `，共 ${fr.total} 间` : ''}，点击即选用。</div>` : '<div class="cse-materials__empty">该时段没有空闲教室。</div>'}${error}${more}`;
    }

    async function searchFreeRooms({ append = false } = {}) {
        const form = state.form;
        if (!form) return;
        const key = freeRoomSlotKey();
        if (state.freeRooms.slotKey === key && state.freeRooms.loading) return;
        const previous = state.freeRooms.slotKey === key && append ? state.freeRooms : null;
        const page = previous ? previous.page + 1 : 1;
        const requestId = ++freeRoomsRequestId;
        const isCurrent = () => requestId === freeRoomsRequestId && freeRoomSlotKey() === key;
        const original = resolveSelection(form.key)?.lesson;
        const roomId = form.room_id || '';
        const roomName = form.room_id ? form.room : (original?.classroom || '');
        state.freeRooms = { slotKey: key, items: [], status: '', roomStatus: '', message: '', page: 0, hasMore: false, ...previous, loading: true, pageError: '' };
        renderFreeRooms();
        try {
            const eff = effectiveSlot(form.week, form.weekday); // 调休上课日按被补那天查教室占用
            const params = new URLSearchParams({ year: term().year || '', term: term().term || '', week: String(eff.week), weekday: String(eff.weekday), sections: currentFormSections().join(','), room_id: roomId, room: roomName, page: String(page), page_size: '40' });
            const data = await api(`${API}/free-rooms?${params}`);
            if (!isCurrent()) return;
            const result = data.result || {};
            if (result.status !== 'success') throw new Error(result.message || '空闲教室查询失败，请稍后重试。');
            const received = Array.isArray(result.items) ? result.items : [];
            const items = [...(previous?.items || [])];
            for (const item of received) if (!items.some(known => String(known.place_id || known.room_code) === String(item.place_id || item.room_code))) items.push(item);
            state.freeRooms = { slotKey: key, items, status: 'success', roomStatus: result.room_status || 'unknown', loading: false, message: result.message || '', page,
                roomStatusMessage: result.room_status_message || '',
                total: Number(result.total_count) || items.length,
                hasMore: Boolean(result.has_more ?? (page < Number(result.total_page || 1))) && received.length > 0 };
            if (result.room_status && result.room_status !== 'unknown') {
                state.availability = { ...state.availability, data: null };
                await loadAvailability(form.key, { roomId, roomName: roomId ? roomName : '' });
            }
        } catch (error) {
            if (!isCurrent()) return;
            state.freeRooms = previous ? { ...previous, loading: false, pageError: error.message }
                : { slotKey: key, items: [], status: 'failed', roomStatus: 'unknown', loading: false, message: error.message, page: 0, hasMore: false };
        }
        if (isCurrent()) renderFreeRooms();
    }

    async function syncAvailability() {
        if (state.busy) return;
        setBusy(true);
        try {
            const data = await api(`${API}/availability/sync`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term }) });
            state.availability = { key: '', roomKey: '', data: null, loading: false };
            applyPayload(data);
            const result = data.result || {};
            if (result.status === 'missing_credential') LQ.toast(result.message, { tone: 'warning', duration: 8000, action: { label: '去设置教务账号', href: CREDENTIAL_URL } }).catch(() => {});
            else toast(result.message || '已同步。', result.status === 'success' ? 'success' : 'warning');
            if (state.selectedKey) { const sel = resolveSelection(state.selectedKey); if (sel) loadAvailability(sel.lesson.event_key); }
        } catch (error) { toast(error.message, 'danger'); }
        finally { setBusy(false); }
    }

    function renderMaterialsBlock(lesson) {
        if (!lesson.class_offering_id || !lesson.session_id) {
            return `<section class="cse-section"><div class="cse-section__title">上课材料</div><div class="cse-materials__empty">${lesson.class_offering_id ? '该课次尚未与平台课次精确关联，请先同步教务课表。' : '该课程尚未在平台开设课堂，无法绑定材料。'}${lesson.create_url ? ` <a href="${escapeHtml(lesson.create_url)}">去开设课堂</a>` : ''}</div></section>`;
        }
        const cacheKey = `${lesson.class_offering_id}:${lesson.session_id}`;
        const cache = state.materials;
        const manageUrl = `/classroom/${lesson.class_offering_id}?session_id=${lesson.session_id}`;
        let body = '<div class="cse-materials__empty">正在读取课次材料…</div>';
        if (cache.key === cacheKey && Array.isArray(cache.items)) {
            body = cache.items.length
                ? `<div class="cse-materials">${cache.items.map(item => `<div class="cse-materials__item"><span>${escapeHtml(item.name || '未命名材料')}</span>${item.open_url ? `<a href="${escapeHtml(item.open_url)}" target="_blank" rel="noopener">打开</a>` : ''}</div>`).join('')}</div>`
                : '<div class="cse-materials__empty">该课次还没有绑定材料。</div>';
        } else if (cache.key === cacheKey && cache.error) {
            body = `<div class="cse-materials__empty">${escapeHtml(cache.error)}</div>`;
        }
        return `<section class="cse-section" data-cse-materials><div class="cse-section__title">上课材料</div>${body}<div><a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" href="${escapeHtml(manageUrl)}">管理课次材料 →</a></div></section>`;
    }

    async function loadMaterials(lesson) {
        const cacheKey = `${lesson.class_offering_id}:${lesson.session_id}`;
        if (state.materials.key === cacheKey && (state.materials.items || state.materials.loading || state.materials.error)) return;
        state.materials = { key: cacheKey, items: null, loading: true };
        try {
            const data = await api(`/api/classrooms/${lesson.class_offering_id}/learning-materials?session_id=${lesson.session_id}&generate_blurbs=false`);
            state.materials = { key: cacheKey, items: data.entries || data.materials || data.items || [], loading: false };
        } catch (error) {
            state.materials = { key: cacheKey, items: null, loading: false, error: error.message || '课次材料读取失败' };
        }
        const section = refs.drawer?.querySelector('[data-cse-materials]');
        if (section && state.selectedKey) {
            const selection = resolveSelection(state.selectedKey);
            if (selection) section.outerHTML = renderMaterialsBlock(selection.lesson);
        }
    }

    /**
     * 教务冲突明细。联调（2026-09-27）确认 ctxxList 每项为大写键：CTLX 冲突类型（上课教师冲突 / 课表场地冲突 /
     * 课表冲突 / 上课冲突=学生）、MC 对象（教师名 / 教室 / 班级串 / 学生名）、JXBMC、KCMC、XQJ、JC、ZCD；学生行另有 XH、BJ。
     * 学生冲突可能上百行，按冲突类型分组汇总，同一类型只列前几个对象。
     */
    function conflictDetailsHtml(conflict) {
        const rows = Array.isArray(conflict?.details) ? conflict.details.filter(r => r && typeof r === 'object') : [];
        if (!rows.length) return '';
        const get = (row, key) => { const hit = Object.keys(row).find(k => k.toLowerCase() === key); return hit ? String(row[hit] ?? '').trim() : ''; };
        const groups = new Map();
        for (const row of rows) {
            const type = get(row, 'ctlx') || '冲突';
            const where = [get(row, 'zcd'), get(row, 'xqj') ? `周${DAY_NAMES[Number(get(row, 'xqj')) - 1] || get(row, 'xqj')}` : '', get(row, 'jc')].filter(Boolean).join(' ');
            const course = [get(row, 'kcmc'), get(row, 'jxbmc') && get(row, 'jxbmc') !== get(row, 'kcmc') ? get(row, 'jxbmc') : ''].filter(Boolean).join(' ');
            const isStudent = type.includes('上课冲突') || Boolean(get(row, 'xh'));
            const who = isStudent ? [get(row, 'mc') || get(row, 'xm'), get(row, 'bj')].filter(Boolean).join('·') : (get(row, 'mc') || get(row, 'xm') || get(row, 'cdmc'));
            const group = groups.get(type) || { type, isStudent, who: [], context: new Set() };
            if (who && !group.who.includes(who)) group.who.push(who);
            if (course || where) group.context.add([course, where].filter(Boolean).join(' · '));
            groups.set(type, group);
        }
        const lines = [...groups.values()].map(group => {
            const shown = group.who.slice(0, group.isStudent ? 6 : 4).map(escapeHtml).join('、');
            const more = group.who.length > (group.isStudent ? 6 : 4) ? ` 等 ${group.who.length} ${group.isStudent ? '人' : '项'}` : '';
            const context = [...group.context].slice(0, 2).map(escapeHtml).join('；');
            return `<li><b>${escapeHtml(group.type)}</b>${group.isStudent ? `（${group.who.length} 名学生）` : ''}：${shown}${more}${context ? `<small>${context}</small>` : ''}</li>`;
        });
        const total = Number(conflict.detail_count || rows.length);
        return `<div class="cse-draft__msg"><strong>教务冲突明细：</strong><ul class="cse-conflict-list">${lines.join('')}</ul>${total > rows.length ? `<span>教务共返回 ${total} 条，已按类型汇总</span>` : ''}</div>`;
    }

    /** 教务预检结论（保存本地草稿后自动运行 / 弹窗里再跑一次）。 */
    function precheckOf(draft) {
        const pre = draft?.availability?.zf_precheck;
        return pre && pre.status ? pre : null;
    }
    const PRECHECK_LABELS = { ok: '教务预检：无冲突', already: '教务预检：已有记录', conflict: '教务预检：有冲突（可强制保存）', hard: '教务预检：不能保存', failed: '教务预检失败' };

    let detailLayer = null, detailRevision = 0;
    const overflowLabels = refs.drafts ? bindOverflowLabels(refs.drafts) : null;
    function closeDraftDetail() {
        detailRevision++;
        detailLayer?.destroy(); detailLayer = null;
        overflowLabels?.refresh();
    }
    async function showDraftDetail(trigger) {
        const draft = draftById(trigger.dataset.cseDraftId);
        if (!draft) return;
        closeDraftDetail();
        const revision = detailRevision;
        const kind = trigger.dataset.cseDetail;
        const body = document.createElement('div'); body.className = 'cse-detail-body';
        const paragraph = text => { const p = document.createElement('p'); p.textContent = text; body.append(p); };
        const title = { reason: '调整原因', proofs: '证明材料', remote: '教务状态与详情' }[kind];
        if (!title) return;
        if (kind === 'reason') {
            paragraph(draft.reason || '尚未填写调整原因。请定位到课次，在属性中补充。');
            if (draft.note) paragraph(`备注：${draft.note}`);
        } else if (kind === 'proofs') {
            paragraph('材料保存在本平台；提交教务申请时，请在教务系统附上所需材料。');
            if (!draft.proofs?.length) paragraph('暂无证明材料。请定位到课次上传。');
            for (const proof of draft.proofs || []) {
                const a = LQ.button({ label: `${proof.filename || proof.name || '证明材料'} · ${formatBytes(proof.size)}`, variant: 'link', size: 'sm',
                    href: `${API}/drafts/${draft.id}/proofs/${encodeURIComponent(proof.id)}`, attrs: { target: '_blank', rel: 'noopener' } });
                body.append(a);
            }
        } else {
            paragraph(changeStatus(draft)[0]);
            if (draft.remote_ttk_id) paragraph(`教务申请：${draft.remote_ttk_id}；本条明细：${draft.remote_detail_id || '待核对'}`);
            if (draft.status === 'pushed') paragraph('此状态表示平台曾成功保存；当前提交或审批进度请在教务系统核对。平台不会代您提交申请。');
            if (draft.remote_label) paragraph(draft.remote_label);
            if (draft.remote_message) paragraph(draft.remote_message);
            const pre = precheckOf(draft);
            if (pre?.message && pre.message !== draft.remote_message) paragraph(pre.message);
            const conflict = document.createElement('div');
            conflict.innerHTML = conflictDetailsHtml(draft.remote_conflict?.details?.length ? draft.remote_conflict : pre);
            body.append(conflict);
            if (!draft.remote_message && !pre && draft.status !== 'pushed') paragraph('尚未完成教务预检，可使用“预检冲突”检查。');
        }
        try {
            const dialogs = await LQ.load('dialogs');
            if (revision !== detailRevision || !trigger.isConnected) return;
            trigger.setAttribute('aria-expanded', 'true');
            const reset = () => { trigger.setAttribute('aria-expanded', 'false'); if (revision === detailRevision) detailLayer = null; };
            detailLayer = dialogs.openDialog({ type: 'popover', title, body }, { anchor: trigger, trigger, owner: refs.drafts, onClose: reset, onDestroy: reset });
        } catch (error) { trigger.setAttribute('aria-expanded', 'false'); toast(error.message || '详情暂时无法打开。', 'danger'); }
    }
    window.addEventListener('pagehide', event => { closeDraftDetail(); if (!event.persisted) overflowLabels?.destroy(); });

    function renderDrafts() {
        if (!refs.drafts) return;
        closeDraftDetail();
        const list = drafts();
        const pushed = list.filter(d => d.status === 'pushed').length;
        const groups = groupChanges(list);
        const statusChip = (label, tone = 'neutral') => LQ.html.chip({ label, tone, size: 'sm' });
        const row = draft => `<article class="cse-draft lq-surface" data-lq-component="surface" data-cse-draft="${draft.id}" data-cse-draft-id="${draft.id}">
            <div class="cse-draft__title"><strong>${escapeHtml(draft.course_name)}</strong><span class="cse-draft__details" data-cse-details="${draft.id}"></span></div>
            <div class="cse-draft__status">${statusChip(...changeStatus(draft))}${draft.change_kind === 'room' ? statusChip('仅换教室') : ''}${draft.room_status === 'busy' ? statusChip('教室占用', 'warning') : draft.room_status === 'free' ? statusChip('教室空闲', 'success') : ''}${precheckOf(draft) && draft.status !== 'pushed' ? statusChip(({ ok: '无冲突', already: '已有记录', conflict: '有冲突', hard: '不能保存', failed: '预检失败' })[precheckOf(draft).status] || '待核对', ({ ok: 'success', already: 'warning', conflict: 'warning', hard: 'danger', failed: 'danger' })[precheckOf(draft).status] || 'neutral') : ''}</div>
            <div class="cse-draft__compare" data-cse-compare-slot="${draft.id}"></div>
            <div class="cse-draft__actions">
                <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" data-cse-locate="${draft.id}">定位</button>
                ${draft.status === 'pushed' ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--sm cse-btn--danger lq-btn--destructive" data-cse-withdraw="${draft.id}">从教务撤回</button>` : `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--sm cse-btn--danger lq-btn--destructive" data-cse-discard="${draft.id}">撤销</button>`}
                ${draft.status !== 'pushed' ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" data-cse-precheck="${draft.id}">${precheckOf(draft) ? '重新预检' : '预检冲突'}</button>` : ''}
                ${draft.status === 'conflict' && !draft.remote_conflict?.hard && precheckOf(draft)?.status !== 'hard' ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" data-cse-force="${draft.id}">冲突仍保存</button>` : ''}
            </div>
        </article>`;
        let applicationIndex = 0;
        const rows = groups.map((group, index) => {
            const label = group.remoteId ? `教务申请 ${++applicationIndex} · ${group.drafts.length} 项`
                : group.kind === 'unverified' ? '已保存 · 申请待核对' : `待保存 · ${group.drafts.length} 项`;
            return `<section class="cse-change-group" data-cse-change-group="${escapeHtml(group.key)}" aria-label="${escapeHtml(group.drafts[0].course_name)}变更组 ${index + 1}">
                <div class="cse-change-group__head">${statusChip(label)}<span>${escapeHtml(group.drafts[0].class_label || group.drafts[0].teaching_class_name || '')}</span>${group.drafts.length > 1 ? `<span>${group.remoteId ? '共用一份申请，各项可单独定位和撤回' : '同一教学班，保存时合并申请'}</span>` : ''}</div>${group.drafts.map(row).join('')}</section>`;
        }).join('');
        const applications = new Set(list.filter(d => d.status === 'pushed' && d.remote_ttk_id && d.remote_detail_id).map(d => d.remote_ttk_id)).size;
        const next = pushed ? `<div class="cse-drafts__next"><span>${pushed} 项已保存${applications ? ` · ${applications} 份教务申请` : ''}</span><a class="lq-btn lq-btn--link lq-btn--sm" data-lq-component="button" href="${escapeHtml(state.payload?.zf_entry_url || '#')}" target="_blank" rel="noopener">前往教务核对 / 提交 ↗</a></div>` : '';
        refs.drafts.classList.add('lq-surface'); refs.drafts.dataset.lqComponent = 'surface';
        refs.drafts.innerHTML = `<div class="cse-drafts__head"><h3>变更清单</h3><p>上原下新，红色为变更项。保存到教务后仍需自行提交申请。</p></div>
            ${list.length ? `<div class="cse-drafts__list">${rows}</div>` : '<div class="cse-materials__empty">还没有任何调整。按住课次拖到新的节次，或单击课次在右侧设置。</div>'}${next}`;
        for (const draft of list) {
            refs.drafts.querySelector(`[data-cse-compare-slot="${draft.id}"]`).append(createChangeComparison(draft));
            const details = refs.drafts.querySelector(`[data-cse-details="${draft.id}"]`);
            details.append(createDetailButton(draft, 'reason', `原因：${draft.reason || '待填写'}`),
                createDetailButton(draft, 'proofs', draft.proofs?.length ? `材料 ${draft.proofs.length} 份：${draft.proofs.map(p => p.filename || p.name || '证明材料').join('、')}` : '材料：待上传'),
                createDetailButton(draft, 'remote', '教务详情'));
        }
        overflowLabels?.refresh();
    }

    /* ------------------------------------------------------------------ 课次重排预览 */
    function describeSlot(slot) {
        if (!slot?.date) return '未排期';
        const d = new Date(`${slot.date}T00:00:00`);
        const weekday = Number.isNaN(d.getTime()) ? '' : `周${DAY_NAMES[(d.getDay() + 6) % 7]}`;
        return `${slot.week ? `第${slot.week}周 ` : ''}${shortDate(slot.date)} ${weekday} ${sectionText(slot.sections || [])}`.trim();
    }

    /** 课次重排预览（只读）：调课经教务审批同步后自动执行，这里只在保存到教务时告知结果。 */
    async function fetchResequencePreview(draftIds) {
        try {
            const params = new URLSearchParams({ year: term().year || '', term: term().term || '', draft_ids: (draftIds || []).join(',') });
            return await api(`${API}/resequence-preview?${params}`);
        } catch { return null; }
    }

    async function refreshHolidays() {
        if (state.busy) return;
        setBusy(true);
        try {
            const data = await api(`${API}/holidays/refresh`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term }) });
            applyPayload(data);
            const stored = Object.entries(data.result?.stored || {}).map(([y, n]) => `${y} 年 ${n} 条`).join('，');
            const failed = Object.keys(data.result?.failed || {});
            toast(stored ? `已更新全国节假日/调休：${stored}${failed.length ? `；${failed.join('、')} 年暂无数据` : ''}` : `未获取到数据${failed.length ? `（${failed.join('、')}）` : ''}`, stored ? 'success' : 'warning');
        } catch (error) { toast(error.message, 'danger'); }
        finally { setBusy(false); }
    }

    /* ------------------------------------------------------------------ actions */
    function setBusy(flag) { state.busy = flag; renderMeta(); renderTermSelect(); }

    async function loadTerm(year, termCode) {
        setBusy(true);
        try {
            const data = await api(`${API}?year=${encodeURIComponent(year)}&term=${encodeURIComponent(termCode)}`);
            applyPayload(data, { keepWeek: false, keepSelection: false });
            const url = new URL(window.location.href); url.searchParams.set('year', year); url.searchParams.set('term', termCode);
            window.history.replaceState(null, '', url);
        } catch (error) { toast(error.message, 'danger'); }
        finally { setBusy(false); }
    }

    async function saveDraft(body, { select = true } = {}) {
        if (state.busy) return null;
        setBusy(true);
        try {
            const data = await api(`${API}/drafts`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term, ...body }) });
            state.form = null;
            if (select && data.draft) state.selectedKey = data.draft.event_key;
            applyPayload(data);
            toast(`已记录：${data.draft.course_name} → ${data.draft.proposed_label}，正在请教务预检冲突…`, 'success');
            if (data.draft) void precheckDrafts([data.draft.id], { notify: 'summary' });
            return data.draft;
        } catch (error) { toast(error.message, 'danger'); return null; }
        finally { setBusy(false); }
    }

    /**
     * 提前预测：用教务自身的冲突检测试跑（不保存），结论落在草稿 availability.zf_precheck 上。
     * notify = 'full'（逐条提示）| 'summary'（只在有冲突时提示）| 'none'（弹窗内自行展示）。
     */
    async function precheckDrafts(draftIds, { notify = 'full' } = {}) {
        try {
            const data = await api(`${API}/push/check`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term, draft_ids: draftIds || [] }) });
            applyPayload(data);
            const result = data.result || {};
            const clean = result.status === 'success' && !result.conflicts && !result.hard && !result.failed;
            if (notify === 'full') {
                if (result.status === 'missing_credential') LQ.toast(result.message, { tone: 'warning', duration: 8000, action: { label: '去设置教务账号', href: CREDENTIAL_URL } }).catch(() => {});
                else toast(result.message || '预检完成。', clean ? 'success' : 'warning');
            } else if (notify === 'summary') {
                if (result.status === 'success' && !clean) toast(`教务预检：${result.message} 变更清单里有原因与建议。`, 'warning');
                else if (result.status === 'missing_credential') toast('未配置教务账号，暂时无法提前检测冲突。', 'warning');
            }
            return result;
        } catch (error) { if (notify !== 'none') toast(error.message, 'danger'); return null; }
    }

    async function discardDraft(id) {
        if (state.busy) return;
        const draft = draftById(id);
        if (!draft || draft.status === 'pushed') return;
        const ok = await LQ.confirm({ title: '撤销变更', message: `撤销「${draft.course_name}」调至 ${draft.proposed_label} 的本地变更？`, confirmLabel: '撤销', danger: true });
        if (!ok || state.busy) return;
        setBusy(true);
        try {
            const data = await api(`${API}/drafts/${id}?year=${encodeURIComponent(term().year)}&term=${encodeURIComponent(term().term)}`, { method: 'DELETE' });
            state.form = null;
            if (state.selectedKey === `draft:${id}`) state.selectedKey = draft.event_key;
            applyPayload(data);
            toast('已撤销该变更。', 'success');
        } catch (error) { toast(error.message, 'danger'); }
        finally { setBusy(false); }
    }

    function describePushResult(result) {
        const lines = (result.results || []).map(item => `${item.course_name}：${item.original_label} → ${item.proposed_label}｜${item.message || item.status}`);
        return [result.message, ...lines].filter(Boolean).join('\n');
    }

    async function pushDrafts({ draftIds = null, force = false } = {}) {
        if (state.busy) return;
        const targets = draftIds ? draftIds.map(draftById).filter(Boolean) : pendingDrafts();
        if (!targets.length) { toast('没有待检测/保存的变更。'); return; }
        if (!force) { await openPushDialog(targets); return; }
        const ok = await LQ.confirm({
            title: '按冲突调停课保存',
            message: `教务已检测到冲突，仍保存将标记为"冲突调停课"，教务审批时需要说明。${targets.length} 项变更写入教务草稿（待提交），不会替您提交申请。`,
            confirmLabel: '仍然保存',
        });
        if (!ok) return;
        const result = await runPush(targets.map(d => d.id), { force: true });
        if (result) toast(describePushResult(result), result.status === 'success' ? 'success' : 'warning');
    }

    /** 实际写入教务草稿；结果由调用方展示。 */
    async function runPush(ids, { force = false } = {}) {
        setBusy(true);
        try {
            const data = await api(`${API}/push`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term, draft_ids: ids, force, force_note: force ? '已与相关方沟通，按新安排上课' : '' }) });
            state.lastPush = data.result;
            applyPayload(data);
            if (refs.feedback) refs.feedback.textContent = data.result?.message || '';
            return data.result || {};
        } catch (error) { toast(error.message, 'danger'); return null; }
        finally { setBusy(false); }
    }

    function mergePushResults(first, second) {
        if (!first) return second;
        if (!second) return first;
        const pushed = (first.pushed || 0) + (second.pushed || 0);
        return { ...second, results: [...(first.results || []), ...(second.results || [])], pushed,
            conflicts: (first.conflicts || 0) + (second.conflicts || 0), failed: (first.failed || 0) + (second.failed || 0),
            message: `${first.message || ''} ${second.message || ''}`.trim(),
            status: first.status === 'success' && second.status === 'success' ? 'success' : (pushed ? 'partial' : 'failed') };
    }

    /**
     * 「检测冲突并保存」弹窗（降低期待、明确告知）：
     * 打开即用教务自身的冲突检测试跑每一项（不保存）→ 逐项给出结论与下一步建议 →
     * 保存没有冲突的（可选连同软冲突强制保存）→ 同一弹窗展示保存结果与后续操作。
     */
    async function openPushDialog(targets) {
        const dialogs = await LQ.load('dialogs');
        const ids = targets.map(d => d.id);
        const preview = await fetchResequencePreview(ids);
        const body = document.createElement('div'); body.className = 'cse-push';
        const footer = document.createElement('div'); footer.className = 'cse-push__foot';
        let phase = 'checking';            // checking → checked → saving → done
        let check = null;                  // result of push/check
        let pushResult = null;
        const VERDICT = {
            ok: { tone: 'ok', label: '无冲突，可保存' }, already: { tone: 'ok', label: '教务已有记录，保存时直接关联' },
            conflict: { tone: 'room', label: '有冲突（可强制保存）' }, hard: { tone: 'conflict', label: '不能保存' }, failed: { tone: 'muted', label: '检测失败' },
        };
        const NEXT_STEP = {
            conflict: '建议：换一个时段或教室后再试；若已与相关方沟通，也可「连同冲突一起保存」，教务审批时会标记为冲突调停课。',
            hard: '教务里该原课次已有调课申请或已补课，不允许再申请：请先在教务撤回旧申请，或在这里撤销这条变更。',
            failed: '教务未能识别该课次，多半是本地课表过期：先点「同步教务课表」再重新检测。',
        };
        const current = () => targets.map(d => draftById(d.id) || d);
        const verdictOf = draft => check?.results?.find(r => r.draft_id === draft.id) || precheckOf(draft) || null;
        const okIds = () => current().filter(d => ['ok', 'already'].includes(verdictOf(d)?.status)).map(d => d.id);
        const softIds = () => current().filter(d => verdictOf(d)?.status === 'conflict').map(d => d.id);
        const unchecked = () => !check || check.status !== 'success';
        const pushedItem = draft => pushResult?.results?.find(r => r.draft_id === draft.id);
        const itemBadge = (draft, pushed, v) => {
            if (pushed) return `<span class="cse-tag cse-tag--${pushed.status === 'pushed' ? 'ok' : pushed.status === 'conflict' ? 'room' : 'conflict'}">${pushed.status === 'pushed' ? '已保存到教务草稿' : pushed.status === 'conflict' ? '教务冲突，未保存' : '保存失败'}</span>`;
            const meta = v ? VERDICT[v.status] || VERDICT.failed : null;
            if (meta) return `<span class="cse-tag cse-tag--${meta.tone}">${meta.label}</span>`;
            return phase === 'checking' ? '<span class="cse-tag cse-tag--muted">检测中…</span>' : '<span class="cse-tag cse-tag--muted">未检测</span>';
        };
        const nextStepFor = (pushed, v) => {
            if (pushed) return pushed.status === 'pushed' ? '' : NEXT_STEP[pushed.status === 'conflict' ? (pushed.conflict?.hard ? 'hard' : 'conflict') : 'failed'];
            return v ? NEXT_STEP[v.status] || '' : '';
        };
        const renderItem = draft => {
            const v = verdictOf(draft); const pushed = pushedItem(draft);
            const detail = pushed?.message || v?.message || '';
            const next = nextStepFor(pushed, v);
            return `<li class="cse-push__item" data-cse-push-item="${draft.id}"><div class="cse-push__head"><b>${escapeHtml(draft.course_name)}</b>${itemBadge(draft, pushed, v)}</div><span>${escapeHtml(draft.original_label)} → ${escapeHtml(draft.proposed_label)}</span><small>${draft.reason ? `原因：${escapeHtml(draft.reason)}` : '<em>未填写原因</em>'} · ${(draft.proofs || []).length ? `证明材料 ${draft.proofs.length} 份` : '<em>无证明材料</em>'}</small>${detail ? `<div class="cse-push__detail">${escapeHtml(detail)}</div>` : ''}${conflictDetailsHtml(pushed?.conflict || v?.conflict || v)}${next ? `<div class="cse-push__next">${escapeHtml(next)}</div>` : ''}</li>`;
        };
        const renderCheckNote = () => {
            if (!check) return '';
            if (check.status === 'missing_credential') return `<div class="cse-push__block cse-status cse-status--conflict"><strong>未配置教务账号，无法提前检测</strong><span>${escapeHtml(check.message || '')}</span><a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" href="${CREDENTIAL_URL}">去设置教务账号</a></div>`;
            if (check.status !== 'success') return `<div class="cse-push__block cse-status cse-status--conflict"><strong>检测未完成</strong><span>${escapeHtml(check.message || '')} 可重试，或直接尝试保存（保存时教务仍会逐项检测，有冲突的不会写入）。</span><button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" data-cse-push-recheck>重试检测</button></div>`;
            const clean = !check.conflicts && !check.hard && !check.failed;
            return `<div class="cse-push__block cse-status ${clean ? 'cse-status--pushed' : 'cse-status--conflict'}"><strong>教务预检结果</strong><span>${escapeHtml(check.message || '')}</span><button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" data-cse-push-recheck>重新检测</button></div>`;
        };
        const renderDoneNote = () => {
            if (!pushResult) return '';
            const title = pushResult.status === 'success' ? '已保存到教务草稿' : pushResult.status === 'partial' ? '部分已保存，其余需处理' : pushResult.status === 'missing_credential' ? '未配置教务账号' : '未能保存';
            const proofCount = current().reduce((n, d) => n + ((d.proofs || []).length), 0);
            const steps = pushResult.pushed ? `<div class="cse-push__block"><strong>下一步</strong><span>1) 登录教务系统 → 调停课申请 → 核对「待提交」并点击「提交申请」；2) 提交时附上证明材料${proofCount ? '（变更清单可下载已上传的材料）' : '（本批尚未上传）'}；3) 审批通过并同步后，剩余课次自动重排，材料随序号不变。</span><a data-lq-component="button" class="lq-btn lq-btn--sm cse-btn cse-btn--sm cse-btn--primary lq-btn--prominent" href="${escapeHtml(state.payload?.zf_entry_url || '#')}" target="_blank" rel="noopener">打开教务调停课申请 ↗</a></div>` : '';
            const fix = pushResult.status === 'missing_credential' ? `<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm" href="${CREDENTIAL_URL}">去设置教务账号</a>` : '';
            return `<div class="cse-push__block cse-status ${pushResult.status === 'success' ? 'cse-status--pushed' : 'cse-status--conflict'}"><strong>${title}</strong><span>${escapeHtml(pushResult.message || '')}</span>${fix}</div>${steps}`;
        };
        const renderBody = () => {
            const list = current();
            const done = phase === 'done';
            const missingReason = list.filter(d => !String(d.reason || '').trim() && d.status !== 'pushed');
            const missingProof = list.filter(d => !(d.proofs || []).length);
            const plans = (preview?.plans || []).filter(p => p.changes?.length);
            const planHtml = plans.length ? `<div class="cse-push__block"><strong>审批通过并同步后的课次重排</strong><ul class="cse-reseq__list">${plans.map(p => `<li><b>${escapeHtml(p.course_label || '')}</b>：${p.change_count} 次课按真实上课时间调整顺序${p.changes.slice(0, 3).map(c => `<br><small>原第 ${c.old_order_index ?? c.order_index} 次 → 新第 ${c.order_index} 次 · ${escapeHtml(describeSlot(c.new))}</small>`).join('')}${p.changes.length > 3 ? `<br><small>… 共 ${p.changes.length} 次</small>` : ''}</li>`).join('')}</ul><p class="cse-field__hint">课次身份和真实上课时段不互换；教学材料按教学序号整体迁移，作业与出勤仍归原课次。</p></div>` : '';
            body.dataset.phase = phase;
            body.innerHTML = `<p class="cse-push__lead">${done ? '结果如下，有问题的项已给出建议。' : `${list.length} 项变更会先交给教务做冲突检测（不保存）；确认没有冲突的才写入教务的调停课申请草稿（待提交），平台不会替您提交申请。`}</p>
                ${done ? renderDoneNote() : renderCheckNote()}
                <ul class="cse-push__list">${list.map(renderItem).join('')}</ul>
                ${done || !missingReason.length ? '' : `<div class="cse-push__block cse-status cse-status--conflict"><strong>${missingReason.length} 项未填写调课原因</strong><button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn cse-btn--sm cse-btn--ai" data-cse-push-ai-all>AI 填写全部</button></div>`}
                ${done ? '' : `<div class="cse-push__block"><strong>证明材料</strong><span class="cse-section__hint">放假通知、会议通知等，教务提交申请时需附上。${missingProof.length ? `当前 ${missingProof.length} 项还没有材料。` : '全部已有材料。'}</span><label class="cse-btn cse-btn--sm cse-upload${state.proofsBusy ? ' is-busy' : ''}"><input data-lq-component="file" class="lq-native-file" type="file" data-cse-push-proofs multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx,.txt" hidden>${state.proofsBusy ? '上传中…' : '为这些变更上传证明材料'}</label></div>`}
                ${done ? '' : planHtml}`;
            const busy = phase === 'checking' || phase === 'saving';
            const blocked = check && (check.status === 'missing_credential' || check.status === 'nothing');
            const confirmLabel = phase === 'checking' ? '正在检测…' : phase === 'saving' ? '正在保存…' : unchecked() ? '直接尝试保存' : `保存无冲突的 ${okIds().length} 项`;
            footer.innerHTML = done
                ? '<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--primary lq-btn--prominent" data-cse-push-cancel>完成</button>'
                : `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass cse-btn" data-cse-push-cancel>取消</button>
                   ${softIds().length ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--danger lq-btn--destructive" data-cse-push-force${busy ? ' disabled' : ''}>连同 ${softIds().length} 项冲突一起保存</button>` : ''}
                   <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm cse-btn cse-btn--primary lq-btn--prominent" data-cse-push-confirm${busy || blocked || (!unchecked() && !okIds().length) ? ' disabled' : ''} title="${unchecked() ? '预检未完成；保存时教务仍会逐项检测冲突，有冲突的不会写入' : ''}">${confirmLabel}</button>`;
        };
        renderBody();
        const root = dialogs.createDialog({ title: '检测冲突并保存到教务', body, footer, size: 'lg', attrs: { 'data-cse-push-dialog': '' } });
        return new Promise(resolve => {
            let decided = false;
            const finish = value => { if (!decided) { decided = true; resolve(value); } };
            const handle = dialogs.openDialog(root, { onClose: () => finish(pushResult) });
            const close = async () => { await LQ.layer.close(handle, 'button'); };
            const runCheck = async () => {
                phase = 'checking'; renderBody();
                check = (await precheckDrafts(ids, { notify: 'none' })) || { status: 'failed', message: '预检请求失败，可直接尝试保存或稍后重试。' };
                phase = 'checked'; renderBody();
            };
            const save = async (force) => {
                phase = 'saving'; renderBody();
                let result = null;
                if (unchecked()) result = await runPush(ids);
                else {
                    const ok = okIds(); const soft = softIds();
                    if (ok.length) result = await runPush(ok);
                    if (force && soft.length) result = mergePushResults(result, await runPush(soft, { force: true }));
                }
                pushResult = result || { status: 'failed', message: '保存请求失败，请稍后重试。', results: [] };
                phase = 'done'; renderBody();
                if (pushResult.status === 'missing_credential') LQ.toast(pushResult.message, { tone: 'warning', duration: 8000, action: { label: '去设置教务账号', href: CREDENTIAL_URL } }).catch(() => {});
                else toast(pushResult.message || '', pushResult.status === 'success' ? 'success' : pushResult.status === 'partial' ? 'warning' : 'danger');
            };
            root.addEventListener('click', async event => {
                if (event.target.closest('[data-cse-push-cancel]')) { await close(); return; }
                if (event.target.closest('[data-cse-push-recheck]')) { await runCheck(); return; }
                if (event.target.closest('[data-cse-push-confirm]')) { await save(false); return; }
                if (event.target.closest('[data-cse-push-force]')) {
                    const ok = await LQ.confirm({ title: '连同冲突一起保存', message: '有冲突的项会以「冲突调停课」保存到教务草稿，教务审批时需要说明；确定继续？', confirmLabel: '仍然保存' });
                    if (ok) await save(true);
                    return;
                }
                const aiAll = event.target.closest('[data-cse-push-ai-all]');
                if (aiAll) {
                    aiAll.disabled = true; aiAll.textContent = 'AI 填写中…';
                    for (const draft of current().filter(d => !String(d.reason || '').trim())) {
                        try {
                            const data = await api(`${API}/reason-suggest`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term, event_key: draft.event_key, week: draft.proposed.week, weekday: draft.proposed.weekday, sections: draft.proposed.sections, room: draft.proposed.room }) });
                            const saved = await api(`${API}/drafts`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term, event_key: draft.event_key, week: draft.proposed.week, weekday: draft.proposed.weekday, sections: draft.proposed.sections, room: draft.proposed.room, room_id: draft.proposed.room_id, reason: data.reason }) });
                            state.payload = saved;
                        } catch (error) { toast(error.message, 'danger'); }
                    }
                    renderBody(); renderAll();
                }
            });
            root.addEventListener('change', async event => {
                const input = event.target.closest('[data-cse-push-proofs]');
                if (!input || !input.files?.length) return;
                const files = [...input.files];
                state.proofsBusy = true; renderBody();
                for (const draft of current()) await uploadProofs(draft.id, files);
                state.proofsBusy = false; renderBody();
            });
            void runCheck();
        });
    }

    async function withdrawDraft(id) {
        if (state.busy) return;
        const draft = draftById(id);
        if (!draft || draft.status !== 'pushed') return;
        const ok = await LQ.confirm({ title: '从教务撤回', message: `从教务草稿中删除「${draft.course_name}」的这一条调整？同一申请中的其他调整会保留。撤回成功后可继续修改并再次保存；已提交的申请须先在教务系统处理。`, confirmLabel: '撤回', danger: true });
        if (!ok || state.busy) return;
        setBusy(true);
        try {
            const data = await api(`${API}/drafts/${id}/withdraw`, { method: 'POST', body: '{}' });
            state.form = null;
            applyPayload(data);
            toast(data.result?.message || '已撤回。', data.result?.status === 'success' ? 'success' : 'danger');
        } catch (error) { toast(error.message, 'danger'); }
        finally { setBusy(false); }
    }

    async function syncTerm() {
        if (state.busy) return;
        setBusy(true);
        try {
            await syncAcademicSchedule({ year: term().year, term: term().term });
            const data = await api(`${API}?year=${encodeURIComponent(term().year)}&term=${encodeURIComponent(term().term)}`);
            applyPayload(data);
            toast('教务课表已同步。', 'success');
        } catch (error) { toast(error.message || '教务同步未完成。', 'danger'); }
        finally { setBusy(false); }
    }

    function locateDraft(id) {
        const draft = draftById(id);
        if (!draft) return;
        state.activeWeek = Number(draft.proposed.week);
        state.selectedKey = draft.event_key;
        state.form = null;
        renderAll();
        refs.stageBody?.querySelector(`[data-cse-lesson="draft:${draft.id}"]`)?.scrollIntoView({ block: 'nearest' });
    }

    function selectLesson(key) {
        const selection = resolveSelection(key);
        if (selection?.lesson.counts_towards_total === false && !selection.lesson.edit_ghost) {
            toast(`${scheduleChangeLabel(selection.lesson) || '计划位置'}仅供对照，请在原正式课次上编辑。`, 'info');
            return;
        }
        state.selectedKey = selection ? selection.lesson.event_key : '';
        state.form = null;
        renderStage(); renderDrawer(); renderLegend(); renderWeekRail();
        if (selection) loadAvailability(selection.lesson.event_key);
    }

    /* ------------------------------------------------------------------ drag & drop */
    function cellAt(x, y) {
        const element = document.elementFromPoint(x, y);
        return { cell: element?.closest?.('[data-cse-cell]') || null, weekButton: element?.closest?.('[data-cse-week]') || null };
    }

    function dropTarget(cell) {
        if (!cell || !state.drag) return null;
        const weekday = Number(cell.dataset.weekday);
        const start = Number(cell.dataset.section);
        const { min_section: minSection, max_section: maxSection } = rules();
        const span = state.drag.span;
        const end = start + span - 1;
        const sections = Array.from({ length: span }, (_, i) => start + i);
        const day = dayState(state.activeWeek, weekday);
        let valid = start >= minSection && end <= maxSection;
        let reason = valid ? '' : (start < minSection ? '第 1 节为早读，不可放置' : `超出第 ${maxSection} 节`);
        if (valid && day.kind === 'holiday') { valid = false; reason = `节假日（${day.info.label || '放假'}）不可放置`; }
        if (valid && day.kind === 'past') { valid = false; reason = '已过去的日期不可放置'; }
        if (valid && day.kind === 'ended') { valid = false; reason = '学期已结束，不可放置'; }
        if (valid && !pairStarts().includes(start)) { valid = false; reason = '课次以两小节为单位：只能从第 2、4、6、8、10 节开始'; }
        if (valid) {
            const week = activeWeekData();
            const clash = [...(week?.lessons || []), ...mirrorLessons(week)].find(lesson => {
                if (lesson.event_key === state.drag.sourceKey || lesson.event_key === state.drag.ghostKey || lesson.source_event_key === state.drag.sourceKey) return false;
                if (lesson.edit_draft) return false;                       // moving away
                if (lesson.counts_towards_total === false && !lesson.edit_ghost) return false; // pending proposals do not occupy
                return Number(lesson.weekday) === weekday && (lesson.sections || []).some(s => sections.includes(s));
            });
            if (clash) { valid = false; reason = `与「${clash.course_name}」${clash.edit_mirror ? '（调休当天课）' : ''}重叠`; }
        }
        let roomBusy = false;
        if (valid) {
            const eff = effectiveSlot(state.activeWeek, weekday);
            const verdict = slotVerdict(availabilityData(state.drag.sourceKey), eff.week, eff.weekday, sections);
            if (verdict.level === 'block') { valid = false; reason = verdict.reasons[0] || '学生有课'; }
            else if (verdict.level === 'room') { roomBusy = true; reason = '学生有空但教室已占用，放置后请选择空闲教室'; }
        }
        return { weekday, start, sections, valid, reason, roomBusy, workday: day.kind === 'workday' ? day.info : null };
    }

    function paintTarget(target) {
        refs.stageBody?.querySelectorAll('.is-drop-ok, .is-drop-bad, .is-drop-warn').forEach(node => node.classList.remove('is-drop-ok', 'is-drop-bad', 'is-drop-warn'));
        if (!target) return;
        for (const section of target.sections) {
            refs.stageBody?.querySelector(`[data-cse-cell][data-weekday="${target.weekday}"][data-section="${section}"]`)?.classList.add(!target.valid ? 'is-drop-bad' : target.roomBusy ? 'is-drop-warn' : 'is-drop-ok');
        }
    }

    function startDrag(pointer, card, selection) {
        const { lesson } = selection;
        const ghost = document.createElement('div');
        ghost.className = 'cse-drag-ghost';
        ghost.innerHTML = `${escapeHtml(lesson.course_name)}<small>${escapeHtml(sectionText(lesson.sections))} · ${escapeHtml(compactClassroomName(lesson.classroom || ''))}</small>`;
        document.body.appendChild(ghost);
        state.drag = {
            pointerId: pointer.pointerId, sourceKey: lesson.event_key, ghostKey: selection.ghostKey, span: (lesson.sections || []).length,
            ghost, target: null, weekTimer: null, hoverWeek: null, fromWeek: state.activeWeek,
        };
        document.body.classList.add('cse-dragging');
        card.classList.add('is-drag-source');
        moveGhost(pointer);
        loadAvailability(lesson.event_key);
    }

    function moveGhost(pointer) {
        const { ghost } = state.drag;
        ghost.style.left = `${pointer.clientX}px`; ghost.style.top = `${pointer.clientY}px`;
    }

    function onPointerMove(event) {
        const drag = state.drag;
        if (!drag || event.pointerId !== drag.pointerId) return;
        event.preventDefault();
        moveGhost(event);
        const { cell, weekButton } = cellAt(event.clientX, event.clientY);
        const hoverWeek = weekButton ? Number(weekButton.dataset.cseWeek) : null;
        if (hoverWeek !== drag.hoverWeek) {
            window.clearTimeout(drag.weekTimer);
            refs.weeks?.querySelectorAll('.is-drop-hover').forEach(node => node.classList.remove('is-drop-hover'));
            drag.hoverWeek = hoverWeek;
            if (hoverWeek && hoverWeek !== state.activeWeek && !weekButton.dataset.locked) {
                weekButton.classList.add('is-drop-hover');
                drag.weekTimer = window.setTimeout(() => {
                    if (!state.drag || state.drag.hoverWeek !== hoverWeek) return;
                    state.activeWeek = hoverWeek; renderWeekRail(); renderStage();
                    weekButton.classList.remove('is-drop-hover');
                    toast(`已摊开${activeWeekData()?.label || ''}，继续拖到目标节次即可跨周调整。`);
                }, WEEK_HOVER_DELAY);
            }
        }
        const target = cell && !cell.dataset.locked ? dropTarget(cell) : (cell ? { ...dropTarget(cell), valid: false, reason: LOCK_REASONS[cell.dataset.locked] || '不可放置' } : null);
        drag.target = target;
        paintTarget(target);
        if (refs.feedback) refs.feedback.textContent = target ? (target.valid ? `放置到 ${activeWeekData()?.label || ''} 周${DAY_NAMES[target.weekday - 1]} ${sectionText(target.sections)}${target.workday ? `（调休上课日，按${target.workday.makeup_for_weekday || ''}课表）` : ''}${target.roomBusy ? ` · ${target.reason}` : ''}` : target.reason) : '拖到节次格子放置；拖到左侧周次可跨周调整';
    }

    async function onPointerUp(event) {
        const drag = state.drag;
        if (!drag || event.pointerId !== drag.pointerId) return;
        endDrag();
        const target = drag.target;
        if (refs.feedback) refs.feedback.textContent = '';
        if (!target) return;
        if (!target.valid) { toast(target.reason || '该位置不可放置。', 'warning'); return; }
        const saved = await saveDraft({ event_key: drag.sourceKey, week: state.activeWeek, weekday: target.weekday, start_section: target.start });
        if (saved && (target.roomBusy || saved.room_status === 'busy')) {
            toast('该时段学生有空，但原教室已被占用；已为你查询该时段的空闲教室。', 'warning');
            await searchFreeRooms();
        }
    }

    function endDrag() {
        const drag = state.drag;
        if (!drag) return;
        window.clearTimeout(drag.weekTimer);
        drag.ghost.remove();
        document.body.classList.remove('cse-dragging');
        refs.stageBody?.querySelectorAll('.is-drag-source').forEach(node => node.classList.remove('is-drag-source'));
        refs.weeks?.querySelectorAll('.is-drop-hover').forEach(node => node.classList.remove('is-drop-hover'));
        paintTarget(null);
        state.drag = null;
    }

    let press = null;
    function onStagePointerDown(event) {
        if (event.button > 0 || state.busy) return;
        const card = event.target.closest('[data-cse-lesson]');
        if (!card) return;
        if (card.dataset.mirror) { // 调休当天镜像：跳到被补那天的原课次
            const sourceKey = card.dataset.cseLesson.replace(/^mirror:/, '').replace(/:\d{4}-\d{2}-\d{2}$/, '');
            const source = findLesson(sourceKey);
            if (source) { state.activeWeek = Number(source.week.week_index); renderWeekRail(); selectLesson(sourceKey); }
            return;
        }
        const selection = resolveSelection(card.dataset.cseLesson);
        if (!selection) return;
        if (selection.lesson.counts_towards_total === false && !selection.lesson.edit_ghost) return; // pending 拟安排
        const past = Boolean(card.dataset.past);
        press = { card, key: card.dataset.cseLesson, x: event.clientX, y: event.clientY, pointerId: event.pointerId, selection,
            locked: selection.draft?.status === 'pushed' || past, lockReason: past ? '该课次已上过，不能再调整。' : '该变更已保存到教务草稿，请先「从教务撤回」再拖动。' };
    }

    function onDocumentPointerMove(event) {
        if (state.drag) { onPointerMove(event); return; }
        if (!press || event.pointerId !== press.pointerId || press.locked) return;
        if (Math.abs(event.clientX - press.x) + Math.abs(event.clientY - press.y) < DRAG_THRESHOLD) return;
        const current = press; press = null;
        if (!state.payload?.editable) return;
        startDrag(event, current.card, current.selection);
        onPointerMove(event);
    }

    function onDocumentPointerUp(event) {
        if (state.drag) { onPointerUp(event); return; }
        if (!press || event.pointerId !== press.pointerId) return;
        const current = press; press = null;
        if (current.locked) toast(current.lockReason, 'warning');
        selectLesson(current.key);
    }

    /* ------------------------------------------------------------------ events */
    refs.termSelect?.addEventListener('change', () => {
        const [year, termCode] = String(refs.termSelect.value || '').split('|');
        if (year && termCode) loadTerm(year, termCode);
    });
    refs.pushBtn?.addEventListener('click', () => pushDrafts());
    refs.syncBtn?.addEventListener('click', syncTerm);
    refs.availSync?.addEventListener('click', syncAvailability);
    refs.holidayRefresh?.addEventListener('click', refreshHolidays);
    // 调休连线跟随布局：窗口尺寸、周列表滚动、抽屉开合都重画
    window.addEventListener('resize', scheduleSwapLines, { passive: true });
    refs.weeks?.addEventListener('scroll', scheduleSwapLines, { passive: true });
    if (typeof ResizeObserver === 'function' && refs.layout) new ResizeObserver(scheduleSwapLines).observe(refs.layout);
    refs.layout?.addEventListener('transitionend', scheduleSwapLines);
    refs.weeks?.addEventListener('click', event => {
        const button = event.target.closest('[data-cse-week]');
        if (!button) return;
        state.activeWeek = Number(button.dataset.cseWeek);
        renderWeekRail(); renderStage();
    });
    refs.weeks?.addEventListener('keydown', event => {
        if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        const list = weeks();
        const index = list.findIndex(week => Number(week.week_index) === state.activeWeek);
        const next = list[Math.min(list.length - 1, Math.max(0, index + (event.key === 'ArrowDown' ? 1 : -1)))];
        if (next) { state.activeWeek = Number(next.week_index); renderWeekRail(); renderStage(); refs.weeks.querySelector('.cse-week.is-active')?.focus(); }
    });
    refs.stageBody?.addEventListener('pointerdown', onStagePointerDown);
    refs.stageBody?.addEventListener('click', event => {
        const jump = event.target.closest('[data-cse-request-jump]');
        if (!jump) return;
        const target = findLesson(jump.dataset.cseRequestJump);
        if (!target) return;
        state.activeWeek = Number(target.week.week_index);
        state.selectedKey = ''; state.form = null;
        renderWeekRail(); renderStage(); renderDrawer(); renderLegend();
        const card = [...refs.stageBody.querySelectorAll('[data-cse-lesson]')].find(node => node.dataset.cseLesson === target.lesson.event_key);
        card?.focus({ preventScroll: true }); card?.scrollIntoView({ block: 'nearest' });
    });
    refs.stageBody?.addEventListener('keydown', event => {
        const card = event.target.closest('[data-cse-lesson]');
        if (card && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectLesson(card.dataset.cseLesson); }
    });
    document.addEventListener('pointermove', onDocumentPointerMove, { passive: false });
    document.addEventListener('pointerup', onDocumentPointerUp);
    document.addEventListener('pointercancel', () => { press = null; endDrag(); });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && state.drag) { endDrag(); return; }
        if (event.key === 'Escape' && state.selectedKey && !event.defaultPrevented && !detailLayer && !document.querySelector('.lq-dialog[open], dialog[open], .lq-dialog-root:not([hidden])')) { state.selectedKey = ''; state.form = null; renderStage(); renderDrawer(); }
    });
    refs.drawer?.addEventListener('click', async event => {
        const close = event.target.closest('[data-cse-close]');
        if (close) { state.selectedKey = ''; state.form = null; renderStage(); renderDrawer(); renderLegend(); renderWeekRail(); return; }
        const roomItem = event.target.closest('[data-cse-room]');
        if (roomItem && state.form) {
            state.form = { ...state.form, room_id: roomItem.dataset.cseRoom, room: roomItem.dataset.cseRoomName };
            state.availability = { ...state.availability, data: null };
            renderDrawer(); return;
        }
        const freeRooms = event.target.closest('[data-cse-free-rooms]');
        if (freeRooms) { await searchFreeRooms(); return; }
        if (event.target.closest('[data-cse-free-more]')) { await searchFreeRooms({ append: true }); return; }
        if (event.target.closest('[data-cse-reason-ai]')) { await fillReasonByAi(); return; }
        const proofDel = event.target.closest('[data-cse-proof-del]');
        if (proofDel && state.form) {
            const draft = resolveSelection(state.form.key)?.draft;
            if (draft) await removeProof(draft.id, proofDel.dataset.cseProofDel);
            return;
        }
        const save = event.target.closest('[data-cse-save]');
        if (save && state.form) {
            const form = state.form;
            await saveDraft({ event_key: form.key, week: form.week, weekday: form.weekday, start_section: form.start, room: form.room, room_id: form.room_id, reason: form.reason });
            return;
        }
        const discard = event.target.closest('[data-cse-discard]');
        if (discard) { await discardDraft(discard.dataset.cseDiscard); return; }
        const withdraw = event.target.closest('[data-cse-withdraw]');
        if (withdraw) await withdrawDraft(withdraw.dataset.cseWithdraw);
    });
    refs.drawer?.addEventListener('input', event => {
        const field = event.target.closest('[data-cse-field]');
        if (!field || !state.form) return;
        const name = field.dataset.cseField;
        if (name === 'room') {
            const option = field.selectedOptions?.[0];
            state.form = { ...state.form, room_id: String(field.value || ''), room: field.value ? String(option?.textContent || '') : '' };
            state.availability = { ...state.availability, data: null };
            renderDrawer(); return;
        }
        if (name === 'reason') { state.form = { ...state.form, reason: field.value }; return; }
        state.form = { ...state.form, [name]: Number(field.value) };
        if (name === 'week') { renderDrawer(); return; } // 星期选项的日期/节假日提示随周次变化
        renderDrawerVerdict(); renderFreeRooms();
    });
    refs.drawer?.addEventListener('change', async event => {
        const input = event.target.closest('[data-cse-proof-input]');
        if (!input || !input.files?.length || !state.form) return;
        const draft = resolveSelection(state.form.key)?.draft;
        if (draft) await uploadProofs(draft.id, [...input.files]);
    });
    refs.drafts?.addEventListener('click', async event => {
        const detail = event.target.closest('[data-cse-detail]');
        if (detail) { await showDraftDetail(detail); return; }
        if (state.busy) return;
        const locate = event.target.closest('[data-cse-locate]');
        if (locate) { locateDraft(locate.dataset.cseLocate); return; }
        const discard = event.target.closest('[data-cse-discard]');
        if (discard) { await discardDraft(discard.dataset.cseDiscard); return; }
        const withdraw = event.target.closest('[data-cse-withdraw]');
        if (withdraw) { await withdrawDraft(withdraw.dataset.cseWithdraw); return; }
        const force = event.target.closest('[data-cse-force]');
        if (force) { await pushDrafts({ draftIds: [Number(force.dataset.cseForce)], force: true }); return; }
        const precheck = event.target.closest('[data-cse-precheck]');
        if (precheck) {
            setBusy(true); precheck.disabled = true; precheck.textContent = '检测中…';
            try { await precheckDrafts([Number(precheck.dataset.csePrecheck)]); }
            finally { setBusy(false); renderDrafts(); }
        }
    });

    applyPayload(boot, { keepWeek: false, keepSelection: false });
    if (refs.termSelect) bindDropdown(refs.termSelect, { placeholder: '选择学年学期' });
}
