/**
 * 浮窗内通用控件构造：元素工厂、滑块行、色板行、分段选择。
 */
import { escapeHtml } from '../../ui.js';
import { COLOR_SWATCHES, ICONS } from '../constants.js';

export function h(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'className') el.className = value;
        else if (key === 'html') el.innerHTML = value;
        else if (key === 'text') el.textContent = value;
        else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
        else if (key === 'dataset') Object.assign(el.dataset, value);
        else el.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of [].concat(children)) {
        if (child === null || child === undefined || child === false) continue;
        el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
}

export function sectionTitle(text, hint = '') {
    return h('div', { className: 'twb-section-title' }, [
        h('span', { text }),
        hint ? h('small', { text: hint }) : null,
    ]);
}

/**
 * @returns {{el:HTMLElement, set:(value:number)=>void}}
 */
export function rangeRow({ id, label, min, max, step = 1, value, format = (v) => String(v), onInput }) {
    const output = h('output', { className: 'twb-range-value', text: format(value) });
    const input = h('input', {
        id, type: 'range', min, max, step, value, className: 'twb-range', 'aria-label': label,
        onInput: () => {
            const next = Number(input.value);
            output.textContent = format(next);
            onInput(next);
        },
    });
    const el = h('label', { className: 'twb-row twb-row--range' }, [
        h('span', { className: 'twb-row-label', text: label }),
        input,
        output,
    ]);
    return {
        el,
        set(next) {
            input.value = String(next);
            output.textContent = format(next);
        },
    };
}

export function swatchRow({ value, onPick, label = '颜色' }) {
    const custom = h('input', {
        type: 'color', value, className: 'twb-color-input', 'aria-label': `${label}（自定义）`,
        onInput: () => {
            api.set(custom.value);
            onPick(custom.value);
        },
    });
    const buttons = COLOR_SWATCHES.map((swatch) => h('button', {
        type: 'button',
        className: 'twb-swatch',
        title: swatch.label,
        'aria-label': swatch.label,
        style: `--swatch:${swatch.value}`,
        dataset: { color: swatch.value },
        onClick: () => {
            api.set(swatch.value);
            onPick(swatch.value);
        },
    }));
    const el = h('div', { className: 'twb-row twb-row--swatches', role: 'group', 'aria-label': label }, [
        ...buttons,
        h('span', { className: 'twb-swatch twb-swatch--custom', title: '自定义颜色' }, [custom]),
    ]);
    const api = {
        el,
        set(next) {
            const normalized = String(next || '').toLowerCase();
            buttons.forEach((button) => {
                const active = button.dataset.color.toLowerCase() === normalized;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
            if (/^#[0-9a-f]{6}$/i.test(normalized)) custom.value = normalized;
        },
    };
    api.set(value);
    return api;
}

export function segmented({ options, value, onChange, label }) {
    const buttons = options.map((option) => h('button', {
        type: 'button',
        role: 'radio',
        className: 'twb-segment',
        dataset: { value: option.value },
        text: option.label,
        onClick: () => {
            api.set(option.value);
            onChange(option.value);
        },
    }));
    const el = h('div', { className: 'twb-segmented', role: 'radiogroup', 'aria-label': label }, buttons);
    const api = {
        el,
        set(next) {
            buttons.forEach((button) => {
                const active = button.dataset.value === next;
                button.classList.toggle('is-active', active);
                button.setAttribute('aria-checked', active ? 'true' : 'false');
            });
        },
    };
    api.set(value);
    return api;
}

export function menuItem({ icon, label, hint = '', onClick, disabled = false, danger = false }) {
    return h('button', {
        type: 'button',
        role: 'menuitem',
        className: `twb-menu-item${danger ? ' is-danger' : ''}`,
        disabled,
        onClick,
        html: `<span class="twb-menu-icon">${ICONS[icon] || ''}</span><span class="twb-menu-label">${escapeHtml(label)}</span>${hint ? `<kbd class="twb-menu-hint">${escapeHtml(hint)}</kbd>` : ''}`,
    });
}
