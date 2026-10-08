import { describe, it, expect } from 'vitest';
import { dropdownResults } from '../../static/js/lq/dropdown.js';

describe('dropdown async result validation precedes native mutations', () => {
    it('normalizes supported options and preserves the query ticket', () => {
        expect(dropdownResults({ query: '课堂', generation: 2, options: [{ value: 'a', label: '课堂 A' }] })).toEqual({ query: '课堂', generation: 2, options: [{ value: 'a', label: '课堂 A', disabled: false, hint: '' }] });
    });
    for (const invalid of [
        { status: 'other' }, { generation: -1 }, { query: 3 }, { message: {} }, { unknown: true },
        { options: {} }, { options: [{ value: 'a', label: 3 }] }, { options: [{ value: 'a', label: 'A', disabled: 'false' }] },
        { options: [{ value: 'a', label: 'A' }, { value: 'a', label: 'B' }] }, { options: [{ value: 'a', label: 'A', hint: {} }] },
    ]) it(`rejects ${JSON.stringify(invalid)}`, () => expect(() => dropdownResults({ query: '', generation: 1, ...invalid })).toThrow());
});
