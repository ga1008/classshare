export class APIError extends Error {
    constructor(message, status, data, options = {}) {
        super(message);
        this.name = 'APIError';
        this.status = status;
        this.data = data;
        this.redirectTo = options.redirectTo || null;
        this.suppressToast = Boolean(options.suppressToast);
    }
}

function normalizeErrorMessage(rawMessage) {
    if (rawMessage == null) return '未知错误';

    const message = String(rawMessage)
        .replace(/\s+/g, ' ')
        .trim();

    if (!message) return '未知错误';

    if (/traceback|sqlite3\.|syntaxerror|valueerror|typeerror|keyerror|exception:/i.test(message)) {
        return '服务端处理失败，请稍后重试或联系管理员。';
    }

    return message.length > 220 ? `${message.slice(0, 220)}...` : message;
}

async function parseResponseData(response) {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
        return await response.json();
    }
    return await response.text();
}

function extractErrorMessage(response, data) {
    if (data && typeof data === 'object' && data.detail) {
        return normalizeErrorMessage(
            typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
        );
    }

    if (typeof data === 'string' && data.length > 0) {
        const trimmed = data.trim();
        const looksLikeHtml = /<!doctype html>|<html[\s>]/i.test(trimmed);
        if (looksLikeHtml) {
            const titleMatch = trimmed.match(/<title[^>]*>(.*?)<\/title>/i);
            return titleMatch
                ? normalizeErrorMessage(`服务异常：${titleMatch[1].trim()}`)
                : '服务异常，请稍后重试';
        }
        return normalizeErrorMessage(trimmed);
    }

    if (response.statusText) {
        return normalizeErrorMessage(response.statusText);
    }

    return '未知错误发生';
}

export async function handleAuthFailureResponse(response, preloadedData = undefined) {
    const data = preloadedData === undefined ? await parseResponseData(response) : preloadedData;
    const redirectTo = data && typeof data === 'object' ? data.redirect_to : null;

    if ((response.status === 401 || response.status === 403) && redirectTo) {
        window.location.href = redirectTo;
        throw new APIError('页面跳转中', response.status, data, {
            redirectTo,
            suppressToast: true,
        });
    }

    return data;
}

export async function apiFetch(endpoint, options = {}) {
    const defaultHeaders = {
        Accept: 'application/json'
    };

    if (options.body && !(options.body instanceof FormData) && typeof options.body === 'object') {
        options.body = JSON.stringify(options.body);
        defaultHeaders['Content-Type'] = 'application/json';
    }

    const requestConfig = {
        ...options,
        headers: {
            ...defaultHeaders,
            ...options.headers
        }
    };

    try {
        const response = await fetch(endpoint, requestConfig);
        let data = await parseResponseData(response);

        if (!response.ok) {
            data = await handleAuthFailureResponse(response, data);
            const errorMessage = extractErrorMessage(response, data);
            throw new APIError(errorMessage, response.status, data);
        }

        return data;
    } catch (error) {
        if (error instanceof APIError) {
            console.error(`[API Error ${error.status}] ${endpoint}:`, error.message);
            if (window.UI && window.UI.showToast && !options.silent && !error.suppressToast) {
                window.UI.showToast(`操作失败：${normalizeErrorMessage(error.message)}`, 'error');
            }
            throw error;
        }

        console.error(`[Network Error] ${endpoint}:`, error);
        if (window.UI && window.UI.showToast && !options.silent) {
            window.UI.showToast('网络连接异常，请检查您的网络设置。', 'error');
        }
        throw new Error('网络请求失败');
    }
}

export const API = {
    get: (url, options = {}) => apiFetch(url, { ...options, method: 'GET' }),
    post: (url, data, options = {}) => apiFetch(url, { ...options, method: 'POST', body: data }),
    put: (url, data, options = {}) => apiFetch(url, { ...options, method: 'PUT', body: data }),
    delete: (url, options = {}) => apiFetch(url, { ...options, method: 'DELETE' })
};

window.API = API;
window.apiFetch = apiFetch;
window.apiRequest = apiFetch;
window.APIError = APIError;
window.handleAuthFailureResponse = handleAuthFailureResponse;
