/**
 * 性能档位。
 *
 * 讲台机的差异很大：有独显的一体机和一台八年前的集显小主机跑的是同一套代码。
 * 这里把「画质 / 特效 / 保真度」和「流畅度」之间的取舍集中成两档，
 * 默认按设备探测自动选，用户也可以在背景浮窗里手动锁定。
 *
 * 档位是**每台设备**的选择（存 localStorage，不跨设备同步）—— 慢的是讲台机而不是这个人，
 * 把它同步到老师的笔记本上反而是错的。
 */

export const PERF_MODES = Object.freeze(['auto', 'full', 'lite']);

export const PERF_MODE_LABELS = Object.freeze({
    auto: '自动',
    full: '高画质',
    lite: '流畅',
});

export const PROFILES = Object.freeze({
    full: Object.freeze({
        maxDpr: 1.75,
        glass: true,
        minorGrid: true,
        softEraser: true,
        undoLimit: 80,
        simplifyTolerance: 0.5,
        settleMs: 140,
        syncIntervalMs: 30_000,
    }),
    lite: Object.freeze({
        maxDpr: 1.25,
        glass: false,
        minorGrid: false,
        softEraser: false,
        undoLimit: 40,
        simplifyTolerance: 1.2,
        settleMs: 300,
        syncIntervalMs: 120_000,
    }),
});

/**
 * 设备探测。两个信号都不可靠也不精确，但足以把「明显跑不动的机器」挑出来：
 * 逻辑核心 ≤ 4 或内存 ≤ 4GB 判为 lite。拿不到就按 full，宁可保画质也不要凭空降级。
 */
export function detectTier(nav = typeof navigator === 'undefined' ? {} : navigator) {
    const cores = Number(nav.hardwareConcurrency) || 0;
    const memory = Number(nav.deviceMemory) || 0;
    if (cores > 0 && cores <= 4) return 'lite';
    if (memory > 0 && memory <= 4) return 'lite';
    return 'full';
}

export function normalizePerfMode(value) {
    return PERF_MODES.includes(value) ? value : 'auto';
}

/** 把模式解析成一份具体参数；`tier` 是最终生效的档位。 */
export function resolveProfile(mode, nav) {
    const normalized = normalizePerfMode(mode);
    const tier = normalized === 'auto' ? detectTier(nav) : normalized;
    return { mode: normalized, tier, ...PROFILES[tier] };
}
