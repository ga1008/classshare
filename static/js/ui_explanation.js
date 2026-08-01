const TRIGGER_SELECTOR = '[data-explain], [data-lp-tip]';
const DEFAULT_DELAY_MS = 2000;
const DEFAULT_LONG_PRESS_MS = 650;
const VIEWPORT_MARGIN = 12;
const PANEL_GAP = 10;

const registry = new Map();
const attachedConfigs = new WeakMap();

const state = {
    trigger: null,
    panel: null,
    openTimer: 0,
    closeTimer: 0,
    longPressTimer: 0,
    touch: null,
    suppressClickUntil: 0,
    previousDescribedBy: null,
    repositionFrame: 0,
    config: null,
};

function asElement(target) {
    if (target instanceof Element) return target;
    if (typeof target === 'string') return document.querySelector(target);
    return null;
}

function findTrigger(target) {
    return target instanceof Element ? target.closest(TRIGGER_SELECTOR) : null;
}

function boundedNumber(value, fallback, minimum, maximum) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, parsed));
}

function safeUrl(value) {
    if (!value) return '';
    try {
        const url = new URL(String(value), window.location.href);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_error) {
        return '';
    }
}

function normalizeLinks(value) {
    let raw = value;
    if (typeof raw === 'string') {
        try {
            raw = JSON.parse(raw);
        } catch (_error) {
            return [];
        }
    }
    if (!Array.isArray(raw)) return [];
    return raw.slice(0, 4).map((item) => {
        if (!item || typeof item !== 'object') return null;
        const href = safeUrl(item.href);
        const label = String(item.label || '').trim().slice(0, 32);
        return href && label ? { href, label } : null;
    }).filter(Boolean);
}

function normalizeConfig(config = {}) {
    const placement = ['auto', 'top', 'bottom', 'left', 'right'].includes(config.placement)
        ? config.placement
        : 'auto';
    return {
        title: String(config.title || '').trim().slice(0, 80),
        text: String(config.text || config.description || '').trim().slice(0, 800),
        links: normalizeLinks(config.links),
        media: safeUrl(config.media),
        mediaAlt: String(config.mediaAlt || '').trim().slice(0, 120),
        placement,
        delay: boundedNumber(config.delay, DEFAULT_DELAY_MS, 250, 5000),
        longPress: boundedNumber(config.longPress, DEFAULT_LONG_PRESS_MS, 450, 1500),
    };
}

function resolveConfig(trigger, override = null) {
    const registered = trigger.dataset.explainId
        ? registry.get(trigger.dataset.explainId)
        : null;
    const attached = attachedConfigs.get(trigger);
    const inline = {
        title: trigger.dataset.explainTitle,
        text: trigger.dataset.explainText ?? trigger.dataset.lpTip,
        links: trigger.dataset.explainLinks,
        media: trigger.dataset.explainMedia,
        mediaAlt: trigger.dataset.explainMediaAlt,
        placement: trigger.dataset.explainPlacement,
        delay: trigger.dataset.explainDelay,
        longPress: trigger.dataset.explainLongPress,
    };
    return normalizeConfig({
        ...(registered || {}),
        ...(attached || {}),
        ...Object.fromEntries(Object.entries(inline).filter(([, value]) => value !== undefined)),
        ...(override || {}),
    });
}

function hasContent(config) {
    return Boolean(config.title || config.text || config.links.length || config.media);
}

function createPanel() {
    if (state.panel) return state.panel;
    const panel = document.createElement('aside');
    panel.id = 'ui-explanation-popover';
    panel.className = 'ui-explain-popover';
    panel.hidden = true;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'false');
    panel.setAttribute('aria-labelledby', 'ui-explanation-title');
    panel.setAttribute('aria-describedby', 'ui-explanation-text');
    panel.innerHTML = `
        <div class="ui-explain-popover__head">
            <strong id="ui-explanation-title"></strong>
            <button type="button" class="ui-explain-popover__close" aria-label="关闭说明">×</button>
        </div>
        <p id="ui-explanation-text" class="ui-explain-popover__text"></p>
        <img class="ui-explain-popover__media" alt="" loading="lazy" decoding="async" hidden>
        <nav class="ui-explain-popover__links" aria-label="快速跳转" hidden></nav>
    `;
    document.body.appendChild(panel);
    panel.querySelector('.ui-explain-popover__close')?.addEventListener('click', () => {
        const trigger = state.trigger;
        closeExplanation();
        trigger?.focus?.({ preventScroll: true });
    });
    panel.addEventListener('pointerenter', cancelClose);
    panel.addEventListener('pointerleave', () => scheduleClose(160));
    panel.addEventListener('focusin', cancelClose);
    panel.addEventListener('focusout', (event) => {
        if (!panel.contains(event.relatedTarget) && !state.trigger?.contains(event.relatedTarget)) {
            scheduleClose(100);
        }
    });
    state.panel = panel;
    return panel;
}

