// 登录页「人生一言」场景：进页面即铺一言背景图 + 液态玻璃表单，
// 登录成功后表单原地化为一言玻璃卡，结束时把背景交棒给首页顶栏。
import { playLoginSceneReveal, sampleImageTone } from '/static/js/cultivation_identity.js?v=20260803-scene3';

const MANIFEST_URL = '/static/img/life_tips/manifest.json';
const IMAGE_BASE = '/static/img/life_tips/';
const IMAGE_PRELOAD_TIMEOUT_MS = 4500;

function preloadSceneImage(url, timeoutMs) {
    return new Promise((resolve) => {
        if (!url) {
            resolve(null);
            return;
        }
        const image = new Image();
        const timer = window.setTimeout(() => resolve(null), timeoutMs);
        image.onload = () => {
            window.clearTimeout(timer);
            resolve(image);
        };
        image.onerror = () => {
            window.clearTimeout(timer);
            resolve(null);
        };
        image.src = url;
    });
}

async function pickSceneImage() {
    try {
        const response = await fetch(MANIFEST_URL, { cache: 'force-cache' });
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
export async function initLoginScene() {
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

    playLoginSceneReveal(profile || null, {
        loginTip: loginTip || null,
        scene: scene || null,
        fromElement: cardElement || document.querySelector('.login-card'),
        onDone: go,
    });
}
