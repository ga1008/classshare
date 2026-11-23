import {
    api,
    apiBase,
    authHeaders,
    bindIconTooltips,
    bootstrap,
    button,
    confirmAction,
    el,
    field,
    formPanel,
    gameIcon,
    libraryLink,
    makeId,
    notice,
    onAction,
    panel,
    responseError,
    run,
    showControl,
    statusChip,
    svgIcon,
} from './ui.js';

const boot = bootstrap(),
    base = '/admin/games';
const statusText = {
    draft: '草稿',
    listed: '已上架',
    unlisted: '已下架',
    deleting: '删除中',
    deleted: '已彻底删除',
    validating: '验收中',
    ready: '验收就绪',
    retired: '已归档',
    rejected: '验收失败',
    pending: '待处理',
    queued: '排队中',
    waiting_probe: '待浏览器验收',
    retry: '待重试',
    running: '处理中',
    done: '已完成',
    succeeded: '已完成',
    completed: '已完成',
    failed: '需处理',
};
const statusTones = {
    listed: 'success',
    ready: 'success',
    succeeded: 'success',
    done: 'success',
    completed: 'success',
    validating: 'info',
    running: 'info',
    queued: 'info',
    waiting_probe: 'info',
    pending: 'info',
    unlisted: 'warning',
    retry: 'warning',
    rejected: 'danger',
    failed: 'danger',
    deleting: 'danger',
    deleted: 'danger',
    draft: 'neutral',
    retired: 'neutral',
};
const translated = (s) => statusText[s] || s || '';
const toneOf = (s) => statusTones[s] || 'neutral';
const detailPath = () => base + '/' + encodeURIComponent(boot.gameId);
const filters = document.querySelector('[data-admin-filters]');
let game,
    versions = [],
    jobs = [],
    dirty = false,
    pollTimer,
    deleteJobId,
    refreshCount = 0;
const draftTargets = {};
const cursorPages = [location.href];
const attributes = document.querySelector('[data-game-attributes]');
const scopeNames = {
    private: '私有',
    classroom: '课堂',
    class: '行政班',
    department: '本系部',
    college: '本学院',
    school: '本校',
    public: '全平台',
};

async function listGames() {
    const u = new URL(location.href),
        target = '/admin/games' + u.search,
        seq = ++refreshCount;
    const result = await api(target);
    if (seq !== refreshCount) return;
    const list = document.querySelector('[data-admin-list]');
    const rows = result.items.map((g) => {
        const row = el('li', null, 'lq-row game-admin-row');
        const main = el('div', null, 'game-row-main');
        const head = el('div', null, 'game-row-head');
        head.append(libraryLink(g));
        if (g.featured) {
            const star = el('span', null, 'game-featured-mark');
            star.setAttribute('aria-label', '精选');
            star.append(svgIcon('sparkles', 'game-mini-icon'));
            head.append(star);
        }
        const meta = el('div', null, 'game-row-meta');
        if (g.gameKey) meta.append(el('code', g.gameKey, 'game-key-pill'));
        meta.append(el('small', scopeNames[g.scope] || g.scope));
        if (g.minTier > 0 || g.maxTier < 10)
            meta.append(el('small', 'Lv.' + g.minTier + '–' + g.maxTier));
        for (const tag of (g.tags || []).slice(0, 4)) meta.append(el('small', '#' + tag, 'game-tag-pill'));
        main.append(head, meta);
        row.append(gameIcon(g), main, statusChip(translated(g.status), toneOf(g.status)));
        return row;
    });
    list.replaceChildren(...(rows.length ? rows : [el('li', '未找到符合条件的游戏', 'game-muted game-empty-row')]));
    showControl('next', !!result.nextCursor);
    showControl('previous', u.searchParams.has('cursor'));
    filters.dataset.next = result.nextCursor || '';
}

