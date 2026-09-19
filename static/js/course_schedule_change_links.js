/** Pair only the explicit pending endpoints supplied by the academic snapshot. */
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
 * not a page boundary. Room-only requests retain one actual lesson card.
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
        if (!change || !['move', 'room'].includes(change.kind)) continue;

        if (change.kind === 'room') {
            const original = slot(change.original), proposed = slot(change.proposed);
            if (change.endpoint !== 'original' || change.counterpart_event_key || !original || !proposed
                || !sameTime(original, proposed) || !original.room || !proposed.room || original.room === proposed.room) continue;
            connections.push({
                key: JSON.stringify([text(change.request_id), visibleKey]),
                sourceKey: visibleKey, targetKey: visibleKey, direction: 'room', edge: null,
                label: '教室更改', boundaryLabel: '',
                title: `${slotText(original)} → ${slotText(proposed)}`,
                jumpKey: visibleKey, jumpWeek: currentWeek, courseName: text(entry.lesson.course_name),
            });
            continue;
        }

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
        const roomChanged = original.room && proposed.room && original.room !== proposed.room;
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
