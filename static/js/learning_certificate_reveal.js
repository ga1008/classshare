import { apiFetch } from './api.js';
import { showToast } from './ui.js';
import { createDomainModal } from './lq/domain-modal.js';
import { getLayerSystem } from './lq/layer.js';
import { createDialog, openDialog } from './lq/dialogs.js';
import { createDomainButton } from './lq/domain-controls.js';
import { awardStyle, certificateFilename, createCertificateImage } from './learning_certificate_art.js';

const OWNERS = new WeakMap();
const REVEAL_DELAY_MS = 560;
function storageGet(key) { try { return window.localStorage?.getItem(key) || ''; } catch { return ''; } }
function storageSet(key) { try { window.localStorage?.setItem(key, '1'); } catch { /* Server reveal state is still durable. */ } }

function certificatePayload(card, certificate = {}) {
    return {
        id: String(card.dataset.certificateId || certificate.id || ''),
        title: String(card.dataset.certificateTitle || certificate.title || '破境道印'),
        level: String(card.dataset.certificateLevel || certificate.level_name || '新境界'),
        course: String(card.dataset.certificateCourse || certificate.course_name || '课堂'),
        student: String(card.dataset.certificateStudent || certificate.student_name || ''),
        code: String(card.dataset.certificateCode || certificate.certificate_code || ''),
        issued: String(card.dataset.certificateIssued || certificate.issued_at || ''),
        theme: String(card.dataset.theme || certificate.theme || certificate.level_key || certificate.stage_key || ''),
    };
}

/** A finite artwork celebration; LQ exclusively owns the surrounding layer motion. */
function celebrate(root, style) {
    const preference = root.closest('[data-lq-motion]')?.dataset.lqMotion
        || document.documentElement.dataset.lqMotion || 'standard';
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || preference === 'off' || preference === 'quiet') return [];
    const easing = getComputedStyle(root).getPropertyValue('--lq-motion-ease').trim() || 'ease-out';
    const animations = [];
    for (const [index, node] of [...root.querySelectorAll('[data-award-orbit]')].entries()) {
        animations.push(node.animate([
            { transform: `rotate(${index * 25 - 16}deg) scale(.84)`, opacity: 0 },
            { offset: .17, opacity: .8 },
            { transform: `rotate(${index * 25 + 28 + style.tier * 4}deg) scale(1.12)`, opacity: .28 },
        ], { duration: style.duration, easing, iterations: 1 }));
    }
    for (const [index, node] of [...root.querySelectorAll('[data-award-spark]')].entries()) {
        animations.push(node.animate([
            { transform: 'scale(.2)', opacity: 0 }, { offset: .3, transform: 'scale(1)', opacity: 1 },
            { transform: 'scale(.65)', opacity: .25 },
        ], { duration: style.duration * .8, delay: index * 45, easing, iterations: 1 }));
    }
    return animations;
}