function gotoList(cursor, historyMode = 'push') {
    const u = new URL(location.href);
    u.search = '';
    for (const [k, v] of new FormData(filters)) if (v) u.searchParams.set(k, v);
    if (cursor) u.searchParams.set('cursor', cursor);
    history[historyMode + 'State'](null, '', u);
    if (historyMode === 'push') cursorPages.push(u.href);
    else cursorPages.splice(0, cursorPages.length, u.href);
    return listGames();
}

async function draft() {
    await formPanel('上传新游戏', field('游戏名称', 'title'), '创建草稿', async (data) => {
        const title = String(data.get('title')).trim();
        if (!title) throw new Error('请填写游戏名称');
        const result = await api(base, {
            method: 'POST',
            body: {
                title,
                summary: '',
                scope: 'private',
                targets: [],
                tags: [],
                minTier: 0,
                maxTier: 10,
                featured: false,
            },
        });
        location.assign('/manage/library/games/' + encodeURIComponent(result.game.id));
    });
}

if (filters) {
    function init() {
        const q = new URLSearchParams(location.search);
        for (const input of filters.elements) if (input.name) input.value = q.get(input.name) || '';
        if (cursorPages.at(-2) === location.href) cursorPages.pop();
        run(null, listGames);
    }
    filters.addEventListener('submit', (e) => {
        e.preventDefault();
        run(null, () => gotoList('', 'replace'));
    });
    filters.addEventListener('change', (e) => {
        if (e.target.tagName === 'SELECT') run(null, () => gotoList('', 'replace'));
    });
    onAction({
        draft,
        next: () => gotoList(filters.dataset.next, 'push'),
        previous: () => {
            if (cursorPages.length > 1) history.back();
            else return gotoList('', 'replace');
        },
    });
    addEventListener('popstate', init);
    init();
}

function snapshotCurrentTargets() {
    if (!attributes) return;
    const prevScope = attributes.dataset.activeScope;
    if (prevScope === 'class' || prevScope === 'classroom') {
        draftTargets[prevScope] = new FormData(attributes).getAll('target').map(Number);
    }
}

function renderTargets(selected) {
    const area = attributes.querySelector('[data-targets]'),
        scope = attributes.elements.scope.value;
    snapshotCurrentTargets();
    attributes.dataset.activeScope = scope;
    area.hidden = !['class', 'classroom'].includes(scope);
    const holder = area.querySelector('div');
    holder.replaceChildren();
    if (area.hidden) return;
    if (selected !== undefined) draftTargets[scope] = selected.map(Number);
    const activeIds = draftTargets[scope] || [];
    const choices = boot.scopeOptions?.[scope] || [];
    if (!choices.length) {
        holder.append(el('p', '暂无可授权对象', 'game-muted'));
        return;
    }
    for (const item of choices) {
        const input = el('input', null, 'lq-control');
        input.type = 'checkbox';
        input.dataset.lqComponent = 'checkbox';
        input.name = 'target';
        input.value = item.id;
        input.checked = activeIds.includes(Number(item.id));
        const l = el('label', null, 'game-check');
        l.append(input, el('span', item.name || item.title || item.label));
        holder.append(l);
    }
}

function summary() {
    const f = attributes.elements;
    const node = document.querySelector('[data-policy-summary]');
    if (!node) return;
    node.textContent =
        (scopeNames[f.scope.value] || '') + ' · Lv.' + f.minTier.value + '–' + f.maxTier.value;
}

function replaceForm() {
    const f = attributes.elements;
    f.title.value = game.title;
    f.summary.value = game.summary || '';
    f.scope.value = game.scope || 'private';
    f.tags.value = (game.tags || []).join('，');
    f.featured.checked = !!game.featured;
    f.minTier.value = game.minTier ?? 0;
    f.maxTier.value = game.maxTier ?? 10;
    delete attributes.dataset.activeScope;
    renderTargets(game.targets || []);
    summary();
    dirty = false;
    attributes.dataset.revision = game.revision;
}

