import { initTheme } from './lq/theme.js';

/** SSR is authoritative. No browser cache crosses account boundaries. */
export const PALETTE_KEYS = Object.freeze(['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']);
export const normalizePalette = value => PALETTE_KEYS.includes(value) ? value : 'indigo';
// Mirrors BACKDROP_CATEGORIES in user_ui_preferences_service.py; the parity is
// asserted by tests/test_user_ui_preferences_backdrop.py so the two cannot drift.
export const BACKDROP_CATEGORY_KEYS = Object.freeze(['academic-rules', 'thesis', 'teaching', 'career', 'research', 'postgrad', 'life', 'wellbeing', 'internship', 'industry', 'civil-service', 'graduation', 'scholarship', 'interview', 'contract']);
export const BACKDROP_MODES = Object.freeze(['scene', 'off', ...BACKDROP_CATEGORY_KEYS.map(key => `scene-${key}`)]);
// A colour only ever reaches a style through this exact shape. Anything else is
// replaced by the default; no arbitrary string is written to a custom property.
export const BACKDROP_COLOR = /^#[0-9a-f]{6}$/;
export const DEFAULT_BACKDROP_COLOR = '#' + 'f'.repeat(6);
const BACKDROP_FILE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(?:webp|jpg|jpeg|png)$/;
const FIELDS = Object.freeze(['palette_key', 'appearance', 'glass', 'backdrop', 'backdrop_color']);
const LABELS = { palette_key: '配色', appearance: '外观', glass: '玻璃效果', backdrop: '页面背景', backdrop_color: '背景纯色' };
const VALUE_LABELS = { auto: '跟随系统', light: '浅色', dark: '深色', tinted: '柔和玻璃', off: '关闭玻璃', teal: '青碧', indigo: '靛蓝', sky: '晴空', mint: '薄荷', violet: '紫罗兰', rose: '玫瑰', scene: '开屏大图' };
const normalize = value => ({
    palette_key: normalizePalette(value.palette_key),
    appearance: ['auto', 'light', 'dark'].includes(value.appearance) ? value.appearance : 'auto',
    glass: value.glass === 'off' ? 'off' : 'tinted',
    backdrop: BACKDROP_MODES.includes(value.backdrop) ? value.backdrop : 'scene',
    backdrop_color: BACKDROP_COLOR.test(String(value.backdrop_color || '')) ? value.backdrop_color : DEFAULT_BACKDROP_COLOR,
});

/** FNV-1a, byte for byte with the server so a preview shows the saved image. */
export function stableIndex(seed, size) {
    if (!(size > 0)) return 0;
    let digest = 2166136261;
    for (const byte of new TextEncoder().encode(String(seed))) {
        digest = Math.imul(digest ^ byte, 16777619) >>> 0;
    }
    return digest % size;
}

/** Pick the same library file the server would for this account-day and mode. */
export function pickBackdropFile(images, { mode, seed, label = null }) {
    if (mode === 'off') return null;
    const library = images.filter(item => item && BACKDROP_FILE.test(String(item.file || '')))
        .map(item => ({ file: item.file, categories: Array.isArray(item.categories) ? item.categories : [] }))
        .sort((first, second) => first.file < second.file ? -1 : first.file > second.file ? 1 : 0);
    const pool = label ? library.filter(item => item.categories.includes(label)) : library;
    const source = pool.length ? pool : library;
    return source.length ? source[stableIndex(`${seed}|${mode}`, source.length)].file : null;
}

/** The fixed viewport-bottom layer. It is the surface glass is seen against,
 * so it never gains a backdrop-filter and never becomes a blur host itself. */
