/** One full-viewport scene owner, shared by welcome handoff and preferences. */
import { publishSceneTone, resolveSceneTone } from './lq/scene_tone.js';

export const BACKDROP_BASE = '/static/img/life_tips/';
const FILE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(?:webp|jpg|jpeg|png)$/;
const HANDOFF_KEY = 'lanshareLoginScene';
const SESSION_KEY = 'lansharePageScene';
const OWNER = Symbol.for('lanshare.page-backdrop');
const MAX_AGE = 45000;
const LIBRARY_WAIT_MS = 2500;

export const isBackdropImageMode = value => typeof value === 'string' && value.startsWith('image:') && FILE.test(value.slice(6));
export function backdropFileFromUrl(value) {
    return typeof value === 'string' && value.startsWith(BACKDROP_BASE) && FILE.test(value.slice(BACKDROP_BASE.length)) ? value.slice(BACKDROP_BASE.length) : null;
}
export function stableIndex(seed, size) {
    if (!(size > 0)) return 0;
    let digest = 2166136261;
    for (const byte of new TextEncoder().encode(String(seed))) digest = Math.imul(digest ^ byte, 16777619) >>> 0;
    return digest % size;
}
export function pickBackdropFile(images, { mode, seed, label = null }) {
    if (mode === 'off') return null;
    const library = images.filter(item => item && FILE.test(String(item.file || '')))
        .map(item => ({ file: item.file, categories: Array.isArray(item.categories) ? item.categories : [] }))
        .sort((a, b) => a.file < b.file ? -1 : a.file > b.file ? 1 : 0);
    if (isBackdropImageMode(mode)) return library.some(item => item.file === mode.slice(6)) ? mode.slice(6) : null;
    const pool = label ? library.filter(item => item.categories.includes(label)) : library;
    const source = pool.length ? pool : library;
    return source.length ? source[stableIndex(`${seed}|${mode}`, source.length)].file : null;
}
const read = (win, key) => { try { return JSON.parse(win.sessionStorage.getItem(key) || 'null'); } catch { return null; } };
const write = (win, key, value) => { try { win.sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Optional decoration. */ } };
export function writeSceneHandoff(state, win = window) {
    if (backdropFileFromUrl(state?.image)) write(win, HANDOFF_KEY, { image: state.image, tip: String(state.tip || ''), t: Date.now() });
}
function preload(win, url) {
    return new Promise(resolve => {
        const image = new win.Image();
        let settled = false;
        const done = result => { if (settled) return; settled = true; win.clearTimeout(timer); image.onload = image.onerror = null; resolve(result); };
        const timer = win.setTimeout(() => done(false), 1800);
        // Resolve the decoded image itself so a manifest without a tone can still be sampled.
        image.onload = () => done(image.naturalWidth > 0 ? image : false); image.onerror = () => done(false); image.src = url;
    });
}

