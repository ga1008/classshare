// 登录页「人生一言」场景：进页面即铺一言背景图 + 液态玻璃表单，
// 登录成功后表单原地化为一言玻璃卡，结束时把背景交棒给首页顶栏。
import { playLoginSceneReveal, sampleImageTone } from '/static/js/cultivation_identity.js?v=20260803-scene3';
import { spinner } from './lq/components.js';

const MANIFEST_URL = '/static/img/life_tips/manifest.json';
const IMAGE_BASE = '/static/img/life_tips/';
const IMAGE_PRELOAD_TIMEOUT_MS = 4500;

function preloadSceneImage(url, timeoutMs, signal) {
    return new Promise((resolve) => {
        if (!url) {
            resolve(null);
            return;
        }
        const image = new Image();
        let settled = false;
        const finish = value => {
            if (settled) return;
            settled = true;
            window.clearTimeout(timer);
            signal?.removeEventListener('abort', aborted);
            image.onload = null;
            image.onerror = null;
            resolve(value);
        };
        const aborted = () => finish(null);
        const timer = window.setTimeout(() => finish(null), timeoutMs);
        image.onload = () => {
            finish(image.naturalWidth > 0 ? image : null);
        };
        image.onerror = () => {
            finish(null);
        };
        if (signal?.aborted) { finish(null); return; }
        signal?.addEventListener('abort', aborted, { once: true });
        image.src = url;
    });
}

async function pickSceneImage(signal) {
    try {
        const response = await fetch(MANIFEST_URL, { cache: 'force-cache', ...(signal ? { signal } : {}) });
        if (!response.ok) return null;
        const manifest = await response.json();
        const images = Array.isArray(manifest?.images)
            ? manifest.images.filter((item) => item && item.file)
            : [];
        if (!images.length) return null;
        // 时段感知：白天优先阳光治愈系，傍晚/夜间优先电影暗调系，氛围随一天节律。
        const hour = new Date().getHours();
        const wantSunny = hour >= 6 && hour < 17;
        const moodPool = images.filter((item) => item.file.includes('-sunny-') === wantSunny);
        const source = moodPool.length ? moodPool : images;
        const chosen = source[Math.floor(Math.random() * source.length)];
        return {
            url: IMAGE_BASE + chosen.file,
            categories: Array.isArray(chosen.categories) ? chosen.categories : [],
        };
    } catch (error) {
        return null;
    }
}

/**
 * 初始化登录场景：挑图、预载、铺背景、采样色调。
 * 返回 scene 句柄（拿不到图时返回 null，页面回落纯渐变背景）。
 */
async function initLegacyLoginScene() {
    const picked = await pickSceneImage();
    if (!picked) return null;
    const image = await preloadSceneImage(picked.url, IMAGE_PRELOAD_TIMEOUT_MS);
    if (!image) return null;

    const tone = sampleImageTone(image);
    const backdrop = document.createElement('div');
    backdrop.className = 'login-scene-backdrop';
    backdrop.setAttribute('aria-hidden', 'true');
    const imageLayer = document.createElement('div');
    imageLayer.className = 'login-scene-backdrop__image';
    imageLayer.style.backgroundImage = `url('${picked.url}')`;
    const veilLayer = document.createElement('div');
    veilLayer.className = 'login-scene-backdrop__veil';
    backdrop.append(imageLayer, veilLayer);
    document.body.prepend(backdrop);
    document.body.dataset.sceneTone = tone;
    window.requestAnimationFrame(() => document.body.classList.add('login-scene-active'));

    return { imageUrl: picked.url, categories: picked.categories, tone };
}

const sceneKey = Symbol.for('lanshare.login-scene.centered');

