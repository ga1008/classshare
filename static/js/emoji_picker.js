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

const MAX_FREQUENT_ITEMS = 8;

/**
 * Create an emoji picker instance.
 *
 * @param {{ targetInput: HTMLTextAreaElement|HTMLInputElement }} options
 * @returns {{ element: HTMLElement, open: Function, close: Function, toggle: Function, isOpen: Function }}
 */
export function createEmojiPicker(options) {
    const targetInput = options?.targetInput;
    if (!targetInput) {
        throw new Error('emoji_picker: targetInput is required');
    }

    let isOpen = false;
    const container = document.createElement('div');
    container.className = 'emoji-picker';
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

    function open() {
        isOpen = true;
        container.hidden = false;
        requestAnimationFrame(() => container.classList.add('is-open'));
    }

    function close() {
        isOpen = false;
        container.classList.remove('is-open');
        setTimeout(() => {
            if (!container.classList.contains('is-open')) {
                container.hidden = true;
            }
        }, 160);
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