export function createBackdropLayer(root = document) {
    const find = selector => root?.querySelector?.(selector);
    const layer = find('[data-lq-page-backdrop]');
    const doc = layer?.ownerDocument || root.ownerDocument || root;
    const win = doc.defaultView;
    let catalog = [];
    try { catalog = JSON.parse(find('[data-lq-backdrop-catalog]')?.textContent || '[]'); } catch { /* Empty catalog. */ }
    let library = null, libraryAbort = null, disposed = false, generation = 0, scene = null;
    let outgoing = null, fade = null;
    function clearFade() {
        fade?.cancel(); fade = null;
        outgoing?.remove(); outgoing = null;
    }
    const context = doc.body?.dataset.uiPaletteContext || '';
    const handoff = win && read(win, HANDOFF_KEY);
    const age = Date.now() - Number(handoff?.t || 0);
    const entered = Boolean(layer && backdropFileFromUrl(handoff?.image) && age >= 0 && age < MAX_AGE);
    if (entered) {
        scene = handoff;
        try { win.sessionStorage.removeItem(HANDOFF_KEY); } catch { /* Optional. */ }
    } else if (win && context) {
        const saved = read(win, SESSION_KEY);
        if (saved?.context === context && backdropFileFromUrl(saved.image)) scene = saved;
    }
    function images() {
        if (!layer || disposed) return Promise.resolve([]);
        if (library) return library;
        const controller = new win.AbortController();
        libraryAbort = controller;
        let timer;
        const deadline = new Promise((_, reject) => {
            timer = win.setTimeout(() => { controller.abort(); reject(new Error('backdrop_library_timeout')); }, LIBRARY_WAIT_MS);
        });
        // Bound both headers and body parsing: an optional image library must
        // never hold the welcome/replay lifecycle open on an unresponsive host.
        const request = win.fetch(layer.dataset.lqBackdropManifest, { credentials: 'same-origin', cache: 'no-cache', signal: controller.signal })
            .then(response => { if (!response.ok) throw new Error('backdrop_library_unavailable'); return response.json(); });
        library = Promise.race([request, deadline])
            .then(data => {
                const valid = Array.isArray(data?.images) ? data.images.filter(item => FILE.test(String(item?.file || ''))) : [];
                if (!valid.length) throw new Error('backdrop_library_invalid');
                return valid;
            })
            .catch(() => { library = null; return []; })
            .finally(() => { win.clearTimeout(timer); if (libraryAbort === controller) libraryAbort = null; });
        return library;
    }
    function finishCover() {
        if (!doc.documentElement.classList.contains('has-scene-cover')) return;
        const html = doc.documentElement;
        const done = () => { html.classList.remove('has-scene-cover', 'scene-cover-dissolving'); html.style.removeProperty('--scene-cover-image'); };
        if (win.matchMedia?.('(prefers-reduced-motion: reduce)').matches) { done(); return; }
        win.requestAnimationFrame(() => { html.classList.add('scene-cover-dissolving'); win.setTimeout(done, 560); });
    }
    function paint(file, color, rendered, tone = null) {
        const image = layer.querySelector('[data-lq-backdrop-image]');
        const paintValue = rendered ? `url(${rendered})` : 'none';
        const changed = layer.style.getPropertyValue('--lq-backdrop-paint').trim() !== paintValue;
        // Keep the decoded outgoing frame above the next scene while it fades.
        // This also animates turning the picture off, without a white flash.
        if (changed) {
            clearFade();
            if (image && layer.dataset.lqBackdropFrostState !== 'off' &&
                !win.matchMedia?.('(prefers-reduced-motion: reduce)').matches && image.animate) {
                const style = win.getComputedStyle(image);
                outgoing = image.cloneNode(false);
                outgoing.removeAttribute('data-lq-backdrop-image');
                outgoing.setAttribute('data-lq-backdrop-outgoing', '');
                Object.assign(outgoing.style, {
                    backgroundImage: style.backgroundImage, filter: style.filter,
                    inset: style.inset, opacity: style.opacity, animation: 'none',
                });
                layer.append(outgoing);
            }
        }
        layer.dataset.lqBackdropImageUrl = file ? BACKDROP_BASE + file : '';
        layer.dataset.lqBackdropFrostState = file ? 'ready' : 'off';
        layer.style.setProperty('--lq-backdrop-image', file ? `url(${BACKDROP_BASE}${file})` : 'none');
        layer.style.setProperty('--lq-backdrop-paint', paintValue);
        if (changed && outgoing) {
            const frame = outgoing;
            fade = frame.animate([{ opacity: frame.style.opacity }, { opacity: 0 }], { duration: 320, easing: 'ease-out', fill: 'forwards' });
            fade.finished.then(() => { if (outgoing === frame) clearFade(); }).catch(() => {});
        }
        // Legacy consumers remain in sync until every material uses this layer.
        const html = doc.documentElement;
        html.style.setProperty('--lq-frost-image', file ? `url(${BACKDROP_BASE}frost/${file.replace(/\.[^.]+$/, '')}.webp)` : 'none');
        html.style.setProperty('--lq-frost-base', file ? 'hsl(var(--ls-background))' : color);
        html.dataset.lqFrost = file ? 'on' : 'off';
        // The scene's tone drives every clear material's fill/ink pairing
        // (materials.css). An account's manual image or colour goes through
        // the same path, so its glass copy stays readable too.
        publishSceneTone(tone, doc);
    }
    async function apply(values) {
        if (!layer || disposed) return;
        const mine = ++generation;
        layer.dataset.lqBackdropMode = values.backdrop;
        layer.dataset.lqBackdropColor = values.backdrop_color;
        layer.style.setProperty('--lq-backdrop-color', values.backdrop_color);
        if (values.backdrop === 'off') { paint(null, values.backdrop_color, null, resolveSceneTone({ color: values.backdrop_color })); finishCover(); return; }
        const list = await images();
        if (disposed || mine !== generation) return;
        const sceneFile = backdropFileFromUrl(scene?.image);
        const validScene = sceneFile && list.some(item => item.file === sceneFile);
        const file = values.backdrop === 'scene' && validScene ? sceneFile : pickBackdropFile(list, {
            mode: values.backdrop, seed: layer.dataset.lqBackdropSeed || '', label: catalog.find(item => item?.key === values.backdrop)?.name || null,
        });
        // A unavailable manifest keeps the safe SSR image; it never blanks an
        // already usable page while an optional preference library is offline.
        if (!file) { finishCover(); return; }
        const original = BACKDROP_BASE + file;
        const ready = await preload(win, original);
        if (disposed || mine !== generation) return;
        if (!ready) {
            // Keep the last usable scene when a replacement cannot be decoded.
            finishCover(); return;
        }
        const entry = list.find(item => item.file === file) || null;
        paint(file, values.backdrop_color, original, resolveSceneTone({ entry, image: ready }));
        if (validScene && context) write(win, SESSION_KEY, { ...scene, context });
        finishCover();
    }
    return {
        layer, images, entered,
        get scene() { return scene; },
        apply,
        async adopt(image, tip = '') {
            if (!backdropFileFromUrl(image)) return;
            scene = { image, tip: String(tip), t: Date.now() };
            await apply({ backdrop: layer?.dataset.lqBackdropMode || 'scene', backdrop_color: layer?.dataset.lqBackdropColor || '#ffffff' });
        },
        dispose() { disposed = true; generation++; libraryAbort?.abort(); clearFade(); finishCover(); },
    };
}
export function initPageBackdrop(doc = document) {
    if (doc[OWNER]) return doc[OWNER];
    const owner = createBackdropLayer(doc);
    if (!owner.layer) return owner;
    doc[OWNER] = owner;
    owner.ready = owner.apply({ backdrop: owner.layer.dataset.lqBackdropMode, backdrop_color: owner.layer.dataset.lqBackdropColor });
    return owner;
}
