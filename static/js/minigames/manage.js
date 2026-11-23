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
import { captureGameTransfer, prepareGamePackage } from './package-input.js';

const boot = bootstrap(),
    base = '/admin/games';
const student = boot.role === 'student';
const workspace = boot.workspace || (student ? '/games/workshop' : '/manage/library/games');
const reviewNames = {queued:'等候 AI 审核',running:'AI 审核中',manual:'待超管审核',approved:'内容审核通过',rejected:'内容被退回',cancelled:'审核已取消'};
const statusText = {
    draft: '草稿',
    listed: '已上架',
    unlisted: '已下架',
    deleting: '删除中',
    deleted: '已彻底删除',
    validating: '验收中',
    reviewing: '内容审核中',
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
    cancelled: '已取消',
    failed: '需处理',
};
const statusTones = {
    listed: 'success',
    ready: 'success',
    succeeded: 'success',
    done: 'success',
    completed: 'success',
    validating: 'info',
    reviewing: 'info',
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
        head.append(libraryLink(g, workspace));
        if (g.featured) {
            const star = el('span', null, 'game-featured-mark');
            star.setAttribute('aria-label', '精选');
            star.append(svgIcon('sparkles', 'game-mini-icon'));
            head.append(star);
        }
        const meta = el('div', null, 'game-row-meta');
        if (g.versionCount != null) meta.append(el('small', g.versionCount + ' 个版本'));
        if (g.authorName || g.author) meta.append(el('small', '作者 ' + (g.authorName || g.author)));
        if (g.genreLabel) meta.append(el('small', g.genreLabel));
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
    const form = document.importNode(document.querySelector('#game-upload-form').content.firstElementChild, true);
    form.elements.sourceVersionId.value = new URL(location.href).searchParams.get('sourceVersionId') || '';
    const lifetime = new AbortController();
    let send;
    await formPanel('上传新游戏', form, '上传并验收', async () => {
        const result = await send();
        location.assign(workspace + '/' + encodeURIComponent(result.gameId));
    }, { onClose: () => lifetime.abort(), onDestroy: () => lifetime.abort() });
    send = bindUpload(form, lifetime.signal);
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
    if (new URL(location.href).searchParams.get('sourceVersionId')) run(null, draft);
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
    node.textContent = (scopeNames[f.scope.value] || '') +
        (f.minTier ? ' · Lv.' + f.minTier.value + '–' + f.maxTier.value : '');
}

function replaceForm() {
    const f = attributes.elements;
    if (f.summary) f.summary.value = game.summary || '';
    f.scope.value = game.scope || 'private';
    if (f.tags) f.tags.value = (game.tags || []).join('，');
    if (f.featured) f.featured.checked = !!game.featured;
    if (f.minTier) f.minTier.value = game.minTier ?? 0;
    if (f.maxTier) f.maxTier.value = game.maxTier ?? 10;
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
        if (!student && !boot.studentWork && game.ownerRole !== 'student' && v.status === 'ready') {
            actions.append(
                button('教师预览', () => preview(v), 'soft', { icon: 'eye' }),
                button('确认已试玩', () => verifyPlayed(v), 'soft', { icon: 'check' })
            );
            if (v.id !== game.currentVersionId || game.status !== 'listed')
                actions.append(button('发布此版本', () => publish(v), 'prominent', { icon: 'rocket' }));
        }
        if (v.review) {
            info.append(statusChip(reviewNames[v.review.status] || v.review.status,
                v.review.status === 'rejected' ? 'danger' : v.review.status === 'approved' ? 'success' : 'info'));
            if (v.review.reason) info.append(el('small', v.review.reason, 'game-muted'));
        }
        if (v.sourceAuthor) info.append(el('small', '改编自 ' + v.sourceAuthor, 'game-muted'));
        if (v.authorName) info.append(el('small', '作者 ' + v.authorName, 'game-muted'));
        const genre = (boot.genreOptions || []).find((item) => item.id === v.config?.genre);
        if (genre) info.append(el('small', genre.label, 'game-tag-pill'));
        for (const tag of [...new Set([...(v.sourceTags || []), ...(v.tags || [])])])
            info.append(el('small', '#' + tag, 'game-tag-pill'));
        if (v.status !== 'deleting' && v.status !== 'deleted') {
            if (v.id === game.currentVersionId && game.status === 'listed' && v.allowRemix) {
                const link = el('a', '下载源码', 'lq-btn lq-btn--soft lq-btn--sm');
                link.dataset.lqComponent = 'button';
                link.href = apiBase + '/games/' + encodeURIComponent(game.id) + '/source';
                actions.append(link);
            }
            if (v.allowRemix !== undefined && (v.releaseVersion || v.author))
                actions.append(button(v.allowRemix ? '关闭二开许可' : '允许二次开发', () => license(v), 'ghost'));
            if (v.status === 'ready')
                actions.append(button('下架此版本', () => changeVersion(v, false), 'soft'));
            actions.append(button('删除此版本', () => changeVersion(v, true), 'destructive'));
        }
        row.append(info, actions);
        return row;
    });
    root.replaceChildren(...(rows.length ? rows : [el('li', '尚未上传游戏包', 'game-muted game-empty-row')]));
}

