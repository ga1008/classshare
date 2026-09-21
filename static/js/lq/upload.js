import { attributesMarkup, escapeHtml, normalizeAttributes } from './html.js';
import { componentMarkup, createComponent } from './components.js';

export const UPLOAD_KINDS = ['upload', 'dropzone', 'file_chip'];
export const FILE_STATES = ['selected', 'validating', 'rejected', 'uploading', 'uploaded', 'failed', 'removing'];
const LABELS = { selected: '已选择，尚未上传', validating: '正在检查', rejected: '未接受', uploading: '正在上传', uploaded: '已保存到服务器', failed: '上传失败', removing: '正在移除' };
const OWNER = Symbol.for('lanshare.lq.upload-owner.v1');
const LIFETIME = Symbol.for('lanshare.lq.upload-lifetime.v1');
function watchUpload(root, controller) {
    const doc = root.ownerDocument;
    let record = doc[LIFETIME];
    if (!record) {
        const owners = new Map();
        const observer = new doc.defaultView.MutationObserver(() => {
            for (const [element, owner] of [...owners]) if (!element.isConnected) owner.destroy();
        });
        record = { owners, observer };
        doc[LIFETIME] = record;
        observer.observe(doc.documentElement, { childList: true, subtree: true });
    }
    record.owners.set(root, controller);
    return () => {
        if (record.owners.get(root) !== controller) return;
        record.owners.delete(root);
        if (!record.owners.size) {
            record.observer.disconnect();
            if (doc[LIFETIME] === record) delete doc[LIFETIME];
        }
    };
}
const mapping = (v, keys) => { if (!v || typeof v !== 'object' || Array.isArray(v) || Object.keys(v).some(k => !keys.includes(k))) throw new TypeError('Invalid LQ upload props'); };
const text = (v, required = false) => { if (typeof v !== 'string' || required && !v.trim()) throw new TypeError('LQ upload requires text'); return v; };
const optionalText = v => text(v === undefined ? '' : v);
const integer = v => { if (!Number.isSafeInteger(v) || v < 0) throw new TypeError('Invalid upload generation'); return v; };
const flag = (v, fallback = false) => { if (v === undefined) return fallback; if (typeof v !== 'boolean') throw new TypeError('Invalid upload flag'); return v; };
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
const action = (id, disabled) => ({ component: 'button', props: { label: id === 'retry' ? '重试' : '移除', variant: 'ghost', size: 'sm', disabled, attrs: { 'data-lq-upload-action': id } } });

