function getMarkdownRuntime() {
    return window.MarkdownRuntime || null;
}

function escapeHtml(unsafe) {
    const runtime = getMarkdownRuntime();
    if (runtime && typeof runtime.escapeHtml === 'function') {
        return runtime.escapeHtml(unsafe);
    }

    return String(unsafe ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function safeMarkedParse(content, fallback = '') {
    const runtime = getMarkdownRuntime();
    if (runtime && typeof runtime.parse === 'function') {
        return runtime.parse(content, {
            emptyHtml: fallback,
            fallbackMode: 'pre-code',
        });
    }

    if (content == null || content === '') {
        return fallback;
    }

    return `<pre><code>${escapeHtml(String(content))}</code></pre>`;
}

window.safeMarkedParse = safeMarkedParse;
window.escapeHtml = window.escapeHtml || escapeHtml;