export function createBackdropLayer(root = document) {
    // Hosts without the SSR layer (or without a DOM at all) stay inert.
    const find = selector => (typeof root?.querySelector === 'function' ? root.querySelector(selector) : null);
    const layer = find('[data-lq-page-backdrop]');
    let catalog = [];
    try { catalog = JSON.parse(find('[data-lq-backdrop-catalog]')?.textContent || '[]'); }
    catch (_) { catalog = []; }
    const labelOf = mode => (Array.isArray(catalog) ? catalog : []).find(item => item?.key === mode)?.name || null;
    let library = null;
    let disposed = false;
    let generation = 0;

    function images() {
        if (!library) {
            const url = layer?.dataset.lqBackdropManifest;
            library = !url ? Promise.resolve([]) : fetch(url, { credentials: 'same-origin', cache: 'force-cache' })
                .then(response => response.ok ? response.json() : null)
                .then(data => Array.isArray(data?.images) ? data.images : [])
                .catch(() => []);
        }
        return library;
    }
    function paint(file, color) {
        layer.dataset.lqBackdropImageUrl = file ? layer.dataset.lqBackdropBase + file : '';
        // Only whitelisted library file names reach this property.
        layer.style.setProperty('--lq-backdrop-image', file ? `url(${layer.dataset.lqBackdropBase}${file})` : 'none');
        paintFrost(file, color);
    }

    /* The frost pair mirrors the pick onto the document root, where every
     * material reads it. Preview has to write it too: a panel paints the
     * pre-blurred slice itself, so a scene the account is still trying out
     * would otherwise keep frosting the previous image. Derivatives are
     * always .webp, whatever the source container was. */
    function paintFrost(file, color) {
        const root = layer.ownerDocument?.documentElement;
        if (!root) return;
        const name = file ? `${file.replace(/\.[^.]+$/, '')}.webp` : '';
        root.style.setProperty('--lq-frost-image', name ? `url(${layer.dataset.lqBackdropBase}frost/${name})` : 'none');
        // With no image there is nothing behind the glass but the chosen colour.
        root.style.setProperty('--lq-frost-base', file ? 'hsl(var(--ls-background))' : color);
        root.dataset.lqFrost = file ? 'on' : 'off';
    }
    return {
        layer,
        apply(values) {
            if (!layer || disposed) return;
            const mine = ++generation;
            layer.dataset.lqBackdropMode = values.backdrop;
            layer.dataset.lqBackdropColor = values.backdrop_color;
            layer.style.setProperty('--lq-backdrop-color', values.backdrop_color);
            if (values.backdrop === 'off') { paint(null, values.backdrop_color); return; }
            void images().then(list => {
                // A later choice already repainted; a slow manifest never wins.
                if (disposed || mine !== generation) return;
                paint(pickBackdropFile(list, { mode: values.backdrop, seed: layer.dataset.lqBackdropSeed || '', label: labelOf(values.backdrop) }), values.backdrop_color);
            });
        },
        dispose() { disposed = true; },
    };
}

