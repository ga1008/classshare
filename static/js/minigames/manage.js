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
    modeText,
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
import { enhanceDropdowns, getDropdown } from '../lq/dropdown.js';

const boot = bootstrap(),
    base = '/admin/games';
const student = boot.role === 'student';
const workspace = boot.workspace || (student ? '/games/workshop' : '/manage/library/games');
const reviewNames = {
    queued: '等候 AI 审核',
    running: 'AI 审核中',
    manual: '待超管审核',
    approved: '内容审核通过',
    rejected: '内容被退回',
    cancelled: '审核已取消',
};
const relationNames = {
    source: '原作',
    remix: '改编版',
    same_title: '同名作品',
};
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
    relatedGames = [],
    jobs = [],
    selectedVersionId = new URLSearchParams(location.search).get('version') || '',
    editing = false,
    dirty = false,
    pollTimer,
    deleteJobId,
    refreshCount = 0;
const draftTargets = {};
const cursorPages = [location.href];
const attributes = document.querySelector('[data-game-attributes]');
const attributesView = document.querySelector('[data-attributes-view]');
const scopeNames = {
    private: '私有',
    classroom: '指定课堂',
    class: '指定行政班',
    department: '本系部',
    college: '本学院',
    school: '本校',
    public: '全平台',
};
const scopeShortNames = {
    ...scopeNames,
    classroom: '课堂',
    class: '行政班',
};

function linkButton(label, href, variant = 'ghost', opts = {}) {
    const a = el('a', null, 'lq-btn lq-btn--' + variant + ' lq-btn--' + (opts.size || 'sm'));
    a.dataset.lqComponent = 'button';
    a.href = href;
    if (opts.blank) {
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
    }
    if (opts.icon) {
        const iconWrap = el('span', null, 'lq-btn__icon');
        iconWrap.setAttribute('aria-hidden', 'true');
        iconWrap.append(svgIcon(opts.icon));
        a.append(iconWrap);
    }
    a.append(el('span', label, 'lq-btn__label'));
    return a;
}

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
        meta.append(el('small', scopeShortNames[g.scope] || g.scope));
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
    const template = document.querySelector('#game-upload-form');
    if (!template) return;
    const form = document.importNode(template.content.firstElementChild, true);
    form.elements.sourceVersionId.value = new URL(location.href).searchParams.get('sourceVersionId') || '';
    const lifetime = new AbortController();
    let send;
    await formPanel(
        '上传新游戏',
        form,
        '上传并验收',
        async () => {
            const result = await send();
            location.assign(workspace + '/' + encodeURIComponent(result.gameId));
        },
        { onClose: () => lifetime.abort(), onDestroy: () => lifetime.abort() }
    );
    send = bindUpload(form, lifetime.signal);
}

