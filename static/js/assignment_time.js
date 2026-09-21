const SECOND_MS = 1000;
const MIN_SYNC_MS = 60 * SECOND_MS;
const MAX_SYNC_MS = 5 * 60 * SECOND_MS;

let clockStates = [];
let tickTimer = 0;
let syncTimer = 0;
let stateChangeCallback = null;
let syncInFlight = false;
let ownerGeneration = 0;
let requestGeneration = 0;
let ownerActive = false;
const boundaryTimers = new Set();
const presentationListeners = new Map();
const RUNTIME = Symbol.for('lanshare.assignment-clocks.v1');
const isOwner = generation => ownerActive && generation === ownerGeneration;

function datasetFingerprint(el) {
    return JSON.stringify(['assignmentId', 'serverNow', 'startsAt', 'countdownAt', 'personalResubmission', 'resubmissionDueAt',
        'canResubmit', 'lateUntil', 'deadlinePhase', 'accepting', 'lateOpen', 'latePolicyLabel'].map(key => el.dataset[key]));
}

function parseDateMs(value) {
    if (!value) return null;
    const text = String(value).trim();
    if (!text) return null;
    const normalized = text.includes('T') ? text : text.replace(' ', 'T');
    const parsed = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}+08:00`);
    return Number.isFinite(parsed) ? parsed : null;
}

export function formatDuration(totalSeconds) {
    const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    const pad = (value) => String(value).padStart(2, '0');
    if (days > 0) return `${days} 天 ${hours} 小时`;
    if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
    return `${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
}

