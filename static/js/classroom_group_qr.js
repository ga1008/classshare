import { apiFetch } from './api.js';

const trigger = document.querySelector('[data-group-qr-open]');
const dialog = document.getElementById('classroom-group-qr-dialog');

if (trigger && dialog) {
    // Keep the top-layer dialog outside the hero's animated grid and transforms.
    document.body.append(dialog);
    const find = key => dialog.querySelector(`[data-group-qr-${key}]`);
    const endpoint = trigger.dataset.endpoint;
    const thumbnail = trigger.querySelector('[data-group-qr-thumbnail]');
    const placeholder = trigger.querySelector('[data-group-qr-placeholder]');
    const hint = trigger.querySelector('[data-group-qr-hint]');
    const preview = find('preview');
    const empty = find('empty');
    const emptyText = find('empty-text');
    const form = find('form');
    const description = form?.querySelector('[name="description"]');
    const fileInput = find('file');
    const save = find('save');
    const status = dialog.querySelector('[role="status"]');
    const discardPanel = find('discard-panel');
    const conflictPanel = find('conflict-panel');
    let record = JSON.parse(document.getElementById('classroom-group-qr-data').textContent);
    let objectUrl = '';
    let pendingFile = null;
    let removeImage = false;
    let loading = false;
    let saving = false;
    let decoding = false;
    let loaded = false;
    let decodeGeneration = 0;
    let controller;
    let conflictRecord = null;

    const normalizeDescription = value => String(value || '').replace(/\r\n?/g, '\n').trim();
    function dirty() {
        return Boolean(form && (pendingFile || removeImage || normalizeDescription(description.value) !== normalizeDescription(record.description)));
    }
    function setStatus(message = '', error = false) {
        status.textContent = message;
        status.dataset.error = String(error);
    }
    function setControls() {
        dialog.setAttribute('aria-busy', String(loading || saving));
        find('retry').disabled = loading || saving;
        if (!form) return;
        const disabled = loading || saving || !loaded;
        description.disabled = disabled;
        for (const key of ['upload', 'file', 'remove', 'undo-image']) find(key).disabled = disabled || decoding;
        save.disabled = disabled || decoding || !dirty() || Boolean(conflictRecord);
        save.textContent = saving ? '保存中…' : '保存修改';
        find('count').textContent = `${description.value.length} / 1000`;
        dialog.querySelectorAll('[data-group-qr-close]').forEach(button => { button.disabled = saving; });
    }
    function releaseDraftImage() {
        decodeGeneration += 1;
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = '';
        pendingFile = null;
        removeImage = false;
        decoding = false;
        if (fileInput) fileInput.value = '';
    }
    function showImage(element, url) {
        element.hidden = !url;
        if (url) {
            if (element.getAttribute('src') !== url) element.src = url;
        } else element.removeAttribute('src');
    }
    function renderCard() {
        showImage(thumbnail, record.image_url);
        placeholder.hidden = Boolean(record.image_url);
        hint.textContent = record.image_url ? '点击放大' : (form ? '点击设置' : '暂未设置');
    }
    function renderImage() {
        const url = removeImage ? '' : (objectUrl || record.image_url);
        showImage(preview, url);
        empty.hidden = Boolean(url);
        emptyText.textContent = removeImage ? '保存后将移除二维码，班群简介会保留' : (form ? '添加班群二维码' : '教师尚未设置班群二维码');
        find('scan').hidden = !url;
        find('scan-help').hidden = !url || Boolean(objectUrl);
        const download = find('download');
        download.hidden = !record.image_url || Boolean(objectUrl) || removeImage || !loaded;
        if (!download.hidden) download.href = `${endpoint}/image?download=true`;
        else download.removeAttribute('href');
        if (form) {
            find('upload').textContent = url ? '更换二维码' : '上传二维码';
            find('remove').hidden = !url;
            find('undo-image').hidden = !pendingFile && !removeImage;
            find('undo-image').textContent = record.image_url ? '恢复原图' : '撤销选图';
        }
    }
    function renderRecord() {
        renderCard();
        renderImage();
        if (description) description.value = record.description;
        if (find('description')) find('description').textContent = record.description || '暂无班群简介';
        setControls();
    }
    function hideGuards() {
        if (discardPanel) discardPanel.hidden = true;
        if (conflictPanel) conflictPanel.hidden = true;
        conflictRecord = null;
    }

    async function loadRecord() {
        controller?.abort();
        const request = new AbortController();
        controller = request;
        loading = true;
        loaded = false;
        find('retry').hidden = true;
        // Do not invite scanning an old QR while its latest state is unknown.
        showImage(preview, '');
        empty.hidden = false;
        emptyText.textContent = '正在读取班群信息…';
        find('scan').hidden = true;
        find('scan-help').hidden = true;
        if (find('description')) find('description').textContent = '';
        setStatus();
        setControls();
        const timeout = setTimeout(() => request.abort('timeout'), 20000);
        try {
            const result = await apiFetch(endpoint, { signal: request.signal, silent: true });
            if (controller !== request || !dialog.open || request.signal.aborted) return;
            record = result;
            loaded = true;
            hideGuards();
            releaseDraftImage();
            renderRecord();
            setStatus();
        } catch (error) {
            if (controller === request && dialog.open) {
                setStatus(request.signal.reason === 'timeout' ? '读取超时，请重试。' : (error.message || '读取失败，请重试。'), true);
                emptyText.textContent = '暂时无法读取班群信息';
                find('retry').hidden = false;
            }
        } finally {
            clearTimeout(timeout);
            if (controller === request) { loading = false; setControls(); }
        }
    }
    trigger.addEventListener('click', () => {
        if (dialog.open) return;
        hideGuards();
        releaseDraftImage();
        if (description) description.value = record.description;
        dialog.showModal();
        loadRecord();
    });
    find('retry').addEventListener('click', loadRecord);

    function close() {
        if (saving) return;
        if (dirty()) {
            discardPanel.hidden = false;
            discardPanel.scrollIntoView({ block: 'nearest' });
            find('keep').focus();
        } else dialog.close();
    }
    dialog.querySelectorAll('[data-group-qr-close]').forEach(button => button.addEventListener('click', close));
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    // Existing classroom overlays also listen on document; consume this dialog's Escape.
    dialog.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    });
    let backdropDown = false;
    function outside(event) {
        const rect = dialog.getBoundingClientRect();
        return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
    }
    dialog.addEventListener('pointerdown', event => { backdropDown = outside(event); });
    dialog.addEventListener('click', event => { if (backdropDown && outside(event)) close(); backdropDown = false; });
    find('keep')?.addEventListener('click', () => { discardPanel.hidden = true; description.focus(); });
    find('discard')?.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        controller?.abort();
        controller = null;
        releaseDraftImage();
        if (description) description.value = record.description;
        trigger.focus({ preventScroll: true });
    });
    window.addEventListener('beforeunload', event => {
        if (dialog.open && dirty()) { event.preventDefault(); event.returnValue = ''; }
    });
    description?.addEventListener('input', () => {
        discardPanel.hidden = true;
        if (!conflictRecord) setStatus(dirty() ? '修改尚未保存' : '');
        setControls();
    });
    find('upload')?.addEventListener('click', () => fileInput.click());
    find('remove')?.addEventListener('click', () => {
        releaseDraftImage();
        removeImage = Boolean(record.image_url);
        renderImage();
        setControls();
        setStatus(removeImage ? '二维码待移除，保存后生效。' : '已撤销选图');
    });
    find('undo-image')?.addEventListener('click', () => {
        releaseDraftImage();
        renderImage();
        setControls();
        setStatus(record.image_url ? '已恢复原图' : '已撤销选图');
    });
    fileInput?.addEventListener('change', async () => {
        const file = fileInput.files[0];
        if (!file) return;
        if (!file.size || file.size > 5 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
            fileInput.value = '';
            setStatus('请选择不超过 5 MB 的 PNG、JPG 或 WebP 图片。', true);
            return;
        }
        const candidateUrl = URL.createObjectURL(file);
        const generation = ++decodeGeneration;
        const candidate = new Image();
        candidate.src = candidateUrl;
        decoding = true;
        setControls();
        try {
            await candidate.decode();
            if (generation !== decodeGeneration || !dialog.open) return;
            if (candidate.naturalWidth * candidate.naturalHeight > 12000000) throw new Error('图片分辨率过大，请使用不超过 1200 万像素的图片。');
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            objectUrl = candidateUrl;
            pendingFile = file;
            removeImage = false;
            renderImage();
            setStatus('新图片待保存');
        } catch (error) {
            if (generation === decodeGeneration) {
                fileInput.value = '';
                setStatus(error.name === 'EncodingError' ? '图片无法读取，请重新选择。' : (error.message || '图片无法读取，请重新选择。'), true);
            }
        } finally {
            if (objectUrl !== candidateUrl) URL.revokeObjectURL(candidateUrl);
            if (generation === decodeGeneration) { decoding = false; setControls(); }
        }
    });

    async function loadConflict() {
        try {
            conflictRecord = await apiFetch(endpoint, { silent: true, signal: AbortSignal.timeout(20000) });
            find('conflict-description').textContent = `${conflictRecord.image_url ? '已设置二维码' : '未设置二维码'}\n${conflictRecord.description || '暂无班群简介'}`;
            conflictPanel.hidden = false;
        } catch {
            setStatus('班群信息已更新，暂时无法读取最新设置。草稿已保留，请再次保存以重试。', true);
        }
    }
    find('use-latest')?.addEventListener('click', () => {
        record = conflictRecord;
        hideGuards();
        releaseDraftImage();
        renderRecord();
        setStatus('已采用最新设置');
    });
    find('keep-draft')?.addEventListener('click', () => {
        // Untouched fields use the latest record; edited fields require an
        // explicit Save after the teacher resolves the conflicting version.
        const draftDescription = description.value;
        const changedDescription = normalizeDescription(draftDescription) !== normalizeDescription(record.description);
        record = conflictRecord;
        hideGuards();
        renderCard();
        renderImage();
        description.value = changedDescription ? draftDescription : record.description;
        setControls();
        setStatus('已保留你的修改，请确认后保存。');
    });
    form?.addEventListener('submit', async event => {
        event.preventDefault();
        if (!loaded || loading || saving || decoding || !dirty() || conflictRecord) return;
        const body = new FormData();
        body.append('description', description.value);
        body.append('revision', record.revision);
        body.append('remove_image', String(removeImage));
        if (pendingFile) body.append('file', pendingFile);
        saving = true;
        discardPanel.hidden = true;
        setControls();
        setStatus('正在保存…');
        const saveSignal = AbortSignal.timeout(30000);
        try {
            record = await apiFetch(endpoint, { method: 'POST', body, silent: true, signal: saveSignal });
            releaseDraftImage();
            renderRecord();
            setStatus('班群信息已保存');
        } catch (error) {
            setStatus(saveSignal.aborted ? '保存响应超时，修改已保留，请重试确认。' : (error.message || '保存失败，请重试。'), true);
            if (error.status === 409) await loadConflict();
        } finally { saving = false; setControls(); }
    });
    function thumbnailError() {
        thumbnail.hidden = true;
        placeholder.hidden = false;
        hint.textContent = form ? '图片不可用 · 点击更新' : '图片暂不可用';
    }
    thumbnail.addEventListener('error', thumbnailError);
    if (thumbnail.getAttribute('src') && thumbnail.complete && !thumbnail.naturalWidth) thumbnailError();
    preview.addEventListener('error', () => {
        preview.hidden = true;
        empty.hidden = false;
        emptyText.textContent = form ? '图片无法显示，可更换或移除后重新设置' : '图片暂不可用，请联系教师更新';
        find('scan').hidden = true;
        find('scan-help').hidden = true;
    });
}
