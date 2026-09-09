import { describe, expect, test } from 'vitest';
import { PERF_MODES, PROFILES, detectTier, normalizePerfMode, resolveProfile } from './perf_profile.js';
import { percentile, summarize } from './perf_probe.js';

describe('perf_profile: 设备探测', () => {
    test('核心数或内存偏低判为流畅档', () => {
        expect(detectTier({ hardwareConcurrency: 4, deviceMemory: 16 })).toBe('lite');
        expect(detectTier({ hardwareConcurrency: 16, deviceMemory: 4 })).toBe('lite');
        expect(detectTier({ hardwareConcurrency: 2 })).toBe('lite');
    });

    test('配置够用时走高画质', () => {
        expect(detectTier({ hardwareConcurrency: 8, deviceMemory: 16 })).toBe('full');
    });

    test('探测不到就按高画质，不凭空降级', () => {
        expect(detectTier({})).toBe('full');
        expect(detectTier({ hardwareConcurrency: 0, deviceMemory: 0 })).toBe('full');
    });
});

describe('perf_profile: 模式解析', () => {
    test('非法值退回 auto', () => {
        expect(normalizePerfMode('nope')).toBe('auto');
        expect(normalizePerfMode(undefined)).toBe('auto');
        for (const mode of PERF_MODES) expect(normalizePerfMode(mode)).toBe(mode);
    });

    test('手动锁定时忽略设备探测', () => {
        const weak = { hardwareConcurrency: 2, deviceMemory: 2 };
        expect(resolveProfile('full', weak).tier).toBe('full');
        expect(resolveProfile('lite', { hardwareConcurrency: 32, deviceMemory: 64 }).tier).toBe('lite');
        expect(resolveProfile('auto', weak).tier).toBe('lite');
    });

    test('解析结果带上该档位的全部参数', () => {
        const profile = resolveProfile('lite');
        expect(profile.mode).toBe('lite');
        expect(profile.tier).toBe('lite');
        for (const key of Object.keys(PROFILES.lite)) expect(profile[key]).toBe(PROFILES.lite[key]);
    });

    test('流畅档在每一项上都不比高画质更费', () => {
        expect(PROFILES.lite.maxDpr).toBeLessThan(PROFILES.full.maxDpr);
        expect(PROFILES.lite.undoLimit).toBeLessThan(PROFILES.full.undoLimit);
        expect(PROFILES.lite.simplifyTolerance).toBeGreaterThan(PROFILES.full.simplifyTolerance);
        expect(PROFILES.lite.settleMs).toBeGreaterThan(PROFILES.full.settleMs);
        expect(PROFILES.lite.syncIntervalMs).toBeGreaterThan(PROFILES.full.syncIntervalMs);
        expect(PROFILES.lite.glass).toBe(false);
        expect(PROFILES.lite.softEraser).toBe(false);
    });
});

describe('perf_probe: 统计', () => {
    test('分位数按线性插值', () => {
        expect(percentile([1, 2, 3, 4], 0.5)).toBeCloseTo(2.5, 6);
        expect(percentile([10], 0.95)).toBe(10);
        expect(percentile([], 0.5)).toBe(0);
    });

    test('汇总给出帧数、P50/P95、最差帧与 P95 对应帧率', () => {
        const samples = Array.from({ length: 100 }, (_, index) => (index === 99 ? 120 : 16.7));
        const summary = summarize(samples);
        expect(summary.frames).toBe(100);
        expect(summary.p50).toBeCloseTo(16.7, 1);
        expect(summary.worst).toBe(120);
        expect(summary.fpsP95).toBeGreaterThan(0);
    });

    test('空样本不报错', () => {
        expect(summarize([])).toEqual({ frames: 0, p50: 0, p95: 0, worst: 0, fpsP95: 0 });
    });
});