function readClockState(el) {
    const serverNowMs = parseDateMs(el.dataset.serverNow);
    const nowMs = Date.now();
    const startsAtMs = parseDateMs(el.dataset.startsAt);
    return {
        id: String(el.dataset.assignmentId || '').trim(),
        fingerprint: datasetFingerprint(el),
        el,
        labelEl: el.querySelector('[data-assignment-clock-label]'),
        valueEl: el.querySelector('[data-assignment-clock-value]'),
        detailEl: el.querySelector('[data-assignment-clock-detail]'),
        offsetMs: serverNowMs === null ? 0 : serverNowMs - nowMs,
        countdownAtMs: parseDateMs(el.dataset.countdownAt),
        startsAtMs,
        startSyncRequested: startsAtMs === null || startsAtMs <= (serverNowMs ?? nowMs),
        personalResubmission: el.dataset.personalResubmission === '1',
        resubmissionDueAtMs: parseDateMs(el.dataset.resubmissionDueAt),
        canResubmit: el.dataset.canResubmit === '1',
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
    if (!state.startSyncRequested && state.startsAtMs !== null && serverMs >= state.startsAtMs) {
        // Scheduling is authoritative on the server. Refresh at the boundary;
        // do not infer permission from a start timestamp alone.
        state.startSyncRequested = true;
        const generation = ownerGeneration;
        const timer = window.setTimeout(() => {
            boundaryTimers.delete(timer);
            if (isOwner(generation)) void syncAssignmentTimeStates();
        }, 0);
        boundaryTimers.add(timer);
    }
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
    if (phase === 'late' && countdownAtMs !== null && serverMs >= countdownAtMs) {
        phase = 'closed';
        accepting = false;
        lateOpen = false;
    }
    state.localAccepting = accepting;
    state.localDeadlinePhase = phase;
    state.localLateOpen = lateOpen;
    state.localCountdownAt = countdownAtMs;

    if (state.personalResubmission) {
        const personalOpen = state.canResubmit && (state.resubmissionDueAtMs === null || serverMs < state.resubmissionDueAtMs);
        state.el.classList.toggle('is-expired', !personalOpen);
        state.el.classList.toggle('is-late', false);
        state.el.classList.toggle('is-urgent', personalOpen && state.resubmissionDueAtMs !== null && state.resubmissionDueAtMs - serverMs <= 3600000);
        if (state.labelEl) state.labelEl.textContent = personalOpen ? '重交截止' : '重交已关闭';
        if (state.detailEl) state.detailEl.textContent = personalOpen ? '按本人的重交期限提交' : '当前个人重交窗口已关闭';
        if (state.valueEl) state.valueEl.textContent = state.resubmissionDueAtMs === null ? (personalOpen ? '开放中' : '已截止')
            : new Date(state.resubmissionDueAtMs).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
        state.presentation = presentation(state, personalOpen ? 'regular' : 'closed',
            personalOpen && state.resubmissionDueAtMs !== null && state.resubmissionDueAtMs - serverMs <= 3600000,
            state.resubmissionDueAtMs, serverMs);
        return;
    }

    const remainingSeconds = countdownAtMs === null
        ? null
        : Math.floor((countdownAtMs - serverMs) / SECOND_MS);
    const isExpired = remainingSeconds !== null && remainingSeconds <= 0;
    const isUrgent = remainingSeconds !== null && remainingSeconds > 0 && remainingSeconds <= 3600;
    const showDeadline = remainingSeconds !== null && remainingSeconds > 86400;

    state.el.classList.toggle('is-late', lateOpen || phase === 'late');
    state.el.classList.toggle('is-urgent', isUrgent);
    state.el.classList.toggle('is-expired', isExpired && !accepting);

    if (phase === 'late') {
        if (state.labelEl) state.labelEl.textContent = showDeadline ? '补交截止' : '补交剩余';
        if (state.detailEl) state.detailEl.textContent = state.latePolicyLabel || '补交扣分已生效';
    } else if (phase === 'regular') {
        if (state.labelEl) state.labelEl.textContent = showDeadline ? '截止时间' : '剩余时间';
        if (state.detailEl && ['', '截止时间', '倒计时', '长期有效', '请在首次截止前提交'].includes(state.detailEl.textContent.trim())) {
            state.detailEl.textContent = showDeadline ? '' : '请在首次截止前提交';
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
            const label = isExpired ? '已截止' : showDeadline
                ? new Date(countdownAtMs).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
                : formatDuration(remainingSeconds);
            if (state.valueEl.textContent !== label) state.valueEl.textContent = label;
        }
    }
    state.presentation = presentation(state, phase, isUrgent, countdownAtMs, serverMs);
}

function presentation(state, phase, urgent, deadlineAtMs, serverMs) {
    return Object.freeze({ assignmentId: state.id, phase, urgent,
        remainingSeconds: deadlineAtMs === null ? null : Math.floor((deadlineAtMs - serverMs) / SECOND_MS),
        deadlineAt: deadlineAtMs === null ? null : new Date(deadlineAtMs).toISOString(),
        label: state.labelEl?.textContent || '', value: state.valueEl?.textContent || '', detail: state.detailEl?.textContent || '' });
}

function notifyPresentation(listener, snapshot) {
    try { listener(snapshot); } catch (error) { console.warn('Assignment clock presentation listener failed:', error); }
}

function publishPresentations(generation) {
    for (const state of [...clockStates]) {
        if (!isOwner(generation)) return;
        for (const record of [...(presentationListeners.get(state.el) || [])]) {
            if (!isOwner(generation)) return;
            if (presentationListeners.get(state.el)?.has(record)) notifyPresentation(record.listener, state.presentation);
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
            countdown_at: state.localCountdownAt == null ? '' : new Date(state.localCountdownAt).toISOString(),
        });
    });
    stateChangeCallback(map);
}

function tick(generation = ownerGeneration) {
    if (!isOwner(generation)) return;
    clockStates.forEach(renderClock);
    publishPresentations(generation);
    if (!isOwner(generation)) return;
    emitStateChange();
}

function scheduleTick(generation = ownerGeneration) {
    if (!isOwner(generation)) return;
    window.clearInterval(tickTimer);
    if (!clockStates.length) return;
    tick(generation);
    if (isOwner(generation)) tickTimer = window.setInterval(() => tick(generation), SECOND_MS);
}

function scheduleSync(generation = ownerGeneration) {
    if (!isOwner(generation)) return;
    window.clearTimeout(syncTimer);
    if (!clockStates.some((state) => state.id)) return;
    const delay = MIN_SYNC_MS + Math.floor(Math.random() * (MAX_SYNC_MS - MIN_SYNC_MS));
    syncTimer = window.setTimeout(() => { if (isOwner(generation)) void syncAssignmentTimeStates(); }, delay);
}

