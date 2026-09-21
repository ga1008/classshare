import { describe, expect, it } from 'vitest';
import { componentProps } from '../../static/js/lq/component-props.js';
import { componentMarkup, html } from '../../static/js/lq/components.js';
import { iconMarkup } from '../../static/js/lq/icons.js';

describe('LQ presentation contract', () => {
  it('escapes text and attributes, including script-shaped strings', () => {
    const rendered = html.button({ label: '<img src=x onerror=alert(1)>', attrs: { title: '" onmouseover="alert(2)' } });
    expect(rendered).not.toContain('<img');
    expect(rendered).toContain('&lt;img');
    expect(rendered).toContain('title="&quot; onmouseover=&quot;alert(2)"');
  });
  it.each(['onclick', 'style', 'class', 'href', 'role', 'tabindex'])('rejects attribute bypass %s', key => {
    expect(() => html.button({ label: '保存', attrs: { [key]: 'x' } })).toThrow();
  });
  it.each(['javascript:alert(1)', 'data:text/html,x', '//other.test', '\\other.test', 'java\nscript:x', '/two words'])('rejects URL %s', href => {
    expect(() => html.button({ label: '打开', href })).toThrow();
  });
  it('uses native link semantics, protects target and preserves a busy name', () => {
    const props = componentProps('button', { label: '保存', loading: true, href: '/safe?a=1&b=2', attrs: {
      target: '_blank', rel: 'opener external', 'aria-hidden': true, 'aria-label': 'wrong', 'aria-labelledby': 'missing', 'aria-busy': false,
    } });
    expect(props.tag).toBe('a');
    expect(props.attrs).toMatchObject({ 'aria-label': '保存', 'aria-busy': 'true', 'aria-disabled': 'true', rel: 'external noopener noreferrer' });
    expect(props.attrs).not.toHaveProperty('aria-hidden');
    expect(props.attrs).not.toHaveProperty('aria-labelledby');
    expect(html.button({ label: '保存', loading: true })).toContain('lq-btn__label">保存');
  });
  it('requires icon-only names and gives unknown icons a safe fallback', () => {
    expect(() => html.button({ icon: 'plus' })).toThrow();
    expect(() => html.button({ attrs: { 'aria-label': '新建' } })).toThrow();
    expect(html.button({ icon: 'plus', attrs: { 'aria-label': '新建' } })).toContain('lq-btn--icon');
    expect(iconMarkup('__proto__')).toBe(iconMarkup('circle-help'));
    expect(iconMarkup('<svg onload=alert(1)>')).toBe(iconMarkup('circle-help'));
  });
  it('keeps status quiet and filter/tag interactions distinct', () => {
    expect(componentProps('chip', { label: '已完成', attrs: { 'aria-live': 'assertive' } }).attrs).not.toHaveProperty('aria-live');
    expect(html.chip({ label: '已选', kind: 'filter', pressed: true })).toContain('aria-pressed="true"');
    expect(html.chip({ label: '张三', kind: 'tag', removable: true })).toContain('aria-label="移除张三"');
    expect(() => html.chip({ label: '状态', removable: true })).toThrow();
  });
  it('omits zero badges and requires a name for a dot', () => {
    expect(html.badge({ value: 0 })).toBe('');
    expect(html.badge({ value: 0, dot: true, label: '未读' })).toBe('');
    expect(() => html.badge({ dot: true })).toThrow();
    expect(html.badge({ dot: true, label: '未读' })).toContain('role="img"');
  });
  it('hashes codepoints and keeps avatar image decorative', () => {
    const props = componentProps('avatar', { name: '😀张三', src: '/avatar.png' });
    expect(props.initial).toBe('😀');
    expect(props.attrs['data-avatar-bucket']).toBe('5');
    expect(html.avatar({ name: '张三', src: '/avatar.png' })).toContain('alt=""');
    expect(() => html.avatar({ name: '张三', src: 'mailto:a@b' })).toThrow();
  });
  it('keeps indeterminate progress honest and rejects impossible values', () => {
    expect(componentProps('progress', { label: '上传' }).attrs).not.toHaveProperty('value');
    expect(componentProps('progress', { label: '上传', value: 0 }).attrs.value).toBe('0');
    for (const value of [-1, 101, NaN, Infinity, true, '10']) expect(() => html.progress({ label: '上传', value })).toThrow();
    expect(() => componentMarkup('constructor', {})).toThrow();
  });
  it('keeps skeletons out of accessible content and rejects unbounded placeholders', () => {
    const attrs = componentProps('skeleton', { lines: 3, attrs: { 'aria-hidden': false, 'aria-label': 'Fake content', 'aria-busy': true } }).attrs;
    expect(attrs).toEqual({ 'aria-hidden': 'true' });
    expect(html.skeleton({ lines: 3 }).match(/class="lq-skeleton__part"/g)).toHaveLength(3);
    for (const lines of [0, 9, 1.5, true, '3']) expect(() => html.skeleton({ lines })).toThrow();
    expect(() => html.skeleton({ shape: 'avatar', lines: 3 })).toThrow();
  });
});
