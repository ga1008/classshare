import { clone, equal, uid } from './model.js';

// Three-way merge applies canonical fields only where the user did not edit
// after submission. Stable-ID arrays preserve concurrent edits and ordering.
export function mergeCanonical(base, server, local) {
    if (equal(base, local)) return clone(server);
    if (equal(base, server) || local === undefined) return clone(local);
    if (Array.isArray(base) && Array.isArray(server) && Array.isArray(local)) {
        if ([...base, ...server, ...local].every((x) => x && typeof x === 'object' && typeof x.id === 'string')) {
            const bm = new Map(base.map((x) => [x.id, x])), sm = new Map(server.map((x) => [x.id, x]));
            const merged = local.filter((x) => sm.has(x.id) || !bm.has(x.id) || !equal(x, bm.get(x.id)))
                .map((x) => mergeCanonical(bm.get(x.id), sm.get(x.id), x));
            const ids = new Set(local.map((x) => x.id));
            for (const x of server) if (!ids.has(x.id) && !bm.has(x.id)) merged.push(clone(x));
            return merged;
        }
        return clone(local);
    }
    if ([base, server, local].every((x) => x && typeof x === 'object' && !Array.isArray(x))) {
        const result = {};
        for (const key of new Set([...Object.keys(server), ...Object.keys(local)])) {
            const value = mergeCanonical(base[key], server[key], local[key]);
            if (value !== undefined) result[key] = value;
        }
        return result;
    }
    return clone(local);
}

export class EditorStore {
    constructor(document, revision, { maxCommands = 100, maxHistoryBytes = 24 * 1024 * 1024 } = {}) {
        this.model = clone(document); this.saved = clone(document); this.revision = revision;
        this.undoStack = []; this.redoStack = []; this.listeners = new Set(); this.serial = 0;
        this.pending = null; this.retry = null; this.blocked = null;
        this.maxCommands = maxCommands; this.maxHistoryBytes = maxHistoryBytes;
        this.ui = { slide: 0, selection: [], groupPath: [], trial: false };
    }
    get dirty() { return !equal(this.model, this.saved); }
    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    emit(type, details = {}) { for (const fn of this.listeners) fn({ type, ...details }); }
    trim() {
        let bytes = this.undoStack.reduce((n, x) => n + x.bytes, 0) + this.redoStack.reduce((n, x) => n + x.bytes, 0);
        while (this.undoStack.length && (this.undoStack.length > this.maxCommands || bytes > this.maxHistoryBytes)) bytes -= this.undoStack.shift().bytes;
    }
    command(label, mutate, { coalesce = '', now = Date.now() } = {}) {
        const before = clone(this.model), next = clone(this.model);
        mutate(next);
        if (equal(next, before)) return false;
        const last = this.undoStack.at(-1);
        if (coalesce && last?.coalesce === coalesce && now - last.time < 700 && !this.pending) {
            last.after = clone(next); last.time = now; last.bytes = (JSON.stringify(last.before).length + JSON.stringify(next).length) * 2;
        } else this.undoStack.push({ label, before, after: clone(next), time: now, coalesce, bytes: (JSON.stringify(before).length + JSON.stringify(next).length) * 2 });
        this.model = next; this.serial++; this.redoStack = []; this.trim();
        if (this.blocked?.kind === 'invalid') this.blocked = null;
        this.emit('document', { label }); return true;
    }
    travel(from, to) {
        const item = from.pop(); if (!item) return;
        to.push(item); this.model = clone(to === this.redoStack ? item.before : item.after); this.serial++;
        if (this.blocked?.kind === 'invalid') this.blocked = null;
        this.emit('document', { label: item.label });
    }
    undo() { this.travel(this.undoStack, this.redoStack); }
    redo() { this.travel(this.redoStack, this.undoStack); }
    select(ids) { this.ui.selection = [...new Set(ids)]; this.emit('selection'); }
    page(index) { this.ui.slide = index; this.ui.selection = []; this.ui.groupPath = []; this.emit('page'); }
    beginSave({ restoreId } = {}) {
        if (this.pending || this.blocked || (!this.dirty && !this.retry)) return null;
        this.pending = this.retry || { document: clone(this.model), revision: this.revision, operation_id: uid('op'), serial: this.serial, restoreId };
        this.retry = null; this.emit('save'); return clone(this.pending);
    }
    savedResponse(result) {
        const attempt = this.pending; if (!attempt) throw new Error('没有等待中的保存');
        const canonical = result.document;
        this.model = mergeCanonical(attempt.document, canonical, this.model);
        for (const stack of [this.undoStack, this.redoStack]) for (const item of stack) {
            item.before = mergeCanonical(attempt.document, canonical, item.before);
            item.after = mergeCanonical(attempt.document, canonical, item.after);
            item.coalesce = ''; item.bytes = (JSON.stringify(item.before).length + JSON.stringify(item.after).length) * 2;
        }
        this.saved = clone(canonical); this.revision = result.revision; this.pending = null; this.retry = null; this.blocked = null;
        this.trim(); this.emit('saved', { result });
    }
    saveFailed(error) {
        if (error.status === 409) this.blocked = { kind: 'conflict', error };
        else if ([400, 403, 404, 410, 413, 422, 428].includes(error.status)) this.blocked = { kind: 'invalid', error };
        else this.retry = this.pending; // Response may have been lost after commit. Retry the same operation.
        this.pending = null; this.emit('save-error', { error });
    }
    adoptServer(result, { keepLocal = false } = {}) {
        if (this.pending) throw new Error('请等待当前保存结束');
        const prior = clone(this.model);
        this.saved = clone(result.document); this.revision = result.revision;
        this.model = keepLocal ? prior : clone(result.document);
        this.undoStack = []; this.redoStack = []; this.retry = null; this.blocked = null; this.serial++;
        this.emit('document', { label: keepLocal ? '在新版本上保留本地修改' : '载入服务器版本' });
    }
}
