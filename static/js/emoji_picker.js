/**
 * Lightweight reusable emoji picker.
 * Reuses emoji catalog data from chat_emoji_catalog.js.
 *
 * Usage:
 *   import { createEmojiPicker } from './emoji_picker.js';
 *   const picker = createEmojiPicker({ targetInput: myTextarea });
 *   document.body.appendChild(picker.element);
 *   picker.open();
 */
import {
    DEFAULT_FREQUENT_EMOJIS,
    EMOJI_CATEGORIES,
    getEmojiMeta,
    buildTwemojiUrl,
} from './chat_emoji_catalog.js';
import { adoptDomainControl } from './lq/domain-controls.js';
import { setOverlayOpen } from './ui_overlay_motion.js';

const MAX_FREQUENT_ITEMS = 8;

/**
 * Create an emoji picker instance.
 *
 * The picker is mounted by the caller inside an anchor element; the anchor is made
 * the positioning box by emoji-picker.css (`:has(> .emoji-picker)`). On every open
 * the picker measures the room around the anchor inside its nearest clipping
 * ancestor (a scrolling dialog body, an overflow-clipped panel, or the viewport),
 * opens on the side with more room, caps its height to that room and aligns to
 * the trigger's side, so it never drifts or gets cut off.
 *
 * @param {{ targetInput: HTMLTextAreaElement|HTMLInputElement, trigger?: HTMLElement }} options
 * @returns {{ element: HTMLElement, open: Function, close: Function, toggle: Function, isOpen: Function }}
 */
