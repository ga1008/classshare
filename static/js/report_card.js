import { enhanceShell } from './lq/shells.js';
import { enhanceNavbarShell } from './lq/navbar-shell.js';

const OWNER = Symbol.for('lanshare.report-card.pilot');
const SERIES = ['report-mine', 'report-class-average'];

/** Theme only: preserve the effective-score projection, null gaps and legend state. */
export function reportChartThemeOptions(element) {
    const doc = element.ownerDocument, win = doc.defaultView, style = win.getComputedStyle(element);
    const color = token => `hsl(${style.getPropertyValue(token).trim().split(/\s+/).join(', ')})`;
    const forced = win.matchMedia('(forced-colors: active)').matches;
    const systemColor = name => {
        const probe = doc.createElement('span'); probe.style.color = name; doc.body.append(probe);
        const value = win.getComputedStyle(probe).color; probe.remove(); return value;
    };
    const ink = forced ? systemColor('CanvasText') : color('--ls-ink-2');
    const muted = forced ? ink : color('--ls-ink-3');
    const line = forced ? ink : color('--ls-line');
    const primary = forced ? ink : color('--ls-primary');
    const background = forced ? systemColor('Canvas') : color('--ls-surface-1');
    const axis = () => ({ axisLabel: { color: ink }, nameTextStyle: { color: ink }, axisLine: { lineStyle: { color: line } }, axisTick: { lineStyle: { color: line } }, splitLine: { lineStyle: { color: line } } });
    return {
        animation: !forced && !win.matchMedia('(prefers-reduced-motion: reduce)').matches,
        textStyle: { color: ink, fontFamily: style.getPropertyValue('--ls-font-sans').trim() },
        legend: { textStyle: { color: ink } },
        tooltip: { backgroundColor: background, borderColor: line, textStyle: { color: ink } },
        xAxis: axis(), yAxis: axis(),
        series: [{ id: SERIES[0], lineStyle: { color: primary }, itemStyle: { color: primary } },
            { id: SERIES[1], lineStyle: { color: muted }, itemStyle: { color: muted } }],
    };
}

export function reportChartOptions(data) {
    if (!data || !Array.isArray(data.labels) || data.labels.length < 2 || !['mine', 'class_avg'].every(key =>
        Array.isArray(data[key]) && data[key].length === data.labels.length && data[key].every(value => value === null || typeof value === 'number' && Number.isFinite(value)))) return null;
    return {
        grid: { left: 36, right: 12, top: 28, bottom: 24 }, tooltip: { trigger: 'axis' },
        legend: { data: ['我的分数', '班级平均'], top: 0, textStyle: { fontSize: 11 } },
        xAxis: { type: 'category', data: [...data.labels], axisLabel: { fontSize: 10 } },
        yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
        series: [
            { id: SERIES[0], name: '我的分数', type: 'line', data: [...data.mine], smooth: true, lineStyle: { width: 2.5 } },
            { id: SERIES[1], name: '班级平均', type: 'line', data: [...data.class_avg], smooth: true, lineStyle: { type: 'dashed' } },
        ],
    };
}

/** Own only this page's chart and Shell resources, never grades or preferences. */
export function initReportCardPilot(root, { echarts = root?.ownerDocument?.defaultView?.echarts, shellFactory = enhanceShell } = {}) {
    if (root?.[OWNER]) return root[OWNER];
    if (!root?.matches?.('[data-lq-report-card]') || !root.isConnected) throw new TypeError('A connected report-card pilot is required');
    const doc = root.ownerDocument, win = doc.defaultView, charts = [], hidden = new Map();
    let destroyed = false, resizeFrame = 0;
    const topbar = doc.querySelector('[data-lq-report-card-topbar]');
    const shell = enhanceNavbarShell(topbar, { shellFactory, document: doc });
    let data = [];
    try { data = JSON.parse(doc.querySelector('[data-report-chart-data]')?.textContent || '[]'); } catch { /* SSR records remain the accessible fallback. */ }
    if (!Array.isArray(data)) data = [];
    for (const element of root.querySelectorAll('[data-report-chart]')) {
        const index = Number(element.dataset.reportChart), options = Number.isInteger(index) ? reportChartOptions(data[index]) : null;
        if (!options || !echarts?.init) { hidden.set(element, element.hidden); element.hidden = true; continue; }
        let chart;
        try {
            chart = echarts.getInstanceByDom?.(element) || echarts.init(element);
            chart.setOption(options); chart.setOption(reportChartThemeOptions(element));
            charts.push({ element, chart });
        } catch {
            // A failed decorative plot must not retain a renderer or hide the
            // server-rendered score records that provide its accessible data.
            if (chart && !chart.isDisposed?.()) chart.dispose();
            hidden.set(element, element.hidden); element.hidden = true;
        }
    }
    const refreshTheme = () => {
        if (destroyed) return;
        for (const { element, chart } of charts) if (!chart.isDisposed?.()) chart.setOption(reportChartThemeOptions(element));
    };
    const resize = () => {
        if (destroyed || resizeFrame) return;
        resizeFrame = win.requestAnimationFrame(() => { resizeFrame = 0; if (!destroyed) for (const { chart } of charts) if (!chart.isDisposed?.()) chart.resize(); });
    };
    const media = [win.matchMedia('(prefers-reduced-motion: reduce)'), win.matchMedia('(forced-colors: active)')];
    for (const query of media) query.addEventListener('change', refreshTheme);
    const onPageHide = event => { if (!event.persisted) destroy(); };
    const onPageShow = () => { refreshTheme(); resize(); };
    const observer = new win.MutationObserver(() => { if (!root.isConnected) destroy(); });
    const sizeObserver = new win.ResizeObserver(resize);
    function destroy() {
        if (destroyed) return; destroyed = true;
        observer.disconnect(); sizeObserver.disconnect();
        if (resizeFrame) win.cancelAnimationFrame(resizeFrame); resizeFrame = 0;
        doc.removeEventListener('lq:theme-change', refreshTheme); win.removeEventListener('resize', resize);
        win.removeEventListener('pagehide', onPageHide); win.removeEventListener('pageshow', onPageShow);
        for (const query of media) query.removeEventListener('change', refreshTheme);
        shell?.destroy();
        for (const { chart } of charts) if (!chart.isDisposed?.()) chart.dispose();
        for (const [element, value] of hidden) element.hidden = value;
        if (root[OWNER] === handle) delete root[OWNER];
    }
    const handle = { refreshTheme, destroy, get destroyed() { return destroyed; } };
    root[OWNER] = handle;
    doc.addEventListener('lq:theme-change', refreshTheme); win.addEventListener('resize', resize, { passive: true });
    win.addEventListener('pagehide', onPageHide); win.addEventListener('pageshow', onPageShow);
    observer.observe(doc.documentElement, { childList: true, subtree: true });
    for (const { element } of charts) sizeObserver.observe(element);
    return handle;
}

if (typeof document !== 'undefined') {
    const root = document.querySelector('[data-lq-report-card]');
    if (root) initReportCardPilot(root);
}
