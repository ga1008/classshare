/** Classroom readers preserve the original tab's discussion and upload state. */
export function returnFromClassroomReader(source, windowRef = window, frame = null) {
    if (!source || !/^\/classroom\/[1-9]\d*$/.test(source.url || '')) return false;
    if (frame && source.close_tab) {
        try {
            const initial = new URL(frame.src, windowRef.location.origin).href;
            const current = frame.contentWindow?.location?.href;
            // history.length does not shrink after Back; compare the actual
            // package location so Back at its starting page can close the tab.
            if (current && current !== 'about:blank' && current !== initial) {
                windowRef.history.back();
                return true;
            }
        } catch {
            // A package may navigate its iframe to another origin. Its joint
            // history can still return to the authorized entry without access
            // to the cross-origin document.
            windowRef.history.back();
            return true;
        }
    }
    if (source.close_tab) {
        windowRef.close();
        windowRef.setTimeout(() => windowRef.location.assign(source.url), 160);
    } else windowRef.location.assign(source.url);
    return true;
}

export function initClassroomReaderReturn(documentRoot = document, windowRef = window) {
    const source = windowRef.MATERIAL_READER_RETURN;
    if (!source) return;
    const frame = documentRoot.getElementById('render-shell-frame');
    documentRoot.querySelectorAll('[data-classroom-reader-return]').forEach(button => {
        if (button.dataset.readerReturnBound) return;
        button.dataset.readerReturnBound = 'true';
        button.addEventListener('click', event => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button > 0) return;
            if (returnFromClassroomReader(source, windowRef, frame)) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        }, true);
    });
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initClassroomReaderReturn(), { once: true });
    else initClassroomReaderReturn();
}