export function initLearningCertificateReveal(config = window.APP_CONFIG || {}, options = {}) {
    const backdrop = options.root || document.getElementById('learning-certificate-backdrop');
    const card = backdrop?.querySelector('[data-learning-certificate-card]');
    if (!backdrop || !card) return null;
    if (OWNERS.has(backdrop)) return OWNERS.get(backdrop);
    const certificate = config?.learningProgress?.latest_unrevealed_certificate
        || config?.cultivationProfile?.latest_unrevealed_certificate || config?.learningCertificate
        || config?.latest_unrevealed_certificate || {};
    const payload = certificatePayload(card, certificate);
    if (!payload.id) return null;
    const style = awardStyle(payload.theme), storageKey = `learning-cert-seen:${payload.id}`;
    const art = card.querySelector('[data-award-image]'), scene = backdrop.querySelector('[data-award-scene]');
    art.src = style.url; art.alt = `${payload.level}道印`;
    scene.dataset.tier = String(style.tier);
    scene.style.setProperty('--award-color', style.primary);
    scene.style.setProperty('--award-light', style.light);
    const constellation = scene.querySelector('[data-award-constellation]');
    constellation.replaceChildren();
    for (let index = 0; index < style.particles; index++) {
        const spark = document.createElement('i'); spark.dataset.awardSpark = '';
        const radians = (index * 360 / style.particles - 90) * Math.PI / 180;
        spark.style.left = `${50 + 45 * Math.cos(radians)}%`;
        spark.style.top = `${50 + 45 * Math.sin(radians)}%`;
        constellation.append(spark);
    }
    let timer = null, destroyed = false, animations = [], saveLayer = null, saving = false, imagePromise = null;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const stopEffects = () => { animations.forEach(item => item.cancel()); animations = []; };
    const markRevealed = () => {
        if (options.historical) return;
        if (backdrop.dataset.revealMarked === 'true') return;
        backdrop.dataset.revealMarked = 'true';
        apiFetch(`/api/learning/certificates/${encodeURIComponent(payload.id)}/revealed`, { method: 'POST', silent: true })
            .catch(() => { backdrop.dataset.revealMarked = 'false'; });
    };
    const finish = () => { stopEffects(); backdrop.classList.remove('is-open'); if (options.historical) queueMicrotask(() => controller.destroy()); };
    const owner = createDomainModal(backdrop, {
        type: 'viewer', surface: backdrop.querySelector('.learning-certificate-shell'),
        closeOnOutside: true, initialFocus: backdrop.querySelector('.learning-certificate-shell'),
        onCloseRequested: () => { if (!options.historical) storageSet(storageKey); stopEffects(); }, onClose: finish, onDestroy: finish,
    });
    const imageBlob = () => {
        if (!imagePromise) imagePromise = createCertificateImage(payload).catch(error => { imagePromise = null; throw error; });
        return imagePromise;
    };
    const openSave = () => {
        if (destroyed || saveLayer || saving) return;
        const body = document.createElement('div'), description = document.createElement('p');
        description.textContent = '保存一枚属于你的道印，包含课程、姓名和颁发记录。'; body.append(description);
        const status = document.createElement('p'); status.className = 'lq-status'; status.setAttribute('role', 'status');
        status.textContent = 'PNG · 透明背景 · 高清图片'; body.append(status);
        const footer = document.createDocumentFragment();
        const cancel = createDomainButton({ label: '取消', variant: 'ghost' });
        const save = createDomainButton({ label: '保存道印', variant: 'prominent' }); footer.append(cancel, save);
        const dialog = createDialog({ title: '收藏这枚道印', body, footer, size: 'sm' });
        saveLayer = openDialog(dialog, { trigger: card, parentLayer: owner.handle, initialFocus: save,
            beforeClose: () => !saving, onClose: () => { saveLayer = null; }, onDestroy: () => { saveLayer = null; } });
        cancel.addEventListener('click', () => { if (saveLayer) void getLayerSystem(document).close(saveLayer, 'cancel'); });
        save.addEventListener('click', async () => {
            if (saving) return;
            saving = true; save.disabled = true; cancel.disabled = true; status.textContent = '正在准备道印图片…';
            try {
                // Invoke the OS picker in the click's activation before awaiting encoding.
                const file = typeof window.showSaveFilePicker === 'function'
                    ? await window.showSaveFilePicker({ suggestedName: certificateFilename(payload),
                        types: [{ description: 'PNG 道印图片', accept: { 'image/png': ['.png'] } }] }) : null;
                const blob = await imageBlob();
                if (destroyed) return;
                if (file) {
                    const stream = await file.createWritable();
                    try { await stream.write(blob); await stream.close(); }
                    catch (error) { await stream.abort().catch(() => {}); throw error; }
                } else {
                    const url = URL.createObjectURL(blob), link = document.createElement('a');
                    link.href = url; link.download = certificateFilename(payload); link.rel = 'noopener';
                    document.body.append(link); link.click(); link.remove();
                    window.setTimeout(() => URL.revokeObjectURL(url), 30000);
                }
                status.textContent = file ? '道印已保存。' : '道印已交给浏览器保存。';
                showToast(file ? '道印已保存' : '道印图片已生成，请查看浏览器下载', 'success');
            } catch (error) {
                if (!destroyed) status.textContent = error?.name === 'AbortError' ? '已取消保存，可以稍后再收藏。' : error.message || '保存失败，请重试。';
            } finally {
                saving = false; save.disabled = false; cancel.disabled = false;
            }
        });
    };
    card.addEventListener('click', openSave);
    media.addEventListener('change', stopEffects);
    const preferenceObserver = new MutationObserver(stopEffects);
    preferenceObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-lq-motion'] });
    const controller = { open() {
        if (destroyed) return;
        backdrop.classList.add('is-open'); owner.open({ trigger: options.trigger || document.activeElement }); markRevealed();
        stopEffects(); animations = celebrate(scene, style);
    }, close: () => owner.close('programmatic'), destroy() {
        if (destroyed) return;
        destroyed = true; window.clearTimeout(timer); stopEffects(); preferenceObserver.disconnect();
        media.removeEventListener('change', stopEffects); card.removeEventListener('click', openSave);
        saveLayer?.destroy(); owner.destroy(); OWNERS.delete(backdrop); window.removeEventListener('pagehide', controller.destroy);
        if (options.historical) backdrop.remove();
    } };
    OWNERS.set(backdrop, controller); window.addEventListener('pagehide', controller.destroy, { once: true });
    if (options.historical) { controller.open(); return controller; }
    if (storageGet(storageKey) === '1') { markRevealed(); return controller; }
    timer = window.setTimeout(controller.open, REVEAL_DELAY_MS);
    return controller;
}

