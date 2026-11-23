import {
    api,
    bindIconTooltips,
    bootstrap,
    button,
    choiceField,
    closePanel,
    confirmAction,
    el,
    field,
    formPanel,
    makeId,
    notice,
    onAction,
    panel,
    run,
    setButtonIcon,
    setCsrfToken,
    showControl,
    statusChip,
    svgIcon,
} from './ui.js';
import { gameBridge } from './bridge.js';

const { game, previewVersion } = bootstrap(),
    gamePath = '/games/' + encodeURIComponent(game.id);
const hostKey = 'minigame:instance:' + game.id;
let hostInstance = sessionStorage.getItem(hostKey) || makeId(),
    releaseInstance;

async function identifyTab() {
    if (navigator.locks) {
        const claim = () =>
            new Promise((resolve, reject) => {
                navigator.locks
                    .request(hostKey + ':' + hostInstance, { ifAvailable: true }, (lock) => {
                        resolve(!!lock);
                        if (lock) return new Promise((done) => (releaseInstance = done));
                    })
                    .catch(reject);
            });
        if (!(await claim())) {
            hostInstance = makeId();
            await claim();
        }
    } else if (typeof BroadcastChannel === 'function') {
        const channel = new BroadcastChannel(hostKey),
            marker = makeId();
        let claimed = false,
            occupied = false;
        channel.onmessage = ({ data }) => {
            if (data?.instance !== hostInstance || data.marker === marker) return;
            if (data.type === 'probe' && (claimed || marker < data.marker))
                channel.postMessage({ type: 'owner', instance: hostInstance, marker });
            if (data.type === 'owner') occupied = true;
        };
        channel.postMessage({ type: 'probe', instance: hostInstance, marker });
        await new Promise((resolve) => setTimeout(resolve, 120));
        if (occupied) hostInstance = makeId();
        claimed = true;
        releaseInstance = () => channel.close();
    } else {
        hostInstance = makeId();
    }
    sessionStorage.setItem(hostKey, hostInstance);
}

const holder = document.querySelector('[data-stage-frame]'),
    stage = document.querySelector('[data-stage-panel]'),
    emblem = document.querySelector('[data-stage-emblem]'),
    modeDeck = document.querySelector('[data-stage-modes]');
const title = document.querySelector('[data-stage-title]'),
    message = document.querySelector('[data-stage-message]'),
    countWrap = document.querySelector('[data-countdown-wrap]'),
    countRing = document.querySelector('[data-countdown-ring]'),
    count = document.querySelector('[data-countdown]');
const flags = { preview: !!previewVersion };
let lease = null,
    bridge = null,
    snap = null,
    prefs = {},
    progress = null,
    versionDef = null,
    foreign = null;
let pollTimer,
    leaseTimer,
    clockTimer,
    saveClearTimer,
    syncRequest,
    mutating = false,
    suspended = false,
    stopped = false,
    lastClock = 0,
    nowAt = 0,
    lastAnnouncement = '';
let messages = [],
    roomPanel = null,
    chatSeen = null,
    chatMessages = null;
const live = document.querySelector('[data-wait-members]');

const terminal = (phase) =>
    [
        'finished',
        'completed',
        'closed',
        'ended',
        'interrupted',
        'canceled',
        'cancelled',
        'expired',
        'paused',
    ].includes(phase);
const permission = (cap) => versionDef?.config?.capabilities?.includes(cap);
const idpath = (segment, id) => '/' + segment + '/' + encodeURIComponent(id);
const gameMode = () => versionDef?.config?.modes || game.config?.modes || {};

function focusStage() {
    if (bridge && stage.hidden && !document.querySelector('[data-lq-layer-open], dialog[open]')) {
        bridge.focus();
    }
}

function setEmblem(iconName, tone = 'primary', pulse = false) {
    if (!emblem) return;
    emblem.dataset.tone = tone;
    emblem.toggleAttribute('data-pulse', pulse);
    emblem.replaceChildren(svgIcon(iconName, 'game-emblem-svg'));
}

function renderStageModes(show) {
    if (!modeDeck) return;
    modeDeck.replaceChildren();
    modeDeck.hidden = !show;
    if (!show) return;
    const multi = gameMode().multiplayer;
    if (!multi) return;
    const cards = [];
    if (multi.privateRooms) {
        const range =
            multi.minPlayers === multi.maxPlayers
                ? multi.minPlayers + ' 人'
                : multi.minPlayers + '–' + multi.maxPlayers + ' 人';
        cards.push(['create', 'plus', '创建队伍', range], ['join', 'key', '加入队伍', '4 位邀请码']);
    }
    if (multi.randomSizes?.length) {
        cards.push(['random', 'radar', '随机匹配', multi.randomSizes.join('/') + ' 人局']);
    }
    for (const [action, icon, label, hint] of cards) {
        const card = el('button', null, 'lq-surface lq-domain-choice game-mode-card');
        card.type = 'button';
        card.dataset.lqComponent = 'choice';
        card.dataset.lqShape = 'surface';
        card.dataset.action = action;
        card.setAttribute('aria-label', label);
        const iconBox = el('span', null, 'game-mode-card__icon');
        iconBox.setAttribute('aria-hidden', 'true');
        iconBox.append(svgIcon(icon));
        card.append(iconBox, el('strong', label), el('small', hint));
        modeDeck.append(card);
    }
}

