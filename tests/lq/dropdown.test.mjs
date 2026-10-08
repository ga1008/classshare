import { describe, it, expect } from 'vitest';
import { dropdownResults } from '../../static/js/lq/dropdown.js';
import { formProps } from '../../static/js/lq/forms.js';

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

describe('typed searchable select declaration', () => {
    const select = tree => tree.tag === 'select' ? tree : (tree.children || []).filter(child => typeof child === 'object').map(select).find(Boolean);
    it('emits from the Field and direct select APIs while protected attrs cannot override it', () => {
        expect(select(formProps('field', { id: 'scope', label: '范围', control: 'select', searchable: true })).attrs['data-lq-searchable']).toBe('');
        expect(select(formProps('select', { id: 'scope', label: '范围', searchable: false, attrs: { 'data-lq-searchable': '' } })).attrs).not.toHaveProperty('data-lq-searchable');
    });
    for (const [kind, searchable] of [['select', 'true'], ['select', 1], ['input', true]]) it(`rejects ${kind}/${searchable}`, () => {
        expect(() => formProps(kind, { id: 'scope', label: '范围', searchable })).toThrow();
    });
});
