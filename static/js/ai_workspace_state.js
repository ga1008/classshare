/** Per-account, per-tab state. Chat history stays on the authenticated server. */
export function createWorkspaceState(userKey) {
    const valid = /^(teacher|student):[1-9]\d*$/.test(userKey || '');
    const prefix = 'lanshare.aiWorkspace.v2.';
    let tab = '';
    try {
        tab = sessionStorage.getItem(prefix + 'tab') || crypto.randomUUID();
        sessionStorage.setItem(prefix + 'tab', tab);
    } catch { tab = crypto.randomUUID(); }
    const key = valid ? prefix + userKey : null;
    const draftKey = `${userKey}:${tab}`;
    let meta = {};
    try { if (key) meta = JSON.parse(sessionStorage.getItem(key) || '{}'); } catch { /* Restricted storage. */ }
    if (!meta || typeof meta !== 'object' || Array.isArray(meta)) meta = {};
    const db = valid && window.indexedDB ? new Promise(resolve => {
        const request = indexedDB.open('lanshare-ai-drafts', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('drafts');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => resolve(null);
    }).catch(() => null) : Promise.resolve(null);
    async function transact(mode, action) {
        const database = await db;
        if (!database) return null;
        return new Promise(resolve => {
            try {
                const tx = database.transaction('drafts', mode);
                const request = action(tx.objectStore('drafts'));
                tx.oncomplete = () => resolve(request?.result ?? true);
                tx.onerror = tx.onabort = () => resolve(null);
            } catch { resolve(null); }
        });
    }
    // A different login in the same tab cannot inherit an open window or draft.
    try {
        const previous = sessionStorage.getItem(prefix + 'owner');
        if (previous && previous !== userKey) {
            sessionStorage.removeItem(prefix + previous);
            void transact('readwrite', store => store.delete(`${previous}:${tab}`));
        }
        if (valid) sessionStorage.setItem(prefix + 'owner', userKey);
    } catch { /* No persistence rather than an anonymous shared bucket. */ }
    return {
        key,
        get value() { return meta; },
        patch(update) {
            meta = { ...meta, ...update };
            try { if (key) sessionStorage.setItem(key, JSON.stringify(meta)); } catch { /* Still usable without storage. */ }
        },
        async saveFiles(files) {
            if (!valid) return;
            const safe = files.slice(0, 5);
            if (safe.reduce((n, f) => n + f.size, 0) > 20 * 1024 * 1024) return;
            await transact('readwrite', store => safe.length
                ? store.put({ owner: userKey, time: Date.now(), files: safe }, draftKey)
                : store.delete(draftKey));
        },
        async loadFiles() {
            const saved = await transact('readonly', store => store.get(draftKey));
            if (!saved || saved.owner !== userKey || Date.now() - saved.time > 86400000) {
                await transact('readwrite', store => store.delete(draftKey));
                return [];
            }
            return (saved.files || []).filter(file => file instanceof Blob).map(file =>
                new File([file], file.name || '截图.png', { type: file.type, lastModified: file.lastModified }));
        },
    };
}
