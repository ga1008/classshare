/**
 * 修为境界标志 — decorates every student avatar on the page with the student's
 * highest level emblem (across all classrooms) and lets anyone hover/focus it to
 * see the level and 修为值. Students who hide their cultivation get no emblem.
 *
 * Recognised avatars (no per-page wiring needed):
 *   - <img src="/api/profile/avatar?role=student&user_id=N…">  → student N
 *   - <img src="/api/profile/avatar…"> without role             → the signed-in user
 *   - any element with data-cultivation-student-id="N"          → student N (initials avatars)
 * Opt out with data-cultivation-emblem="off" on the avatar or an ancestor.
 *
 * Data: GET /api/cultivation/emblems?students=self,12,13 (batched, cached).
 * Hover text uses the shared LQ tooltip (bound lazily on first hover/focus).
 * Also owns the 隐藏修为 switch in profile settings ([data-cultivation-visibility]).
 */
import { bindTooltip } from './lq/tooltips.js';

const INIT_KEY = Symbol.for('lanshare.cultivation-emblems.v1');
const EMBLEM_ROOT = '/static/img/cultivation/emblems/';
const CACHE_TTL_MS = 60 * 1000;
const STORAGE_KEY = 'lanshare.cultivation-emblems.v1';
const AVATAR_SELECTOR = 'img[src*="/api/profile/avatar"], [data-cultivation-student-id]';
const INTERACTIVE = 'a[href], button, [role="button"]:not(.lq-cultivation-emblem), summary, label, [role="menuitem"], [role="link"]';
export const VISIBILITY_EVENT = 'lanshare:cultivation-visibility';

export const EMBLEM_KEYS = Object.freeze([
    'qi_awakening', 'qi_refining', 'foundation', 'application_seed', 'golden_core',
    'practical_mastery', 'systems_thinking', 'nascent_soul', 'independent_path', 'mentor_heart',
]);

/** @param {string} key */
export function emblemUrl(key) {
    return EMBLEM_KEYS.includes(key) ? `${EMBLEM_ROOT}${key}.svg` : '';
}

/**
 * Plain emblem image (no tooltip) for panels and rankings.
 * @param {{key?: string, name?: string}|null|undefined} level
 * @param {{size?: number, className?: string}} [options]
 */
export function emblemImage(level, { size = 32, className = '' } = {}) {
    const url = emblemUrl(level?.key || '');
    if (!url) return null;
    const img = document.createElement('img');
    img.className = `lq-cultivation-emblem-img ${className}`.trim();
    img.src = url;
    img.width = size;
    img.height = size;
    img.alt = level?.name ? `${level.name}标志` : '';
    img.decoding = 'async';
    img.draggable = false;
    return img;
}

/** @param {{name?: string, tier?: number, certificate_title?: string, score?: number}} payload */
export function emblemLabel(payload) {
    const parts = [`${payload.name || '境界'} · 第 ${payload.tier || '?'} 境`];
    if (payload.score !== undefined && payload.score !== null) parts.push(`修为 ${Number(payload.score).toFixed(1).replace(/\.0$/, '')}`);
    if (payload.certificate_title) parts.push(`持「${payload.certificate_title}」`);
    return parts.join(' · ');
}

function readStorage() {
    try {
        const raw = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || '{}');
        return raw && typeof raw === 'object' ? raw : {};
    } catch {
        return {};
    }
}

function writeStorage(entries) {
    try { window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries)); } catch { /* quota or privacy mode */ }
}

