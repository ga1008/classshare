import { api, el, panel, setCsrfToken } from './ui.js';
import { enhanceDropdowns } from '../lq/dropdown.js';

const endpoint = '/classroom-access';

export async function openClassroomGameSettings(trigger) {
    const template = document.getElementById('classroom-game-settings');
    if (!template) return;
    const form = document.importNode(template.content.firstElementChild, true);
    const { offering, minutes, mode, gameSearch, allGames } = form.elements;
    const fields = form.querySelector('[data-policy-fields]');
    const status = form.querySelector('[data-policy-status]');
    const feedback = form.querySelector('[data-policy-feedback]');
    const submit = form.querySelector('[type="submit"]');
    const reload = form.querySelector('[data-policy-reload]');
    const more = form.querySelector('[data-policy-more]');
    const results = form.querySelector('[data-policy-results]');
    const fixed = trigger.dataset.gameOffering;
    const lifetime = new AbortController();
    const selected = new Map();
    let state, observedAt, dirty = false, savePending = false, readGeneration = 0, searchGeneration = 0;
    let searchTimer, clock, cursor = '', searchRequest, composing = false;
    function cleanup() {
        lifetime.abort();
        searchRequest?.abort();
        clearTimeout(searchTimer);
        clearTimeout(clock);
        dropdowns.forEach(dropdown => dropdown.destroy());
    }
    let dropdowns = [];
    await panel('课堂小游戏设置', form, {
        trigger, beforeClose: () => !savePending, onClose: cleanup, onDestroy: cleanup,
    });

    function tell(message) {
        feedback.textContent = message;
        feedback.hidden = !message;
    }
    function showState() {
        clearTimeout(clock);
        if (!state || lifetime.signal.aborted) return;
        const now = state.serverNow + performance.now() - observedAt;
        const temporary = state.mode !== 'auto' && state.expiresAt > now;
        if (!temporary) {
            if (state.mode !== 'auto' && !dirty) {
                mode.value = 'auto';
                toggle();
            }
            status.textContent = state.nextChangeAt && state.nextChangeAt <= now
                ? '当前跟随学生课表，上课暂停，课间和课后开放。'
                : '当前跟随课表 · ' + (state.scheduledNow ? '本课堂正在上课' : '本课堂当前为课外时间');
            if (state.nextChangeAt > now)
                clock = setTimeout(showState, Math.min(2147483647, state.nextChangeAt - now + 20));
            return;
        }
        const secs = Math.ceil((state.expiresAt - now) / 1000);
        const target = state.games?.length ? state.games.map(g => g.title).join('、') : '全部游戏';
        status.textContent = (state.mode === 'allow' ? '已临时放行' : '已临时禁止') + ' · ' + target;
        countdown.textContent = '剩余 ' + Math.floor(secs / 60) + ' 分 ' + String(secs % 60).padStart(2, '0') + ' 秒';
        status.append(' ', countdown);
        clock = setTimeout(showState, Math.min(1000, state.expiresAt - now + 10));
    }
    const countdown = el('span');
    countdown.setAttribute('role', 'timer');
    countdown.setAttribute('aria-live', 'off');
    status.setAttribute('aria-live', 'off');

    function selectionLabel() {
        form.querySelector('[data-policy-selected]').textContent = selected.size
            ? '已选 ' + selected.size + ' 款 · ' + [...selected.values()].join('、')
            : '请至少选择一款游戏。';
    }
    async function search(append = false) {
        clearTimeout(searchTimer);
        searchRequest?.abort();
        const query = gameSearch.value.trim();
        const sequence = ++searchGeneration;
        const request = new AbortController();
        searchRequest = request;
        const params = new URLSearchParams({ q: query, limit: '20', genres: '', tags: '' });
        if (append && cursor) params.set('cursor', cursor);
        more.disabled = true;
        try {
            const page = await api('/catalog?' + params, { signal: request.signal });
            if (sequence !== searchGeneration || lifetime.signal.aborted) return;
            if (!append || page.cursorReset) results.replaceChildren();
            const known = new Set([...results.querySelectorAll('input')].map(n => n.value));
            for (const item of page.items || []) {
                if (known.has(item.id)) continue;
                const label = el('label', null, 'lq-choice');
                const input = el('input', null, 'lq-control');
                input.type = 'checkbox';
                input.dataset.lqComponent = 'checkbox';
                input.value = item.id;
                input.checked = selected.has(item.id);
                input.addEventListener('change', () => {
                    if (input.checked) selected.set(item.id, item.title);
                    else selected.delete(item.id);
                    selectionLabel();
                });
                label.append(input, el('span', item.title));
                results.append(label);
            }
            if (!results.childElementCount) results.append(el('p', '没有匹配的游戏'));
            cursor = page.nextCursor || '';
            more.hidden = !cursor;
        } catch (e) {
            if (!request.signal.aborted && !lifetime.signal.aborted) tell(e.message);
        } finally {
            if (sequence === searchGeneration) more.disabled = false;
        }
    }
    function toggle() {
        const temporary = mode.value !== 'auto';
        minutes.disabled = !temporary;
        form.querySelector('[data-policy-temporary]').hidden = !temporary;
        form.querySelector('[data-policy-auto-help]').hidden = temporary;
        const showGames = temporary && !allGames.checked;
        form.querySelector('[data-policy-picker]').hidden = !showGames;
        if (showGames) search();
        else {
            clearTimeout(searchTimer);
            searchRequest?.abort();
        }
        selectionLabel();
    }
    function applyState(next, keepDraft = false) {
        state = next;
        observedAt = performance.now();
        setCsrfToken(state.csrfToken);
        if (!keepDraft) dirty = false;
        showState();
        if (keepDraft) return;
        mode.value = state.mode;
        minutes.value = state.durationMinutes || 15;
        allGames.checked = !state.gameIds?.length;
        selected.clear();
        for (const game of state.games || []) selected.set(game.id, game.title);
        gameSearch.value = '';
        toggle();
    }
    const url = () => endpoint + '/' + encodeURIComponent(fixed || offering.value);
    async function load() {
        const generation = ++readGeneration;
        fields.disabled = submit.disabled = true;
        state = null;
        clearTimeout(clock);
        tell('');
        status.textContent = '正在读取课堂规则…';
        try {
            if (!fixed && !offering.length) {
                const list = await api(endpoint, {signal:lifetime.signal});
                if (generation !== readGeneration) return;
                for (const item of list.classrooms || []) offering.add(new Option(item.name, item.id));
                if (!offering.length) {
                    status.textContent = '暂无可管理的授课课堂';
                    return;
                }
                dropdowns = enhanceDropdowns(form);
            }
            const next = await api(url(), {signal:lifetime.signal});
            if (generation !== readGeneration || lifetime.signal.aborted) return;
            applyState(next);
            fields.disabled = submit.disabled = false;
        } catch (e) {
            if (generation === readGeneration && !lifetime.signal.aborted) status.textContent = e.message;
        }
    }
    if (fixed) {
        const courseLabel = el('strong', trigger.dataset.gameOfferingName || '当前课堂');
        form.prepend(courseLabel);
        if (!document.querySelector('meta[name="minigames-csrf"]')) {
            const meta = el('meta');
            meta.name = 'minigames-csrf';
            document.head.append(meta);
        }
    }
    form.querySelector('[data-course-picker]').hidden = !!fixed;
    fields.addEventListener('input', e => { if (e.target !== gameSearch) dirty = true; });
    offering.addEventListener('change', load);
    reload.addEventListener('click', load);
    form.addEventListener('change', e => {
        if (e.target.name === 'mode' || e.target === allGames) toggle();
    });
    function queueSearch() {
        clearTimeout(searchTimer);
        searchRequest?.abort();
        searchGeneration++;
        if (!composing) searchTimer = setTimeout(() => search(), 220);
    }
    gameSearch.addEventListener('compositionstart', () => { composing = true; queueSearch(); });
    gameSearch.addEventListener('compositionend', () => { composing = false; queueSearch(); });
    gameSearch.addEventListener('input', queueSearch);
    more.addEventListener('click', () => search(true));
    form.addEventListener('submit', async e => {
        e.preventDefault();
        if (e.isComposing || composing || !state || savePending || !form.reportValidity()) return;
        const temporary = mode.value !== 'auto';
        const specific = temporary && !allGames.checked;
        if (specific && !selected.size) {
            tell('请勾选游戏，或切换为应用到全部游戏。');
            gameSearch.focus();
            return;
        }
        const body = { mode: mode.value, durationMinutes: temporary ? Number(minutes.value) : 0,
            gameIds: specific ? [...selected.keys()] : [], expectedRevision: state.revision };
        savePending = fields.disabled = submit.disabled = offering.disabled = reload.disabled = true;
        try {
            applyState(await api(url(), {method:'PUT', body}));
            tell('规则已应用到本课堂。');
            status.tabIndex = -1;
            status.focus({preventScroll:true});
            status.scrollIntoView({block:'nearest'});
        } catch (err) {
            if (err.status === 409) {
                try {
                    applyState(await api(url(), {signal:lifetime.signal}), true);
                    tell('其他页面已更改规则。上方已显示最新状态，你的选择已保留，核对后可再次应用。');
                } catch (readError) { tell(readError.message); }
            } else tell(err.message);
        } finally {
            savePending = fields.disabled = submit.disabled = offering.disabled = reload.disabled = false;
        }
    });
    await load();
}