/** Per-field user intent, with a single serialized whole-row CAS queue. */
export function createUIPreferencesController({ initial, request, onPreview = () => {}, onStatus = () => {}, onConfirmed = () => {}, debounceMs = 240 }) {
    const context = String(initial.context_token || '');
    let confirmed = { ...initial, ...normalize(initial) };
    const desired = normalize(initial);
    const dirty = new Set();
    const conflicts = new Map();
    const intents = Object.fromEntries(FIELDS.map(field => [field, 0]));
    let busy = false;
    let needsRecovery = initial.available === false;
    let uncertain = null;
    let halted = false;
    let identityChanged = false;
    let timer = null;
    let disposed = false;

    function status() {
        if (conflicts.size) {
            const values = [...conflicts].map(([field, value]) => `${LABELS[field]}（服务器：${VALUE_LABELS[value] || value}）`).join('、');
            onStatus('conflict', `${values}已更新。保留当前预览；请重新选择对应选项，或在该选项按回车确认。`);
        } else if (!dirty.size) onStatus('saved', '界面偏好已保存');
    }
    function accept(preferences) {
        if (!preferences || preferences.context_token !== context) throw Object.assign(new Error('identity_changed'), { code: 'identity_changed' });
        if (!Number.isInteger(preferences.version) || preferences.version < 0) throw new Error('invalid_version');
        confirmed = { ...preferences, ...normalize(preferences) };
        for (const field of conflicts.keys()) conflicts.set(field, confirmed[field]);
        let changed = false;
        for (const field of FIELDS) {
            if (!dirty.has(field) && desired[field] !== confirmed[field]) { desired[field] = confirmed[field]; changed = true; }
        }
        onConfirmed({ ...confirmed });
        if (changed) onPreview({ ...desired });
    }
    function acknowledge() {
        for (const field of [...dirty]) {
            if (desired[field] === confirmed[field]) { dirty.delete(field); conflicts.delete(field); }
        }
    }
    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(() => { timer = null; void flush(); }, debounceMs);
    }
    const writable = () => [...dirty].filter(field => !conflicts.has(field));
    function fail(error) {
        if (error?.code === 'identity_changed') {
            identityChanged = true;
            clearTimeout(timer);
            onStatus('identity_changed', '登录账号已变化，请刷新页面后再修改界面偏好。');
        } else {
            halted = true;
            needsRecovery = true;
            onStatus('error', '界面偏好未同步，当前为临时预览；重新选择或按回车重试时会先核对服务器。');
        }
    }
    async function flush() {
        if (disposed || busy || halted || identityChanged || !writable().length) return;
        busy = true;
        let sent = null;
        if (conflicts.size) status(); else onStatus('saving', '正在保存界面偏好…');
        try {
            if (needsRecovery) {
                const result = await request('GET');
                if (disposed) return;
                accept(result);
                needsRecovery = false;
                if (uncertain) {
                    for (const field of uncertain.fields) {
                        // Own committed value may be followed by a newer local
                        // choice. Never silently overwrite another writer.
                        if (dirty.has(field) && desired[field] !== confirmed[field] && confirmed[field] !== uncertain.before[field] && confirmed[field] !== uncertain.values[field]) conflicts.set(field, confirmed[field]);
                    }
                    uncertain = null;
                    acknowledge();
                }
            }
            const fields = writable();
            if (!fields.length) { status(); return; }
            sent = { fields, before: { ...confirmed }, values: { ...desired }, intents: { ...intents } };
            const payload = { version: confirmed.version };
            for (const field of fields) payload[field] = desired[field];
            const result = await request('PATCH', payload);
            if (disposed) return;
            accept(result);
            acknowledge();
            status();
        } catch (error) {
            if (disposed) return;
            if (error?.code === 'identity_changed') fail(error);
            else if (error?.status === 409 && sent) {
                // Choices made before seeing a conflict are not permission to
                // overwrite it. Only a fresh choice of that field unblocks it.
                for (const field of sent.fields) conflicts.set(field, confirmed[field]);
                try {
                    const latest = await request('GET');
                    if (disposed) return;
                    accept(latest);
                    for (const field of sent.fields) conflicts.set(field, confirmed[field]);
                    needsRecovery = false;
                    acknowledge();
                    status();
                } catch (refreshError) { fail(refreshError); }
            } else {
                if (sent) uncertain = sent;
                fail(error);
            }
        } finally {
            busy = false;
            if (!disposed && !halted && !identityChanged && writable().length) schedule();
        }
    }
    function select(field, value) {
        if (disposed || identityChanged || !FIELDS.includes(field)) return;
        desired[field] = normalize({ ...desired, [field]: value })[field];
        intents[field] += 1;
        dirty.add(field);
        conflicts.delete(field); // Consent applies to this field alone.
        halted = false;
        onPreview({ ...desired });
        if (conflicts.size) status(); else onStatus('preview', '正在预览界面偏好');
        schedule();
    }
    return {
        select, flush,
        retry(field) {
            if (disposed || identityChanged) return;
            if (FIELDS.includes(field)) select(field, desired[field]);
            else if (halted) { halted = false; schedule(); }
        },
        snapshot: () => ({ desired: { ...desired }, confirmed: { ...confirmed }, intents: { ...intents }, dirty: !!dirty.size, dirtyFields: [...dirty], conflicts: Object.fromEntries(conflicts), busy, needsRetry: halted || !!conflicts.size, identityChanged }),
        dispose() { disposed = true; clearTimeout(timer); },
    };
}

/** Legacy palette-only integrations retain callbacks and snapshot shape. */
export function createPaletteController(options) {
    const controller = createUIPreferencesController({ ...options, onPreview: values => options.onPreview?.(values.palette_key) });
    return {
        ...controller,
        select: value => controller.select('palette_key', value),
        retry: () => controller.retry('palette_key'),
        snapshot: () => { const state = controller.snapshot(); return { ...state, desired: state.desired.palette_key }; },
    };
}