async function mutation(suffix, body = {}, method = 'POST', key) {
    const result = await api(detailPath() + suffix, {
        method,
        key,
        body: { expectedRevision: game.revision, ...body },
    });
    if (result?.game) game = result.game;
    return result;
}

async function preview(v) {
    const result = await mutation('/versions/' + encodeURIComponent(v.id) + '/preview');
    const u = new URL(result.url, location.origin);
    if (u.origin !== location.origin) throw new Error('无效的预览地址');
    const body = el('div', null, 'game-form');
    const a = el('a', '打开隔离预览', 'lq-btn lq-btn--prominent');
    a.href = u.href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    body.append(a);
    await panel('教师预览', body);
}

async function inspectVersion(v) {
    const report = await api(detailPath() + '/versions/' + encodeURIComponent(v.id));
    const body = el('div', null, 'game-form');
    const record = report.version || v,
        job = jobs.find((j) => j.versionId === v.id && j.kind !== 'delete');
    const header = el('div', null, 'game-row-head');
    header.append(
        el('strong', record.releaseVersion || '候选版'),
        statusChip(translated(record.status), toneOf(record.status))
    );
    if (record.digest) header.append(el('code', record.digest.slice(0, 12) + '…', 'game-key-pill'));
    body.append(header);
    if (job) {
        body.append(el('p', job.stage || translated(job.status), 'game-muted'));
        const details = job.error?.message ? job.error : job.result?.probe;
        if (details) body.append(el('pre', JSON.stringify(details, null, 2), 'game-report-pre'));
    } else body.append(el('p', '暂无近期验收明细', 'game-muted'));
    await panel('版本验收报告', body);
}

async function verifyPlayed(v) {
    if (!(await confirmAction('确认已试玩', '已在隔离预览中核验本版本的启动、操作与存档？'))) return;
    await mutation('/versions/' + encodeURIComponent(v.id) + '/playtests', { passed: true });
    await reloadDetail(false);
    notice('已登记试玩');
}

async function publish(v) {
    if (
        !(await confirmAction(
            '发布此版本',
            '新开局将切换至 ' + v.releaseVersion + '；候场队列重置，进行中对局不受影响。'
        ))
    )
        return;
    await mutation('/publish', { versionId: v.id });
    await reloadDetail(false);
    notice('已发布 ' + v.releaseVersion);
}

function renderVersions() {
    const root = document.querySelector('[data-game-versions]');
    const rows = versions.map((v) => {
        const row = el('li', null, 'lq-row game-version-row');
        const info = el('div', null, 'game-row-head');
        info.append(
            el('strong', v.releaseVersion || '候选版', 'game-version-num'),
            statusChip(translated(v.status), toneOf(v.status))
        );
        if (v.id === game.currentVersionId) info.append(statusChip('当前版本', 'primary'));
        const actions = el('div', null, 'game-actions');
        actions.append(
            button('验收报告', () => inspectVersion(v), 'ghost', {
                icon: 'shield',
                iconOnly: true,
                ariaLabel: '验收报告',
            })
        );
        if (v.status === 'ready') {
            actions.append(
                button('教师预览', () => preview(v), 'soft', { icon: 'eye' }),
                button('确认已试玩', () => verifyPlayed(v), 'soft', { icon: 'check' })
            );
            if (v.id !== game.currentVersionId || game.status !== 'listed')
                actions.append(button('发布此版本', () => publish(v), 'prominent', { icon: 'rocket' }));
        }
        row.append(info, actions);
        return row;
    });
    root.replaceChildren(...(rows.length ? rows : [el('li', '尚未上传游戏包', 'game-muted game-empty-row')]));
}