async function license(version) {
    const label = version.allowRemix ? '关闭二开许可' : '允许二次开发';
    if (!(await confirmAction(label, version.allowRemix ? '关闭后将停止提供该版本的源码下载。' :
        '本版本公开后，可游玩的师生可下载完整原包并改编。'))) return;
    await mutation('/versions/' + encodeURIComponent(version.id) + '/license', {allowRemix: !version.allowRemix}, 'PATCH');
    await reloadDetail(false);
    notice('已更新此版本的二开许可');
}

async function changeVersion(version, remove) {
    const label = remove ? '删除此版本' : '下架此版本';
    if (!(await confirmAction(label, (version.releaseVersion || '此候选版本') + (remove ?
        ' 的完整游戏包将被删除，此操作不可撤销。' : ' 将停止公开，正在使用该版本的对局将结束。'), true))) return;
    const path = '/versions/' + encodeURIComponent(version.id);
    await mutation(remove ? path : path + '/retire', {}, remove ? 'DELETE' : 'POST');
    await reloadDetail(false);
    notice(remove ? '已提交版本删除' : '此版本已下架');
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
        if (!student && j.status === 'rejected' && j.kind === 'validate')
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
    const displayed = Math.max(1, visiblePages);
    const newVersions = [...(detail.versions || [])];
    let offset = detail.nextVersionOffset;
    for (let page = 1; page < displayed && offset != null; page++) {
        const more = await api(detailPath() + '?versionOffset=' + encodeURIComponent(offset));
        if (serial !== refreshCount) return;
        newVersions.push(...more.versions);
        offset = more.nextVersionOffset;
    }
    versions = newVersions;
    showControl('more-versions', offset != null);
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
        const active = versions.some((v) => ['queued','running'].includes(v.review?.status)) || jobs.some((j) =>
            ['running', 'pending', 'queued', 'retry', 'validating', 'processing'].includes(j.status)
        );
        const delay = active ? 3000 : jobs.some((j) => j.status === 'waiting_probe') ? 60000 : 0;
        if (delay) pollTimer = setTimeout(() => run(null, () => reloadDetail(false)), delay);
    }
}

async function saveAttributes() {
    const f = attributes.elements,
        targets = new FormData(attributes).getAll('target').map(Number);
    const minTier = f.minTier ? Number(f.minTier.value) : game.minTier,
        maxTier = f.maxTier ? Number(f.maxTier.value) : game.maxTier;
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
                scope: f.scope.value,
                ...(!student && !boot.studentWork && {
                    summary: f.summary.value.trim(), targets, minTier, maxTier,
                    featured: f.featured.checked, tags: parseTags(f.tags.value),
                }),
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

function parseTags(value) {
    return [...new Set(value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean))];
}