function availableControls() {
    const modes = gameMode(),
        multi = modes.multiplayer,
        active = snap && !terminal(snap.phase),
        running = snap?.phase === 'running';
    const other = document.querySelector('[data-foreign-game]');
    other.hidden = !foreign;
    if (foreign) other.href = '/games/' + encodeURIComponent(foreign.gameId);
    const grouping =
        !flags.preview && !stopped && !foreign && (!active || (snap.mode === 'solo' && !snap.readOnly));
    const stageLauncher = grouping && !active && !modes.solo;
    renderStageModes(stageLauncher);
    showControl('create', grouping && !stageLauncher && multi?.privateRooms);
    showControl('join', grouping && !stageLauncher && multi?.privateRooms);
    showControl('teams', grouping && multi?.privateRooms);
    showControl('random', grouping && !stageLauncher && !!multi?.randomSizes?.length);
    showControl('history', !flags.preview && !stopped);
    showControl(
        'chapters',
        !flags.preview &&
            !snap?.readOnly &&
            modes.solo &&
            (!active || snap.mode === 'solo') &&
            progress?.rank > 0
    );
    showControl('restart', !flags.preview && !stopped && !!snap && terminal(snap.phase) && !!modes.solo);
    showControl('room', !!snap?.roomId && !terminal(snap.phase));
    showControl('rotate', snap?.isOwner && !snap.readOnly && snap.phase === 'waiting');
    showControl('leave', !!active && !running && !snap.readOnly);
    showControl('takeover', !!active && snap.readOnly);
    showControl('retry', !bridge && (!active || running || stopped));
    const muteBtn = document.querySelector('[data-action="mute"]');
    if (muteBtn) {
        const muted = !!prefs.audioMuted;
        muteBtn.setAttribute('aria-pressed', String(muted));
        muteBtn.toggleAttribute('data-muted', muted);
        setButtonIcon(muteBtn, muted ? 'mute' : 'volume');
    }
}

function announce(text) {
    if (text !== lastAnnouncement) {
        lastAnnouncement = text;
        notice(text);
    }
}

function context() {
    return {
        protocol: 1,
        environment: flags.preview ? 'preview' : 'platform',
        me: snap?.me || lease?.me || { displayName: '预览玩家', playerId: null, seat: null },
        mode: snap?.mode || 'solo',
        capabilities: versionDef?.config?.capabilities || [],
        roomId: snap?.roomId || null,
        runId: snap?.runId || null,
        audio: { muted: !!prefs.audioMuted },
    };
}

function projection() {
    if (!snap) return { phase: 'waiting', members: [], ...context() };
    const {
        phase,
        mode,
        roomId,
        runId,
        me,
        members,
        seq,
        revision,
        startsAt,
        serverNow,
        turnSeat,
        publicState,
        startRank,
        outcome,
        targetSize,
    } = snap;
    return {
        phase,
        mode,
        roomId,
        runId,
        me,
        seat: me?.seat ?? null,
        members: members || [],
        seq,
        revision,
        startsAt,
        serverNow,
        turnSeat,
        publicState,
        state: publicState,
        startRank,
        outcome,
        targetSize,
    };
}

const readPrivate = async () => {
    const query = snap?.runId ? '?' + new URLSearchParams({ runId: snap.runId }) : '';
    progress = await api('/me/progress/' + encodeURIComponent(game.id) + query);
    return progress;
};

function writable() {
    if (flags.preview || stopped || suspended || !snap?.runId || snap.readOnly || snap.phase !== 'running')
        throw new Error('当前游戏不可操作');
}

const control = () => ({ hostInstance, controlEpoch: snap?.controlEpoch });

async function sdkCall(method, p, id) {
    if (!bridge || flags.preview) throw new Error('当前连接未开放此操作');
    switch (method) {
        case 'session.snapshot':
            return projection();
        case 'room.members':
            return snap?.members || [];
        case 'progress.read':
            return readPrivate();
        case 'history.list': {
            const query = new URLSearchParams({
                gameId: game.id,
                limit: String(Math.max(1, Math.min(20, Number(p.limit) || 10))),
            });
            if (typeof p.cursor === 'string' && p.cursor.length < 128) query.set('cursor', p.cursor);
            return api('/me/history?' + query);
        }
        case 'chat.open':
            if (!snap?.roomId || !permission('room.chat')) return { opened: false };
            await openRoom();
            return { opened: true };
        case 'game.intent': {
            writable();
            const intent = p.action || p.intent || { type: p.type, payload: p.payload };
            const r = await api(idpath('runs', snap.runId) + '/intents', {
                method: 'POST',
                key: p.requestId || id,
                body: { ...control(), intent, expectedRevision: p.expectedRevision },
            });
            if (r.phase) apply(r);
            else schedule(0);
            return { runId: r.runId || snap.runId, revision: r.revision, throughSeq: r.throughSeq ?? r.seq };
        }
        case 'progress.writePrivate': {
            writable();
            if (!permission('save.private')) throw new Error('未启用个人存档');
            saveStatus('syncing', '同步中');
            try {
                const r = await api('/me/progress/' + encodeURIComponent(game.id) + '/private', {
                    method: 'PUT',
                    key: p.requestId || id,
                    body: {
                        ...control(),
                        runId: snap.runId,
                        expectedRevision: p.expectedRevision,
                        privateSave: p.privateSave ?? p.values,
                    },
                });
                progress = r;
                saveStatus('saved', '已存');
                bridge?.emit('progress.changed', { privateRevision: r.privateRevision });
                return r;
            } catch (e) {
                saveStatus('error', e.status === 409 ? '存档冲突' : '未保存');
                throw e;
            }
        }
        case 'game.finish': {
            writable();
            const r = await api(idpath('runs', snap.runId) + '/finish', {
                method: 'POST',
                key: p.requestId || id,
                body: control(),
            });
            if (r.phase) apply(r);
            else schedule(0);
            return { runId: r.runId || snap.runId, revision: r.revision, throughSeq: r.throughSeq ?? r.seq };
        }
        default:
            throw new Error('未开放的游戏能力');
    }
}

