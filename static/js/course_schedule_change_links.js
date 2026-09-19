/** Pair only the explicit pending endpoints supplied by the academic snapshot. */
import { classroomChangeState } from './course_schedule_presentation.js?v=schedule-glass-20260920';

const text = value => typeof value === 'string' ? value.trim() : '';
const positiveInteger = value => (typeof value === 'number' || (typeof value === 'string' && /^\d+$/.test(value)))
    && Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;

function sections(value) {
    if (!Array.isArray(value) || !value.length || value.some(item => !positiveInteger(item))) return null;
    return [...new Set(value.map(Number))].sort((a, b) => a - b);
}

function slot(value) {
    if (!value || typeof value !== 'object') return null;
    const date = text(value.date);
    const periods = sections(value.sections);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !periods) return null;
    const parsed = new Date(`${date}T00:00:00Z`);
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) return null;
    return { date, sections: periods, room: text(value.room) };
}

const sameTime = (a, b) => a.date === b.date && a.sections.join(',') === b.sections.join(',');
const sameSlot = (a, b) => sameTime(a, b) && a.room === b.room;
const slotText = value => `${value.date} 第${value.sections.join('、')}节${value.room ? ` · ${value.room}` : ''}`;
const sameKnownIdentity = (a, b, key) => a[key] == null || a[key] === '' || b[key] == null || b[key] === '' || String(a[key]) === String(b[key]);

function pending(lesson) {
    const change = lesson?.adjustment;
    return change?.phase === 'pending' && text(change.request_id)
        && ['original', 'proposed'].includes(change.endpoint) ? change : null;
}

function weekMatches(value, week) {
    return value == null || value === '' || positiveInteger(value) === week;
}

/**
 * Each move is directed from its original time to its proposed time, even when
 * that means travelling to an earlier day/week. A filtered-out counterpart is
 * not a page boundary. Room-only requests retain their card comparison but do
 * not produce an arrow, because no lesson moves to another time.
 */
export function scheduleChangeConnections(overview, week) {
    const currentWeek = positiveInteger(week?.week_index);
    if (!currentWeek || !Array.isArray(week?.lessons) || !Array.isArray(overview?.weeks)) return [];
    const byKey = new Map();
    for (const row of overview.weeks) {
        const weekIndex = positiveInteger(row?.week_index);
        if (!weekIndex) continue;
        for (const lesson of row.lessons || []) {
            const key = text(lesson?.event_key);
            if (!key) continue;
            // Duplicate identities must never select an arbitrary lesson.
            byKey.set(key, byKey.has(key) ? null : { lesson, weekIndex });
        }
    }
    const visible = new Set();
    const duplicateVisible = new Set();
    for (const lesson of week.lessons) {
        const key = text(lesson?.event_key);
        if (!key) continue;
        if (visible.has(key)) duplicateVisible.add(key);
        visible.add(key);
    }
    for (const key of duplicateVisible) visible.delete(key);

    const connections = [];
    const emitted = new Set();
    for (const visibleKey of visible) {
        const entry = byKey.get(visibleKey);
        if (!entry || entry.weekIndex !== currentWeek) continue;
        const change = pending(entry.lesson);
        if (!change || change.kind !== 'move') continue;

        const counterpartKey = text(change.counterpart_event_key);
        const other = byKey.get(counterpartKey);
        const counterpart = pending(other?.lesson);
        if (!counterpartKey || counterpartKey === visibleKey || !other || !counterpart || counterpart.kind !== 'move'
            || counterpart.endpoint === change.endpoint || text(counterpart.counterpart_event_key) !== visibleKey
            || text(counterpart.request_id) !== text(change.request_id)
            || !weekMatches(change.counterpart_week_index, other.weekIndex)
            || !weekMatches(counterpart.counterpart_week_index, entry.weekIndex)
            || !sameKnownIdentity(entry.lesson, other.lesson, 'session_id')
            || !sameKnownIdentity(entry.lesson, other.lesson, 'class_offering_id')) continue;

        const originalEntry = change.endpoint === 'original' ? entry : other;
        const proposedEntry = change.endpoint === 'proposed' ? entry : other;
        const originalKey = text(originalEntry.lesson.event_key), proposedKey = text(proposedEntry.lesson.event_key);
        const originalChange = originalEntry.lesson.adjustment, proposedChange = proposedEntry.lesson.adjustment;
        const original = slot(originalChange.original), proposed = slot(originalChange.proposed);
        const otherOriginal = slot(proposedChange.original), otherProposed = slot(proposedChange.proposed);
        if (!original || !proposed || !otherOriginal || !otherProposed
            || !sameSlot(original, otherOriginal) || !sameSlot(proposed, otherProposed) || sameTime(original, proposed)) continue;
        const connectionKey = JSON.stringify([text(originalChange.request_id), originalKey]);
        if (emitted.has(connectionKey)) continue;

        const sameWeek = originalEntry.weekIndex === proposedEntry.weekIndex;
        if (sameWeek && (!visible.has(originalKey) || !visible.has(proposedKey))) continue;
        const outgoing = entry.lesson === originalEntry.lesson;
        const remoteWeek = outgoing ? proposedEntry.weekIndex : originalEntry.weekIndex;
        const roomChanged = classroomChangeState(original.room, proposed.room) === true;
        connections.push({
            key: connectionKey,
            sourceKey: sameWeek || outgoing ? originalKey : null,
            targetKey: sameWeek || !outgoing ? proposedKey : null,
            direction: sameWeek ? 'local' : outgoing ? 'outgoing' : 'incoming',
            edge: sameWeek ? null : remoteWeek < currentWeek ? 'left' : 'right',
            label: roomChanged ? '时间更改 · 教室更改' : '时间更改',
            boundaryLabel: sameWeek ? '' : `${outgoing ? '至' : '来自'}第${remoteWeek}周`,
            title: `${slotText(original)} → ${slotText(proposed)}`,
            jumpKey: sameWeek || outgoing ? proposedKey : originalKey,
            jumpWeek: sameWeek ? currentWeek : remoteWeek,
            courseName: text(originalEntry.lesson.course_name),
        });
        emitted.add(connectionKey);
    }
    return connections;
}

