import { afterEach, beforeEach, expect, it, vi } from 'vitest';

class Scope {
    listeners = new Set();
    addEventListener(_type, listener) { this.listeners.add(listener); }
    removeEventListener(_type, listener) { this.listeners.delete(listener); }
}
let doc;
let images;
beforeEach(async () => {
    vi.resetModules();
    doc = new Scope();
    doc.body = { classList: { remove() {} } };
    vi.stubGlobal('document', doc);
    vi.stubGlobal('window', { removeEventListener: vi.fn() });
    images = await import('../../static/js/ls_image_lightbox.js');
});
afterEach(() => { images.destroyImageLightbox(); vi.unstubAllGlobals(); });

it('LQ image delegation registers a scope once and returns its stable reversible disposer', () => {
    const scope = new Scope();
    const first = images.bindImageLightboxDelegation(scope);
    expect(images.bindImageLightboxDelegation(scope)).toBe(first);
    expect(scope.listeners.size).toBe(1);
    first(); first(); expect(scope.listeners.size).toBe(0);
    images.bindImageLightboxDelegation(scope); expect(scope.listeners.size).toBe(1);
    images.destroyImageLightbox(); expect(scope.listeners.size).toBe(0); expect(doc.listeners.size).toBe(0);
});

it('LQ scoped consumed clicks do not also open through the document delegation', () => {
    const lookup = vi.fn();
    for (const listener of doc.listeners) listener({ defaultPrevented: true, target: { closest: lookup } });
    expect(lookup).not.toHaveBeenCalled();
});

it('LQ shared image grouping preserves scoped DOM order, selected index and original links', () => {
    const scope = new Scope();
    const make = (src, group, title) => ({ tagName: 'IMG', src, currentSrc: src, alt: title,
        dataset: { lsLightboxGroup: group, lsLightboxOriginal: `${src}?original` },
        getAttribute: () => '', closest: selector => selector === '[data-ls-lightbox-scope]' ? scope : { dataset: { lsLightboxLabel: '教学附件' } },
    });
    const first = make('/a.png', 'group', '第一张'); const second = make('/b.png', 'group', '第二张'); const other = make('/c.png', 'else', '其他');
    scope.querySelectorAll = () => [first, other, second];
    const result = images.collectLightboxGroup(second);
    expect(result.index).toBe(1); expect(result.groupLabel).toBe('教学附件');
    expect(result.items.map(item => [item.title, item.originalSrc])).toEqual([['第一张', '/a.png?original'], ['第二张', '/b.png?original']]);
});