function saveStatus(state, text) {
    const node = document.querySelector('[data-save-status]');
    if (!node) return;
    clearTimeout(saveClearTimer);
    if (!text) {
        node.hidden = true;
        node.textContent = '';
        delete node.dataset.saveState;
        return;
    }
    node.hidden = false;
    node.dataset.saveState = state;
    node.textContent = text;
    if (state === 'saved') {
        saveClearTimer = setTimeout(() => {
            node.hidden = true;
        }, 2200);
    }
}

function fitStage() {
    const display = versionDef?.config?.display || {},
        ratio = display.aspectRatio || 'free',
        tone = display.stageTone || 'auto';
    holder.dataset.tone = tone;
    if (tone === 'dark' || tone === 'light') stage.dataset.lqTone = tone;
    else delete stage.dataset.lqTone;
    if (['16:9', '4:3', '1:1', '3:4', '9:16'].includes(ratio)) {
        const [w, h] = ratio.split(':').map(Number);
        holder.dataset.ratio = ratio;
        holder.style.setProperty('--game-ratio', String(w / h));
    } else delete holder.dataset.ratio;
}

function mount() {
    if (bridge || !lease || !versionDef || stopped) return;
    bridge = gameBridge({
        holder,
        url: lease.contentEntryUrl,
        definition: versionDef,
        context,
        call: sdkCall,
        onReady() {
            stage.hidden = flags.preview || (snap?.phase === 'running' && !snap?.readOnly);
            showControl('retry', false);
            focusStage();
        },
        onFault(text) {
            bridge = null;
            stage.hidden = false;
            setEmblem('shield', 'danger', false);
            title.textContent = '连接中断';
            message.textContent = text;
            showControl('retry', true);
        },
    });
}

function disposeFrame() {
    const old = bridge;
    bridge = null;
    old?.destroy();
    holder.replaceChildren();
}

function stop(text) {
    stopped = true;
    suspended = true;
    syncRequest?.abort();
    clearTimeout(pollTimer);
    clearTimeout(leaseTimer);
    clearTimeout(clockTimer);
    disposeFrame();
    roomPanel?.destroy();
    roomPanel = null;
    stage.hidden = false;
    setEmblem('pause', 'warning', false);
    title.textContent = '已停止';
    message.textContent = text;
    if (countWrap) countWrap.hidden = true;
    count.hidden = true;
    live.replaceChildren();
    document.querySelector('[data-room-code]').hidden = true;
    availableControls();
    notice(text);
}

function remaining() {
    return Math.max(0, Math.ceil(((snap.startsAt || 0) - lastClock - (performance.now() - nowAt)) / 1000));
}

function clockTick() {
    clearTimeout(clockTimer);
    if (!snap || snap.phase !== 'countdown' || stopped) return;
    const rawMs = Math.max(0, (snap.startsAt || 0) - lastClock - (performance.now() - nowAt));
    const sec = Math.max(0, Math.ceil(rawMs / 1000));
    count.textContent = sec > 0 ? String(sec) : '0';
    if (countRing) {
        const progressRatio = Math.min(1, Math.max(0, rawMs / 5000));
        countRing.style.setProperty('--ring-offset', String((1 - progressRatio) * 100));
    }
    if (rawMs > 0) clockTimer = setTimeout(clockTick, 120);
}

function chapter(rank) {
    return versionDef?.writeback?.milestones?.[rank]?.label || '第 ' + (Number(rank) + 1) + ' 章';
}

function renderSeats(snapshot) {
    const members = snapshot.members || [];
    const total = Math.max(members.length, Number(snapshot.targetSize) || 0);
    if (!members.length && (!total || terminal(snapshot.phase))) {
        live.replaceChildren();
        return;
    }
    const nodes = members.map((m, idx) => {
        const li = el('li', null, 'game-seat');
        const reconnecting = m.status === 'reconnecting';
        li.dataset.seatState = reconnecting ? 'reconnecting' : 'online';
        const dot = el('i', null, 'game-seat__dot');
        dot.setAttribute('aria-hidden', 'true');
        const badge = el('b', String(m.seat || idx + 1), 'game-seat__num');
        li.append(badge, el('span', m.displayName), dot);
        return li;
    });
    if (snapshot.phase === 'waiting' || snapshot.phase === 'countdown') {
        for (let i = members.length; i < Math.min(total, 8); i++) {
            const slot = el('li', null, 'game-seat game-seat--empty');
            slot.setAttribute('aria-hidden', 'true');
            slot.append(el('b', String(i + 1), 'game-seat__num'), el('span', '空席'));
            nodes.push(slot);
        }
    }
    live.replaceChildren(...nodes);
}