async function uploadVersion() {
    const template = document.querySelector('#game-upload-form');
    if (!template) return;
    const form = document.importNode(template.content.firstElementChild, true);
    const lifetime = new AbortController();
    let send;
    await formPanel(
        '上传新版本',
        form,
        '上传并验收',
        async () => {
            const result = await send();
            if (result?.versionId) {
                selectedVersionId = result.versionId;
                history.replaceState(null, '', versionUrl(result.versionId));
            }
            await reloadDetail(false);
            notice('上传成功，可在顶部查看验收状态');
        },
        { onClose: () => lifetime.abort(), onDestroy: () => lifetime.abort() }
    );
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

function isStudentGame() {
    return student || !!boot.studentWork || game?.ownerRole === 'student';
}

function activeVersion() {
    if (!versions.length) return null;
    if (selectedVersionId) {
        const picked = versions.find((v) => v.id === selectedVersionId);
        if (picked) return picked;
    }
    return versions.find((v) => v.id === game?.currentVersionId) || versions[0];
}

function versionUrl(vid) {
    const u = new URL(workspace + '/' + encodeURIComponent(game.id), location.origin);
    if (vid) u.searchParams.set('version', vid);
    return u.pathname + u.search;
}

function validateJobFor(v) {
    if (!v) return null;
    return jobs.find((j) => j.versionId === v.id && j.kind === 'validate') || null;
}

function genreLabelFor(v) {
    const id = v?.config?.genre;
    if (!id) return '';
    return (boot.genreOptions || []).find((item) => item.id === id)?.label || '';
}

function versionStatusBadge(v) {
    const job = validateJobFor(v);
    if (v.status === 'validating' && job?.status === 'waiting_probe') {
        return { text: '待浏览器验收', tone: 'info' };
    }
    return { text: translated(v.status), tone: toneOf(v.status) };
}

function canSubmitReview(v) {
    if (!v || game?.status === 'deleting' || game?.status === 'deleted') return false;
    const isLive = game.status === 'listed' && v.id === game.currentVersionId;
    if (v.status === 'retired') return true;
    if (v.status === 'ready' && !isLive) return true;
    if (v.status === 'rejected' && v.review?.status === 'cancelled') return true;
    return false;
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
    const scope = editing ? f.scope.value : game?.scope || f.scope.value;
    const scopeText = isStudentGame() && scope === 'private' ? '仅自己' : scopeShortNames[scope] || scope || '';
    const minT = editing && f.minTier ? f.minTier.value : game?.minTier;
    const maxT = editing && f.maxTier ? f.maxTier.value : game?.maxTier;
    node.textContent = scopeText + (f.minTier && minT != null ? ' · Lv.' + minT + '–' + maxT : '');
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
    getDropdown(f.scope)?.refresh();
    summary();
    dirty = false;
    attributes.dataset.revision = game.revision;
}

function setEditing(next, { focusField = false } = {}) {
    if (!attributes || !attributesView) return;
    editing = !!next;
    attributes.hidden = !editing;
    attributesView.hidden = editing;
    const editable = game?.status !== 'deleting' && game?.status !== 'deleted';
    showControl('edit-attributes', !editing && editable);
    if (editing) {
        enhanceDropdowns(attributes);
        getDropdown(attributes.elements.scope)?.refresh();
        if (focusField) {
            const first = attributes.elements.summary || attributes.elements.scope;
            first?.focus?.();
        }
    }
    summary();
}

async function cancelEdit() {
    if (dirty && !(await confirmAction('放弃修改', '未保存的属性改动将被还原，确定取消编辑？'))) return;
    replaceForm();
    setEditing(false);
    document.querySelector('[data-action="edit-attributes"]')?.focus?.();
}

function attrRow(label, content) {
    const wrap = el('div', null, 'game-attr-item');
    wrap.append(el('dt', label));
    const dd = el('dd');
    if (typeof content === 'string') dd.textContent = content;
    else if (Array.isArray(content)) dd.append(...content);
    else dd.append(content);
    wrap.append(dd);
    return wrap;
}

function scopeDisplayText() {
    const scope = game.scope || 'private';
    if (isStudentGame() && scope === 'private') return '仅自己';
    const baseLabel = scopeNames[scope] || scope;
    if (scope === 'class' || scope === 'classroom') {
        const ids = (game.targets || []).map(Number);
        const choices = boot.scopeOptions?.[scope] || [];
        const names = ids
            .map((id) => choices.find((c) => Number(c.id) === id))
            .filter(Boolean)
            .map((c) => c.name || c.title || c.label);
        if (names.length) return baseLabel + '：' + names.join('、');
        if (ids.length) return baseLabel + '（已选 ' + ids.length + ' 项）';
    }
    return baseLabel;
}

function renderAttributesView() {
    if (!attributesView || !game) return;
    const v = activeVersion();
    const rows = [];
    const summaryText = (game.summary || v?.config?.summary || '').trim();
    rows.push(
        attrRow(
            '简介',
            summaryText ? summaryText : el('span', '暂未填写简介', 'game-muted')
        )
    );
    const mergedTags = game.tags?.length
        ? game.tags
        : [...new Set([...(v?.sourceTags || []), ...(v?.tags || [])])];
    if (mergedTags.length) {
        const wrap = el('div', null, 'game-row-meta');
        for (const t of mergedTags) wrap.append(el('span', '#' + t, 'game-tag-pill'));
        rows.push(attrRow('标签', wrap));
    } else {
        rows.push(attrRow('标签', el('span', '未设置标签', 'game-muted')));
    }
    rows.push(attrRow('可见范围', scopeDisplayText()));
    {
        const minT = game.minTier ?? 0,
            maxT = game.maxTier ?? 10;
        rows.push(
            attrRow('修为等级', 'Lv.' + minT + '–' + maxT + (minT === 0 && maxT === 10 ? '（不限）' : ''))
        );
        rows.push(
            attrRow(
                '教师精选',
                game.featured ? statusChip('已精选', 'warning') : el('span', '否', 'game-muted')
            )
        );
    }
    if (v) {
        const meta = el('div', null, 'game-row-meta');
        meta.append(el('code', v.releaseVersion || '候选版', 'game-key-pill'));
        const genre = genreLabelFor(v);
        if (genre) meta.append(el('span', genre, 'game-tag-pill'));
        const modes = modeText(v);
        if (modes) meta.append(el('span', modes, 'game-muted'));
        if (v.authorName) meta.append(el('span', '作者 ' + v.authorName, 'game-muted'));
        if (v.sourceAuthor) meta.append(el('span', '改编自 ' + v.sourceAuthor, 'game-muted'));
        rows.push(attrRow('当前查看版本', meta));
        if (v.allowRemix !== undefined && (v.releaseVersion || v.author)) {
            rows.push(
                attrRow(
                    '二开许可',
                    v.allowRemix
                        ? statusChip('允许下载源码并二次开发', 'info')
                        : el('span', '仅限游玩（未开放二开）', 'game-muted')
                )
            );
        }
    } else {
        rows.push(attrRow('当前查看版本', el('span', '尚未上传游戏包', 'game-muted')));
    }
    attributesView.replaceChildren(...rows);
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
    a.dataset.lqComponent = 'button';
    a.href = u.href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    body.append(a);
    await panel('隔离预览', body);
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
    if (record.review) {
        body.append(
            el(
                'p',
                (reviewNames[record.review.status] || record.review.status) +
                    (record.review.reason ? ' · ' + record.review.reason : ''),
                'game-muted'
            )
        );
    }
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
            '上架此版本',
            '新开局将切换至 ' + v.releaseVersion + '；候场队列重置，进行中对局不受影响。'
        ))
    )
        return;
    await mutation('/publish', { versionId: v.id });
    await reloadDetail(false);
    notice('已上架 ' + v.releaseVersion);
}

