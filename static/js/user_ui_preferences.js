/** Account-scoped palette. SSR is authoritative; old browser caches are never
 * read. Writes are serialized and coalesced, never cancelled on the assumption
 * that an already delivered HTTP mutation was undone. */
export const PALETTE_KEYS = Object.freeze(['indigo', 'sky', 'mint', 'violet', 'rose']);
export const normalizePalette = value => PALETTE_KEYS.includes(value) ? value : 'indigo';

export function createPaletteController({ initial, request, onPreview, onStatus, debounceMs = 240 }) {
    const context = String(initial.context_token || '');
    let confirmed = { ...initial, palette_key: normalizePalette(initial.palette_key) };
    let desired = confirmed.palette_key;
    let intent = 0;
    let dirty = false;
    let busy = false;
    let needsRecovery = initial.available === false;
    let needsRetry = false;
    let identityChanged = false;
    let timer = null;
    let disposed = false;

    function accept(preferences) {
        if (!preferences || preferences.context_token !== context) {
            identityChanged = true;
            throw new Error('identity_changed');
        }
        if (!Number.isInteger(preferences.version) || preferences.version < 0) throw new Error('invalid_version');
        confirmed = { ...preferences, palette_key: normalizePalette(preferences.palette_key) };
    }

    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(() => { timer = null; void flush(); }, debounceMs);
    }

    async function flush() {
        if (disposed || busy || !dirty || needsRetry || identityChanged) return;
        busy = true;
        const startedIntent = intent;
        onStatus('saving', '正在保存配色…');
        try {
            if (needsRecovery) {
                accept(await request('GET'));
                needsRecovery = false;
            }
            // A selection made while refreshing must be the one submitted.
            const sentPalette = desired;
            const result = await request('PATCH', { palette_key: sentPalette, version: confirmed.version });
            accept(result);
            if (disposed) return;
            dirty = desired !== confirmed.palette_key;
            if (!dirty) onStatus('saved', '配色已保存');
        } catch (error) {
            if (disposed) return;
            needsRetry = true;
            needsRecovery = true;
            if (error?.code === 'identity_changed' || identityChanged) {
                identityChanged = true;
                onStatus('identity_changed', '登录账号已变化，请刷新页面后再选择配色。');
            } else if (error?.status === 409) {
                try { accept(await request('GET')); needsRecovery = false; }
                catch (refreshError) {
                    if (identityChanged || refreshError?.code === 'identity_changed') {
                        identityChanged = true;
                        onStatus('identity_changed', '登录账号已变化，请刷新页面后再选择配色。');
                    }
                }
                if (!identityChanged) onStatus('conflict', '配色已在其他页面或设备更新。当前为临时预览，请重新选择以保存。');
            } else {
                onStatus('error', '配色未同步，下次登录可能恢复原配色；请在下拉中重新选择以重试。');
            }
        } finally {
            busy = false;
            if (!disposed && dirty && !needsRetry && !identityChanged) {
                // Coalesce rapid choices; do not let an old response repaint.
                if (intent !== startedIntent || desired !== confirmed.palette_key) schedule();
            }
        }
    }

    function select(value) {
        if (disposed || identityChanged) return;
        desired = normalizePalette(value);
        intent += 1;
        dirty = true;
        needsRetry = false;
        onPreview(desired);
        onStatus('preview', '正在预览配色');
        schedule();
    }

    return {
        select,
        flush,
        retry() { if (needsRetry) select(desired); },
        snapshot: () => ({ desired, confirmed: { ...confirmed }, busy, dirty, needsRetry, identityChanged }),
        dispose() { disposed = true; clearTimeout(timer); },
    };
}

export function initUserUIPreferences(documentRoot = document) {
    const scope = documentRoot.body;
    if (!scope?.hasAttribute('data-ui-palette') || scope.dataset.uiPaletteMounted === 'true') return null;
    scope.dataset.uiPaletteMounted = 'true';
    const selects = [...scope.querySelectorAll('[data-ui-palette-select]')];
    if (!selects.length) return null;
    const context = scope.dataset.uiPaletteContext || '';
    let statusTimer = null;
    let controller;

    function status(kind, message) {
        clearTimeout(statusTimer);
        const failed = ['error', 'conflict', 'identity_changed'].includes(kind);
        selects.forEach(select => {
            select.setAttribute('aria-busy', String(kind === 'saving'));
            select.setAttribute('aria-invalid', String(failed));
            if (kind === 'identity_changed') select.disabled = true;
        });
        scope.querySelectorAll('[data-ui-palette-status]').forEach(node => {
            node.textContent = message;
            node.classList.toggle('sr-only', !failed);
            node.classList.toggle('is-visible', failed);
        });
        if (failed || kind === 'saved') statusTimer = setTimeout(() => {
            scope.querySelectorAll('[data-ui-palette-status]').forEach(node => {
                node.classList.add('sr-only');
                node.classList.remove('is-visible');
            });
        }, failed ? 9000 : 1800);
    }

    async function request(method, payload) {
        const response = await fetch('/api/profile/ui-preferences', {
            method,
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { Accept: 'application/json', ...(payload ? { 'Content-Type': 'application/json', 'X-UI-Preferences-Context': context } : {}) },
            ...(payload ? { body: JSON.stringify(payload) } : {}),
        });
        const data = await response.json();
        if (!response.ok) {
            const error = new Error(data.message || data.detail?.message || '配色同步失败');
            error.status = response.status;
            error.code = data.code || data.detail?.code;
            throw error;
        }
        return data.preferences;
    }

    controller = createPaletteController({
        initial: {
            palette_key: scope.dataset.uiPalette,
            version: Number(scope.dataset.uiPaletteVersion || 0),
            context_token: context,
            available: scope.dataset.uiPaletteAvailable !== 'false',
        },
        request,
        onPreview(key) {
            scope.dataset.uiPalette = key;
            selects.forEach(select => { select.value = key; });
            // The body is the explicit palette context for all existing portals.
            scope.dispatchEvent(new CustomEvent('lanshare:ui-palette-change', { detail: { palette_key: key } }));
        },
        onStatus: status,
    });
    selects.forEach(select => {
        select.value = normalizePalette(scope.dataset.uiPalette);
        select.addEventListener('change', () => controller.select(select.value));
        // Opening the menu is not consent to overwrite a cross-device change.
        // A new choice retries; Enter can explicitly confirm the same choice.
        select.addEventListener('keydown', event => { if (event.key === 'Enter') controller.retry(); });
    });
    requestAnimationFrame(() => scope.classList.add('ui-palette-ready'));
    return controller;
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initUserUIPreferences(), { once: true });
    else initUserUIPreferences();
}