function apply(snapshot) {
    if (!snapshot?.phase || stopped) return;
    const previous = snap;
    const wasHidden = stage.hidden;
    snap = snapshot;
    lastClock = snapshot.serverNow || Date.now();
    nowAt = performance.now();
    if (previous?.roomId !== snapshot.roomId) {
        messages = [];
        chatSeen = null;
        roomPanel?.destroy();
        roomPanel = null;
        renderChat();
    }
    if (Array.isArray(snapshot.messages)) {
        messages = snapshot.messages;
        renderChat();
    }
    clearTimeout(clockTimer);
    const inCountdown = snapshot.phase === 'countdown';
    if (countWrap) countWrap.hidden = !inCountdown;
    count.hidden = !inCountdown;
    document.querySelector('[data-room-code]').hidden = !snapshot.code || snapshot.phase !== 'waiting';
    document.querySelector('[data-code-text]').textContent = snapshot.code || '';
    renderSeats(snapshot);

    const wasNew =
        !previous ||
        snapshot.phase !== previous.phase ||
        snapshot.generation !== previous.generation ||
        snapshot.roomId !== previous.roomId;

    if (snapshot.readOnly && !terminal(snapshot.phase)) {
        stage.hidden = false;
        setEmblem('bolt', 'warning', false);
        title.textContent = '游戏已在另一页面打开';
        message.textContent = '';
    } else if (snapshot.phase === 'running') {
        setEmblem('gamepad', 'primary', true);
        title.textContent = game.title;
        message.textContent = '';
        stage.hidden = !!bridge;
        if (lease) mount();
        if (stage.hidden && !wasHidden) focusStage();
        if (wasNew) announce('');
    } else if (snapshot.phase === 'countdown') {
        stage.hidden = false;
        setEmblem('coop', 'success', true);
        title.textContent = '满员开局';
        message.textContent =
            chapter(snapshot.startRank ?? 0) +
            (snapshot.ownRank < snapshot.startRank ? ' · 进度同步' : '');
        clockTick();
        if (wasNew) announce('');
    } else if (snapshot.phase === 'reconnecting') {
        stage.hidden = false;
        setEmblem('refresh', 'warning', true);
        title.textContent = '等待队友重连';
        message.textContent = '席位保留中';
    } else if (terminal(snapshot.phase)) {
        stage.hidden = false;
        const winner = snapshot.publicState?.winnerSeat;
        const won = snapshot.outcome === 'actorWins' || snapshot.outcome === 'completed';
        setEmblem(won ? 'trophy' : 'compass', won ? 'success' : 'neutral', false);
        title.textContent = snapshot.phase === 'finished' ? '本局结束' : '对局已结束';
        message.textContent =
            snapshot.outcome === 'actorWins' && winner
                ? '第 ' + winner + ' 席获胜。'
                : snapshot.outcome === 'draw'
                  ? '平局'
                  : snapshot.outcome === 'completed'
                    ? '已通关'
                    : '';
        if (wasNew) {
            bridge?.emit('session.ended', { outcome: snapshot.outcome });
            const endedFrame = bridge;
            setTimeout(() => {
                if (bridge === endedFrame && terminal(snap?.phase)) disposeFrame();
            }, 750);
            readPrivate()
                .then(availableControls)
                .catch(() => {});
        }
    } else {
        stage.hidden = false;
        const isRandom = snapshot.mode === 'random';
        setEmblem(isRandom ? 'radar' : 'key', isRandom ? 'info' : 'primary', isRandom);
        title.textContent = isRandom ? '正在匹配队友' : '等待队友加入';
        message.textContent = snapshot.targetSize ? snapshot.targetSize + ' 人局' : '';
    }
    message.hidden = !message.textContent;
    if (wasNew) bridge?.emit('session.changed', context(), snapshot.seq);
    if (previous?.revision !== snapshot.revision) bridge?.emit('game.changed', projection(), snapshot.seq);
    if (previous?.seq !== snapshot.seq)
        bridge?.emit('room.changed', { members: snapshot.members || [] }, snapshot.seq);
    availableControls();
}

function subject() {
    if (!snap) return {};
    if (snap.roomId) return { roomId: snap.roomId };
    if (snap.runId) return { runId: snap.runId };
    return { ticketId: snap.ticketId };
}

function schedule(ms) {
    clearTimeout(pollTimer);
    if (!stopped && !flags.preview && snap && !terminal(snap.phase)) pollTimer = setTimeout(sync, ms);
}

async function pollSnapshot(previous, args, signal) {
    const randomRoom = previous.mode === 'random' && previous.phase === 'countdown' && !!previous.roomId;
    try {
        const result = previous.readOnly
            ? await api('/sync?' + new URLSearchParams(args), { signal })
            : await api('/sync', { method: 'POST', body: args, signal });
        if (!randomRoom || result.snapshot?.outcome !== 'queue_changed') return result;
    } catch (e) {
        if (!randomRoom || ![404, 409].includes(e.status)) throw e;
    }
    const a = await api('/me/activity?' + new URLSearchParams({ gameId: game.id, hostInstance }), { signal });
    if (a.multiplayer?.gameId === game.id) return { reconciled: true, snapshot: a.multiplayer };
    await api(gamePath, { signal });
    return { released: true, foreign: a.multiplayer };
}

