import {
    api,
    bindIconTooltips,
    bootstrap,
    el,
    formPanel,
    gameIcon,
    gameLink,
    modeBadges,
    notice,
    onAction,
    svgIcon,
} from './ui.js';

import { wellbeingSummary } from './play-time.js';
import { createComponent } from '../lq/components.js';

const boot = bootstrap();
const wellbeing = document.querySelector('[data-wellbeing-summary]');
if (wellbeing) wellbeing.textContent = wellbeingSummary(boot.playWellbeing);
function restriction(access) {
    if (!access || (access.allowed && access.source !== 'classroom_allow')) return null;
    const badge = el('small', access.allowed ? '临时开放' : '课堂限制', 'lq-status');
    badge.title = access.reason;
    return badge;
}
const sidebarDelay = () => 55000 + Math.floor(Math.random() * 10000);
const grid = document.querySelector('[data-games-grid]'),
    filters = document.querySelector('[data-game-search]');
const next = document.querySelector('[data-game-next]'),
    prev = document.querySelector('[data-game-prev]');
let queryRequest,
    timer,
    composing = false,
    sidebarSerial = 0,
    sidebarTimer,
    expiresAt = boot.hydrated ? Date.now() + 60000 : 0;
const pages = [location.href];
const pointer = matchMedia(
    '(hover:hover) and (pointer:fine) and (prefers-reduced-motion:no-preference) and (forced-colors:none)'
);
let active = null,
    activeHalo = null,
    rect = null,
    frame = 0,
    point = null;

function resetHalo() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    active?.removeAttribute('data-halo-active');
    active = null;
    activeHalo = null;
    rect = null;
}

function drawHalo() {
    frame = 0;
    if (!active || !activeHalo || !point) return;
    rect ||= active.getBoundingClientRect();
    activeHalo.style.transform =
        'translate3d(' + (point.x - rect.left - 96) + 'px,' + (point.y - rect.top - 96) + 'px,0)';
}

grid.addEventListener('pointermove', (e) => {
    const prefs = document.documentElement.dataset;
    if (!pointer.matches || prefs.lqGlass === 'off' || prefs.lqTier === 'C' || prefs.lqMotion === 'off')
        return resetHalo();
    const card = e.target.closest('[data-game-tile]');
    if (!card) return resetHalo();
    if (card !== active) {
        resetHalo();
        active = card;
        activeHalo = card.querySelector('.game-halo');
        active.dataset.haloActive = '';
    }
    point = { x: e.clientX, y: e.clientY };
    if (!frame) frame = requestAnimationFrame(drawHalo);
});
grid.addEventListener('pointerleave', resetHalo);
pointer.addEventListener('change', resetHalo);
window.addEventListener('blur', resetHalo);
window.addEventListener(
    'scroll',
    () => {
        rect = null;
    },
    { passive: true, capture: true }
);
window.addEventListener(
    'resize',
    () => {
        rect = null;
    },
    { passive: true }
);

function tile(g, index) {
    const li = el('li'),
        a = gameLink(g);
    a.className = 'lq-surface game-tile';
    a.dataset.gameTile = '';
    if (g.access?.nextChangeAt) a.dataset.accessChange = g.access.nextChangeAt;
    a.dataset.lqComponent = 'surface';
    const halo = el('span', null, 'game-halo');
    halo.setAttribute('aria-hidden', 'true');
    a.append(halo, gameIcon(g, { eager: index < 8 }));
    if (g.featured) {
        const star = el('span', null, 'game-tile-star');
        star.setAttribute('aria-hidden', 'true');
        star.append(svgIcon('sparkles', 'game-mini-icon'));
        a.append(star);
    }
    a.append(el('strong', g.title));
    const info = [g.genreLabel, ...(g.tags || []).slice(0,2).map((tag) => '#' + tag)].filter(Boolean).join(' · ');
    if (info) a.append(el('small',info,'game-tile-meta game-muted'));
    a.append(modeBadges(g));
    const badge = restriction(g.access);
    if (badge) a.append(badge);
    li.append(a);
    return li;
}

