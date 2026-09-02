/**
 * 工具栏 DOM 模板（四组：板组 | 工具 | 样式芯片 | 操作）。
 * 保留 teacher-whiteboard-toolbar / -btn 基类（考试画板共用），新样式一律 twb- 前缀。
 */
import { ICONS } from './constants.js';

const btn = (attrs, icon, extraClass = '') => `<button type="button" class="teacher-whiteboard-btn twb-btn-icon ${extraClass}" ${attrs}>${ICONS[icon]}</button>`;

export function buildToolbarHtml() {
    return `
        <div class="teacher-whiteboard-toolbar twb-toolbar" id="teacher-whiteboard-toolbar" role="toolbar" aria-label="讲课白板工具栏">
            <div class="twb-group twb-group--board" aria-label="白板">
                ${btn('data-whiteboard-action="history" title="历史白板" aria-label="历史白板" aria-haspopup="dialog" aria-expanded="false"', 'menu')}
                <button type="button" class="teacher-whiteboard-btn twb-btn-icon twb-save-btn" data-whiteboard-action="save-menu" title="保存" aria-label="保存" aria-haspopup="menu" aria-expanded="false">
                    ${ICONS.save}<span class="twb-save-caret">${ICONS.chevron}</span><i class="twb-status-dot" id="teacher-whiteboard-sync-dot" data-status="local"></i>
                </button>
                ${btn('data-whiteboard-action="new-board" title="新建白板" aria-label="新建白板"', 'plus')}
            </div>
            <span class="twb-sep" aria-hidden="true"></span>
            <div class="twb-group twb-group--tools" aria-label="工具">
                ${btn('data-whiteboard-tool="hand" title="拖动画布（H / 按住空格）" aria-label="拖动画布"', 'hand')}
                ${btn('data-whiteboard-tool="brush" title="画笔（B）" aria-label="画笔"', 'pen')}
                ${btn('data-whiteboard-tool="eraser" title="橡皮（E，再点一次调节）" aria-label="橡皮" aria-haspopup="dialog" aria-expanded="false"', 'eraser')}
                ${btn('data-whiteboard-tool="text" title="文字（T）" aria-label="文字"', 'text')}
                <span class="twb-sep twb-sep--inner" aria-hidden="true"></span>
                ${btn('data-whiteboard-shape="circle" title="圆形" aria-label="圆形"', 'circle')}
                ${btn('data-whiteboard-shape="square" title="正方形" aria-label="正方形"', 'square')}
                ${btn('data-whiteboard-shape="rectangle" title="长方形" aria-label="长方形"', 'rectangle')}
                ${btn('data-whiteboard-shape="rounded" title="圆角矩形" aria-label="圆角矩形"', 'rounded')}
                ${btn('data-whiteboard-shape="diamond" title="菱形" aria-label="菱形"', 'diamond')}
            </div>
            <span class="twb-sep" aria-hidden="true"></span>
            <div class="twb-group twb-group--chips" aria-label="样式">
                <button type="button" class="twb-chip" data-whiteboard-chip="brush" title="画笔颜色与粗细" aria-haspopup="dialog" aria-expanded="false">
                    <i class="twb-chip-swatch" id="twb-chip-brush-swatch"></i><span class="twb-chip-label">画笔</span><b class="twb-chip-value" id="twb-chip-brush-value"></b>
                </button>
                <button type="button" class="twb-chip" data-whiteboard-chip="text" title="文字颜色与字号" aria-haspopup="dialog" aria-expanded="false">
                    <i class="twb-chip-swatch twb-chip-swatch--text" id="twb-chip-text-swatch">T</i><span class="twb-chip-label">文字</span><b class="twb-chip-value" id="twb-chip-text-value"></b>
                </button>
                <button type="button" class="twb-chip" data-whiteboard-chip="ink" title="笔迹透明度" aria-haspopup="dialog" aria-expanded="false">
                    <span class="twb-chip-icon">${ICONS.ink}</span><span class="twb-chip-label">笔迹</span><b class="twb-chip-value" id="twb-chip-ink-value"></b>
                </button>
                <button type="button" class="twb-chip" data-whiteboard-chip="background" title="背景透明度" aria-haspopup="dialog" aria-expanded="false">
                    <span class="twb-chip-icon">${ICONS.grid}</span><span class="twb-chip-label">背景</span><b class="twb-chip-value" id="twb-chip-background-value"></b>
                </button>
            </div>
            <span class="twb-sep" aria-hidden="true"></span>
            <div class="twb-group twb-group--actions" aria-label="操作">
                ${btn('data-whiteboard-action="undo" title="撤销（Ctrl+Z）" aria-label="撤销"', 'undo')}
                ${btn('data-whiteboard-action="redo" title="重做（Ctrl+Y）" aria-label="重做"', 'redo')}
                ${btn('data-whiteboard-action="zoom-out" title="缩小" aria-label="缩小"', 'zoomOut')}
                ${btn('data-whiteboard-action="zoom-in" title="放大" aria-label="放大"', 'zoomIn')}
                ${btn('data-whiteboard-action="reset-view" title="回到中心" aria-label="回到中心"', 'resetView')}
                <span class="twb-sep twb-sep--inner" aria-hidden="true"></span>
                <button type="button" class="twb-clear-btn" data-whiteboard-action="clear" title="清空当前白板" aria-label="清屏" aria-haspopup="dialog" aria-expanded="false">${ICONS.clear}<span>清屏</span></button>
            </div>
        </div>`;
}
