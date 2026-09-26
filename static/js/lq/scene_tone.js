/**
 * Scene tone: the one place that decides whether glass on a photograph gets
 * dark ink on a light fill or light ink on a dark fill.
 *
 * Contract (see docs/lq-scene-ink-2026-09-26.md):
 *   - A surface never picks its text colour from the account theme. It reads
 *     `--lq-material-ink/muted/accent`, which `[data-lq-tone]` pairs with the
 *     fill in materials.css.
 *   - The page publishes the tone of its backdrop on `<html data-lq-scene-tone>`
 *     (SSR from the manifest, refreshed here when the backdrop changes).
 *   - An element that wants its own probe declares `data-lq-scene="auto"`;
 *     `watchAutoTone()` samples the backdrop behind its box and writes
 *     `data-lq-tone` for it.
 */

/** Rec.709 luma of a 0–255 pixel; matches tools/tips/compress_images.py. */
export const TONE_LUMA_THRESHOLD = 148;
export const SCENE_TONE_EVENT = 'lq:scene-tone';
const SAMPLE_WIDTH = 48;
const SAMPLE_HEIGHT = 27;
const HEX = /^#([0-9a-f]{6})$/i;

export const lumaOf = (r, g, b) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
export const toneFromLuma = luma => (Number(luma) > TONE_LUMA_THRESHOLD ? 'light' : 'dark');
export const isTone = value => value === 'light' || value === 'dark';

/** Solid backdrops ("off" mode) are measured from their hex, no canvas needed. */
export function toneFromHex(hex) {
    const match = HEX.exec(String(hex || '').trim());
    if (!match) return null;
    const n = parseInt(match[1], 16);
    return toneFromLuma(lumaOf((n >> 16) & 255, (n >> 8) & 255, n & 255));
}

function averageLuma(data) {
    if (!data?.length) return null;
    let sum = 0;
    for (let i = 0; i < data.length; i += 4) sum += lumaOf(data[i], data[i + 1], data[i + 2]);
    return sum / (data.length / 4);
}

/**
 * Average luma of a rectangle of the image, expressed as fractions of the
 * image box (x, y, w, h in 0–1). Defaults to the central band where copy sits.
 * Returns null when the image cannot be read (cross-origin, not decoded).
 */
export function sampleImageLuma(image, region = { x: 0.125, y: 0.3, w: 0.75, h: 0.4 }, doc = document) {
    try {
        const canvas = doc.createElement('canvas');
        canvas.width = SAMPLE_WIDTH;
        canvas.height = SAMPLE_HEIGHT;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(image, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT);
        const x = Math.max(0, Math.min(SAMPLE_WIDTH - 1, Math.round(region.x * SAMPLE_WIDTH)));
        const y = Math.max(0, Math.min(SAMPLE_HEIGHT - 1, Math.round(region.y * SAMPLE_HEIGHT)));
        const w = Math.max(1, Math.min(SAMPLE_WIDTH - x, Math.round(region.w * SAMPLE_WIDTH)));
        const h = Math.max(1, Math.min(SAMPLE_HEIGHT - y, Math.round(region.h * SAMPLE_HEIGHT)));
        return averageLuma(ctx.getImageData(x, y, w, h).data);
    } catch {
        return null;
    }
}

/** Tone of an image's central band; dark when the pixels cannot be read. */
export function sampleImageTone(image, region) {
    const luma = sampleImageLuma(image, region);
    return luma === null ? 'dark' : toneFromLuma(luma);
}

/**
 * Map an element's viewport box onto a `background-size: cover` image so the
 * probe reads the pixels actually behind the element.
 */
export function coverRegion(rect, viewport, image) {
    const iw = image?.naturalWidth || image?.width || 0;
    const ih = image?.naturalHeight || image?.height || 0;
    if (!(iw > 0 && ih > 0 && viewport?.width > 0 && viewport?.height > 0)) return null;
    const scale = Math.max(viewport.width / iw, viewport.height / ih);
    const offsetX = (iw * scale - viewport.width) / 2;
    const offsetY = (ih * scale - viewport.height) / 2;
    const clamp = value => Math.max(0, Math.min(1, value));
    const x = clamp((rect.left + offsetX) / (iw * scale));
    const y = clamp((rect.top + offsetY) / (ih * scale));
    const right = clamp((rect.right + offsetX) / (iw * scale));
    const bottom = clamp((rect.bottom + offsetY) / (ih * scale));
    return { x, y, w: Math.max(right - x, 1 / SAMPLE_WIDTH), h: Math.max(bottom - y, 1 / SAMPLE_HEIGHT) };
}

/**
 * Resolve the tone of a backdrop choice. Manifest metadata wins (no decode
 * cost, identical to SSR); a decoded image is sampled; a hex colour is
 * measured; otherwise null so callers keep whatever was published before.
 */
export function resolveSceneTone({ entry = null, image = null, color = null } = {}) {
    if (isTone(entry?.tone)) return entry.tone;
    if (Number.isFinite(entry?.luma)) return toneFromLuma(entry.luma);
    if (image) return sampleImageTone(image);
    return toneFromHex(color);
}

/** Publish the page tone; consumers listen for SCENE_TONE_EVENT on <html>. */
export function publishSceneTone(tone, doc = document) {
    const html = doc.documentElement;
    if (!isTone(tone)) {
        if (!html.dataset.lqSceneTone) return null;
        delete html.dataset.lqSceneTone;
        html.dispatchEvent(new CustomEvent(SCENE_TONE_EVENT, { detail: { tone: null } }));
        return null;
    }
    if (html.dataset.lqSceneTone === tone) return tone;
    html.dataset.lqSceneTone = tone;
    html.dispatchEvent(new CustomEvent(SCENE_TONE_EVENT, { detail: { tone } }));
    return tone;
}

export const readSceneTone = (doc = document) => (isTone(doc.documentElement.dataset.lqSceneTone) ? doc.documentElement.dataset.lqSceneTone : null);

/**
 * Per-element probe for `[data-lq-scene="auto"]`. `getImage()` returns the
 * decoded backdrop image (or null); when no image is available the element
 * falls back to the published page tone.
 */
export function watchAutoTone({ getImage, root = document, win = window } = {}) {
    const doc = root.ownerDocument || root;
    let frame = null;
    const measure = () => {
        frame = null;
        const image = typeof getImage === 'function' ? getImage() : null;
        const pageTone = readSceneTone(doc);
        const viewport = { width: win.innerWidth, height: win.innerHeight };
        root.querySelectorAll('[data-lq-scene="auto"]').forEach(node => {
            let tone = pageTone;
            if (image) {
                const region = coverRegion(node.getBoundingClientRect(), viewport, image);
                const luma = region ? sampleImageLuma(image, region, doc) : null;
                if (luma !== null) tone = toneFromLuma(luma);
            }
            if (isTone(tone)) node.dataset.lqTone = tone;
        });
    };
    const schedule = () => { if (frame === null) frame = win.requestAnimationFrame(measure); };
    doc.documentElement.addEventListener(SCENE_TONE_EVENT, schedule);
    win.addEventListener('resize', schedule, { passive: true });
    schedule();
    return {
        refresh: schedule,
        dispose() {
            if (frame !== null) win.cancelAnimationFrame(frame);
            doc.documentElement.removeEventListener(SCENE_TONE_EVENT, schedule);
            win.removeEventListener('resize', schedule);
        },
    };
}