async function submitReview(v) {
    if (
        !(await confirmAction(
            '提交审核',
            '将版本 ' + (v.releaseVersion || '此版本') + ' 提交至小游戏审核队列，审核通过后即正式发布。'
        ))
    )
        return;
    await mutation('/versions/' + encodeURIComponent(v.id) + '/submit-review');
    await reloadDetail(false);
    notice('已提交审核');
}

async function unlistGame() {
    if (await confirmAction('下架游戏', '候场与进行中对局将立即结束，已确认存档保留。', true)) {
        await mutation('/unlist');
        await reloadDetail(false);
        notice('已下架');
    }
}

async function license(version) {
    const label = version.allowRemix ? '关闭二开许可' : '允许二次开发';
    if (
        !(await confirmAction(
            label,
            version.allowRemix
                ? '关闭后将停止提供该版本的源码下载。'
                : '本版本公开后，可游玩的师生可下载完整原包并改编。'
        ))
    )
        return;
    await mutation('/versions/' + encodeURIComponent(version.id) + '/license', { allowRemix: !version.allowRemix }, 'PATCH');
    await reloadDetail(false);
    notice('已更新此版本的二开许可');
}

async function changeVersion(version, remove) {
    const label = remove ? '删除此版本' : '停用此版本';
    if (
        !(await confirmAction(
            label,
            (version.releaseVersion || '此候选版本') +
                (remove ? ' 的完整游戏包将被删除，此操作不可撤销。' : ' 将归档停用，正在使用该版本的对局将结束。'),
            true
        ))
    )
        return;
    const path = '/versions/' + encodeURIComponent(version.id);
    await mutation(remove ? path : path + '/retire', {}, remove ? 'DELETE' : 'POST');
    if (remove && selectedVersionId === version.id) selectedVersionId = '';
    await reloadDetail(false);
    notice(remove ? '已提交版本删除' : '此版本已停用');
}

