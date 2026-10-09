/** Display-only formatting. This module never pairs, reschedules or renumbers lessons. */
const text = value => typeof value === 'string' ? value.trim() : '';
const normalizedText = value => text(value).normalize('NFKC').replace(/[–—]/g, '-').replace(/\s+/g, ' ');

/** View-only changes can coexist: an effective move and a later pending move. */
export function scheduleChanges(lesson) {
    const changes = Array.isArray(lesson?.schedule_changes) ? lesson.schedule_changes : [lesson?.adjustment];
    return changes.filter(change => change && (
        ['pending', 'planned'].includes(change.phase) && ['move', 'cancel', 'room'].includes(change.kind) && ['original', 'proposed'].includes(change.endpoint)
        || change.phase === 'draft' && ['move', 'cancel', 'room'].includes(change.kind) && ['original', 'proposed'].includes(change.endpoint)
        || change.phase === 'approved' && change.kind === 'move' && ['original', 'effective'].includes(change.endpoint)
        || change.phase === 'approved' && change.kind === 'room' && change.endpoint === 'effective'
    ));
}

const PHASE_BADGES = {
    draft: { label: '草稿', tone: 'neutral', title: '调课申请草稿，尚未提交教务审核' },
    pending: { label: '审核中', tone: 'warning', title: '调课申请已提交教务，等待审核' },
    planned: { label: '已批准', tone: 'success', title: '申请已批准，正式课表待落实' },
};

/**
 * One compact stage badge per card: draft → 审核中 → 已批准/已生效. A fully
 * effective time change needs no badge (the card already sits at its new
 * position); any approved room change is always marked.
 */
export function scheduleChangeBadge(lesson) {
    const changes = scheduleChanges(lesson);
    if (!changes.length) return null;
    const roomChanged = changes.some(change => change.kind === 'room'
        || classroomChangeState(change.original?.room, change.proposed?.room) === true);
    const effectiveRoom = changes.find(change => change.phase === 'approved' && change.endpoint === 'effective' && roomChanged);
    if (effectiveRoom) return { phase: 'approved', label: '已换教室', tone: 'info', roomChanged: true, title: '更换教室申请已批准并生效' };
    if (lesson?.is_change_history) return { phase: 'approved', label: '原安排', tone: 'neutral', roomChanged: false, title: '已调课的原安排，不计入课时' };
    const stage = ['pending', 'planned', 'draft'].map(phase => changes.find(change => change.phase === phase)).find(Boolean);
    if (!stage) return null;
    const badge = PHASE_BADGES[stage.phase];
    if (stage.phase === 'planned' && stage.endpoint === 'proposed') return { phase: 'planned', label: '计划位置', tone: 'info', roomChanged, title: '已批准调课的计划位置，正式课表待落实' };
    if (stage.phase === 'pending' && stage.endpoint === 'proposed') return { phase: 'pending', label: '拟位置', tone: 'warning', roomChanged, title: '待审申请的拟安排位置' };
    if (stage.phase === 'draft' && stage.endpoint === 'proposed') return { phase: 'draft', label: '草稿位置', tone: 'neutral', roomChanged, title: '申请草稿的拟安排位置，尚未提交教务' };
    return { phase: stage.phase, label: roomChanged && stage.kind !== 'cancel' ? `${badge.label}·换教室` : badge.label, tone: badge.tone, roomChanged, title: badge.title };
}

function classroomCode(value) {
    const normalized = normalizedText(value);
    const candidates = [];
    const mentionedCodes = new Set();
    // A building/room context is required except for a standalone room code.
    // In particular, year numbers, student classes and equipment model numbers
    // are not classroom identifiers merely because they contain letters/digits.
    const pattern = /[A-Za-z]{1,2}\s?\d{2,4}(?:-\d{1,2})?|\d{3,4}(?:-\d{1,2})?/g;
    for (const match of normalized.matchAll(pattern)) {
        const before = normalized.slice(0, match.index), after = normalized.slice(match.index + match[0].length);
        if (/[A-Za-z\d-]$/.test(before) || /^[A-Za-z\d-]/.test(after) || /^\s*(?:班|级|届|学号)/.test(after)) continue;
        const code = match[0].replace(/\s/g, '').toUpperCase();
        mentionedCodes.add(code);
        const building = before.match(/([\p{Script=Han}A-Za-z\d_-]{1,24}(?:楼|大厦|馆))\s*\(?\s*$/u)?.[1] || '';
        const roomSuffix = /^\s*(?:教室|实验室|室)(?=$|[\s)、,;；])/u.test(after);
        const standalone = /^[A-Z]\d{2,4}(?:-\d{1,2})?$/.test(code) && !before.trim() && !after.trim();
        const parenthesized = /^\s*\(\s*$/.test(before) && /^\s*\)/.test(after) && /^[A-Z]\d{2,4}(?:-\d{1,2})?$/.test(code);
        const roomPrefix = /(?:教室|上课地点)\s*[:：]?\s*$/.test(before);
        if (building || roomSuffix || standalone || parenthesized || roomPrefix) candidates.push({ code, building });
    }
    const distinct = new Map(candidates.map(candidate => [`${candidate.building}|${candidate.code}`, candidate]));
    return distinct.size === 1 && mentionedCodes.size === 1 ? [...distinct.values()][0] : null;
}

