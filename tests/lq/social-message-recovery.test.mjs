import { expect, it, vi } from 'vitest';
import { recoverMessageWindow } from '../../static/js/social/message_recovery.js';

const rows = (first, last) => Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => ({
    id: first + i, body: `Message ${first + i}`, created_at: '2026-11-05T10:00:00+00:00',
}));
const options = overrides => ({
    cached: rows(1, 10), afterId: 10, hasMore: false,
    page: { items: rows(151, 200), has_more: true }, isCurrent: () => true,
    ...overrides,
});

it('LQ social history fills a gap longer than one latest page without advancing from a WS maximum', async () => {
    const fetchAfter = vi.fn(async (after, through, size) => {
        const end = Math.min(after + size, through);
        return { items: rows(after + 1, end), next_after_id: end, through_id: through, has_more: end < through };
    });
    const recovered = await recoverMessageWindow(options({ cached: [...rows(1, 10), ...rows(250, 250)], fetchAfter }));
    expect(fetchAfter.mock.calls).toEqual([[10, 200, 100], [110, 200, 100]]);
    expect(recovered.items.map(item => item.id)).toEqual([...rows(1, 200), ...rows(250, 250)].map(item => item.id));
    expect(recovered.cursor).toBe(200);
    expect(recovered.hasMore).toBe(false);
    expect(recovered.reset).toBe(false);
});

it('LQ social reconnect bounds request and DOM work for a very large backlog', async () => {
    const fetchAfter = vi.fn(async (after, through, size) => ({
        items: rows(after + 1, after + size), next_after_id: after + size, through_id: through, has_more: true,
    }));
    const latest = rows(9951, 10000);
    const recovered = await recoverMessageWindow(options({ page: { items: latest, has_more: true }, fetchAfter }));
    expect(fetchAfter).toHaveBeenCalledTimes(8);
    expect(recovered).toMatchObject({ items: latest, hasMore: true, cursor: 10000, reset: true });
});

it('LQ social recovery stops after a conversation switch and never writes a late page', async () => {
    let current = true;
    const fetchAfter = vi.fn(async () => {
        current = false;
        return { items: rows(11, 110), next_after_id: 110, through_id: 200, has_more: true };
    });
    expect(await recoverMessageWindow(options({ fetchAfter, isCurrent: () => current }))).toBe(null);
    expect(fetchAfter).toHaveBeenCalledOnce();
});

it('LQ a backend without forward cursors resets to a contiguous latest window', async () => {
    const latest = rows(151, 200);
    const fetchAfter = vi.fn(async () => ({ items: latest, has_more: true }));
    const recovered = await recoverMessageWindow(options({ fetchAfter }));
    expect(recovered.items).toEqual(latest);
    expect(recovered.reset).toBe(true);
    expect(fetchAfter).toHaveBeenCalledOnce();
});

it('LQ authoritative join boundaries retire earlier cached group history', async () => {
    const old = rows(1, 10).map(message => ({ ...message, created_at: '2026-11-01T10:00:00+00:00' }));
    const latest = rows(11, 20);
    const fetchAfter = vi.fn(async () => ({ items: latest, next_after_id: 20, through_id: 20, has_more: false,
        history_since: '2026-11-04T00:00:00+00:00' }));
    const recovered = await recoverMessageWindow(options({ cached: old,
        page: { items: latest, has_more: false, history_since: '2026-11-04T00:00:00+00:00' }, fetchAfter }));
    expect(recovered.items).toEqual(latest);
    expect(recovered.historySince).toBe('2026-11-04T00:00:00+00:00');
});

it('LQ a join boundary changed during catchup also filters the initial HTTP snapshot', async () => {
    const recovered = await recoverMessageWindow(options({ fetchAfter: async (after, through) => ({
        items: [], next_after_id: after, through_id: through, has_more: false,
        history_since: '2026-11-06T00:00:00+00:00',
    }) }));
    expect(recovered.items).toEqual([]);
});

it('LQ revalidated old history carries recall and media state over cached data', async () => {
    const recalled = { ...rows(8, 8)[0], recalled: true, body: '', media: [] };
    const recovered = await recoverMessageWindow(options({ cached: rows(1, 10), afterId: 10,
        page: { items: [recalled, ...rows(9, 10)], has_more: true }, fetchAfter: vi.fn() }));
    expect(recovered.items.find(message => message.id === 8)).toEqual(recalled);
    expect(recovered.cursor).toBe(10);
});

it('LQ an empty initial conversation needs no recovery query', async () => {
    const fetchAfter = vi.fn();
    expect(await recoverMessageWindow(options({ cached: [], afterId: 0,
        page: { items: [], has_more: false }, fetchAfter }))).toMatchObject({ items: [], cursor: 0, hasMore: false, reset: false });
    expect(fetchAfter).not.toHaveBeenCalled();
});
