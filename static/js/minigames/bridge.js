import { makeId } from './ui.js';

export function gameBridge({ holder, url, context, definition, call, onFault, onReady }) {
    const entry = new URL(url);
    if (!['http:', 'https:'].includes(entry.protocol) || entry.origin === location.origin)
        throw new Error('游戏内容源未隔离，无法启动');
    const nonce = makeId(),
        frame = document.createElement('iframe'),
        pending = new Map();
    let port = null,
        closed = false,
        loaded = false,
        busy = 0;
    const helloTimer = setTimeout(() => fault('游戏未完成连接，请重新打开'), 15000);
    entry.hash = new URLSearchParams({ ls_nonce: nonce, ls_host: location.origin }).toString();
    frame.title = definition.config.title + ' 游戏画面';
    frame.sandbox = 'allow-scripts';
    frame.referrerPolicy = 'no-referrer';
    frame.setAttribute(
        'allow',
        "camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'"
    );
    function send(message) {
        if (!closed && port) port.postMessage(message);
    }
    function fault(message) {
        if (!closed) {
            destroy();
            onFault(message);
        }
    }
    function destroy() {
        closed = true;
        clearTimeout(helloTimer);
        window.removeEventListener('message', hello);
        frame.removeEventListener('load', loadedFrame);
        port?.close();
        port = null;
        for (const { reject, timer } of pending.values()) {
            clearTimeout(timer);
            reject(new Error('已关闭游戏连接'));
        }
        pending.clear();
        frame.remove();
    }
    async function handleRequest(event) {
        const message = event.data;
        if (
            closed ||
            !message ||
            message.v !== 1 ||
            typeof message.id !== 'string' ||
            message.id.length > 128 ||
            event.ports.length
        )
            return;
        if (pending.has(message.id) && typeof message.ok === 'boolean') {
            const pendingRequest = pending.get(message.id);
            pending.delete(message.id);
            clearTimeout(pendingRequest.timer);
            if (message.ok) pendingRequest.resolve(message.result);
            else pendingRequest.reject(new Error(message.error?.message || '游戏拒绝保存'));
            return;
        }
        if (
            typeof message.method !== 'string' ||
            typeof message.params !== 'object' ||
            message.params === null ||
            Array.isArray(message.params)
        )
            return;
        let size;
        try {
            size = JSON.stringify(message).length;
        } catch {
            return;
        }
        if (size > 48000 || busy >= 12) {
            send({ v: 1, id: message.id, ok: false, error: { code: 'LIMIT', message: '请求超限' } });
            return;
        }
        busy++;
        try {
            const result = await call(message.method, message.params, message.id);
            send({ v: 1, id: message.id, ok: true, result: result ?? null });
        } catch (err) {
            send({
                v: 1,
                id: message.id,
                ok: false,
                error: {
                    code: err.code || 'UNAVAILABLE',
                    message: err.message,
                    retryable: err.status >= 500,
                },
            });
        } finally {
            busy--;
        }
    }
    function hello(e) {
        if (
            closed ||
            port ||
            e.source !== frame.contentWindow ||
            e.origin !== 'null' ||
            e.data?.type !== 'lanshare:hello' ||
            e.data?.protocol !== 1 ||
            e.data?.nonce !== nonce ||
            e.ports.length !== 1
        )
            return;
        port = e.ports[0];
        port.onmessage = handleRequest;
        port.start();
        clearTimeout(helloTimer);
        send({ type: 'welcome', context: context(), definition });
        onReady?.();
    }
    function loadedFrame() {
        if (loaded) {
            fault('游戏页面发生跳转，请重新连接');
            return;
        }
        loaded = true;
        frame.contentWindow.postMessage({ type: 'lanshare:discover' }, '*');
    }
    window.addEventListener('message', hello);
    frame.addEventListener('load', loadedFrame);
    frame.src = entry.href;
    holder.replaceChildren(frame);
    return {
        destroy,
        focus: () => frame.focus({ preventScroll: true }),
        emit: (event, data, seq) => send({ v: 1, event, data, seq }),
        prepareLeave() {
            if (!port) return Promise.reject(new Error('游戏尚未完成连接，稍后再试'));
            const id = 'flush-' + makeId();
            return new Promise((resolve, reject) => {
                const timer = setTimeout(() => {
                    pending.delete(id);
                    reject(new Error('游戏未确认保存，请重试'));
                }, 6000);
                pending.set(id, { resolve, reject, timer });
                send({ v: 1, id, method: 'prepareLeave', params: {} });
            });
        },
    };
}