function renderPanel(config) {
    const panel = createPanel();
    const title = panel.querySelector('#ui-explanation-title');
    const text = panel.querySelector('#ui-explanation-text');
    const media = panel.querySelector('.ui-explain-popover__media');
    const links = panel.querySelector('.ui-explain-popover__links');

    title.textContent = config.title || '功能说明';
    text.textContent = config.text;
    text.hidden = !config.text;

    if (config.media) {
        media.src = config.media;
        media.alt = config.mediaAlt || `${config.title || '功能'}演示`;
        media.hidden = false;
    } else {
        media.hidden = true;
        media.removeAttribute('src');
        media.alt = '';
    }

    links.replaceChildren();
    config.links.forEach((item) => {
        const link = document.createElement('a');
        link.href = item.href;
        link.textContent = item.label;
        if (new URL(item.href).origin !== window.location.origin) {
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
        }
        links.appendChild(link);
    });
    links.hidden = !config.links.length;
}

function preferredSides(preferred) {
    const fallback = ['bottom', 'top', 'right', 'left'];
    return preferred === 'auto'
        ? fallback
        : [preferred, ...fallback.filter((side) => side !== preferred)];
}

function chooseSide(triggerRect, panelRect, preferred) {
    const spaces = {
        top: triggerRect.top - VIEWPORT_MARGIN,
        bottom: window.innerHeight - triggerRect.bottom - VIEWPORT_MARGIN,
        left: triggerRect.left - VIEWPORT_MARGIN,
        right: window.innerWidth - triggerRect.right - VIEWPORT_MARGIN,
    };
    const required = {
        top: panelRect.height + PANEL_GAP,
        bottom: panelRect.height + PANEL_GAP,
        left: panelRect.width + PANEL_GAP,
        right: panelRect.width + PANEL_GAP,
    };
    const sides = preferredSides(preferred);
    return sides.find((side) => spaces[side] >= required[side])
        || sides.sort((a, b) => spaces[b] - spaces[a])[0];
}

function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
}

function positionPanel() {
    const { panel, trigger } = state;
    if (!panel || panel.hidden || !trigger?.isConnected) return;
    const triggerRect = trigger.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const config = state.config || resolveConfig(trigger);
    const side = chooseSide(triggerRect, panelRect, config.placement);
    let left;
    let top;

    if (side === 'top' || side === 'bottom') {
        left = triggerRect.left + (triggerRect.width - panelRect.width) / 2;
        top = side === 'top'
            ? triggerRect.top - panelRect.height - PANEL_GAP
            : triggerRect.bottom + PANEL_GAP;
    } else {
        left = side === 'left'
            ? triggerRect.left - panelRect.width - PANEL_GAP
            : triggerRect.right + PANEL_GAP;
        top = triggerRect.top + (triggerRect.height - panelRect.height) / 2;
    }

    panel.style.left = `${Math.round(clamp(left, VIEWPORT_MARGIN, window.innerWidth - panelRect.width - VIEWPORT_MARGIN))}px`;
    panel.style.top = `${Math.round(clamp(top, VIEWPORT_MARGIN, window.innerHeight - panelRect.height - VIEWPORT_MARGIN))}px`;
    panel.dataset.side = side;
}

function scheduleReposition() {
    if (state.repositionFrame) return;
    state.repositionFrame = window.requestAnimationFrame(() => {
        state.repositionFrame = 0;
        positionPanel();
    });
}

function bindOpenViewportListeners() {
    window.addEventListener('resize', scheduleReposition, { passive: true });
    window.addEventListener('scroll', scheduleReposition, { passive: true, capture: true });
}

function unbindOpenViewportListeners() {
    window.removeEventListener('resize', scheduleReposition);
    window.removeEventListener('scroll', scheduleReposition, true);
}

function cancelOpen() {
    if (state.openTimer) window.clearTimeout(state.openTimer);
    state.openTimer = 0;
}

function cancelClose() {
    if (state.closeTimer) window.clearTimeout(state.closeTimer);
    state.closeTimer = 0;
}

function scheduleClose(delay = 120) {
    cancelClose();
    state.closeTimer = window.setTimeout(closeExplanation, delay);
}

function scheduleOpen(trigger, delay) {
    cancelOpen();
    cancelClose();
    if (state.trigger === trigger && state.panel && !state.panel.hidden) return;
    state.openTimer = window.setTimeout(() => openExplanation(trigger), delay);
}

export function openExplanation(target, override = null) {
    const trigger = asElement(target);
    if (!trigger) return false;
    const config = resolveConfig(trigger, override);
    if (!hasContent(config)) return false;

    cancelOpen();
    cancelClose();
    if (state.trigger && state.trigger !== trigger) closeExplanation();
    state.trigger = trigger;
    state.config = config;
    renderPanel(config);
    const panel = createPanel();
    state.previousDescribedBy = trigger.getAttribute('aria-describedby');
    trigger.setAttribute('aria-describedby', panel.id);
    trigger.setAttribute('aria-expanded', 'true');
    panel.hidden = false;
    panel.classList.remove('is-visible');
    bindOpenViewportListeners();
    window.requestAnimationFrame(() => {
        positionPanel();
        panel.classList.add('is-visible');
    });
    return true;
}

