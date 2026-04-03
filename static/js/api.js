/**
 * api.js
 * Centralized API fetch wrapper for consistent error handling and headers.
 */

// Import UI message toast (assumes ui.js is loaded first or bundled)
// import { showToast } from './ui.js';

export class APIError extends Error {
    constructor(message, status, data) {
        super(message);
        this.name = 'APIError';
        this.status = status;
        this.data = data;
    }
}

/**
 * Core fetch wrapper
 * @param {string} endpoint - API endpoint
 * @param {object} options - Fetch options
 * @returns {Promise<any>} JSON response or throws APIError
 */
export async function apiFetch(endpoint, options = {}) {
    const defaultHeaders = {
        'Accept': 'application/json',
    };

    // If body is an object and not FormData, stringify it
    if (options.body && !(options.body instanceof FormData) && typeof options.body === 'object') {
        options.body = JSON.stringify(options.body);
        defaultHeaders['Content-Type'] = 'application/json';
    }

    const config = {
        ...options,
        headers: {
            ...defaultHeaders,
            ...options.headers,
        },
    };

    try {
        const response = await fetch(endpoint, config);

        // Try to parse JSON response, fallback to text if not JSON
        let data;
        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
            data = await response.json();
        } else {
            data = await response.text();
        }

        if (!response.ok) {
            // Extract meaningful error message
            let errorMessage = '未知错误发生';
            if (data && typeof data === 'object' && data.detail) {
                errorMessage = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
            } else if (typeof data === 'string' && data.length > 0) {
                const trimmed = data.trim();
                const looksLikeHtml = /<!doctype html>|<html[\s>]/i.test(trimmed);
                if (looksLikeHtml) {
                    const titleMatch = trimmed.match(/<title[^>]*>(.*?)<\/title>/i);
                    errorMessage = titleMatch ? `服务异常：${titleMatch[1].trim()}` : '服务异常，请稍后重试';
                } else {
                    errorMessage = trimmed.length > 180 ? `${trimmed.slice(0, 180)}...` : trimmed;
                }
            } else if (response.statusText) {
                errorMessage = response.statusText;
            }

            throw new APIError(errorMessage, response.status, data);
        }

        return data;
    } catch (error) {
        if (error instanceof APIError) {
            console.error(`[API Error ${error.status}] ${endpoint}:`, error.message);
            // Optionally auto-toast errors if window.UI exists
            if (window.UI && window.UI.showToast && !options.silent) {
                window.UI.showToast(`操作失败: ${error.message}`, 'error');
            }
            throw error;
        }

        // Network errors or parsing errors
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
    delete: (url, options = {}) => apiFetch(url, { ...options, method: 'DELETE' }),
};

// Make available globally for inline scripts compatibility
window.API = API;
window.apiFetch = apiFetch;
// Alias used by manage pages and some inline scripts
window.apiRequest = apiFetch;
