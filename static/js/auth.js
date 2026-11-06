const OWNER = Symbol.for('lanshare.authRecovery');
const recovery = window[OWNER] ||= {
    requestId: 0, lastSuccessId: 0, noticeRequestId: 0,
    notified: false, notice: null, loading: null, responses: new WeakMap(),
};

function resolveRequestUrl(input) {
    try {
        if (input instanceof Request) return new URL(input.url, window.location.origin);
        if (typeof input === 'string' || input instanceof URL) return new URL(input, window.location.origin);
        if (input && typeof input.url === 'string') return new URL(input.url, window.location.origin);
    } catch {
        return null;
    }
    return null;
}

function isSameOriginApiRequest(input) {
    const url = resolveRequestUrl(input);
    return Boolean(url && url.origin === window.location.origin && /^\/api(?:\/|$)/.test(url.pathname));
}

function loginTarget(data) {
    const code = data?.error?.code || data?.code;
    if (code !== 'login_required') return null;
    const target = data.redirect_to || data.error?.details?.redirect_to;
    if (typeof target !== 'string') return null;
    const url = resolveRequestUrl(target);
    if (!url || url.origin !== window.location.origin || url.username || url.password
        || !['/student/login', '/teacher/login'].includes(url.pathname)) return null;
    return url.href;
}

function markAuthSuccess(requestId) {
    recovery.lastSuccessId = Math.max(recovery.lastSuccessId, requestId);
    if (requestId <= recovery.noticeRequestId) return;
    recovery.notified = false;
    recovery.notice?.destroy();
    recovery.notice = null;
}

/** Report a verified browser-session loss while keeping the current draft alive. */
export async function reportAuthFailure(response, data, requestId = recovery.responses.get(response) || recovery.requestId) {
    if (response.status !== 401) return false;
    const target = loginTarget(data);
    if (!target) return false;
    if (requestId < recovery.lastSuccessId || recovery.notified) return true;
    recovery.notified = true;
    recovery.noticeRequestId = requestId;
    const event = new CustomEvent('lanshare:auth-required', { cancelable: true, detail: { loginUrl: target } });
    if (!window.dispatchEvent(event)) return true;
    // Loading the shared presentation must never keep an API caller waiting.
    recovery.loading ||= import('./lq/toast.js');
    void recovery.loading.then(({ toast }) => {
        if (!recovery.notified || requestId !== recovery.noticeRequestId) return;
        recovery.notice = toast('登录状态已失效。请在新窗口重新登录，再回到此页重试。', {
            tone: 'warning', duration: 0, key: 'session-login-required',
            action: { label: '重新登录', onClick: () => window.open(target, '_blank', 'noopener,noreferrer') },
        });
    }).catch(() => {
        recovery.loading = null;
        window.UI?.showToast?.('登录状态已失效，请打开登录页重新登录后重试。', 'warning', 0);
    });
    return true;
}

async function inspectAuthResponse(response, requestId) {
    if (response.status === 401 && (response.headers.get('content-type') || '').includes('application/json')) {
        try {
            await reportAuthFailure(response, await response.clone().json(), requestId);
        } catch {
            // Preserve the response and the caller's existing payload handling.
        }
    } else if (response.ok || response.status === 403) {
        markAuthSuccess(requestId);
    }
}

if (!window.__lanshareAuthFetchPatched) {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async function patchedFetch(input, init) {
        const track = isSameOriginApiRequest(input);
        const requestId = track ? ++recovery.requestId : 0;
        const response = await originalFetch(input, init);
        if (track) {
            recovery.responses.set(response, requestId);
            await inspectAuthResponse(response, requestId);
        }
        return response;
    };
    window.__lanshareAuthFetchPatched = true;
}

function xhrPayload(xhr) {
    if (xhr.responseType === 'json') return xhr.response;
    if (!(xhr.getResponseHeader('content-type') || '').includes('application/json')) return null;
    try { return JSON.parse(xhr.responseText || '{}'); } catch { return null; }
}

if (!window.__lanshareAuthXhrPatched) {
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
        this.__lanshareAuthUrl = url;
        return originalOpen.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function patchedSend(body) {
        this.__lanshareAuthRequestId = isSameOriginApiRequest(this.__lanshareAuthUrl) ? ++recovery.requestId : 0;
        if (!this.__lanshareAuthHandlerBound) {
            this.addEventListener('readystatechange', () => {
                if (this.readyState !== XMLHttpRequest.DONE || !this.__lanshareAuthRequestId) return;
                if (this.status === 401) {
                    void reportAuthFailure(this, xhrPayload(this), this.__lanshareAuthRequestId);
                } else if ((this.status >= 200 && this.status < 400) || this.status === 403) {
                    markAuthSuccess(this.__lanshareAuthRequestId);
                }
            });
            this.__lanshareAuthHandlerBound = true;
        }
        return originalSend.call(this, body);
    };
    window.__lanshareAuthXhrPatched = true;
}
