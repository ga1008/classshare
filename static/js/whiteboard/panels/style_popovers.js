/**
 * 画笔 / 文字 / 笔迹 / 背景 四个样式小浮窗。
 * 每个工厂返回 { popover, refresh }，refresh() 用当前 settings 回填控件。
 */
import { LIMITS } from '../constants.js';
import { createPopover } from '../popover.js';
import { h, rangeRow, sectionTitle, swatchRow } from './controls.js';

export function createBrushPopover(board, anchor) {
    const dot = h('span', { className: 'twb-preview-dot' });
    const preview = h('div', { className: 'twb-preview twb-preview--brush' }, [dot]);
    const paint = () => {
        dot.style.background = board.settings.brushColor;
        dot.style.width = `${board.settings.brushSize}px`;
        dot.style.height = `${board.settings.brushSize}px`;
    };
    const swatches = swatchRow({
        value: board.settings.brushColor,
        label: '画笔颜色',
        onPick: (color) => { board.updateSettings({ brushColor: color }); paint(); },
    });
    const size = rangeRow({
        label: '粗细',
        min: LIMITS.brushSize[0],
        max: LIMITS.brushSize[1],
        value: board.settings.brushSize,
        format: (v) => `${Math.round(v)}px`,
        onInput: (v) => { board.updateSettings({ brushSize: v }); paint(); },
    });
    const panel = h('div', { className: 'twb-panel-body' }, [sectionTitle('画笔'), swatches.el, size.el, preview]);
    const popover = createPopover({ anchor, panel, kind: 'popover', label: '画笔设置' });
    const refresh = () => { swatches.set(board.settings.brushColor); size.set(board.settings.brushSize); paint(); };
    refresh();
    return { popover, refresh };
}

export function createTextPopover(board, anchor) {
    const preview = h('div', { className: 'twb-preview twb-preview--text', text: 'Aa 文字' });
    const paint = () => {
        preview.style.color = board.settings.textColor;
        preview.style.fontSize = `${Math.min(board.settings.fontSize, 40)}px`;
    };
    const swatches = swatchRow({
        value: board.settings.textColor,
        label: '文字颜色',
        onPick: (color) => { board.updateSettings({ textColor: color }); paint(); },
    });
    const size = rangeRow({
        label: '字号',
        min: LIMITS.fontSize[0],
        max: LIMITS.fontSize[1],
        value: board.settings.fontSize,
        format: (v) => `${Math.round(v)}px`,
        onInput: (v) => { board.updateSettings({ fontSize: v }); paint(); },
    });
    const panel = h('div', { className: 'twb-panel-body' }, [sectionTitle('文字'), swatches.el, size.el, preview]);
    const popover = createPopover({ anchor, panel, kind: 'popover', label: '文字设置' });
    const refresh = () => { swatches.set(board.settings.textColor); size.set(board.settings.fontSize); paint(); };
    refresh();
    return { popover, refresh };
}

export function createInkPopover(board, anchor) {
    const opacity = rangeRow({
        label: '透明度',
        min: Math.round(LIMITS.boardOpacity[0] * 100),
        max: 100,
        step: 5,
        value: Math.round(board.settings.boardOpacity * 100),
        format: (v) => `${Math.round(v)}%`,
        onInput: (v) => board.updateSettings({ boardOpacity: v / 100 }),
    });
    const panel = h('div', { className: 'twb-panel-body' }, [sectionTitle('笔迹', '只影响墨迹，不影响背景'), opacity.el]);
    const popover = createPopover({ anchor, panel, kind: 'popover', label: '笔迹透明度' });
    const refresh = () => opacity.set(Math.round(board.settings.boardOpacity * 100));
    return { popover, refresh };
}

export function createBackgroundPopover(board, anchor) {
    const opacity = rangeRow({
        label: '透明度',
        min: 0,
        max: Math.round(LIMITS.backgroundOpacity[1] * 100),
        step: 5,
        value: Math.round(board.settings.backgroundOpacity * 100),
        format: (v) => `${Math.round(v)}%`,
        onInput: (v) => board.updateSettings({ backgroundOpacity: v / 100 }),
    });
    const panel = h('div', { className: 'twb-panel-body' }, [sectionTitle('背景', '0% 完全透视文档'), opacity.el]);
    const popover = createPopover({ anchor, panel, kind: 'popover', label: '背景透明度' });
    const refresh = () => opacity.set(Math.round(board.settings.backgroundOpacity * 100));
    return { popover, refresh };
}