export function uploadItem(value) {
    mapping(value, ['id', 'generation', 'name', 'sizeLabel', 'state', 'progress', 'reason', 'questionLabel', 'rejectionCode', 'duplicateOfQuestion', 'confirmation', 'retryable', 'removable']);
    const item = { id: text(value.id, true), generation: integer(value.generation), name: text(value.name, true), sizeLabel: optionalText(value.sizeLabel), state: text(value.state), reason: optionalText(value.reason), questionLabel: optionalText(value.questionLabel), rejectionCode: optionalText(value.rejectionCode), duplicateOfQuestion: optionalText(value.duplicateOfQuestion), retryable: flag(value.retryable, true), removable: flag(value.removable, true) };
    if (!FILE_STATES.includes(item.state)) throw new TypeError('Invalid upload state');
    if (['rejected', 'failed'].includes(item.state) && !item.reason.trim()) throw new TypeError('Rejected/failed file needs a visible reason');
    if (item.rejectionCode && (item.rejectionCode !== 'duplicate-image' || item.state !== 'rejected' || !item.duplicateOfQuestion.trim())) throw new TypeError('Duplicate image needs its owning question');
    if (value.progress !== undefined) { if (item.state !== 'uploading' || typeof value.progress !== 'number' || !Number.isFinite(value.progress) || value.progress < 0 || value.progress > 100) throw new TypeError('Invalid upload progress'); item.progress = value.progress; }
    if (value.confirmation !== undefined) {
        mapping(value.confirmation, ['id']); const id = value.confirmation.id;
        if (!(typeof id === 'string' && id.trim()) && !(Number.isSafeInteger(id) && id >= 0)) throw new TypeError('Invalid server confirmation');
        item.confirmation = { id: String(id) };
    }
    if (item.state === 'uploaded' && !item.confirmation) throw new TypeError('Uploaded requires an explicit server confirmation');
    return item;
}
export function uploadSnapshot(value) {
    mapping(value, ['generation', 'disabled', 'items']);
    const generation = integer(value.generation), disabled = flag(value.disabled);
    if (!Array.isArray(value.items)) throw new TypeError('Upload items must be a list');
    const items = value.items.map(uploadItem), ids = new Set(items.map(i => i.id));
    if (ids.size !== items.length) throw new TypeError('Duplicate upload identity');
    const state = items.some(i => ['validating', 'uploading', 'removing'].includes(i.state)) ? 'busy' : items.some(i => ['rejected', 'failed'].includes(i.state)) ? 'partial-failed' : 'idle';
    return { generation, disabled, items, state };
}
function chip(item, disabled) {
    const blocked = disabled || ['validating', 'uploading', 'removing'].includes(item.state);
    return node('div', { class: 'lq-file-chip', 'data-lq-upload': 'file_chip', 'data-file-id': item.id, 'data-file-generation': String(item.generation), 'data-file-state': item.state, role: 'group', 'aria-label': item.name, tabindex: '-1' }, [
        node('div', { class: 'lq-file-chip__content' }, [
            node('strong', { class: 'lq-file-chip__name' }, [item.name]),
            node('span', { class: 'lq-file-chip__meta' }, [[item.questionLabel, item.sizeLabel].filter(Boolean).join(' · ')]),
            node('span', { class: 'lq-file-chip__state' }, [item.state === 'uploading' && item.progress === 100 ? '上传完成，等待服务器确认' : LABELS[item.state]]),
            ...(item.state === 'uploading' ? [node('progress', { max: '100', ...(item.progress === undefined ? {} : { value: String(item.progress) }), 'aria-label': `${item.name}上传进度` })] : []),
            ...(item.reason ? [node('p', { class: 'lq-file-chip__reason' }, [item.reason])] : []),
            ...(item.duplicateOfQuestion ? [node('p', { class: 'lq-file-chip__owner' }, [`截图归属：${item.duplicateOfQuestion}`])] : []),
        ]),
        node('div', { class: 'lq-file-chip__actions' }, [
            ...(item.state === 'failed' && item.retryable ? [action('retry', blocked)] : []),
            ...(item.removable ? [action('remove', blocked)] : []),
        ]),
    ]);
}
export function uploadProps(kind, props = {}) {
    if (!UPLOAD_KINDS.includes(kind)) throw new TypeError('Invalid upload kind');
    mapping(props, kind === 'file_chip' ? ['item', 'disabled'] : ['id', 'label', 'policy', 'accept', 'multiple', 'name', 'form', 'snapshot', 'attrs']);
    if (kind === 'file_chip') return chip(uploadItem(props.item), flag(props.disabled));
    const id = text(props.id, true); if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) throw new TypeError('Invalid upload id');
    const label = text(props.label, true), policy = text(props.policy, true), accept = optionalText(props.accept);
    const snapshot = uploadSnapshot(props.snapshot === undefined ? { generation: 0, items: [] } : props.snapshot);
    const attrs = normalizeAttributes(props.attrs); for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-') || key.startsWith('aria-') || ['id', 'name', 'form', 'target', 'rel'].includes(key)) delete attrs[key];
    const inputAttrs = { id: `${id}-input`, type: 'file', class: 'lq-dropzone__input', 'aria-describedby': `${id}-policy`, accept, ...(flag(props.multiple, true) ? { multiple: '' } : {}), ...(snapshot.disabled ? { disabled: '' } : {}) };
    for (const k of ['name', 'form']) if (props[k] !== undefined) inputAttrs[k] = text(props[k]);
    return node('div', { ...attrs, id, class: 'lq-upload', 'data-lq-upload': kind, 'data-upload-state': snapshot.state, 'data-upload-generation': String(snapshot.generation), tabindex: '-1' }, [
        node('div', { class: 'lq-dropzone', 'data-lq-upload-dropzone': '' }, [node('label', { class: 'lq-dropzone__label', for: inputAttrs.id }, [label]), node('p', { class: 'lq-dropzone__hint' }, ['选择文件，或在此拖放、粘贴文件']), node('input', inputAttrs)]),
        node('p', { id: `${id}-policy`, class: 'lq-upload__policy' }, [policy]),
        node('p', { class: 'lq-upload__summary', 'data-lq-upload-summary': '' }, [summary(snapshot)]),
        node('div', { class: 'lq-upload__list', 'data-lq-upload-list': '' }, snapshot.items.map(item => chip(item, snapshot.disabled))),
        node('p', { class: 'lq-upload__notice', 'data-lq-upload-notice': '', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' }),
    ]);
}
function summary(snapshot) { return `${snapshot.items.length} 个文件 · ${{ idle: '当前无传输任务', busy: '仍有文件处理中', 'partial-failed': '部分文件未完成，请检查原因' }[snapshot.state]}`; }
function markup(tree) { if (typeof tree === 'string') return escapeHtml(tree); if (tree.component) return componentMarkup(tree.component, tree.props); return `<${tree.tag}${attributesMarkup(tree.attrs)}>${tree.tag === 'input' ? '' : `${tree.children.map(markup).join('')}</${tree.tag}>`}`; }
function element(tree, doc) { if (typeof tree === 'string') return doc.createTextNode(tree); if (tree.component) return createComponent(tree.component, tree.props, doc); const result = doc.createElement(tree.tag); for (const [k, v] of Object.entries(tree.attrs)) result.setAttribute(k, v); for (const child of tree.children) result.append(element(child, doc)); return result; }
export const uploadMarkup = (kind, props) => markup(uploadProps(kind, props));
export const createUpload = (kind, props, doc = document) => element(uploadProps(kind, props), doc);

// Preserve existing action buttons/focus during progress-only snapshots.
function patch(target, source) {
    for (const attr of [...target.attributes]) if (!source.hasAttribute(attr.name)) target.removeAttribute(attr.name);
    for (const attr of source.attributes) target.setAttribute(attr.name, attr.value);
    const before = [...target.childNodes], after = [...source.childNodes];
    after.forEach((next, index) => { const old = before[index]; if (!old) target.append(next); else if (old.nodeType === 3 && next.nodeType === 3) old.textContent = next.textContent; else if (old.nodeType === 1 && next.nodeType === 1 && old.tagName === next.tagName && old.className === next.className && old.getAttribute('data-lq-upload-action') === next.getAttribute('data-lq-upload-action')) patch(old, next); else old.replaceWith(next); });
    for (const old of before.slice(after.length)) old.remove();
}

/** Controlled presentation only. The initial snapshot is required.
 * onFiles(FileList, context), onAction(context). context.files retains a frozen
 * array of File references captured inside the native drop/paste event.
 * Callbacks must check context.isCurrent() after awaiting before operating old files.
 * A resolved promise (including false/veto) never changes file state or removes an item.
 */
export function bindUpload(root, options = {}) {
    if (!root?.isConnected || !['upload', 'dropzone'].includes(root.dataset.lqUpload)) throw new TypeError('Expected a connected LQ upload/dropzone');
    if (root[OWNER]) return root[OWNER];
    for (const key of ['onFiles', 'onAction', 'onError']) if (options[key] !== undefined && typeof options[key] !== 'function') throw new TypeError('Invalid upload callback');
    let snapshot = uploadSnapshot(options.snapshot); // The controller supplies the initial authoritative snapshot too.
    const doc = root.ownerDocument, input = root.querySelector('input[type=file]'), list = root.querySelector('[data-lq-upload-list]'), zone = root.querySelector('[data-lq-upload-dropzone]'), notice = root.querySelector('[data-lq-upload-notice]'), summaryNode = root.querySelector('[data-lq-upload-summary]');
    if (!input || !list || !zone || !notice || !summaryNode) throw new TypeError('Invalid upload structure');
    const listeners = [], pending = new Map();
    let destroyed = false, selectionPending = null, unwatch = () => {};
    const listen = (node, event, callback) => { node.addEventListener(event, callback); listeners.push(() => node.removeEventListener(event, callback)); };
    const usable = () => !destroyed && root.isConnected && !snapshot.disabled && !input.closest('fieldset:disabled');
    function paint() {
        const active = doc.activeElement, focused = root.contains(active);
        root.dataset.uploadState = snapshot.state; root.dataset.uploadGeneration = String(snapshot.generation);
        input.disabled = snapshot.disabled || !!selectionPending; zone.setAttribute('aria-disabled', String(input.disabled));
        summaryNode.textContent = summary(snapshot);
        const old = new Map([...list.children].map(row => [JSON.stringify([row.dataset.fileId, Number(row.dataset.fileGeneration)]), row]));
        for (const [index, item] of snapshot.items.entries()) {
            const key = JSON.stringify([item.id, item.generation]), fresh = element(chip(item, snapshot.disabled || pending.has(key) || !!input.closest('fieldset:disabled')), doc), row = old.get(key);
            if (row) { patch(row, fresh); old.delete(key); if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null); } else list.insertBefore(fresh, list.children[index] || null);
        }
        for (const row of old.values()) row.remove();
        if (focused && !doc.contains(active)) root.focus({ preventScroll: true });
        else if (focused && doc.activeElement !== active && !active.matches(':disabled')) active.focus({ preventScroll: true });
    }
    function fail(error, context) { if (!context.isCurrent()) return; notice.textContent = error instanceof Error && error.message ? error.message : '操作未完成，请重试。'; try { options.onError?.(error, context); } catch { /* Presentation cleanup must survive consumer errors. */ } }
    function intent(action, item) {
        const key = JSON.stringify([item.id, item.generation]); if (!usable() || pending.has(key) || !options.onAction) return;
        const token = {}, context = { action, id: item.id, itemGeneration: item.generation, generation: snapshot.generation, isCurrent: () => usable() && pending.get(key) === token && snapshot.items.some(i => i.id === item.id && i.generation === item.generation) };
        pending.set(key, token); notice.textContent = ''; paint();
        let result; try { result = options.onAction(context); } catch (error) { fail(error, context); }
        Promise.resolve(result).catch(error => fail(error, context)).finally(() => { if (destroyed || pending.get(key) !== token) return; pending.delete(key); paint(); });
    }
    function files(fileList, source, event) {
        if (!usable() || selectionPending || !fileList?.length || !options.onFiles) return;
        const token = {}, generation = snapshot.generation, context = { source, input, event, generation, files: Object.freeze([...fileList]), isCurrent: () => usable() && selectionPending === token && snapshot.generation === generation };
        selectionPending = token; notice.textContent = ''; paint();
        let result; try { result = options.onFiles(fileList, context); } catch (error) { fail(error, context); }
        Promise.resolve(result).catch(error => fail(error, context)).finally(() => { if (destroyed || selectionPending !== token) return; selectionPending = null; paint(); });
    }
    listen(input, 'change', event => files(input.files, 'input', event));
    listen(zone, 'dragover', event => { if ([...(event.dataTransfer?.types || [])].includes('Files')) { event.preventDefault(); if (usable()) zone.dataset.dragover = ''; } });
    listen(zone, 'dragleave', event => { if (!zone.contains(event.relatedTarget)) delete zone.dataset.dragover; });
    listen(zone, 'drop', event => { delete zone.dataset.dragover; if (!event.dataTransfer?.files.length) return; event.preventDefault(); files(event.dataTransfer.files, 'drop', event); });
    listen(zone, 'paste', event => { if (!event.clipboardData?.files.length) return; event.preventDefault(); files(event.clipboardData.files, 'paste', event); });
    listen(list, 'click', event => {
        const button = event.target.closest?.('[data-lq-upload-action]'); if (!button || !list.contains(button) || button.disabled) return;
        const row = button.closest('[data-file-id]'), item = snapshot.items.find(i => i.id === row?.dataset.fileId && i.generation === Number(row?.dataset.fileGeneration));
        if (item) intent(button.dataset.lqUploadAction, item);
    });
    const controller = {
        root, input,
        update(value) {
            if (destroyed) return false;
            const next = uploadSnapshot(value); if (next.generation <= snapshot.generation) return false;
            snapshot = next;
            for (const key of pending.keys()) if (next.disabled || !next.items.some(i => key === JSON.stringify([i.id, i.generation]))) pending.delete(key);
            if (next.disabled) selectionPending = null;
            notice.textContent = ''; paint(); return true;
        },
        destroy() { if (destroyed) return; destroyed = true; unwatch(); pending.clear(); selectionPending = null; paint(); listeners.forEach(remove => remove()); delete zone.dataset.dragover; delete root[OWNER]; },
    };
    root[OWNER] = controller; paint(); if (!destroyed) unwatch = watchUpload(root, controller); return controller;
}