function renderStatusBanner() {
    const banner = document.querySelector('[data-status-detail]');
    if (!banner || !game) return;
    const items = [];
    const v = activeVersion();
    const vJob = validateJobFor(v);
    const isLive = !!(v && game.status === 'listed' && v.id === game.currentVersionId);

    function makeAlert(tone, chipLabel, chipTone, text, extraActions = []) {
        const box = el('div', null, 'lq-alert game-status-row');
        box.dataset.lqComponent = 'alert';
        box.dataset.lqStatus = 'alert';
        box.dataset.lqToneLevel = tone;
        const main = el('div', null, 'game-row-head');
        if (chipLabel) main.append(statusChip(chipLabel, chipTone || tone));
        if (text) main.append(el('span', text));
        box.append(main);
        if (extraActions.length) {
            const act = el('div', null, 'game-actions');
            act.append(...extraActions);
            box.append(act);
        }
        return box;
    }

    if (game.status === 'deleting' || game.status === 'deleted') {
        const delJob = jobs.find((j) => j.kind === 'delete');
        items.push(
            makeAlert(
                'danger',
                translated(game.status),
                'danger',
                delJob?.stage || (game.status === 'deleted' ? '游戏及全部版本文件已清理完毕' : '正在清理游戏资产与记录')
            )
        );
    } else if (!v) {
        items.push(makeAlert('info', '未上传包', 'neutral', '当前尚无游戏版本，点击“上传新版本”或返回列表上传游戏包。'));
    } else if (v.status === 'validating') {
        if (vJob?.status === 'waiting_probe') {
            items.push(makeAlert('info', '待浏览器验收', 'info', vJob.stage || '静态检查已通过，启用验收后自动继续'));
        } else if (vJob?.status === 'retry') {
            const err = typeof vJob.error === 'string' ? vJob.error : vJob.error?.message || '';
            items.push(makeAlert('warning', '等待重试', 'warning', [vJob.stage, err].filter(Boolean).join(' · ')));
        } else {
            items.push(makeAlert('info', '验收中', 'info', vJob?.stage || '正在执行包校验与隔离验收'));
        }
    } else if (v.status === 'reviewing') {
        const rStatus = v.review?.status || 'manual';
        const tone = rStatus === 'manual' ? 'warning' : 'info';
        const desc =
            v.review?.reason ||
            (rStatus === 'manual'
                ? '已通过第一轮基础校验，等待超级管理员审核，通过后正式发布'
                : '已通过基础校验，AI 正在进行内容风险查验');
        items.push(makeAlert(tone, reviewNames[rStatus] || rStatus, tone, desc));
    } else if (v.status === 'rejected') {
        if (v.review?.status === 'rejected') {
            items.push(
                makeAlert('danger', '内容被退回', 'danger', v.review.reason || '内容审核未通过，请修改并提升版本号后重新上传')
            );
        } else if (v.review?.status === 'cancelled') {
            items.push(makeAlert('warning', '审核已取消', 'warning', '此前审核已取消，可直接点击“提交审核”重新送审。'));
        } else {
            const error = vJob?.error;
            const detail =
                typeof error === 'string'
                    ? error
                    : [error?.file, error?.field, error?.message, error?.repairHint].filter(Boolean).join(' · ');
            const extra = [];
            if (!student && vJob?.status === 'rejected') {
                extra.push(button('生成修复建议', () => suggest('repair', vJob.id), 'soft', { icon: 'sparkles' }));
            }
            items.push(makeAlert('danger', '验收失败', 'danger', detail || vJob?.stage || '游戏包未通过校验', extra));
        }
    } else if (v.status === 'ready' && !isLive) {
        const reviewed = v.review?.status === 'approved' && boot.canPublish;
        items.push(makeAlert('info', '未上架', 'info', reviewed
            ? '此版本已有审核通过记录。核对试玩结果后可重新上架。'
            : '可隔离预览此版本，提交超级管理员审核后上架。'));
    } else if (v.status === 'retired') {
        items.push(makeAlert('neutral', '已归档', 'neutral', '此版本已归档停用，可提交审核重新上架。'));
    }

    const suggestJob = jobs.find((j) => j.kind === 'suggest' && j.status === 'succeeded' && j.result?.suggestion);
    if (suggestJob && game.status !== 'deleting' && game.status !== 'deleted') {
        const label = suggestJob.result.purpose === 'metadata' ? '编目建议已就绪' : '修复建议已就绪';
        items.push(
            makeAlert('info', label, 'info', suggestJob.stage || '点击查看并核对建议内容', [
                button('查看建议', () => showSuggestion(suggestJob.result), 'soft', { icon: 'eye' }),
            ])
        );
    }

    banner.replaceChildren(...items);
    banner.hidden = !items.length;
}

