import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { tone, toneLevels } from '../../static/js/lq/tones.js';

describe('LQ semantic state levels', () => {
    it('matches the Python generated registry and all known state names', () => {
        expect(toneLevels).toEqual(JSON.parse(readFileSync('classroom_app/lq_tones.generated.json', 'utf8')));
        for (const [family, states] of Object.entries(toneLevels)) for (const [state, level] of Object.entries(states)) {
            expect(tone(family, state)).toEqual({ name: `${family}-${state}`, level, known: true });
        }
    });
    it('does not expose prototype keys or raw unknown values in attributes', () => {
        for (const [family, state] of [[null, null], ['save', 'missing'], ['__proto__', 'constructor'], ['save', 'x]{}'], [{}, []]]) {
            expect(tone(family, state)).toEqual({ name: 'neutral', level: 'neutral', known: false });
        }
        expect(() => { toneLevels.save.local_saved = 'success'; }).toThrow();
    });
    it('does not misrepresent local persistence or replaced work as completion', () => {
        expect(tone('save', 'local_saved').level).toBe('neutral');
        expect(tone('save', 'synced').level).toBe('success');
        expect(tone('save', 'conflict').level).toBe('danger');
        expect(tone('job', 'superseded').level).toBe('neutral');
    });
});
