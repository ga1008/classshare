const SECOND_MS = 1000;
const MIN_SYNC_MS = 60 * SECOND_MS;
const MAX_SYNC_MS = 5 * 60 * SECOND_MS;

let clockStates = [];
let tickTimer = 0;
let syncTimer = 0;
let stateChangeCallback = null;

function parseDateMs(value) {
    if (!value) return null;
    const text = String(value).trim();
    if (!text) return null;
    const normalized = text.includes('T') ? text : text.replace(' ', 'T');
    const parsed = Date.parse(normalized);
    return Number.isFinite(parsed) ? parsed : null;
}

function formatDuration(totalSeconds) {
    const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    const pad = (value) => String(value).padStart(2, '0');
    if (days > 0) {
        return `${days}天 ${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
    }
    return `${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
}

function readClockState(el) {
    const serverNowMs = parseDateMs(el.dataset.serverNow);
    const nowMs = Date.now();
    return {
        id: String(el.dataset.assignmentId || '').trim(),
        el,
        labelEl: el.querySelector('[data-assignment-clock-label]'),
        valueEl: el.querySelector('[data-assignment-clock-value]'),
        detailEl: el.querySelector('[data-assignment-clock-detail]'),
        offsetMs: serverNowMs === null ? 0 : serverNowMs - nowMs,
        countdownAtMs: parseDateMs(el.dataset.countdownAt),
        lateUntilMs: parseDateMs(el.dataset.lateUntil),
        deadlinePhase: el.dataset.deadlinePhase || 'none',
        accepting: el.dataset.accepting === '1' || el.dataset.accepting === 'true',
        lateOpen: el.dataset.lateOpen === '1' || el.dataset.lateOpen === 'true',
        latePolicyLabel: el.dataset.latePolicyLabel || '',
    };
}

function writeDatasetFromPayload(state, payload, serverNow) {
    const serverNowText = payload.server_now || serverNow || '';
    state.el.dataset.serverNow = serverNowText;
    state.el.dataset.countdownAt = payload.countdown_at || '';
    state.el.dataset.lateUntil = payload.late_submission_until || '';
    state.el.dataset.deadlinePhase = payload.deadline_phase || 'none';
    state.el.dataset.accepting = payload.is_accepting_submissions ? '1' : '0';
    state.el.dataset.lateOpen = payload.is_late_submission_open ? '1' : '0';
    state.el.dataset.latePolicyLabel = payload.late_policy_label || '';

    const refreshed = readClockState(state.el);
    Object.assign(state, refreshed);
}

function renderClock(state) {
    const serverMs = Date.now() + state.offsetMs;
    let phase = state.deadlinePhase;
    let accepting = state.accepting;
    let lateOpen = state.lateOpen;
    let countdownAtMs = state.countdownAtMs;
    if (phase === 'regular' && countdownAtMs !== null && serverMs >= countdownAtMs) {
        if (state.latePolicyLabel && (state.lateUntilMs === null || serverMs <= state.lateUntilMs)) {
            phase = 'late';
            accepting = true;
            lateOpen = true;
            countdownAtMs = state.lateUntilMs;
        } else {
            phase = 'closed';
            accepting = false;
            lateOpen = false;
        }
    }
    state.localAccepting = accepting;
    state.localDeadlinePhase = phase;
    state.localLateOpen = lateOpen;

    const remainingSeconds = countdownAtMs === null
        ? null
        : Math.floor((countdownAtMs - serverMs) / SECOND_MS);
    const isExpired = remainingSeconds !== null && remainingSeconds <= 0;
    const isUrgent = remainingSeconds !== null && remainingSeconds > 0 && remainingSeconds <= 3600;

    state.el.classList.toggle('is-late', lateOpen || phase === 'late');
    state.el.classList.toggle('is-urgent', isUrgent);
    state.el.classList.toggle('is-expired', isExpired && !accepting);

    if (phase === 'late') {
        if (state.labelEl) state.labelEl.textContent = '补交剩余';
        if (state.detailEl) state.detailEl.textContent = state.latePolicyLabel || '补交扣分已生效';
    } else if (phase === 'regular') {
        if (state.labelEl) state.labelEl.textContent = '剩余时间';
        if (state.detailEl && !state.detailEl.textContent.trim()) {
            state.detailEl.textContent = '请在首次截止前提交';
        }
    } else if (accepting) {
        if (state.labelEl) state.labelEl.textContent = lateOpen ? '补交开放中' : '开放中';
        if (state.detailEl) state.detailEl.textContent = state.latePolicyLabel || '';
    } else {
        if (state.labelEl) state.labelEl.textContent = '已截止';
        if (state.detailEl) state.detailEl.textContent = '当前不再接收提交';
    }

    if (state.valueEl) {
        if (remainingSeconds === null) {
            state.valueEl.textContent = accepting ? (lateOpen ? '补交开放' : '长期开放') : '已截止';
        } else {
            state.valueEl.textContent = isExpired ? '00:00:00' : formatDuration(remainingSeconds);
        }
    }
}

function emitStateChange() {
    if (typeof stateChangeCallback !== 'function') return;
    const map = new Map();
    clockStates.forEach((state) => {
        if (!state.id) return;
        map.set(state.id, {
            assignment_id: state.id,
            is_accepting_submissions: state.localAccepting ?? state.accepting,
            deadline_phase: state.localDeadlinePhase || state.deadlinePhase,
            is_late_submission_open: state.localLateOpen ?? state.lateOpen,
            late_policy_label: state.latePolicyLabel,
            countdown_at: state.el.dataset.countdownAt || '',
        });
    });
    stateChangeCallback(map);
}

function tick() {
    clockStates.forEach(renderClock);
    emitStateChange();
}

function scheduleTick() {
    window.clearInterval(tickTimer);
    if (!clockStates.length) return;
    tick();
    tickTimer = window.setInterval(tick, SECOND_MS);
}

function scheduleSync() {
    window.clearTimeout(syncTimer);
    if (!clockStates.some((state) => state.id)) return;
    const delay = MIN_SYNC_MS + Math.floor(Math.random() * (MAX_SYNC_MS - MIN_SYNC_MS));
    syncTimer = window.setTimeout(syncAssignmentTimeStates, delay);
}

async function syncAssignmentTimeStates() {
    const ids = [...new Set(clockStates.map((state) => state.id).filter(Boolean))];
    if (!ids.length) return;
    try {
        const response = await fetch(`/api/assignments/time-state?ids=${encodeURIComponent(ids.join(','))}`, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
        });
        if (!response.ok) throw new Error(`time-state ${response.status}`);
        const payload = await response.json();
        const byId = new Map((payload.assignments || []).map((item) => [String(item.assignment_id), item]));
        clockStates.forEach((state) => {
            const update = byId.get(state.id);
            if (update) {
                writeDatasetFromPayload(state, update, payload.server_now);
            }
        });
        tick();
        emitStateChange();
    } catch (error) {
        console.warn('Failed to sync assignment time state:', error);
    } finally {
        scheduleSync();
    }
}

export function initAssignmentClocks(options = {}) {
    stateChangeCallback = options.onStateChange || null;
    clockStates = Array.from(document.querySelectorAll('[data-assignment-clock]')).map(readClockState);
    scheduleTick();
    emitStateChange();
    scheduleSync();
    return {
        syncNow: syncAssignmentTimeStates,
        getStates: () => new Map(clockStates.map((state) => [state.id, state])),
    };
}