function renderQuickActions() {
    const bar = document.querySelector('[data-quick-actions]');
    if (!bar || !game) return;
    if (game.status === 'deleting' || game.status === 'deleted') {
        bar.replaceChildren();
        bar.hidden = true;
        return;
    }
    bar.hidden = false;
    const v = activeVersion();
    const isLive = !!(v && game.status === 'listed' && v.id === game.currentVersionId);
    const vDeleting = !v || v.status === 'deleting' || v.status === 'deleted';
    const main = el('div', null, 'game-quickbar-main');
    const danger = el('div', null, 'game-quickbar-danger');

    if (game.status === 'listed' && (!v || v.id === game.currentVersionId)) {
        main.append(linkButton('进入游玩', '/games/' + encodeURIComponent(game.slug), 'glass', { icon: 'external', blank: true }));
    }

    const testable = v && ['ready', 'reviewing'].includes(v.status);
    const archivedPreview = boot.isSuperAdmin && v?.digest && ['retired', 'rejected'].includes(v.status);
    if (testable || archivedPreview) {
        main.append(button('隔离预览', () => preview(v), 'soft', { icon: 'eye' }));
    }
    if (testable) {
        if (v.playtested) main.append(statusChip('已登记试玩', 'success'));
        else if (v.previewed) main.append(button('确认已试玩', () => verifyPlayed(v), 'soft', { icon: 'check' }));
    }
    if (v && !isLive && boot.canPublish && v.status === 'ready' && v.review?.status === 'approved') {
        main.append(button('重新上架', () => publish(v), 'prominent', { icon: 'rocket' }));
    } else if (canSubmitReview(v)) {
        main.append(button('提交审核', () => submitReview(v), 'prominent', { icon: 'shield' }));
    }
    if (boot.isSuperAdmin && v?.status === 'reviewing') {
        main.append(linkButton('前往内容复核', '/manage/library/games/reviews?version=' + encodeURIComponent(v.id),
            'prominent', {icon:'shield'}));
    }

    if (game.status === 'listed') {
        main.append(
            button('下架游戏', unlistGame, 'soft', {
                icon: 'pause',
                attrs: { 'data-lq-action-tone': 'warning' },
            })
        );
    }

    if (v && v.status === 'ready' && !isLive) {
        main.append(button('停用此版本', () => changeVersion(v, false), 'ghost', { icon: 'pause' }));
    }

    if (!vDeleting && v.allowRemix !== undefined && (v.releaseVersion || v.author)) {
        main.append(
            button(v.allowRemix ? '关闭二开许可' : '允许二次开发', () => license(v), 'ghost', {
                icon: 'copy',
            })
        );
    }

    if (v?.digest && ['ready', 'reviewing', 'retired', 'rejected'].includes(v.status)) {
        const path = detailPath() + '/versions/' + encodeURIComponent(v.id) + '/package';
        main.append(linkButton('下载原包', apiBase + path, 'ghost', {icon:'download'}));
    }

    if (!vDeleting) {
        main.append(button('验收报告', () => inspectVersion(v), 'ghost', { icon: 'shield' }));
        danger.append(button('删除此版本', () => changeVersion(v, true), 'ghost', { icon: 'trash' }));
    }

    danger.append(button('彻底删除游戏', removeGame, 'destructive', { icon: 'trash' }));
    bar.replaceChildren(main, danger);
}