let accessTimer, lastCheckedBoundary = 0;
function scheduleAccessRefresh() {
    clearTimeout(accessTimer);
    if (document.hidden) return;
    const changes = [...grid.querySelectorAll('[data-access-change]')]
        .map(node => Number(node.dataset.accessChange)).filter(at => at > lastCheckedBoundary);
    if (!changes.length) return;
    const boundary = Math.min(...changes);
    accessTimer = setTimeout(() => {
        if (composing) return;
        lastCheckedBoundary = boundary;
        search(urlFor(new URL(location.href).searchParams.get('cursor')));
    }, Math.min(2147480000, Math.max(0, boundary - Date.now())) + Math.random() * 1500);
}

function syncModeChips(value) {
    for (const chip of filters.querySelectorAll('[data-mode-chip]')) {
        const selected = chip.dataset.modeChip === value;
        chip.setAttribute('aria-pressed', String(selected));
        chip.classList.toggle('is-selected', selected);
    }
}

function urlFor(pageCursor) {
    const u = new URL('/games', location.origin);
    for (const [k, v] of new FormData(filters)) if (v || k === 'genres' || k === 'tags') u.searchParams.set(k, v);
    if (pageCursor) u.searchParams.set('cursor', pageCursor);
    return u;
}

async function search(url, historyMode) {
    queryRequest?.abort();
    const controller = new AbortController();
    queryRequest = controller;
    try {
        const data = await api('/catalog' + url.search, { signal: controller.signal });
        if (controller !== queryRequest) return;
        if (data.cursorReset) {
            url.searchParams.delete('cursor');
            historyMode = 'replace';
        }
        if (historyMode) {
            history[historyMode + 'State'](null, '', url);
            if (historyMode === 'push') pages.push(url.href);
            else pages.splice(0, pages.length, url.href);
        }
        resetHalo();
        grid.replaceChildren(...(data.items || []).map((g, i) => tile(g, i)));
        scheduleAccessRefresh();
        const empty = document.querySelector('[data-grid-empty]');
        empty.hidden = !!data.items?.length;
        const text = empty.querySelector('span') || empty;
        text.textContent =
            ['q','mode','genres','tags'].some((key) => url.searchParams.get(key)) ? '未找到匹配游戏' : '暂无可玩游戏';
        next.hidden = !data.nextCursor;
        if (data.nextCursor) next.href = urlFor(data.nextCursor);
        prev.hidden = !url.searchParams.has('cursor');
        prev.href = pages.at(-2) || urlFor('');
        notice('');
    } catch (e) {
        if (e.name !== 'AbortError') notice('加载失败：' + e.message);
    }
}

function filterChanged() {
    clearTimeout(timer);
    if (!composing) timer = setTimeout(() => search(urlFor(''), 'replace'), 200);
}

filters.addEventListener('compositionstart', () => {
    composing = true;
    clearTimeout(timer);
});
filters.addEventListener('compositionend', () => {
    composing = false;
    filterChanged();
});
filters.addEventListener('input', filterChanged);
filters.addEventListener('click', (e) => {
    const chip = e.target.closest('[data-mode-chip]');
    if (!chip) return;
    e.preventDefault();
    const nextMode = chip.dataset.modeChip || '';
    if (filters.elements.mode.value === nextMode) return;
    filters.elements.mode.value = nextMode;
    syncModeChips(nextMode);
    clearTimeout(timer);
    if (!composing) search(urlFor(''), 'replace');
});
filters.addEventListener('submit', (e) => {
    e.preventDefault();
    clearTimeout(timer);
    if (!composing) search(urlFor(''), 'replace');
});

