/**
 * Shared handwriting pad for signature capture (鼠标/触摸通吃).
 *
 * Opens a self-contained overlay with a canvas; confirm hands back a white
 * background PNG Blob — the server-side upload normalizer trims margins and
 * converts the background to transparent, so the pad stays dumb on purpose.
 *
 * @param {{ onConfirm: (blob: Blob) => void, title?: string }} options
 */
export function openSignaturePad({ onConfirm, title = '手写签名' }) {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:2600;display:grid;place-items:center;background:rgba(15,23,42,.55);backdrop-filter:blur(3px);padding:16px;';
    overlay.innerHTML = `
        <div style="display:grid;gap:12px;width:min(720px,100%);padding:18px;border-radius:14px;background:#fff;box-shadow:0 24px 70px rgba(15,23,42,.3);">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                <strong style="color:#172033;font-size:1.05rem;">${title}</strong>
                <button type="button" data-pad-close style="width:32px;height:32px;border:0;border-radius:8px;background:#f1f5f9;color:#475569;font-size:20px;cursor:pointer;">×</button>
            </div>
            <p style="margin:0;color:#64748b;font-size:0.85rem;">用鼠标或手指在下方白板上书写签名；提交后系统会自动裁边并把白底转为透明。</p>
            <canvas data-pad-canvas style="width:100%;height:280px;border:1px dashed #cbd5e1;border-radius:10px;background:#fff;cursor:crosshair;touch-action:none;"></canvas>
            <div style="display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;">
                <button type="button" class="btn btn-ghost btn-sm" data-pad-undo>撤销一笔</button>
                <button type="button" class="btn btn-ghost btn-sm" data-pad-clear>清空</button>
                <span style="flex:1"></span>
                <button type="button" class="btn btn-outline btn-sm" data-pad-cancel>取消</button>
                <button type="button" class="btn btn-primary btn-sm" data-pad-confirm disabled>使用这个签名</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const canvas = overlay.querySelector('[data-pad-canvas]');
    const confirmButton = overlay.querySelector('[data-pad-confirm]');
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const cssWidth = canvas.clientWidth || 680;
    const cssHeight = 280;
    canvas.width = Math.round(cssWidth * ratio);
    canvas.height = Math.round(cssHeight * ratio);
    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio);
    ctx.lineWidth = 4.5;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#111827';

    /** @type {Array<Array<{x: number, y: number}>>} */
    const strokes = [];
    let activeStroke = null;

    const paintBackground = () => {
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.restore();
    };

    const redraw = () => {
        paintBackground();
        strokes.forEach((stroke) => {
            if (stroke.length < 2) return;
            ctx.beginPath();
            ctx.moveTo(stroke[0].x, stroke[0].y);
            stroke.slice(1).forEach((point) => ctx.lineTo(point.x, point.y));
            ctx.stroke();
        });
        if (confirmButton) confirmButton.disabled = !strokes.some((stroke) => stroke.length > 1);
    };

    const pointFromEvent = (event) => {
        const rect = canvas.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    canvas.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        canvas.setPointerCapture(event.pointerId);
        activeStroke = [pointFromEvent(event)];
        strokes.push(activeStroke);
    });
    canvas.addEventListener('pointermove', (event) => {
        if (!activeStroke) return;
        activeStroke.push(pointFromEvent(event));
        redraw();
    });
    const endStroke = () => {
        if (activeStroke && activeStroke.length < 2) strokes.pop();
        activeStroke = null;
        redraw();
    };
    canvas.addEventListener('pointerup', endStroke);
    canvas.addEventListener('pointercancel', endStroke);

    const close = () => overlay.remove();
    overlay.querySelector('[data-pad-close]')?.addEventListener('click', close);
    overlay.querySelector('[data-pad-cancel]')?.addEventListener('click', close);
    overlay.addEventListener('click', (event) => {
        if (event.target === overlay) close();
    });
    overlay.querySelector('[data-pad-undo]')?.addEventListener('click', () => {
        strokes.pop();
        redraw();
    });
    overlay.querySelector('[data-pad-clear]')?.addEventListener('click', () => {
        strokes.length = 0;
        redraw();
    });
    confirmButton?.addEventListener('click', () => {
        canvas.toBlob((blob) => {
            if (blob) onConfirm(blob);
            close();
        }, 'image/png');
    });

    paintBackground();
    redraw();
}