function renderVersions() {
    const root = document.querySelector('[data-game-versions]');
    const countNode = document.querySelector('[data-version-count]');
    if (countNode) countNode.textContent = versions.length ? versions.length + ' 个版本' : '0 个版本';
    const current = activeVersion();
    const rows = versions.map((v) => {
        const isCurrent = current?.id === v.id;
        const isLive = game.status === 'listed' && v.id === game.currentVersionId;
        const row = el('li', null, 'game-version-item');
        if (isCurrent) row.dataset.lqSelected = 'true';
        const link = el('a', null, 'game-version-jump');
        link.href = versionUrl(v.id);
        link.dataset.versionJump = v.id;
        if (isCurrent) link.setAttribute('aria-current', 'true');

        const main = el('div', null, 'game-version-main');
        const head = el('div', null, 'game-row-head');
        const badge = versionStatusBadge(v);
        head.append(
            el('strong', v.releaseVersion || '候选版', 'game-version-num'),
            statusChip(badge.text, badge.tone)
        );
        if (isLive) head.append(statusChip('当前线上', 'primary'));
        if (v.review) {
            head.append(
                statusChip(
                    reviewNames[v.review.status] || v.review.status,
                    v.review.status === 'rejected' ? 'danger' : v.review.status === 'approved' ? 'success' : 'info'
                )
            );
        }

        const meta = el('div', null, 'game-row-meta');
        if (v.authorName) meta.append(el('small', '作者 ' + v.authorName, 'game-muted'));
        const genre = genreLabelFor(v);
        if (genre) meta.append(el('small', genre, 'game-tag-pill'));
        if (v.sourceAuthor) meta.append(el('small', '改编自 ' + v.sourceAuthor, 'game-muted'));
        if (v.allowRemix !== undefined && (v.releaseVersion || v.author)) {
            meta.append(el('small', v.allowRemix ? '允许二开' : '仅限游玩', 'game-muted'));
        }
        for (const tag of [...new Set([...(v.sourceTags || []), ...(v.tags || [])])].slice(0, 3)) {
            meta.append(el('small', '#' + tag, 'game-tag-pill'));
        }
        main.append(head);
        if (meta.childElementCount) main.append(meta);

        const cue = el('span', null, 'game-version-cue');
        if (isCurrent) {
            cue.append(el('small', '当前查看', 'game-policy-pill'));
        } else {
            cue.append(el('small', '查看', 'game-muted'), svgIcon('chevron-right', 'game-mini-icon'));
        }
        link.append(main, cue);
        row.append(link);
        return row;
    });
    root.replaceChildren(
        ...(rows.length ? rows : [el('li', '尚未上传游戏包', 'game-muted game-empty-row')])
    );
}

