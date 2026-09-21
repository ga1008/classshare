import { afterEach, describe, expect, it, vi } from 'vitest';
import { createToastClock, toastProps } from '../../static/js/lq/toast.js';

afterEach(() => vi.useRealTimers());
describe('LQ toast contract', () => {
    it('keeps long text intact and defaults to polite info and 3000ms', () => {
        expect(toastProps(' 消息 '.repeat(100))).toMatchObject({ message: ' 消息 '.repeat(100).trim(), tone: 'info', duration: 3000 });
        expect(toastProps('<script>unsafe as text</script>')).toMatchObject({ icon: 'info', key: null });
    });
    it.each([{ tone: 'error' }, { duration: -1 }, { duration: NaN }, { duration: '3000' }, { duration: 2147483648 }, { closeLabel: '' }, { onClose: 'code()' }, { key: false }])('rejects invalid option %j', options => {
        expect(() => toastProps('消息', options)).toThrow();
    });
    it.each([{ label: '' }, { label: '打开' }, { label: '打开', href: 'javascript:bad()' }, { label: '打开', href: '//outside' }, { label: '打开', href: '/safe', onClick() {} }, { label: '操作', onClick: 'code()' }])('requires one named safe action %j', action => {
        expect(() => toastProps('消息', { action })).toThrow();
    });
    it('preserves safe link or callback identity for deduplication', () => {
        const callback = () => {};
        expect(toastProps('消息', { action: { label: '打开', href: '/safe?x=1&y=2' } }).action.href).toBe('/safe?x=1&y=2');
        expect(toastProps('消息', { action: { label: '操作', onClick: callback } }).action.onClick).toBe(callback);
    });
    it('subtracts elapsed time and independently pauses hover, focus and hidden owners', () => {
        vi.useFakeTimers(); const expired = vi.fn(); const clock = createToastClock(expired, { now: () => Date.now() });
        clock.reset(1000); vi.advanceTimersByTime(250); clock.pause('hover'); clock.pause('focus');
        expect(clock.remaining).toBe(750); vi.advanceTimersByTime(5000); clock.resume('hover');
        vi.advanceTimersByTime(5000); expect(expired).not.toHaveBeenCalled();
        clock.pause('hidden'); clock.resume('focus'); vi.advanceTimersByTime(2000); clock.resume('hidden');
        vi.advanceTimersByTime(749); expect(expired).not.toHaveBeenCalled(); vi.advanceTimersByTime(1); expect(expired).toHaveBeenCalledTimes(1);
    });
    it('updates while paused, supports sticky zero and destroys all scheduled work', () => {
        vi.useFakeTimers(); const expired = vi.fn(); const clock = createToastClock(expired, { now: () => Date.now() });
        clock.reset(1000); clock.pause('focus'); clock.reset(2000); vi.advanceTimersByTime(5000); clock.resume('focus');
        vi.advanceTimersByTime(1999); expect(expired).not.toHaveBeenCalled();
        clock.reset(0); vi.advanceTimersByTime(99999); expect(expired).not.toHaveBeenCalled();
        clock.reset(20); clock.destroy(); clock.resume('focus'); clock.reset(1); vi.advanceTimersByTime(100);
        expect(expired).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0);
    });
});