function bindUpload(form, signal) {
    const fileInput = form.elements.file;
    const folderInput = form.querySelector('[data-folder-input]');
    const folderPicker = form.querySelector('[data-pick-folder]');
    const dropzone = form.querySelector('[data-dropzone]');
    const fileMeta = form.querySelector('[data-file-meta]');
    const hint = fileMeta.textContent;
    const submit = form.querySelector('[type="submit"]');
    let selection, reading, sending = false;
    signal?.addEventListener('abort', () => reading?.abort(), { once: true });

    const reset = () => {
        reading?.abort();
        reading = null;
        selection = null;
        fileMeta.textContent = hint;
        submit.disabled = false;
        dropzone.removeAttribute('aria-busy');
        uploadAttempts.delete(form);
    };
    form.addEventListener('reset', reset);
    async function select(source) {
        if (sending) return;
        reset();
        const current = new AbortController();
        reading = current;
        dropzone.setAttribute('aria-busy', 'true');
        submit.disabled = true;
        fileMeta.textContent = '正在读取游戏文件…';
        try {
            const file = await prepareGamePackage(source, { signal: current.signal });
            if (current.signal.aborted) return;
            selection = file;
            fileMeta.textContent = file.name + ' · ' + Math.ceil(file.size / 1024) + ' KiB';
        } catch (error) {
            if (!current.signal.aborted) fileMeta.textContent = error.message || '读取失败，请重新选择游戏包。';
        } finally {
            if (reading === current) {
                reading = null;
                submit.disabled = false;
                dropzone.removeAttribute('aria-busy');
            }
        }
    }
    form.querySelector('[data-pick-zip]').addEventListener('click', () => fileInput.click());
    folderPicker.hidden = !('webkitdirectory' in folderInput);
    folderPicker.addEventListener('click', () => folderInput.click());
    for (const input of [fileInput, folderInput]) {
        input.addEventListener('change', () => {
            const files = Array.from(input.files);
            input.value = '';
            if (files.length) void select(files);
        });
    }
    const hasFiles = event => Array.from(event.dataTransfer?.types || []).includes('Files');
    for (const type of ['dragenter', 'dragover']) {
        dropzone.addEventListener(type, event => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = sending ? 'none' : 'copy';
            if (!sending) dropzone.dataset.drag = 'true';
        });
    }
    dropzone.addEventListener('dragleave', event => {
        if (!dropzone.contains(event.relatedTarget)) delete dropzone.dataset.drag;
    });
    dropzone.addEventListener('drop', event => {
        delete dropzone.dataset.drag;
        event.preventDefault();
        const source = captureGameTransfer(event.dataTransfer);
        if (source) void select(source);
    });
    const pasteScope = form.closest('[role="dialog"]') || form;
    pasteScope.addEventListener('paste', event => {
        if (event.defaultPrevented) return;
        const source = captureGameTransfer(event.clipboardData);
        if (!source) {
            if (dropzone.contains(event.target) && !selection && !reading) {
                fileMeta.textContent = '剪贴板中没有可读取的游戏文件。可从文件管理器复制 ZIP，或点“选择文件夹”。';
            }
            return;
        }
        event.preventDefault();
        void select(source);
    }, { signal });
    const license = form.elements.allowRemix;
    const licenseHint = form.querySelector('[data-license-hint]');
    const sharingHint = licenseHint.textContent;
    license.addEventListener('change', () => {
        license.closest('[data-lq-status]').dataset.lqToneLevel = license.checked ? 'info' : 'neutral';
        licenseHint.textContent = license.checked ? sharingHint : '此版本仅供游玩，平台将关闭源码下载和改编入口。';
    });
    return async () => {
        if (sending) throw new Error('游戏包正在上传。');
        if (reading) throw new Error('请等待游戏文件读取完成。');
        if (!selection) throw new Error('请先选择、拖入或粘贴游戏包。');
        sending = true;
        const pickZip = form.querySelector('[data-pick-zip]');
        pickZip.disabled = folderPicker.disabled = true;
        try {
            return await upload(form, selection);
        } finally {
            sending = false;
            pickZip.disabled = folderPicker.disabled = false;
        }
    };
}

const uploadAttempts = new WeakMap();

function upload(form, file) {
    const tags = parseTags(form.elements.uploadTags.value);
    if (tags.length > 12) throw new Error('最多可填写 12 个标签');
    const license = form.elements.allowRemix.checked;
    const source = form.elements.sourceVersionId.value.trim();
    const revision = game?.revision;
    const signature = JSON.stringify([revision, tags, license, source]);
    const oldAttempt = uploadAttempts.get(form);
    const key = oldAttempt?.file === file && oldAttempt.signature === signature ? oldAttempt.key : makeId();
    uploadAttempts.set(form, { file, signature, key });
    const body = new FormData();
    body.set('file', file);
    body.set('tags', JSON.stringify(tags));
    body.set('allowRemix', String(license));
    if (source) body.set('sourceVersionId', source);
    if (boot.gameId) {
        if (revision == null) throw new Error('作品正在载入，请稍后再试');
        body.set('targetGameId', boot.gameId);
        body.set('expectedRevision', revision);
    }
    const progress = form.querySelector('[data-upload-progress]');
    progress.hidden = false;
    progress.max = 100;
    progress.value = 0;
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', apiBase + '/admin/imports');
        Object.entries(authHeaders(key)).forEach(([k, v]) => xhr.setRequestHeader(k, v));
        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) progress.value = (e.loaded * 100) / e.total;
            else progress.removeAttribute('value');
        };
        xhr.onloadend = () => { progress.hidden = true; };
        xhr.onerror = () => reject(new Error('连接中断，可重新提交同一文件'));
        xhr.onload = () => {
            let result;
            try { result = JSON.parse(xhr.responseText); } catch {}
            if (xhr.status < 200 || xhr.status >= 300) {
                if (xhr.status === 409 && boot.gameId) run(null, () => reloadDetail(false));
                return reject(responseError(xhr.status, result));
            }
            if (!result?.gameId) return reject(new Error('未取得上传回执，可重试'));
            uploadAttempts.delete(form);
            resolve(result);
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

let visiblePages = 1;
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
    const send = form && bindUpload(form);
    form?.addEventListener('submit', (e) => {
        e.preventDefault();
        run(e.submitter, async () => {
            await send();
            form.reset();
            form.elements.allowRemix.dispatchEvent(new Event('change'));
            await reloadDetail(false);
            notice('上传成功，可在验收记录中查看进度');
        });
    });
    addEventListener('beforeunload', (e) => {
        if (dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
    onAction({
        'more-versions': () => { visiblePages++; return reloadDetail(false); },
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