next.addEventListener('click', (e) => {
    e.preventDefault();
    search(new URL(next.href), 'push');
});
prev.addEventListener('click', (e) => {
    if (pages.length > 1) {
        e.preventDefault();
        history.back();
    }
});
addEventListener('popstate', () => {
    const u = new URL(location.href);
    filters.elements.q.value = u.searchParams.get('q') || '';
    const mode = u.searchParams.get('mode') || '';
    filters.elements.mode.value = mode;
    syncModeChips(mode);
    for (const key of ['genres','tags']) filters.elements[key].value =
        u.searchParams.get(key) ?? (savedFilters[key] || []).join(',');
    discoverySummary();
    if (pages.at(-2) === u.href) pages.pop();
    search(u);
});

let savedFilters = boot.discovery || {genres:[], tags:[], revision:0};
const splitTags = (value) => [...new Set(value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean))];
function discoverySummary() {
    const labels = new Map((boot.genreOptions || []).map((g) => [g.id, g.label]));
    const genres = splitTags(filters.elements.genres.value).map((id) => labels.get(id) || id);
    const tags = splitTags(filters.elements.tags.value).map((tag) => '#' + tag);
    document.querySelector('[data-discovery-summary]').textContent = [...genres, ...tags].join(' · ');
}

async function chooseDiscovery() {
    const form = document.importNode(document.querySelector('#discovery-form').content.firstElementChild, true);
    const selected = new Set(splitTags(filters.elements.genres.value));
    form.querySelectorAll('[name="genre"]').forEach((input) => { input.checked = selected.has(input.value); });
    form.elements.tagList.value = filters.elements.tags.value.replaceAll(',', '，');
    form.addEventListener('click', (event) => {
        const tag = event.target.closest('[data-pick-tag]')?.dataset.pickTag;
        if (tag) form.elements.tagList.value = [...new Set([...splitTags(form.elements.tagList.value),tag])].join('，');
        if (event.target.closest('[data-clear-filters]')) {
            form.querySelectorAll('[name="genre"]').forEach((input) => { input.checked = false; });
            form.elements.tagList.value = '';
        }
    });
    await formPanel('类型与标签', form, '确认筛选', async (data) => {
        const genres = data.getAll('genre');
        const tags = splitTags(String(data.get('tagList')));
        if (tags.length > 12 || tags.some((t) => [...t].length > 32))
            throw new Error('最多 12 个标签，每个标签最多 32 字');
        if (data.has('remember')) {
            try {
                savedFilters = await api('/discovery-preferences', {method:'PUT',
                    body:{genres,tags,expectedRevision:savedFilters.revision || 0}});
            } catch (error) {
                if (error.status !== 409) throw error;
                savedFilters = await api('/discovery-preferences');
                throw new Error('其他设备已更新偏好。请核对当前选择，再次确认即可保存。');
            }
        }
        filters.elements.genres.value = genres.join(',');
        filters.elements.tags.value = tags.join(',');
        discoverySummary();
        clearTimeout(timer);
        await search(urlFor(''), 'replace');
    });
}
discoverySummary();

function shortNote(row) {
    const raw = row.reason || row.label || '';
    return raw.replace(/^同标签\s*·\s*/, '#').replace('教师精选', '精选').replace('近期热门', '热门').replace('最近更新', '新上');
}

