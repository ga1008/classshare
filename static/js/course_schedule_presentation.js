/** Display-only formatting. This module never pairs, reschedules or renumbers lessons. */
const text = value => typeof value === 'string' ? value.trim() : '';
const normalizedText = value => text(value).normalize('NFKC').replace(/[–—]/g, '-').replace(/\s+/g, ' ');

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
export function adjustmentActionText(lesson) {
    const change = lesson?.adjustment;
    if (change?.phase !== 'pending') return '';
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