/** One scene owner per document. Image completion never replaces the form. */
export function initLoginScene() {
    const card = document.querySelector('[data-lq-login-card]');
    if (!card) return initLegacyLoginScene();
    if (document[sceneKey]) return document[sceneKey].promise;
    const root = document.documentElement;
    const frame = card.closest('[data-lq-login-material]');
    const abort = new AbortController();
    let disposed = false, loaded = false, backdrop = null, scene = null;
    let animationFrame = null;
    const deadline = performance.now() + IMAGE_PRELOAD_TIMEOUT_MS;
    const timer = window.setTimeout(() => abort.abort(), IMAGE_PRELOAD_TIMEOUT_MS);
    const refresh = () => {
        const clear = loaded && card.dataset.lqLoginCard === 'student'
            && root.dataset.lqTier === 'A' && root.dataset.lqGlass === 'tinted'
            && root.dataset.lqContrast !== 'more' && root.dataset.lqForcedColors !== 'true';
        card.classList.toggle('lq-glass--clear', clear);
        card.classList.toggle('lq-glass--thick', !clear);
        if (clear) card.dataset.lqTone = scene.tone;
        else delete card.dataset.lqTone;
        if (frame) frame.dataset.lqLoginMaterial = clear ? 'clear' : 'thick';
    };
    const pagehide = event => { if (!event.persisted) owner.dispose(); };
    const owner = { promise: null, dispose() {
        if (disposed) return;
        disposed = true; loaded = false;
        abort.abort(); window.clearTimeout(timer);
        if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
        root.removeEventListener('lq:theme-change', refresh);
        window.removeEventListener('pagehide', pagehide);
        backdrop?.remove();
        document.body.classList.remove('login-scene-active');
        delete document.body.dataset.sceneTone;
        card.dataset.lqSceneState = 'unavailable';
        refresh();
        if (document[sceneKey] === owner) delete document[sceneKey];
    } };
    document[sceneKey] = owner;
    card.dataset.lqSceneState = 'loading';
    root.addEventListener('lq:theme-change', refresh);
    window.addEventListener('pagehide', pagehide);
    refresh();
    owner.promise = (async () => {
        try {
            const picked = await pickSceneImage(abort.signal);
            if (!picked || abort.signal.aborted || disposed) return null;
            const url = new URL(picked.url, window.location.href);
            if (url.origin !== window.location.origin || !url.pathname.startsWith(IMAGE_BASE)) return null;
            const remaining = deadline - performance.now();
            if (remaining <= 0) return null;
            const image = await preloadSceneImage(url.href, remaining, abort.signal);
            if (!image || abort.signal.aborted || disposed || !card.isConnected) return null;
            scene = { imageUrl: picked.url, categories: picked.categories, tone: sampleImageTone(image), dispose: owner.dispose };
            backdrop = document.createElement('div');
            backdrop.className = 'login-scene-backdrop';
            backdrop.setAttribute('aria-hidden', 'true');
            const imageLayer = document.createElement('div');
            imageLayer.className = 'login-scene-backdrop__image';
            imageLayer.style.backgroundImage = `url(${JSON.stringify(url.href)})`;
            const veil = document.createElement('div');
            veil.className = 'login-scene-backdrop__veil';
            backdrop.append(imageLayer, veil);
            document.body.prepend(backdrop);
            document.body.dataset.sceneTone = scene.tone;
            loaded = true;
            card.dataset.lqSceneState = 'ready';
            refresh();
            animationFrame = window.requestAnimationFrame(() => { if (!disposed) document.body.classList.add('login-scene-active'); });
            return scene;
        } finally {
            window.clearTimeout(timer);
            if (!loaded && !disposed) {
                card.dataset.lqSceneState = 'unavailable';
                root.removeEventListener('lq:theme-change', refresh);
                window.removeEventListener('pagehide', pagehide);
                if (document[sceneKey] === owner) delete document[sceneKey];
            }
        }
    })();
    return owner.promise;
}

/** Keep the real button/name/label nodes stable during a pending submission. */
export function setLoginSubmitting(button, submitting) {
    if (!button?.classList.contains('lq-btn')) return false;
    button.disabled = submitting;
    button.classList.toggle('is-loading', submitting);
    button.setAttribute('aria-busy', String(submitting));
    if (submitting && !button.querySelector('[data-login-spinner]')) {
        const node = document.createElement('span');
        node.className = 'lq-btn__spinner'; node.dataset.loginSpinner = '';
        node.setAttribute('aria-hidden', 'true'); node.append(spinner({ size: 'sm' }));
        button.append(node);
    } else if (!submitting) button.querySelector('[data-login-spinner]')?.remove();
    return true;
}

export function setLoginFeedback(form, message = '') {
    const feedback = form?.querySelector('[data-login-feedback]');
    if (!feedback) return false;
    feedback.textContent = message;
    feedback.hidden = !message;
    // Authentication does not disclose which credential was wrong.
    const described = new Set((form.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    if (message) described.add(feedback.id); else described.delete(feedback.id);
    if (described.size) form.setAttribute('aria-describedby', [...described].join(' '));
    else form.removeAttribute('aria-describedby');
    if (message) feedback.focus({ preventScroll: false });
    return true;
}

/**
 * 登录成功后的收尾：预取首页、表单变形为一言玻璃卡、写交棒状态并跳转。
 */
export function finishLoginWithScene({ scene, profile, loginTip, redirectTo, cardElement }) {
    const target = redirectTo || '/dashboard';
    const go = () => window.location.assign(target);

    // 一言展示期间顺手把首页拉进缓存，收缩动画结束时主区即刻可见。
    try {
        fetch(target, { credentials: 'same-origin' }).catch(() => {});
    } catch (error) {
        // 预取失败不影响流程。
    }

    try { playLoginSceneReveal(profile || null, {
        loginTip: loginTip || null,
        scene: scene || null,
        fromElement: cardElement || document.querySelector('.login-card'),
        onDone: go,
    }); } catch (_) { go(); }
}
