import { afterEach, describe, expect, it, vi } from 'vitest';
import { findPopoverParent, getPopoverPosition } from '../../static/js/ui_popover.js';
import { cancelOverlayMotion, setOverlayOpen } from '../../static/js/ui_overlay_motion.js';

afterEach(() => vi.useRealTimers());

describe('shared layer geometry preserves old popover placement', () => {
    const base = { anchor: { left: 140, right: 220, top: 200, bottom: 230 }, panel: { width: 120, height: 90 }, width: 500, height: 400 };
    it('keeps bottom-start, bottom-end, viewport clipping and upward flip', () => {
        expect(getPopoverPosition(base)).toEqual({ left: 140, top: 238, flipped: false });
        expect(getPopoverPosition({ ...base, placement: 'bottom-end' })).toEqual({ left: 100, top: 238, flipped: false });
        expect(getPopoverPosition({ ...base, height: 300 })).toEqual({ left: 140, top: 102, flipped: true });
        expect(getPopoverPosition({ ...base, width: 180 })).toEqual({ left: 48, top: 238, flipped: false });
    });
    it('shares four-direction positioning without creating another layer manager', () => {
        expect(getPopoverPosition({ ...base, placement: 'top-end' })).toEqual({ left: 100, top: 102, flipped: false });
        expect(getPopoverPosition({ ...base, placement: 'left-start' })).toEqual({ left: 12, top: 200, flipped: false });
        expect(getPopoverPosition({ ...base, placement: 'right-end', width: 330 })).toEqual({ left: 12, top: 140, flipped: true });
    });
    it('resolves the nearest owning ancestor and supports panel/root facades', () => {
        const anchor = {}; const parent = { panel: { contains: value => value === anchor } }; const nested = { panel: { contains: value => value === anchor } };
        expect(findPopoverParent([parent, nested], anchor)).toBe(nested);
        expect(findPopoverParent([parent], null)).toBeNull();
        expect(findPopoverParent([{ root: parent.panel }], anchor, item => item.root)?.root).toBe(parent.panel);
    });
});

describe('layer forced destruction cancels shared presence resources', () => {
    function fixture() {
        const listeners = new Set();
        const motion = { matches: false, addEventListener: (_, listener) => listeners.add(listener), removeEventListener: (_, listener) => listeners.delete(listener) };
        const root = { hidden: false, dataset: {}, querySelectorAll: () => [], ownerDocument: { defaultView: {
            matchMedia: () => motion, getComputedStyle: () => ({ transitionDuration: '60s', transitionDelay: '0s', animationDuration: '0s', animationDelay: '0s', animationIterationCount: '1' }),
        } } };
        return { root, listeners };
    }
    it('settles cancelled close false, removes its timer/media listener and never hides later', async () => {
        vi.useFakeTimers(); const { root, listeners } = fixture();
        const pending = setOverlayOpen(root, false);
        expect(listeners.size).toBe(1); expect(vi.getTimerCount()).toBe(1);
        cancelOverlayMotion(root); cancelOverlayMotion(root);
        expect(await pending).toBe(false); expect(listeners.size).toBe(0); expect(vi.getTimerCount()).toBe(0);
        await vi.advanceTimersByTimeAsync(61000); expect(root.hidden).toBe(false);
    });
    it('a new generation remains independent after cancellation and a late animation resolution', async () => {
        vi.useFakeTimers(); const { root, listeners } = fixture(); let finish;
        root.getAnimations = () => [{ effect: { getComputedTiming: () => ({ endTime: 60000 }) }, playState: 'running', finished: new Promise(resolve => { finish = resolve; }) }];
        const old = setOverlayOpen(root, false); cancelOverlayMotion(root);
        const late = finish; const next = setOverlayOpen(root, true); late();
        expect(await old).toBe(false); await Promise.resolve(); await Promise.resolve();
        expect(root.hidden).toBe(false); expect(listeners.size).toBe(1);
        cancelOverlayMotion(root); expect(await next).toBe(false); expect(vi.getTimerCount()).toBe(0);
    });
});