async function syncAssignmentTimeStates() {
    if (!ownerActive || syncInFlight) return;
    const generation = ownerGeneration;
    const request = requestGeneration;
    const current = () => isOwner(generation) && request === requestGeneration;
    const ids = [...new Set(clockStates.map((state) => state.id).filter(Boolean))];
    if (!ids.length) return;
    syncInFlight = true;
    try {
        let payload, byId;
        try {
            const response = await fetch(`/api/assignments/time-state?ids=${encodeURIComponent(ids.join(','))}`, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' },
            });
            if (!current()) return;
            if (!response.ok) throw new Error(`time-state ${response.status}`);
            payload = await response.json();
            if (!current()) return;
            byId = new Map((payload.assignments || []).map((item) => [String(item.assignment_id), item]));
        } catch (error) {
            if (current()) console.warn('Failed to sync assignment time state:', error);
            return;
        }
        if (!current()) return;
        clockStates.forEach((state) => {
            const update = byId.get(state.id);
            if (update) {
                writeDatasetFromPayload(state, update, payload.server_now);
            }
        });
        tick(generation);
        if (current()) emitStateChange();
    } finally {
        if (current()) {
            syncInFlight = false;
            scheduleSync(generation);
        }
    }
}

function clearOwnerTimers() {
    window.clearInterval(tickTimer); window.clearTimeout(syncTimer);
    tickTimer = 0; syncTimer = 0;
    for (const timer of boundaryTimers) window.clearTimeout(timer);
    boundaryTimers.clear();
}

function initOwnedClocks(options = {}) {
    clearOwnerTimers();
    const generation = ++ownerGeneration;
    requestGeneration++;
    ownerActive = true; syncInFlight = false;
    stateChangeCallback = options.onStateChange || null;
    clockStates = Array.from(document.querySelectorAll('[data-assignment-clock]')).map(readClockState);
    scheduleTick(generation);
    if (isOwner(generation)) emitStateChange();
    scheduleSync(generation);
    return {
        syncNow: () => isOwner(generation) ? syncAssignmentTimeStates() : Promise.resolve(),
        getStates: () => new Map(isOwner(generation) ? clockStates.map((state) => [state.id, state]) : []),
        refresh() {
            if (!isOwner(generation)) return false;
            requestGeneration++; syncInFlight = false;
            const previous = new Map(clockStates.map(state => [state.el, state]));
            clockStates = Array.from(document.querySelectorAll('[data-assignment-clock]')).map(el => {
                const state = previous.get(el);
                return state?.fingerprint === datasetFingerprint(el) ? state : readClockState(el);
            });
            scheduleTick(generation); scheduleSync(generation);
            return true;
        },
        dispose() {
            if (!isOwner(generation)) return;
            ownerActive = false; ownerGeneration++;
            clearOwnerTimers(); clockStates = []; stateChangeCallback = null; syncInFlight = false;
        },
    };
}

function subscribeOwnedClock(element, listener) {
    if (!element || typeof listener !== 'function') throw new TypeError('Clock presentation requires an element and listener');
    let listeners = presentationListeners.get(element);
    if (!listeners) { listeners = new Set(); presentationListeners.set(element, listeners); }
    const record = { listener };
    listeners.add(record);
    let disposed = false;
    const handle = {
        refresh() {
            if (disposed || !ownerActive) return false;
            const snapshot = clockStates.find(state => state.el === element)?.presentation;
            if (snapshot) notifyPresentation(listener, snapshot);
            return Boolean(snapshot);
        },
        dispose() {
            if (disposed) return;
            disposed = true; listeners.delete(record);
            if (!listeners.size) presentationListeners.delete(element);
        },
    };
    handle.refresh();
    return handle;
}

function clockRuntime() {
    // Source/hashed module aliases must never install a second timer owner.
    if (!document[RUNTIME]) Object.defineProperty(document, RUNTIME, {
        configurable: true, value: { init: initOwnedClocks, subscribe: subscribeOwnedClock },
    });
    return document[RUNTIME];
}

export function initAssignmentClocks(options = {}) { return clockRuntime().init(options); }

/** Read-only presentation subscription: never initializes, polls or starts timers. */
export function subscribeAssignmentClock(element, listener) { return clockRuntime().subscribe(element, listener); }
