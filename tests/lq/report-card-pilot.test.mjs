import { it, expect } from 'vitest';
import { initReportCardPilot, reportChartOptions, reportChartThemeOptions } from '../../static/js/report_card.js';

it('LQ report card keeps zero, null, aligned series and the original projection untouched', () => {
    const data = { labels: ['A', 'B', 'C'], mine: [80, 0, null], class_avg: [40, 0, null] }, before = JSON.stringify(data);
    const options = reportChartOptions(data);
    expect(options.series[0].data).toEqual([80, 0, null]); expect(options.series[1].lineStyle.type).toBe('dashed');
    options.series[0].data[0] = 90; expect(JSON.stringify(data)).toBe(before);
    for (const value of [null, { ...data, mine: [0] }, { ...data, mine: [0, false, null] }, { ...data, mine: [0, NaN, null] }]) expect(reportChartOptions(value)).toBeNull();
});

it('LQ report card theme patch has no data, formatter or selected-state ownership', () => {
    const tokens = { '--ls-ink-2': '215 20% 75%', '--ls-ink-3': '215 16% 65%', '--ls-line': '215 15% 30%', '--ls-primary': '243 80% 70%', '--ls-surface-1': '215 30% 12%', '--ls-font-sans': 'system-ui' };
    const element = { ownerDocument: { defaultView: { getComputedStyle: () => ({ getPropertyValue: key => tokens[key] }), matchMedia: query => ({ matches: query.includes('reduced-motion') }) } } };
    const options = reportChartThemeOptions(element);
    expect(options.animation).toBe(false); expect(options.textStyle.color).toBe('hsl(215, 20%, 75%)');
    for (const series of options.series) { expect(series).not.toHaveProperty('data'); expect(series).not.toHaveProperty('type'); }
    expect(options.legend).not.toHaveProperty('selected'); expect(options.tooltip).not.toHaveProperty('formatter');
});

function fixture({ fail = false, fallback } = {}) {
    class Target extends EventTarget {
        listeners = new Map();
        addEventListener(type, callback, options) { super.addEventListener(type, callback, options); const set = this.listeners.get(type) || new Set(); set.add(callback); this.listeners.set(type, set); }
        removeEventListener(type, callback, options) { super.removeEventListener(type, callback, typeof options === 'boolean' ? { capture: options } : options); this.listeners.get(type)?.delete(callback); }
        get listenerCount() { return [...this.listeners.values()].reduce((total, set) => total + set.size, 0); }
    }
    const observers = [], frames = new Map(), media = [], allCharts = [], shellCalls = [];
    class Observer {
        constructor(callback) { this.callback = callback; this.connected = false; observers.push(this); }
        observe() { this.connected = true; }
        disconnect() { this.connected = false; }
    }
    const doc = new Target(), win = new Target(), topbar = new Target();
    let paneOpen = true;
    const pane = { matches: () => paneOpen };
    const more = { isConnected: true, getClientRects: () => [{}], closest: () => null, focus() { doc.activeElement = this; } };
    const action = { isConnected: true, getClientRects: () => paneOpen ? [{}] : [], closest: selector => selector === '[data-open-feedback]' ? action : null, focus() { doc.activeElement = this; } };
    const dismiss = { focus() { doc.activeElement = this; } };
    const modal = { hidden: true, shown: false, classList: { contains: () => modal.shown },
        getAttribute: () => String(modal.hidden), contains: node => node === dismiss,
        querySelector: () => dismiss };
    Object.assign(topbar, { contains: node => node === action, querySelector: selector => selector === '[data-lq-pane="actions"]' ? pane : more });
    const element = { dataset: { reportChart: '0' }, hidden: false, ownerDocument: doc };
    const data = [{ labels: ['A', 'B'], mine: [0, null], class_avg: [0, null] }];
    let shellDestroys = 0, paneDestroys = 0, nextFrame = 0;
    Object.assign(win, {
        getComputedStyle: () => ({ getPropertyValue: () => '215 20% 50%' }),
        matchMedia: () => { const query = new Target(); query.matches = false; media.push(query); return query; },
        MutationObserver: Observer, ResizeObserver: Observer,
        requestAnimationFrame: callback => { frames.set(++nextFrame, callback); return nextFrame; },
        cancelAnimationFrame: id => frames.delete(id),
    });
    Object.assign(doc, { defaultView: win, documentElement: { dataset: { lqShellFallback: fallback } }, body: {}, getElementById: () => modal,
        querySelector: selector => selector === '[data-report-chart-data]' ? { textContent: JSON.stringify(data) } : topbar });
    const root = { ownerDocument: doc, isConnected: true, matches: () => true, querySelectorAll: () => [element] };
    const echarts = { init: () => {
        const chart = { disposed: false, optionCalls: 0, resizeCalls: 0, disposeCalls: 0,
            setOption() { this.optionCalls++; if (fail) throw new Error('injected renderer failure'); },
            isDisposed() { return this.disposed; }, resize() { this.resizeCalls++; },
            dispose() { this.disposed = true; this.disposeCalls++; } };
        allCharts.push(chart); return chart;
    } };
    return { root, doc, win, topbar, action, more, modal, dismiss, element, frames, observers, media, allCharts, shellCalls,
        options: { echarts, shellFactory: (node, options) => {
            shellCalls.push({ node, options });
            return { destroy: () => { shellDestroys++; }, openPane: () => ({ destroy() { paneDestroys++; paneOpen = false; more.focus(); } }) };
        } },
        openPane() { paneOpen = true; action.focus(); },
        get paneDestroys() { return paneDestroys; },
        get shellDestroys() { return shellDestroys; },
        get listenerCount() { return doc.listenerCount + win.listenerCount + topbar.listenerCount + media.reduce((total, query) => total + query.listenerCount, 0); },
    };
}

