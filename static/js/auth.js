function resolveRequestUrl(input) {
    try {
        if (input instanceof Request) {
            return new URL(input.url, window.location.origin);
        }
        if (typeof input === 'string') {
            return new URL(input, window.location.origin);
        }
        if (input && typeof input.url === 'string') {
            return new URL(input.url, window.location.origin);
        }
    } catch {
        return null;
    }
    return null;
}

function extractRedirectTarget(data) {
    if (data && typeof data === 'object' && data.redirect_to) {
        return String(data.redirect_to);
    }
    return null;
}

function isSameOriginApiRequest(input) {
    const url = resolveRequestUrl(input);
    return Boolean(url && url.origin === window.location.origin && url.pathname.startsWith('/api'));
}

function redirectToAuthTarget(redirectTo) {
    if (!redirectTo || window.__lanshareAuthRedirectInFlight) {
        return;
    }
    window.__lanshareAuthRedirectInFlight = true;
    window.location.assign(redirectTo);
}

async function extractAuthRedirect(response) {
    if (response.status !== 401 && response.status !== 403) {
        return null;
    }

    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
        return null;
    }

    try {
        const data = await response.clone().json();
        return extractRedirectTarget(data);
    } catch {
        return null;
    }
}

if (!window.__lanshareAuthFetchPatched) {
    const originalFetch = window.fetch.bind(window);

    window.fetch = async function patchedFetch(input, init) {
        const response = await originalFetch(input, init);

        if (!isSameOriginApiRequest(input)) {
            return response;
        }

        const redirectTo = await extractAuthRedirect(response);
        if (!redirectTo) {
            return response;
        }

        redirectToAuthTarget(redirectTo);
        return new Promise(() => {});
    };

    window.__lanshareAuthFetchPatched = true;
}

function extractXhrAuthRedirect(xhr) {
    if (xhr.status !== 401 && xhr.status !== 403) {
        return null;
    }

    if (xhr.responseType === 'json') {
        return extractRedirectTarget(xhr.response);
    }

    const contentType = xhr.getResponseHeader('content-type') || '';
    if (!contentType.includes('application/json')) {
        return null;
    }

    try {
        return extractRedirectTarget(JSON.parse(xhr.responseText || '{}'));
    } catch {
        return null;
    }
}

if (!window.__lanshareAuthXhrPatched) {
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
        this.__lanshareAuthUrl = url;
        return originalOpen.call(this, method, url, ...rest);
    };

    XMLHttpRequest.prototype.send = function patchedSend(body) {
        if (!this.__lanshareAuthHandlerBound) {
            this.addEventListener('readystatechange', () => {
                if (this.readyState !== XMLHttpRequest.DONE) {
                    return;
                }
                if (!isSameOriginApiRequest(this.__lanshareAuthUrl)) {
                    return;
                }

                const redirectTo = extractXhrAuthRedirect(this);
                if (redirectTo) {
                    redirectToAuthTarget(redirectTo);
                }
            });
            this.__lanshareAuthHandlerBound = true;
        }

        return originalSend.call(this, body);
    };

    window.__lanshareAuthXhrPatched = true;
}
