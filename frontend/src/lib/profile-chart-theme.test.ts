import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

vi.mock('../../../static/js/api.js', () => ({ apiFetch: vi.fn() }));
vi.mock('../../../static/js/ui.js', () => ({ escapeHtml: vi.fn(), showToast: vi.fn() }));

let profileChartThemeOptions: (element: any, type: string) => any;
let bindProfileChartTheme: (element: any, chart: any, type: string) => () => void;
beforeAll(async () => {
  // Import the real page module without mounting unrelated profile forms.
  vi.stubGlobal('document', { querySelector: () => null, getElementById: () => null });
  // @ts-expect-error Native SSR module has no generated declarations.
  ({ profileChartThemeOptions, bindProfileChartTheme } = await import('../../../static/js/profile.js'));
  vi.unstubAllGlobals();
});
const cleanups: Array<() => void> = [];
afterEach(() => cleanups.splice(0).forEach(cleanup => cleanup()));

function fixture() {
  let tokens: Record<string, string> = { '--ls-ink-2': '215 20% 75%', '--ls-ink-3': '215 16% 58%', '--ls-line': '220 14% 22%' };
  const win = Object.assign(new EventTarget(), { getComputedStyle: () => ({ getPropertyValue: (key: string) => tokens[key] || '' }) });
  const doc = Object.assign(new EventTarget(), { defaultView: win });
  const element = { ownerDocument: doc, isConnected: true };
  const chart = { setOption: vi.fn(), resize: vi.fn(), isDisposed: vi.fn(() => false) };
  return { element, chart, doc, win, setTokens: (value: Record<string, string>) => { tokens = value; } };
}

describe('profile canvas annotation theme', () => {
  it('reads the current dark tokens during binding, before any theme event', () => {
    const f = fixture();
    cleanups.push(bindProfileChartTheme(f.element, f.chart, 'bar'));
    expect(f.chart.setOption).toHaveBeenCalledExactlyOnceWith({
      textStyle: { color: 'hsl(215, 20%, 75%)' }, legend: { textStyle: { color: 'hsl(215, 20%, 75%)' } },
      xAxis: expect.objectContaining({ axisLabel: { color: 'hsl(215, 16%, 58%)' }, splitLine: { lineStyle: { color: 'hsl(220, 14%, 22%)' } } }),
      yAxis: expect.objectContaining({ axisLabel: { color: 'hsl(215, 16%, 58%)' }, axisLine: { lineStyle: { color: 'hsl(220, 14%, 22%)' } } }),
    });
  });
  it('updates the existing bar and pie once each, leaving data and series colors out of patches', () => {
    const f = fixture();
    const pie = { ...f.chart, setOption: vi.fn(), resize: vi.fn() };
    cleanups.push(bindProfileChartTheme(f.element, f.chart, 'bar'));
    cleanups.push(bindProfileChartTheme(f.element, pie, 'pie'));
    f.chart.setOption.mockClear(); pie.setOption.mockClear();
    f.setTokens({ '--ls-ink-2': '215 25% 27%', '--ls-ink-3': '215 16% 45%', '--ls-line': '214 32% 91%' });
    f.doc.dispatchEvent(new Event('lq:theme-change'));
    expect(f.chart.setOption).toHaveBeenCalledTimes(1);
    expect(pie.setOption).toHaveBeenCalledExactlyOnceWith({
      textStyle: { color: 'hsl(215, 25%, 27%)' }, legend: { textStyle: { color: 'hsl(215, 25%, 27%)' } },
      series: [{ label: { color: 'hsl(215, 25%, 27%)' }, labelLine: { lineStyle: { color: 'hsl(214, 32%, 91%)' } } }],
    });
    expect(f.chart.setOption.mock.lastCall![0]).not.toHaveProperty('series');
    expect(pie.setOption.mock.lastCall![0]).not.toHaveProperty('color');
    expect(pie.setOption.mock.lastCall![0].series[0]).not.toHaveProperty('data');
    expect(pie.setOption.mock.lastCall![0].series[0]).not.toHaveProperty('itemStyle');
    f.win.dispatchEvent(new Event('resize'));
    expect(f.chart.resize).toHaveBeenCalledTimes(1);
    expect(pie.resize).toHaveBeenCalledTimes(1);
  });
  it('shares one listener and a stale cleanup cannot remove a replacement binding', () => {
    const f = fixture();
    const listen = vi.spyOn(f.doc, 'addEventListener');
    const first = bindProfileChartTheme(f.element, f.chart, 'bar');
    const second = bindProfileChartTheme(f.element, f.chart, 'bar');
    cleanups.push(first, second);
    expect(listen).toHaveBeenCalledTimes(1);
    first(); first();
    f.chart.setOption.mockClear();
    f.doc.dispatchEvent(new Event('lq:theme-change'));
    expect(f.chart.setOption).toHaveBeenCalledTimes(1);
    second();
    f.doc.dispatchEvent(new Event('lq:theme-change'));
    expect(f.chart.setOption).toHaveBeenCalledTimes(1);
    cleanups.push(bindProfileChartTheme(f.element, f.chart, 'bar'));
    expect(listen).toHaveBeenCalledTimes(2);
  });
  it('prunes detached or already-disposed charts without disposing or recreating them', () => {
    const f = fixture();
    const disposed = { ...f.chart, setOption: vi.fn(), isDisposed: () => true };
    cleanups.push(bindProfileChartTheme(f.element, f.chart, 'bar'));
    cleanups.push(bindProfileChartTheme(f.element, disposed, 'pie'));
    f.chart.setOption.mockClear();
    f.element.isConnected = false;
    f.doc.dispatchEvent(new Event('lq:theme-change'));
    expect(f.chart.setOption).not.toHaveBeenCalled();
    expect(disposed.setOption).not.toHaveBeenCalled();
  });
  it('uses safe visible fallbacks when a token is absent', () => {
    const f = fixture(); f.setTokens({});
    expect(profileChartThemeOptions(f.element, 'pie')).toMatchObject({ textStyle: { color: '#334155' }, series: [{ labelLine: { lineStyle: { color: '#e2e8f0' } } }] });
  });
});
