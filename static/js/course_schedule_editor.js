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
import { compactClassroomName } from './course_schedule_presentation.js?v=schedule-glass-20260920';
import { syncAcademicSchedule } from '/static/js/academic_schedule_sync.js?v=academic-sync-20260919';
import { getLQ } from './lq/index.js';

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
        freeRooms: { slotKey: '', items: [], status: '', roomStatus: '', loading: false, message: '' },
        resequence: { loading: false, error: '', preview: null, applied: null },
    };
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
        resequence: root.querySelector('[data-cse-resequence]'),
        holidayRefresh: root.querySelector('[data-cse-holiday-refresh]'),
    };

    /* ------------------------------------------------------------------ data helpers */
    const overview = () => state.payload?.overview || {};
    const weeks = () => overview().weeks || [];
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
    function dayState(week, weekday) {
        const iso = dateOf(week, weekday);
        const info = iso ? calendarDay(iso) : null;
        if (info?.kind === 'holiday') return { kind: 'holiday', iso, info };
        if (isPastDate(iso)) return { kind: 'past', iso, info };
        if (info?.kind === 'workday' && info.makeup_for_date) return { kind: 'workday', iso, info };
        return { kind: '', iso, info };
    }

    /** (week, weekday) whose timetable is followed on that day — 调休上课日走被补那天的课表。 */
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
                if (Number(lesson.weekday) !== Number(info.makeup_weekday) || lesson.edit_ghost || lesson.edit_draft) continue;
                out.push({ ...lesson, weekday, edit_mirror: true, mirror_label: `调休 · 按${info.makeup_for_weekday || ''}课表`, actual_date: day.iso,
                    event_key: `mirror:${lesson.event_key}:${day.iso}`, source_event_key: lesson.event_key, edit_ghost: false, edit_draft: null, adjustment: null, counts_towards_total: true });
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
        return { level, reasons };
    }

    function freeStartCount(data, week, span) {
        if (!data) return null;
        const { max_section: maxSection } = rules();
        let count = 0;
        for (let weekday = 1; weekday <= 7; weekday += 1) {
            const day = dayState(week, weekday);
            if (day.kind === 'holiday' || day.kind === 'past') continue;      // 节假日 / 已过去的日期不可放
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
        if (state.availability.key === sourceKey && state.availability.roomKey === roomKey && (state.availability.data || state.availability.loading)) return state.availability.data;
        state.availability = { key: sourceKey, roomKey, data: null, loading: true };
        try {
            const params = new URLSearchParams({ year: term().year || '', term: term().term || '', event_key: sourceKey, room_id: roomId, room: roomName });
            const data = await api(`${API}/availability?${params}`);
            if (state.availability.key !== sourceKey || state.availability.roomKey !== roomKey) return null;
            state.availability = { key: sourceKey, roomKey, data: data.availability || null, loading: false };
        } catch (error) {
            state.availability = { key: sourceKey, roomKey, data: null, loading: false };
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
        refs.legend.innerHTML = `<span class="cse-legend__item cse-legend__item--block">学生/本人有课 · 禁放</span><span class="cse-legend__item cse-legend__item--room">教室已占用 · 需换教室</span><span class="cse-legend__item cse-legend__item--ok">可放置</span><span class="cse-legend__item cse-legend__item--unknown">教室未查询</span><span class="cse-legend__item cse-legend__item--holiday">节假日 · 禁放</span><span class="cse-legend__item cse-legend__item--past">已过去 · 禁放</span><span class="cse-legend__item cse-legend__item--workday">调休上课 · 按被补那天课表</span><span class="cse-legend__note">${escapeHtml(notes.join(' · '))}</span>`;
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
        renderResequence();
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
            refs.pushBtn.innerHTML = `保存到教务 <span class="cse-btn__badge">${pending}</span>`;
        }
        if (refs.syncBtn) refs.syncBtn.disabled = state.busy;
        if (refs.availSync) refs.availSync.disabled = state.busy || !state.payload?.editable;
        if (refs.backLink) {
            const t = term();
            refs.backLink.href = `/manage/academic/course-schedule${t.year ? `?year=${encodeURIComponent(t.year)}&term=${encodeURIComponent(t.term)}` : ''}`;
        }
    }

    function renderWeekRail() {
        if (!refs.weeks) return;
        const selection = state.selectedKey ? resolveSelection(state.selectedKey) : null;
        const availData = selection ? availabilityData(selection.lesson.event_key) : null;
        const span = selection ? (selection.lesson.sections || []).length : 0;
        const items = weeks().map(week => {
            const index = Number(week.week_index);
            const free = availData && span ? freeStartCount(availData, index, span) : null;
            const draftCount = new Set((week.lessons || []).filter(l => l.edit_draft || l.edit_ghost).map(l => l.edit_draft ? l.edit_draft.id : l.edit_draft_id)).size;
            const holidays = Array.from({ length: 7 }, (_, i) => dayState(index, i + 1)).filter(d => d.kind === 'holiday');
            const swaps = swapsForWeek(index);
            const past = dateOf(index, 7) && isPastDate(dateOf(index, 7));
            const classes = ['cse-week', index === state.activeWeek ? 'is-active' : '', week.is_current ? 'is-current' : '', past ? 'is-past' : '', holidays.length ? 'has-holiday' : ''].filter(Boolean).join(' ');
            const marks = [
                holidays.length ? `<span class="cse-week__holiday" title="${escapeHtml(holidays.map(d => `${shortDate(d.iso)} ${d.info.label || '放假'}`).join('；'))}">假 ${holidays.length}</span>` : '',
                ...swaps.map(swap => `<span class="cse-week__swap" data-cse-swap-dot="${escapeHtml(swap.workday_date)}" style="--cse-swap:${swapColor(swap)}" title="${escapeHtml(swapTitle(swap))}">${Number(swap.week) === index ? '调休' : '被补'}</span>`),
            ].filter(Boolean).join('');
            return `<button type="button" class="${classes}" data-cse-week="${index}" aria-pressed="${index === state.activeWeek}">
                <strong>${escapeHtml(week.label)}${past ? '<em class="cse-week__past">已过</em>' : ''}</strong>
                <span class="cse-week__count">${draftCount ? `<span class="cse-week__draft" title="本周有 ${draftCount} 项调整">${draftCount}</span> ` : ''}${free !== null ? `<span class="cse-week__free${free ? '' : ' is-none'}" title="本周学生与本人都有空的时段数（教室占用另查）">${free} 可放</span> ` : ''}${week.lesson_count} 节</span>
                <small>${escapeHtml(week.date_range_label || '')}</small>
                ${marks ? `<span class="cse-week__marks">${marks}</span>` : ''}
            </button>`;
        });
        refs.weeks.innerHTML = `<div class="cse-weeks__title">全部周次</div>${items.join('')}`;
        refs.weeks.querySelector('.cse-week.is-active')?.scrollIntoView({ block: 'nearest' });
        scheduleSwapLines();
    }

    function lessonCardHtml(lesson, { minSection, maxSection, columnBase }) {
        const sections = lesson.sections || [];
        const start = Math.max(minSection, sections[0] || minSection);
        const end = Math.min(maxSection, sections[sections.length - 1] || start);
        const column = Math.min(7, Math.max(1, lesson.weekday || 1)) + columnBase - 1;
        const gridPos = `grid-column:${column};grid-row:${start - minSection + 2} / span ${Math.max(1, end - start + 1)};`;
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
        const pendingChange = lesson.adjustment && lesson.counts_towards_total === false;
        const classes = ['cs-lesson', 'cs-lesson--cell', 'cse-lesson', ghost ? 'cse-lesson--ghost' : '', moved ? 'cse-lesson--moved' : '', mirror ? 'cse-lesson--mirror' : '', past ? 'cse-lesson--past' : '',
            selected ? 'is-selected' : '', dragging ? 'is-drag-source' : '', pendingChange ? 'cs-lesson--proposed' : ''].filter(Boolean).join(' ');
        const roomBusy = ghost ? lesson.edit_room_status === 'busy' : (moved && lesson.edit_draft.room_status === 'busy');
        const tag = mirror ? `<span class="cse-tag cse-tag--workday">${escapeHtml(lesson.mirror_label || '调休上课')}</span>`
            : ghost ? `<span class="cse-tag cse-tag--${escapeHtml(status)}">${escapeHtml(statusLabel)}</span>${roomBusy ? '<span class="cse-tag cse-tag--room">需换教室</span>' : ''}`
                : moved ? `<span class="cse-tag cse-tag--muted">已计划调至 ${escapeHtml(lesson.edit_draft.proposed_label)}</span>`
                    : pendingChange ? '<span class="cse-tag cse-tag--muted">待审拟安排（不可编辑）</span>'
                        : past ? '<span class="cse-tag cse-tag--muted">已上过 · 不可调整</span>' : '';
        const time = [lesson.actual_date, lesson.section_label].filter(Boolean).join(' · ');
        return `<div class="cs-lesson-slot" style="${gridPos}">
            <div class="${classes}" data-cse-lesson="${escapeHtml(key)}" data-status="${escapeHtml(status)}"${mirror ? ' data-mirror="1"' : ''}${past ? ' data-past="1"' : ''} tabindex="0" role="button"
                 aria-label="${escapeHtml(`${lesson.course_name} ${time} ${room}`)}" title="${escapeHtml(`${lesson.course_name} · ${room}`)}" style="--cs-accent:hsl(var(--ls-primary))">
                <div class="cs-lesson__surface">
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
                    : st.kind === 'past' ? '<span class="cse-dayhead__tag cse-dayhead__tag--past">已过</span>' : '';
            return `<div class="${classes}" data-cse-dayhead="${weekday}" data-date="${escapeHtml(st.iso)}" style="grid-column:${index + columnBase};grid-row:1;"><span class="cse-dayhead__name">周${day}<small>${escapeHtml(shortDate(st.iso))}${weekday === todayColumn ? ' · 今天' : ''}</small></span>${tag}</div>`;
        }).join('');
        const sectionLabels = Array.from({ length: sectionCount }, (_, offset) => {
            const section = 1 + offset;
            const pairStart = pairStarts().includes(section);
            return `<div class="cs-grid__section cs-grid__section--${sectionBand(section)}${pairStart ? ' cse-section--pair' : ''}" style="grid-column:2;grid-row:${offset + 2};" title="${section < minSection ? '早读时段不可放置课次' : pairStart ? `课次可从第 ${section} 节开始` : ''}">${section}</div>`;
        }).join('');
        const lockReason = { holiday: '节假日不可放置', past: '已过去的日期不可放置' };
        const cells = Array.from({ length: sectionCount * 7 }, (_, cell) => {
            const section = 1 + Math.floor(cell / 7);
            const weekday = (cell % 7) + 1;
            const st = days[weekday - 1];
            const dayLock = st.kind === 'holiday' || st.kind === 'past' ? st.kind : '';
            const locked = section < minSection || Boolean(dayLock);
            const eff = effectiveSlot(week.week_index, weekday);
            const avail = availData && !locked ? cellState(availData, eff.week, eff.weekday, section) : '';
            const reason = avail ? cellReason(availData, eff.week, eff.weekday, section) : '';
            const classes = ['cs-grid__cellbg', `cs-grid__cellbg--${sectionBand(section)}`, weekday >= 6 ? 'cs-grid__cellbg--weekend' : '',
                weekday === todayColumn ? 'cs-grid__cellbg--today' : '', section < minSection ? 'cs-grid__cellbg--locked' : '', dayLock ? `cs-grid__cellbg--${dayLock}` : '',
                st.kind === 'workday' ? 'cs-grid__cellbg--workday' : '', avail ? `is-avail-${avail}` : ''].filter(Boolean).join(' ');
            const title = dayLock ? `${lockReason[dayLock]}${st.kind === 'holiday' ? `（${st.info.label || ''}）` : ''}` : reason ? `${AVAIL_LABELS[avail] || ''}：${reason}` : '';
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
        const lessons = [...(week.lessons || []), ...mirrorLessons(week)].map(lesson => lessonCardHtml(lesson, { minSection: 1, maxSection, columnBase })).join('');
        const rows = Array.from({ length: sectionCount }, (_, offset) => (1 + offset < minSection ? 'minmax(22px, .5fr)' : 'minmax(40px, 1fr)')).join(' ');
        refs.stageBody.innerHTML = `<div class="cs-grid cs-grid--expanded cse-grid" style="grid-template-columns:30px 54px repeat(7, minmax(0, 1fr));grid-template-rows:44px ${rows};">
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
        // Curved arrow. Header→header arcs above the headers; rail↔header bends horizontally.
        if (kind === 'head-head') {
            const lift = Math.max(26, Math.min(60, Math.abs(to.cx - from.cx) * 0.25));
            const y = Math.min(from.top, to.top) - 6;
            return { d: `M ${from.cx} ${from.top} C ${from.cx} ${y - lift}, ${to.cx} ${y - lift}, ${to.cx} ${to.top}`, label: { x: (from.cx + to.cx) / 2, y: y - lift * 0.75 } };
        }
        if (kind === 'head-rail') {
            const sx = from.cx, sy = from.top, tx = to.right + 2, ty = to.cy;
            const midX = tx + Math.max(40, (sx - tx) * 0.35);
            return { d: `M ${sx} ${sy} C ${sx} ${sy - 40}, ${midX} ${ty}, ${tx} ${ty}`, label: { x: (sx + midX) / 2, y: sy - 34 } };
        }
        if (kind === 'rail-head') {
            const sx = from.right + 2, sy = from.cy, tx = to.cx, ty = to.top;
            const midX = sx + Math.max(40, (tx - sx) * 0.35);
            return { d: `M ${sx} ${sy} C ${midX} ${sy}, ${tx} ${ty - 40}, ${tx} ${ty}`, label: { x: (sx + midX) / 2, y: sy - 14 } };
        }
        // rail-rail: small bracket along the rail's right edge
        const x = Math.max(from.right, to.right) + 14;
        return { d: `M ${from.right} ${from.cy} C ${x} ${from.cy}, ${x} ${to.cy}, ${to.right} ${to.cy}`, label: { x: x + 4, y: (from.cy + to.cy) / 2 } };
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
        const ns = 'http://www.w3.org/2000/svg';
        const el = (tag, attrs = {}, text = '') => { const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v)); if (text) n.textContent = text; return n; };
        const defs = el('defs');
        const nodes = [defs];
        const active = Number(week.week_index);
        swaps.forEach((swap, index) => {
            const color = swapColor(swap);
            const fromWeek = Number(swap.week), toWeek = Number(swap.makeup_week);
            let from = null, to = null, kind = '';
            if (fromWeek === active && toWeek === active) { from = anchorRect(head(swap.weekday), layoutRect); to = anchorRect(head(swap.makeup_weekday), layoutRect); kind = 'head-head'; }
            else if (fromWeek === active) { const card = railCard(toWeek); if (!railVisible(card)) return; from = anchorRect(head(swap.weekday), layoutRect); to = anchorRect(card, layoutRect); kind = 'head-rail'; }
            else if (toWeek === active) { const card = railCard(fromWeek); if (!railVisible(card)) return; from = anchorRect(card, layoutRect); to = anchorRect(head(swap.makeup_weekday), layoutRect); kind = 'rail-head'; }
            else { const a = railCard(fromWeek), b = railCard(toWeek); if (!railVisible(a) || !railVisible(b)) return; from = anchorRect(a, layoutRect); to = anchorRect(b, layoutRect); kind = 'rail-rail'; }
            if (!from || !to) return;
            const markerId = `cse-swap-arrow-${index}`;
            const marker = el('marker', { id: markerId, markerWidth: 12, markerHeight: 12, refX: 10, refY: 6, orient: 'auto', markerUnits: 'userSpaceOnUse', overflow: 'visible' });
            marker.append(el('path', { d: 'M 2 2 L 10 6 L 2 10', fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
            defs.append(marker);
            const { d, label } = swapPath(from, to, kind);
            const group = el('g', { class: `cse-swap cse-swap--${kind}`, 'data-cse-swap': swap.workday_date, style: `color:${color}` });
            group.append(el('title', {}, swapTitle(swap)));
            group.append(el('path', { class: 'cse-swap__line', d, stroke: color, 'marker-end': `url(#${markerId})` }));
            group.append(el('circle', { class: 'cse-swap__origin', cx: from.cx, cy: kind.startsWith('head') ? from.top : from.cy, r: 2.8, stroke: color }));
            const text = kind === 'head-head' ? `补${swap.makeup_for_weekday || ''}课` : kind === 'head-rail' ? `补第${toWeek}周${swap.makeup_for_weekday || ''}课` : kind === 'rail-head' ? `第${fromWeek}周${shortDate(swap.workday_date)}调休来补` : `第${fromWeek}周→第${toWeek}周`;
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
        const selection = state.selectedKey ? resolveSelection(state.selectedKey) : null;
        refs.layout?.classList.toggle('has-drawer', Boolean(selection));
        if (!selection) { refs.drawer.hidden = true; refs.drawer.innerHTML = ''; state.form = null; return; }
        const { lesson, week, draft } = selection;
        if (!state.form || state.form.key !== lesson.event_key) state.form = buildForm(selection);
        const form = state.form;
        const { max_section: maxSection, max_week: maxWeek } = rules();
        const pastLesson = isPastDate(String(lesson.actual_date || ''));
        const locked = lesson.counts_towards_total === false || draft?.status === 'pushed' || pastLesson;
        const weekOptions = Array.from({ length: Math.max(maxWeek, weeks().length) }, (_, i) => i + 1)
            .map(w => `<option value="${w}"${w === form.week ? ' selected' : ''}>第${w}周${dateOf(w, 7) && isPastDate(dateOf(w, 7)) ? '（已过）' : ''}</option>`).join('');
        const dayOptions = DAY_NAMES.map((d, i) => {
            const st = dayState(form.week, i + 1);
            const note = st.kind === 'holiday' ? `（${st.info.label || '放假'}）` : st.kind === 'past' ? '（已过）' : st.kind === 'workday' ? `（调休·补${st.info.makeup_for_weekday || ''}）` : '';
            return `<option value="${i + 1}"${i + 1 === form.weekday ? ' selected' : ''}>周${d} ${shortDate(st.iso)}${note}</option>`;
        }).join('');
        const starts = pairStarts().filter(s => s + form.span - 1 <= maxSection);
        if (form.start && !starts.includes(form.start)) starts.push(form.start);
        const startOptions = starts.sort((a, b) => a - b)
            .map(s => `<option value="${s}"${s === form.start ? ' selected' : ''}>第${s}${form.span > 1 ? `-${s + form.span - 1}` : ''}节${pairStarts().includes(s) ? '' : '（不合规）'}</option>`).join('');
        const targetDay = dayState(form.week, form.weekday);
        const dayNote = targetDay.kind === 'holiday' ? `<div class="cse-status cse-status--conflict">目标日期 ${shortDate(targetDay.iso)} 为节假日（${escapeHtml(targetDay.info.label || '放假')}），不能安排课程。</div>`
            : targetDay.kind === 'past' ? `<div class="cse-status cse-status--conflict">目标日期 ${shortDate(targetDay.iso)} 已经过去，不能放置。</div>`
                : targetDay.kind === 'workday' ? `<div class="cse-status cse-status--workday">目标日期 ${shortDate(targetDay.iso)} 为调休上课日：当天按 ${escapeHtml(targetDay.info.makeup_for_weekday || '')}（${shortDate(targetDay.info.makeup_for_date)}）课表上课，冲突与可调时段按那一天判断。${targetDay.info.inferred ? '补课星期为推断，以学校通知为准。' : ''}</div>` : '';
        const statusBlock = pastLesson ? '<div class="cse-status cse-status--muted"><strong>该课次已上过</strong><br>已发生的课次不能再调整；调整后续课次时，课次序号与材料保持不变。</div>'
            : draft ? `<div class="cse-status cse-status--${escapeHtml(draft.status)}"><strong>${escapeHtml(draft.status_label)}</strong>${draft.remote_message ? `<br>${escapeHtml(draft.remote_message)}` : ''}${draft.status === 'pushed' ? '<br>如需修改，请先「从教务撤回」。' : ''}</div>` : '';
        const materialsBlock = renderMaterialsBlock(lesson);
        refs.drawer.hidden = false;
        refs.drawer.innerHTML = `
            <div class="cse-drawer__head">
                <div><h3>${escapeHtml(lesson.course_name)}</h3><p>${escapeHtml(lesson.class_label || lesson.teaching_class_name || '')}${lesson.teaching_class_name && lesson.class_label !== lesson.teaching_class_name ? ` · ${escapeHtml(lesson.teaching_class_name)}` : ''}</p></div>
                <button type="button" class="cse-drawer__close" data-cse-close aria-label="关闭课次属性">×</button>
            </div>
            <div class="cse-drawer__body">
                ${statusBlock}
                <section class="cse-section">
                    <div class="cse-section__title">原安排</div>
                    <dl class="cse-kv">
                        <dt>时间</dt><dd>${escapeHtml(week.label)} ${escapeHtml(lesson.weekday_label || '')} ${escapeHtml(lesson.section_label || '')}${lesson.actual_date ? `（${escapeHtml(lesson.actual_date)}）` : ''}</dd>
                        <dt>教室</dt><dd>${escapeHtml(lesson.classroom || '教室待定')}</dd>
                        <dt>占用</dt><dd>${form.span} 节${lesson.session_no ? ` · 第${lesson.session_no}次课${lesson.session_total ? `（共${lesson.session_total}次）` : ''}` : ''}</dd>
                    </dl>
                </section>
                <section class="cse-section">
                    <div class="cse-section__title">调整到</div>
                    <div class="cse-field-row">
                        <div class="cse-field"><label for="cseWeek">周次</label><select id="cseWeek" class="cse-select" data-cse-field="week"${locked ? ' disabled' : ''}>${weekOptions}</select></div>
                        <div class="cse-field"><label for="cseWeekday">星期</label><select id="cseWeekday" class="cse-select" data-cse-field="weekday"${locked ? ' disabled' : ''}>${dayOptions}</select></div>
                        <div class="cse-field"><label for="cseStart">节次</label><select id="cseStart" class="cse-select" data-cse-field="start"${locked ? ' disabled' : ''}>${startOptions}</select></div>
                    </div>
                    <div class="cse-field__hint">第 1 节为早读，不可放置；课次以两小节为单位（2-3 / 4-5 / 6-7 / 8-9 / 10-11），四小节课可跨上午下午；占用节数固定为 ${form.span} 节，最多到第 ${maxSection} 节。</div>
                    ${dayNote}
                    <div class="cse-field cse-rooms">
                        <label for="cseRoom">教室（教务场地）</label>
                        <input id="cseRoom" class="cse-input" data-cse-field="room" value="${escapeHtml(form.room)}" placeholder="输入楼名/教室号搜索，或留空沿用原教室" autocomplete="off"${locked ? ' disabled' : ''}>
                        <div class="cse-rooms__list" data-cse-rooms hidden></div>
                        <div class="cse-field__hint">${form.room_id ? `已选教务场地 ${escapeHtml(form.room_id)}` : '未选择教务场地时沿用原教室'}</div>
                    </div>
                    <div class="cse-verdict" data-cse-verdict></div>
                    <div class="cse-field cse-free-rooms" data-cse-free-rooms-panel>
                        <div class="cse-free-rooms__head">
                            <label>该时段空闲教室（二次搜索，实时查教务）</label>
                            <button type="button" class="cse-btn cse-btn--sm" data-cse-free-rooms${locked ? ' disabled' : ''}>查询空闲教室</button>
                        </div>
                        <div class="cse-free-rooms__body" data-cse-free-rooms-list></div>
                    </div>
                    <div class="cse-field">
                        <label for="cseReason">调课原因（随草稿一并写入教务，可在教务提交时修改）</label>
                        <textarea id="cseReason" class="cse-textarea" data-cse-field="reason" maxlength="400" placeholder="例如：国庆假期调休、参加学术会议…"${locked ? ' disabled' : ''}>${escapeHtml(form.reason)}</textarea>
                    </div>
                </section>
                ${materialsBlock}
            </div>
            <div class="cse-drawer__foot">
                ${locked ? '' : '<button type="button" class="cse-btn cse-btn--primary" data-cse-save>保存变更</button>'}
                ${draft && draft.status !== 'pushed' ? `<button type="button" class="cse-btn cse-btn--danger" data-cse-discard="${draft.id}">撤销变更</button>` : ''}
                ${draft && draft.status === 'pushed' ? `<button type="button" class="cse-btn cse-btn--danger" data-cse-withdraw="${draft.id}">从教务撤回</button>` : ''}
                ${lesson.classroom_url ? `<a class="cse-btn" href="${escapeHtml(lesson.classroom_url)}">进入课堂</a>` : ''}
            </div>`;
        if (lesson.class_offering_id && lesson.session_id) loadMaterials(lesson);
        renderDrawerVerdict();
        renderFreeRooms();
        loadAvailability(lesson.event_key, { roomId: form.room_id, roomName: form.room_id ? form.room : '' });
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
        return form ? `${term().year}|${term().term}|${form.week}|${form.weekday}|${currentFormSections().join(',')}` : '';
    }

    function renderFreeRooms() {
        const list = refs.drawer?.querySelector('[data-cse-free-rooms-list]');
        if (!list || !state.form) return;
        const fr = state.freeRooms;
        if (fr.slotKey !== freeRoomSlotKey()) { list.innerHTML = '<div class="cse-materials__empty">选择目标时段后点击「查询空闲教室」，教务会返回该时段所有空闲教室。</div>'; return; }
        if (fr.loading) { list.innerHTML = '<div class="cse-materials__empty">正在向教务查询空闲教室…</div>'; return; }
        if (fr.status !== 'success') { list.innerHTML = `<div class="cse-materials__empty">${escapeHtml(fr.message || '查询失败')}</div>`; return; }
        const roomLine = fr.roomStatus === 'busy' ? '<div class="cse-status cse-status--conflict">原教室该时段已被占用，请从下方选择一间空闲教室。</div>'
            : fr.roomStatus === 'free' ? '<div class="cse-status cse-status--pushed">原教室该时段空闲，可直接保存。</div>' : '';
        const items = fr.items.slice(0, 40).map(room => `<button type="button" class="cse-rooms__item" data-cse-room="${escapeHtml(room.place_id || room.room_code || '')}" data-cse-room-name="${escapeHtml(room.display_name || room.room_full_name || room.room_name || '')}"><span>${escapeHtml(room.display_name || room.room_full_name || room.room_name || '')}</span><small>${escapeHtml([room.campus_name, room.building_name, room.seat_count ? `${room.seat_count} 座` : '', room.room_type_name].filter(Boolean).join(' · '))}</small></button>`).join('');
        list.innerHTML = `${roomLine}${items ? `<div class="cse-free-rooms__grid">${items}</div><div class="cse-field__hint">共 ${fr.items.length} 间空闲教室，点击即选用。</div>` : '<div class="cse-materials__empty">该时段没有空闲教室。</div>'}`;
    }

    async function searchFreeRooms() {
        const form = state.form;
        if (!form) return;
        const key = freeRoomSlotKey();
        const original = resolveSelection(form.key)?.lesson;
        const roomId = form.room_id || '';
        const roomName = form.room_id ? form.room : (original?.classroom || '');
        state.freeRooms = { slotKey: key, items: [], status: '', roomStatus: '', loading: true, message: '' };
        renderFreeRooms();
        try {
            const eff = effectiveSlot(form.week, form.weekday); // 调休上课日按被补那天查教室占用
            const params = new URLSearchParams({ year: term().year || '', term: term().term || '', week: String(eff.week), weekday: String(eff.weekday), sections: currentFormSections().join(','), room_id: roomId, room: roomName });
            const data = await api(`${API}/free-rooms?${params}`);
            const result = data.result || {};
            state.freeRooms = { slotKey: key, items: result.items || [], status: result.status || 'failed', roomStatus: result.room_status || 'unknown', loading: false, message: result.message || '' };
            if (result.room_status && result.room_status !== 'unknown') {
                state.availability = { ...state.availability, data: null };
                await loadAvailability(form.key, { roomId, roomName: roomId ? roomName : '' });
            }
        } catch (error) {
            state.freeRooms = { slotKey: key, items: [], status: 'failed', roomStatus: 'unknown', loading: false, message: error.message };
        }
        renderFreeRooms();
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
        return `<section class="cse-section" data-cse-materials><div class="cse-section__title">上课材料</div>${body}<div><a class="cse-btn cse-btn--sm" href="${escapeHtml(manageUrl)}">管理课次材料 →</a></div></section>`;
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

    /** 教务冲突明细（ctxxList / conflictXs 结构未知，按常见字段友好展示，其余原样列出）。 */
    function conflictDetailsHtml(conflict) {
        const rows = Array.isArray(conflict?.details) ? conflict.details : [];
        if (!rows.length) return '';
        const known = [['kcmc', '课程'], ['jxbmc', '教学班'], ['jsxm', '教师'], ['xm', '学生'], ['xh', '学号'], ['cdmc', '教室'], ['sksj', '时间'], ['zcd', '周次'], ['xqj', '星期'], ['jc', '节次'], ['ctlx', '冲突类型']];
        const lines = rows.slice(0, 8).map(row => {
            if (!row || typeof row !== 'object') return escapeHtml(String(row));
            const parts = known.filter(([k]) => row[k]).map(([k, label]) => `${label} ${escapeHtml(String(row[k]))}`);
            return parts.length ? parts.join(' · ') : escapeHtml(Object.entries(row).filter(([, v]) => v !== '' && v !== null).slice(0, 6).map(([k, v]) => `${k}=${v}`).join(' · '));
        });
        return `<div class="cse-draft__msg"><strong>教务冲突明细：</strong><ul class="cse-conflict-list">${lines.map(l => `<li>${l}</li>`).join('')}</ul>${rows.length > 8 ? `<span>… 共 ${rows.length} 条</span>` : ''}</div>`;
    }

    function renderDrafts() {
        if (!refs.drafts) return;
        const list = drafts();
        const pushed = list.filter(d => d.status === 'pushed').length;
        const rows = list.map(draft => `<div class="cse-draft" data-cse-draft="${draft.id}">
            <div class="cse-draft__title"><span>${escapeHtml(draft.course_name)}</span><span class="cse-tag cse-tag--${escapeHtml(draft.status)}">${escapeHtml(draft.status_label)}</span>${draft.change_kind === 'room' ? '<span class="cse-tag cse-tag--muted">仅换教室</span>' : ''}${draft.room_status === 'busy' ? '<span class="cse-tag cse-tag--room">教室已占用 · 需换教室</span>' : draft.room_status === 'free' ? '<span class="cse-tag cse-tag--ok">教室空闲</span>' : ''}</div>
            <div class="cse-draft__route">${escapeHtml(draft.original_label)} → <b>${escapeHtml(draft.proposed_label)}</b>${draft.reason ? ` · 原因：${escapeHtml(draft.reason)}` : ''}</div>
            ${draft.remote_message || draft.remote_label ? `<div class="cse-draft__msg">${escapeHtml(draft.remote_label ? `教务：${draft.remote_label}` : '')}${draft.remote_label && draft.remote_message ? ' · ' : ''}${escapeHtml(draft.remote_message || '')}</div>` : ''}
            ${conflictDetailsHtml(draft.remote_conflict)}
            <div class="cse-draft__actions">
                <button type="button" class="cse-btn cse-btn--sm" data-cse-locate="${draft.id}">定位</button>
                ${draft.status === 'pushed' ? `<button type="button" class="cse-btn cse-btn--sm cse-btn--danger" data-cse-withdraw="${draft.id}">从教务撤回</button>` : `<button type="button" class="cse-btn cse-btn--sm cse-btn--danger" data-cse-discard="${draft.id}">撤销</button>`}
                ${draft.status === 'conflict' && !draft.remote_conflict?.hard ? `<button type="button" class="cse-btn cse-btn--sm" data-cse-force="${draft.id}">冲突仍保存</button>` : ''}
            </div>
        </div>`).join('');
        const next = pushed ? `<div class="cse-drafts__next"><span>已有 ${pushed} 项保存到教务草稿。下一步：</span><a href="${escapeHtml(state.payload?.zf_entry_url || '#')}" target="_blank" rel="noopener">登录教务系统 → 调停课申请 → 核对「待提交」并点击「提交申请」 ↗</a></div>` : '';
        refs.drafts.innerHTML = `<div class="cse-drafts__head"><h3>变更清单</h3><p>拖拽或在右侧属性中保存后，变更先记录在平台；「保存到教务」只写入教务草稿，不会提交申请。</p></div>
            ${list.length ? `<div class="cse-drafts__list">${rows}</div>` : '<div class="cse-materials__empty">还没有任何调整。按住课次拖到新的节次，或单击课次在右侧设置。</div>'}${next}`;
    }

    /* ------------------------------------------------------------------ 课次重排 (resequence) */
    function renderResequence() {
        const box = refs.resequence;
        if (!box) return;
        if (!state.payload?.editable) { box.hidden = true; box.innerHTML = ''; return; }
        const rs = state.resequence;
        const pending = pendingDrafts().length + drafts().filter(d => d.status === 'pushed').length;
        let body = '';
        if (rs.loading) body = '<div class="cse-materials__empty">正在计算课次重排预览…</div>';
        else if (rs.error) body = `<div class="cse-materials__empty">${escapeHtml(rs.error)}</div>`;
        else if (rs.preview) {
            const plans = rs.preview.plans || [];
            body = plans.length ? plans.map(plan => `<div class="cse-reseq__course">
                <div class="cse-reseq__title"><strong>${escapeHtml(plan.course_label || '课程')}</strong><span>已发生 ${plan.frozen_count} 次不变 · 重排 ${plan.change_count} 次 · 直接调整 ${plan.moved_count} 次</span></div>
                ${plan.changes.length ? `<ol class="cse-reseq__list">${plan.changes.map(c => `<li${c.moved_directly ? ' class="is-direct"' : ''}><b>第 ${c.order_index} 次课</b>${c.title ? ` ${escapeHtml(c.title)}` : ''}：<span class="cse-reseq__old">${escapeHtml(describeSlot(c.old))}</span> → <span class="cse-reseq__new">${escapeHtml(describeSlot(c.new))}</span>${c.moved_directly ? '<i>本次调整</i>' : ''}</li>`).join('')}</ol>` : '<div class="cse-materials__empty">顺序不变。</div>'}
            </div>`).join('') : '<div class="cse-materials__empty">当前草稿不涉及平台课堂课次，或课次顺序不受影响。</div>';
            body += `<div class="cse-field__hint">${escapeHtml(rs.preview.note || '')}</div>`;
        } else body = `<div class="cse-materials__empty">${pending ? '点击「预览课次重排」查看调整生效后各课次的新顺序。' : '还没有调整；调整后可在这里预览课次重排结果。'}</div>`;
        const lastApplied = rs.applied ? `<div class="cse-status cse-status--pushed">已按当前日期重排 ${rs.applied.applied_count} 个课次${rs.applied.applied_count ? '：' + rs.applied.reports.flatMap(r => r.summary).slice(0, 6).map(escapeHtml).join('；') : '（顺序已正确）'}</div>` : '';
        box.hidden = false;
        box.innerHTML = `<div class="cse-drafts__head"><h3>课次重排</h3><p>课程一旦调整，已发生的课次保持不变，剩余课次按新日期重新排序；课次序号与教学材料（含 HTML 包 lesson_N、git 同步）跟随序号自动重绑，无需手动改。教务审批通过并同步后自动执行。</p></div>
            ${lastApplied}${body}
            <div class="cse-draft__actions">
                <button type="button" class="cse-btn cse-btn--sm" data-cse-reseq-preview${state.busy ? ' disabled' : ''}>预览课次重排</button>
                <button type="button" class="cse-btn cse-btn--sm" data-cse-reseq-apply${state.busy ? ' disabled' : ''} title="按课次当前日期立即重排（用于日期已在课堂中手动改过、或审批已生效但顺序未更新的情况）">按当前日期重排</button>
            </div>`;
    }

    function describeSlot(slot) {
        if (!slot?.date) return '未排期';
        const d = new Date(`${slot.date}T00:00:00`);
        const weekday = Number.isNaN(d.getTime()) ? '' : `周${DAY_NAMES[(d.getDay() + 6) % 7]}`;
        return `${slot.week ? `第${slot.week}周 ` : ''}${shortDate(slot.date)} ${weekday} ${sectionText(slot.sections || [])}`.trim();
    }

    async function previewResequence() {
        state.resequence = { ...state.resequence, loading: true, error: '' };
        renderResequence();
        try {
            const data = await api(`${API}/resequence-preview?year=${encodeURIComponent(term().year)}&term=${encodeURIComponent(term().term)}`);
            state.resequence = { ...state.resequence, loading: false, preview: data };
        } catch (error) { state.resequence = { ...state.resequence, loading: false, error: error.message }; }
        renderResequence();
    }

    async function applyResequence() {
        const ok = await LQ.confirm({
            title: '按当前日期重排课次',
            message: '将按各课次当前日期重新排序本学期课次：已发生的课次不变，其余课次的日期按顺序重新分配；课次序号、材料绑定不变。此操作会改动课堂课次日期，确定继续？',
            confirmLabel: '立即重排',
        });
        if (!ok) return;
        setBusy(true);
        try {
            const data = await api(`${API}/resequence/apply`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term }) });
            state.resequence = { loading: false, error: '', preview: null, applied: data.result };
            applyPayload(data);
            toast(data.result?.applied_count ? `已重排 ${data.result.applied_count} 个课次。` : '课次顺序已经正确，无需重排。', 'success');
        } catch (error) { toast(error.message, 'danger'); }
        finally { setBusy(false); }
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
    function setBusy(flag) { state.busy = flag; renderMeta(); renderTermSelect(); renderResequence(); }

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
            toast(`已记录：${data.draft.course_name} → ${data.draft.proposed_label}`, 'success');
            return data.draft;
        } catch (error) { toast(error.message, 'danger'); return null; }
        finally { setBusy(false); }
    }

    async function discardDraft(id) {
        const draft = draftById(id);
        if (!draft) return;
        const ok = await LQ.confirm({ title: '撤销变更', message: `撤销「${draft.course_name}」调至 ${draft.proposed_label} 的本地变更？`, confirmLabel: '撤销', danger: true });
        if (!ok) return;
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
        const targets = draftIds ? draftIds.map(draftById).filter(Boolean) : pendingDrafts();
        if (!targets.length) { toast('没有待保存到教务的变更。'); return; }
        const ok = await LQ.confirm({
            title: force ? '按冲突调停课保存' : '保存到教务系统',
            message: `${targets.length} 项变更将写入教务系统的调停课申请草稿（待提交），不会替您提交申请。${force ? '教务已检测到冲突，仍保存将标记为"冲突调停课"。' : ''}保存后请登录教务系统核对并点击「提交申请」。`,
            confirmLabel: force ? '仍然保存' : '保存到教务',
        });
        if (!ok) return;
        setBusy(true);
        try {
            const data = await api(`${API}/push`, { method: 'POST', body: JSON.stringify({ year: term().year, term: term().term, draft_ids: targets.map(d => d.id), force, force_note: force ? '已与相关方沟通，按新安排上课' : '' }) });
            state.lastPush = data.result;
            applyPayload(data);
            const result = data.result || {};
            if (result.status === 'missing_credential') {
                LQ.toast(result.message, { tone: 'warning', duration: 8000, action: { label: '去设置教务账号', href: CREDENTIAL_URL } }).catch(() => {});
            } else {
                toast(describePushResult(result), result.status === 'success' ? 'success' : result.status === 'partial' ? 'warning' : 'danger');
            }
            if (refs.feedback) refs.feedback.textContent = result.message || '';
        } catch (error) { toast(error.message, 'danger'); }
        finally { setBusy(false); }
    }

    async function withdrawDraft(id) {
        const draft = draftById(id);
        if (!draft) return;
        const ok = await LQ.confirm({ title: '从教务撤回', message: `从教务草稿中删除「${draft.course_name}」的这条调整？删除后可继续修改并再次保存。`, confirmLabel: '撤回', danger: true });
        if (!ok) return;
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
        state.selectedKey = selection ? selection.lesson.event_key : '';
        state.form = null;
        renderStage(); renderDrawer(); renderLegend(); renderWeekRail();
        if (selection) loadAvailability(selection.lesson.event_key);
    }

    /* ------------------------------------------------------------------ rooms search */
    function searchRooms(query) {
        const list = refs.drawer?.querySelector('[data-cse-rooms]');
        if (!list) return;
        window.clearTimeout(state.roomsTimer);
        state.roomsTimer = window.setTimeout(async () => {
            state.roomsRequest?.abort?.();
            const controller = new AbortController(); state.roomsRequest = controller;
            try {
                const data = await api(`${API}/rooms?q=${encodeURIComponent(query)}&limit=30`, { signal: controller.signal });
                if (controller.signal.aborted) return;
                const rooms = data.rooms || [];
                list.hidden = false;
                list.innerHTML = rooms.length
                    ? rooms.map(room => `<button type="button" class="cse-rooms__item" data-cse-room="${escapeHtml(room.room_id)}" data-cse-room-name="${escapeHtml(room.full_name || room.name)}"><span>${escapeHtml(room.full_name || room.name)}</span><small>${escapeHtml([room.campus, room.building, room.seat_count ? `${room.seat_count} 座` : '', room.type].filter(Boolean).join(' · '))}${room.schedulable ? '' : ' · 不可排课'}</small></button>`).join('')
                    : '<div class="cse-rooms__empty">没有匹配的教务场地；可保留文字作为教室名称，保存到教务时将沿用原教室场地。</div>';
            } catch (error) { if (error.name !== 'AbortError') list.innerHTML = `<div class="cse-rooms__empty">${escapeHtml(error.message)}</div>`; }
        }, 220);
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
            if (hoverWeek && hoverWeek !== state.activeWeek) {
                weekButton.classList.add('is-drop-hover');
                drag.weekTimer = window.setTimeout(() => {
                    if (!state.drag || state.drag.hoverWeek !== hoverWeek) return;
                    state.activeWeek = hoverWeek; renderWeekRail(); renderStage();
                    weekButton.classList.remove('is-drop-hover');
                    toast(`已摊开${activeWeekData()?.label || ''}，继续拖到目标节次即可跨周调整。`);
                }, WEEK_HOVER_DELAY);
            }
        }
        const LOCK_REASONS = { dawn: '第 1 节为早读，不可放置', holiday: '节假日不可放置', past: '已过去的日期不可放置' };
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
    refs.resequence?.addEventListener('click', async event => {
        if (event.target.closest('[data-cse-reseq-preview]')) { await previewResequence(); return; }
        if (event.target.closest('[data-cse-reseq-apply]')) await applyResequence();
    });
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
    refs.stageBody?.addEventListener('keydown', event => {
        const card = event.target.closest('[data-cse-lesson]');
        if (card && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); selectLesson(card.dataset.cseLesson); }
    });
    document.addEventListener('pointermove', onDocumentPointerMove, { passive: false });
    document.addEventListener('pointerup', onDocumentPointerUp);
    document.addEventListener('pointercancel', () => { press = null; endDrag(); });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && state.drag) { endDrag(); return; }
        if (event.key === 'Escape' && state.selectedKey && !event.defaultPrevented && !document.querySelector('.lq-dialog[open], dialog[open]')) { state.selectedKey = ''; state.form = null; renderStage(); renderDrawer(); }
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
        if (name === 'room') { state.form = { ...state.form, room: field.value, room_id: '' }; searchRooms(field.value); return; }
        if (name === 'reason') { state.form = { ...state.form, reason: field.value }; return; }
        state.form = { ...state.form, [name]: Number(field.value) };
        renderDrawerVerdict(); renderFreeRooms();
    });
    refs.drawer?.addEventListener('focusin', event => { if (event.target.matches('[data-cse-field="room"]')) searchRooms(event.target.value); });
    document.addEventListener('pointerdown', event => {
        const list = refs.drawer?.querySelector('[data-cse-rooms]');
        if (list && !list.hidden && !event.target.closest('.cse-rooms')) list.hidden = true;
    });
    refs.drafts?.addEventListener('click', async event => {
        const locate = event.target.closest('[data-cse-locate]');
        if (locate) { locateDraft(locate.dataset.cseLocate); return; }
        const discard = event.target.closest('[data-cse-discard]');
        if (discard) { await discardDraft(discard.dataset.cseDiscard); return; }
        const withdraw = event.target.closest('[data-cse-withdraw]');
        if (withdraw) { await withdrawDraft(withdraw.dataset.cseWithdraw); return; }
        const force = event.target.closest('[data-cse-force]');
        if (force) await pushDrafts({ draftIds: [Number(force.dataset.cseForce)], force: true });
    });

    applyPayload(boot, { keepWeek: false, keepSelection: false });
}
