export class APIError extends Error {
    constructor(message, status, data) {
        super(message);
        this.name = 'APIError';
        this.status = status;
        this.data = data;
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

        let data;
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            data = await response.json();
        } else {
            data = await response.text();
        }

        if (!response.ok) {
            let errorMessage = '未知错误发生';

            if (data && typeof data === 'object' && data.detail) {
                errorMessage = normalizeErrorMessage(
                    typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
                );
            } else if (typeof data === 'string' && data.length > 0) {
                const trimmed = data.trim();
                const looksLikeHtml = /<!doctype html>|<html[\s>]/i.test(trimmed);

                if (looksLikeHtml) {
                    const titleMatch = trimmed.match(/<title[^>]*>(.*?)<\/title>/i);
                    errorMessage = titleMatch
                        ? normalizeErrorMessage(`服务异常：${titleMatch[1].trim()}`)
                        : '服务异常，请稍后重试';
                } else {
                    errorMessage = normalizeErrorMessage(trimmed);
                }
            } else if (response.statusText) {
                errorMessage = normalizeErrorMessage(response.statusText);
            }

            throw new APIError(errorMessage, response.status, data);
        }

        return data;
    } catch (error) {
        if (error instanceof APIError) {
            console.error(`[API Error ${error.status}] ${endpoint}:`, error.message);
            if (window.UI && window.UI.showToast && !options.silent) {
                window.UI.showToast(`操作失败：${normalizeErrorMessage(error.message)}`, 'error');
            }
            throw error;
        }

        console.error(`[Network Error] ${endpoint}:`, error);
        if (window.UI && window.UI.showToast && !options.silent) {
            window.UI.showToast('网络连接异常，请检查您的网络设置', 'error');
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
