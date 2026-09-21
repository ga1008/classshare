(function (win, doc) {
    'use strict';
    var palettes = ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'];
    function normalizePreferences(value) {
        value = value || {};
        return {
            palette_key: palettes.indexOf(value.palette_key) >= 0 ? value.palette_key : 'indigo',
            appearance: ['auto', 'light', 'dark'].indexOf(value.appearance) >= 0 ? value.appearance : 'auto',
            glass: value.glass === 'off' ? 'off' : 'tinted'
        };
    }
    function readPreferences(root) {
        return normalizePreferences({
            palette_key: root.getAttribute('data-ui-palette'),
            appearance: root.getAttribute('data-appearance-preference'),
            glass: root.getAttribute('data-glass-preference')
        });
    }
    function detectCapabilities(environment) {
        var w = environment || win;
        function media(query) {
            try { return !!(w.matchMedia && w.matchMedia(query).matches); } catch (_) { return false; }
        }
        function supports(property, value) {
            try { return !!(w.CSS && w.CSS.supports && w.CSS.supports(property, value)); } catch (_) { return false; }
        }
        var nav = w.navigator || {};
        var connection = nav.connection || {};
        var element = w.HTMLElement && w.HTMLElement.prototype || {};
        var chrome = /(?:Chrome|Chromium)\/(\d+)/.exec(nav.userAgent || '');
        var oldX5 = /(?:TBS\/|MQQBrowser\/)/.test(nav.userAgent || '') && (!chrome || Number(chrome[1]) < 96);
        var backdrop = supports('backdrop-filter', 'blur(1px)') || supports('-webkit-backdrop-filter', 'blur(1px)');
        var features = {
            backdrop: backdrop,
            linear: supports('animation-timing-function', 'linear(0, 1)'),
            popover: 'popover' in element,
            dialog: typeof w.HTMLDialogElement !== 'undefined',
            scrollbarGutter: supports('scrollbar-gutter', 'stable'),
            viewTransition: !!(w.document && w.document.startViewTransition),
            dynamicViewport: supports('height', '100dvh'),
            inert: 'inert' in element
        };
        return {
            dark: media('(prefers-color-scheme: dark)'),
            reducedTransparency: media('(prefers-reduced-transparency: reduce)'),
            reducedMotion: media('(prefers-reduced-motion: reduce)'),
            forcedColors: media('(forced-colors: active)'),
            contrast: media('(prefers-contrast: more)'),
            lowEnd: media('(pointer: coarse)') && ((nav.deviceMemory > 0 && nav.deviceMemory <= 4) || (nav.hardwareConcurrency > 0 && nav.hardwareConcurrency <= 4)),
            saveData: !!connection.saveData || /^(slow-)?2g$/.test(connection.effectiveType || ''),
            tier: !backdrop || oldX5 ? 'C' : Object.keys(features).every(function (key) { return features[key]; }) ? 'A' : 'B',
            features: features
        };
    }
    function resolveTheme(input) {
        var preferences = normalizePreferences(input.preferences);
        var capabilities = input.capabilities || {};
        var tier = ['A', 'B', 'C'].indexOf(capabilities.tier) >= 0 ? capabilities.tier : 'C';
        return {
            preferences: preferences,
            appearance: preferences.appearance === 'auto' ? (capabilities.dark ? 'dark' : 'light') : preferences.appearance,
            glass: preferences.glass === 'off' || tier === 'C' || capabilities.reducedTransparency || capabilities.forcedColors ? 'off' : 'tinted',
            tier: tier,
            lowEnd: !!capabilities.lowEnd,
            saveData: !!capabilities.saveData,
            reducedMotion: !!capabilities.reducedMotion,
            forcedColors: !!capabilities.forcedColors,
            contrast: capabilities.contrast ? 'more' : 'normal'
        };
    }
    function applyTheme(input, root) {
        root = root || doc.documentElement;
        var resolved = resolveTheme(input);
        var attrs = {
            'data-ui-palette': resolved.preferences.palette_key,
            'data-appearance-preference': resolved.preferences.appearance,
            'data-appearance': resolved.appearance,
            'data-glass-preference': resolved.preferences.glass,
            'data-lq-glass': resolved.glass,
            'data-lq-tier': resolved.tier,
            'data-lq-low-end': String(resolved.lowEnd),
            'data-lq-save-data': String(resolved.saveData),
            'data-lq-reduced-motion': String(resolved.reducedMotion),
            'data-lq-forced-colors': String(resolved.forcedColors),
            'data-lq-contrast': resolved.contrast
        };
        Object.keys(attrs).forEach(function (name) { root.setAttribute(name, attrs[name]); });
        root.style.colorScheme = resolved.appearance;
        return resolved;
    }
    win.LanShareTheme = Object.freeze({
        normalizePreferences: normalizePreferences, readPreferences: readPreferences,
        detectCapabilities: detectCapabilities, resolveTheme: resolveTheme, applyTheme: applyTheme
    });
    applyTheme({ preferences: readPreferences(doc.documentElement), capabilities: detectCapabilities(win) }, doc.documentElement);
})(window, document);
