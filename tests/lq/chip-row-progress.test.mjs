import { describe, it, expect } from 'vitest';
import { chipRowProps, chipRowMarkup } from '../../static/js/lq/chip-row.js';
import { componentProps } from '../../static/js/lq/component-props.js';
import { componentMarkup } from '../../static/js/lq/components.js';

describe('LQ chip row and progress contracts', () => {
  it('keeps no-JS chips reachable and escapes text through the existing chip factory', () => {
    const html = chipRowMarkup({ id: 'tags', label: '标签', items: [{ label: '<script>bad</script>' }] });
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('data-lq-chip-collapsed');
    expect(html).toContain('lq-chip-row__disclosure" hidden');
  });
  it('validates every tail item before building any markup', () => {
    expect(() => chipRowProps({ id: 'tags', label: '标签', items: [...Array.from({ length: 8 }, () => ({ label: 'safe' })), { label: 'bad', attrs: { onclick: 'x' } }] })).toThrow();
    expect(() => chipRowProps({ id: 'tags', label: '标签', items: Array(3) })).toThrow();
  });
  it('gives ring real progress semantics, owning numeric aria and no unknown percentage', () => {
    const unknown = componentProps('progress', { label: '下载', variant: 'ring' });
    expect(unknown.attrs.role).toBe('progressbar'); expect(unknown.attrs['aria-valuenow']).toBeUndefined(); expect(unknown.percent).toBe(null);
    const known = componentProps('progress', { label: '下载', variant: 'ring', value: 3, max: 7, attrs: { 'aria-valuenow': 90 } });
    expect(known.attrs['aria-valuenow']).toBe('3'); expect(known.percent).toBe(43);
    expect(componentMarkup('progress', { label: '下载', variant: 'ring', value: 0 })).toContain('visibility="hidden"');
  });
  it('preserves default native bar semantics and rejects malformed progress', () => {
    expect(componentProps('progress', { label: '下载', value: 0 }).tag).toBe('progress');
    for (const p of [{ variant: null }, { max: 0 }, { value: true }, { value: 101 }]) expect(() => componentProps('progress', { label: '下载', ...p })).toThrow();
  });
});
