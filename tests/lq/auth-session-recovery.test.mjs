import { expect, it } from 'vitest';
import { initSessionRecovery } from '../../static/js/session_expired.js';

function fixture({ automatic = true, href = 'https://session.test/teacher/login?next=%2Fmanage%3Fview%3Dmine' } = {}) {
    class TrackedTarget extends EventTarget {
        listeners = new Map();
        addEventListener(type, listener, ...rest) {
            const set = this.listeners.get(type) || new Set(); set.add(listener); this.listeners.set(type, set);
            super.addEventListener(type, listener, ...rest);
        }
        removeEventListener(type, listener, ...rest) {
            this.listeners.get(type)?.delete(listener); super.removeEventListener(type, listener, ...rest);
        }
        count() { return [...this.listeners.values()].reduce((n, set) => n + set.size, 0); }
    }
    const link = Object.assign(new TrackedTarget(), { href });
    const alternate = new TrackedTarget(), win = new TrackedTarget();
    const note = { hidden: true, dataset: { autoRedirect: String(automatic) } }, count = { textContent: '5' };
    const timers = new Map(), redirects = [];
    let now = 0, sequence = 0, maximum = 0;
    Object.assign(win, {
        location: { href: 'https://session.test/manage', origin: 'https://session.test', assign: url => redirects.push(url) },
        Date: { now: () => now },
        setTimeout(callback, delay) { timers.set(++sequence, { callback, due: now + delay }); maximum = Math.max(maximum, timers.size); return sequence; },
        clearTimeout(id) { timers.delete(id); },
    });
    const doc = {
        querySelector: selector => selector === '[data-lq-session-login]' ? link : note,
        querySelectorAll: () => [alternate], getElementById: () => count,
    };
    const advance = milliseconds => {
        const end = now + milliseconds;
        while ([...timers.values()].some(timer => timer.due <= end)) {
            const [id, timer] = [...timers].sort((a, b) => a[1].due - b[1].due)[0];
            timers.delete(id); now = timer.due; timer.callback();
        }
        now = end;
    };
    return { doc, win, link, alternate, note, count, timers, redirects, advance,
        late: () => {
            const [id, timer] = [...timers][0];
            return () => { timers.delete(id); timer.callback(); };
        },
        jump: milliseconds => { now += milliseconds; },
        maximum: () => maximum,
        listeners: () => link.count() + alternate.count() + win.count() };
}

it('LQ recovery countdown opts in only after installing a valid same-origin login destination', () => {
    for (const options of [{ automatic: false }, { href: 'https://outside.test/student/login' }, { href: 'https://session.test/dashboard' }]) {
        const f = fixture(options);
        expect(initSessionRecovery(f.doc, f.win)).toBe(null);
        expect(f.note.hidden).toBe(true); expect(f.timers.size).toBe(0); expect(f.listeners()).toBe(0);
    }
});

it('LQ recovery counts once and follows precisely the server anchor with its protected query', () => {
    const f = fixture(), destroy = initSessionRecovery(f.doc, f.win);
    expect(f.note.hidden).toBe(false); expect(f.count.textContent).toBe('5');
    expect(initSessionRecovery(f.doc, f.win)).toBe(destroy);
    expect(f.listeners()).toBe(3); expect(f.timers.size).toBe(1);
    f.advance(4999); expect(f.count.textContent).toBe('1'); expect(f.redirects).toEqual([]);
    f.advance(1); expect(f.redirects).toEqual([f.link.href]);
    expect(f.timers.size).toBe(0); expect(f.listeners()).toBe(0); expect(f.note.hidden).toBe(true);
    f.advance(20000); expect(f.redirects).toHaveLength(1); expect(f.maximum()).toBe(1);
});

for (const target of ['link', 'alternate']) it(`LQ recovery manual ${target} navigation cancels even a queued late tick`, () => {
    const f = fixture(); initSessionRecovery(f.doc, f.win);
    const late = f.late(); f[target].dispatchEvent(new Event('click'));
    f.jump(9000); late();
    expect(f.redirects).toEqual([]); expect(f.note.hidden).toBe(true);
    expect(f.timers.size).toBe(0); expect(f.listeners()).toBe(0);
});

it('LQ recovery pagehide clears timer and listeners, and forced destroy is idempotent', () => {
    const f = fixture(), destroy = initSessionRecovery(f.doc, f.win), late = f.late();
    f.win.dispatchEvent(new Event('pagehide')); destroy(); destroy();
    f.jump(6000); late();
    expect(f.timers.size).toBe(0); expect(f.listeners()).toBe(0); expect(f.redirects).toEqual([]);
    expect(f.note.hidden).toBe(true);
});

it('LQ recovery delayed execution uses elapsed time rather than promising five extra ticks', () => {
    const f = fixture(); initSessionRecovery(f.doc, f.win);
    const late = f.late(); f.jump(8000); late();
    expect(f.redirects).toEqual([f.link.href]); expect(f.listeners()).toBe(0); expect(f.timers.size).toBe(0);
    late(); expect(f.redirects).toHaveLength(1);
});
