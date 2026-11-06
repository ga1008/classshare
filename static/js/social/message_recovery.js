const MAX_RECOVERY_PAGES = 8;
const HISTORY_PAGE_SIZE = 100;

/** Fill an HTTP-verified gap without advancing a cursor from socket arrivals. */
export async function recoverMessageWindow({ cached, page, afterId, hasMore, fetchAfter, isCurrent }) {
    const latest = page.items || [];
    const through = Number(latest.at(-1)?.id) || Number(afterId) || 0;
    let historySince = page.history_since || null;
    const allowed = message => !historySince || Date.parse(message.created_at) >= Date.parse(historySince);
    const fallback = () => ({
        items: latest.filter(allowed), hasMore: Boolean(page.has_more), cursor: through, reset: true, historySince,
    });
    const recovered = [];
    let cursor = Number(afterId) || 0;
    if (cursor > 0 && through > cursor) {
        for (let count = 0; count < MAX_RECOVERY_PAGES; count++) {
            if (!isCurrent()) return null;
            const part = await fetchAfter(cursor, through, HISTORY_PAGE_SIZE);
            if (!isCurrent()) return null;
            if (part.history_since) historySince = part.history_since;
            const items = part.items || [];
            // During a rolling release an older backend may ignore the new
            // cursor. Retire separated windows instead of presenting a gap.
            const next = Number(part.next_after_id);
            if (Number(part.through_id) !== through || !Number.isFinite(next) || next < cursor
                || items.some(item => Number(item.id) <= cursor || Number(item.id) > through)
                || (items.length && next !== Number(items.at(-1).id))) return fallback();
            recovered.push(...items);
            if (!part.has_more) break;
            if (next <= cursor || count === MAX_RECOVERY_PAGES - 1) return fallback();
            cursor = next;
        }
    }
    const previous = cached.filter(allowed);
    const first = Number(latest[0]?.id) || Infinity;
    const retainedOlder = previous.some(message => Number(message.id) < first);
    const merged = new Map([...previous, ...latest, ...recovered].filter(allowed).map(message => [message.id, message]));
    return {
        items: [...merged.values()].sort((a, b) => a.id - b.id),
        hasMore: retainedOlder ? Boolean(hasMore) : Boolean(page.has_more), cursor: through, reset: false, historySince,
    };
}
