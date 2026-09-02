/**
 * 文本工具 mixin：舞台内 textarea 编辑器的打开/提交/关闭。挂到 TeacherWhiteboard.prototype。
 */
import { clamp, makeId, nowIso } from './state.js';

export const textEditorMixin = {
    openTextEditor(worldPoint) {
        this.closeTextEditor();
        const screenPoint = this.worldToScreen(worldPoint);
        const editor = document.createElement('textarea');
        editor.className = 'teacher-whiteboard-text-editor';
        editor.rows = 2;
        editor.placeholder = '输入文字';
        editor.style.left = `${clamp(screenPoint.x, 8, Math.max(8, this.canvasWidth - 220))}px`;
        editor.style.top = `${clamp(screenPoint.y, 8, Math.max(8, this.canvasHeight - 80))}px`;
        editor.style.color = this.settings.textColor;
        editor.style.fontSize = `${this.settings.fontSize}px`;
        editor.addEventListener('pointerdown', (pointerEvent) => pointerEvent.stopPropagation());
        editor.addEventListener('keydown', (keyEvent) => {
            if (keyEvent.key === 'Escape') { keyEvent.preventDefault(); this.closeTextEditor(); return; }
            if (keyEvent.key === 'Enter' && !keyEvent.shiftKey) { keyEvent.preventDefault(); this.commitTextEditor(); }
        });
        editor.addEventListener('blur', () => window.setTimeout(() => this.commitTextEditor(), 0), { once: true });
        this.stageEl.appendChild(editor);
        this.textEditor = { element: editor, worldPoint, fontSize: this.settings.fontSize / this.viewport.scale, color: this.settings.textColor };
        window.requestAnimationFrame(() => editor.focus());
    },

    commitTextEditor() {
        if (!this.textEditor?.element) return;
        const data = this.textEditor;
        const text = data.element.value.trim();
        this.closeTextEditor();
        if (!text) return;
        this.pushUndoSnapshot();
        this.activeBoard.elements.push({
            id: makeId('text'), type: 'text', text, x: data.worldPoint.x, y: data.worldPoint.y, color: data.color, fontSize: data.fontSize, createdAt: nowIso(),
        });
        this.scheduleRender(true);
        this.markDirty();
    },

    closeTextEditor() {
        if (!this.textEditor?.element) return;
        const editor = this.textEditor.element;
        this.textEditor = null;
        editor.remove();
    },
};
