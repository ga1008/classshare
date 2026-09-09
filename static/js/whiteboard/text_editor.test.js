import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { textEditorMixin } from './text_editor.js';

function host() {
    return {
        ...textEditorMixin,
        viewport: { scale: 1 }, canvasWidth: 800, canvasHeight: 600,
        settings: { textColor: '#f00', fontSize: 28 },
        stageEl: { appendChild: vi.fn() }, worldToScreen: (point) => point,
        commitTextEditor: vi.fn(),
    };
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('window', { setTimeout, requestAnimationFrame: (callback) => setTimeout(callback, 16) });
    vi.stubGlobal('document', {
        createElement: () => {
            const listeners = {};
            return {
                style: {}, listeners, addEventListener: (name, listener) => { listeners[name] = listener; },
                remove: () => listeners.blur?.(), focus: vi.fn(),
            };
        },
    });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

test('旧文本编辑器失焦回调不能提交新编辑器，延迟 focus 只作用于当前编辑器', () => {
    const app = host();
    app.openTextEditor({ x: 100, y: 100 });
    const first = app.textEditor.element;
    app.openTextEditor({ x: 200, y: 200 });
    const current = app.textEditor.element;
    vi.runAllTimers();
    expect(app.commitTextEditor).not.toHaveBeenCalled();
    expect(first.focus).not.toHaveBeenCalled();
    expect(current.focus).toHaveBeenCalledTimes(1);
    current.listeners.blur();
    vi.runAllTimers();
    expect(app.commitTextEditor).toHaveBeenCalledTimes(1);
});

test('编辑文字时 Escape 仅取消文本，阻止继续冒泡关闭白板', () => {
    const app = host();
    app.openTextEditor({ x: 100, y: 100 });
    const event = { key: 'Escape', preventDefault: vi.fn(), stopPropagation: vi.fn() };
    app.textEditor.element.listeners.keydown(event);
    vi.runAllTimers();
    expect(app.textEditor).toBeNull();
    expect(event.stopPropagation).toHaveBeenCalledTimes(1);
    expect(app.commitTextEditor).not.toHaveBeenCalled();
});