function renderJobs() {
    const root = document.querySelector('[data-jobs]');
    const rows = jobs.map((j) => {
        const row = el('li', null, 'lq-row game-job-row');
        const head = el('div', null, 'game-row-head');
        head.append(statusChip(translated(j.status), toneOf(j.status)), el('span', j.stage || ''));
        row.append(head);
        const error = j.error;
        if ((typeof error === 'string' && error) || error?.message)
            row.append(
                el(
                    'div',
                    typeof error === 'string'
                        ? error
                        : [error.file, error.field, error.message, error.repairHint]
                              .filter(Boolean)
                              .join(' · '),
                    'lq-alert game-job-alert'
                )
            );
        const actions = el('div', null, 'game-actions');
        if (j.status === 'rejected' && j.kind === 'validate')
            actions.append(button('生成修复建议', () => suggest('repair', j.id), 'soft', { icon: 'sparkles' }));
        if (j.result?.suggestion)
            actions.append(button('查看建议', () => showSuggestion(j.result), 'soft', { icon: 'eye' }));
        if (actions.childElementCount) row.append(actions);
        return row;
    });
    root.replaceChildren(...(rows.length ? rows : [el('li', '暂无任务记录', 'game-muted game-empty-row')]));
}

async function suggest(purpose, jobId) {
    await mutation('/suggestions', { purpose, ...(jobId ? { jobId } : {}) });
    await reloadDetail(false);
    notice('已加入建议队列');
}

async function showSuggestion(result) {
    const body = el('div', null, 'game-form');
    body.append(
        statusChip(result.source === 'model' ? 'AI 辅助' : '规则推导', 'info'),
        el('p', result.suggestion)
    );
    if (result.purpose === 'metadata') {
        const tags = Array.isArray(result.tags) ? result.tags : [];
        if (result.summary) body.append(el('p', result.summary, 'game-muted'));
        if (tags.length) {
            const tagRow = el('div', null, 'game-row-meta');
            for (const t of tags) tagRow.append(el('small', '#' + t, 'game-tag-pill'));
            body.append(tagRow);
        }
        if (result.summary || tags.length)
            body.append(
                button(
                    '采用到属性草稿',
                    () => {
                        if (result.summary) attributes.elements.summary.value = result.summary;
                        if (tags.length) attributes.elements.tags.value = tags.join('，');
                        dirty = true;
                        notice('已填入草稿，核对后保存');
                    },
                    'prominent',
                    { icon: 'check' }
                )
            );
    }
    await panel('编目与修复建议', body);
}

async function trackDelete(id) {
    deleteJobId = id;
    const j = await api('/admin/jobs/' + encodeURIComponent(id));
    const state = j.status;
    notice('正在清理 · ' + translated(state) + (j.stage ? ' · ' + j.stage : ''));
    if (['done', 'completed', 'succeeded', 'deleted'].includes(state)) {
        notice('游戏已彻底删除');
        sessionStorage.removeItem('minigame:delete:' + boot.gameId);
        document.querySelectorAll('[data-game-detail] .game-section').forEach((n) => (n.hidden = true));
        return;
    }
    clearTimeout(pollTimer);
    if (!document.hidden) pollTimer = setTimeout(() => run(null, () => trackDelete(id)), 3500);
}

async function reloadDetail(resetForm) {
    const serial = ++refreshCount;
    const detail = await api(detailPath());
    if (serial !== refreshCount) return;
    game = detail.game;
    versions = detail.versions || [];
    jobs = detail.jobs || [];
    document.querySelector('[data-detail-heading]').textContent = game.title;
    const iconSlot = document.querySelector('[data-detail-icon]');
    if (iconSlot) iconSlot.replaceChildren(gameIcon(game, { eager: true }));
    const statusBox = document.querySelector('[data-detail-status]');
    statusBox.replaceChildren(statusChip(translated(game.status), toneOf(game.status)));
    if (game.gameKey) statusBox.append(el('code', game.gameKey, 'game-key-pill'));
    showControl('unlist', game.status === 'listed');
    showControl('delete', game.status !== 'deleting');
    if (resetForm || !dirty) replaceForm();
    renderVersions();
    renderJobs();
    if (game.status === 'deleting') {
        const job = jobs.find((j) => j.kind === 'delete');
        if (job) {
            await trackDelete(job.id);
            return;
        }
    }
    clearTimeout(pollTimer);
    if (!document.hidden) {
        const active = jobs.some((j) =>
            ['running', 'pending', 'queued', 'retry', 'validating', 'processing'].includes(j.status)
        );
        const delay = active ? 3000 : jobs.some((j) => j.status === 'waiting_probe') ? 60000 : 0;
        if (delay) pollTimer = setTimeout(() => run(null, () => reloadDetail(false)), delay);
    }
}

