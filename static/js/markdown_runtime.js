(function initMarkdownRuntime(globalObject) {
    const ELEMENT_NODE = 1;
    const TEXT_NODE = 3;
    const COMMENT_NODE = 8;
    const DOCUMENT_FRAGMENT_NODE = 11;
    const GLOBAL_ATTRIBUTES = new Set(['class', 'title', 'id', 'lang', 'dir', 'role', 'aria-label']);
    const TAG_ATTRIBUTES = {
        a: new Set(['href', 'target', 'rel']),
        img: new Set(['src', 'alt', 'width', 'height', 'loading']),
        input: new Set(['type', 'checked', 'disabled']),
        th: new Set(['align', 'colspan', 'rowspan']),
        td: new Set(['align', 'colspan', 'rowspan']),
        ol: new Set(['start', 'reversed']),
        col: new Set(['span', 'width']),
        details: new Set(['open']),
    };
    const ALLOWED_TAGS = new Set([
        'a', 'abbr', 'article', 'b', 'blockquote', 'br', 'caption', 'code', 'col',
        'colgroup', 'dd', 'del', 'details', 'div', 'dl', 'dt', 'em', 'figcaption',
        'figure', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'input',
        'ins', 'kbd', 'li', 'mark', 'ol', 'p', 'pre', 's', 'samp', 'section',
        'small', 'span', 'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td',
        'tfoot', 'th', 'thead', 'time', 'tr', 'u', 'ul', 'var',
    ]);
    const DROP_CONTENT_TAGS = new Set([
        'base', 'embed', 'form', 'iframe', 'link', 'meta', 'object', 'script',
        'select', 'style', 'textarea',
    ]);
    const SAFE_TARGETS = new Set(['_blank', '_self', '_parent', '_top']);
    const SAFE_URL_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:']);
    const SAFE_DATA_IMAGE_PATTERN = /^data:image\/(?:png|gif|jpe?g|webp|bmp);base64,[a-z0-9+/=\s]+$/i;
    // User-authored content (blog posts/comments) is "untrusted": an <img> fires
    // a request for every reader without a click, so a same-origin path such as
    // "/logout" must never be reachable through it. Only stored media and static
    // assets are allowed on our origin; ids/classes cannot spoof site UI either.
    const UNTRUSTED_SAME_ORIGIN_IMAGE_PATTERN = /^\/(?:api\/blog\/image\/[a-f0-9]{64}|static\/)[^\s]*$/i;
    const UNTRUSTED_CODE_CLASS_PATTERN = /^language-[a-z0-9_+-]{1,40}$/i;
    const DEFAULT_POLICY = Object.freeze({ untrusted: false });
    let activePolicy = DEFAULT_POLICY;

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

    function isSafeUntrustedImageSource(value) {
        // Browsers resolve "..", "%2e" and "\" path segments, so "/static/../logout"
        // would still reach /logout; refuse any such form outright.
        if (/\.\.|%2e|\\/i.test(value)) {
            return false;
        }
        if (UNTRUSTED_SAME_ORIGIN_IMAGE_PATTERN.test(value)) {
            return true;
        }
        const compactValue = value.replace(/[\u0000-\u001f\u007f\s]+/g, '');
        if (SAFE_DATA_IMAGE_PATTERN.test(compactValue)) {
            return true;
        }
        // Only absolute external images; every other relative form ("logout",
        // "./x", "?y", "//host") would resolve against our own origin.
        if (!/^https?:\/\/[^/\s]/i.test(compactValue)) {
            return false;
        }
        // An absolute URL naming our own host gets the same allow-list as a
        // relative one, otherwise "https://<site>/logout" slips through.
        const sameOrigin = resolveSameOriginPath(compactValue);
        return sameOrigin === null || UNTRUSTED_SAME_ORIGIN_IMAGE_PATTERN.test(sameOrigin);
    }

    /** Path+query when `value` resolves to the current origin, else null. */
    function resolveSameOriginPath(value) {
        if (typeof location === 'undefined' || typeof URL !== 'function') {
            return null;
        }
        try {
            const resolved = new URL(value, location.href);
            return resolved.origin === location.origin ? `${resolved.pathname}${resolved.search}` : null;
        } catch (error) {
            return null;
        }
    }

    function isUntrustedLogoutLink(value) {
        const path = resolveSameOriginPath(value);
        return path !== null && /^\/logout(?:[/?#]|$)/i.test(path);
    }

    function isSafeUrl(rawValue, tagName, attributeName) {
        const normalizedValue = normalizeContent(rawValue).trim();
        if (!normalizedValue) {
            return false;
        }
        if (activePolicy.untrusted && tagName === 'img' && attributeName === 'src') {
            return isSafeUntrustedImageSource(normalizedValue);
        }
        if (activePolicy.untrusted && attributeName === 'href' && isUntrustedLogoutLink(normalizedValue)) {
            // A disguised "sign out" link in someone else's post is never wanted.
            return false;
        }
        if (
            normalizedValue.startsWith('#')
            || normalizedValue.startsWith('/')
            || normalizedValue.startsWith('./')
            || normalizedValue.startsWith('../')
            || normalizedValue.startsWith('?')
        ) {
            return true;
        }

        const compactValue = normalizedValue.replace(/[\u0000-\u001f\u007f\s]+/g, '');
        const schemeMatch = compactValue.match(/^([a-z][a-z0-9+.-]*:)/i);
        if (!schemeMatch) {
            return true;
        }

        const scheme = schemeMatch[1].toLowerCase();
        if (SAFE_URL_PROTOCOLS.has(scheme)) {
            return true;
        }

        return (
            tagName === 'img'
            && attributeName === 'src'
            && SAFE_DATA_IMAGE_PATTERN.test(compactValue)
        );
    }

    function normalizeAttributeValue(attributeName, attributeValue) {
        const normalizedValue = normalizeContent(attributeValue).trim();
        if (!normalizedValue) {
            return '';
        }

        if (attributeName === 'class') {
            return normalizedValue.split(/\s+/).join(' ');
        }

        return normalizedValue;
    }

    function unwrapElement(element) {
        Array.from(element.childNodes).forEach((childNode) => sanitizeNode(childNode));

        const fragment = document.createDocumentFragment();
        // Sanitizing can remove or unwrap children. Move only surviving nodes;
        // a snapshot taken before sanitizing would resurrect rejected markup.
        while (element.firstChild) {
            fragment.appendChild(element.firstChild);
        }
        element.replaceWith(fragment);
    }

    function sanitizeAttribute(element, attribute) {
        const tagName = element.tagName.toLowerCase();
        const attributeName = attribute.name.toLowerCase();
        const allowedTagAttributes = TAG_ATTRIBUTES[tagName];

        if (
            attributeName.startsWith('on')
            || attributeName === 'style'
            || (attributeName !== 'aria-label' && attributeName.startsWith('aria-'))
            || (!GLOBAL_ATTRIBUTES.has(attributeName) && !allowedTagAttributes?.has(attributeName))
        ) {
            element.removeAttribute(attribute.name);
            return;
        }

        const normalizedValue = normalizeAttributeValue(attributeName, attribute.value);
        if (!normalizedValue && !['checked', 'disabled', 'open', 'reversed'].includes(attributeName)) {
            element.removeAttribute(attribute.name);
            return;
        }

        if (activePolicy.untrusted && (attributeName === 'id' || attributeName === 'class')) {
            // Keep only the highlighter class marked emits on code blocks.
            const keepCodeLanguage = attributeName === 'class' && tagName === 'code'
                && UNTRUSTED_CODE_CLASS_PATTERN.test(normalizedValue);
            if (!keepCodeLanguage) {
                element.removeAttribute(attribute.name);
                return;
            }
        }

        if (attributeName === 'href' || attributeName === 'src') {
            if (!isSafeUrl(normalizedValue, tagName, attributeName)) {
                element.removeAttribute(attribute.name);
                return;
            }
        }

        if (attributeName === 'target') {
            const normalizedTarget = normalizedValue.toLowerCase();
            if (!SAFE_TARGETS.has(normalizedTarget)) {
                element.removeAttribute(attribute.name);
                return;
            }
            element.setAttribute('target', normalizedTarget);
            return;
        }

        if (attributeName === 'align') {
            const normalizedAlign = normalizedValue.toLowerCase();
            if (!['left', 'center', 'right'].includes(normalizedAlign)) {
                element.removeAttribute(attribute.name);
                return;
            }
            element.setAttribute('align', normalizedAlign);
            return;
        }

        if (attributeName === 'type' && tagName === 'input') {
            if (normalizedValue.toLowerCase() !== 'checkbox') {
                element.removeAttribute(attribute.name);
                return;
            }
            element.setAttribute('type', 'checkbox');
            return;
        }

        element.setAttribute(attribute.name, normalizedValue);
    }

    function sanitizeElement(element) {
        const tagName = element.tagName.toLowerCase();
        if (DROP_CONTENT_TAGS.has(tagName)) {
            element.remove();
            return;
        }

        if (!ALLOWED_TAGS.has(tagName)) {
            unwrapElement(element);
            return;
        }

        Array.from(element.attributes).forEach((attribute) => {
            sanitizeAttribute(element, attribute);
        });

        Array.from(element.childNodes).forEach((childNode) => sanitizeNode(childNode));

        if (tagName === 'a') {
            const href = element.getAttribute('href');
            if (!href) {
                element.removeAttribute('target');
                element.removeAttribute('rel');
                return;
            }

            if (element.getAttribute('target') === '_blank') {
                element.setAttribute('rel', 'noopener noreferrer');
            } else {
                element.removeAttribute('rel');
            }
            return;
        }

        if (tagName === 'img') {
            if (!element.getAttribute('src')) {
                element.remove();
                return;
            }
            if (!element.getAttribute('loading')) {
                element.setAttribute('loading', 'lazy');
            }
            return;
        }

        if (tagName === 'input') {
            if ((element.getAttribute('type') || '').toLowerCase() !== 'checkbox') {
                element.remove();
                return;
            }
            element.setAttribute('disabled', '');
            if (element.hasAttribute('checked')) {
                element.setAttribute('checked', '');
            }
        }
    }

    function sanitizeNode(node) {
        if (!node) {
            return;
        }

        switch (node.nodeType) {
        case TEXT_NODE:
            return;
        case ELEMENT_NODE:
            sanitizeElement(node);
            return;
        case DOCUMENT_FRAGMENT_NODE:
            Array.from(node.childNodes).forEach((childNode) => sanitizeNode(childNode));
            return;
        case COMMENT_NODE:
        default:
            node.remove();
        }
    }

    /**
     * @param {string} html
     * @param {{untrusted?: boolean}} [options] untrusted: user-authored content
     *   (no same-origin image side effects, no id/class from the author).
     */
    function sanitizeHtml(html, options) {
        const normalizedHtml = normalizeContent(html);
        if (!normalizedHtml || typeof document === 'undefined') {
            return normalizedHtml;
        }

        const template = document.createElement('template');
        template.innerHTML = normalizedHtml;
        // Sanitizing is synchronous, so a scoped policy cannot leak to other calls.
        activePolicy = options && options.untrusted ? { untrusted: true } : DEFAULT_POLICY;
        try {
            sanitizeNode(template.content);
        } finally {
            activePolicy = DEFAULT_POLICY;
        }
        return template.innerHTML;
    }

    function parse(content, options) {
        const config = options || {};
        const emptyHtml = typeof config.emptyHtml === 'string' ? config.emptyHtml : '';

        if (content == null || content === '') {
            return emptyHtml;
        }

        const normalized = normalizeContent(content);
        if (!normalized.trim()) {
            return emptyHtml;
        }

        try {
            const markedRuntime = getMarkedRuntime();
            if (!markedRuntime) {
                throw new Error('marked is unavailable');
            }

            const markedOptions = { gfm: true };
            if (config.breaks) {
                // 聊天/AI 流式输出以单换行表达换行意图。
                markedOptions.breaks = true;
            }
            return sanitizeHtml(markedRuntime.parse(normalized, markedOptions), { untrusted: Boolean(config.untrusted) });
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
        sanitizeHtml,
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
