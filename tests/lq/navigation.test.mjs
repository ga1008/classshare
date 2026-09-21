import { describe, it, expect } from 'vitest';
import { navigationProps } from '../../static/js/lq/navigation.js';

describe('LQ navigation non-JSON callers', () => {
    it('rejects sparse items before a two-slot array can render only one actual tab', () => {
        const items = [{ key: 'first', label: 'First' }];
        items.length = 2;
        expect(() => navigationProps('tabs', { id: 'views', label: 'Views', items })).toThrow(TypeError);
    });
});