export function createEmojiPicker(options) {
    const targetInput = options?.targetInput;
    const trigger = options?.trigger || null;
    if (!targetInput) {
        throw new Error('emoji_picker: targetInput is required');
    }

    let isOpen = false;
    const container = document.createElement('div');
    container.className = 'emoji-picker lq-glass lq-domain-raised';
    container.dataset.lqComponent = 'popover'; container.dataset.lqMaterial = 'raised';
    container.dataset.lqPresence = 'panel';
    container.hidden = true;
    container.setAttribute('role', 'dialog');
    container.setAttribute('aria-label', '表情选择器');

    buildPickerContent();

    function buildPickerContent() {
        container.innerHTML = '';

        const frequentSection = document.createElement('div');
        frequentSection.className = 'emoji-picker-section';
        const frequentHeader = document.createElement('div');
        frequentHeader.className = 'emoji-picker-header';
        frequentHeader.textContent = '常用';
        frequentSection.appendChild(frequentHeader);

        const frequentGrid = document.createElement('div');
        frequentGrid.className = 'emoji-picker-grid';
        const frequentItems = DEFAULT_FREQUENT_EMOJIS.slice(0, MAX_FREQUENT_ITEMS);
        if (frequentItems.length) {
            frequentItems.forEach((char) => {
                frequentGrid.appendChild(createEmojiButton(char));
            });
        } else {
            const empty = document.createElement('div');
            empty.className = 'emoji-picker-empty';
            empty.textContent = '发送表情后这里会显示常用表情';
            frequentGrid.appendChild(empty);
        }
        frequentSection.appendChild(frequentGrid);
        container.appendChild(frequentSection);

        EMOJI_CATEGORIES.forEach((category) => {
            const section = document.createElement('div');
            section.className = 'emoji-picker-section';

            const header = document.createElement('div');
            header.className = 'emoji-picker-header';
            header.textContent = category.label;
            section.appendChild(header);

            const grid = document.createElement('div');
            grid.className = 'emoji-picker-grid';
            category.emojis.forEach((emoji) => {
                grid.appendChild(createEmojiButton(emoji.char));
            });
            section.appendChild(grid);
            container.appendChild(section);
        });
    }

    function createEmojiButton(char) {
        const meta = getEmojiMeta(char) || {};
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'emoji-picker-item';
        adoptDomainControl(button, { kind: 'choice', variant: 'ghost' });
        button.dataset.lqShape = 'surface';
        button.title = meta.name || char;
        button.setAttribute('aria-label', meta.name || char || 'emoji');

        const code = meta.code;
        if (code) {
            const img = document.createElement('img');
            img.src = buildTwemojiUrl(code);
            img.alt = char;
            img.loading = 'lazy';
            img.decoding = 'async';
            img.onerror = () => {
                const fallback = document.createElement('span');
                fallback.textContent = char;
                img.replaceWith(fallback);
            };
            button.appendChild(img);
        } else {
            button.textContent = char;
        }

        button.addEventListener('click', () => {
            insertAtCursor(char);
        });
        return button;
    }

    function insertAtCursor(char) {
        const start = targetInput.selectionStart ?? targetInput.value.length;
        const end = targetInput.selectionEnd ?? targetInput.value.length;
        const value = targetInput.value;
        targetInput.value = `${value.slice(0, start)}${char}${value.slice(end)}`;
        const nextPos = start + char.length;
        targetInput.focus();
        targetInput.setSelectionRange(nextPos, nextPos);
    }

    const IDEAL_HEIGHT = 352;
    const MIN_HEIGHT = 160;
    const GAP = 8;

    function clippingBox(node) {
        const box = { top: 0, left: 0, right: window.innerWidth, bottom: window.innerHeight };
        for (let el = node.parentElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
            const style = getComputedStyle(el);
            if (/(auto|scroll|hidden|clip)/.test(`${style.overflowX} ${style.overflowY}`)) {
                const rect = el.getBoundingClientRect();
                box.top = Math.max(box.top, rect.top); box.bottom = Math.min(box.bottom, rect.bottom);
                box.left = Math.max(box.left, rect.left); box.right = Math.min(box.right, rect.right);
            }
        }
        return box;
    }

    function place() {
        const anchor = container.parentElement;
        if (!anchor) return;
        // Coordinates are computed against the trigger (or the anchor when no
        // trigger is given) and written as offsets inside the anchor, which
        // emoji-picker.css makes the positioning box. A caller whose anchor is
        // not next to its trigger (feedback dialog) still opens beside the button.
        const anchorRect = anchor.getBoundingClientRect();
        const reference = (trigger && trigger.isConnected ? trigger : anchor).getBoundingClientRect();
        const box = clippingBox(anchor);
        const above = reference.top - box.top - GAP;
        const below = box.bottom - reference.bottom - GAP;
        const placement = above >= Math.min(IDEAL_HEIGHT, 260) || above >= below ? 'top' : 'bottom';
        const room = Math.floor(placement === 'top' ? above : below);
        const width = Math.min(384, Math.max(220, Math.floor(box.right - box.left - 16)));
        const left = Math.min(Math.max(reference.left, box.left + 8), box.right - 8 - width) - anchorRect.left;
        container.dataset.placement = placement;
        container.style.setProperty('--emoji-picker-max-height', `${Math.max(MIN_HEIGHT, Math.min(IDEAL_HEIGHT, room))}px`);
        container.style.setProperty('--emoji-picker-max-width', `${width}px`);
        container.style.insetInlineStart = `${Math.round(left)}px`;
        container.style.insetInlineEnd = 'auto';
        if (placement === 'top') {
            container.style.top = 'auto';
            container.style.bottom = `${Math.round(anchorRect.bottom - reference.top + GAP)}px`;
        } else {
            container.style.bottom = 'auto';
            container.style.top = `${Math.round(reference.bottom - anchorRect.top + GAP)}px`;
        }
    }

    function open() {
        isOpen = true;
        place();
        void setOverlayOpen(container, true);
        container.classList.add('is-open');
    }

    function close() {
        isOpen = false;
        container.classList.remove('is-open');
        return setOverlayOpen(container, false);
    }

    function toggle() {
        if (isOpen) {
            close();
        } else {
            open();
        }
    }

    function getIsOpen() {
        return isOpen && !container.hidden;
    }

    return {
        element: container,
        open,
        close,
        toggle,
        isOpen: getIsOpen,
    };
}
