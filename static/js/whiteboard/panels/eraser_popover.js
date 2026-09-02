/**
 * 橡皮浮窗：模式（像素擦/整笔擦）、大小、边缘硬度、实时预览。
 */
import { LIMITS } from '../constants.js';
import { createPopover } from '../popover.js';
import { h, rangeRow, sectionTitle, segmented } from './controls.js';

export function createEraserPopover(board, anchor) {
    const ring = h('span', { className: 'twb-preview-ring' });
    const preview = h('div', { className: 'twb-preview twb-preview--eraser' }, [ring]);
    const paint = () => {
        const size = Math.min(board.settings.eraserSize, 96);
        ring.style.width = `${size}px`;
        ring.style.height = `${size}px`;
        const softness = board.settings.eraserMode === 'pixel' ? (1 - board.settings.eraserHardness) : 0;
        ring.style.filter = softness > 0 ? `blur(${(softness * 3).toFixed(1)}px)` : 'none';
    };
    let hardness;
    const mode = segmented({
        label: '橡皮模式',
        value: board.settings.eraserMode,
        options: [
            { value: 'pixel', label: '像素擦' },
            { value: 'stroke', label: '整笔擦' },
        ],
        onChange: (value) => {
            board.updateSettings({ eraserMode: value });
            hardness.el.hidden = value !== 'pixel';
            paint();
        },
    });
    const size = rangeRow({
        label: '大小',
        min: LIMITS.eraserSize[0],
        max: LIMITS.eraserSize[1],
        value: board.settings.eraserSize,
        format: (v) => `${Math.round(v)}px`,
        onInput: (v) => { board.updateSettings({ eraserSize: v }); paint(); },
    });
    hardness = rangeRow({
        label: '硬度',
        min: 0,
        max: 100,
        step: 5,
        value: Math.round(board.settings.eraserHardness * 100),
        format: (v) => `${Math.round(v)}%`,
        onInput: (v) => { board.updateSettings({ eraserHardness: v / 100 }); paint(); },
    });
    hardness.el.hidden = board.settings.eraserMode !== 'pixel';
    const panel = h('div', { className: 'twb-panel-body' }, [
        sectionTitle('橡皮', '像素擦只擦之前的笔迹；整笔擦整条删除'),
        mode.el,
        size.el,
        hardness.el,
        preview,
    ]);
    const popover = createPopover({ anchor, panel, kind: 'popover', label: '橡皮设置' });
    const refresh = () => {
        mode.set(board.settings.eraserMode);
        size.set(board.settings.eraserSize);
        hardness.set(Math.round(board.settings.eraserHardness * 100));
        hardness.el.hidden = board.settings.eraserMode !== 'pixel';
        paint();
    };
    refresh();
    return { popover, refresh };
}