async function sync() {
    if (stopped || flags.preview || mutating || syncRequest || !snap || terminal(snap.phase)) return;
    const controller = new AbortController();
    syncRequest = controller;
    const previous = snap;
    const waiting = snap.phase === 'waiting' || snap.phase === 'countdown';
    let delay = document.hidden
        ? 4500
        : snap.mode === 'solo'
          ? 2600
          : waiting
            ? 850
            : 1700 + Math.floor(Math.random() * 201);
    try {
        const args = {
            ...subject(),
            ...control(),
            after: snap.seq || 0,
            waitMs: snap.phase === 'waiting' || snap.phase === 'countdown' ? 1000 : 0,
        };
        const r = await pollSnapshot(previous, args, controller.signal);
        if (controller !== syncRequest || mutating || previous !== snap) return;
        if (r.released) {
            disposeFrame();
            snap = null;
            foreign = r.foreign;
            chooseMode();
            announce('已退出匹配');
            return;
        }
        if (r.reconciled) {
            await enterResult(r.snapshot);
            if (r.snapshot.phase === 'waiting' && !r.snapshot.readOnly) announce('队友离开，继续匹配');
            return;
        }
        if (r.snapshot) apply(r.snapshot);
        if (Array.isArray(r.messages)) {
            messages = r.messages;
            renderChat();
        }
        for (const ev of r.events || []) {
            const entry = ev.payload?.message || ev.message;
            if (entry && !messages.some((m) => m.messageId === entry.messageId)) messages.push(entry);
        }
        renderChat();
    } catch (e) {
        if (e.name === 'AbortError') return;
        if ([401, 403, 404, 410].includes(e.status)) return stop('访问已结束或游戏已下架');
        if (e.status === 409 && previous === snap) {
            const result = await api(
                '/sync?' + new URLSearchParams({ ...subject(), hostInstance, after: 0 }),
                { signal: controller.signal }
            ).catch(() => null);
            if (controller === syncRequest && result?.snapshot) apply(result.snapshot);
        }
        notice(e.status === 409 ? '状态已更新' : '重连中…');
        delay = 3500;
    } finally {
        if (syncRequest === controller) {
            syncRequest = null;
            schedule(delay);
        }
    }
}

async function freshLease(current = false) {
    const body = { hostInstance, purpose: flags.preview ? 'preview' : 'play' };
    if (previewVersion) body.versionId = previewVersion;
    if (!previewVersion && snap?.versionId) {
        body.versionId = snap.versionId;
        if (!current && snap.runId && snap.versionId !== game.version_id) body.runId = snap.runId;
    }
    lease = await api(gamePath + '/launches', { method: 'POST', body });
    if (lease?.csrfToken) setCsrfToken(lease.csrfToken);
    versionDef = lease.definition;
    fitStage();
    renewLater();
}

async function enterResult(result) {
    if (result.versionId && result.versionId !== lease?.versionId) {
        disposeFrame();
        snap = result;
        try {
            await freshLease(true);
            game.version_id = result.versionId;
            await readPrivate();
        } catch (e) {
            stop('版本已更新，请重新打开。' + e.message);
            return;
        }
    }
    apply(result);
}

function renewLater(delay) {
    clearTimeout(leaseTimer);
    const bound = lease;
    if (!bound || stopped) return;
    const ms = Math.max(1500, (Number(bound.expiresAt) || Date.now() + 90000) - Date.now() - 30000);
    leaseTimer = setTimeout(async () => {
        try {
            const r = await api(idpath('launches', bound.leaseId) + '/renew', {
                method: 'POST',
                body: { hostInstance },
            });
            if (bound === lease && !stopped) {
                bound.expiresAt = r.expiresAt;
                renewLater();
            }
        } catch (e) {
            if (bound !== lease || stopped) return;
            if ((!e.status || e.status >= 500) && Date.now() < bound.expiresAt) {
                renewLater(Math.max(100, Math.min(3500, bound.expiresAt - Date.now())));
                return;
            }
            stop('授权未能续期：' + e.message);
        }
    }, delay ?? ms);
}

async function initial() {
    stopped = false;
    suspended = false;
    notice('');
    stage.hidden = false;
    setEmblem('gamepad', 'primary', true);
    title.textContent = game.title;
    message.textContent = '';
    message.hidden = true;
    prefs = await api('/me/preferences');
    if (flags.preview) {
        await freshLease();
        mount();
        availableControls();
        return;
    }
    const q = new URLSearchParams({ gameId: game.id, hostInstance });
    const a = await api('/me/activity?' + q);
    foreign = a.multiplayer?.gameId !== game.id ? a.multiplayer : null;
    snap = (a.multiplayer?.gameId === game.id ? a.multiplayer : a.solo) || null;
    await freshLease();
    await readPrivate();
    if (snap) {
        apply(snap);
        schedule(0);
        return;
    }
    if (gameMode().solo) {
        const s = await command(gamePath + '/runs', { mode: 'solo' });
        await enterResult(s);
        schedule(0);
        return;
    }
    if (
        !foreign &&
        !gameMode().multiplayer?.privateRooms &&
        gameMode().multiplayer?.randomSizes?.length === 1
    ) {
        await selectMode('random', gameMode().multiplayer.randomSizes[0]);
        return;
    }
    chooseMode();
}

function chooseMode() {
    clearTimeout(clockTimer);
    if (countWrap) countWrap.hidden = true;
    count.hidden = true;
    stage.hidden = false;
    live.replaceChildren();
    document.querySelector('[data-room-code]').hidden = true;
    roomPanel?.destroy();
    roomPanel = null;
    setEmblem(foreign ? 'bolt' : 'gamepad', foreign ? 'warning' : 'primary', false);
    title.textContent = game.title;
    message.textContent = foreign ? '已在另一款联机游戏中' : '';
    message.hidden = !message.textContent;
    availableControls();
    showControl('retry', false);
}

function command(path, body, method = 'POST', key) {
    return api(path, { method, key, body: { ...control(), ...body } });
}

function stopSync() {
    if (syncRequest) {
        syncRequest.abort();
        syncRequest = null;
    }
    clearTimeout(pollTimer);
}