/** Keep ambiguous/multiple locations unchanged instead of selecting one room. */
export function compactClassroomName(value) {
    const original = text(value);
    const classroom = classroomCode(original);
    return classroom ? `${classroom.code}教室` : original;
}

function normalizedTime(position) {
    if (!position || typeof position !== 'object') return null;
    const date = text(position.date);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
    const parsed = new Date(`${date}T00:00:00Z`);
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) return null;
    if (!Array.isArray(position.sections) || !position.sections.length) return null;
    const validSection = value => (typeof value === 'number' || (typeof value === 'string' && /^\d+$/.test(value)))
        && Number.isSafeInteger(Number(value)) && Number(value) > 0;
    if (!position.sections.every(validSection)) return null;
    return `${date}|${[...new Set(position.sections.map(Number))].sort((a, b) => a - b).join(',')}`;
}

function normalizedRoom(value) {
    const original = normalizedText(value);
    if (!original || /^(?:教室待定|待定|未安排|无|暂无|待分配|-+)$/u.test(original)) return null;
    const classroom = classroomCode(original);
    return { text: original.replace(/\s/g, '').toUpperCase(), ...classroom };
}

function sameRoom(a, b) {
    if (a.code && b.code) {
        if (a.code !== b.code) return false;
        // A supplied short name omits the building. If both are explicit, keep
        // different buildings distinct even when they use the same room number.
        return !a.building || !b.building || a.building === b.building;
    }
    return a.text === b.text;
}

/** True/false describe known locations; null means either location is unknown. */
export function classroomChangeState(from, to) {
    const original = normalizedRoom(from), proposed = normalizedRoom(to);
    return original && proposed ? !sameRoom(original, proposed) : null;
}

/** Compare known original/proposed facts; kind=move alone proves no time change. */
export function adjustmentActionText(lesson, suppliedChange = null) {
    const change = suppliedChange || scheduleChanges(lesson)[0];
    if (!change) return '';
    if (change.phase === 'approved') return change.kind === 'room' ? '已换教室' : change.endpoint === 'original' ? '已调至新位' : '查看原安排';
    if (change.phase === 'draft') return change.kind === 'cancel' ? '停课草稿' : '调课草稿';
    if (change.phase === 'planned') return change.kind === 'cancel' ? '已批准待停课' : change.endpoint === 'original' ? '已批准待落实' : '计划新位置';
    if (change.kind === 'cancel') return '停课';
    const fromTime = normalizedTime(change.original), toTime = normalizedTime(change.proposed);
    const roomChanged = classroomChangeState(change.original?.room, change.proposed?.room);
    // "Only time"/"only room" needs evidence about both dimensions. Missing
    // details retain a neutral pending label instead of inventing a change type.
    if (!fromTime || !toTime || roomChanged === null) return '待审变更';
    const timeChanged = fromTime !== toTime;
    if (timeChanged && roomChanged) return '教室+时间';
    if (timeChanged) return '改时间';
    if (roomChanged) return '改教室';
    return '待审变更';
}


export function scheduleChangeLabel(lesson, suppliedChange = null) {
    const change = suppliedChange || scheduleChanges(lesson)[0];
    if (!change) return '';
    if (change.phase === 'approved') return change.kind === 'room' ? '教室已更换 · 申请已批准' : change.endpoint === 'original' ? '原安排（已调课）' : '调课已生效';
    if (change.phase === 'draft') return change.kind === 'cancel' ? '停课申请草稿 · 未提交'
        : change.endpoint === 'proposed' ? '草稿拟安排 · 未提交' : '调课申请草稿 · 未提交';
    if (change.phase === 'planned') return change.kind === 'cancel' ? '停课已批准·待落实'
        : change.endpoint === 'original' ? '原安排 · 已批准·待落实' : '计划安排 · 已批准·待落实';
    if (change.endpoint === 'proposed') return '正在申请变更';
    return ({ move: '调课待审', cancel: '停课待审', room: '更换教室待审' })[change.kind];
}