it('LQ report card twenty init/destroy cycles own one chart, cancel frames and release every listener', () => {
    const f = fixture();
    for (let cycle = 0; cycle < 20; cycle++) {
        const controller = initReportCardPilot(f.root, f.options);
        expect(initReportCardPilot(f.root, f.options)).toBe(controller);
        f.win.dispatchEvent(new Event('resize')); f.win.dispatchEvent(new Event('resize'));
        expect(f.frames.size).toBe(1);
        const chart = f.allCharts.at(-1), previous = chart.optionCalls;
        f.doc.dispatchEvent(new Event('lq:theme-change')); expect(chart.optionCalls).toBe(previous + 1);
        controller.destroy(); controller.destroy();
        expect(controller.destroyed).toBe(true); expect(chart.disposeCalls).toBe(1);
        expect(f.frames.size).toBe(0); expect(f.listenerCount).toBe(0);
        expect(f.observers.every(observer => !observer.connected)).toBe(true);
        f.doc.dispatchEvent(new Event('lq:theme-change')); controller.refreshTheme();
        expect(chart.optionCalls).toBe(previous + 1);
    }
    expect(f.allCharts).toHaveLength(20); expect(f.shellDestroys).toBe(20);
});

it('LQ report card explicit inline fallback keeps the existing Shell guard and chart lifecycle', () => {
    for (const fallback of ['inline', undefined, 'false', 'drawer']) {
        const f = fixture({ fallback }), controller = initReportCardPilot(f.root, f.options);
        expect(initReportCardPilot(f.root, f.options)).toBe(controller);
        expect(f.shellCalls).toHaveLength(1);
        expect(f.shellCalls[0]).toEqual({ node: f.topbar,
            options: fallback === 'inline' ? { paneGuards: { actions: { keepOpen: true } } } : undefined });
        expect(f.allCharts).toHaveLength(1); expect(f.element.hidden).toBe(false);
        const chart = f.allCharts[0], before = chart.optionCalls;
        f.doc.dispatchEvent(new Event('lq:theme-change'));
        expect(chart.optionCalls).toBe(before + 1);
        controller.destroy();
        expect(chart.disposeCalls).toBe(1); expect(f.shellDestroys).toBe(1); expect(f.listenerCount).toBe(0);
        // This is an adapter contract test, not a browser geometry/CLS result.
        expect(f.doc.documentElement.dataset.lqShellFallback).toBe(fallback);
    }
});