async function selectMode(kind, value, extras = {}) {
    if (mutating) throw new Error('正在切换，请稍候');
    mutating = true;
    stopSync();
    let fromSolo,
        prepared = false;
    try {
        if (snap?.mode === 'solo' && snap.phase === 'running') {
            if (snap.readOnly) throw new Error('请先接管控制权');
            if (bridge) {
                prepared = true;
                const b = await bridge.prepareLeave();
                fromSolo = { runId: snap.runId, controlEpoch: snap.controlEpoch };
                if (b?.privateSave != null) {
                    fromSolo.pendingPrivateSave = b.privateSave;
                    fromSolo.expectedPrivateRevision =
                        b.expectedPrivateRevision ?? b.expectedRevision ?? progress?.privateRevision;
                }
            } else fromSolo = { runId: snap.runId, controlEpoch: snap.controlEpoch };
        }
        suspended = true;
        const route = kind === 'random' ? '/queue' : kind === 'join' ? '/rooms/join' : '/rooms';
        const body = { ...extras, ...(kind === 'join' ? { code: value } : { targetSize: Number(value) }) };
        if (fromSolo) body.fromSolo = fromSolo;
        let result;
        try {
            result = await command(gamePath + route, body);
        } catch (err) {
            if (!err.status) {
                const a = await api('/me/activity?' + new URLSearchParams({ gameId: game.id, hostInstance }));
                if (a.multiplayer?.gameId === game.id && !a.multiplayer.readOnly) result = a.multiplayer;
            }
            if (!result) throw err;
        }
        disposeFrame();
        snap = null;
        await enterResult(result);
    } catch (e) {
        if (prepared) bridge?.emit('session.resumeInput', {});
        throw e;
    } finally {
        mutating = false;
        suspended = false;
        schedule(0);
    }
}

async function teamForm(kind, template) {
    const m = gameMode().multiplayer,
        body = el('div', null, 'game-form');
    if (kind === 'join') {
        const f = field('四位队伍码', 'code');
        const input = f.querySelector('input');
        input.required = true;
        input.maxLength = 8;
        input.placeholder = 'M7K3';
        input.classList.add('game-code-input');
        input.autocapitalize = 'characters';
        input.autocomplete = 'off';
        input.spellcheck = false;
        body.append(f);
    } else {
        const sizes =
            kind === 'random'
                ? m.randomSizes
                : Array.from({ length: m.maxPlayers - m.minPlayers + 1 }, (_, i) => m.minPlayers + i);
        body.append(
            choiceField(
                '队伍人数',
                'size',
                sizes.map((n) => [n, n + ' 人']),
                template?.targetSize || sizes[0]
            )
        );
        if (kind === 'create') {
            body.append(field('队伍名称（选填）', 'teamName', 'text', template?.teamName || ''));
            if (m.kind === 'coop' && progress?.rank > 0)
                body.append(
                    choiceField('起始章节', 'rank', [
                        ['', '跟随最高进度'],
                        ...Array.from({ length: progress.rank + 1 }, (_, i) => [i, chapter(i)]),
                    ])
                );
        }
    }
    return formPanel(
        kind === 'join' ? '加入队伍' : kind === 'random' ? '随机匹配' : '创建队伍',
        body,
        kind === 'join' ? '加入' : kind === 'random' ? '开始匹配' : '创建',
        async (data) => {
            if (kind === 'join') {
                const code = String(data.get('code')).replace(/[\s-]/g, '').toUpperCase();
                if (!/^(?=.*[A-Z])(?=.*[2-9])[A-HJKMNP-Z2-9]{4}$/.test(code))
                    throw new Error('请输入 4 位有效邀请码（不含 0/1/O/I/L）');
                return selectMode(kind, code);
            }
            const extras = {};
            if (template) extras.templateId = template.templateId;
            if (data.get('teamName')) extras.teamName = String(data.get('teamName')).trim();
            if (data.get('rank') != null && data.get('rank') !== '')
                extras.startRank = Number(data.get('rank'));
            return selectMode(kind, data.get('size'), extras);
        }
    );
}

async function templates() {
    const r = await api('/me/teams?gameId=' + encodeURIComponent(game.id)),
        body = el('div', null, 'game-form');
    let dialog;
    for (const t of r.items.filter((t) => t.gameId === game.id)) {
        const row = el('div', null, 'lq-row game-template-row');
        const names = (t.knownMembers || [])
            .map((x) => (typeof x === 'string' ? x : x.displayName))
            .filter(Boolean);
        const info = el('div', null, 'game-row-main');
        info.append(
            el(
                'strong',
                [t.teamName || '历史队伍', t.targetSize + ' 人'].join(' · ')
            )
        );
        const sub = [
            names.join('、'),
            t.lastUsedAt ? new Date(t.lastUsedAt).toLocaleDateString() : '',
        ]
            .filter(Boolean)
            .join(' · ');
        if (sub) info.append(el('small', sub, 'game-muted'));
        const actions = el('div', null, 'game-actions');
        actions.append(
            button(
                '按此配置新建',
                async () => {
                    await closePanel(dialog);
                    await teamForm('create', t);
                },
                'soft',
                { icon: 'plus' }
            ),
            button(
                '移除记录',
                async () => {
                    await api('/me/teams/' + encodeURIComponent(t.templateId), { method: 'DELETE', body: {} });
                    row.remove();
                },
                'ghost',
                { icon: 'trash', iconOnly: true, ariaLabel: '移除记录' }
            )
        );
        row.append(info, actions);
        body.append(row);
    }
    if (!body.childElementCount) body.append(el('p', '暂无历史队伍', 'game-muted'));
    dialog = await panel('历史队伍', body);
}

