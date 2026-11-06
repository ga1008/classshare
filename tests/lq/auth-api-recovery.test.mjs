import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const shared = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../../static/js/lq/toast.js', () => ({ toast: shared.toast }));

const payload = (redirect_to = '/student/login?next=%2Fdashboard') => ({
    detail: '登录已过期', code: 'login_required', error: { code: 'login_required' }, redirect_to,
});
const json = (status, data, headers = {}) => new Response(JSON.stringify(data), {
    status, headers: { 'content-type': 'application/json', ...headers },
});
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

let win, transport, api, notices;
beforeEach(async () => {
    vi.resetModules();
    notices = [];
    shared.toast.mockReset().mockImplementation(() => {
        const handle = { destroy: vi.fn() }; notices.push(handle); return handle;
    });
    transport = vi.fn();
    win = Object.assign(new EventTarget(), {
        location: { origin: 'https://auth.test', href: 'https://auth.test/dashboard', assign: vi.fn() },
        open: vi.fn(), fetch: transport, UI: { showToast: vi.fn() },
    });
    class FakeXhr extends EventTarget {
        static DONE = 4;
        open() {}
        send() {
            this.readyState = FakeXhr.DONE;
            this.dispatchEvent(new Event('readystatechange'));
        }
        getResponseHeader() { return 'application/json'; }
    }
    vi.stubGlobal('window', win);
    vi.stubGlobal('XMLHttpRequest', FakeXhr);
    await import('../../static/js/auth.js');
    vi.stubGlobal('fetch', win.fetch);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    api = await import('../../static/js/api.js');
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('LQ auth failure settles raw fetch and legacy API without replacing the draft page', async () => {
    transport.mockImplementation(async () => json(401, payload()));
    const response = await win.fetch('/api/submit', { method: 'POST', body: 'draft' });
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual(payload());
    await expect(api.apiFetch('/api/submit', { method: 'POST', body: { text: 'draft' } })).rejects.toMatchObject({
        status: 401, code: 'login_required', suppressToast: true,
    });
    await vi.waitFor(() => expect(shared.toast).toHaveBeenCalledTimes(1));
    expect(win.location.assign).not.toHaveBeenCalled();
    expect(win.location.href).toBe('https://auth.test/dashboard');
    expect(transport).toHaveBeenCalledTimes(2);
    const [message, options] = shared.toast.mock.calls[0];
    expect(message).toContain('回到此页重试');
    expect(options.duration).toBe(0);
    expect(win.open).not.toHaveBeenCalled();
    options.action.onClick();
    expect(win.open).toHaveBeenCalledWith('https://auth.test/student/login?next=%2Fdashboard', '_blank', 'noopener,noreferrer');
});

it('LQ concurrent fetch and XHR failures share one notice and leave callbacks usable', async () => {
    transport.mockImplementation(async () => json(401, payload()));
    const responses = await Promise.all(Array.from({ length: 8 }, () => win.fetch('/api/summary')));
    expect(responses.map(response => response.status)).toEqual(Array(8).fill(401));
    const xhr = new XMLHttpRequest();
    const ready = vi.fn(); xhr.addEventListener('readystatechange', ready);
    xhr.open('GET', '/api/upload-status');
    xhr.status = 401; xhr.responseType = 'json'; xhr.response = payload(); xhr.send();
    await vi.waitFor(() => expect(shared.toast).toHaveBeenCalledTimes(1));
    expect(ready).toHaveBeenCalledOnce();
    expect(win.location.href).toBe('https://auth.test/dashboard');
});

it('LQ resource 403 and overload remain local and never replay a POST', async () => {
    for (const status of [403, 429, 503, 504]) {
        transport.mockResolvedValueOnce(json(status, {
            detail: 'temporary failure', error: { code: status === 403 ? 'permission_denied' : 'service_unavailable' },
            redirect_to: '/student/login',
        }, { 'Retry-After': '3' }));
        await expect(api.apiFetch('/api/save', { method: 'POST', body: { text: 'draft' } })).rejects.toMatchObject({
            status, retryAfterSeconds: 3,
        });
    }
    expect(transport).toHaveBeenCalledTimes(4);
    expect(shared.toast).not.toHaveBeenCalled();
    expect(win.location.href).toBe('https://auth.test/dashboard');
});

it('LQ stale 401 after a newer successful request cannot raise a login notice', async () => {
    const delayed = deferred();
    transport.mockImplementationOnce(() => delayed.promise).mockResolvedValueOnce(json(200, { ok: true }));
    const old = win.fetch('/api/save');
    await win.fetch(new URL('https://auth.test/api/current-session'));
    delayed.resolve(json(401, payload()));
    expect((await old).status).toBe(401);
    expect(shared.toast).not.toHaveBeenCalled();
    expect(win.location.href).toBe('https://auth.test/dashboard');
});

it('LQ an older success cannot dismiss a newer failure but a renewed session does', async () => {
    const delayed = deferred();
    transport.mockImplementationOnce(() => delayed.promise).mockResolvedValueOnce(json(401, payload()));
    const old = win.fetch('/api/older');
    await win.fetch('/api/current');
    await vi.waitFor(() => expect(notices).toHaveLength(1));
    delayed.resolve(json(200, { ok: true }));
    await old;
    expect(notices[0].destroy).not.toHaveBeenCalled();
    transport.mockResolvedValueOnce(json(200, { ok: true }));
    await win.fetch('/api/recovered');
    expect(notices[0].destroy).toHaveBeenCalledOnce();
});

it('LQ only typed same-origin API login failures offer safe browser login targets', async () => {
    for (const [path, status, data] of [
        ['https://provider.test/api/task', 401, payload()],
        ['/apiary/failure', 401, payload()],
        ['/api/internal-provider', 401, { redirect_to: '/student/login', error: { code: 'upstream_error' } }],
        ['/api/invalid', 401, payload('https://outside.test/student/login')],
        ['/api/invalid', 401, payload('/api/delete-all')],
        ['/api/forbidden', 403, payload()],
    ]) {
        transport.mockResolvedValueOnce(json(status, data));
        expect((await win.fetch(path)).status).toBe(status);
    }
    expect(shared.toast).not.toHaveBeenCalled();
    expect(win.open).not.toHaveBeenCalled();
});

it('LQ draft owner can handle auth recovery without adding a second overlay', async () => {
    const recover = vi.fn(event => event.preventDefault());
    win.addEventListener('lanshare:auth-required', recover);
    transport.mockResolvedValueOnce(json(401, payload()));
    expect((await win.fetch('/api/save')).status).toBe(401);
    expect(recover).toHaveBeenCalledOnce();
    expect(recover.mock.calls[0][0].detail.loginUrl).toBe('https://auth.test/student/login?next=%2Fdashboard');
    expect(shared.toast).not.toHaveBeenCalled();
});
