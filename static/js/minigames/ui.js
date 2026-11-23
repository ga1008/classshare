import { createComponent } from '../lq/components.js';
import { getLayerSystem } from '../lq/layer.js';

export const apiBase = '/api/games/v1';
export const bootstrap = () => JSON.parse(document.querySelector('#game-bootstrap')?.textContent || '{}');
export const makeId = () =>
    crypto.randomUUID?.() ||
    Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => n.toString(16).padStart(2, '0')).join('');

export function setCsrfToken(token) {
    const meta = document.querySelector('meta[name="minigames-csrf"]');
    if (meta && typeof token === 'string' && token) meta.content = token;
}

export function authHeaders(key) {
    return {
        'X-LanGame-CSRF': document.querySelector('meta[name="minigames-csrf"]')?.content || '',
        'Idempotency-Key': key,
    };
}

export function responseError(status, data) {
    const detail = data?.detail ?? data?.error ?? data;
    const message =
        typeof detail === 'string' ? detail : detail?.message || detail?.code || '请求失败（' + status + '）';
    return Object.assign(new Error(message), { status, code: detail?.code, detail });
}

export async function api(path, { method = 'GET', body, key, signal } = {}) {
    const write = method !== 'GET';
    const headers = write ? authHeaders(key || makeId()) : {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const options = { method, headers, signal, credentials: 'same-origin', cache: 'no-store' };
    if (body !== undefined) options.body = JSON.stringify(body);
    let res;
    try {
        res = await fetch(apiBase + path, options);
    } catch (error) {
        if (!write || signal?.aborted) throw error;
        res = await fetch(apiBase + path, options);
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw responseError(res.status, data);
    return data;
}

export function el(tag, text, cls) {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICON_PATHS = {
    gamepad: [
        ['rect', { x: 2, y: 6, width: 20, height: 12, rx: 5 }],
        ['path', { d: 'M6.5 12h4M8.5 10v4M15.2 10.8h.01M17.6 13.2h.01' }],
    ],
    grid: [
        ['rect', { x: 3.5, y: 3.5, width: 7, height: 7, rx: 1.8 }],
        ['rect', { x: 13.5, y: 3.5, width: 7, height: 7, rx: 1.8 }],
        ['rect', { x: 3.5, y: 13.5, width: 7, height: 7, rx: 1.8 }],
        ['rect', { x: 13.5, y: 13.5, width: 7, height: 7, rx: 1.8 }],
    ],
    solo: [
        ['circle', { cx: 12, cy: 8.2, r: 3.6 }],
        ['path', { d: 'M5.5 19.5a6.5 6.5 0 0 1 13 0' }],
    ],
    coop: [
        ['circle', { cx: 9, cy: 8.5, r: 3.1 }],
        ['circle', { cx: 15.8, cy: 9.4, r: 2.5 }],
        ['path', { d: 'M3.5 19.2a5.5 5.5 0 0 1 11 0M14.8 19.2a4.4 4.4 0 0 1 6-3.9' }],
    ],
    versus: [
        ['path', { d: 'M14.5 17.5 3 6V3h3l11.5 11.5M13 19l6-6M16 16l4 4' }],
        ['path', { d: 'M9.5 17.5 21 6V3h-3L6.5 14.5M11 19l-6-6M8 16l-4 4' }],
    ],
    radar: [
        ['circle', { cx: 12, cy: 12, r: 2.2 }],
        ['path', { d: 'M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4' }],
        ['path', { d: 'M19.2 4.8a10.2 10.2 0 0 1 0 14.4M4.8 19.2a10.2 10.2 0 0 1 0-14.4' }],
    ],
    key: [
        ['circle', { cx: 8, cy: 15, r: 4 }],
        ['path', { d: 'm10.8 12.2 9.2-9.2M16 7l3 3M18.5 4.5 21 7' }],
    ],
    plus: [['path', { d: 'M12 5v14M5 12h14' }]],
    teams: [
        ['circle', { cx: 9, cy: 7.5, r: 3.3 }],
        ['path', { d: 'M3 19.5v-1.2A4.3 4.3 0 0 1 7.3 14h3.4A4.3 4.3 0 0 1 15 18.3v1.2' }],
        ['path', { d: 'M15.8 4.4a3.2 3.2 0 0 1 0 6.2M21 19.5v-1a4 4 0 0 0-3-3.85' }],
    ],
    compass: [
        ['circle', { cx: 12, cy: 12, r: 8.8 }],
        ['polygon', { points: '15.8 8.2 13.5 13.5 8.2 15.8 10.5 10.5 15.8 8.2' }],
    ],
    message: [
        ['path', { d: 'M20 14.5a2.5 2.5 0 0 1-2.5 2.5H8l-4 3.5V6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5z' }],
        ['path', { d: 'M8.5 10h7M8.5 13.2h4.5' }],
    ],
    trophy: [
        ['path', { d: 'M7 4h10v5a5 5 0 0 1-10 0zM7 6H4.5a2 2 0 0 0 0 4H7M17 6h2.5a2 2 0 0 1 0 4H17' }],
        ['path', { d: 'M12 14v4M8.5 20h7' }],
    ],
    volume: [
        ['polygon', { points: '11 5 6 9 3 9 3 15 6 15 11 19 11 5' }],
        ['path', { d: 'M15.5 8.5a5 5 0 0 1 0 7M18.5 5.8a8.8 8.8 0 0 1 0 12.4' }],
    ],
    mute: [
        ['polygon', { points: '11 5 6 9 3 9 3 15 6 15 11 19 11 5' }],
        ['path', { d: 'm21 9-5 6M16 9l5 6' }],
    ],
    book: [
        ['path', { d: 'M4 5.5A2.5 2.5 0 0 1 6.5 3H19.5v16H6.5A2.5 2.5 0 0 0 4 21.5z' }],
        ['path', { d: 'M6.5 19H20M8.5 7.5h6.5M8.5 11h4.5' }],
    ],
    refresh: [
        ['path', { d: 'M20 11a8 8 0 0 0-14.6-4.5L4 8M4 3.5V8h4.5' }],
        ['path', { d: 'M4 13a8 8 0 0 0 14.6 4.5L20 16M20 20.5V16h-4.5' }],
    ],
    sliders: [['path', { d: 'M4 6h10M18 6h2M14 4v4M4 12h4M12 12h8M8 10v4M4 18h12M16 16v4' }]],
    sparkles: [
        ['path', { d: 'm12 3 1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z' }],
        ['path', { d: 'M19 15v4M17 17h4M5 16v3M3.5 17.5h3' }],
    ],
    upload: [
        ['path', { d: 'M12 15V4M7.5 8.5 12 4l4.5 4.5' }],
        ['path', { d: 'M4 16.5v2A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5v-2' }],
    ],
    eye: [
        ['path', { d: 'M2.2 12S5.8 5.5 12 5.5 21.8 12 21.8 12 18.2 18.5 12 18.5 2.2 12 2.2 12z' }],
        ['circle', { cx: 12, cy: 12, r: 2.8 }],
    ],
    check: [['path', { d: 'm5 12.5 4.5 4.5L19 7.5' }]],
    rocket: [
        ['path', { d: 'M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z' }],
        ['path', { d: 'm12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z' }],
        ['path', { d: 'M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5' }],
    ],
    pause: [
        ['rect', { x: 6, y: 5, width: 4, height: 14, rx: 1 }],
        ['rect', { x: 14, y: 5, width: 4, height: 14, rx: 1 }],
    ],
    trash: [
        ['path', { d: 'M4 7h16M9.5 7V4.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V7' }],
        ['path', { d: 'M6.5 7l.8 12a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4l.8-12M10 11v5M14 11v5' }],
    ],
    copy: [
        ['rect', { x: 9, y: 9, width: 11, height: 11, rx: 2 }],
        ['path', { d: 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1' }],
    ],
    rotate: [['path', { d: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5' }]],
    shield: [
        ['path', { d: 'M12 3 4.5 6v5.5c0 4.8 3.2 8.7 7.5 9.8 4.3-1.1 7.5-5 7.5-9.8V6z' }],
        ['path', { d: 'm9 12 2 2 4-4' }],
    ],
    clock: [
        ['circle', { cx: 12, cy: 12, r: 8.5 }],
        ['path', { d: 'M12 7.5V12l3 2' }],
    ],
    bolt: [['polygon', { points: '13 2 4 14 11 14 10 22 20 10 13 10 13 2' }]],
    exit: [
        ['path', { d: 'M9 20H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4' }],
        ['path', { d: 'm16 17 5-5-5-5M21 12H9' }],
    ],
    crown: [['path', { d: 'm3 18 2-11 4.5 4L12 5l2.5 6L19 7l2 11zM4 21h16' }]],
};

export function svgIcon(name, cls = 'lq-icon') {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.9');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    if (cls) svg.setAttribute('class', cls);
    for (const [tag, attrs] of ICON_PATHS[name] || ICON_PATHS.gamepad) {
        const node = document.createElementNS(SVG_NS, tag);
        for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
        svg.append(node);
    }
    return svg;
}

let tooltipModulePromise = null;
const tooltipOwner = new WeakMap();
export function bindIconTooltips(root = document) {
    const targets = [...root.querySelectorAll('[data-game-tip]')].filter(
        (n) =>
            !tooltipOwner.has(n) &&
            n.matches('button,[role="button"]') &&
            !n.textContent.trim() &&
            n.querySelector('svg,img')
    );
    if (!targets.length) return;
    tooltipModulePromise ||= import('../lq/tooltips.js').catch(() => null);
    tooltipModulePromise.then((mod) => {
        if (!mod?.bindTooltip) return;
        for (const trigger of targets) {
            if (!trigger.isConnected || tooltipOwner.has(trigger) || trigger.textContent.trim()) continue;
            const text = (trigger.getAttribute('data-game-tip') || trigger.getAttribute('aria-label') || '').trim();
            if (!text) continue;
            tooltipOwner.set(
                trigger,
                mod.bindTooltip(trigger, { text }, { hoverDelay: 380, observeRemoval: true })
            );
        }
    });
}

export function setButtonIcon(btn, iconName) {
    const slot = btn?.querySelector('.lq-btn__icon');
    if (slot) slot.replaceChildren(svgIcon(iconName));
}

export function button(label, click, variant = 'soft', opts = {}) {
    const iconOnly = !!opts.iconOnly;
    const accessible = opts.ariaLabel || label;
    const attrs = { ...(opts.attrs || {}) };
    if (iconOnly) {
        attrs['aria-label'] = accessible;
        attrs['data-game-tip'] = accessible;
    }
    const n = createComponent('button', {
        label: iconOnly ? '' : label,
        variant,
        size: opts.size || 'sm',
        icon: opts.icon ? 'play' : null,
        attrs,
    });
    if (opts.icon) setButtonIcon(n, opts.icon);
    n.addEventListener('click', (e) => {
        e.preventDefault();
        run(n, click);
    });
    if (iconOnly) queueMicrotask(() => bindIconTooltips(n.parentElement || document));
    return n;
}

export function statusChip(label, tone = 'neutral') {
    return createComponent('chip', { label, kind: 'status', tone, size: 'sm' });
}

export function notice(message, root = document) {
    const n = root.querySelector('[data-game-notice]') || document.querySelector('[data-game-notice]');
    if (n) {
        n.textContent = message || '';
        n.hidden = !message;
    }
}

export async function run(control, callback) {
    if (control?.disabled) return;
    if (control) control.disabled = true;
    try {
        return await callback();
    } catch (e) {
        if (e.name !== 'AbortError') notice(e.message);
    } finally {
        if (control) control.disabled = false;
    }
}

export function showControl(name, show) {
    document.querySelectorAll('[data-game-control="' + name + '"]').forEach((n) => (n.hidden = !show));
}

export function onAction(mapping) {
    document.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-action]');
        if (btn && Object.hasOwn(mapping, btn.dataset.action)) {
            event.preventDefault();
            run(btn, () => mapping[btn.dataset.action](btn));
        }
    });
}

export function field(text, name, type = 'text', value = '') {
    const label = el('label', null, 'game-field'),
        input = el('input', null, 'lq-input');
    input.name = name;
    input.type = type;
    input.value = value ?? '';
    input.dataset.lqComponent = 'input';
    label.append(el('span', text), input);
    return label;
}

export function choiceField(text, name, options, selected = '') {
    const label = el('label', null, 'game-field'),
        input = el('select', null, 'lq-select');
    input.name = name;
    input.dataset.lqComponent = 'select';
    for (const [value, caption] of options)
        input.add(new Option(caption, value, false, String(value) === String(selected)));
    label.append(el('span', text), input);
    return label;
}

export async function panel(title, body, options = {}) {
    const { openDialog } = await import('../lq/dialogs.js');
    return openDialog(
        { title, body, type: options.type || 'modal' },
        { trigger: options.trigger || document.activeElement, ...options }
    );
}

export function closePanel(handle) {
    return getLayerSystem(document).close(handle, 'selected');
}

export async function confirmAction(title, message, danger = false) {
    const { confirm } = await import('../lq/dialogs.js');
    return confirm({ title, message, danger, confirmLabel: danger ? '确认执行' : '确认' });
}

export async function formPanel(title, content, label, submit, opts = {}) {
    const form = content.tagName === 'FORM' ? content : el('form', null, 'game-form');
    if (form !== content) form.append(content);
    if (!form.querySelector('[type="submit"]'))
        form.append(
            createComponent('button', {
                label,
                type: 'submit',
                variant: opts.danger ? 'destructive' : 'prominent',
            })
        );
    const status = el('p', null, 'lq-status');
    status.setAttribute('role', 'status');
    status.hidden = true;
    form.append(status);
    let saving = false;
    const handle = await panel(title, form, { ...opts, beforeClose: () => !saving });
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (e.isComposing || saving || !form.reportValidity()) return;
        const btn = form.querySelector('[type="submit"]');
        btn.disabled = true;
        saving = true;
        try {
            status.hidden = true;
            await submit(new FormData(form), form);
            saving = false;
            await getLayerSystem(document).close(handle, 'submitted');
        } catch (err) {
            status.textContent = err.message;
            status.hidden = false;
        } finally {
            btn.disabled = false;
            saving = false;
        }
    });
    return handle;
}

export function modeText(g) {
    const m = g.config?.modes;
    if (!m) return '';
    const parts = m.solo ? ['单人'] : [];
    if (m.multiplayer) {
        const { minPlayers, maxPlayers, kind } = m.multiplayer;
        parts.push(
            (minPlayers === maxPlayers ? minPlayers : minPlayers + '–' + maxPlayers) +
                ' 人' +
                (kind === 'coop' ? '合作' : '对战')
        );
    }
    return parts.join(' · ');
}

export function modeBadges(g) {
    const m = g.config?.modes;
    const wrap = el('span', null, 'game-mode-badges');
    if (!m) return wrap;
    if (m.solo) {
        const pill = el('span', null, 'game-mode-pill');
        pill.dataset.modeKind = 'solo';
        pill.append(svgIcon('solo', 'game-mini-icon'), el('span', '单人'));
        wrap.append(pill);
    }
    if (m.multiplayer) {
        const { minPlayers, maxPlayers, kind } = m.multiplayer;
        const range = minPlayers === maxPlayers ? String(minPlayers) : minPlayers + '–' + maxPlayers;
        const pill = el('span', null, 'game-mode-pill');
        pill.dataset.modeKind = kind === 'coop' ? 'coop' : 'versus';
        pill.append(
            svgIcon(kind === 'coop' ? 'coop' : 'versus', 'game-mini-icon'),
            el('span', range + ' ' + (kind === 'coop' ? '合作' : '对战'))
        );
        wrap.append(pill);
    }
    return wrap;
}

export function gameHue(g) {
    const alpha = '0123456789abcdefghijklmnopqrstuvwxyz-_';
    const seed = String(g?.slug || g?.gameKey || g?.id || '').toLowerCase();
    let h = 0;
    for (const ch of seed) {
        const idx = alpha.indexOf(ch);
        h = (h * 31 + (idx >= 0 ? idx : 7)) % 6;
    }
    return String(h);
}

export function libraryLink(g, workspace = '/manage/library/games') {
    const a = el('a', g.title, 'game-library-link');
    a.href = workspace + '/' + encodeURIComponent(g.id);
    return a;
}

export function gameLink(g) {
    const a = el('a');
    a.href = '/games/' + encodeURIComponent(g.slug);
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    return a;
}

export function gameIcon(g, { eager = false } = {}) {
    const span = el('span', null, 'game-icon');
    span.setAttribute('aria-hidden', 'true');
    span.dataset.emblemTone = gameHue(g);
    const kind = g?.config?.modes?.multiplayer?.kind;
    span.append(svgIcon(kind === 'versus' ? 'versus' : kind === 'coop' ? 'compass' : 'gamepad'));
    const url = g?.iconUrl || (g?.id ? '/api/games/v1/games/' + encodeURIComponent(g.id) + '/icon' : '');
    if (url) {
        const image = el('img');
        image.width = 80;
        image.height = 80;
        image.alt = '';
        image.loading = eager ? 'eager' : 'lazy';
        image.decoding = 'async';
        image.src = url;
        span.append(image);
    }
    return span;
}

document.addEventListener(
    'error',
    (e) => {
        if (e.target?.matches?.('.game-icon img')) e.target.remove();
    },
    true
);
queueMicrotask(() => bindIconTooltips(document));