let historySequence = 0;
/** Historical certificates come from the already-authorized progress response.
 * Viewing one never marks any certificate revealed, including a new award.
 */
export function showLearningCertificate(certificate, { trigger = document.activeElement } = {}) {
    if (!certificate?.id || !certificate?.certificate_code) throw new Error('这枚道印的颁发记录不完整，请刷新后重试。');
    const root = document.createElement('div'), id = `learning-certificate-history-${++historySequence}`;
    root.className = 'lq-domain-region learning-certificate-backdrop'; root.dataset.lqComponent = 'layer'; root.hidden = true;
    // Structure is static; all certificate/user values below are assigned as text.
    root.innerHTML = `<section class="learning-certificate-shell" role="dialog" data-ui-overlay-surface>
      <div class="learning-certificate-scene" data-award-scene>
        <div class="learning-certificate-aura" aria-hidden="true"></div>
        <div class="learning-certificate-orbit" data-award-orbit aria-hidden="true"></div>
        <div class="learning-certificate-orbit learning-certificate-orbit--outer" data-award-orbit aria-hidden="true"></div>
        <div class="learning-certificate-constellation" data-award-constellation aria-hidden="true"></div>
        <button type="button" class="lq-btn lq-btn--ghost lq-domain-choice learning-certificate-medal" data-lq-component="choice" data-lq-shape="surface" data-learning-certificate-card><img data-award-image width="640" height="640" draggable="false" alt=""></button>
      </div>
      <div class="lq-surface learning-certificate-caption" data-lq-component="surface" data-lq-material="raised"><span class="lq-status"></span><h3></h3><p></p><small>轻点道印收藏 · 点击空白处收起</small></div>
    </section>`;
    const shell = root.querySelector('section'), heading = root.querySelector('h3'), description = root.querySelector('p');
    heading.id = `${id}-title`; description.id = `${id}-description`;
    shell.setAttribute('aria-labelledby', heading.id); shell.setAttribute('aria-describedby', description.id);
    const level = certificate.level_name || certificate.name || '新境界';
    root.querySelector('.lq-status').textContent = `第 ${certificate.tier || awardStyle(certificate.level_key || certificate.key).tier} 境 · 破境道印`;
    heading.textContent = certificate.title || certificate.certificate_title || level;
    description.textContent = [certificate.course_name || '课堂', certificate.student_name].filter(Boolean).join(' · ');
    const card = root.querySelector('[data-learning-certificate-card]');
    card.setAttribute('aria-label', `收藏${level}道印`);
    document.body.append(root);
    return initLearningCertificateReveal({ learningCertificate: certificate }, { root, historical: true, trigger });
}