export function initUserUIPreferences(documentRoot = document) {
    // Most pages have no controls but still follow system/accessibility changes.
    const theme = initTheme(documentRoot);
    const scope = documentRoot.body;
    if (!scope?.hasAttribute('data-ui-palette') || scope.dataset.uiPaletteMounted === 'true') return null;
    const selects = [...scope.querySelectorAll('[data-ui-palette-select], [data-ui-preference-select]')];
    // All controls are SSR nodes. They share this owner and the existing CAS
    // queue; a Profile section must never mount a second preferences owner.
    const inputs = [...scope.querySelectorAll('[data-ui-preference-input]')].filter(node =>
        (node.dataset.uiPreferenceInput === 'appearance' && node.type === 'radio' && ['auto', 'light', 'dark'].includes(node.value)) ||
        (node.dataset.uiPreferenceInput === 'glass' && node.type === 'checkbox') ||
        (node.dataset.uiPreferenceInput === 'backdrop_color' && node.type === 'color'));
    const choices = [...scope.querySelectorAll('[data-ui-preference-choice]')].filter(node =>
        node.tagName === 'BUTTON' && node.type === 'button' && node.dataset.uiPreferenceChoice === 'palette_key' && PALETTE_KEYS.includes(node.dataset.uiPreferenceValue));
    const controls = [...selects, ...inputs, ...choices];
    if (!controls.length) return null;
    scope.dataset.uiPaletteMounted = 'true';
    const context = scope.dataset.uiPaletteContext || '';
    const fieldOf = node => node.dataset.uiPreferenceInput || node.dataset.uiPreferenceChoice || node.dataset.uiPreferenceSelect || 'palette_key';
    const cleanups = [];
    const listen = (node, type, handler) => { node.addEventListener(type, handler); cleanups.push(() => node.removeEventListener(type, handler)); };
    let statusTimer = null;
    let controller;
    let domDisposed = false;
    const statuses = [...scope.querySelectorAll('[data-ui-palette-status]')];
    const summaries = [...scope.querySelectorAll('[data-ui-preferences-summary-status]')];
    const primaryStatus = statuses.find(node => node.hasAttribute('data-ui-preference-primary-status'));
    if (primaryStatus) {
        [...statuses, ...summaries].filter(node => node !== primaryStatus).forEach(node => {
            const live = node.getAttribute('aria-live');
            node.setAttribute('aria-live', 'off');
            cleanups.push(() => { if (live === null) node.removeAttribute('aria-live'); else node.setAttribute('aria-live', live); });
        });
    }
    const backdrop = createBackdropLayer(scope);
    cleanups.push(() => backdrop.dispose());
    function syncControls(values) {
        selects.forEach(node => { node.value = values[fieldOf(node)]; });
        inputs.forEach(node => {
            if (node.type === 'color') node.value = values.backdrop_color;
            else node.checked = node.type === 'radio' ? node.value === values.appearance : values.glass === 'tinted';
        });
        choices.forEach(node => node.setAttribute('aria-pressed', String(node.dataset.uiPreferenceValue === values.palette_key)));
    }
    function status(kind, message) {
        clearTimeout(statusTimer);
        const failed = ['error', 'conflict', 'identity_changed'].includes(kind);
        const unresolved = failed || kind === 'preview' || kind === 'saving';
        summaries.forEach(node => {
            node.hidden = !unresolved;
            node.textContent = kind === 'identity_changed' ? '账号已变化' : kind === 'saving' ? '保存中' : '未保存';
        });
        controls.forEach(node => {
            node.setAttribute('aria-busy', String(kind === 'saving'));
            node.setAttribute('aria-invalid', String(failed && (kind !== 'conflict' || fieldOf(node) in (controller?.snapshot().conflicts || {}))));
            if (kind === 'identity_changed') node.disabled = true;
        });
        statuses.forEach(node => {
            node.textContent = message;
            node.dataset.uiPreferenceStatus = kind;
            node.classList.toggle('sr-only', !failed && node !== primaryStatus);
            node.classList.toggle('is-visible', failed || node === primaryStatus);
        });
        if (kind === 'saved') statusTimer = setTimeout(() => {
            statuses.filter(node => node !== primaryStatus).forEach(node => { node.classList.add('sr-only'); node.classList.remove('is-visible'); });
        }, 1800);
    }
    async function request(method, payload) {
        const response = await fetch('/api/profile/ui-preferences', {
            method, credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json', ...(payload ? { 'Content-Type': 'application/json', 'X-UI-Preferences-Context': context } : {}) },
            ...(payload ? { body: JSON.stringify(payload) } : {}),
        });
        const data = await response.json();
        if (!response.ok) throw Object.assign(new Error(data.message || data.detail?.message || '界面偏好同步失败'), { status: response.status, code: data.code || data.detail?.code });
        return data.preferences;
    }
    controller = createUIPreferencesController({
        initial: {
            palette_key: scope.dataset.uiPalette,
            appearance: scope.dataset.appearancePreference,
            glass: scope.dataset.glassPreference,
            backdrop: backdrop.layer?.dataset.lqBackdropMode,
            backdrop_color: backdrop.layer?.dataset.lqBackdropColor,
            version: Number(scope.dataset.uiPaletteVersion || 0),
            context_token: context,
            available: scope.dataset.uiPaletteAvailable !== 'false',
        }, request,
        onPreview(values) {
            const paletteChanged = scope.dataset.uiPalette !== values.palette_key;
            scope.dataset.uiPalette = values.palette_key;
            scope.dataset.appearancePreference = values.appearance;
            scope.dataset.glassPreference = values.glass;
            syncControls(values);
            backdrop.apply(values);
            // The theme runtime owns only these three; backdrop is our layer.
            theme?.refresh({ palette_key: values.palette_key, appearance: values.appearance, glass: values.glass });
            if (paletteChanged) scope.dispatchEvent(new documentRoot.defaultView.CustomEvent('lanshare:ui-palette-change', { detail: { palette_key: values.palette_key }, bubbles: true }));
        },
        onConfirmed(preferences) { scope.dataset.uiPaletteVersion = String(preferences.version); scope.dataset.uiPaletteAvailable = 'true'; },
        onStatus: status,
    });
    syncControls(controller.snapshot().desired);
    [...selects, ...inputs].forEach(node => {
        listen(node, 'change', () => {
            if (node.disabled || (node.type === 'radio' && !node.checked)) return;
            const value = node.dataset.uiPreferenceInput === 'glass' ? (node.checked ? 'tinted' : 'off') : String(node.value).toLowerCase();
            controller.select(fieldOf(node), value);
        });
        listen(node, 'keydown', event => {
            if (event.key === 'Enter' && !node.disabled && controller.snapshot().needsRetry) {
                event.preventDefault();
                controller.retry(fieldOf(node));
            }
        });
    });
    choices.forEach(node => listen(node, 'click', () => { if (!node.disabled) controller.select(fieldOf(node), node.dataset.uiPreferenceValue); }));
    const details = [...scope.querySelectorAll('[data-ui-preferences-details]')];
    details.forEach(panel => listen(panel, 'keydown', event => {
        if (event.key !== 'Escape' || !panel.open) return;
        event.preventDefault(); event.stopPropagation();
        panel.open = false;
        panel.querySelector('[data-ui-preferences-toggle]')?.focus({ preventScroll: true });
    }));
    if (details.length) listen(documentRoot, 'click', event => {
        details.forEach(panel => {
            if (!panel.open || panel.contains(event.target)) return;
            panel.open = false;
            if (panel.contains(documentRoot.activeElement)) panel.querySelector('[data-ui-preferences-toggle]')?.focus({ preventScroll: true });
        });
    });
    const ready = () => { if (!domDisposed) scope.classList.add('ui-palette-ready'); };
    const frame = documentRoot.defaultView.requestAnimationFrame?.(ready);
    if (frame === undefined) ready();
    return { ...controller, dispose() {
        if (domDisposed) return;
        domDisposed = true;
        controller.dispose(); clearTimeout(statusTimer);
        if (frame !== undefined) documentRoot.defaultView.cancelAnimationFrame?.(frame);
        cleanups.forEach(fn => fn());
        delete scope.dataset.uiPaletteMounted;
    } };
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initUserUIPreferences(), { once: true });
    else initUserUIPreferences();
}