function createController(doc) {
    const cache = new Map(Object.entries(readStorage()));
    const tracked = new Map(); // element -> { el, id, host, emblem }
    const pending = new Set();
    let timer = null;
    cache.delete('self');
    let selfId = null;

    const fresh = (entry) => Boolean(entry) && Date.now() - entry.t < CACHE_TTL_MS;
    // 'self' lives only in memory so a shared browser never shows the previous user's emblem.
    const persist = () => writeStorage(Object.fromEntries([...cache.entries()].filter(([key, entry]) => key !== 'self' && fresh(entry))));

    function identify(el) {
        if (el.closest('[data-cultivation-emblem="off"], .lq-cultivation-emblem, .lq-cultivation')) return null;
        const explicit = el.getAttribute('data-cultivation-student-id');
        if (explicit) return /^\d+$/.test(explicit) ? explicit : null;
        if (el.tagName !== 'IMG') return null;
        let url;
        try { url = new URL(el.getAttribute('src') || '', doc.baseURI); } catch { return null; }
        if (url.pathname !== '/api/profile/avatar') return null;
        const role = url.searchParams.get('role');
        const userId = url.searchParams.get('user_id');
        if (!role && !userId) return 'self';
        return role === 'student' && /^\d+$/.test(userId || '') ? userId : null;
    }

    function hostFor(el) {
        if (el.tagName !== 'IMG') return el;
        const parent = el.parentElement;
        if (parent?.classList.contains('lq-cultivation-host') || parent?.classList.contains('lq-cultivation-host--positioned')) return parent;
        if (parent && !parent.matches('a, button, body, li, p, td, label') && parent.children.length <= 2
            && (parent.textContent || '').trim().length <= 2) {
            return parent;
        }
        const wrap = doc.createElement('span');
        wrap.className = 'lq-cultivation-host lq-cultivation-host--wrap';
        el.replaceWith(wrap);
        wrap.append(el);
        return wrap;
    }

    function resolve(id) {
        return cache.get(String(id));
    }

    function render(record) {
        const payload = resolve(record.id)?.payload || null;
        if (!payload || !emblemUrl(payload.key)) {
            record.emblem?.remove();
            record.emblem = null;
            if (record.host && !record.host.classList.contains('lq-cultivation-host--wrap')) record.host.classList.remove('lq-cultivation-host', 'lq-cultivation-host--positioned');
            return;
        }
        if (!record.host || !record.host.isConnected) record.host = hostFor(record.el);
        const host = record.host;
        if (!host.classList.contains('lq-cultivation-host') && !host.classList.contains('lq-cultivation-host--positioned')) {
            // Never demote an absolutely/fixed/sticky positioned avatar to relative.
            const position = host.classList.contains('lq-cultivation-host--wrap') ? 'static' : getComputedStyle(host).position;
            host.classList.add(position === 'static' ? 'lq-cultivation-host' : 'lq-cultivation-host--positioned');
        }
        const label = emblemLabel(payload);
        let emblem = record.emblem;
        if (!emblem || !emblem.isConnected) {
            emblem = doc.createElement('span');
            emblem.className = 'lq-cultivation-emblem';
            emblem.setAttribute('role', 'button');
            const img = doc.createElement('img');
            img.alt = '';
            img.decoding = 'async';
            img.draggable = false;
            emblem.append(img);
            record.host.append(emblem);
            record.emblem = emblem;
        }
        const nested = record.host.parentElement?.closest(INTERACTIVE) || record.host.closest('[aria-hidden="true"]');
        emblem.tabIndex = nested ? -1 : 0;
        emblem.dataset.tier = String(payload.tier || '');
        if (emblem.dataset.tooltipText && emblem.dataset.tooltipText !== label && emblem.dataset.tooltipBound) {
            // The bound tooltip froze the old text: drop it so the next hover rebinds.
            try { bindTooltip(emblem, { text: emblem.dataset.tooltipText }).destroy(); } catch { /* not bound */ }
            delete emblem.dataset.tooltipBound;
            emblem.removeAttribute('aria-label');
        }
        emblem.dataset.tooltipText = label;
        emblem.setAttribute('aria-label', `修为境界：${label}`);
        const img = emblem.querySelector('img');
        const src = emblemUrl(payload.key);
        if (img.getAttribute('src') !== src) img.src = src;
    }

    function track(el) {
        if (el.classList?.contains('lq-cultivation-emblem') || el.closest?.('.lq-cultivation-emblem')) return;
        const id = identify(el);
        const existing = tracked.get(el);
        if (!id) {
            if (existing) { existing.emblem?.remove(); tracked.delete(el); }
            return;
        }
        const record = existing && existing.id === id ? existing : { el, id, host: existing?.host || null, emblem: existing?.emblem || null };
        tracked.set(el, record);
        if (fresh(resolve(id))) render(record);
        else { pending.add(id); schedule(); }
    }

    function schedule() {
        if (timer) return;
        timer = window.setTimeout(flush, 60);
    }

    async function flush() {
        timer = null;
        const ids = [...pending].slice(0, 200);
        ids.forEach((id) => pending.delete(id));
        if (!ids.length) return;
        try {
            const response = await fetch(`/api/cultivation/emblems?students=${encodeURIComponent(ids.join(','))}`, {
                credentials: 'same-origin', headers: { Accept: 'application/json' },
            });
            if (!response.ok) {
                if (response.status >= 500) ids.forEach((id) => pending.add(id));
                return;
            }
            const data = await response.json();
            const items = data?.items && typeof data.items === 'object' ? data.items : {};
            const now = Date.now();
            if (data?.self_id !== undefined && data.self_id !== null) selfId = Number(data.self_id);
            for (const id of ids) {
                const realId = id === 'self' ? (selfId === null ? null : String(selfId)) : String(id);
                const payload = realId ? items[realId] || null : null;
                const entry = { t: now, payload };
                if (realId) cache.set(realId, entry);
                if (id === 'self') cache.set('self', { ...entry, selfId });
            }
            persist();
            for (const record of tracked.values()) if (ids.includes(record.id)) render(record);
        } catch {
            /* Emblems are decorative: a failed lookup leaves avatars untouched. */
        } finally {
            if (pending.size) schedule();
        }
    }

    function scan(root) {
        if (!root || root.nodeType !== 1) return;
        if (root.matches?.(AVATAR_SELECTOR)) track(root);
        root.querySelectorAll?.(AVATAR_SELECTOR).forEach(track);
    }

    function prune() {
        for (const [el, record] of tracked) {
            if (!el.isConnected) { record.emblem?.remove(); tracked.delete(el); }
        }
    }

    const observer = new MutationObserver((mutations) => {
        let removed = false;
        for (const mutation of mutations) {
            if (mutation.type === 'attributes') { track(mutation.target); continue; }
            mutation.addedNodes.forEach((node) => {
                if (node.nodeType === 1 && !node.classList?.contains('lq-cultivation-emblem')) scan(node);
            });
            if (mutation.removedNodes.length) removed = true;
        }
        if (removed) prune();
    });

    function showTooltip(emblem) {
        const text = emblem.dataset.tooltipText;
        if (!text) return;
        try {
            bindTooltip(emblem, { text }).show();
        } catch {
            delete emblem.dataset.tooltipBound; // allow a retry on the next hover
        }
    }

    function onFirstContact(event) {
        const emblem = event.target.closest?.('.lq-cultivation-emblem');
        if (!emblem || emblem.dataset.tooltipBound) return;
        emblem.dataset.tooltipBound = '1';
        if (event.type === 'focusin' || event.pointerType !== 'touch') showTooltip(emblem);
    }

    function refreshSelf() {
        const keys = ['self'];
        if (selfId !== null) keys.push(String(selfId));
        keys.forEach((key) => cache.delete(key));
        persist();
        for (const record of tracked.values()) {
            if (keys.includes(record.id)) pending.add(record.id);
        }
        schedule();
    }

    return {
        start() {
            scan(doc.body);
            observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'data-cultivation-student-id'] });
            doc.addEventListener('pointerover', onFirstContact, true);
            doc.addEventListener('focusin', onFirstContact, true);
            window.addEventListener(VISIBILITY_EVENT, refreshSelf);
        },
        scan,
        refreshSelf,
    };
}