export function closeExplanation() {
    cancelOpen();
    cancelClose();
    const { trigger, panel } = state;
    if (trigger) {
        if (state.previousDescribedBy) trigger.setAttribute('aria-describedby', state.previousDescribedBy);
        else trigger.removeAttribute('aria-describedby');
        trigger.removeAttribute('aria-expanded');
    }
    if (panel) {
        panel.classList.remove('is-visible');
        panel.hidden = true;
    }
    unbindOpenViewportListeners();
    state.trigger = null;
    state.previousDescribedBy = null;
    state.config = null;
}

export function registerExplanations(entries = {}) {
    Object.entries(entries).forEach(([key, config]) => {
        if (key) registry.set(String(key), normalizeConfig(config));
    });
}

export function attachExplanation(target, config) {
    const elements = typeof target === 'string'
        ? Array.from(document.querySelectorAll(target))
        : [asElement(target)].filter(Boolean);
    elements.forEach((element) => {
        element.setAttribute('data-explain', '');
        attachedConfigs.set(element, normalizeConfig(config));
    });
    return elements;
}

document.addEventListener('pointerover', (event) => {
    if (event.pointerType && !['mouse', 'pen'].includes(event.pointerType)) return;
    const trigger = findTrigger(event.target);
    if (!trigger || trigger.contains(event.relatedTarget)) return;
    scheduleOpen(trigger, resolveConfig(trigger).delay);
});

document.addEventListener('pointerout', (event) => {
    if (event.pointerType && !['mouse', 'pen'].includes(event.pointerType)) return;
    const trigger = findTrigger(event.target);
    if (!trigger || trigger.contains(event.relatedTarget)) return;
    if (state.panel?.contains(event.relatedTarget)) return;
    cancelOpen();
    if (state.trigger === trigger) scheduleClose(140);
});

document.addEventListener('focusin', (event) => {
    const trigger = findTrigger(event.target);
    if (trigger) scheduleOpen(trigger, 300);
});

document.addEventListener('focusout', (event) => {
    const trigger = findTrigger(event.target);
    if (!trigger || trigger.contains(event.relatedTarget) || state.panel?.contains(event.relatedTarget)) return;
    cancelOpen();
    if (state.trigger === trigger) scheduleClose(100);
});

document.addEventListener('pointerdown', (event) => {
    const trigger = findTrigger(event.target);
    if (state.trigger && !trigger && !state.panel?.contains(event.target)) closeExplanation();
    if (event.pointerType !== 'touch' || !trigger) return;
    if (state.longPressTimer) window.clearTimeout(state.longPressTimer);
    state.touch = {
        trigger,
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        opened: false,
    };
    state.longPressTimer = window.setTimeout(() => {
        if (!state.touch || state.touch.pointerId !== event.pointerId) return;
        state.touch.opened = openExplanation(trigger);
        if (state.touch.opened) state.suppressClickUntil = Date.now() + 900;
    }, resolveConfig(trigger).longPress);
});

document.addEventListener('pointermove', (event) => {
    if (!state.touch || state.touch.pointerId !== event.pointerId) return;
    if (Math.hypot(event.clientX - state.touch.x, event.clientY - state.touch.y) > 10) {
        window.clearTimeout(state.longPressTimer);
        state.longPressTimer = 0;
        state.touch = null;
    }
}, { passive: true });

function endTouch(event) {
    if (!state.touch || state.touch.pointerId !== event.pointerId) return;
    window.clearTimeout(state.longPressTimer);
    state.longPressTimer = 0;
    state.touch = null;
}

document.addEventListener('pointerup', endTouch);
document.addEventListener('pointercancel', endTouch);

document.addEventListener('contextmenu', (event) => {
    if (findTrigger(event.target) && window.matchMedia('(pointer: coarse)').matches) {
        event.preventDefault();
    }
});

document.addEventListener('click', (event) => {
    if (Date.now() < state.suppressClickUntil && findTrigger(event.target)) {
        event.preventDefault();
        event.stopPropagation();
        return;
    }
    const toggle = event.target instanceof Element
        ? event.target.closest('[data-explain-toggle]')
        : null;
    if (!toggle) return;
    event.preventDefault();
    if (state.trigger === toggle && state.panel && !state.panel.hidden) closeExplanation();
    else openExplanation(toggle);
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state.trigger) closeExplanation();
});

window.LanShareExplanation = Object.freeze({
    attach: attachExplanation,
    close: closeExplanation,
    open: openExplanation,
    register: registerExplanations,
});

window.dispatchEvent(new CustomEvent('lanshare:explanation-ready'));
