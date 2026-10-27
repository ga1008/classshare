import { describe, expect, it } from 'vitest';
import { awardStyle, certificateFilename, wrapCertificateText } from '../../static/js/learning_certificate_art.js';
import fs from 'node:fs';

describe('collectible cultivation award identity', () => {
    it('uses ten distinct, existing artworks and escalates bounded effects', () => {
        const keys = ['qi_awakening', 'qi_refining', 'foundation', 'application_seed', 'golden_core',
            'practical_mastery', 'systems_thinking', 'nascent_soul', 'independent_path', 'mentor_heart'];
        const styles = keys.map(awardStyle);
        expect(new Set(styles.map(item => item.url)).size).toBe(10);
        styles.forEach((style, index) => {
            expect(style.tier).toBe(index + 1);
            const art = fs.readFileSync(`.${style.url.split('?')[0]}`, 'utf8');
            expect(art).toContain('viewBox="0 0 640 640"');
            expect(art).not.toMatch(/<script|<foreignObject|animation:|href=["']https?:/);
            expect(style.duration + style.particles * 45).toBeLessThan(6500);
        });
        expect(styles[9].particles).toBeGreaterThan(styles[0].particles);
    });
    it('normalizes historical aliases and cannot build an arbitrary asset path', () => {
        expect(awardStyle('self_directed').key).toBe('independent_path');
        expect(awardStyle('teacher_guiding').tier).toBe(10);
        expect(awardStyle('../../private').key).toBe('qi_awakening');
    });
    it('sanitizes export filenames and bounds long inscriptions without splitting emoji', () => {
        expect(certificateFilename({ level: '启蒙/入门', code: 'LS:*?证书' })).toBe('启蒙-入门-LS-证书.png');
        const ctx = { measureText: value => ({ width: Array.from(value).length * 10 }) };
        const lines = wrapCertificateText(ctx, '学生🙂课程名称很长需要换行', 40, 2);
        expect(lines).toHaveLength(2);
        expect(lines[0]).toBe('学生🙂课');
        expect(lines[1]).toBe('程名称…');
    });
});