async function initVisibilitySwitch(doc) {
    const input = doc.querySelector('[data-cultivation-visibility-input]');
    if (!input) return;
    const [{ apiFetch }, { showToast }] = await Promise.all([import('./api.js'), import('./ui.js')]);
    try {
        const state = await apiFetch('/api/profile/cultivation-visibility', { silent: true });
        input.checked = Boolean(state?.hidden);
        input.disabled = !state?.applicable;
    } catch {
        input.disabled = true;
        return;
    }
    input.addEventListener('change', async () => {
        const wanted = input.checked;
        input.disabled = true;
        try {
            const result = await apiFetch('/api/profile/cultivation-visibility', {
                method: 'PUT', silent: true,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hidden: wanted }),
            });
            input.checked = Boolean(result?.hidden);
            showToast(result?.message || '已保存', 'success');
            window.dispatchEvent(new CustomEvent(VISIBILITY_EVENT, { detail: { hidden: input.checked } }));
        } catch (error) {
            input.checked = !wanted;
            showToast(error?.message || '保存失败，请稍后重试', 'error');
        } finally {
            input.disabled = false;
        }
    });
}

export function initCultivationEmblems(doc = document) {
    if (doc[INIT_KEY]) return doc[INIT_KEY];
    const controller = createController(doc);
    doc[INIT_KEY] = controller;
    const begin = () => { controller.start(); void initVisibilitySwitch(doc); };
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', begin, { once: true });
    else begin();
    return controller;
}

if (typeof document !== 'undefined' && document.documentElement?.dataset?.cultivationEmblems !== 'off') {
    initCultivationEmblems(document);
}
