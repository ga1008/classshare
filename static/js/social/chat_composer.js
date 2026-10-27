/**
 * Chat composer: shared LQ Composer DOM (createComposer + enhanceComposer
 * with Enter = send, Shift+Enter = newline, IME composition guarded by the
 * shared owner) plus the social intents: emoji, paste/attach (≤ 9), voice
 * recording, a minimal Markdown toolbar, structured @ mentions, per-file
 * progress, quota hints and the LAN-first path for large DM files.
 *
 * Drafts are kept per conversation in memory; a failed send keeps the text,
 * the attachment queue and the same client_nonce, so a retry is idempotent.
 */
import { createComposer, enhanceComposer } from '../lq/composer.js';
import { createEmojiPicker } from '../emoji_picker.js';
import { socialApi, errorText } from './social_api.js';
import { uploadSocialFile, sha256File, checkLimits, mediaKind, fileSpec } from './social_upload.js';
import { sendViaLan, lanSupported, LAN_MIN_BYTES } from './social_lan.js';
import { el, formatSize, lqButton, nonce, notify } from './social_dom.js';

const MAX_ATTACHMENTS = 9;
const BODY_MAX = 4000;
const TYPING_THROTTLE_MS = 3000;
const VOICE_MAX_MS = 120_000;
const MENTION_LIMIT = 8;
const PHASE_LABEL = { wait: '等待上传', hash: '校验中', check: '查重中', upload: '上传中', lan: '局域网直传中', done: '已就绪', error: '失败' };