// Independent line colors: neither course-card hues nor request hashes select
// these colors. The first changes get separated, dark colors on a white canvas.
const CHANGE_COLORS = [
    '#b91c1c', '#047857', '#a16207', '#a21caf', '#0e7490',
    '#4338ca', '#9a3412', '#be185d', '#4d7c0f', '#0369a1',
    '#7e22ce', '#115e59', '#92400e', '#9f1239', '#1d4ed8',
    '#3f6212', '#86198f', '#0f766e', '#7f1d1d', '#334155',
];

function generatedColor(index) {
    // Golden-angle stepping separates consecutive hues. Saturation/lightness
    // bands add more choices once a busy semester exceeds the initial palette.
    const hue = (index * 137.508) % 360;
    const saturation = (64 + Math.floor(index / 12) % 4 * 7) / 100;
    const lightness = (30 + Math.floor(index / 48) % 4 * 4) / 100;
    const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
    const x = chroma * (1 - Math.abs(hue / 60 % 2 - 1));
    const base = lightness - chroma / 2;
    const channels = hue < 60 ? [chroma, x, 0] : hue < 120 ? [x, chroma, 0]
        : hue < 180 ? [0, chroma, x] : hue < 240 ? [0, x, chroma]
            : hue < 300 ? [x, 0, chroma] : [chroma, 0, x];
    return `#${channels.map(channel => Math.round((channel + base) * 255).toString(16).padStart(2, '0')).join('')}`;
}

function whiteContrast(color) {
    const channels = [1, 3, 5].map(offset => parseInt(color.slice(offset, offset + 2), 16) / 255)
        .map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
    return 1.05 / (0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2] + 0.05);
}

/**
 * Allocate one distinct color per complete pending move across all supplied
 * weeks. Supply the semester's prior map on refresh/filtering; old assignments
 * are retained so removing/reordering a pair cannot recolor another one. The
 * caller owns that semester-scoped cache; this function never mutates it.
 */
export function scheduleChangeColors(overview, previous = new Map()) {
    const colors = new Map();
    const occupied = new Set();
    const byKey = ([a], [b]) => a < b ? -1 : a > b ? 1 : 0;
    for (const [key, candidate] of [...previous].sort(byKey)) {
        const color = text(candidate).toLowerCase();
        if (!text(key) || !/^#[\da-f]{6}$/.test(color) || occupied.has(color)) continue;
        colors.set(key, color);
        occupied.add(color);
    }
    const keys = new Set();
    for (const week of overview?.weeks || []) {
        for (const connection of scheduleChangeConnections(overview, week)) keys.add(connection.key);
    }
    let paletteIndex = 0, generatedIndex = 0;
    for (const key of [...keys].sort()) {
        if (colors.has(key)) continue;
        let color;
        do {
            color = paletteIndex < CHANGE_COLORS.length ? CHANGE_COLORS[paletteIndex++] : generatedColor(generatedIndex++);
        } while (occupied.has(color) || whiteContrast(color) < 4.5);
        colors.set(key, color);
        occupied.add(color);
    }
    return colors;
}