async function saveAttributes() {
    const f = attributes.elements,
        targets = new FormData(attributes).getAll('target').map(Number);
    const minTier = Number(f.minTier.value),
        maxTier = Number(f.maxTier.value);
    if (minTier > maxTier) throw new Error('修为下限不能超过上限');
    if (['class', 'classroom'].includes(f.scope.value) && !targets.length)
        throw new Error('请至少选择一个可授权对象');
    const policyChanged =
        game.scope !== f.scope.value ||
        game.minTier !== minTier ||
        game.maxTier !== maxTier ||
        JSON.stringify([...targets].sort()) !== JSON.stringify([...(game.targets || [])].sort());
    if (
        game.status === 'listed' &&
        policyChanged &&
        !(await confirmAction('更新访问范围', '超出新范围的在玩学生将退出当前对局。'))
    )
        return;
    try {
        const result = await api(detailPath(), {
            method: 'PATCH',
            body: {
                title: f.title.value.trim(),
                summary: f.summary.value.trim(),
                scope: f.scope.value,
                targets,
                minTier,
                maxTier,
                featured: f.featured.checked,
                tags: [
                    ...new Set(
                        f.tags.value
                            .split(/[,，]/)
                            .map((s) => s.trim())
                            .filter(Boolean)
                    ),
                ],
                expectedRevision: Number(attributes.dataset.revision),
            },
        });
        game = result.game;
        dirty = false;
        await reloadDetail(true);
        notice('已保存');
    } catch (e) {
        if (e.status === 409) {
            await reloadDetail(false);
            const body = el('div', null, 'game-form');
            body.append(
                el(
                    'p',
                    '草稿已保留。服务器修订 #' +
                        game.revision +
                        '：' +
                        game.title +
                        ' · ' +
                        scopeNames[game.scope] +
                        ' · Lv.' +
                        game.minTier +
                        '–' +
                        game.maxTier
                ),
                button('以此修订重提草稿', () => {
                    attributes.dataset.revision = game.revision;
                    notice('基准已更新，请再次保存');
                }),
                button('载入服务器版本', () => {
                    replaceForm();
                    notice('已同步服务器属性');
                })
            );
            await panel('属性冲突', body);
            return;
        }
        throw e;
    }
}

function upload(form) {
    const file = form.elements.file.files[0];
    if (!file) throw new Error('请先选择 ZIP 文件');
    const progress = document.querySelector('[data-upload-progress]');
    const meta = document.querySelector('[data-file-meta]');
    progress.hidden = false;
    progress.max = 100;
    progress.value = 0;
    const body = new FormData();
    body.set('file', file);
    body.set('expectedRevision', game.revision);
    const key = makeId();
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', apiBase + detailPath() + '/versions');
        Object.entries(authHeaders(key)).forEach(([k, v]) => xhr.setRequestHeader(k, v));
        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) progress.value = (e.loaded * 100) / e.total;
            else progress.removeAttribute('value');
        };
        xhr.onload = () => {
            let result;
            try {
                result = JSON.parse(xhr.responseText);
            } catch {}
            progress.hidden = true;
            if (xhr.status < 200 || xhr.status >= 300) return reject(responseError(xhr.status, result));
            form.reset();
            if (meta) meta.textContent = '支持单根 ZIP（≤ 64 MiB）';
            notice('已上传，正在隔离验收');
            reloadDetail(false).then(resolve, reject);
        };
        xhr.onerror = () => {
            progress.hidden = true;
            reloadDetail(false).catch(() => {});
            reject(new Error('传输中断，请重试'));
        };
        xhr.send(body);
    });
}

