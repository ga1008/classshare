import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import { uploadProps, uploadMarkup, uploadSnapshot, uploadItem } from '../../static/js/lq/upload.js';
const fixture = JSON.parse(fs.readFileSync(new URL('../e2e/components/fixtures/lq-upload.json', import.meta.url), 'utf8'));
describe('LQ upload controlled presentation', () => {
    for (const [index, item] of fixture.invalid.entries()) it(`rejects malformed fixture ${index}`, () => expect(() => uploadProps(item.kind, item.props)).toThrow());
    it('100 percent is still uploading until explicit server confirmation', () => { expect(uploadMarkup('file_chip', fixture.cases[3].props)).toContain('等待服务器确认'); expect(uploadItem(fixture.cases[4].props.item).confirmation.id).toBe('42'); });
    it('queue state is derived from authoritative file states with active work taking priority', () => { const failed = fixture.cases[5].props.item, uploading = fixture.cases[3].props.item; expect(uploadSnapshot({ generation: 1, items: [failed] }).state).toBe('partial-failed'); expect(uploadSnapshot({ generation: 2, items: [failed, uploading] }).state).toBe('busy'); expect(uploadSnapshot({ generation: 3, items: [] }).state).toBe('idle'); });
    it('does not mutate snapshots, inject markup or advertise client-side acceptance as success', () => { const input = structuredClone(fixture.cases[2].props); const before = JSON.stringify(input); const html = uploadMarkup('file_chip', input); expect(JSON.stringify(input)).toBe(before); expect(html).toContain('&lt;img'); expect(html).toContain('第2题'); expect(html).not.toContain('<img'); expect(uploadMarkup('upload', fixture.cases[0].props)).toContain('type="file"'); });
    it('source contains no request/upload queue/object URL/timer machinery', () => { const source = fs.readFileSync(new URL('../../static/js/lq/upload.js', import.meta.url), 'utf8'); expect(source).not.toMatch(/\b(?:fetch|XMLHttpRequest|setInterval|setTimeout|createObjectURL)\s*\(/); });
});