it('LQ report card hands native feedback off once, returns focus to a visible trigger and preserves its controller', () => {
    const f = fixture(), controller = initReportCardPilot(f.root, f.options);
    let originalOpens = 0;
    const originalClick = () => { originalOpens++; f.modal.hidden = false; f.modal.shown = true; };
    f.doc.addEventListener('click', originalClick);
    for (let cycle = 0; cycle < 20; cycle++) {
        f.openPane();
        const click = new Event('click', { cancelable: true }); Object.defineProperty(click, 'target', { value: f.action });
        f.topbar.dispatchEvent(click);
        expect(click.defaultPrevented).toBe(false); expect(originalOpens).toBe(cycle);
        expect(f.paneDestroys).toBe(cycle + 1);
        f.doc.dispatchEvent(click); expect(originalOpens).toBe(cycle + 1);
        const watcher = f.observers.at(-1); watcher.callback();
        expect(f.doc.activeElement).toBe(f.dismiss);
        f.modal.hidden = true; f.modal.shown = false; watcher.callback();
        expect(f.doc.activeElement).toBe(f.more); expect(watcher.connected).toBe(false);
        const outside = {}; f.doc.activeElement = outside; watcher.callback();
        expect(f.doc.activeElement).toBe(outside);
    }
    f.doc.removeEventListener('click', originalClick); controller.destroy();
    expect(f.listenerCount).toBe(0); expect(f.observers.every(observer => !observer.connected)).toBe(true);
});

it('LQ report card feedback handoff cannot steal later focus or survive page destruction', () => {
    const f = fixture(), controller = initReportCardPilot(f.root, f.options);
    const click = () => { const event = new Event('click'); Object.defineProperty(event, 'target', { value: f.action }); f.topbar.dispatchEvent(event); };
    click(); const watcher = f.observers.at(-1);
    f.modal.hidden = false; f.modal.shown = true; watcher.callback();
    const elsewhere = {}; f.doc.activeElement = elsewhere;
    f.modal.hidden = true; f.modal.shown = false; watcher.callback();
    expect(f.doc.activeElement).toBe(elsewhere);
    // The inline desktop case never interferes with the original feedback click.
    click(); expect(f.paneDestroys).toBe(1);
    f.openPane(); click(); const pending = f.observers.at(-1);
    controller.destroy(); f.modal.hidden = false; f.modal.shown = true; pending.callback();
    expect(f.doc.activeElement).toBe(f.more); expect(pending.connected).toBe(false); expect(f.listenerCount).toBe(0);
});

it('LQ report card preserves bfcache, releases removed pages and hides only a failed decorative plot', () => {
    const f = fixture(), controller = initReportCardPilot(f.root, f.options);
    const hide = new Event('pagehide'); Object.defineProperty(hide, 'persisted', { value: true });
    f.win.dispatchEvent(hide); expect(controller.destroyed).toBe(false);
    f.root.isConnected = false; f.observers[0].callback();
    expect(controller.destroyed).toBe(true); expect(f.listenerCount).toBe(0); expect(f.allCharts[0].disposed).toBe(true);
    const failure = fixture({ fail: true }), failedController = initReportCardPilot(failure.root, failure.options);
    expect(failure.element.hidden).toBe(true); expect(failure.allCharts[0].disposeCalls).toBe(1);
    failedController.destroy(); expect(failure.element.hidden).toBe(false); expect(failure.listenerCount).toBe(0);
    expect(failure.allCharts[0].disposeCalls).toBe(1);
});