export function createChatComposer({ client, onSent }) {
    const form = el('form', { class: 'social-composer', novalidate: true, 'aria-label': '发送消息' });
    const reply = el('div', { class: 'social-composer__reply', hidden: true });
    const mention = el('div', { class: 'social-mention', role: 'group', 'aria-label': '选择要 @ 的成员', hidden: true });
    const tray = el('ul', { class: 'social-tray', role: 'list', 'aria-label': '待发送附件', hidden: true });
    const voice = el('div', { class: 'social-voice', role: 'status', hidden: true });
    const notice = el('p', { class: 'social-composer__notice', role: 'alert', hidden: true });
    const mentionHint = el('p', { class: 'social-hint social-composer__mention-hint', hidden: true, 'aria-live': 'polite' });
    const root = createComposer('composer', {
        label: '消息内容', placeholder: '输入消息，Enter 发送，Shift+Enter 换行', enter: 'send', maxlength: BODY_MAX,
        attachment: '添加附件', emoji: '表情', sendLabel: '发送',
    }, { content: [reply, mention, mentionHint, voice, tray, notice] });
    form.append(root);
    const input = root.querySelector('[data-lq-composer-input]');
    const tools = root.querySelector('.lq-composer__tools');
    const fileInput = el('input', { type: 'file', multiple: true, hidden: true, 'aria-hidden': 'true', tabindex: '-1' });
    const imageInput = el('input', { type: 'file', multiple: true, accept: 'image/*', hidden: true, 'aria-hidden': 'true', tabindex: '-1' });
    form.append(fileInput, imageInput);
    const imageButton = lqButton({ icon: 'image', variant: 'ghost', aria: '发送图片', attrs: { 'data-tool': 'image' } });
    const mentionButton = lqButton({ label: '@', variant: 'ghost', aria: '@ 提醒对方', attrs: { 'data-tool': 'mention' } });
    const voiceButton = lqButton({ icon: 'circle', variant: 'ghost', aria: '录音', attrs: { 'data-tool': 'voice', 'aria-pressed': 'false' } });
    const mdTools = [['bold', '加粗', 'bold'], ['italic', '斜体', 'italic'], ['code', '代码', 'code'], ['link', '链接', 'link']]
        .map(([icon, label, tool]) => lqButton({ icon, variant: 'ghost', aria: label, attrs: { 'data-tool': tool } }));
    tools.append(imageButton, voiceButton, mentionButton, el('span', { class: 'social-composer__divider', 'aria-hidden': 'true' }), ...mdTools);
    const emojiAnchor = el('span', { class: 'social-composer__emoji-anchor' });
    form.append(emojiAnchor);
    let owner = null;
    let picker = null;

    const state = {
        ctx: null, queue: [], replyTo: null, busy: false, nonce: null, mentions: new Map(),
        drafts: new Map(), quota: null, mentionQuota: null, composing: false, lastTyping: 0, recorder: null, voiceTimer: 0, abort: null,
    };

    /* ------------------------------------------------------------ helpers */
    const sync = () => {
        owner?.set({ hasContent: state.queue.length > 0, busy: state.busy, disabled: !state.ctx?.writable });
        mentionButton.disabled = state.busy || !state.ctx?.writable;
    };

    function showNotice(text) {
        notice.textContent = text || '';
        notice.hidden = !text;
    }

    function quotaText() {
        const quota = state.quota;
        if (!quota) return '';
        return `今日已上传 ${formatSize(quota.used_today)} / ${formatSize(quota.daily_limit)}，单个文件 ≤ ${formatSize(quota.file_limit)}`;
    }

    function renderTray() {
        tray.replaceChildren(...state.queue.map((item, index) => el('li', { class: 'social-tray__item', 'data-phase': item.phase }, [
            el('span', { class: 'social-tray__name', text: item.file.name }),
            el('small', { class: 'social-tray__meta', text: `${formatSize(item.file.size)} · ${item.error || PHASE_LABEL[item.phase] || ''}${['hash', 'upload', 'lan'].includes(item.phase) ? ` ${Math.round(item.progress * 100)}%` : ''}` }),
            el('progress', { class: 'social-tray__bar', max: '100', value: String(Math.round(item.progress * 100)), 'aria-label': `${item.file.name} 进度` }),
            state.busy ? null : lqButton({ icon: 'x', variant: 'ghost', aria: `移除 ${item.file.name}`, attrs: { 'data-remove': String(index) } }),
        ])));
        if (state.queue.length) tray.append(el('li', { class: 'social-tray__quota', text: quotaText() }));
        tray.hidden = !state.queue.length;
        sync();
    }

    async function loadQuota() {
        try { state.quota = await socialApi.quota(); } catch { state.quota = null; }
    }

    function addFiles(files) {
        const list = [...files];
        if (!list.length) return;
        if (!state.ctx?.writable) return;
        const room = MAX_ATTACHMENTS - state.queue.length;
        if (list.length > room) showNotice(`一条消息最多 ${MAX_ATTACHMENTS} 个附件，已忽略多出的 ${list.length - Math.max(0, room)} 个。`);
        else showNotice('');
        for (const file of list.slice(0, Math.max(0, room))) {
            const problem = checkLimits(file, state.quota);
            if (problem) { showNotice(problem); continue; }
            state.queue.push({ file, phase: 'wait', progress: 0, token: null, sha256: '', lan: false, error: '' });
        }
        renderTray();
        if (!state.quota) void loadQuota().then(renderTray);
    }

    function wrapSelection(before, after = before, fallback = '') {
        const start = input.selectionStart, end = input.selectionEnd;
        const selected = input.value.slice(start, end) || fallback;
        input.setRangeText(`${before}${selected}${after}`, start, end, 'end');
        if (!input.value.slice(start, end)) input.setSelectionRange(start + before.length, start + before.length + selected.length);
        input.focus();
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    /* ------------------------------------------------------------ mentions */
    function mentionQuery() {
        if (!state.ctx?.writable || state.composing) return null;
        const before = input.value.slice(0, input.selectionStart);
        const match = /(^|\s)@([^\s@]{0,20})$/.exec(before);
        return match ? match[2] : null;
    }

    function renderMentions() {
        const query = mentionQuery();
        const selected = collectMentions(input.value).length;
        mentionHint.hidden = query === null && !selected;
        const quota = state.mentionQuota;
        const remaining = quota ? (quota.limit === null ? '当前账号不限次数' : `今天还可 @ ${quota.remaining} 次，每天 ${quota.limit} 次`) : '学生每天 3 次，教师 20 次，管理员不限';
        mentionHint.textContent = `选择 @ 对象后，发送时会邮件提醒并附上几条上下文。${remaining}。`;
        if (query === null) { mention.hidden = true; return; }
        const members = (state.ctx.members || []).filter((member) => member.identity !== client.me);
        const options = members
            .filter((member) => !query || `${member.nickname || ''}${member.name}`.toLowerCase().includes(query.toLowerCase()))
            .slice(0, MENTION_LIMIT)
            .map((member) => ({ label: member.nickname || member.name, identity: member.identity }));
        if (state.ctx.type === 'group' && ['owner', 'admin'].includes(state.ctx.myRole) && (!query || '全体成员'.includes(query))) options.unshift({ label: '全体成员', identity: 'all' });
        mention.replaceChildren(...options.map((option) => lqButton({
            label: `@${option.label}`, variant: 'ghost', size: 'sm',
            attrs: { 'data-mention': option.identity, 'data-mention-label': option.label },
        })));
        mention.hidden = !options.length;
    }

    function pickMention(identity, label) {
        const caret = input.selectionStart;
        const before = input.value.slice(0, caret).replace(/@([^\s@]{0,20})$/, `@${label} `);
        input.value = before + input.value.slice(caret);
        input.setSelectionRange(before.length, before.length);
        state.mentions.set(identity, label);
        mention.hidden = true;
        input.focus();
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function collectMentions(body) {
        return [...state.mentions].filter(([, label]) => {
            const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            return new RegExp(`(^|\\s)@${escaped}(?=\\s|$|[，。！？、])`, 'u').test(body);
        }).map(([identity]) => identity);
    }

    function rememberDraft() {
        if (state.ctx) state.drafts.set(state.ctx.key, { body: input.value, mentions: [...state.mentions] });
    }

    /* ------------------------------------------------------------ voice */
    async function toggleVoice() {
        if (state.recorder) { state.recorder.stop(); return; }
        if (!navigator.mediaDevices?.getUserMedia || typeof window.MediaRecorder !== 'function') {
            showNotice('当前浏览器不支持录音。');
            return;
        }
        let stream;
        try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch {
            showNotice('无法使用麦克风，请在浏览器中允许录音权限。');
            return;
        }
        const chunks = [];
        const recorder = new MediaRecorder(stream);
        const started = Date.now();
        let cancelled = false;
        state.recorder = recorder;
        voiceButton.setAttribute('aria-pressed', 'true');
        const time = el('strong', { text: '0:00' });
        voice.replaceChildren(el('span', { class: 'social-voice__dot', 'aria-hidden': 'true' }), el('span', { text: '录音中 ' }), time,
            lqButton({ label: '完成', variant: 'soft', size: 'sm', onClick: () => recorder.stop() }),
            lqButton({ label: '取消', variant: 'ghost', size: 'sm', onClick: () => { cancelled = true; recorder.stop(); } }));
        voice.hidden = false;
        state.voiceTimer = window.setInterval(() => {
            const seconds = Math.floor((Date.now() - started) / 1000);
            time.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
            if (Date.now() - started >= VOICE_MAX_MS) recorder.stop();
        }, 250);
        recorder.addEventListener('dataavailable', (event) => { if (event.data?.size) chunks.push(event.data); });
        recorder.addEventListener('stop', () => {
            window.clearInterval(state.voiceTimer);
            stream.getTracks().forEach((track) => track.stop());
            state.recorder = null;
            voiceButton.setAttribute('aria-pressed', 'false');
            voice.hidden = true;
            const duration = Date.now() - started;
            if (cancelled || !chunks.length || duration < 800) { if (!cancelled) showNotice('录音太短。'); return; }
            const type = recorder.mimeType || 'audio/webm';
            const extension = type.includes('ogg') ? 'ogg' : (type.includes('mp4') ? 'm4a' : 'webm');
            const stamp = new Date().toLocaleTimeString('zh-CN', { hour12: false }).replace(/:/g, '');
            addFiles([new File(chunks, `语音-${stamp}-${Math.round(duration / 1000)}秒.${extension}`, { type })]);
        });
        recorder.start(1000);
    }

    /* ------------------------------------------------------------ send */
    function lanEligible(item) {
        const ctx = state.ctx;
        return ctx?.type === 'dm' && ctx.peer && item.file.size >= LAN_MIN_BYTES && lanSupported()
            && client.isOnline(ctx.peer) && client.status === 'online';
    }

    function setPhase(item, phase, progress = item.progress) {
        item.phase = phase;
        item.progress = Math.max(0, Math.min(1, progress));
        renderTray();
    }

    async function prepare(item, signal) {
        if (item.token || item.lan) return;
        if (lanEligible(item)) {
            setPhase(item, 'hash', 0);
            item.sha256 = item.sha256 || await sha256File(item.file, { signal, onProgress: (f) => setPhase(item, 'hash', f) });
            const pre = await socialApi.precheck(fileSpec(item.file, item.sha256));
            if (pre?.exists && pre.media_token) { item.token = pre.media_token; setPhase(item, 'done', 1); return; }
            item.lan = true;
            setPhase(item, 'wait', 0);
            return;
        }
        const result = await uploadSocialFile(item.file, {
            signal, sha256: item.sha256,
            onPhase: (phase, fraction = 0) => setPhase(item, phase, fraction),
        });
        item.sha256 = result.sha256;
        item.token = result.media_token;
    }

    async function postMessage(key, payload) {
        const message = await socialApi.send(key, payload);
        if (message.mention_quota) state.mentionQuota = message.mention_quota;
        onSent?.(message);
        return message;
    }

    async function sendLanItem(key, item, signal) {
        setPhase(item, 'lan', 0);
        const outcome = await sendViaLan({
            client, peer: state.ctx.peer, file: item.file, sha256: item.sha256, signal,
            onProgress: (f) => setPhase(item, 'lan', f),
            sendMessage: (spec) => postMessage(key, { body: '', body_format: 'md', lan_media: [spec], client_nonce: nonce() }),
        });
        if (outcome.ok) { setPhase(item, 'done', 1); return; }
        // Direct link failed (timeout, other network, refused): normal server upload.
        item.lan = false;
        const result = await uploadSocialFile(item.file, { signal, sha256: item.sha256, onPhase: (phase, f = 0) => setPhase(item, phase, f) });
        item.token = result.media_token;
        await postMessage(key, { body: '', body_format: 'md', media_tokens: [item.token], client_nonce: nonce() });
        setPhase(item, 'done', 1);
    }

    async function send() {
        const ctx = state.ctx;
        if (!ctx?.writable || state.busy || state.composing) return;
        const body = input.value;
        if (!body.trim() && !state.queue.length) return;
        if (body.length > BODY_MAX) { showNotice(`消息不能超过 ${BODY_MAX} 字。`); return; }
        state.busy = true;
        state.abort = new AbortController();
        state.nonce ||= nonce();
        showNotice('');
        renderTray();
        const key = ctx.key;
        try {
            for (const item of state.queue) {
                try { await prepare(item, state.abort.signal); } catch (error) {
                    item.error = errorText(error, '上传失败');
                    setPhase(item, 'error', item.progress);
                    throw error;
                }
            }
            const tokens = state.queue.filter((item) => item.token && !item.sent).map((item) => item.token);
            if (body.trim() || tokens.length) {
                const payload = { body, body_format: 'md', media_tokens: tokens, client_nonce: state.nonce };
                if (state.replyTo) payload.reply_to_id = state.replyTo.id;
                const mentions = collectMentions(body);
                if (mentions.length) payload.mentions = mentions;
                await postMessage(key, payload);
                state.queue.filter((item) => item.token).forEach((item) => { item.sent = true; });
                input.value = '';
                state.drafts.delete(key);
                setReply(null);
                state.mentions.clear();
            }
            for (const item of state.queue.filter((entry) => entry.lan && !entry.sent)) {
                await sendLanItem(key, item, state.abort.signal);
                item.sent = true;
            }
            state.queue = [];
            state.nonce = null;
            void loadQuota();
        } catch (error) {
            if (error?.name !== 'AbortError') showNotice(errorText(error, '发送失败，请重试。'));
            if (error?.status === 429 && collectMentions(body).length) void socialApi.mentionQuota().then((quota) => { state.mentionQuota = quota; renderMentions(); }).catch(() => {});
            state.queue = state.queue.filter((item) => !item.sent);
        } finally {
            state.busy = false;
            state.abort = null;
            renderTray();
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
        }
    }

    /* ------------------------------------------------------------ reply */
    function setReply(message) {
        state.replyTo = message;
        if (!message) { reply.hidden = true; reply.replaceChildren(); return; }
        reply.replaceChildren(
            el('span', { class: 'social-composer__reply-text', text: `回复 ${message.sender?.name || ''}：${String(message.body || '[附件]').replace(/\s+/g, ' ').slice(0, 60)}` }),
            lqButton({ icon: 'x', variant: 'ghost', aria: '取消回复', onClick: () => setReply(null) }),
        );
        reply.hidden = false;
        input.focus();
    }

    /* ------------------------------------------------------------ events */
    form.addEventListener('submit', (event) => { event.preventDefault(); void send(); });
    input.addEventListener('input', (event) => {
        if (!event.isComposing) renderMentions();
        rememberDraft();
        const now = Date.now();
        if (event.isTrusted && state.ctx && input.value && now - state.lastTyping > TYPING_THROTTLE_MS) {
            state.lastTyping = now;
            client.send({ type: 'typing', conversation_key: state.ctx.key });
        }
    });
    input.addEventListener('compositionstart', () => { state.composing = true; mention.hidden = true; });
    input.addEventListener('compositionend', () => { state.composing = false; renderMentions(); rememberDraft(); });
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !mention.hidden) { event.preventDefault(); event.stopPropagation(); mention.hidden = true; }
    });
    input.addEventListener('paste', (event) => {
        const files = [...(event.clipboardData?.files || [])];
        if (!files.length) return;
        event.preventDefault();
        addFiles(files);
    });
    form.addEventListener('dragover', (event) => { if (event.dataTransfer?.types?.includes('Files')) event.preventDefault(); });
    form.addEventListener('drop', (event) => {
        if (!event.dataTransfer?.files?.length) return;
        event.preventDefault();
        addFiles(event.dataTransfer.files);
    });
    fileInput.addEventListener('change', () => { addFiles(fileInput.files); fileInput.value = ''; });
    imageInput.addEventListener('change', () => { addFiles(imageInput.files); imageInput.value = ''; });
    form.addEventListener('click', (event) => {
        const remove = event.target.closest('[data-remove]');
        if (remove) { state.queue.splice(Number(remove.dataset.remove), 1); renderTray(); showNotice(''); return; }
        const option = event.target.closest('[data-mention]');
        if (option) { pickMention(option.dataset.mention, option.dataset.mentionLabel); return; }
        if (event.target.closest('[data-lq-composer-attachment]')) { fileInput.click(); return; }
        if (event.target.closest('[data-lq-composer-emoji]')) { toggleEmoji(event.target.closest('button')); return; }
        const tool = event.target.closest('[data-tool]')?.dataset.tool;
        if (tool === 'image') imageInput.click();
        else if (tool === 'mention') {
            if (!state.ctx?.writable || state.busy) return;
            const before = input.value.slice(0, input.selectionStart);
            wrapSelection(before && !/\s$/.test(before) ? ' @' : '@', '', '');
        }
        else if (tool === 'voice') void toggleVoice();
        else if (tool === 'bold') wrapSelection('**', '**', '粗体');
        else if (tool === 'italic') wrapSelection('*', '*', '斜体');
        else if (tool === 'code') wrapSelection('`', '`', 'code');
        else if (tool === 'link') wrapSelection('[', '](https://)', '链接文字');
    });

    function toggleEmoji(trigger) {
        if (!picker) {
            picker = createEmojiPicker({ targetInput: input, trigger });
            emojiAnchor.append(picker.element);
            // The picker writes .value directly; tell the composer owner.
            picker.element.addEventListener('click', () => input.dispatchEvent(new Event('input', { bubbles: true })));
            document.addEventListener('pointerdown', (event) => {
                if (picker.isOpen() && !picker.element.contains(event.target) && !event.target.closest?.('[data-lq-composer-emoji]')) picker.close();
            });
        }
        picker.toggle();
    }

    return {
        element: form,
        mount() { owner ||= enhanceComposer(root); sync(); if (!state.quota) void loadQuota(); },
        /** @param {{key: string, type: 'dm'|'group', peer?: string, members?: object[], myRole?: string, writable: boolean, readOnlyReason?: string}} ctx */
        setConversation(ctx) {
            if (state.busy && state.ctx?.key !== ctx.key) state.abort?.abort();
            const switched = state.ctx?.key !== ctx.key;
            const becameWritable = ctx.writable && !state.ctx?.writable;
            if (switched) rememberDraft();
            state.ctx = ctx;
            if (ctx.mentionQuota) state.mentionQuota = ctx.mentionQuota;
            if (switched) {
                const draft = state.drafts.get(ctx.key);
                input.value = draft?.body || '';
                state.queue = [];
                state.nonce = null;
                state.mentions = new Map(draft?.mentions || []);
                setReply(null);
                showNotice('');
                renderTray();
            }
            input.placeholder = ctx.writable ? '输入消息，Enter 发送，Shift+Enter 换行' : (ctx.readOnlyReason || '无法发送消息');
            if (becameWritable) showNotice('');
            if (!ctx.writable) showNotice(ctx.readOnlyReason || '');
            sync();
            input.dispatchEvent(new Event('input', { bubbles: true }));
        },
        setReply,
        focus() { if (state.ctx?.writable) input.focus(); },
        isBusy: () => state.busy,
        mediaKind,
    };
}
