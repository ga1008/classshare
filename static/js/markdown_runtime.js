(function initMarkdownRuntime(globalObject) {
    function normalizeContent(value) {
        if (value == null) {
            return '';
        }
        return String(value);
    }

    function escapeHtml(value) {
        return normalizeContent(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function createFallbackHtml(content, fallbackMode) {
        const safeContent = escapeHtml(content);
        switch (fallbackMode) {
        case 'lines':
            return safeContent.replace(/\n/g, '<br>');
        case 'pre':
            return `<pre>${safeContent}</pre>`;
        case 'none':
            return safeContent;
        case 'pre-code':
        default:
            return `<pre><code>${safeContent}</code></pre>`;
        }
    }

    function getMarkedRuntime() {
        if (!globalObject.marked || typeof globalObject.marked.parse !== 'function') {
            return null;
        }
        return globalObject.marked;
    }

    function parse(content, options) {
        const config = options || {};
        const emptyHtml = typeof config.emptyHtml === 'string' ? config.emptyHtml : '';

        if (content == null || content === '') {
            return emptyHtml;
        }

        const normalized = normalizeContent(content).trim();
        if (!normalized) {
            return emptyHtml;
        }

        try {
            const markedRuntime = getMarkedRuntime();
            if (!markedRuntime) {
                throw new Error('marked is unavailable');
            }

            return markedRuntime.parse(normalized);
        } catch (error) {
            if (!config.silent) {
                console.error('Markdown render failed:', error);
            }
            return createFallbackHtml(normalized, config.fallbackMode || 'pre-code');
        }
    }

    function renderIntoElement(element, content, options) {
        if (!element) {
            return '';
        }

        const html = parse(content, options);
        element.innerHTML = html;
        return html;
    }

    const api = {
        escapeHtml,
        createFallbackHtml,
        hasParser() {
            return Boolean(getMarkedRuntime());
        },
        parse,
        renderIntoElement,
    };

    globalObject.MarkdownRuntime = api;

    if (typeof globalObject.safeMarkedParse !== 'function') {
        globalObject.safeMarkedParse = function safeMarkedParse(content, fallback) {
            return parse(content, {
                emptyHtml: typeof fallback === 'string' ? fallback : '',
                fallbackMode: 'pre-code',
            });
        };
    }

    if (typeof globalObject.escapeHtml !== 'function') {
        globalObject.escapeHtml = escapeHtml;
    }
})(typeof globalThis !== 'undefined' ? globalThis : window);