function renderRelatedGames() {
    const card = document.querySelector('[data-related-card]');
    const list = document.querySelector('[data-related-games]');
    if (!card || !list) return;
    if (!relatedGames.length) {
        card.hidden = true;
        list.replaceChildren();
        return;
    }
    card.hidden = false;
    const rows = relatedGames.map((item) => {
        const row = el('li', null, 'game-version-item');
        const link = el('a', null, 'game-version-jump');
        link.href = workspace + '/' + encodeURIComponent(item.id);
        const main = el('div', null, 'game-version-main');
        const head = el('div', null, 'game-row-head');
        head.append(
            el('strong', item.title),
            statusChip(translated(item.status), toneOf(item.status))
        );
        if (item.relation && relationNames[item.relation]) {
            head.append(el('small', relationNames[item.relation], 'game-tag-pill'));
        }
        const meta = el('div', null, 'game-row-meta');
        if (item.gameKey) meta.append(el('code', item.gameKey, 'game-key-pill'));
        if (item.versionCount != null) meta.append(el('small', item.versionCount + ' 个版本', 'game-muted'));
        main.append(head, meta);
        const cue = el('span', null, 'game-version-cue');
        cue.append(el('small', '跳转', 'game-muted'), svgIcon('chevron-right', 'game-mini-icon'));
        link.append(main, cue);
        row.append(link);
        return row;
    });
    list.replaceChildren(...rows);
}

function renderHero() {
    document.querySelector('[data-detail-heading]').textContent = game.title;
    const iconSlot = document.querySelector('[data-detail-icon]');
    if (iconSlot) iconSlot.replaceChildren(gameIcon(game, { eager: true }));
    const statusBox = document.querySelector('[data-detail-status]');
    const chips = [statusChip(translated(game.status), toneOf(game.status))];
    const v = activeVersion();
    if (v) {
        const badge = versionStatusBadge(v);
        chips.push(el('code', 'v' + (v.releaseVersion || '候选版'), 'game-key-pill'));
        if (v.status !== 'ready' || game.status !== 'listed' || v.id !== game.currentVersionId) {
            chips.push(statusChip(badge.text, badge.tone));
        } else {
            chips.push(statusChip('当前线上', 'primary'));
        }
        if (v.review) {
            chips.push(
                statusChip(
                    reviewNames[v.review.status] || v.review.status,
                    v.review.status === 'rejected' ? 'danger' : v.review.status === 'approved' ? 'success' : 'info'
                )
            );
        }
    }
    if (game.gameKey) chips.push(el('code', game.gameKey, 'game-key-pill'));
    statusBox.replaceChildren(...chips);
}