function section(name, rows) {
    const list = document.querySelector('[data-sidebar="' + name + '"]');
    if (!list) return;
    const items = rows.map((row, idx) => {
        const g = row.game || row,
            li = el('li'),
            a = gameLink(g);
        a.className = 'game-side-row';
        if (name === 'popular') {
            const rank = el('b', String(idx + 1), 'game-rank');
            if (idx < 3) rank.dataset.top = String(idx + 1);
            a.append(rank);
        }
        a.append(gameIcon(g));
        const info = el('span', null, 'game-side-main');
        const person = row.friendName || row.displayName;
        if (name === 'friends' && person) {
            const liveWrap = el('span', null, 'game-friend-line');
            const dot = el('i', null, 'game-live-dot');
            dot.setAttribute('aria-hidden', 'true');
            liveWrap.append(dot, el('strong', person));
            info.append(liveWrap, el('small', g.title));
        } else if (name === 'popular') {
            const title = el('span', null, 'game-popular-title');
            title.append(el('span', g.title));
            if (row.heatScore != null) {
                title.append(createComponent('badge', { value: '热度 ' + row.heatScore, tone: 'warning' }));
            }
            info.append(title);
            if (row.playerCount != null) {
                const recent = row.recentPlayerCount == null ? '' : ' · 近 7 天 ' + row.recentPlayerCount + ' 人';
                info.append(el('small', '累计 ' + row.playerCount + ' 人玩过' + recent, 'game-popular-people'));
            }
        } else {
            info.append(el('span', g.title));
        }
        const restricted = restriction(row.access || g.access);
        if (restricted) info.append(restricted);
        a.append(info);
        const note = name !== 'popular' && shortNote(row);
        if (note) {
            const badge = el('small', note, 'game-side-badge');
            a.append(badge);
        }
        li.append(a);
        return li;
    });
    list.replaceChildren(...(items.length ? items : [el('li', list.dataset.empty, 'game-muted game-side-empty')]));
}

async function updateSidebar() {
    if (document.hidden) return;
    clearTimeout(sidebarTimer);
    if (expiresAt > 0 && Date.now() >= expiresAt) section('friends', []);
    const serial = ++sidebarSerial;
    try {
        const data = await api('/sidebar');
        if (serial !== sidebarSerial || document.hidden) return;
        if (wellbeing && data.playWellbeing) wellbeing.textContent = wellbeingSummary(data.playWellbeing);
        for (const k of ['recent', 'popular', 'friends', 'recommended']) section(k, data[k] || []);
        expiresAt = Date.now() + 60000;
    } catch {
        if (Date.now() >= expiresAt) section('friends', []);
    } finally {
        if (serial === sidebarSerial && !document.hidden) sidebarTimer = setTimeout(updateSidebar, sidebarDelay());
    }
}

document.addEventListener('visibilitychange', () => {
    scheduleAccessRefresh();
    if (document.hidden) {
        resetHalo();
        clearTimeout(sidebarTimer);
        sidebarSerial++;
    } else if (Date.now() >= expiresAt - 5000) {
        updateSidebar();
    } else {
        clearTimeout(sidebarTimer);
        sidebarTimer = setTimeout(updateSidebar, Math.max(1000, expiresAt - Date.now()));
    }
});

onAction({
    discovery: chooseDiscovery,
    refresh: () => Promise.all([updateSidebar(), search(new URL(location.href))]),
    preferences: async () => {
        const preferences = await api('/me/preferences');
        const form = document.importNode(
            document.querySelector('#preferences-form').content.firstElementChild,
            true
        );
        form.elements.displayAlias.value = preferences.displayAlias || '';
        form.elements.sharePlaying.checked = !!preferences.sharePlaying;
        await formPanel('游玩设置', form, '保存设置', async (data) => {
            const alias = String(data.get('displayAlias')).trim(),
                sharing = data.has('sharePlaying'),
                changes = {};
            if (alias !== preferences.displayAlias) changes.displayAlias = alias;
            if (sharing !== preferences.sharePlaying) changes.sharePlaying = sharing;
            try {
                await api('/me/preferences', {
                    method: 'PUT',
                    body: { ...changes, expectedRevision: preferences.revision },
                });
            } catch (e) {
                if (e.status !== 409) throw e;
                const latest = await api('/me/preferences');
                await api('/me/preferences', {
                    method: 'PUT',
                    body: { ...changes, expectedRevision: latest.revision },
                });
            }
            notice('设置已保存');
        });
    },
});

bindIconTooltips(document);
scheduleAccessRefresh();
if (boot.hydrated) {
    if (boot.nextCursor) {
        next.hidden = false;
        next.href = urlFor(boot.nextCursor);
    }
    sidebarTimer = setTimeout(updateSidebar, sidebarDelay());
} else {
    updateSidebar();
}