async function removeGame() {
    const plan = await mutation('/deletion-plans');
    const impact = plan.impact || {},
        body = el('div', null, 'game-form');
    const key = makeId(),
        expectedRevision = game.revision;
    const stats = el('div', null, 'game-impact-grid');
    for (const [val, label] of [
        [impact.versions || 0, '版本'],
        [impact.saves || 0, '存档'],
        [impact.runs || 0, '对局'],
        [impact.messages || 0, '消息'],
    ]) {
        const cell = el('div', null, 'game-impact-cell');
        cell.append(el('strong', String(val)), el('small', label, 'game-muted'));
        stats.append(cell);
    }
    body.append(stats, field('输入 ' + plan.confirmationText + ' 确认', 'text'));
    await formPanel(
        '彻底删除游戏',
        body,
        '确认彻底删除',
        async (data) => {
            if (data.get('text') !== plan.confirmationText) throw new Error('确认标识不匹配');
            let result;
            try {
                result = await mutation(
                    '',
                    { planToken: plan.planToken, confirmationText: data.get('text'), expectedRevision },
                    'DELETE',
                    key
                );
            } catch (e) {
                if (e.code === 'DELETE_REGISTER_PENDING' && e.detail?.jobId) {
                    sessionStorage.setItem('minigame:delete:' + boot.gameId, e.detail.jobId);
                    notice('等待删除登记 · ' + e.detail.jobId);
                    throw new Error('正在等待删除登记，请再次提交确认');
                }
                throw e;
            }
            sessionStorage.setItem('minigame:delete:' + boot.gameId, result.jobId);
            await trackDelete(result.jobId);
        },
        { danger: true }
    );
}

if (attributes) {
    attributes.addEventListener('input', () => {
        dirty = true;
        summary();
    });
    attributes.elements.scope.addEventListener('change', () => {
        renderTargets();
        summary();
    });
    attributes.addEventListener('submit', (e) => {
        e.preventDefault();
        run(e.submitter, saveAttributes);
    });
    const form = document.querySelector('[data-version-upload]');
    const dropzone = form?.querySelector('[data-dropzone]');
    const fileInput = form?.elements?.file;
    const fileMeta = form?.querySelector('[data-file-meta]');
    fileInput?.addEventListener('change', () => {
        const f = fileInput.files?.[0];
        if (fileMeta) {
            fileMeta.textContent = f
                ? f.name + ' · ' + Math.max(1, Math.round(f.size / 1024)) + ' KiB'
                : '支持单根 ZIP（≤ 64 MiB）';
        }
    });
    if (dropzone) {
        for (const ev of ['dragenter', 'dragover'])
            dropzone.addEventListener(ev, (e) => {
                e.preventDefault();
                dropzone.dataset.drag = 'true';
            });
        for (const ev of ['dragleave', 'drop'])
            dropzone.addEventListener(ev, () => {
                delete dropzone.dataset.drag;
            });
    }
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        run(e.submitter, () => upload(form));
    });
    addEventListener('beforeunload', (e) => {
        if (dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
    onAction({
        delete: removeGame,
        suggest: () => suggest('metadata'),
        unlist: async () => {
            if (await confirmAction('下架游戏', '候场与进行中对局将立即结束，已确认存档保留。', true)) {
                await mutation('/unlist');
                await reloadDetail(false);
                notice('已下架');
            }
        },
    });
    document.addEventListener('visibilitychange', () => {
        clearTimeout(pollTimer);
        if (!document.hidden) run(null, () => (deleteJobId ? trackDelete(deleteJobId) : reloadDetail(false)));
    });
    const id = sessionStorage.getItem('minigame:delete:' + boot.gameId);
    run(null, () => (id ? trackDelete(id) : reloadDetail(true)));
}
bindIconTooltips(document);