function renderDetailView() {
    if (!game) return;
    const v = activeVersion();
    selectedVersionId = v?.id || '';
    renderHero();
    renderStatusBanner();
    renderQuickActions();
    renderAttributesView();
    renderVersions();
    renderRelatedGames();
    summary();
    const editable = game.status !== 'deleting' && game.status !== 'deleted';
    showControl('edit-attributes', !editing && editable);
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
        if (boot.isSuperAdmin && (result.summary || tags.length))
            body.append(
                button(
                    '采用到属性草稿',
                    () => {
                        setEditing(true);
                        if (result.summary && attributes.elements.summary) attributes.elements.summary.value = result.summary;
                        if (tags.length && attributes.elements.tags) attributes.elements.tags.value = tags.join('，');
                        dirty = true;
                        summary();
                        notice('已填入属性草稿，核对后点击保存');
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
        if (game) game.status = 'deleted';
        setEditing(false);
        document.querySelectorAll('[data-game-detail] .game-section').forEach((n) => (n.hidden = true));
        const bar = document.querySelector('[data-quick-actions]');
        if (bar) {
            bar.replaceChildren();
            bar.hidden = true;
        }
        if (game) {
            renderHero();
            renderStatusBanner();
        }
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
    relatedGames = detail.relatedGames || [];
    showControl('more-versions', offset != null);
    jobs = detail.jobs || [];
    if (resetForm || !dirty) replaceForm();
    renderDetailView();
    if (game.status === 'deleting') {
        const job = jobs.find((j) => j.kind === 'delete');
        if (job) {
            await trackDelete(job.id);
            return;
        }
    }
    clearTimeout(pollTimer);
    if (!document.hidden) {
        const active =
            versions.some((v) => ['queued', 'running'].includes(v.review?.status)) ||
            jobs.some((j) => ['running', 'pending', 'queued', 'retry', 'validating', 'processing'].includes(j.status));
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
                targets, minTier, maxTier,
                ...(boot.isSuperAdmin && {
                        summary: f.summary.value.trim(),
                        featured: f.featured.checked,
                        tags: parseTags(f.tags.value),
                    }),
                expectedRevision: Number(attributes.dataset.revision),
            },
        });
        game = result.game;
        dirty = false;
        setEditing(false);
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
    let selection,
        reading,
        sending = false;
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
    const hasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes('Files');
    for (const type of ['dragenter', 'dragover']) {
        dropzone.addEventListener(type, (event) => {
            if (!hasFiles(event)) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = sending ? 'none' : 'copy';
            if (!sending) dropzone.dataset.drag = 'true';
        });
    }
    dropzone.addEventListener('dragleave', (event) => {
        if (!dropzone.contains(event.relatedTarget)) delete dropzone.dataset.drag;
    });
    dropzone.addEventListener('drop', (event) => {
        delete dropzone.dataset.drag;
        event.preventDefault();
        const source = captureGameTransfer(event.dataTransfer);
        if (source) void select(source);
    });
    const pasteScope = form.closest('[role="dialog"]') || form;
    pasteScope.addEventListener(
        'paste',
        (event) => {
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
        },
        { signal }
    );
    const licenseInput = form.elements.allowRemix;
    const licenseHint = form.querySelector('[data-license-hint]');
    const sharingHint = licenseHint.textContent;
    licenseInput.addEventListener('change', () => {
        licenseInput.closest('[data-lq-status]').dataset.lqToneLevel = licenseInput.checked ? 'info' : 'neutral';
        licenseHint.textContent = licenseInput.checked ? sharingHint : '此版本仅供游玩，平台将关闭源码下载和改编入口。';
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
    const allowRemix = form.elements.allowRemix.checked;
    const source = form.elements.sourceVersionId.value.trim();
    const revision = game?.revision;
    const signature = JSON.stringify([revision, tags, allowRemix, source]);
    const oldAttempt = uploadAttempts.get(form);
    const key = oldAttempt?.file === file && oldAttempt.signature === signature ? oldAttempt.key : makeId();
    uploadAttempts.set(form, { file, signature, key });
    const body = new FormData();
    body.set('file', file);
    body.set('tags', JSON.stringify(tags));
    body.set('allowRemix', String(allowRemix));
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
        xhr.onloadend = () => {
            progress.hidden = true;
        };
        xhr.onerror = () => reject(new Error('连接中断，可重新提交同一文件'));
        xhr.onload = () => {
            let result;
            try {
                result = JSON.parse(xhr.responseText);
            } catch {}
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
        dirty = true;
        renderTargets();
        summary();
    });
    attributes.addEventListener('submit', (e) => {
        e.preventDefault();
        run(e.submitter, saveAttributes);
    });
    document.querySelector('[data-game-versions]')?.addEventListener('click', (e) => {
        const link = e.target.closest('[data-version-jump]');
        if (!link || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        const vid = link.dataset.versionJump;
        if (!vid || vid === selectedVersionId) return;
        selectedVersionId = vid;
        history.pushState(null, '', link.href);
        renderDetailView();
    });
    addEventListener('popstate', () => {
        selectedVersionId = new URLSearchParams(location.search).get('version') || '';
        renderDetailView();
    });
    addEventListener('beforeunload', (e) => {
        if (dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
    onAction({
        'edit-attributes': () => setEditing(true, { focusField: true }),
        'cancel-edit': cancelEdit,
        'upload-version': uploadVersion,
        'more-versions': () => {
            visiblePages++;
            return reloadDetail(false);
        },
        delete: removeGame,
        suggest: () => suggest('metadata'),
        unlist: unlistGame,
    });
    document.addEventListener('visibilitychange', () => {
        clearTimeout(pollTimer);
        if (!document.hidden) run(null, () => (deleteJobId ? trackDelete(deleteJobId) : reloadDetail(false)));
    });
    const id = sessionStorage.getItem('minigame:delete:' + boot.gameId);
    run(null, () => (id ? trackDelete(id) : reloadDetail(true)));
}
bindIconTooltips(document);