async function historyPanel() {
    const body = el('div', null, 'game-form'),
        list = el('ul', null, 'lq-list game-history-list'),
        modeIcons = { solo: 'solo', private: 'coop', random: 'radar' };
    const outcomeSpec = {
        completed: ['通关', 'success'],
        actorWins: ['分出胜负', 'success'],
        draw: ['平局', 'info'],
        finished: ['完成', 'success'],
        running: ['进行中', 'info'],
        interrupted: ['中断', 'warning'],
        ended: ['结束', 'neutral'],
        reconnecting: ['重连中', 'warning'],
        paused: ['暂停', 'neutral'],
    };
    let cursor;
    async function loadMore() {
        const q = new URLSearchParams({ gameId: game.id, limit: 10 });
        if (cursor) q.set('cursor', cursor);
        const page = await api('/me/history?' + q);
        for (const item of page.items) {
            const row = el('li', null, 'lq-row game-history-row');
            const iconWrap = el('span', null, 'game-row-icon');
            iconWrap.append(svgIcon(modeIcons[item.mode] || 'gamepad', 'game-mini-icon'));
            const main = el('div', null, 'game-row-main');
            main.append(
                el('strong', chapter(item.rank || 0)),
                el('small', new Date(item.startedAt).toLocaleString(), 'game-muted')
            );
            const [label, tone] = outcomeSpec[item.outcome] || outcomeSpec[item.phase] || ['结束', 'neutral'];
            row.append(iconWrap, main, statusChip(label, tone));
            list.append(row);
        }
        if (!list.childElementCount) list.append(el('li', '暂无记录', 'game-muted'));
        cursor = page.nextCursor;
        more.hidden = !cursor;
    }
    const more = button('加载更多', loadMore, 'ghost');
    body.append(list, more);
    await loadMore();
    await panel('游玩记录', body);
}

function renderChat() {
    const unread = document.querySelector('[data-action="room"]');
    if (!unread) return;
    const incoming = messages.filter((m) => m.playerId !== snap?.me?.playerId),
        lastId = incoming.at(-1)?.id;
    if (chatMessages || chatSeen === null) chatSeen = lastId || '';
    const hasUnread = !!lastId && lastId !== chatSeen;
    unread.toggleAttribute('data-unread', hasUnread);
    const label = hasUnread ? '队伍与对话，有新消息' : '队伍与对话';
    unread.setAttribute('aria-label', label);
    unread.setAttribute('data-game-tip', label);
    if (!chatMessages) return;
    chatMessages.replaceChildren(
        ...messages.slice(-60).map((m) => {
            const dir = m.playerId === snap?.me?.playerId ? 'outgoing' : 'incoming';
            const li = el('li', null, 'lq-bubble lq-bubble--' + dir);
            li.append(
                el('strong', m.displayName || m.senderName || '队友', 'lq-bubble__author'),
                el('p', m.body || m.text, 'lq-bubble__content')
            );
            return li;
        })
    );
}

async function openRoom() {
    if (roomPanel || !snap?.roomId) return;
    const body = el('div', null, 'game-form');
    let composer, connectComposer;
    const roster = el('ul', null, 'game-members game-members--compact');
    for (const m of snap.members || []) {
        const li = el('li', null, 'game-seat');
        li.dataset.seatState = m.status === 'reconnecting' ? 'reconnecting' : 'online';
        const dot = el('i', null, 'game-seat__dot');
        dot.setAttribute('aria-hidden', 'true');
        li.append(el('b', String(m.seat || '·'), 'game-seat__num'), el('span', m.displayName), dot);
        roster.append(li);
    }
    body.append(roster);
    if (permission('room.chat')) {
        chatMessages = el('ul', null, 'game-messages');
        body.append(chatMessages);
        renderChat();
        const { createComposer, enhanceComposer } = await import('../lq/composer.js');
        const roomId = snap.roomId,
            form = el('form', null, 'game-form'),
            status = el('p', null, 'lq-status');
        status.setAttribute('role', 'status');
        status.hidden = true;
        const root = createComposer('composer', {
            label: '本局消息',
            name: 'message',
            required: true,
            maxlength: 300,
            enter: 'send',
            attachment: null,
            emoji: null,
        });
        form.append(root, status);
        body.append(form);
        connectComposer = () => enhanceComposer(root);
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const input = form.elements.message,
                text = input.value.trim();
            if (!text) return;
            composer.set({ busy: true });
            try {
                await command(idpath('rooms', roomId) + '/chat', { text });
                input.value = '';
                status.textContent = '';
                status.hidden = true;
                schedule(0);
            } catch (err) {
                status.textContent = err.message;
                status.hidden = false;
            } finally {
                composer.set({ busy: false });
            }
        });
    }
    const closed = () => {
        composer?.destroy();
        roomPanel = null;
        chatMessages = null;
    };
    roomPanel = await panel('队伍与对话', body, {
        type: matchMedia('(min-width:1024px)').matches ? 'drawer' : 'sheet',
        onClose: closed,
        onDestroy: closed,
    });
    composer = connectComposer?.();
}

