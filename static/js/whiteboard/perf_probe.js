/**
 * 性能埋点。默认完全关闭：`createPerfProbe()` 返回 null，调用点全是 `this.probe?.xxx()`，
 * 关掉时连一次属性查找都不会发生。
 *
 * 打开方式（二选一）：
 *   - 地址栏加 `?wbperf=1`
 *   - 控制台 `localStorage.setItem('teacher-whiteboard-perf-probe', '1')` 后刷新
 * 报告：控制台 `teacherWhiteboard.perfReport()`，或关闭白板时自动打印一次。
 *
 * 指标对应 docs/whiteboard-performance-2026-09-09.md 第 5.2 节的门槛。
 */

export const PROBE_FLAG = 'teacher-whiteboard-perf-probe';

export function probeEnabled() {
    try {
        if (typeof location !== 'undefined' && /(?:^|[?&])wbperf=1(?:&|$)/.test(location.search)) return true;
        return window.localStorage.getItem(PROBE_FLAG) === '1';
    } catch {
        return false;
    }
}

/** 分位数（线性插值），用于帧间隔的 P95。 */
export function percentile(values, ratio) {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const position = (sorted.length - 1) * ratio;
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    if (lower === upper) return sorted[lower];
    return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}

export function summarize(samples) {
    if (!samples.length) return { frames: 0, p50: 0, p95: 0, worst: 0, fpsP95: 0 };
    const p95 = percentile(samples, 0.95);
    return {
        frames: samples.length,
        p50: Number(percentile(samples, 0.5).toFixed(2)),
        p95: Number(p95.toFixed(2)),
        worst: Number(Math.max(...samples).toFixed(2)),
        fpsP95: Number((1000 / Math.max(p95, 0.001)).toFixed(1)),
    };
}

export function createPerfProbe() {
    if (!probeEnabled()) return null;

    const counters = { render: 0, rebuild: 0, blit: 0, patch: 0, commit: 0, repaintRegion: 0 };
    let frameGaps = [];
    let longTasks = [];
    let observer = null;
    let rafHandle = null;
    let lastFrameAt = 0;

    const tick = (now) => {
        if (lastFrameAt) frameGaps.push(now - lastFrameAt);
        lastFrameAt = now;
        rafHandle = window.requestAnimationFrame(tick);
    };

    return {
        count(name) {
            if (name in counters) counters[name] += 1;
        },

        start() {
            if (rafHandle !== null) return;
            frameGaps = [];
            longTasks = [];
            lastFrameAt = 0;
            for (const key of Object.keys(counters)) counters[key] = 0;
            rafHandle = window.requestAnimationFrame(tick);
            try {
                observer = new PerformanceObserver((list) => {
                    for (const entry of list.getEntries()) longTasks.push(entry.duration);
                });
                observer.observe({ entryTypes: ['longtask'] });
            } catch {
                observer = null; // Safari 等不支持 longtask，其余指标照常
            }
        },

        stop() {
            if (rafHandle !== null) window.cancelAnimationFrame(rafHandle);
            rafHandle = null;
            observer?.disconnect();
            observer = null;
        },

        report(label = '讲课白板性能') {
            const summary = summarize(frameGaps);
            const heapBytes = window.performance?.memory?.usedJSHeapSize || 0;
            const data = {
                ...summary,
                长任务数: longTasks.length,
                长任务最长ms: longTasks.length ? Number(Math.max(...longTasks).toFixed(1)) : 0,
                长任务合计ms: Number(longTasks.reduce((sum, value) => sum + value, 0).toFixed(1)),
                堆峰值MB: heapBytes ? Number((heapBytes / 1024 / 1024).toFixed(1)) : '不可用',
                ...counters,
            };
            // eslint-disable-next-line no-console
            console.table({ [label]: data });
            return data;
        },
    };
}
