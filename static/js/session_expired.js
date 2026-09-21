const OWNER = Symbol.for('lanshare.sessionRecovery');

export function initSessionRecovery(doc = document, win = window) {
    if (doc[OWNER]) return doc[OWNER];
    const link = doc.querySelector('[data-lq-session-login]');
    const note = doc.querySelector('[data-session-countdown]');
    const count = doc.getElementById('countdown');
    if (!link || !note || !count || note.dataset.autoRedirect !== 'true') return null;
    const target = new URL(link.href, win.location.href);
    if (target.origin !== win.location.origin || !['/student/login', '/teacher/login'].includes(target.pathname)) return null;

    let timer = null;
    let stopped = false;
    const links = [link, ...doc.querySelectorAll('[data-session-alternate-login]')];
    const deadline = win.Date.now() + 5000;
    const destroy = () => {
        if (stopped) return;
        stopped = true;
        win.clearTimeout(timer);
        timer = null;
        note.hidden = true;
        links.forEach(item => item.removeEventListener('click', destroy));
        win.removeEventListener('pagehide', destroy);
        if (doc[OWNER] === destroy) delete doc[OWNER];
    };
    doc[OWNER] = destroy;
    links.forEach(item => item.addEventListener('click', destroy));
    win.addEventListener('pagehide', destroy);
    const tick = () => {
        timer = null;
        if (stopped) return;
        const remaining = Math.max(0, deadline - win.Date.now());
        count.textContent = String(Math.ceil(remaining / 1000));
        if (!remaining) {
            destroy();
            win.location.assign(target.href);
        } else timer = win.setTimeout(tick, Math.min(1000, remaining));
    };
    tick();
    note.hidden = false;
    return destroy;
}

if (typeof document !== 'undefined') initSessionRecovery();