async function leave(goHall) {
    if (!snap || terminal(snap.phase) || snap.readOnly) {
        if (goHall) location.assign('/games');
        return;
    }
    if (
        !(await confirmAction(
            '离开本局',
            snap.mode === 'solo' ? '保存当前进度并离开？' : '离开将释放席位，已确认进度保留。'
        ))
    )
        return;
    mutating = true;
    stopSync();
    let prepared = false;
    try {
        const payload = {};
        if (snap.mode === 'solo' && bridge) {
            prepared = true;
            const flush = await bridge.prepareLeave();
            if (flush?.privateSave) {
                payload.privateSave = flush.privateSave;
                payload.expectedPrivateRevision = flush.expectedPrivateRevision ?? progress?.privateRevision;
            }
        }
        const route = snap.runId
            ? idpath('runs', snap.runId) + '/leave'
            : snap.roomId
              ? idpath('rooms', snap.roomId) + '/leave'
              : idpath('queue', snap.ticketId) + '/cancel';
        await command(route, payload);
        disposeFrame();
        snap = null;
        if (goHall) location.assign('/games');
        else chooseMode();
    } catch (e) {
        if (prepared) bridge?.emit('session.resumeInput', {});
        throw e;
    } finally {
        mutating = false;
        schedule(0);
    }
}

async function replay() {
    const root = choiceField(
        '选择章节',
        'rank',
        Array.from({ length: progress.rank + 1 }, (_, i) => [i, chapter(i)]),
        progress.rank
    );
    await formPanel('选章重玩', root, '开始', async (d) => {
        const payload = { mode: 'solo', startRank: Number(d.get('rank')) };
        let prepared = false;
        mutating = true;
        stopSync();
        try {
            if (bridge && snap?.phase === 'running') {
                prepared = true;
                const flush = await bridge.prepareLeave();
                if (flush?.privateSave != null) {
                    payload.privateSave = flush.privateSave;
                    payload.expectedPrivateRevision = flush.expectedPrivateRevision;
                }
            }
            const next = await command(gamePath + '/runs', payload);
            disposeFrame();
            await enterResult(next);
        } catch (e) {
            if (prepared) bridge?.emit('session.resumeInput', {});
            throw e;
        } finally {
            mutating = false;
            schedule(0);
        }
    });
}

onAction({
    create: () => teamForm('create'),
    join: () => teamForm('join'),
    random: () => teamForm('random'),
    teams: templates,
    history: historyPanel,
    room: openRoom,
    restart: async () => {
        disposeFrame();
        await enterResult(await command(gamePath + '/runs', { mode: 'solo' }));
        schedule(0);
    },
    rules: () => {
        const box = el('div', null, 'game-form');
        box.append(el('p', game.summary || '在游戏画面内直接操作即可。'));
        if (game.authorName) box.append(el('p', '作者 ' + game.authorName));
        const metadata = [game.genreLabel, ...(game.tags || []).map(tag => '#' + tag)].filter(Boolean);
        if (metadata.length) box.append(el('p', metadata.join(' · '), 'game-muted'));
        const source = game.config?.attribution;
        if (source) box.append(el('p', '原作 ' + [source.gameKey, source.version, source.author,
            ...(source.tags || []).map(tag => '#' + tag)].filter(Boolean).join(' · ')));
        return panel('玩法', box);
    },
    chapters: replay,
    leave: () => leave(false),
    retry: () => location.reload(),
    takeover: async () => {
        apply(await command(gamePath + '/takeover', subject()));
        schedule(0);
    },
    rotate: async () => {
        const r = await command(idpath('rooms', snap.roomId) + '/rotate-code', {});
        if (r.phase) apply(r);
        schedule(0);
    },
    copy: async () => {
        try {
            await navigator.clipboard.writeText(snap.code);
            notice('已复制');
        } catch {
            const sel = getSelection(),
                r = document.createRange();
            r.selectNodeContents(document.querySelector('[data-code-text]'));
            sel.removeAllRanges();
            sel.addRange(r);
            notice('已选中');
        }
    },
    mute: async () => {
        const muted = !prefs.audioMuted;
        if (flags.preview) {
            prefs = { ...prefs, audioMuted: muted };
        } else {
            try {
                prefs = await api('/me/preferences', {
                    method: 'PUT',
                    body: { audioMuted: muted, expectedRevision: prefs.revision },
                });
            } catch (e) {
                if (e.status !== 409) throw e;
                const latest = await api('/me/preferences');
                prefs = await api('/me/preferences', {
                    method: 'PUT',
                    body: { audioMuted: muted, expectedRevision: latest.revision },
                });
            }
        }
        availableControls();
        bridge?.emit('audio.changed', { muted });
        focusStage();
    },
});

document.querySelector('.game-stage')?.addEventListener('pointerdown', (e) => {
    if (e.target === e.currentTarget || e.target === holder) focusStage();
});

document.querySelector('[data-exit-game]').addEventListener('click', (e) => {
    if (!flags.preview && snap && !terminal(snap.phase)) {
        e.preventDefault();
        run(null, () => leave(true));
    }
});

document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    schedule(0);
    if (foreign && !flags.preview && !stopped)
        api('/me/activity?' + new URLSearchParams({ gameId: game.id, hostInstance }))
            .then((a) => {
                foreign = a.multiplayer?.gameId !== game.id ? a.multiplayer : null;
                if (!snap) chooseMode();
                else availableControls();
            })
            .catch(() => {});
});

addEventListener('pagehide', () => {
    stopped = true;
    releaseInstance?.();
    syncRequest?.abort();
    clearTimeout(pollTimer);
    clearTimeout(clockTimer);
    clearTimeout(leaseTimer);
    clearTimeout(saveClearTimer);
    disposeFrame();
});

addEventListener('pageshow', (e) => {
    if (e.persisted) location.reload();
});

bindIconTooltips(document);
identifyTab()
    .then(initial)
    .catch((e) => stop(e.message));
