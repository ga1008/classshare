/* Synchronous compatibility queue. Components are installed by the ESM entry. */
(() => {
    const key = Symbol.for('lanshare.lq.runtime.v1');
    if (!document[key]) {
        let resolve;
        const loaded = new Promise(done => { resolve = done; });
        const api = { ready(callback) {
            if (callback !== undefined && typeof callback !== 'function') throw new TypeError('LQ.ready expects a function');
            return loaded.then(() => callback ? callback(api) : api);
        } };
        document[key] = { api, resolve, installed: false };
    }
    window.LQ = document[key].api;
})();
