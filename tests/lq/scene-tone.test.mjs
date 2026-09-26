import { beforeEach, describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
    SCENE_TONE_EVENT, TONE_LUMA_THRESHOLD, coverRegion, publishSceneTone, readSceneTone,
    resolveSceneTone, sampleImageLuma, toneFromHex, toneFromLuma, watchAutoTone,
} from '../../static/js/lq/scene_tone.js';

/** A canvas stub whose pixels are a flat colour, so luma is exact. */
function fakeDocument(pixel) {
    const [r, g, b] = pixel;
    const listeners = new Map();
    const html = {
        dataset: {},
        addEventListener: (name, fn) => listeners.set(fn, name),
        removeEventListener: (_, fn) => listeners.delete(fn),
        dispatchEvent(event) { for (const [fn, name] of listeners) if (name === event.type) fn(event); return true; },
    };
    return {
        documentElement: html,
        listeners,
        createElement: () => ({
            width: 0, height: 0,
            getContext: () => ({
                drawImage() {},
                getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4).map((_, i) => [r, g, b, 255][i % 4]) }),
            }),
        }),
    };
}

describe('scene tone', () => {
    it('shares one luma threshold with the compressor and the service', () => {
        const python = readFileSync('tools/tips/compress_images.py', 'utf8');
        expect(python).toContain(`TONE_LUMA_THRESHOLD = ${TONE_LUMA_THRESHOLD}`);
        expect(readFileSync('classroom_app/services/user_ui_preferences_service.py', 'utf8')).toContain(`SCENE_TONE_LUMA_THRESHOLD = ${TONE_LUMA_THRESHOLD}`);
        expect(toneFromLuma(TONE_LUMA_THRESHOLD)).toBe('dark');
        expect(toneFromLuma(TONE_LUMA_THRESHOLD + 1)).toBe('light');
    });

    it('measures solid colours the way the server does', () => {
        expect(toneFromHex('#ffffff')).toBe('light');
        expect(toneFromHex('#102030')).toBe('dark');
        expect(toneFromHex('#959595')).toBe('light');
        expect(toneFromHex('#949494')).toBe('dark');
        expect(toneFromHex('#fff')).toBeNull();
        expect(toneFromHex('white')).toBeNull();
    });

    it('prefers manifest metadata, then a decoded image, then a colour', () => {
        const doc = fakeDocument([10, 10, 10]);
        globalThis.document = doc;
        try {
            expect(resolveSceneTone({ entry: { tone: 'light' }, image: {}, color: '#000000' })).toBe('light');
            expect(resolveSceneTone({ entry: { luma: 200 }, image: {}, color: '#000000' })).toBe('light');
            expect(resolveSceneTone({ entry: { tone: 'purple' }, image: {}, color: '#ffffff' })).toBe('dark');
            expect(resolveSceneTone({ color: '#ffffff' })).toBe('light');
            expect(resolveSceneTone({})).toBeNull();
        } finally {
            delete globalThis.document;
        }
    });

    it('samples a region of the image and reads null when the canvas is unreadable', () => {
        const doc = fakeDocument([255, 255, 255]);
        expect(sampleImageLuma({}, undefined, doc)).toBeCloseTo(255, 5);
        expect(sampleImageLuma({}, { x: 0, y: 0, w: 1, h: 1 }, doc)).toBeCloseTo(255, 5);
        const broken = { createElement: () => { throw new Error('no canvas'); } };
        expect(sampleImageLuma({}, undefined, broken)).toBeNull();
    });

    it('maps an element box onto a cover-fitted image', () => {
        // 2000×1000 image on a 1000×1000 viewport: cover scales to 2000×1000, crops 500px each side.
        const image = { naturalWidth: 2000, naturalHeight: 1000 };
        const viewport = { width: 1000, height: 1000 };
        const centre = coverRegion({ left: 250, top: 250, right: 750, bottom: 750 }, viewport, image);
        expect(centre).toEqual({ x: 0.375, y: 0.25, w: 0.25, h: 0.5 });
        expect(coverRegion({ left: 0, top: 0, right: 10, bottom: 10 }, { width: 0, height: 0 }, image)).toBeNull();
    });
});

describe('page-level publication', () => {
    let doc;
    beforeEach(() => { doc = fakeDocument([0, 0, 0]); });

    it('writes html[data-lq-scene-tone] once per change and clears it on null', () => {
        const seen = [];
        doc.documentElement.addEventListener(SCENE_TONE_EVENT, event => seen.push(event.detail.tone));
        expect(publishSceneTone('light', doc)).toBe('light');
        expect(publishSceneTone('light', doc)).toBe('light');
        expect(readSceneTone(doc)).toBe('light');
        expect(publishSceneTone('bogus', doc)).toBeNull();
        expect(readSceneTone(doc)).toBeNull();
        expect(publishSceneTone(null, doc)).toBeNull();
        expect(seen).toEqual(['light', null]);
    });

    it('resolves data-lq-scene="auto" hosts from the backdrop behind them, else the page tone', () => {
        const nodes = [
            { dataset: {}, getBoundingClientRect: () => ({ left: 0, top: 0, right: 100, bottom: 100 }) },
        ];
        const root = { ownerDocument: doc, querySelectorAll: () => nodes };
        const win = {
            innerWidth: 100, innerHeight: 100,
            requestAnimationFrame: fn => { fn(); return 1; }, cancelAnimationFrame() {},
            addEventListener() {}, removeEventListener() {},
        };
        publishSceneTone('dark', doc);
        const watcher = watchAutoTone({ getImage: () => null, root, win });
        expect(nodes[0].dataset.lqTone).toBe('dark');
        watcher.dispose();
        const bright = fakeDocument([250, 250, 250]);
        root.ownerDocument = bright;
        const brightWatcher = watchAutoTone({ getImage: () => ({ naturalWidth: 200, naturalHeight: 100 }), root, win });
        expect(nodes[0].dataset.lqTone).toBe('light');
        brightWatcher.dispose();
    });
});
