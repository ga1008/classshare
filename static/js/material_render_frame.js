/* Opaque-origin course documents send bounded navigation/activity evidence. */
(() => {
    if (window.parent === window) return;
    const host = window.location.origin;
    let lastActivity = -Infinity;
    const activity = event => {
        if (!event.isTrusted || performance.now() - lastActivity < 1000) return;
        lastActivity = performance.now();
        // No keys, text, paths or user details leave the sandbox.
        window.parent.postMessage({ type: 'lanshare:material-frame-activity' }, host);
    };
    for (const type of ['pointerdown', 'keydown', 'scroll', 'focusin']) {
        document.addEventListener(type, activity, { capture: true, passive: true });
    }
    const publish = () => {
        window.parent.postMessage({
            type: 'lanshare:material-frame-location',
            path: location.pathname,
            title: document.title,
            article: document.body?.classList.contains('article-page') ?? false,
            slideId: document.querySelector('.slide.active')?.dataset.ldSlideId || '',
            hash: location.hash,
        }, host);
    };
    const quiet = document.createElement('style');
    quiet.textContent = `
        .ld-quiet *, .ld-quiet *::before, .ld-quiet *::after {
            animation-play-state: paused !important;
            transition-duration: 0s !important;
        }
        .ld-quiet .stat { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }
    `;
    document.head.append(quiet);
    window.addEventListener('message', event => {
        if (event.source !== window.parent || event.origin !== host) return;
        if (event.data?.type === 'lanshare:material-frame-ready') publish();
        if (event.data?.type === 'lanshare:material-frame-quiet') {
            document.documentElement.classList.toggle('ld-quiet', event.data.open === true);
        }
    });
    for (const event of ['load', 'hashchange', 'popstate']) {
        window.addEventListener(event, () => requestAnimationFrame(publish));
    }
    publish();
})();
