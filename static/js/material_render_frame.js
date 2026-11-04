/* Course documents run in an opaque origin. Send only reader navigation state. */
(() => {
    if (window.parent === window) return;
    const host = window.location.origin;
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
