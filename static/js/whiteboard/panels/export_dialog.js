/**
 * 导出弹窗：格式、尺寸（比例锁定）、快捷尺寸、文件名、实时体积与 5MB 拟合。
 */
import { showToast } from '../../ui.js';
import { EXPORT, ICONS } from '../constants.js';
import {
    EXPORT_FORMATS, buildFileName, defaultExportSize, exportFrame, fitUnderLimit, formatBytes,
    getFormat, lockAspect, renderExportBlob, downloadBlob,
} from '../export.js';
import { createPopover } from '../popover.js';
import { h } from './controls.js';

export function createExportDialog(board) {
    const state = { frame: null, aspect: 1, width: 0, height: 0, formatId: 'png-white', blob: null, renderToken: 0 };
    let debounceTimer = null;
    let popover = null;

    const previewImg = h('img', { className: 'twb-export-preview-img', alt: '导出预览' });
    const previewBox = h('div', { className: 'twb-export-preview' }, [previewImg]);
    const sizeLabel = h('span', { className: 'twb-export-size', text: '—' });
    const widthInput = h('input', { type: 'number', className: 'twb-input twb-input--num', min: EXPORT.MIN_EDGE, max: EXPORT.MAX_EDGE, 'aria-label': '宽度（像素）', 'data-autofocus': true });
    const heightInput = h('input', { type: 'number', className: 'twb-input twb-input--num', min: EXPORT.MIN_EDGE, max: EXPORT.MAX_EDGE, 'aria-label': '高度（像素）' });
    const ratioLabel = h('span', { className: 'twb-export-ratio' });
    const nameInput = h('input', { type: 'text', className: 'twb-input', maxlength: EXPORT.FILE_NAME_MAX, 'aria-label': '文件名' });
    const extLabel = h('span', { className: 'twb-export-ext', text: '.png' });
    const fitButton = h('button', { type: 'button', className: 'twb-btn twb-btn--ghost', text: '自动缩到 5MB 内', hidden: true, onClick: () => fitToLimit() });
    const downloadButton = h('button', { type: 'button', className: 'twb-btn twb-btn--primary', html: `${ICONS.download}<span>下载</span>`, onClick: () => download() });

    const formatGroup = h('div', { className: 'twb-segmented', role: 'radiogroup', 'aria-label': '格式' }, EXPORT_FORMATS.map((format) => h('button', {
        type: 'button', role: 'radio', className: 'twb-segment', dataset: { value: format.id }, text: format.label,
        onClick: () => setFormat(format.id),
    })));
    const presets = h('div', { className: 'twb-row twb-export-presets' }, [
        h('span', { className: 'twb-row-label', text: '快捷' }),
        h('div', { className: 'twb-chip-group' }, EXPORT.PRESETS.map((edge) => h('button', { type: 'button', className: 'twb-chip-btn', text: String(edge), onClick: () => setLongEdge(edge) }))),
    ]);

    const panel = h('div', { className: 'twb-panel-body twb-export' }, [
        h('div', { className: 'twb-dialog-head' }, [
            h('div', { className: 'twb-dialog-title', text: '导出白板' }),
            h('button', { type: 'button', className: 'twb-icon-btn', title: '关闭', 'aria-label': '关闭', html: ICONS.close, onClick: () => popover.close('close') }),
        ]),
        previewBox,
        h('div', { className: 'twb-export-grid' }, [
            h('div', { className: 'twb-row' }, [h('span', { className: 'twb-row-label', text: '格式' }), formatGroup]),
            h('div', { className: 'twb-row' }, [
                h('span', { className: 'twb-row-label', text: '尺寸' }),
                h('div', { className: 'twb-export-size-row' }, [
                    h('label', { className: 'twb-export-dim' }, ['宽 ', widthInput, ' px']),
                    h('span', { className: 'twb-export-x', text: '×' }),
                    h('label', { className: 'twb-export-dim' }, ['高 ', heightInput, ' px']),
                    ratioLabel,
                ]),
            ]),
            presets,
            h('div', { className: 'twb-row' }, [h('span', { className: 'twb-row-label', text: '文件名' }), h('div', { className: 'twb-export-name' }, [nameInput, extLabel])]),
        ]),
        h('div', { className: 'twb-dialog-actions' }, [
            h('div', { className: 'twb-export-meta' }, [h('span', { text: '预计 ' }), sizeLabel, fitButton]),
            h('div', { className: 'twb-dialog-buttons' }, [
                h('button', { type: 'button', className: 'twb-btn', text: '取消', onClick: () => popover.close('cancel') }),
                downloadButton,
            ]),
        ]),
    ]);

    function revokePreview() {
        if (previewImg.src && previewImg.src.startsWith('blob:')) URL.revokeObjectURL(previewImg.src);
        previewImg.removeAttribute('src');
    }

    popover = createPopover({
        panel, kind: 'dialog', modal: true, label: '导出白板',
        onClose: () => { window.clearTimeout(debounceTimer); state.renderToken += 1; revokePreview(); },
    });

    function setFormat(id) {
        state.formatId = id;
        formatGroup.querySelectorAll('.twb-segment').forEach((button) => {
            const active = button.dataset.value === id;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-checked', active ? 'true' : 'false');
        });
        extLabel.textContent = `.${getFormat(id).ext}`;
        previewBox.classList.toggle('is-transparent', id === 'png-transparent');
        scheduleRender();
    }

    function applySize(size) {
        state.width = size.width;
        state.height = size.height;
        widthInput.value = String(size.width);
        heightInput.value = String(size.height);
        scheduleRender();
    }

    function setLongEdge(edge) {
        applySize(defaultExportSize(state.aspect, edge));
    }

    widthInput.addEventListener('input', () => {
        const value = Number(widthInput.value);
        if (!(value > 0)) return;
        const size = lockAspect({ width: value }, state.aspect);
        state.width = size.width;
        state.height = size.height;
        heightInput.value = String(size.height);
        scheduleRender();
    });
    heightInput.addEventListener('input', () => {
        const value = Number(heightInput.value);
        if (!(value > 0)) return;
        const size = lockAspect({ height: value }, state.aspect);
        state.width = size.width;
        state.height = size.height;
        widthInput.value = String(size.width);
        scheduleRender();
    });

    function scheduleRender() {
        window.clearTimeout(debounceTimer);
        sizeLabel.textContent = '计算中…';
        sizeLabel.classList.remove('is-over');
        state.blob = null;
        debounceTimer = window.setTimeout(() => { debounceTimer = null; render(); }, EXPORT.PREVIEW_DEBOUNCE_MS);
    }

    /** 下载前保证最新尺寸已渲染（用户改完尺寸立刻点下载也不丢失）。 */
    async function ensureRendered() {
        if (debounceTimer) {
            window.clearTimeout(debounceTimer);
            debounceTimer = null;
            await render();
        } else if (!state.blob) {
            await render();
        }
    }

    function renderAt(size) {
        return renderExportBlob({
            elements: board.activeBoard.elements, frame: state.frame, width: size.width, height: size.height, formatId: state.formatId,
        });
    }

    async function render() {
        const token = ++state.renderToken;
        try {
            const blob = await renderAt({ width: state.width, height: state.height });
            if (token !== state.renderToken) return;
            state.blob = blob;
            revokePreview();
            previewImg.src = URL.createObjectURL(blob);
            const over = blob.size > EXPORT.MAX_BYTES;
            sizeLabel.textContent = formatBytes(blob.size);
            sizeLabel.classList.toggle('is-over', over);
            fitButton.hidden = !over;
            downloadButton.disabled = over;
        } catch (error) {
            if (token !== state.renderToken) return;
            sizeLabel.textContent = '渲染失败';
            showToast(error?.message || '导出预览失败', 'error');
        }
    }

    async function fitToLimit() {
        fitButton.disabled = true;
        try {
            const result = await fitUnderLimit(renderAt, { width: state.width, height: state.height }, state.aspect);
            applySize({ width: result.width, height: result.height });
        } finally {
            fitButton.disabled = false;
        }
    }

    async function download() {
        downloadButton.disabled = true;
        try {
            await ensureRendered();
        } finally {
            downloadButton.disabled = Boolean(state.blob && state.blob.size > EXPORT.MAX_BYTES);
        }
        if (!state.blob) return;
        if (state.blob.size > EXPORT.MAX_BYTES) {
            showToast('图片超过 5MB，请先缩小尺寸', 'warning');
            return;
        }
        const stem = nameInput.value.trim() || buildFileName(board.context.materialName, board.activeBoard.name);
        const fileName = `${stem}.${getFormat(state.formatId).ext}`;
        downloadBlob(state.blob, fileName);
        showToast(`已导出 ${fileName}（${formatBytes(state.blob.size)}）`, 'success', 2600);
        popover.close('download');
    }

    function open() {
        const frame = exportFrame(board.activeBoard.elements, board.measureWidth);
        if (!frame) {
            showToast('白板为空，没有可导出的内容', 'info');
            return;
        }
        state.frame = frame;
        state.aspect = frame.width / frame.height;
        ratioLabel.textContent = state.aspect >= 1 ? `比例 ${state.aspect.toFixed(2)} : 1` : `比例 1 : ${(1 / state.aspect).toFixed(2)}`;
        nameInput.value = buildFileName(board.context.materialName, board.activeBoard.name);
        state.formatId = 'png-white';
        popover.open();
        setFormat('png-white');
        applySize(defaultExportSize(state.aspect));
    }

    return { popover, open };
}
